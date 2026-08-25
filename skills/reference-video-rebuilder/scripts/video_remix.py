#!/usr/bin/env python3
"""Public alpha CLI for the ``reference-video-rebuilder`` Skill.

The CLI keeps Template IR validation self-contained, then lazily loads the
local media runtime only for commands that need it.  The alpha deliberately
renders only the deterministic S1 subset: semantic interpretation and
generation of render-ready assets remain agent-assisted stages.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
from importlib import metadata as importlib_metadata
import json
import math
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from collections.abc import Mapping, Sequence
from typing import Any, Iterable

try:
    from jsonschema import Draft202012Validator
except ImportError:  # The CLI must not silently downgrade structural validation.
    Draft202012Validator = None  # type: ignore[assignment,misc]


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIRECTORY = SKILL_ROOT / "assets" / "schemas"
TEMPLATE_SCHEMA_PATH = SCHEMA_DIRECTORY / "template-ir.schema.json"
ASSET_MANIFEST_SCHEMA_PATH = SCHEMA_DIRECTORY / "asset-manifest.schema.json"
COMPILER_PLAN_SCHEMA_PATH = SCHEMA_DIRECTORY / "compiler-plan.schema.json"
PROPOSAL_SCHEMA_PATH = SCHEMA_DIRECTORY / "compiler-plan-proposal.schema.json"
REVIEW_SCHEMA_PATH = SCHEMA_DIRECTORY / "review-decision.schema.json"
# Asset-pack packets are intentionally separate from the v0.4 Compiler Plan
# proposal/review contracts above.  Keep both descriptive spellings public so
# callers can select the right contract without overloading the old aliases.
ASSET_PACK_PROPOSAL_SCHEMA_PATH = SCHEMA_DIRECTORY / "asset-pack-proposal.schema.json"
ASSET_MAPPING_REVIEW_SCHEMA_PATH = SCHEMA_DIRECTORY / "asset-mapping-review.schema.json"
# v0.6 generation packets deliberately describe and review local inputs and
# render-ready results.  They do not name or invoke a generator/provider.
GENERATION_REQUEST_SCHEMA_PATH = SCHEMA_DIRECTORY / "generation-request.schema.json"
GENERATION_PLAN_SCHEMA_PATH = SCHEMA_DIRECTORY / "generation-plan.schema.json"
GENERATION_PLAN_REVIEW_SCHEMA_PATH = SCHEMA_DIRECTORY / "generation-plan-review.schema.json"
GENERATION_RESULTS_PROPOSAL_SCHEMA_PATH = SCHEMA_DIRECTORY / "generation-results-proposal.schema.json"
GENERATION_RESULTS_REVIEW_SCHEMA_PATH = SCHEMA_DIRECTORY / "generation-results-review.schema.json"
FAITHFUL_REBUILD_PLAN_SCHEMA_PATH = SCHEMA_DIRECTORY / "faithful-rebuild-plan.schema.json"
FAITHFUL_EVIDENCE_REPORT_SCHEMA_PATH = SCHEMA_DIRECTORY / "faithful-evidence-report.schema.json"
# Descriptive aliases remain public for callers that name the artifact type.
COMPILER_PLAN_PROPOSAL_SCHEMA_PATH = PROPOSAL_SCHEMA_PATH
REVIEW_DECISION_SCHEMA_PATH = REVIEW_SCHEMA_PATH
ASSET_PROPOSAL_SCHEMA_PATH = ASSET_PACK_PROPOSAL_SCHEMA_PATH
ASSET_REVIEW_SCHEMA_PATH = ASSET_MAPPING_REVIEW_SCHEMA_PATH
CLI_VERSION = "0.9.1-alpha"
TEMPLATE_IR_SCHEMA_VERSION = "0.2.0"
SUPPORTED_TEMPLATE_IR_SCHEMA_VERSIONS = ("0.2.0", "0.3.0")
JIANYING_PROFILE = "jianying-compatible-v1"
FAITHFUL_EVIDENCE_SCHEMA_VERSION = "0.9.1"
NLE_SCHEMA_VERSION = "0.9.1"
__version__ = CLI_VERSION
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
SCHEMA_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
PRIVACY_PROFILES = {"local-only", "cloud-assisted", "gpu-worker"}
MEDIA_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "video/mp4",
    "video/quicktime",
    "video/webm",
    "audio/wav",
    "audio/mpeg",
    "audio/mp4",
    "audio/x-matroska",
}
SHA256_CHUNK_SIZE = 1024 * 1024
ASSET_MANIFEST_SCHEMA_VERSIONS = frozenset({"0.1.0", "0.2.0"})
S1_STATIC_RENDERER_CAPABILITIES = {
    "motion_modes": frozenset({"static", "layout-only"}),
    "audio_modes": frozenset({"mute", "preserve-reference", "replace-upload"}),
    "lip_sync": False,
    "voice_clone": False,
}
_V03_REBUILD_REQUIREMENT_FIELDS = frozenset(
    {
        "motion_required",
        "motion_mode",
        "audio_mode",
        "lip_sync_required",
        "voice_likeness_rights_confirmed",
    }
)
_V03_MOTION_MODES = frozenset({"static", "layout-only", "pose-transfer", "video-to-video"})
_V03_AUDIO_MODES = frozenset(
    {"mute", "preserve-reference", "replace-upload", "rebuild-sfx", "clone-authorized-voice"}
)

_schema_validators: dict[Path, Any] = {}
_schema_validator_errors: dict[Path, str] = {}


class CliArgumentError(ValueError):
    """A command-line error that can be returned as bounded JSON."""


class _BoundedArgumentParser(argparse.ArgumentParser):
    """Avoid ``argparse`` usage text and a process exit for malformed input."""

    def error(self, message: str) -> None:  # pragma: no cover - wording differs by Python version.
        raise CliArgumentError(message)


def _lazy_module(name: str) -> Any:
    """Import a media module only when the corresponding command is used."""

    script_directory = str(Path(__file__).resolve().parent)
    if script_directory not in sys.path:
        sys.path.insert(0, script_directory)
    return importlib.import_module(name)


def _runtime_module() -> Any:
    return _lazy_module("rrv_runtime")


def _analyze_module() -> Any:
    return _lazy_module("rrv_analyze")


def _render_module() -> Any:
    return _lazy_module("rrv_render")


def _qa_module() -> Any:
    return _lazy_module("rrv_qa")


def _compile_module() -> Any:
    """Load the bounded reference compiler only for compiler commands."""

    return _lazy_module("rrv_compile")


def _propose_module() -> Any:
    """Load proposal/freeze support only for proposal workflow commands."""

    return _lazy_module("rrv_propose")


def _assets_module() -> Any:
    """Load the local asset-pack workflow only when an asset command needs it."""

    return _lazy_module("rrv_assets")


def _generation_module() -> Any:
    """Load reviewed generation-packet support only for v0.6 commands."""

    return _lazy_module("rrv_generation")


def _faithful_module() -> Any:
    """Load the v0.9 faithful-rebuild core only for faithful commands."""

    return _lazy_module("rrv_faithful")


def _faithful_evidence_module() -> Any:
    """Load the v0.9.1 faithful-evidence core only when it is requested."""

    return _lazy_module("rrv_faithful_evidence")


def _nle_module() -> Any:
    """Load the Jianying-compatible local delivery core only on demand."""

    return _lazy_module("rrv_nle")


def _compact_error_text(value: object, *, limit: int = 480) -> str:
    text = " ".join(str(value).strip().split())
    if not text:
        return "operation failed"
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


class _ContractDuplicateKeyError(ValueError):
    """A v0.4 packet contains duplicate object members."""


class _ContractNonfiniteNumberError(ValueError):
    """A v0.4 packet uses JSON's non-standard non-finite number spelling."""


class _PublicJsonDuplicateKeyError(ValueError):
    """A security-decisive public JSON document has duplicate members."""


class _PublicJsonNonfiniteNumberError(ValueError):
    """A security-decisive public JSON document has a non-finite number."""


class _PublicJsonInvalidError(ValueError):
    """A public JSON document cannot be decoded without exposing its input."""


def _reject_contract_nonfinite_json(value: str) -> None:
    # Never retain the spelling in an exception: it is public packet input.
    raise _ContractNonfiniteNumberError()


def _reject_duplicate_contract_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build one JSON object while rejecting duplicate keys at every depth."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            # Deliberately omit ``key``: it can be a private source label.
            raise _ContractDuplicateKeyError()
        result[key] = value
    return result


def _reject_public_nonfinite_json(value: str) -> None:
    """Reject non-standard JSON numbers without retaining their spelling."""

    raise _PublicJsonNonfiniteNumberError()


def _reject_duplicate_public_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build one public JSON object while rejecting recursive duplicate keys."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            # Keys can be source-controlled labels, so never include one in a
            # public diagnostic.
            raise _PublicJsonDuplicateKeyError()
        result[key] = value
    return result


def _contains_nonfinite_json_number(value: Any) -> bool:
    """Catch decoder overflow (for example ``1e9999``) as well as NaN tokens."""

    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, Mapping):
        return any(_contains_nonfinite_json_number(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_nonfinite_json_number(item) for item in value)
    return False


def _load_public_json_snapshot_bytes(path: Path) -> tuple[Any, bytes, str]:
    """Strict-load a public JSON input and return the exact bytes and digest.

    Template IR and Asset Manifest documents affect rendering decisions.  The
    parser must therefore reject duplicate members at every nesting level and
    the later provenance record must describe the very bytes that were parsed,
    rather than a path re-read after rendering has begun.
    """

    try:
        raw = Path(path).read_bytes()
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_public_nonfinite_json,
            object_pairs_hook=_reject_duplicate_public_members,
        )
        if _contains_nonfinite_json_number(value):
            raise _PublicJsonNonfiniteNumberError()
    except (_PublicJsonDuplicateKeyError, _PublicJsonNonfiniteNumberError):
        raise
    except Exception as exc:
        # Do not expose the path, parser location, invalid value, or decoder
        # text through a public validation/render surface.
        raise _PublicJsonInvalidError() from exc
    return value, raw, hashlib.sha256(raw).hexdigest()


def _load_public_json_snapshot(path: Path) -> tuple[Any, str]:
    value, _raw, digest = _load_public_json_snapshot_bytes(path)
    return value, digest


def _public_json_error(error: BaseException) -> str:
    """Map strict JSON loading failures to the fixed public vocabulary."""

    if isinstance(error, _PublicJsonDuplicateKeyError):
        return "$: json.duplicate_key"
    if isinstance(error, _PublicJsonNonfiniteNumberError):
        return "$: json.finite_number"
    return "$: json.invalid"


def load_json(path: Path) -> Any:
    """Load strict JSON; JSON's non-standard NaN and Infinity are rejected."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle, parse_constant=_reject_nonfinite_json)
    except FileNotFoundError as exc:
        raise ValueError(f"file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    except ValueError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc


def _load_contract_json(path: Path) -> Any:
    """Load a Proposal/Review packet with unambiguous JSON object semantics.

    Legacy contracts retain their historical loader.  v0.4 packets reject
    duplicate object members recursively so a reviewer, CLI, and downstream
    JSON implementation cannot disagree about which decision was supplied.
    """

    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(
                handle,
                parse_constant=_reject_contract_nonfinite_json,
                object_pairs_hook=_reject_duplicate_contract_members,
            )
    except (_ContractDuplicateKeyError, _ContractNonfiniteNumberError):
        raise
    except Exception as exc:
        # The caller returns a fixed public class, not parser text or a path.
        raise ValueError("contract JSON could not be loaded") from exc


def sha256_file(path: Path, chunk_size: int = SHA256_CHUNK_SIZE) -> str:
    """Return a file digest without loading media-sized files into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_path_segments(value: Any) -> tuple[str, ...] | None:
    """Return a canonical manifest-relative POSIX path, or ``None``.

    Asset manifests travel between Windows and POSIX workers.  A path that is
    harmless on the host running this validator can be absolute, rooted, or a
    traversal on the other platform, so the contract deliberately accepts a
    small common subset only.  Keep this lexical check independent from file
    existence so ``check_files=False`` is never a path-policy bypass.
    """

    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        return None
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value)
    if windows.is_absolute() or windows.drive or windows.root or posix.is_absolute():
        return None
    parts = tuple(value.split("/"))
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    # Portable manifests cannot rely on POSIX-only filename affordances such
    # as NTFS alternate streams, reserved devices, trailing dots/spaces, or
    # control characters.  Those spellings are either unsafe or normalize to
    # a different file on a Windows worker.
    reserved_windows_names = {"CON", "PRN", "AUX", "NUL"}
    for part in parts:
        stem = part.split(".", 1)[0].upper()
        if (
            any(ord(character) < 32 or character in '<>:"|?*' for character in part)
            or part.endswith((" ", "."))
            or stem in reserved_windows_names
            or (len(stem) == 4 and stem[:3] in {"COM", "LPT"} and stem[3] in "123456789")
        ):
            return None
    # A manifest path is specified in POSIX spelling.  This final check keeps
    # future changes to the earlier rules from accepting a non-normalized form.
    if PurePosixPath(*parts).as_posix() != value:
        return None
    return parts


def _safe_asset_path(root: Path, value: Any) -> Path | None:
    """Resolve a lexically safe asset path without exposing it in errors."""

    parts = _asset_path_segments(value)
    if parts is None:
        return None
    candidate = root.joinpath(*parts)
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved


def _safe_legacy_asset_path(root: Path, value: Any) -> Path | None:
    """Resolve one v0.1.0 host-native relative asset path safely.

    Asset Manifest 0.1.0 predates the portable frozen contract.  In
    particular, a Windows project legitimately names a child as
    ``assets\\legacy.png``.  Continue rejecting absolute and traversal forms
    on either supported platform, but let ``Path`` apply the current host's
    ordinary relative-path spelling.  Version 0.2.0 intentionally keeps the
    stricter POSIX-only helper above.
    """

    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    native = Path(value)
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value)
    if (
        native.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or bool(windows.root)
        or posix.is_absolute()
    ):
        return None
    if (
        ".." in native.parts
        or ".." in windows.parts
        or ".." in posix.parts
    ):
        return None
    candidate = root / native
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved


def command_version(command: str, args: list[str]) -> str | None:
    executable = shutil.which(command)
    if not executable:
        return None
    try:
        result = subprocess.run(
            [executable, *args], check=False, capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return "detected (version unavailable)"
    output = (result.stdout or result.stderr).strip().splitlines()
    return output[0] if output else "detected (version unavailable)"


def _pillow_available() -> bool:
    """Return whether the deterministic image compositor can be imported."""

    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True


def _distribution_version(distribution: str) -> str | None:
    """Return an installed distribution version without making doctor fail."""

    try:
        return importlib_metadata.version(distribution)
    except importlib_metadata.PackageNotFoundError:
        return None


def doctor_payload(
    *,
    ffmpeg: str | Path | None = None,
    ffprobe: str | Path | None = None,
) -> dict[str, Any]:
    """Report only local alpha capabilities that are truly available.

    Explicit executable paths intentionally go through the shared runtime
    discovery path so a portable FFmpeg installation is reflected accurately.
    """

    runtime = _runtime_module()
    tools = runtime.discover_tools(ffmpeg=ffmpeg, ffprobe=ffprobe, probe_versions=True)
    has_jsonschema = Draft202012Validator is not None
    template_schema_available = has_jsonschema and (
        _get_schema_validator(TEMPLATE_SCHEMA_PATH, "Template IR") is not None
    )
    asset_manifest_schema_available = has_jsonschema and (
        _get_schema_validator(ASSET_MANIFEST_SCHEMA_PATH, "asset manifest") is not None
    )
    compiler_plan_schema_available = has_jsonschema and (
        _get_schema_validator(COMPILER_PLAN_SCHEMA_PATH, "Compiler Plan") is not None
    )
    proposal_schema_available = has_jsonschema and (
        _get_schema_validator(PROPOSAL_SCHEMA_PATH, "Compiler Plan Proposal") is not None
    )
    review_schema_available = has_jsonschema and (
        _get_schema_validator(REVIEW_SCHEMA_PATH, "review decision") is not None
    )
    asset_pack_proposal_schema_available = has_jsonschema and (
        _get_schema_validator(ASSET_PACK_PROPOSAL_SCHEMA_PATH, "asset pack proposal")
        is not None
    )
    asset_mapping_review_schema_available = has_jsonschema and (
        _get_schema_validator(ASSET_MAPPING_REVIEW_SCHEMA_PATH, "asset mapping review")
        is not None
    )
    generation_request_schema_available = has_jsonschema and (
        _get_schema_validator(GENERATION_REQUEST_SCHEMA_PATH, "generation request")
        is not None
    )
    generation_plan_schema_available = has_jsonschema and (
        _get_schema_validator(GENERATION_PLAN_SCHEMA_PATH, "generation plan")
        is not None
    )
    generation_plan_review_schema_available = has_jsonschema and (
        _get_schema_validator(GENERATION_PLAN_REVIEW_SCHEMA_PATH, "generation plan review")
        is not None
    )
    generation_results_proposal_schema_available = has_jsonschema and (
        _get_schema_validator(
            GENERATION_RESULTS_PROPOSAL_SCHEMA_PATH, "generation results proposal"
        )
        is not None
    )
    generation_results_review_schema_available = has_jsonschema and (
        _get_schema_validator(
            GENERATION_RESULTS_REVIEW_SCHEMA_PATH, "generation results review"
        )
        is not None
    )
    faithful_rebuild_plan_schema_available = has_jsonschema and (
        _get_schema_validator(
            FAITHFUL_REBUILD_PLAN_SCHEMA_PATH, "faithful rebuild plan"
        )
        is not None
    )
    faithful_evidence_report_schema_available = has_jsonschema and (
        _get_schema_validator(
            FAITHFUL_EVIDENCE_REPORT_SCHEMA_PATH, "faithful evidence report"
        )
        is not None
    )
    # A discovered regular file is not evidence that it is the requested
    # executable.  Advertise media capabilities only after its own bounded
    # version probe identifies FFmpeg or FFprobe by the official prefix.
    has_ffmpeg = _tool_version_confirmed(tools.ffmpeg, "ffmpeg")
    has_ffprobe = _tool_version_confirmed(tools.ffprobe, "ffprobe")
    has_pillow = _pillow_available()
    compiler_core_available = _compiler_module_available()
    proposal_core_available = _propose_module_available("propose_reference")
    freeze_core_available = _propose_module_available("freeze_plan")
    asset_proposal_core_available = _assets_module_available("propose_asset_pack")
    asset_freeze_core_available = _assets_module_available("freeze_assets")
    generation_plan_core_available = _generation_module_available("prepare_generation")
    generation_results_core_available = _generation_module_available(
        "propose_generation_results"
    )
    generation_assembly_core_available = _generation_module_available(
        "assemble_generation_pack"
    )
    faithful_core_available = _faithful_module_available()
    faithful_evidence_core_available = _faithful_evidence_module_available()
    jianying_export_core_available = _nle_module_available("export_nle_delivery")
    jianying_verify_core_available = _nle_module_available("verify_nle_delivery")
    jianying_export_encoders_available = (
        _ffmpeg_has_jianying_encoders(tools.ffmpeg)
        if has_ffmpeg and has_ffprobe and jianying_export_core_available
        else False
    )
    asset_bound_render_core_available = _asset_bound_render_available()
    compiler_prerequisites = (
        has_ffmpeg
        and has_ffprobe
        and has_pillow
        and has_jsonschema
        and compiler_plan_schema_available
        and template_schema_available
        and compiler_core_available
    )
    proposal_prerequisites = (
        has_ffmpeg
        and has_ffprobe
        and has_pillow
        and has_jsonschema
        and compiler_plan_schema_available
        and proposal_schema_available
        and review_schema_available
        and proposal_core_available
    )
    freeze_prerequisites = (
        has_jsonschema
        and compiler_plan_schema_available
        and proposal_schema_available
        and review_schema_available
        and freeze_core_available
    )
    asset_proposal_prerequisites = (
        has_jsonschema
        and has_pillow
        and has_ffprobe
        and asset_pack_proposal_schema_available
        and asset_mapping_review_schema_available
        and asset_proposal_core_available
    )
    asset_freeze_prerequisites = (
        has_jsonschema
        and has_pillow
        and has_ffprobe
        and asset_pack_proposal_schema_available
        and asset_mapping_review_schema_available
        and asset_freeze_core_available
    )
    asset_bound_render_prerequisites = (
        has_ffmpeg
        and has_pillow
        and asset_manifest_schema_available
        and asset_bound_render_core_available
    )
    generation_schemas_available = (
        generation_request_schema_available
        and generation_plan_schema_available
        and generation_plan_review_schema_available
        and generation_results_proposal_schema_available
        and generation_results_review_schema_available
    )
    generation_planning_prerequisites = (
        has_jsonschema
        and has_pillow
        and has_ffprobe
        and generation_schemas_available
        and generation_plan_core_available
    )
    generation_result_review_prerequisites = (
        has_jsonschema
        and has_pillow
        and has_ffprobe
        and generation_schemas_available
        and generation_results_core_available
    )
    generation_pack_assembly_prerequisites = (
        has_jsonschema
        and has_pillow
        and has_ffprobe
        and generation_schemas_available
        and generation_assembly_core_available
    )
    faithful_rebuild_prerequisites = (
        has_jsonschema
        and has_ffmpeg
        and has_ffprobe
        and faithful_rebuild_plan_schema_available
        and faithful_core_available
    )
    faithful_evidence_prerequisites = (
        has_jsonschema
        and has_ffmpeg
        and has_ffprobe
        and has_pillow
        and faithful_rebuild_plan_schema_available
        and faithful_evidence_report_schema_available
        and faithful_core_available
        and faithful_evidence_core_available
    )
    jianying_export_prerequisites = (
        has_ffmpeg
        and has_ffprobe
        and jianying_export_encoders_available
        and jianying_export_core_available
    )
    jianying_verify_prerequisites = (
        has_ffmpeg and has_ffprobe and jianying_verify_core_available
    )
    public_ffmpeg = _public_doctor_tool(tools.ffmpeg)
    public_ffprobe = _public_doctor_tool(tools.ffprobe)
    return {
        "status": "ok",
        "stage": "alpha",
        "version": CLI_VERSION,
        "template_ir_schema_version": TEMPLATE_IR_SCHEMA_VERSION,
        "template_ir_schema_versions": list(SUPPORTED_TEMPLATE_IR_SCHEMA_VERSIONS),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "runtime": {
            "media_tools": {"ffmpeg": public_ffmpeg, "ffprobe": public_ffprobe},
            "ffmpeg": public_ffmpeg,
            "ffprobe": public_ffprobe,
            "node": command_version("node", ["--version"]),
            "npx": command_version("npx", ["--version"]),
            "nvidia_gpu": command_version(
                "nvidia-smi", ["--query-gpu=name,memory.total", "--format=csv,noheader"]
            ),
            "jsonschema": has_jsonschema,
            "jsonschema_version": (
                _distribution_version("jsonschema") if has_jsonschema else None
            ),
            "pillow": has_pillow,
            "pillow_version": _distribution_version("Pillow") if has_pillow else None,
        },
        "capabilities": {
            "doctor": True,
            "template_validation": template_schema_available,
            "asset_manifest_structure_validation": asset_manifest_schema_available,
            "compiler_plan_validation": compiler_plan_schema_available,
            "proposal_validation": proposal_schema_available,
            "review_validation": review_schema_available,
            "compiler_plan_proposal": proposal_prerequisites,
            "compiler_plan_freeze": freeze_prerequisites,
            "asset_path_policy_validation": True,
            "asset_media_probe_validation": False,
            "asset_pack_proposal": asset_proposal_prerequisites,
            "asset_review_freeze": asset_freeze_prerequisites,
            "asset_bound_render": asset_bound_render_prerequisites,
            "generation_request_validation": generation_request_schema_available,
            "generation_plan_validation": generation_plan_schema_available,
            "generation_plan_review_validation": generation_plan_review_schema_available,
            "generation_results_proposal_validation": generation_results_proposal_schema_available,
            "generation_results_review_validation": generation_results_review_schema_available,
            "generation_planning": generation_planning_prerequisites,
            "generation_result_review": generation_result_review_prerequisites,
            "generation_pack_assembly": generation_pack_assembly_prerequisites,
            "faithful_rebuild": faithful_rebuild_prerequisites,
            "faithful_evidence": faithful_evidence_prerequisites,
            "jianying_export": jianying_export_prerequisites,
            "jianying_verify": jianying_verify_prerequisites,
            "media_probe": has_ffprobe or has_ffmpeg,
            "reference_survey": has_ffmpeg,
            "reference_analysis": compiler_prerequisites,
            "semantic_slot_analysis": False,
            "template_compilation": compiler_prerequisites,
            "asset_generation": False,
            "network_generation": False,
            "cloud_generation": False,
            "subject_motion_replication": False,
            "pose_transfer": False,
            "video_to_video": False,
            "audio_rebuild": False,
            "voice_clone": False,
            "lip_sync": False,
            "timeline_render": (
                has_ffmpeg
                and has_pillow
                and template_schema_available
                and asset_manifest_schema_available
            ),
            "video_qa": has_ffmpeg,
        },
        "notes": [
            "S1 survey, deterministic local render, and technical video QA are available only when their reported tools are present.",
            "Compiler Plan proposal and freeze are limited to authorized local fixed-subject-carousel S1 work and always require explicit human review before freeze.",
            "Asset-pack proposal and freeze require local Pillow, FFprobe, both asset packet schemas, and their guarded local core.",
            "Generation planning and result review prepare and validate local review packets only; this Skill does not include or automatically call an asset generator, network service, or cloud provider.",
            "Faithful rebuild is available only with a valid reviewed plan, local FFmpeg and FFprobe version probes, the faithful-plan schema, and its guarded local core.",
            "Faithful evidence requires an approved faithful plan plus local FFmpeg, FFprobe, Pillow, both faithful schemas, and its guarded local core; it performs no OCR or semantic inference.",
            "Jianying-compatible delivery export and verification require explicit rights confirmation, local FFmpeg and FFprobe version probes, and their guarded local core.",
            "Semantic slot analysis and asset generation remain unavailable; render-ready replacement looks must be supplied before this CLI renders.",
            "Timeline render is static/2D compositing only; it does not provide subject-motion replication, voice cloning, audio rebuild, or lip sync.",
            "This alpha does not promise pixel-perfect replacement for arbitrary videos or recovery of pixels obscured by overlays.",
        ],
    }


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _path(parent: str, component: object) -> str:
    return f"{parent}[{component}]" if isinstance(component, int) else f"{parent}.{component}"


def _find_nonfinite(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        errors.append(f"{path} must be finite (NaN and Infinity are not allowed)")
    elif isinstance(value, dict):
        for key, child in value.items():
            _find_nonfinite(child, _path(path, key), errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _find_nonfinite(child, _path(path, index), errors)


def _get_schema_validator(schema_path: Path, contract_name: str) -> Any | None:
    # Do not let a validator cached before a package/file removal advertise a
    # capability that is no longer installed.  This also keeps doctor honest
    # for partially installed Skills.
    unavailable_message = f"{contract_name} JSON Schema is unavailable"
    if not schema_path.is_file():
        _schema_validators.pop(schema_path, None)
        _schema_validator_errors[schema_path] = unavailable_message
        return None
    if _schema_validator_errors.get(schema_path) == unavailable_message:
        _schema_validator_errors.pop(schema_path, None)
    if schema_path in _schema_validators or schema_path in _schema_validator_errors:
        return _schema_validators.get(schema_path)
    if Draft202012Validator is None:
        _schema_validator_errors[schema_path] = (
            f"jsonschema dependency is required for complete {contract_name} validation; "
            "run `python -m pip install -r requirements-runtime.txt` from the installed Skill "
            "directory (jsonschema>=4.23,<5)."
        )
        return None
    try:
        schema = load_json(schema_path)
        Draft202012Validator.check_schema(schema)
        _schema_validators[schema_path] = Draft202012Validator(schema)
    except Exception as exc:  # pragma: no cover - repository contract failure
        _schema_validator_errors[schema_path] = f"unable to load {contract_name} JSON Schema: {exc}"
    return _schema_validators.get(schema_path)


def _compiler_module_available() -> bool:
    """Check the compiler surface without exposing an import failure publicly."""

    try:
        return callable(getattr(_compile_module(), "compile_reference", None))
    except Exception:
        return False


def _propose_module_available(operation: str) -> bool:
    """Check a proposal-workflow surface without exposing import failures."""

    try:
        return callable(getattr(_propose_module(), operation, None))
    except Exception:
        return False


def _assets_module_available(operation: str) -> bool:
    """Check one asset-pack core entry point without surfacing import details."""

    try:
        return callable(getattr(_assets_module(), operation, None))
    except Exception:
        return False


def _generation_module_available(operation: str) -> bool:
    """Check one v0.6 packet-workflow entry point without import details."""

    try:
        return callable(getattr(_generation_module(), operation, None))
    except Exception:
        return False


def _faithful_module_available() -> bool:
    """Check the complete public v0.9 faithful-rebuild surface."""

    try:
        module = _faithful_module()
        return (
            callable(getattr(module, "validate_faithful_plan", None))
            and callable(getattr(module, "execute_faithful_rebuild", None))
        )
    except Exception:
        return False


def _faithful_evidence_module_available() -> bool:
    """Check the guarded faithful-evidence entry point without import details."""

    try:
        return callable(getattr(_faithful_evidence_module(), "build_faithful_evidence", None))
    except Exception:
        return False


def _nle_module_available(operation: str) -> bool:
    """Check one Jianying delivery entry point without exposing import failures."""

    try:
        return callable(getattr(_nle_module(), operation, None))
    except Exception:
        return False


def _asset_bound_render_available() -> bool:
    """Return whether the renderer exposes the frozen-byte snapshot surface.

    Frozen Asset Manifest 0.2.0 rendering must bind the verified bytes once
    and close that private snapshot after rendering.  Checking both functions
    keeps ``doctor`` from advertising the new path against an older renderer
    that only resolves mutable filenames.
    """

    try:
        renderer = _render_module()
        return (
            callable(getattr(renderer, "render_project", None))
            and callable(getattr(renderer, "resolve_local_assets", None))
            and callable(getattr(renderer, "close_resolved_assets", None))
        )
    except Exception:
        return False


def _tool_version_confirmed(tool: Any, expected_tool: str) -> bool:
    """Require the tool's own official version prefix, not merely any output."""

    path = getattr(tool, "path", None)
    version = getattr(tool, "version", None)
    if not isinstance(path, str) or not path or not isinstance(version, str):
        return False
    prefix = f"{expected_tool} version "
    return version.strip().startswith(prefix)


_SAFE_DOCTOR_TOOL_SOURCE = re.compile(r"^(?:PATH|explicit|env:[A-Z][A-Z0-9_]*)$")


def _public_doctor_tool(tool: Any) -> dict[str, Any]:
    """Return inspectable tool facts without disclosing configured locations."""

    source = getattr(tool, "source", None)
    if not isinstance(source, str) or not _SAFE_DOCTOR_TOOL_SOURCE.fullmatch(source):
        source = None
    version = getattr(tool, "version", None)
    if isinstance(version, str):
        version = _compact_error_text(version, limit=240)
        # A first-line FFmpeg/FFprobe version never needs a filesystem
        # separator.  Drop surprising tool output rather than risk exposing a
        # build/install path through doctor.
        if _contains_absolute_path(version) or "/" in version or "\\" in version:
            version = None
    else:
        version = None
    return {
        "available": bool(getattr(tool, "path", None)),
        "path": None,
        "source": source,
        "version": version,
    }


def _ffmpeg_has_jianying_encoders(tool: Any) -> bool:
    """Confirm the two fixed-profile encoders through a bounded argv probe."""

    if not _tool_version_confirmed(tool, "ffmpeg"):
        return False
    executable = getattr(tool, "path", None)
    if not isinstance(executable, str) or not executable:
        return False
    try:
        result = subprocess.run(
            [executable, "-hide_banner", "-encoders"],
            check=False,
            shell=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    output = f"{result.stdout or ''}\n{result.stderr or ''}"
    return bool(
        re.search(r"(?m)^\s*[A-Z.]+\s+libx264(?:\s|$)", output)
        and re.search(r"(?m)^\s*[A-Z.]+\s+aac(?:\s|$)", output)
    )


def _schema_path(error: Any) -> str:
    path = "$"
    for item in error.absolute_path:
        path = _path(path, item)
    return path


def _validate_schema(data: Any, schema_path: Path, contract_name: str) -> list[str]:
    validator = _get_schema_validator(schema_path, contract_name)
    if validator is None:
        return [_schema_validator_errors.get(schema_path, "jsonschema dependency is unavailable")]
    errors = sorted(
        validator.iter_errors(data),
        key=lambda error: (tuple(str(item) for item in error.absolute_path), error.message),
    )
    return [f"{_schema_path(error)}: {error.message}" for error in errors]


_CONTRACT_POINTER_COMPONENTS = frozenset(
    {
        "schema_version",
        "template_id",
        "family",
        "privacy",
        "review_required",
        "source_fingerprint",
        "candidate_plan",
        "confidence",
        "candidates",
        "evidence",
        "limitations",
        "sha256",
        "width",
        "height",
        "frame_count",
        "fps",
        "has_audio",
        "overall",
        "source_rect",
        "carousel_boundary",
        "slot_count",
        "timing",
        "carousel_layout",
        "background_color",
        "carousel_boundaries",
        "slot_counts",
        "switch_frames",
        "y",
        "score",
        "method",
        "value",
        "frame",
        "prominence",
        "representative_frames",
        "artifacts",
        "overview_contact_sheet",
        "geometry_preview",
        "timing_profile",
        "path",
        "proposal_sha256",
        "decision",
        "reviewer_confirmed",
        "confirmations",
        "approved_plan",
        "notes",
        "geometry",
        "authorization",
        "carousel",
        "background",
        "audio",
        "output_profiles",
        "analysis",
        "source_rect",
        "carousel_rect",
        "subject_rect",
        "x",
        "mode",
        "min_segment_frames",
        "origin",
        "item_width",
        "item_height",
        "gap",
        "end_offset_x",
        "color",
        "replaceable",
        "required",
        "snap_window_frames",
        "min_prominence",
        "max_evidence_frames",
    }
)
_CONTRACT_SCHEMA_RULES = frozenset(
    {
        "additionalProperties",
        "allOf",
        "const",
        "enum",
        "exclusiveMinimum",
        "maxItems",
        "maxLength",
        "maximum",
        "minLength",
        "minItems",
        "minimum",
        "not",
        "pattern",
        "required",
        "type",
        "uniqueItems",
    }
)


def _safe_contract_schema_pointer(error: Any) -> str:
    """Return only known contract keys and indexes, never an instance key."""

    path = "$"
    for item in error.absolute_path:
        if _is_int(item) and item >= 0:
            path = _path(path, item)
        elif isinstance(item, str) and item in _CONTRACT_POINTER_COMPONENTS:
            path = _path(path, item)
        else:
            return "$"
    return path


def _safe_contract_schema_rule(error: Any) -> str:
    rule = getattr(error, "validator", None)
    return str(rule) if rule in _CONTRACT_SCHEMA_RULES else "invalid"


def _validate_contract_schema(data: Any, schema_path: Path, contract_name: str) -> list[str]:
    """Validate v0.4 packets without reflecting user-provided instances."""

    validator = _get_schema_validator(schema_path, contract_name)
    if validator is None:
        return ["$: schema.unavailable"]
    schema_errors = sorted(
        validator.iter_errors(data),
        key=lambda error: (tuple(str(item) for item in error.absolute_path), str(error.validator)),
    )
    return [
        f"{_safe_contract_schema_pointer(error)}: schema.{_safe_contract_schema_rule(error)}"
        for error in schema_errors
    ]


def _find_contract_nonfinite(value: Any, path: str, errors: list[str]) -> None:
    """Find non-finite numbers without reflecting arbitrary object keys.

    Proposal and review packets are public-facing.  Unlike the legacy
    validators, their diagnostics must never turn a hostile object key or
    value into output.  Known contract member names and array indexes are
    stable JSON Pointer components; all other object members are collapsed to
    the root pointer.
    """

    if len(errors) >= _MAX_CONTRACT_ERRORS:
        return
    if isinstance(value, float) and not math.isfinite(value):
        errors.append(f"{path}: schema.finite_number")
    elif isinstance(value, Mapping):
        for key, child in value.items():
            child_path = (
                _path(path, key)
                if isinstance(key, str) and key in _CONTRACT_POINTER_COMPONENTS
                else "$"
            )
            _find_contract_nonfinite(child, child_path, errors)
            if len(errors) >= _MAX_CONTRACT_ERRORS:
                return
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _find_contract_nonfinite(child, _path(path, index), errors)
            if len(errors) >= _MAX_CONTRACT_ERRORS:
                return


def _range_entries(
    entries: Iterable[tuple[str, Any]], duration: int | None, errors: list[str]
) -> list[tuple[int, int]]:
    valid: list[tuple[int, int]] = []
    previous_start: int | None = None
    previous_end: int | None = None
    for path, frame_range in entries:
        if not isinstance(frame_range, dict):
            continue
        start = frame_range.get("start_frame")
        end = frame_range.get("end_frame")
        if not _is_int(start) or not _is_int(end):
            continue
        if start >= end:
            errors.append(f"{path} must be a non-empty half-open range [start_frame, end_frame)")
            continue
        if duration is not None and not (0 <= start < end <= duration):
            errors.append(f"{path} must be within [0, {duration})")
        if previous_start is not None and start <= previous_start:
            errors.append(f"{path} must be strictly ascending by start_frame")
        if previous_end is not None and start < previous_end:
            errors.append(f"{path} overlaps the preceding range")
        previous_start, previous_end = start, end
        valid.append((start, end))
    return valid


def _rect_within(rect: Any, width: Any, height: Any, path: str, errors: list[str]) -> None:
    if not isinstance(rect, dict) or not _is_number(width) or not _is_number(height):
        return
    x, y, rect_width, rect_height = (rect.get(key) for key in ("x", "y", "width", "height"))
    if not all(_is_number(value) for value in (x, y, rect_width, rect_height)):
        return
    if x < 0 or y < 0 or x + rect_width > width or y + rect_height > height:
        errors.append(f"{path} must stay within its declared coordinate space")


def _points_within(points: Any, width: Any, height: Any, path: str, errors: list[str]) -> None:
    if not isinstance(points, list) or not _is_number(width) or not _is_number(height):
        return
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            continue
        x, y = point.get("x"), point.get("y")
        if _is_number(x) and _is_number(y) and (x < 0 or y < 0 or x > width or y > height):
            errors.append(f"{path}[{index}] must stay within its declared coordinate space")


def _validate_transform(
    transform: Any,
    path: str,
    duration: int | None,
    first_active_frame: int | None,
    errors: list[str],
) -> None:
    if not isinstance(transform, dict):
        return
    keyframes = transform.get("keyframes")
    if not isinstance(keyframes, list):
        return
    prior_frame: int | None = None
    first_frame: int | None = None
    for index, keyframe in enumerate(keyframes):
        if not isinstance(keyframe, dict):
            continue
        frame = keyframe.get("frame")
        if not _is_int(frame):
            continue
        if first_frame is None:
            first_frame = frame
        if duration is not None and not 0 <= frame < duration:
            errors.append(f"{path}.keyframes[{index}].frame must be within [0, {duration})")
        if prior_frame is not None and frame <= prior_frame:
            errors.append(f"{path}.keyframes[{index}].frame must be strictly increasing")
        prior_frame = frame
    if first_active_frame is not None and first_frame is not None and first_frame > first_active_frame:
        errors.append(f"{path}.keyframes[0].frame must not be later than the first active range")


def _validate_mask(
    mask: Any,
    path: str,
    slot_ids: set[str],
    canvas: dict[str, Any],
    errors: list[str],
    layout: dict[str, Any] | None = None,
) -> None:
    if mask is None or not isinstance(mask, dict):
        return
    mask_type = mask.get("type")
    if mask_type == "alpha-asset":
        slot_id = mask.get("slot_id")
        if isinstance(slot_id, str) and slot_id not in slot_ids:
            errors.append(f"{path}.slot_id references unknown slot {slot_id}")
        return
    if mask.get("space") == "canvas":
        width, height = canvas.get("width"), canvas.get("height")
    elif mask.get("space") == "layer" and isinstance(layout, dict):
        box = layout.get("box")
        width = box.get("width") if isinstance(box, dict) else None
        height = box.get("height") if isinstance(box, dict) else None
    else:
        return
    if mask_type in {"rect", "rounded-rect"}:
        rect = mask.get("rect")
        _rect_within(rect, width, height, f"{path}.rect", errors)
        if mask_type == "rounded-rect" and isinstance(rect, dict):
            radius = mask.get("corner_radius_px")
            rect_width, rect_height = rect.get("width"), rect.get("height")
            if _is_number(radius) and _is_number(rect_width) and _is_number(rect_height) and radius > min(rect_width, rect_height) / 2:
                errors.append(f"{path}.corner_radius_px cannot exceed half of the rectangle's shortest side")
    elif mask_type == "polygon":
        _points_within(mask.get("points"), width, height, f"{path}.points", errors)


def _validate_source_geometry(
    geometry: Any, path: str, source: dict[str, Any], errors: list[str]
) -> None:
    if not isinstance(geometry, dict):
        return
    width, height = source.get("width"), source.get("height")
    shape_type = geometry.get("type")
    if shape_type in {"rect", "rounded-rect"}:
        rect = geometry.get("rect")
        _rect_within(rect, width, height, f"{path}.rect", errors)
        if shape_type == "rounded-rect" and isinstance(rect, dict):
            radius = geometry.get("corner_radius_px")
            rect_width, rect_height = rect.get("width"), rect.get("height")
            if _is_number(radius) and _is_number(rect_width) and _is_number(rect_height) and radius > min(rect_width, rect_height) / 2:
                errors.append(f"{path}.corner_radius_px cannot exceed half of the rectangle's shortest side")
    elif shape_type == "polygon":
        _points_within(geometry.get("points"), width, height, f"{path}.points", errors)


def _validate_canvas(data: dict[str, Any], errors: list[str]) -> None:
    canvas = data.get("canvas")
    source = data.get("source")
    if not isinstance(canvas, dict) or not isinstance(source, dict):
        return
    _rect_within(canvas.get("source_rect"), source.get("width"), source.get("height"), "$.canvas.source_rect", errors)
    source_rect = canvas.get("source_rect")
    canvas_width, canvas_height = canvas.get("width"), canvas.get("height")
    if isinstance(source_rect, dict):
        rect_width, rect_height = source_rect.get("width"), source_rect.get("height")
        if all(_is_number(value) and value > 0 for value in (canvas_width, canvas_height, rect_width, rect_height)):
            if not math.isclose(rect_width / rect_height, canvas_width / canvas_height, rel_tol=1e-9, abs_tol=1e-9):
                errors.append(
                    "$.canvas.source_rect aspect ratio must match canvas width and height because no source-fit field exists"
                )


def _validate_remove_layers(
    remove_layers: Any,
    duration: int | None,
    source: dict[str, Any],
    canvas: dict[str, Any],
    errors: list[str],
) -> None:
    if not isinstance(remove_layers, list):
        return
    known_ids: set[str] = set()
    crop_layers: list[str] = []
    for index, layer in enumerate(remove_layers):
        path = f"$.remove_layers[{index}]"
        if not isinstance(layer, dict):
            continue
        layer_id = layer.get("id")
        if isinstance(layer_id, str):
            if layer_id in known_ids:
                errors.append(f"{path}.id duplicates {layer_id}")
            known_ids.add(layer_id)
        regions = layer.get("regions")
        if not isinstance(regions, list):
            continue
        for region_index, region in enumerate(regions):
            if not isinstance(region, dict):
                continue
            _range_entries(
                ((f"{path}.regions[{region_index}].active_range", region.get("active_range")),),
                duration,
                errors,
            )
            _validate_source_geometry(region.get("geometry"), f"{path}.regions[{region_index}].geometry", source, errors)
        policy = layer.get("policy")
        if policy == "crop-source-before-analysis":
            crop_layers.append(path)
            if len(regions) != 1:
                errors.append(f"{path} crop policy requires exactly one full-duration static rect region")
            elif isinstance(regions[0], dict):
                region = regions[0]
                frame_range = region.get("active_range")
                geometry = region.get("geometry")
                if region.get("operation") != "keep":
                    errors.append(f"{path} crop policy requires operation=keep")
                if not isinstance(frame_range, dict) or frame_range.get("start_frame") != 0 or frame_range.get("end_frame") != duration:
                    errors.append(f"{path} crop policy requires a full-duration [0, duration) range")
                if not isinstance(geometry, dict) or geometry.get("type") != "rect":
                    errors.append(f"{path} crop policy requires a static rect geometry")
                elif geometry.get("rect") != canvas.get("source_rect"):
                    errors.append(f"{path} crop policy keep rect must exactly match $.canvas.source_rect")
        elif policy in {"mask-and-rebuild", "exclude-from-reconstruction"}:
            for region_index, region in enumerate(regions):
                if isinstance(region, dict) and region.get("operation") != "remove":
                    errors.append(f"{path}.regions[{region_index}] {policy} requires operation=remove")
    if len(crop_layers) > 1:
        errors.append("$.remove_layers may contain at most one crop-source-before-analysis layer")


def _validate_template_semantics(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    source = data.get("source") if isinstance(data.get("source"), dict) else {}
    canvas = data.get("canvas") if isinstance(data.get("canvas"), dict) else {}
    duration = source.get("duration_frames") if _is_int(source.get("duration_frames")) else None
    fps = source.get("fps") if _is_number(source.get("fps")) else None
    _validate_canvas(data, errors)

    tracks = data.get("tracks") if isinstance(data.get("tracks"), list) else []
    track_ids: set[str] = set()
    track_by_id: dict[str, dict[str, Any]] = {}
    for index, track in enumerate(tracks):
        if not isinstance(track, dict):
            continue
        track_id = track.get("id")
        if isinstance(track_id, str):
            if track_id in track_ids:
                errors.append(f"$.tracks[{index}].id duplicates {track_id}")
            else:
                track_ids.add(track_id)
                track_by_id[track_id] = track

    slots = data.get("slots") if isinstance(data.get("slots"), list) else []
    slot_ids: set[str] = set()
    slot_by_id: dict[str, dict[str, Any]] = {}
    for index, slot in enumerate(slots):
        if not isinstance(slot, dict):
            continue
        slot_id = slot.get("id")
        if isinstance(slot_id, str):
            if slot_id in slot_ids:
                errors.append(f"$.slots[{index}].id duplicates {slot_id}")
            else:
                slot_ids.add(slot_id)
                slot_by_id[slot_id] = slot

    layers = data.get("layers") if isinstance(data.get("layers"), list) else []
    layer_ids: set[str] = set()
    layers_by_track: dict[str, list[tuple[int, dict[str, Any], list[tuple[int, int]]]]] = {}
    for index, layer in enumerate(layers):
        path = f"$.layers[{index}]"
        if not isinstance(layer, dict):
            continue
        layer_id = layer.get("id")
        if isinstance(layer_id, str):
            if layer_id in layer_ids:
                errors.append(f"{path}.id duplicates {layer_id}")
            layer_ids.add(layer_id)
        track_id = layer.get("track_id")
        if isinstance(track_id, str) and track_id not in track_ids:
            errors.append(f"{path}.track_id references unknown track {track_id}")
        source_ref = layer.get("source")
        slot_id = source_ref.get("slot_id") if isinstance(source_ref, dict) else None
        if isinstance(slot_id, str) and slot_id not in slot_ids:
            errors.append(f"{path}.source.slot_id references unknown slot {slot_id}")
        if isinstance(slot_id, str) and slot_by_id.get(slot_id, {}).get("type") == "garment" and isinstance(source_ref, dict) and source_ref.get("representation") != "render-ready":
            errors.append(f"{path}.source.representation must be render-ready for garment slots")
        active_ranges = layer.get("active_ranges")
        entries = (
            ((f"{path}.active_ranges[{range_index}]", frame_range) for range_index, frame_range in enumerate(active_ranges))
            if isinstance(active_ranges, list)
            else ()
        )
        ranges = _range_entries(entries, duration, errors)
        first_active = ranges[0][0] if ranges else None
        _validate_transform(layer.get("transform"), f"{path}.transform", duration, first_active, errors)
        _validate_mask(layer.get("mask"), f"{path}.mask", slot_ids, canvas, errors, layer.get("layout") if isinstance(layer.get("layout"), dict) else None)
        if isinstance(track_id, str):
            layers_by_track.setdefault(track_id, []).append((index, layer, ranges))

    for track_id, track in track_by_id.items():
        if track.get("overlap_policy") == "forbid":
            intervals: list[tuple[int, int, str]] = []
            for _, layer, ranges in layers_by_track.get(track_id, []):
                label = str(layer.get("id", "<unnamed>"))
                intervals.extend((start, end, label) for start, end in ranges)
            intervals.sort(key=lambda item: (item[0], item[1], item[2]))
            prior: tuple[int, int, str] | None = None
            for interval in intervals:
                if prior is not None and interval[0] < prior[1]:
                    errors.append(f"track {track_id} forbids overlap between layers {prior[2]} and {interval[2]}")
                if prior is None or interval[1] > prior[1]:
                    prior = interval
        group_layout = track.get("group_layout")
        if isinstance(group_layout, dict):
            _validate_transform(track.get("group_transform"), f"$.tracks[{tracks.index(track)}].group_transform", duration, 0, errors)
            _validate_mask(track.get("clip_mask"), f"$.tracks[{tracks.index(track)}].clip_mask", slot_ids, canvas, errors)
            if isinstance(track.get("clip_mask"), dict) and track["clip_mask"].get("space") != "canvas":
                errors.append(f"track {track_id}.clip_mask must use canvas space")
            item_slots = group_layout.get("item_slots")
            if isinstance(item_slots, list):
                members = layers_by_track.get(track_id, [])
                for item_index, item_slot in enumerate(item_slots):
                    if isinstance(item_slot, str) and item_slot not in slot_ids:
                        errors.append(f"track {track_id}.group_layout.item_slots[{item_index}] references unknown slot {item_slot}")
                    matching = [layer for _, layer, _ in members if isinstance(layer.get("source"), dict) and layer["source"].get("slot_id") == item_slot]
                    if len(matching) != 1:
                        errors.append(f"track {track_id}.group_layout.item_slots[{item_index}] must map to exactly one layer")
                        continue
                    box = matching[0].get("layout", {}).get("box") if isinstance(matching[0].get("layout"), dict) else None
                    origin = group_layout.get("origin")
                    if isinstance(box, dict) and isinstance(origin, dict):
                        item_width, item_height, gap = group_layout.get("item_width"), group_layout.get("item_height"), group_layout.get("gap")
                        origin_x, origin_y = origin.get("x"), origin.get("y")
                        if all(_is_number(value) for value in (item_width, item_height, gap, origin_x, origin_y)):
                            expected_x = origin_x + item_index * (item_width + gap) if group_layout.get("direction") == "horizontal" else origin_x
                            expected_y = origin_y if group_layout.get("direction") == "horizontal" else origin_y + item_index * (item_height + gap)
                            if not all(math.isclose(box.get(key), expected, abs_tol=1e-9) for key, expected in (("x", expected_x), ("y", expected_y), ("width", item_width), ("height", item_height)) if _is_number(box.get(key))):
                                errors.append(f"track {track_id} carousel layer for {item_slot} must match group_layout origin, size, and gap")

    _validate_remove_layers(data.get("remove_layers"), duration, source, canvas, errors)

    events = data.get("events") if isinstance(data.get("events"), list) else []
    event_ids: set[str] = set()
    switches: set[tuple[str, int]] = set()
    event_layer_keys: set[tuple[str, str, int]] = set()
    previous_event_key: tuple[int, str] | None = None
    for index, event in enumerate(events):
        path = f"$.events[{index}]"
        if not isinstance(event, dict):
            continue
        event_id = event.get("id")
        if isinstance(event_id, str):
            if event_id in event_ids:
                errors.append(f"{path}.id duplicates {event_id}")
            event_ids.add(event_id)
        frame = event.get("frame")
        if _is_int(frame) and duration is not None and not 0 <= frame < duration:
            errors.append(f"{path}.frame must be within [0, {duration})")
        track_id, slot_id = event.get("track_id"), event.get("slot_id")
        if _is_int(frame) and isinstance(event_id, str):
            event_key = (frame, event_id)
            if previous_event_key is not None and event_key <= previous_event_key:
                errors.append(f"{path} must be strictly ordered by (frame, id)")
            previous_event_key = event_key
        if isinstance(track_id, str) and track_id not in track_ids:
            errors.append(f"{path}.track_id references unknown track {track_id}")
        if isinstance(slot_id, str) and slot_id not in slot_ids:
            errors.append(f"{path}.slot_id references unknown slot {slot_id}")
        if isinstance(track_id, str) and _is_int(frame):
            key = (track_id, frame)
            if key in switches:
                errors.append(f"{path} duplicates a slot-switch on track {track_id} at frame {frame}")
            switches.add(key)
        if isinstance(track_id, str) and isinstance(slot_id, str) and _is_int(frame):
            matching_layers = [
                layer
                for _, layer, ranges in layers_by_track.get(track_id, [])
                if isinstance(layer.get("source"), dict)
                and layer["source"].get("slot_id") == slot_id
                and any(start == frame for start, _ in ranges)
            ]
            if len(matching_layers) != 1:
                errors.append(
                    f"{path} must match exactly one same-track layer with source.slot_id {slot_id} starting at frame {frame}"
                )
            else:
                event_layer_keys.add((track_id, slot_id, frame))

    subject_track_ids = {
        track_id for track_id, track in track_by_id.items() if track.get("type") == "subject"
    }
    for subject_track_id in subject_track_ids:
        for layer_index, layer, ranges in layers_by_track.get(subject_track_id, []):
            source_ref = layer.get("source")
            slot_id = source_ref.get("slot_id") if isinstance(source_ref, dict) else None
            if (
                isinstance(source_ref, dict)
                and source_ref.get("representation") == "render-ready"
                and isinstance(slot_id, str)
                and slot_by_id.get(slot_id, {}).get("type") == "garment"
            ):
                missing_starts = [
                    start
                    for start, _ in ranges
                    if (subject_track_id, slot_id, start) not in event_layer_keys
                ]
                if missing_starts:
                    errors.append(
                        f"$.layers[{layer_index}] render-ready garment layer on subject track {subject_track_id} "
                        f"is missing slot-switch events for active-range starts {missing_starts}"
                    )

    audio = data.get("audio")
    if isinstance(audio, dict):
        audio_slot = audio.get("slot_id")
        if isinstance(audio_slot, str) and audio_slot not in slot_ids:
            errors.append(f"$.audio.slot_id references unknown slot {audio_slot}")
        elif isinstance(audio_slot, str) and slot_by_id.get(audio_slot, {}).get("type") != "audio":
            errors.append("$.audio.slot_id must reference a slot with type audio")
        start, end = audio.get("timeline_start_frame"), audio.get("timeline_end_frame")
        if _is_int(start) and _is_int(end):
            if start >= end or (duration is not None and not 0 <= start < end <= duration):
                errors.append("$.audio timeline must be a non-empty range within the source duration")
            timeline_frames = end - start
            fade_in, fade_out = audio.get("fade_in_frames"), audio.get("fade_out_frames")
            if _is_int(fade_in) and _is_int(fade_out) and (fade_in > timeline_frames or fade_out > timeline_frames or fade_in + fade_out > timeline_frames):
                errors.append("$.audio fades must fit within the timeline duration")
            source_in, source_out, rate = audio.get("source_in_ms"), audio.get("source_out_ms"), audio.get("playback_rate")
            if _is_number(source_in) and _is_number(source_out) and source_out <= source_in:
                errors.append("$.audio.source_out_ms must be greater than source_in_ms")
            if audio.get("loop") is False and _is_number(source_in) and _is_number(source_out) and _is_number(rate) and rate > 0 and _is_number(fps) and fps > 0:
                coverage_ms = (source_out - source_in) / rate
                expected_ms = timeline_frames * 1000 / fps
                if abs(coverage_ms - expected_ms) > 1000 / fps + 1e-9:
                    errors.append("$.audio non-looping source coverage must match the timeline within one frame")

    outputs = data.get("outputs") if isinstance(data.get("outputs"), list) else []
    output_ids: set[str] = set()
    for index, output in enumerate(outputs):
        if not isinstance(output, dict):
            continue
        output_id = output.get("id")
        if isinstance(output_id, str):
            if output_id in output_ids:
                errors.append(f"$.outputs[{index}].id duplicates {output_id}")
            output_ids.add(output_id)
    return errors


def ordered_layers(template: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the deterministic renderer order; this does not render media."""
    tracks = template.get("tracks", [])
    z_index = {track.get("id"): track.get("z_index") for track in tracks if isinstance(track, dict)}
    layers = [layer for layer in template.get("layers", []) if isinstance(layer, dict)]
    return sorted(
        layers,
        key=lambda layer: (z_index.get(layer.get("track_id"), 0), layer.get("z_offset", 0), layer.get("id", "")),
    )


def validate_template_data(data: Any) -> list[str]:
    errors: list[str] = []
    _find_nonfinite(data, "$", errors)
    schema_errors = _validate_schema(data, TEMPLATE_SCHEMA_PATH, "Template IR")
    errors.extend(schema_errors)
    if _get_schema_validator(TEMPLATE_SCHEMA_PATH, "Template IR") is None or not isinstance(data, dict):
        return errors
    errors.extend(_validate_template_semantics(data))
    return errors


def validate_compiler_plan_data(data: Any) -> list[str]:
    """Validate the frozen Compiler Plan's local structural contract.

    Media-relative rules (source bounds, timing fit, audio presence, and
    renderability) deliberately remain in ``rrv_compile.compile_reference``.
    This function performs no media access and never writes project files.
    """

    errors: list[str] = []
    _find_nonfinite(data, "$", errors)
    errors.extend(_validate_schema(data, COMPILER_PLAN_SCHEMA_PATH, "Compiler Plan"))
    return errors


_MAX_CONTRACT_ERRORS = 64
_MAX_CONTRACT_ERROR_LENGTH = 360


def _bounded_contract_errors(errors: Iterable[str]) -> list[str]:
    """Return deterministic, context-safe validation errors for v0.4 packets."""

    bounded: list[str] = []
    seen: set[str] = set()
    for error in errors:
        compact = _compact_error_text(error, limit=_MAX_CONTRACT_ERROR_LENGTH)
        if compact in seen:
            continue
        seen.add(compact)
        bounded.append(compact)
        if len(bounded) >= _MAX_CONTRACT_ERRORS:
            break
    return bounded


def _safe_nested_plan_errors(prefix: str, plan: Any, errors: Iterable[str]) -> list[str]:
    """Retain nested-plan validation without returning its legacy messages.

    ``validate_compiler_plan_data`` predates the public v0.4 packet surface
    and intentionally provides detailed diagnostics.  It may therefore quote
    invalid values.  Proposal/review validators still invoke it as the frozen
    compatibility gate, then derive public-safe diagnostics directly from the
    same schema and finite-number rule.
    """

    legacy_errors = tuple(errors)
    if not legacy_errors:
        return []
    safe_errors: list[str] = []
    _find_contract_nonfinite(plan, "$", safe_errors)
    safe_errors.extend(
        _validate_contract_schema(plan, COMPILER_PLAN_SCHEMA_PATH, "Compiler Plan")
    )
    nested: list[str] = []
    for error in _bounded_contract_errors(safe_errors):
        if error.startswith("$"):
            nested.append(f"{prefix}{error[1:]}")
        else:  # Defensive fallback: no legacy text may reach a public packet.
            nested.append(f"{prefix}: schema.compiler_plan")
    return nested or [f"{prefix}: schema.compiler_plan"]


def _validate_relative_artifact_path(
    value: Any,
    path: str,
    errors: list[str],
    *,
    project_root: Path | None = None,
) -> None:
    """Enforce portable, project-contained evidence paths without reading media."""

    if not isinstance(value, str):
        return
    if not value or value != value.strip() or "\x00" in value:
        errors.append(f"{path}: path.invalid")
        return
    if "\\" in value:
        errors.append(f"{path}: path.separator")
        return
    if re.match(r"^[A-Za-z]:", value):
        errors.append(f"{path}: path.drive_qualified")
        return
    if value.startswith("/"):
        errors.append(f"{path}: path.rooted")
        return
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        errors.append(f"{path}: path.not_normalized")
        return
    if project_root is not None:
        try:
            root = project_root.resolve()
            resolved = (root.joinpath(*parts)).resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            errors.append(f"{path}: path.escapes_project_root")


def _validate_proposal_artifact_paths(
    data: Mapping[str, Any], errors: list[str], *, project_root: Path | None
) -> None:
    evidence = data.get("evidence")
    if not isinstance(evidence, Mapping):
        return
    artifacts = evidence.get("artifacts")
    if not isinstance(artifacts, Mapping):
        return
    for name in ("overview_contact_sheet", "geometry_preview", "timing_profile"):
        artifact = artifacts.get(name)
        if isinstance(artifact, Mapping):
            _validate_relative_artifact_path(
                artifact.get("path"),
                f"$.evidence.artifacts.{name}.path",
                errors,
                project_root=project_root,
            )


def _fingerprint_media(fingerprint: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Build only the frozen compiler's pure media facts from a fingerprint."""

    width = fingerprint.get("width")
    height = fingerprint.get("height")
    frame_count = fingerprint.get("frame_count")
    fps = fingerprint.get("fps")
    has_audio = fingerprint.get("has_audio")
    if (
        not _is_int(width)
        or width < 1
        or not _is_int(height)
        or height < 1
        or not _is_int(frame_count)
        or frame_count < 1
        or not _is_number(fps)
        or float(fps) <= 0
        or not isinstance(has_audio, bool)
    ):
        return None
    duration_seconds = frame_count / float(fps)
    streams: list[dict[str, Any]] = [
        {
            "type": "video",
            "width": width,
            "height": height,
            "frame_count": frame_count,
            "frame_rate": float(fps),
            "average_frame_rate": float(fps),
            "exact_duration_seconds": duration_seconds,
            "cfr_confirmed": True,
            "rotation_degrees": 0,
        }
    ]
    if has_audio:
        streams.append({"type": "audio"})
    return {"format": {"duration_seconds": duration_seconds}, "streams": streams}


def _proposal_plan_semantic_errors(
    plan: Any, fingerprint: Any, *, prefix: str
) -> list[str]:
    """Run the frozen pure plan semantics without probing or writing media.

    The proposal core uses the same synthetic-CFR facts before freeze.  Keeping
    this check here makes ``validate-proposal`` fail closed for a plan that is
    structurally legal but cannot fit its declared source fingerprint.
    """

    if not isinstance(plan, Mapping) or not isinstance(fingerprint, Mapping):
        return []
    media = _fingerprint_media(fingerprint)
    if media is None:
        return []
    try:
        compiler = _compile_module()
        media_info = compiler._media_info(media, require_exact_timing=True)
        compiler._validate_plan(plan, media_info)
    except Exception as exc:
        # The compiler's detailed exception text can include dimensions or
        # other caller-controlled fields.  It remains useful internally, but
        # this public validator must expose only fixed pointers/rule classes.
        signals: list[str] = []
        message = getattr(exc, "message", None)
        if isinstance(message, str):
            signals.append(message)
        details = getattr(exc, "details", None)
        if isinstance(details, Mapping):
            nested = details.get("errors")
            if isinstance(nested, list):
                signals.extend(item for item in nested if isinstance(item, str))
        try:
            signals.append(str(exc))
        except Exception:  # pragma: no cover - defensive third-party exception.
            pass
        signal = " ".join(signals).lower()
        pointer_suffix = next(
            (
                suffix
                for marker, suffix in (
                    ("source_rect", ".geometry.source_rect"),
                    ("carousel_rect", ".geometry.carousel_rect"),
                    ("subject_rect", ".geometry.subject_rect"),
                    ("switch_frames", ".timing.switch_frames"),
                    ("slot_count", ".timing.slot_count"),
                    ("min_segment_frames", ".timing.min_segment_frames"),
                    ("timing", ".timing"),
                    ("carousel", ".carousel"),
                    ("background", ".background"),
                    ("audio", ".audio"),
                    ("output_profiles", ".output_profiles"),
                    ("analysis", ".analysis"),
                    ("geometry", ".geometry"),
                )
                if marker in signal
            ),
            "",
        )
        return [f"{prefix}{pointer_suffix}: semantic.invalid"]
    return []


def validate_proposal_data(
    data: Any, *, project_root: Path | None = None
) -> list[str]:
    """Validate a v0.4 local proposal and its candidate frozen plan.

    JSON Schema owns packet shape.  The frozen candidate is deliberately
    delegated to the established Compiler Plan validator so a proposal cannot
    smuggle an unknown or stale plan form through a generic ``object`` field.
    """

    errors: list[str] = []
    _find_contract_nonfinite(data, "$", errors)
    errors.extend(
        _validate_contract_schema(data, PROPOSAL_SCHEMA_PATH, "Compiler Plan Proposal")
    )
    if not isinstance(data, Mapping):
        return _bounded_contract_errors(errors)
    candidate_plan = data.get("candidate_plan")
    if "candidate_plan" in data:
        candidate_errors = validate_compiler_plan_data(candidate_plan)
        errors.extend(
            _safe_nested_plan_errors(
                "$.candidate_plan", candidate_plan, candidate_errors
            )
        )
        if not candidate_errors:
            errors.extend(
                _proposal_plan_semantic_errors(
                    candidate_plan,
                    data.get("source_fingerprint"),
                    prefix="$.candidate_plan",
                )
            )
    _validate_proposal_artifact_paths(data, errors, project_root=project_root)
    return _bounded_contract_errors(errors)


def validate_review_data(data: Any) -> list[str]:
    """Validate a v0.4 review decision and its candidate approved plan."""

    errors: list[str] = []
    _find_contract_nonfinite(data, "$", errors)
    errors.extend(_validate_contract_schema(data, REVIEW_SCHEMA_PATH, "review decision"))
    if not isinstance(data, Mapping):
        return _bounded_contract_errors(errors)
    if "approved_plan" in data:
        approved_plan = data.get("approved_plan")
        errors.extend(
            _safe_nested_plan_errors(
                "$.approved_plan", approved_plan, validate_compiler_plan_data(approved_plan)
            )
        )
    return _bounded_contract_errors(errors)


def _validate_packet_file(path: Path, validator: Any, label: str) -> list[str]:
    """Load a public v0.4 packet without reflecting private parser input."""

    try:
        data = _load_contract_json(path)
    except _ContractDuplicateKeyError:
        return ["$: json.duplicate_key"]
    except _ContractNonfiniteNumberError:
        return ["$: json.finite_number"]
    except Exception:
        return ["$: json.invalid"]
    try:
        return _bounded_contract_errors(validator(data))
    except Exception:
        # A public validator must not turn an import/parser failure into a
        # private path, raw instance, or tool-output message.
        return ["$: validation.unavailable"]


# v0.5 asset packets have a deliberately separate diagnostic vocabulary from
# the v0.4 Compiler Plan packets.  Core validation is pure, but it owns the
# full schemas and semantic checks.  Normalize its result here before a public
# CLI response so an unexpected core/import failure cannot reflect a private
# filename, packet value, or FFprobe diagnostic.
_ASSET_PACKET_POINTER_COMPONENTS = frozenset(
    {
        "schema_version",
        "privacy_profile",
        "analysis_rights_confirmed",
        "review_required",
        "template_path",
        "template_sha256",
        "template_id",
        "asset_pack",
        "scanner_policy_version",
        "inventory",
        "inventory_sha256",
        "slot_candidates",
        "evidence",
        "asset_contact_sheet",
        "asset_id",
        "source_path",
        "sha256",
        "size_bytes",
        "media_type",
        "facts",
        "kind",
        "width",
        "height",
        "pixels",
        "duration_seconds",
        "audio_stream_count",
        "video_stream_count",
        "slot_id",
        "required",
        "type",
        "accepted_media",
        "representation_requirement",
        "status",
        "candidate_asset_ids",
        "proposal_sha256",
        "decision",
        "contact_sheet_reviewed",
        "local_only_confirmed",
        "mappings",
        "action",
        "content_reviewed",
        "media_compatibility_confirmed",
        "render_ready_confirmed",
        "rights_confirmed",
        "processor",
        "omit_confirmed",
    }
)
_ASSET_SCHEMA_RULES = frozenset(
    {
        "additionalProperties",
        "allOf",
        "const",
        "enum",
        "exclusiveMinimum",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "oneOf",
        "pattern",
        "required",
        "type",
        "uniqueItems",
        "invalid",
    }
)
_ASSET_SEMANTIC_RULES = frozenset(
    {
        "finite_number",
        "normalized_relative_path",
        "direct_child",
        "stable_sequence",
        "direct_asset_pack_file",
        "accepted_media",
        "image_facts",
        "image_bounds",
        "media_kind",
        "audio_bounds",
        "kind",
        "stable_source_order",
        "canonical_inventory_hash",
        "asset_ids",
        "stable_unique_order",
        "candidate_count",
        "unknown_asset",
        "exact_filename_only",
        "stable_slot_order",
        "approved_review_confirmations",
        "unique",
        "safe_slug",
        "use_confirmations",
        "required",
        "unresolved_approved",
        "semantic.invalid",
        "validation.unavailable",
        "validation.invalid",
    }
)


def _safe_asset_packet_pointer(value: str) -> str:
    """Collapse a core pointer to known packet members and numeric indexes."""

    if not value.startswith("$"):
        return "$"
    result = "$"
    offset = 1
    while offset < len(value):
        if value[offset] == ".":
            match = re.match(r"\.([A-Za-z_][A-Za-z0-9_]*)", value[offset:])
            if match is None or match.group(1) not in _ASSET_PACKET_POINTER_COMPONENTS:
                return "$"
            result += f".{match.group(1)}"
            offset += len(match.group(0))
        elif value[offset] == "[":
            match = re.match(r"\[([0-9]+)\]", value[offset:])
            if match is None:
                return "$"
            result += f"[{int(match.group(1))}]"
            offset += len(match.group(0))
        else:
            return "$"
    return result


def _safe_asset_validation_error(error: Any) -> str:
    """Return one fixed public asset-packet diagnostic class."""

    if not isinstance(error, str):
        return "$: validation.invalid"
    compact = _compact_error_text(error, limit=_MAX_CONTRACT_ERROR_LENGTH)
    if compact in {
        "asset proposal: validation_unavailable",
        "asset review: validation_unavailable",
    }:
        return "$: validation.unavailable"
    if ": " not in compact:
        return "$: validation.invalid"
    pointer, rule = compact.split(": ", 1)
    safe_pointer = _safe_asset_packet_pointer(pointer)
    if rule.startswith("schema."):
        schema_rule = rule.removeprefix("schema.")
        if schema_rule in _ASSET_SCHEMA_RULES:
            return f"{safe_pointer}: schema.{schema_rule}"
    elif rule in _ASSET_SEMANTIC_RULES:
        return f"{safe_pointer}: {rule}"
    return "$: validation.invalid"


def _bounded_asset_errors(errors: Any) -> list[str]:
    """Bound and deduplicate only the v0.5 fixed diagnostic vocabulary."""

    if isinstance(errors, (str, bytes)) or not isinstance(errors, Iterable):
        return ["$: validation.unavailable"]
    result: list[str] = []
    seen: set[str] = set()
    for error in errors:
        safe = _safe_asset_validation_error(error)
        if safe in seen:
            continue
        seen.add(safe)
        result.append(safe)
        if len(result) >= _MAX_CONTRACT_ERRORS:
            break
    return result


def validate_asset_proposal_data(data: Any) -> list[str]:
    """Run the pure v0.5 proposal validator without probing or writing media."""

    try:
        validator = getattr(_assets_module(), "validate_asset_proposal_data", None)
        if not callable(validator):
            return ["$: validation.unavailable"]
        return _bounded_asset_errors(validator(data))
    except Exception:
        return ["$: validation.unavailable"]


def validate_asset_review_data(data: Any) -> list[str]:
    """Run the pure v0.5 review validator without probing or writing media."""

    try:
        validator = getattr(_assets_module(), "validate_asset_review_data", None)
        if not callable(validator):
            return ["$: validation.unavailable"]
        return _bounded_asset_errors(validator(data))
    except Exception:
        return ["$: validation.unavailable"]


def _validate_asset_packet_file(path: Path, validator: Any) -> list[str]:
    """Strict-load and validate a v0.5 packet without relaying input text."""

    try:
        data = _load_contract_json(path)
    except _ContractDuplicateKeyError:
        return ["$: json.duplicate_key"]
    except _ContractNonfiniteNumberError:
        return ["$: json.finite_number"]
    except Exception:
        return ["$: json.invalid"]
    try:
        return _bounded_asset_errors(validator(data))
    except Exception:
        return ["$: validation.unavailable"]


def _generation_validation_errors(errors: Any) -> list[str]:
    """Collapse v0.6 packet diagnostics so prompts and source labels stay local."""

    if isinstance(errors, (str, bytes)) or not isinstance(errors, Iterable):
        return ["$: validation.unavailable"]
    try:
        return [] if not list(errors) else ["$: validation.invalid"]
    except Exception:
        return ["$: validation.unavailable"]


def _validate_generation_packet_data(operation: str, data: Any) -> list[str]:
    """Run one pure v0.6 packet validator without probing or writing media."""

    try:
        validator = getattr(_generation_module(), operation, None)
        if not callable(validator):
            return ["$: validation.unavailable"]
        return _generation_validation_errors(validator(data))
    except Exception:
        return ["$: validation.unavailable"]


def validate_generation_request_data(data: Any) -> list[str]:
    return _validate_generation_packet_data("validate_generation_request_data", data)


def validate_generation_plan_data(data: Any) -> list[str]:
    return _validate_generation_packet_data("validate_generation_plan_data", data)


def validate_generation_plan_review_data(data: Any) -> list[str]:
    return _validate_generation_packet_data("validate_generation_plan_review_data", data)


def validate_generation_results_proposal_data(data: Any) -> list[str]:
    return _validate_generation_packet_data(
        "validate_generation_results_proposal_data", data
    )


def validate_generation_results_review_data(data: Any) -> list[str]:
    return _validate_generation_packet_data(
        "validate_generation_results_review_data", data
    )


def _validate_generation_packet_file(path: Path, validator: Any) -> list[str]:
    """Strict-load a v0.6 packet without reflecting prompts or filenames."""

    try:
        data = _load_contract_json(path)
    except _ContractDuplicateKeyError:
        return ["$: json.duplicate_key"]
    except _ContractNonfiniteNumberError:
        return ["$: json.finite_number"]
    except Exception:
        return ["$: json.invalid"]
    try:
        return _generation_validation_errors(validator(data))
    except Exception:
        return ["$: validation.unavailable"]


def require_dict(value: Any, path: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return {}
    return value


def require_list(value: Any, path: str, errors: list[str]) -> list[Any]:
    if not isinstance(value, list):
        errors.append(f"{path} must be an array")
        return []
    return value


def validate_assets_data(
    template: Any,
    manifest: Any,
    manifest_path: Path,
    check_files: bool,
    project_root: Path | None = None,
) -> list[str]:
    errors = validate_template_data(template)
    if errors:
        return [f"template: {error}" for error in errors]
    _find_nonfinite(manifest, "$", errors)
    manifest_schema_errors = _validate_schema(
        manifest, ASSET_MANIFEST_SCHEMA_PATH, "asset manifest"
    )
    errors.extend(manifest_schema_errors)
    if (
        manifest_schema_errors
        or _get_schema_validator(ASSET_MANIFEST_SCHEMA_PATH, "asset manifest") is None
        or not isinstance(manifest, dict)
    ):
        return errors
    root = manifest
    assets = root["assets"]
    declared_slots = {item["id"]: item for item in template["slots"]}
    mapped_slots: set[str] = set()
    if root["template_id"] != template.get("template_id"):
        errors.append("$.template_id does not match the Template IR")
    schema_version = root.get("schema_version")
    # The schema enforces this too.  Repeating the closed version set here
    # documents the execution contract and keeps this semantic validator safe
    # if its schema is ever refactored.
    if schema_version not in ASSET_MANIFEST_SCHEMA_VERSIONS:
        errors.append("$.schema_version is not a supported Asset Manifest version")
    frozen_local_only = schema_version == "0.2.0"
    if frozen_local_only and root.get("privacy_profile") != "local-only":
        errors.append("$.privacy_profile must be local-only for Asset Manifest 0.2.0")
    root_path = (project_root or manifest_path.parent).resolve()
    root_exists = root_path.is_dir()
    if not root_exists:
        errors.append("project root does not exist or is not a directory")
    for index, item in enumerate(assets):
        asset = item
        slot_id = asset.get("slot_id")
        if slot_id not in declared_slots:
            errors.append(f"$.assets[{index}].slot_id references unknown slot {slot_id}")
        elif slot_id in mapped_slots:
            errors.append(f"$.assets[{index}].slot_id duplicates mapping for {slot_id}")
        else:
            mapped_slots.add(slot_id)
        media_type = asset.get("media_type")
        if slot_id in declared_slots and media_type not in declared_slots[slot_id]["accepted_media"]:
            errors.append(
                f"$.assets[{index}].media_type {media_type} is not accepted by slot {slot_id}"
            )
        path_value = asset.get("path")
        if frozen_local_only:
            if not isinstance(path_value, str):
                errors.append(f"$.assets[{index}].path is required for Asset Manifest 0.2.0")
            if not isinstance(asset.get("sha256"), str):
                errors.append(f"$.assets[{index}].sha256 is required for Asset Manifest 0.2.0")
            if "provider_asset_id" in asset:
                errors.append(f"$.assets[{index}].provider_asset_id is forbidden for Asset Manifest 0.2.0")
            if asset.get("cloud_upload_allowed") is not False:
                errors.append(
                    f"$.assets[{index}].cloud_upload_allowed must be false for Asset Manifest 0.2.0"
                )

        resolved = (
            _safe_asset_path(root_path, path_value)
            if frozen_local_only
            else _safe_legacy_asset_path(root_path, path_value)
        )
        if isinstance(path_value, str) and resolved is None:
            errors.append(f"$.assets[{index}].path violates the project-relative path policy")
            continue
        if not isinstance(path_value, str):
            # A provider-only v0.1 entry remains schema-legal.  It is not a
            # local file to preflight here; the renderer rejects it explicitly.
            continue
        if not root_exists or resolved is None:
            continue
        if check_files:
            try:
                available = resolved.is_file()
            except OSError:
                available = False
            if not available:
                errors.append(f"$.assets[{index}].path is unavailable")
                continue
            expected_sha256 = asset.get("sha256")
            if isinstance(expected_sha256, str):
                try:
                    actual_sha256 = sha256_file(resolved)
                except OSError:
                    errors.append(f"$.assets[{index}].path is unavailable")
                    continue
                if actual_sha256.lower() != expected_sha256.lower():
                    errors.append(f"$.assets[{index}].sha256 does not match file content")
    for slot_id in sorted(slot_id for slot_id, slot in declared_slots.items() if slot.get("required") is True and slot_id not in mapped_slots):
        errors.append(f"required slot is not mapped: {slot_id}")
    return errors


def emit(payload: Mapping[str, Any], as_json: bool) -> None:
    """Preserve the original validator command output contract."""

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
        return
    print(payload.get("status", "unknown"))
    for error in payload.get("errors", []):
        print(f"- {error}")


def _stable_json(payload: Mapping[str, Any]) -> str:
    """Use the shared serializer when the runtime is importable."""

    try:
        return _runtime_module().stable_json_dumps(payload)
    except Exception:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)


def _emit_stable_json(payload: Mapping[str, Any]) -> None:
    print(_stable_json(payload))


def _error_payload(exception: BaseException, *, invalid_argument: bool = False) -> dict[str, Any]:
    """Return a bounded public error envelope without a traceback."""

    message = _compact_error_text(exception)
    try:
        runtime = _runtime_module()
    except Exception:
        return {
            "schema_version": "1.0",
            "status": "error",
            "error": {
                "code": "invalid_argument" if invalid_argument else "operation_failed",
                "message": message,
            },
        }
    if isinstance(exception, runtime.RRVError):
        return runtime.error_payload(exception)
    code = runtime.ERR_INVALID_ARGUMENT if invalid_argument else runtime.ERR_TOOL_EXECUTION
    return runtime.error_payload(runtime.RRVError(code, message))


def _deduplicate_errors(errors: Iterable[str]) -> list[str]:
    """Keep validation failures stable without repeating template errors."""

    result: list[str] = []
    seen: set[str] = set()
    for error in errors:
        if error not in seen:
            seen.add(error)
            result.append(error)
    return result


def _template_requires_review(template: Mapping[str, Any]) -> bool:
    """Return whether a frozen template still has an unresolved review gate."""

    support = template.get("support")
    return isinstance(support, Mapping) and support.get("review_required") is True


def _static_renderer_capability_unavailable(template: Mapping[str, Any]) -> str | None:
    """Return a fixed capability key S1 cannot satisfy for Template IR 0.3.

    This deliberately duplicates the renderer's tiny declaration parser.  The
    CLI must decide before importing ``rrv_render`` or checking replacement
    assets, so importing the renderer as a source of truth would defeat the
    fail-closed boundary.
    """

    if template.get("schema_version") != "0.3.0":
        return None
    requirements = template.get("rebuild_requirements")
    if not isinstance(requirements, Mapping) or set(requirements) != _V03_REBUILD_REQUIREMENT_FIELDS:
        return "rebuild_requirements"
    motion_required = requirements.get("motion_required")
    motion_mode = requirements.get("motion_mode")
    audio_mode = requirements.get("audio_mode")
    lip_sync_required = requirements.get("lip_sync_required")
    voice_rights = requirements.get("voice_likeness_rights_confirmed")
    if not all(
        isinstance(value, bool)
        for value in (motion_required, lip_sync_required, voice_rights)
    ):
        return "rebuild_requirements"
    if motion_mode not in _V03_MOTION_MODES or audio_mode not in _V03_AUDIO_MODES:
        return "rebuild_requirements"
    if motion_required and motion_mode not in {"pose-transfer", "video-to-video"}:
        return "rebuild_requirements"
    if not motion_required and motion_mode not in {"static", "layout-only"}:
        return "rebuild_requirements"
    if lip_sync_required and (not motion_required or audio_mode == "mute"):
        return "rebuild_requirements"
    if audio_mode == "clone-authorized-voice" and voice_rights is not True:
        return "rebuild_requirements"
    if motion_mode not in S1_STATIC_RENDERER_CAPABILITIES["motion_modes"]:
        return f"motion_mode.{motion_mode}"
    if lip_sync_required is True:
        return "lip_sync"
    if audio_mode == "clone-authorized-voice":
        return "voice_clone"
    if audio_mode not in S1_STATIC_RENDERER_CAPABILITIES["audio_modes"]:
        return f"audio_mode.{audio_mode}"
    return None


def _render_completion_status(template: Mapping[str, Any]) -> str:
    """Never overclaim semantic completion from an S1 technical render."""

    return (
        "structural_scope_review_required"
        if template.get("schema_version") == "0.3.0"
        else "structure_only_unclaimed"
    )


def _consumed_json_sha256(value: Any) -> str:
    """Hash an already-consumed JSON value without reopening its source path."""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("render input JSON cannot be hashed") from exc
    return hashlib.sha256(encoded).hexdigest()


def _legacy_renderer_path_is_safe(value: Any) -> bool:
    """Accept a renderer-reported v0.1 relative path without resolving it."""

    if not isinstance(value, str) or not value or "\x00" in value:
        return False
    native = Path(value)
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value)
    return not (
        native.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or bool(windows.root)
        or posix.is_absolute()
        or ".." in native.parts
        or ".." in windows.parts
        or ".." in posix.parts
    )


def _render_hashes(
    template_path: Path,
    manifest_path: Path,
    template: Mapping[str, Any],
    manifest: Mapping[str, Any],
    project_root: Path,
    runtime: Any,
    *,
    renderer_summary: Mapping[str, Any] | None = None,
    template_sha256: str | None = None,
    manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Record the inputs actually consumed by a deterministic delivery.

    ``template_path`` and ``manifest_path`` remain in the public helper's
    historical signature, but deliberately are never reopened here.  Normal
    CLI rendering supplies raw-byte digests captured before validation; the
    in-memory fallback preserves compatibility for direct callers that have
    already supplied parsed documents.
    """

    del template_path, manifest_path, project_root, runtime
    if template_sha256 is None:
        template_sha256 = _consumed_json_sha256(template)
    if manifest_sha256 is None:
        manifest_sha256 = _consumed_json_sha256(manifest)
    if (
        re.fullmatch(r"[0-9a-f]{64}", template_sha256) is None
        or re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is None
    ):
        raise ValueError("render input hashes are invalid")

    source = template.get("source") if isinstance(template.get("source"), Mapping) else {}
    asset_rows: list[dict[str, Any]] = []
    raw_assets = manifest.get("assets") if isinstance(manifest.get("assets"), list) else []
    result = {
        "template_sha256": template_sha256,
        "manifest_sha256": manifest_sha256,
        "source_sha256": source.get("source_sha256"),
        "assets": asset_rows,
    }
    if manifest.get("schema_version") == "0.2.0":
        # Frozen assets are verified while resolving and may be replaced or
        # deleted immediately afterwards.  Provenance must therefore consume
        # the renderer's bound snapshot digest, never hash the mutable path a
        # second time.
        if not isinstance(renderer_summary, Mapping):
            raise ValueError("renderer did not return bound asset provenance")
        summary_assets = renderer_summary.get("assets")
        if not isinstance(summary_assets, list):
            raise ValueError("renderer did not return bound asset provenance")
        expected_paths: dict[str, str] = {}
        for item in raw_assets:
            if not isinstance(item, Mapping):
                raise ValueError("frozen manifest contains an invalid asset")
            slot_id = item.get("slot_id")
            path_value = item.get("path")
            if not isinstance(slot_id, str) or _asset_path_segments(path_value) is None:
                raise ValueError("frozen manifest contains an invalid asset")
            expected_paths[slot_id] = str(path_value)

        returned_slots: set[str] = set()
        for item in summary_assets:
            if not isinstance(item, Mapping):
                raise ValueError("renderer returned invalid bound asset provenance")
            slot_id = item.get("slot_id")
            path_value = item.get("path")
            digest = item.get("sha256")
            if (
                not isinstance(slot_id, str)
                or expected_paths.get(slot_id) != path_value
                or slot_id in returned_slots
                or _asset_path_segments(path_value) is None
                or not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            ):
                raise ValueError("renderer returned invalid bound asset provenance")
            returned_slots.add(slot_id)
            asset_rows.append({"slot_id": slot_id, "path": path_value, "sha256": digest})
        if returned_slots != set(expected_paths):
            raise ValueError("renderer returned incomplete bound asset provenance")
        asset_rows.sort(key=lambda item: str(item["slot_id"]))
        return result

    # Legacy rendering does not bind source asset bytes.  Use the renderer's
    # already-produced relative paths for provenance and omit a misleading
    # post-render file hash.  A lightweight mock may omit ``assets`` entirely.
    summary_assets = renderer_summary.get("assets", []) if isinstance(renderer_summary, Mapping) else []
    if not isinstance(summary_assets, list):
        raise ValueError("renderer returned invalid asset provenance")
    for item in summary_assets:
        if not isinstance(item, Mapping):
            raise ValueError("renderer returned invalid asset provenance")
        slot_id = item.get("slot_id")
        path_value = item.get("path")
        if not isinstance(slot_id, str) or not _legacy_renderer_path_is_safe(path_value):
            raise ValueError("renderer returned invalid asset provenance")
        row: dict[str, Any] = {"slot_id": slot_id, "path": path_value}
        digest = item.get("sha256")
        if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None:
            row["sha256"] = digest
        asset_rows.append(row)
    asset_rows.sort(key=lambda item: str(item["slot_id"]))
    return result


def _provenance_source_type(value: object) -> str | None:
    """Keep a stable discovery category without leaking a configured path."""

    if value in {"explicit", "PATH"}:
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"env:[A-Z][A-Z0-9_]*", value):
        return value
    return None


def _tool_runtime_provenance(tool: Any) -> dict[str, Any]:
    """Return reproducible tool facts while intentionally omitting ``tool.path``."""

    return {
        "available": bool(getattr(tool, "path", None)),
        "source": _provenance_source_type(getattr(tool, "source", None)),
        "version": getattr(tool, "version", None),
    }


def _render_runtime_provenance(tools: Any) -> dict[str, Any]:
    """Record only non-sensitive runtime facts for a completed render."""

    has_pillow = _pillow_available()
    has_jsonschema = Draft202012Validator is not None
    return {
        "python_version": platform.python_version(),
        "pillow": {
            "available": has_pillow,
            "version": _distribution_version("Pillow") if has_pillow else None,
        },
        "jsonschema": {
            "available": has_jsonschema,
            "version": _distribution_version("jsonschema") if has_jsonschema else None,
        },
        "ffmpeg": _tool_runtime_provenance(tools.ffmpeg),
        "ffprobe": _tool_runtime_provenance(tools.ffprobe),
    }


def _write_summary(
    runtime: Any,
    project_root: Path,
    requested_path: Path,
    payload: Mapping[str, Any],
) -> str:
    """Write a stable optional run summary once, never replacing a prior run."""

    output = runtime.resolve_output_path(
        project_root, requested_path, create_parent=True, must_not_exist=True
    )
    try:
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(runtime.stable_json_dumps(payload))
            handle.write("\n")
    except FileExistsError as exc:
        raise runtime.RRVError(
            runtime.ERR_OUTPUT_EXISTS, "refusing to overwrite an existing summary"
        ) from exc
    except OSError as exc:
        raise runtime.RRVError(
            runtime.ERR_TOOL_EXECUTION,
            "could not write render summary",
            {"reason": _compact_error_text(exc)},
        ) from exc
    return runtime.relative_output_path(project_root, output)


_RENDER_TEMPLATE_SHA256_ATTRIBUTE = "_rrv_template_input_sha256"
_RENDER_MANIFEST_SHA256_ATTRIBUTE = "_rrv_manifest_input_sha256"


def _clear_render_input_hashes(args: argparse.Namespace) -> None:
    """Prevent a reused Namespace from carrying a prior request's digest."""

    for attribute in (_RENDER_TEMPLATE_SHA256_ATTRIBUTE, _RENDER_MANIFEST_SHA256_ATTRIBUTE):
        try:
            delattr(args, attribute)
        except (AttributeError, TypeError):
            pass


def _set_render_input_hashes(args: argparse.Namespace, template_sha256: str, manifest_sha256: str) -> None:
    """Attach byte-snapshot digests without requiring a custom args type."""

    try:
        setattr(args, _RENDER_TEMPLATE_SHA256_ATTRIBUTE, template_sha256)
        setattr(args, _RENDER_MANIFEST_SHA256_ATTRIBUTE, manifest_sha256)
    except (AttributeError, TypeError):
        # Direct programmatic callers may provide a frozen namespace-like
        # object.  ``run_render`` then hashes its already-consumed mappings,
        # still without reopening either input path.
        pass


def _render_input_hash(args: argparse.Namespace, attribute: str, data: Mapping[str, Any]) -> str:
    """Return the raw snapshot digest, with a no-reread compatibility fallback."""

    candidate = getattr(args, attribute, None)
    if isinstance(candidate, str) and re.fullmatch(r"[0-9a-f]{64}", candidate) is not None:
        return candidate
    return _consumed_json_sha256(data)


def _safe_render_validation_errors(errors: Iterable[str]) -> list[str]:
    """Return only fixed render-validation diagnostics safe for public JSON."""

    safe: list[str] = []
    for error in errors:
        compact = _compact_error_text(error)
        # This one gate is an intentional workflow instruction rather than a
        # schema diagnostic, and contains no source-controlled field/value.
        if compact == "$.support.review_required must be false before rendering":
            safe.append(compact)
        else:
            # Schema and semantic validators can quote invalid values,
            # filenames, and relative or absolute local paths.  Never return
            # any of that public input through the render command.
            safe.append("$: validation.invalid")
    return _deduplicate_errors(safe)


def _require_render_inputs(
    args: argparse.Namespace,
    runtime: Any,
) -> tuple[Path, dict[str, Any], dict[str, Any], list[str]]:
    """Load and fully validate a render request before creating output files."""

    _clear_render_input_hashes(args)
    project_root = runtime.require_project_root(args.project_root)
    # Reject an unsafe or existing optional summary before inspecting a project
    # further.  This is read-only and ensures a bad path can never be reached
    # after a costly render.
    if args.summary is not None:
        runtime.resolve_output_path(project_root, args.summary, must_not_exist=True)
    try:
        template, template_sha256 = _load_public_json_snapshot(args.template)
    except Exception as exc:
        return project_root, {}, {}, [_public_json_error(exc)]
    try:
        manifest, manifest_sha256 = _load_public_json_snapshot(args.manifest)
    except Exception as exc:
        return project_root, {}, {}, [_public_json_error(exc)]
    if not isinstance(template, dict) or not isinstance(manifest, dict):
        return project_root, {}, {}, ["$: validation.invalid"]
    _set_render_input_hashes(args, template_sha256, manifest_sha256)
    template_errors = validate_template_data(template)
    if not template_errors:
        unsupported_capability = _static_renderer_capability_unavailable(template)
        if unsupported_capability is not None:
            # This must precede all asset checks, renderer imports, FFmpeg
            # discovery, and output writes.  A static compositor may not
            # quietly substitute stills for requested motion or voice work.
            raise runtime.RRVError(
                runtime.ERR_CAPABILITY_UNAVAILABLE,
                "the deterministic S1 renderer cannot satisfy the declared rebuild requirements",
                {"capability": unsupported_capability},
            )
        if _template_requires_review(template):
            # A compiler may publish a technically valid Template IR while asking
            # a reviewer to resolve timing.  Do not hash assets, load a renderer,
            # or create any render output until that decision is explicit.
            return project_root, template, manifest, [
                "$.support.review_required must be false before rendering"
            ]
    asset_errors = validate_assets_data(
        template,
        manifest,
        args.manifest,
        check_files=True,
        project_root=project_root,
    )
    errors = _safe_render_validation_errors([*template_errors, *asset_errors])
    if not errors:
        runtime.validate_timeout(args.timeout)
    return project_root, template, manifest, errors


def _render_qa(
    renderer_summary: Mapping[str, Any],
    *,
    project_root: Path,
    runtime: Any,
    qa: Any,
    tools: Any,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Run mandatory technical QA for every encoded output profile."""

    master = renderer_summary.get("master")
    outputs = renderer_summary.get("outputs")
    if not isinstance(master, Mapping) or not isinstance(outputs, list):
        raise runtime.RRVError(
            runtime.ERR_TOOL_EXECUTION, "renderer returned an invalid run summary"
        )
    frame_count = master.get("frame_count")
    fps = master.get("fps")
    if isinstance(frame_count, bool) or not isinstance(frame_count, int):
        raise runtime.RRVError(runtime.ERR_TOOL_EXECUTION, "renderer summary has no integer frame count")
    if isinstance(fps, bool) or not isinstance(fps, (int, float)):
        raise runtime.RRVError(runtime.ERR_TOOL_EXECUTION, "renderer summary has no numeric frame rate")
    result_rows: list[dict[str, Any]] = []
    for output in outputs:
        if not isinstance(output, Mapping):
            raise runtime.RRVError(runtime.ERR_TOOL_EXECUTION, "renderer summary contains an invalid output")
        path = output.get("path")
        width = output.get("width")
        height = output.get("height")
        if not isinstance(path, str) or isinstance(width, bool) or isinstance(height, bool):
            raise runtime.RRVError(runtime.ERR_TOOL_EXECUTION, "renderer summary output is incomplete")
        delivery_path = runtime.resolve_output_path(project_root, path)
        verification = qa.verify_delivery(
            delivery_path,
            expected_width=width,
            expected_height=height,
            expected_fps=float(fps),
            expected_frames=frame_count,
            expect_audio=bool(output.get("audio_muxed")),
            tools=tools,
            timeout_seconds=timeout_seconds,
        )
        result_rows.append(
            {
                "output_id": output.get("id"),
                "path": path,
                "result": verification,
            }
        )
    return {
        "passed": all(bool(item["result"].get("passed")) for item in result_rows),
        "outputs": result_rows,
    }


def run_render(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Validate, render, hash, and technically verify a local S1 project."""

    runtime = _runtime_module()
    project_root, template, manifest, errors = _require_render_inputs(args, runtime)
    if errors:
        return {"status": "fail", "errors": errors}, 2
    tools = runtime.discover_tools(
        ffmpeg=args.ffmpeg,
        ffprobe=args.ffprobe,
        probe_versions=True,
    )
    if not tools.ffmpeg.path:
        raise runtime.RRVError(
            runtime.ERR_CAPABILITY_UNAVAILABLE,
            "deterministic rendering requires a local ffmpeg executable",
            {"capability": "timeline_render", "missing_tool": "ffmpeg"},
        )
    render = _render_module()
    qa = _qa_module()
    try:
        renderer_summary = render.render_project(
            template,
            manifest,
            project_root,
            frame_directory=args.frame_directory,
            debug_bounds=args.debug_bounds,
            ffmpeg_bin=tools.ffmpeg.path,
            timeout_seconds=args.timeout,
        )
    except runtime.RRVError:
        raise
    except render.RenderError as exc:
        raise runtime.RRVError(
            runtime.ERR_TOOL_EXECUTION,
            "deterministic render failed",
        ) from exc
    qa_summary = _render_qa(
        renderer_summary,
        project_root=project_root,
        runtime=runtime,
        qa=qa,
        tools=tools,
        timeout_seconds=args.timeout,
    )
    summary: dict[str, Any] = {
        "schema_version": "1.0",
        "status": "pass" if qa_summary["passed"] else "fail",
        "technical_status": "pass" if qa_summary["passed"] else "fail",
        "completion": {"status": _render_completion_status(template)},
        "renderer": renderer_summary,
        "qa": qa_summary,
        "hashes": _render_hashes(
            args.template,
            args.manifest,
            template,
            manifest,
            project_root,
            runtime,
            renderer_summary=renderer_summary,
            template_sha256=_render_input_hash(
                args, _RENDER_TEMPLATE_SHA256_ATTRIBUTE, template
            ),
            manifest_sha256=_render_input_hash(
                args, _RENDER_MANIFEST_SHA256_ATTRIBUTE, manifest
            ),
        ),
        "provenance": {"runtime": _render_runtime_provenance(tools)},
    }
    if args.summary is not None:
        # Put the future relative path inside the file too; opening is still
        # exclusive and remains after all render/QA results have been collected.
        summary["artifacts"] = {
            "summary": runtime.relative_output_path(project_root, project_root / args.summary)
        }
        _write_summary(runtime, project_root, args.summary, summary)
    return runtime.success_payload(summary), 0 if qa_summary["passed"] else 1


def run_qa(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Expose the local technical delivery verifier through the public CLI."""

    runtime = _runtime_module()
    qa = _qa_module()
    result = qa.verify_delivery(
        args.source,
        expected_width=args.expected_width,
        expected_height=args.expected_height,
        expected_fps=args.expected_fps,
        expected_frames=args.expected_frames,
        expect_audio=args.expect_audio,
        ffmpeg=args.ffmpeg,
        ffprobe=args.ffprobe,
        timeout_seconds=args.timeout,
    )
    return runtime.success_payload(result), 0 if result.get("passed") else 1


def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    runtime = _runtime_module()
    return runtime.success_payload(
        runtime.probe_media(
            args.source,
            ffmpeg=args.ffmpeg,
            ffprobe=args.ffprobe,
            timeout_seconds=args.timeout,
        )
    )


def run_survey(args: argparse.Namespace) -> dict[str, Any]:
    runtime = _runtime_module()
    analyze = _analyze_module()
    result = analyze.survey_reference(
        args.source,
        args.project_root,
        output_dir=args.output_dir,
        frame_numbers=args.frame_numbers,
        sample_count=args.sample_count,
        include_contact_sheet=args.include_contact_sheet,
        include_audio=args.include_audio,
        contact_sheet_columns=args.contact_sheet_columns,
        ffmpeg=args.ffmpeg,
        ffprobe=args.ffprobe,
        timeout_seconds=args.timeout,
    )
    return runtime.success_payload(result)


def _compile_relative_output_path(value: Any, field: str) -> str:
    """Accept only portable project-relative paths from the compiler core."""

    if not isinstance(value, str) or not value:
        raise TypeError(f"compiler returned an invalid {field}")
    normalized = value.replace("\\", "/")
    if (
        normalized.startswith("/")
        or normalized.startswith("//")
        or re.match(r"^[A-Za-z]:/", normalized)
        or any(part in {"", ".."} for part in normalized.split("/"))
    ):
        raise TypeError(f"compiler returned a non-relative {field}")
    return normalized


def _compact_compile_artifact(value: Any, field: str) -> dict[str, Any]:
    """Keep only safe scalar artifact facts; never relay internal payloads."""

    if not isinstance(value, Mapping):
        raise TypeError(f"compiler returned an invalid {field}")
    compact: dict[str, Any] = {
        "path": _compile_relative_output_path(value.get("path"), f"{field}.path")
    }
    for key in ("sha256", "media_type", "container", "metadata_stripped", "frame", "columns", "rows", "width", "height"):
        item = value.get(key)
        if isinstance(item, bool) or isinstance(item, int) or isinstance(item, str):
            compact[key] = item
    return compact


def _compact_compile_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Expose only the compiler's bounded public result envelope.

    The compiler keeps the full Template IR and timing-score detail on disk.
    This CLI response intentionally returns paths and compact review facts,
    never a template document or a per-frame score dump.
    """

    review_required = result.get("review_required")
    if not isinstance(review_required, bool):
        raise TypeError("compiler returned a result without boolean review_required")
    compact: dict[str, Any] = {"review_required": review_required}
    for key in ("schema_version", "template_id"):
        value = result.get(key)
        if isinstance(value, str):
            compact[key] = value
    if "output_dir" in result:
        compact["output_dir"] = _compile_relative_output_path(
            result["output_dir"], "output_dir"
        )
    switch_frames = result.get("switch_frames")
    if isinstance(switch_frames, list) and len(switch_frames) <= 64 and all(
        _is_int(frame) and frame >= 0 for frame in switch_frames
    ):
        compact["switch_frames"] = switch_frames
    elif switch_frames is not None:
        raise TypeError("compiler returned invalid switch_frames")

    raw_artifacts = result.get("artifacts")
    if isinstance(raw_artifacts, Mapping):
        artifacts: dict[str, Any] = {}
        for key in ("audio_original", "contact_sheet", "template_ir", "compile_report"):
            if key in raw_artifacts:
                artifacts[key] = _compact_compile_artifact(
                    raw_artifacts[key], f"artifacts.{key}"
                )
        center_frames = raw_artifacts.get("center_frames")
        if isinstance(center_frames, list):
            if len(center_frames) > 64:
                raise TypeError("compiler returned too many center-frame artifacts")
            # Paths and hashes remain in compile-report.json.  Returning every
            # evidence row through the CLI adds repeated agent-context tokens
            # without improving the review, which uses the contact sheet.
            artifacts["center_frame_count"] = len(center_frames)
        elif center_frames is not None:
            raise TypeError("compiler returned invalid center-frame artifacts")
        compact["artifacts"] = artifacts
    elif raw_artifacts is not None:
        raise TypeError("compiler returned invalid artifacts")
    return compact


_SAFE_COMPILE_ERROR_DETAIL_KEYS = {
    "backend",
    "capability",
    "cause_code",
    "missing_tool",
    "returncode",
    "timeout_seconds",
    "tool",
}


def _contains_absolute_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return (
        normalized.startswith("/")
        or normalized.startswith("//")
        or re.search(r"[A-Za-z]:/", normalized) is not None
    )


def _safe_compile_error_tool(value: str) -> str | None:
    """Return a basename-only tool label, never its configured path."""

    normalized = value.replace("\\", "/")
    label = normalized.rsplit("/", 1)[-1]
    if not label or _contains_absolute_path(label) or any(char.isspace() for char in label):
        return None
    return label


def _safe_compile_error_details(details: Any) -> dict[str, Any]:
    """Preserve only compact compiler failure facts safe for public JSON."""

    if not isinstance(details, Mapping):
        return {}
    safe: dict[str, Any] = {}
    for key in _SAFE_COMPILE_ERROR_DETAIL_KEYS:
        value = details.get(key)
        if key == "returncode":
            if _is_int(value):
                safe[key] = value
        elif key == "timeout_seconds":
            if _is_number(value):
                safe[key] = value
        elif isinstance(value, str):
            compact = _compact_error_text(value, limit=160)
            if key == "tool":
                label = _safe_compile_error_tool(compact)
                if label is not None:
                    safe[key] = label
            elif not _contains_absolute_path(compact) and "/" not in compact and "\\" not in compact:
                safe[key] = compact
    return safe


def _compile_error_payload(runtime: Any, error: BaseException) -> dict[str, Any]:
    """Build a path- and tool-output-safe compiler failure envelope."""

    details = _safe_compile_error_details(getattr(error, "details", None))
    code = getattr(error, "code", None)
    if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
        code = runtime.ERR_TOOL_EXECUTION
    return runtime.error_payload(
        runtime.RRVError(
            code,
            "reference compilation failed",
            details,
        )
    )


def _public_validation_errors(errors: Any) -> list[str]:
    """Collapse Template/Manifest diagnostics to a fixed safe public class."""

    if isinstance(errors, (str, bytes)) or not isinstance(errors, Iterable):
        return ["$: validation.unavailable"]
    return [] if not list(errors) else ["$: validation.invalid"]


def _validate_public_template_document(data: Any) -> list[str]:
    """Validate a strict-loaded Template IR without reflecting its values."""

    try:
        return _public_validation_errors(validate_template_data(data))
    except Exception:
        return ["$: validation.unavailable"]


def _validate_public_asset_documents(
    template: Any,
    manifest: Any,
    manifest_path: Path,
    *,
    check_files: bool,
    project_root: Path | None,
) -> list[str]:
    """Validate strict-loaded manifest inputs without relaying local paths."""

    try:
        return _public_validation_errors(
            validate_assets_data(
                template,
                manifest,
                manifest_path,
                check_files=check_files,
                project_root=project_root,
            )
        )
    except Exception:
        return ["$: validation.unavailable"]


_SAFE_RENDER_ERROR_CODES = frozenset(
    {
        "invalid_argument",
        "project_root_invalid",
        "output_path_outside_project_root",
        "output_already_exists",
        "source_not_found",
        "tool_not_found",
        "tool_execution_failed",
        "tool_timeout",
        "probe_failed",
        "capability_unavailable",
    }
)


def _render_error_payload(runtime: Any, error: BaseException) -> dict[str, Any]:
    """Return a render failure that cannot disclose input or tool paths."""

    code = getattr(error, "code", None)
    if not isinstance(code, str) or code not in _SAFE_RENDER_ERROR_CODES:
        code = runtime.ERR_TOOL_EXECUTION
    return runtime.error_payload(runtime.RRVError(code, "render request failed"))


def run_compile(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Structurally validate then compile one bounded local S1 reference.

    The core compiler performs media-dependent semantic validation before it
    creates its final artifact directory.  Keeping the schema gate here means
    invalid plans cannot load the compiler, probe media, or create outputs.
    """

    plan = load_json(args.plan)
    plan_errors = validate_compiler_plan_data(plan)
    if plan_errors:
        return {"status": "fail", "errors": plan_errors}, 2

    runtime = _runtime_module()
    try:
        tools = runtime.discover_tools(
            ffmpeg=args.ffmpeg,
            ffprobe=args.ffprobe,
            probe_versions=True,
        )
        result = _compile_module().compile_reference(
            args.source,
            plan,
            args.project_root,
            tools,
            output_dir=args.output_dir,
            timeout_seconds=args.timeout,
            template_validator=validate_template_data,
        )
        if not isinstance(result, Mapping):
            raise TypeError("compiler returned an invalid result")
        compact_result = _compact_compile_result(result)
        return runtime.success_payload(compact_result), 1 if compact_result["review_required"] else 0
    except runtime.RRVError as exc:
        return _compile_error_payload(runtime, exc), 2


_PUBLIC_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PROPOSAL_SUMMARY_LIMITS = {
    "slot_count": (1, 64),
    "carousel_boundary_count": (0, 16),
    "switch_frame_count": (0, 64),
}


def _compact_workflow_relative_path(value: Any, field: str) -> str:
    """Normalize one core-returned path without relaying a private location."""

    path = _compile_relative_output_path(value, field)
    if (
        re.match(r"^[A-Za-z]:", path)
        or path == "."
        or any(part == "." for part in path.split("/"))
    ):
        raise TypeError(f"workflow returned a non-relative {field}")
    return path


def _compact_workflow_artifact(value: Any, field: str) -> dict[str, str]:
    """Expose an artifact only as its portable path and content digest."""

    if not isinstance(value, Mapping):
        raise TypeError(f"workflow returned an invalid {field}")
    digest = value.get("sha256")
    if not isinstance(digest, str) or not _PUBLIC_SHA256_PATTERN.fullmatch(digest):
        raise TypeError(f"workflow returned an invalid {field}.sha256")
    return {
        "path": _compact_workflow_relative_path(value.get("path"), f"{field}.path"),
        "sha256": digest,
    }


def _compact_candidate_summary(value: Any) -> dict[str, int]:
    """Keep only bounded count facts, never confidence or score traces."""

    if not isinstance(value, Mapping):
        raise TypeError("workflow returned an invalid candidate_summary")
    summary: dict[str, int] = {}
    for key, (minimum, maximum) in _PROPOSAL_SUMMARY_LIMITS.items():
        item = value.get(key)
        if not _is_int(item) or not minimum <= item <= maximum:
            raise TypeError(f"workflow returned an invalid candidate_summary.{key}")
        summary[key] = item
    return summary


def _compact_proposal_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return a small, path-safe proposal handoff envelope.

    A proposal's full candidate plan, confidence samples, source label, and
    subprocess output remain in the local packet.  This command response is
    deliberately just enough to route a human review.
    """

    review_required = result.get("review_required")
    template_id = result.get("template_id")
    if review_required is not True:
        raise TypeError("workflow returned a proposal without review_required=true")
    if not isinstance(template_id, str) or not ID_PATTERN.fullmatch(template_id):
        raise TypeError("workflow returned an invalid template_id")
    compact: dict[str, Any] = {
        "review_required": True,
        "template_id": template_id,
        "output_dir": _compact_workflow_relative_path(result.get("output_dir"), "output_dir"),
        "candidate_summary": _compact_candidate_summary(result.get("candidate_summary")),
    }
    schema_version = result.get("schema_version")
    if isinstance(schema_version, str):
        compact["schema_version"] = schema_version
    raw_artifacts = result.get("artifacts")
    if not isinstance(raw_artifacts, Mapping):
        raise TypeError("workflow returned invalid artifacts")
    artifacts: dict[str, Any] = {}
    for name in ("proposal", "review_template"):
        artifacts[name] = _compact_workflow_artifact(
            raw_artifacts.get(name), f"artifacts.{name}"
        )
    raw_evidence = raw_artifacts.get("evidence")
    if not isinstance(raw_evidence, Mapping):
        raw_evidence = raw_artifacts
    for name in ("overview_contact_sheet", "geometry_preview", "timing_profile"):
        item = raw_evidence.get(name)
        if item is not None:
            artifacts[name] = _compact_workflow_artifact(
                item, f"artifacts.evidence.{name}"
            )
    compact["artifacts"] = artifacts
    return compact


def _compact_freeze_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return only freeze output paths and digests, never review internals."""

    template_id = result.get("template_id")
    if not isinstance(template_id, str) or not ID_PATTERN.fullmatch(template_id):
        raise TypeError("workflow returned an invalid template_id")
    raw_artifacts = result.get("artifacts")
    if not isinstance(raw_artifacts, Mapping):
        raise TypeError("workflow returned invalid artifacts")
    compact: dict[str, Any] = {
        "template_id": template_id,
        "output_dir": _compact_workflow_relative_path(result.get("output_dir"), "output_dir"),
        "artifacts": {
            name: _compact_workflow_artifact(
                raw_artifacts.get(name), f"artifacts.{name}"
            )
            for name in ("compiler_plan", "freeze_report")
        },
    }
    schema_version = result.get("schema_version")
    if isinstance(schema_version, str):
        compact["schema_version"] = schema_version
    return compact


def _workflow_error_payload(operation: str, error: BaseException) -> dict[str, Any]:
    """Sanitize proposal/freeze failures before emitting public JSON."""

    message = f"{operation} failed"
    try:
        runtime = _runtime_module()
    except Exception:
        return {
            "schema_version": "1.0",
            "status": "error",
            "error": {"code": "operation_failed", "message": message},
        }
    if isinstance(error, runtime.RRVError):
        code = getattr(error, "code", None)
        if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
            code = runtime.ERR_TOOL_EXECUTION
        details = {
            key: value
            for key, value in _safe_compile_error_details(
                getattr(error, "details", None)
            ).items()
            if not (isinstance(value, str) and re.match(r"^[A-Za-z]:", value))
        }
        return runtime.error_payload(runtime.RRVError(code, message, details))
    return runtime.error_payload(runtime.RRVError(runtime.ERR_TOOL_EXECUTION, message))


def run_propose(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Create a review-required local proposal through the lazy core module."""

    output_profiles = (
        tuple(args.output_profiles)
        if args.output_profiles
        else ("720x1280", "1080x1920")
    )
    try:
        result = _propose_module().propose_reference(
            args.reference,
            project_root=args.project_root,
            template_id=args.template_id,
            output_dir=args.output_dir,
            slot_count_hint=args.slot_count_hint,
            audio_mode=args.audio_mode,
            reference_rights_confirmed=args.reference_rights_confirmed,
            audio_rights_confirmed=args.audio_rights_confirmed,
            output_profiles=output_profiles,
            analysis_width=args.analysis_width,
            max_evidence_frames=args.max_evidence_frames,
            ffmpeg=args.ffmpeg,
            ffprobe=args.ffprobe,
            timeout_seconds=args.timeout,
        )
        if not isinstance(result, Mapping):
            raise TypeError("workflow returned an invalid result")
        runtime = _runtime_module()
        return runtime.success_payload(_compact_proposal_result(result)), 0
    except Exception as exc:
        return _workflow_error_payload("compiler plan proposal", exc), 2


def run_freeze_plan(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Delegate every untrusted packet read and validation to the safe core.

    ``rrv_propose.freeze_plan`` binds a descriptor-safe, root-aware snapshot
    to validation and review hashing. The CLI must not resolve, read, or
    validate packets first: that would add a second, race-prone path traversal
    before the core's containment boundary.
    """
    try:
        result = _propose_module().freeze_plan(
            args.proposal,
            args.review,
            project_root=args.project_root,
            output_dir=args.output_dir,
        )
        if not isinstance(result, Mapping):
            raise TypeError("workflow returned an invalid result")
        runtime = _runtime_module()
        return runtime.success_payload(_compact_freeze_result(result)), 0
    except Exception as exc:
        return _workflow_error_payload("compiler plan freeze", exc), 2


def run_freeze(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Compatibility-friendly public name for the ``freeze-plan`` command."""

    return run_freeze_plan(args)


_ASSET_PROPOSAL_COUNT_KEYS = (
    "inventory_entries",
    "template_slots",
    "suggested_slots",
)
_ASSET_FREEZE_COUNT_KEYS = (
    "inventory_entries",
    "mapped_slots",
    "omitted_slots",
    "copied_assets",
)


def _compact_asset_counts(value: Any, count_keys: Sequence[str]) -> dict[str, int]:
    """Expose only bounded, known asset-workflow count fields."""

    if not isinstance(value, Mapping):
        raise TypeError("asset workflow returned invalid counts")
    compact: dict[str, int] = {}
    for key in count_keys:
        item = value.get(key)
        if not _is_int(item) or not 0 <= item <= 1_000_000:
            raise TypeError(f"asset workflow returned invalid counts.{key}")
        compact[key] = item
    return compact


def _compact_asset_workflow_result(
    result: Mapping[str, Any],
    *,
    review_required: bool,
    count_keys: Sequence[str],
    artifact_names: Sequence[str],
) -> dict[str, Any]:
    """Return only the v0.5 handoff facts safe for public CLI JSON."""

    schema_version = result.get("schema_version")
    if not isinstance(schema_version, str) or not SCHEMA_VERSION_PATTERN.fullmatch(schema_version):
        raise TypeError("asset workflow returned an invalid schema_version")
    returned_review_required = result.get("review_required")
    if returned_review_required is not review_required:
        raise TypeError("asset workflow returned an invalid review_required state")
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise TypeError("asset workflow returned invalid artifacts")
    return {
        "schema_version": schema_version,
        "review_required": review_required,
        "counts": _compact_asset_counts(result.get("counts"), count_keys),
        "artifacts": {
            name: _compact_workflow_artifact(artifacts.get(name), f"artifacts.{name}")
            for name in artifact_names
        },
    }


def run_propose_assets(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Delegate a raw Template path to the guarded v0.5 asset-pack core."""

    # argparse normally enforces this flag before dispatch.  Retaining the
    # check here makes programmatic callers fail before the asset core is even
    # imported, which preserves the no-analysis-without-rights boundary.
    if getattr(args, "asset_pack_rights_confirmed", False) is not True:
        return _error_payload(
            CliArgumentError("--asset-pack-rights-confirmed is required"),
            invalid_argument=True,
        ), 2
    try:
        result = _assets_module().propose_asset_pack(
            args.template,
            project_root=args.project_root,
            asset_pack=args.asset_pack,
            asset_pack_rights_confirmed=args.asset_pack_rights_confirmed,
            output_dir=args.output_dir,
            ffprobe=args.ffprobe,
            timeout_seconds=args.timeout,
        )
        if not isinstance(result, Mapping):
            raise TypeError("asset workflow returned an invalid result")
        runtime = _runtime_module()
        return runtime.success_payload(
            _compact_asset_workflow_result(
                result,
                review_required=True,
                count_keys=_ASSET_PROPOSAL_COUNT_KEYS,
                artifact_names=("proposal", "review_template", "contact_sheet"),
            )
        ), 0
    except Exception as exc:
        return _workflow_error_payload("asset pack proposal", exc), 2


def run_freeze_assets(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Delegate raw packets to the guarded core without a CLI TOCTOU read.

    The core binds proposal/review snapshots, validates their hashes, and
    rescans media before it creates its staging directory.  Do not resolve,
    open, or validate either packet in this wrapper.
    """

    try:
        result = _assets_module().freeze_assets(
            args.proposal,
            args.review,
            project_root=args.project_root,
            output_dir=args.output_dir,
            ffprobe=args.ffprobe,
            timeout_seconds=args.timeout,
        )
        if not isinstance(result, Mapping):
            raise TypeError("asset workflow returned an invalid result")
        runtime = _runtime_module()
        return runtime.success_payload(
            _compact_asset_workflow_result(
                result,
                review_required=False,
                count_keys=_ASSET_FREEZE_COUNT_KEYS,
                artifact_names=("assets_manifest", "freeze_report"),
            )
        ), 0
    except Exception as exc:
        return _workflow_error_payload("asset review freeze", exc), 2


_GENERATION_PREPARE_COUNT_KEYS = (
    "reference_inventory_entries",
    "tasks",
    "generation_tasks",
    "passthrough_tasks",
    "omitted_tasks",
)
_GENERATION_RESULTS_PROPOSAL_COUNT_KEYS = (
    "result_inventory_entries",
    "tasks",
    "generation_tasks",
    "passthrough_tasks",
    "omitted_tasks",
)
_GENERATION_ASSEMBLY_COUNT_KEYS = (
    "output_assets",
    "generation_results",
    "image_passthrough",
    "audio_passthrough",
    "omitted_tasks",
)
_GENERATION_SAFE_ERROR_CODES = frozenset(
    {
        "invalid_argument",
        "project_root_invalid",
        "output_path_outside_project_root",
        "output_already_exists",
        "source_not_found",
        "tool_not_found",
        "tool_execution_failed",
        "tool_timeout",
        "probe_failed",
        "capability_unavailable",
    }
)


def _compact_generation_workflow_result(
    result: Mapping[str, Any],
    *,
    review_required: bool,
    count_keys: Sequence[str],
    artifact_names: Sequence[str],
) -> dict[str, Any]:
    """Keep v0.6 handoffs free of prompts, providers, and local source names."""

    schema_version = result.get("schema_version")
    if not isinstance(schema_version, str) or not SCHEMA_VERSION_PATTERN.fullmatch(schema_version):
        raise TypeError("generation workflow returned an invalid schema_version")
    if result.get("review_required") is not review_required:
        raise TypeError("generation workflow returned an invalid review_required state")
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise TypeError("generation workflow returned invalid artifacts")
    return {
        "schema_version": schema_version,
        "review_required": review_required,
        "counts": _compact_asset_counts(result.get("counts"), count_keys),
        "artifacts": {
            name: _compact_workflow_artifact(artifacts.get(name), f"artifacts.{name}")
            for name in artifact_names
        },
    }


def _compact_generation_assembly_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Expose assembled files only as root-relative path/hash evidence."""

    schema_version = result.get("schema_version")
    if not isinstance(schema_version, str) or not SCHEMA_VERSION_PATTERN.fullmatch(schema_version):
        raise TypeError("generation assembly returned an invalid schema_version")
    if result.get("review_required") is not False:
        raise TypeError("generation assembly returned an invalid review_required state")
    counts = _compact_asset_counts(result.get("counts"), _GENERATION_ASSEMBLY_COUNT_KEYS)
    raw_assets = result.get("assets")
    if not isinstance(raw_assets, list) or len(raw_assets) != counts["output_assets"]:
        raise TypeError("generation assembly returned invalid assets")
    if len(raw_assets) > 1_000_000:
        raise TypeError("generation assembly returned too many assets")
    return {
        "schema_version": schema_version,
        "review_required": False,
        "counts": counts,
        "artifacts": {
            "assets": [
                _compact_workflow_artifact(item, f"assets[{index}]")
                for index, item in enumerate(raw_assets)
            ]
        },
    }


def _generation_workflow_error_payload(
    operation: str, error: BaseException
) -> dict[str, Any]:
    """Return a fixed v0.6 failure without prompts, provider, or tool text."""

    message = f"{operation} failed"
    try:
        runtime = _runtime_module()
    except Exception:
        return {
            "schema_version": "1.0",
            "status": "error",
            "error": {"code": "operation_failed", "message": message},
        }
    code = getattr(error, "code", None)
    if not isinstance(code, str) or code not in _GENERATION_SAFE_ERROR_CODES:
        code = runtime.ERR_TOOL_EXECUTION
    return runtime.error_payload(runtime.RRVError(code, message))


def run_prepare_generation(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Create a review-required local generation plan through the guarded core."""

    if getattr(args, "generation_rights_confirmed", False) is not True:
        return _error_payload(
            CliArgumentError("--generation-rights-confirmed is required"),
            invalid_argument=True,
        ), 2
    try:
        result = _generation_module().prepare_generation(
            args.template,
            args.request,
            project_root=args.project_root,
            reference_pack=args.reference_pack,
            generation_rights_confirmed=args.generation_rights_confirmed,
            output_dir=args.output_dir,
            ffprobe=args.ffprobe,
            timeout_seconds=args.timeout,
        )
        if not isinstance(result, Mapping):
            raise TypeError("generation workflow returned an invalid result")
        return _runtime_module().success_payload(
            _compact_generation_workflow_result(
                result,
                review_required=True,
                count_keys=_GENERATION_PREPARE_COUNT_KEYS,
                artifact_names=(
                    "generation_plan",
                    "review_template",
                    "input_contact_sheet",
                ),
            )
        ), 0
    except Exception as exc:
        return _generation_workflow_error_payload("generation planning", exc), 2


def run_propose_generation_results(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Review local generated results without pre-reading frozen plan packets."""

    if getattr(args, "generation_results_rights_confirmed", False) is not True:
        return _error_payload(
            CliArgumentError("--generation-results-rights-confirmed is required"),
            invalid_argument=True,
        ), 2
    try:
        result = _generation_module().propose_generation_results(
            args.plan,
            args.plan_review,
            project_root=args.project_root,
            result_pack=args.result_pack,
            generation_results_rights_confirmed=args.generation_results_rights_confirmed,
            output_dir=args.output_dir,
            ffprobe=args.ffprobe,
            timeout_seconds=args.timeout,
        )
        if not isinstance(result, Mapping):
            raise TypeError("generation results workflow returned an invalid result")
        return _runtime_module().success_payload(
            _compact_generation_workflow_result(
                result,
                review_required=True,
                count_keys=_GENERATION_RESULTS_PROPOSAL_COUNT_KEYS,
                artifact_names=(
                    "proposal",
                    "review_template",
                    "comparison_contact_sheet",
                ),
            )
        ), 0
    except Exception as exc:
        return _generation_workflow_error_payload("generation results review", exc), 2


def run_assemble_generation_pack(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Delegate all reviewed-packet reads to the assembly core without TOCTOU."""

    try:
        result = _generation_module().assemble_generation_pack(
            args.plan,
            args.plan_review,
            args.results_proposal,
            args.results_review,
            project_root=args.project_root,
            output_dir=args.output_dir,
            ffprobe=args.ffprobe,
            timeout_seconds=args.timeout,
        )
        if not isinstance(result, Mapping):
            raise TypeError("generation assembly returned an invalid result")
        return _runtime_module().success_payload(
            _compact_generation_assembly_result(result)
        ), 0
    except Exception as exc:
        return _generation_workflow_error_payload("generation pack assembly", exc), 2


_FAITHFUL_SAFE_ERROR_CODES = frozenset(
    {
        "invalid_argument",
        "project_root_invalid",
        "output_path_outside_project_root",
        "output_already_exists",
        "source_not_found",
        "tool_not_found",
        "tool_execution_failed",
        "tool_timeout",
        "probe_failed",
        "capability_unavailable",
    }
)


def _validate_faithful_plan_file(path: Path) -> list[str]:
    """Validate a v0.9 plan without exposing JSON or schema diagnostics."""

    try:
        plan, _plan_sha256 = _load_public_json_snapshot(path)
    except Exception as exc:
        return [_public_json_error(exc)]
    try:
        validator = getattr(_faithful_module(), "validate_faithful_plan", None)
        if not callable(validator):
            return ["$: validation.unavailable"]
        validator(plan)
    except Exception:
        # Schema and semantic errors can contain plan text, source paths, or
        # tool-facing values.  Public validation intentionally has one class.
        return ["$: validation.invalid"]
    return []


def _faithful_workflow_error_payload(
    operation: str, error: BaseException
) -> dict[str, Any]:
    """Return a fixed v0.9 error envelope with no plan or tool text."""

    message = f"{operation} failed"
    try:
        runtime = _runtime_module()
    except Exception:
        return {
            "schema_version": "1.0",
            "status": "error",
            "error": {
                "code": "invalid_argument" if isinstance(error, CliArgumentError) else "operation_failed",
                "message": message,
            },
        }
    if isinstance(error, CliArgumentError):
        code = runtime.ERR_INVALID_ARGUMENT
    else:
        code = getattr(error, "code", None)
        if not isinstance(code, str) or code not in _FAITHFUL_SAFE_ERROR_CODES:
            code = runtime.ERR_TOOL_EXECUTION
    return runtime.error_payload(runtime.RRVError(code, message))


def _compact_faithful_payload_hash(value: Any, field: str) -> dict[str, Any]:
    """Expose a bounded packet digest without subprocess or source details."""

    if not isinstance(value, Mapping):
        raise TypeError(f"faithful rebuild returned an invalid {field}")
    digest = value.get("sha256")
    packet_count = value.get("packet_count")
    if (
        not isinstance(digest, str)
        or not _PUBLIC_SHA256_PATTERN.fullmatch(digest)
        or not _is_int(packet_count)
        or not 1 <= packet_count <= 10_000_000
    ):
        raise TypeError(f"faithful rebuild returned an invalid {field}")
    return {"sha256": digest, "packet_count": packet_count}


def _compact_faithful_media_facts(value: Any) -> dict[str, Any]:
    """Keep numerical media facts only; codec/container strings are tool text."""

    if not isinstance(value, Mapping):
        raise TypeError("faithful rebuild returned invalid media_facts")
    width = value.get("width")
    height = value.get("height")
    fps = value.get("fps")
    frame_count = value.get("frame_count")
    duration_seconds = value.get("duration_seconds")
    has_audio = value.get("has_audio")
    audio_stream_count = value.get("audio_stream_count")
    if (
        not _is_int(width)
        or not 1 <= width <= 1920
        or not _is_int(height)
        or not 1 <= height <= 1920
        or not _is_number(fps)
        or not 0 < float(fps) <= 120
        or not _is_int(frame_count)
        or not 1 <= frame_count <= 7200
        or not _is_number(duration_seconds)
        or not 0 < float(duration_seconds) <= 60
        or not isinstance(has_audio, bool)
        or not _is_int(audio_stream_count)
        or not 0 <= audio_stream_count <= 64
    ):
        raise TypeError("faithful rebuild returned invalid media_facts")
    return {
        "width": width,
        "height": height,
        "fps": fps,
        "frame_count": frame_count,
        "duration_seconds": duration_seconds,
        "has_audio": has_audio,
        "audio_stream_count": audio_stream_count,
    }


def _compact_faithful_provenance(value: Any, plan_sha256: str) -> dict[str, Any]:
    """Keep auditable hashes while dropping tool paths and version output."""

    if not isinstance(value, Mapping):
        raise TypeError("faithful rebuild returned invalid provenance")
    workflow_version = value.get("workflow_version")
    core_sha256 = value.get("core_sha256")
    invocation_policy_sha256 = value.get("invocation_policy_sha256")
    plan = value.get("plan")
    if (
        not isinstance(workflow_version, str)
        or len(workflow_version) > 64
        or not SCHEMA_VERSION_PATTERN.fullmatch(workflow_version)
        or not isinstance(core_sha256, str)
        or not _PUBLIC_SHA256_PATTERN.fullmatch(core_sha256)
        or not isinstance(invocation_policy_sha256, str)
        or not _PUBLIC_SHA256_PATTERN.fullmatch(invocation_policy_sha256)
        or not isinstance(plan, Mapping)
    ):
        raise TypeError("faithful rebuild returned invalid provenance")
    input_sha256 = plan.get("input_sha256")
    canonical_sha256 = plan.get("canonical_sha256")
    if (
        not isinstance(input_sha256, str)
        or not _PUBLIC_SHA256_PATTERN.fullmatch(input_sha256)
        or not isinstance(canonical_sha256, str)
        or canonical_sha256 != plan_sha256
    ):
        raise TypeError("faithful rebuild returned invalid provenance.plan")
    return {
        "workflow_version": workflow_version,
        "core_sha256": core_sha256,
        "plan": {
            "input_sha256": input_sha256,
            "canonical_sha256": canonical_sha256,
        },
        "invocation_policy_sha256": invocation_policy_sha256,
    }


def _compact_faithful_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return the safe handoff facts from a successful faithful rebuild."""

    if result.get("schema_version") != "0.9.0":
        raise TypeError("faithful rebuild returned an invalid schema_version")
    if result.get("completion") != "faithful_source_preservation":
        raise TypeError("faithful rebuild returned an invalid completion")
    replica_sha256 = result.get("replica_sha256")
    plan_sha256 = result.get("plan_sha256")
    if (
        not isinstance(replica_sha256, str)
        or not _PUBLIC_SHA256_PATTERN.fullmatch(replica_sha256)
        or not isinstance(plan_sha256, str)
        or not _PUBLIC_SHA256_PATTERN.fullmatch(plan_sha256)
    ):
        raise TypeError("faithful rebuild returned invalid digests")
    text_inventory_count = result.get("text_inventory_count")
    metadata = result.get("metadata")
    if (
        not _is_int(text_inventory_count)
        or not 1 <= text_inventory_count <= 256
        or not isinstance(metadata, Mapping)
        or metadata.get("strip_all") is not True
        or metadata.get("verified") is not True
    ):
        raise TypeError("faithful rebuild returned invalid completion facts")
    raw_payload_hashes = result.get("payload_hashes")
    if not isinstance(raw_payload_hashes, Mapping):
        raise TypeError("faithful rebuild returned invalid payload_hashes")
    raw_video = raw_payload_hashes.get("video")
    raw_audio = raw_payload_hashes.get("audio")
    if not isinstance(raw_video, Mapping) or not isinstance(raw_audio, Mapping):
        raise TypeError("faithful rebuild returned invalid payload_hashes")
    audio_mode = raw_audio.get("mode")
    if audio_mode not in {"preserve-bitstream", "mute"}:
        raise TypeError("faithful rebuild returned invalid payload_hashes.audio.mode")

    def compact_optional_payload(value: Any, field: str) -> dict[str, Any] | None:
        if value is None:
            return None
        return _compact_faithful_payload_hash(value, field)

    return {
        "schema_version": "0.9.0",
        "completion": "faithful_source_preservation",
        "output_dir": _compact_workflow_relative_path(result.get("output_dir"), "output_dir"),
        "replica_path": _compact_workflow_relative_path(result.get("replica_path"), "replica_path"),
        "rebuild_summary_path": _compact_workflow_relative_path(
            result.get("rebuild_summary_path"), "rebuild_summary_path"
        ),
        "replica_sha256": replica_sha256,
        "plan_sha256": plan_sha256,
        "media_facts": _compact_faithful_media_facts(result.get("media_facts")),
        "payload_hashes": {
            "video": {
                "source": _compact_faithful_payload_hash(
                    raw_video.get("source"), "payload_hashes.video.source"
                ),
                "replica": _compact_faithful_payload_hash(
                    raw_video.get("replica"), "payload_hashes.video.replica"
                ),
            },
            "audio": {
                "mode": audio_mode,
                "source": compact_optional_payload(
                    raw_audio.get("source"), "payload_hashes.audio.source"
                ),
                "replica": compact_optional_payload(
                    raw_audio.get("replica"), "payload_hashes.audio.replica"
                ),
            },
        },
        "text_inventory_count": text_inventory_count,
        "metadata": {"strip_all": True, "verified": True},
        "provenance": _compact_faithful_provenance(
            result.get("provenance"), plan_sha256
        ),
    }


def run_faithful_rebuild(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Execute an explicitly approved v0.9 faithful rebuild.

    The only pre-core input access is a strict read of the plan needed to
    check its top-level rights gate.  In particular, a rejected plan never
    imports the faithful core, resolves the project root, discovers media
    tools, accesses its source, or creates an output directory.
    """

    try:
        plan, plan_input_bytes, plan_input_sha256 = _load_public_json_snapshot_bytes(args.plan)
    except Exception:
        return _faithful_workflow_error_payload(
            "faithful rebuild", CliArgumentError("invalid faithful plan")
        ), 2
    if not isinstance(plan, Mapping) or plan.get("rights_confirmed") is not True:
        return _faithful_workflow_error_payload(
            "faithful rebuild", CliArgumentError("rights confirmation is required")
        ), 2
    try:
        result = _faithful_module().execute_faithful_rebuild(
            plan,
            args.project_root,
            args.output_dir,
            ffmpeg=args.ffmpeg,
            ffprobe=args.ffprobe,
            timeout_seconds=args.timeout_seconds,
            plan_input_bytes=plan_input_bytes,
        )
        if not isinstance(result, Mapping):
            raise TypeError("faithful rebuild returned an invalid result")
        compact_result = _compact_faithful_result(result)
        if compact_result["provenance"]["plan"]["input_sha256"] != plan_input_sha256:
            raise TypeError("faithful rebuild returned mismatched plan input provenance")
        return _runtime_module().success_payload(compact_result), 0
    except Exception as exc:
        return _faithful_workflow_error_payload("faithful rebuild", exc), 2


def _compact_faithful_evidence_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return only the non-semantic, bounded faithful-evidence handoff facts."""

    if result.get("schema_version") != FAITHFUL_EVIDENCE_SCHEMA_VERSION:
        raise TypeError("faithful evidence returned an invalid schema_version")
    if result.get("operation") != "faithful-review-evidence":
        raise TypeError("faithful evidence returned an invalid operation")
    if result.get("claim") != "human_review_support_only" or result.get("ocr_used") is not False:
        raise TypeError("faithful evidence returned invalid claim facts")
    plan = result.get("plan")
    source = result.get("source")
    artifacts = result.get("artifacts")
    sampling = result.get("sampling")
    if not isinstance(plan, Mapping) or not isinstance(source, Mapping):
        raise TypeError("faithful evidence returned invalid provenance")
    plan_sha256 = plan.get("canonical_sha256")
    source_sha256 = source.get("sha256")
    if (
        not isinstance(plan_sha256, str)
        or not _PUBLIC_SHA256_PATTERN.fullmatch(plan_sha256)
        or not isinstance(source_sha256, str)
        or not _PUBLIC_SHA256_PATTERN.fullmatch(source_sha256)
        or not isinstance(artifacts, Mapping)
        or not isinstance(sampling, Mapping)
    ):
        raise TypeError("faithful evidence returned invalid provenance")
    inventory_count = result.get("inventory_count")
    covered_frame_count = result.get("inventory_covered_frame_count")
    max_panels = sampling.get("max_panels")
    selected_frames = sampling.get("selected_frames")
    inventory_without_panel = sampling.get("inventory_without_midpoint_panel")
    truncated = sampling.get("truncated")
    if (
        not _is_int(inventory_count)
        or not 0 <= inventory_count <= 256
        or not _is_int(covered_frame_count)
        or not 0 <= covered_frame_count <= 7200
        or not _is_int(max_panels)
        or not 1 <= max_panels <= 24
        or not isinstance(selected_frames, list)
        or not 1 <= len(selected_frames) <= max_panels
        or not all(_is_int(frame) and 0 <= frame < 7200 for frame in selected_frames)
        or not _is_int(inventory_without_panel)
        or not 0 <= inventory_without_panel <= inventory_count
        or not isinstance(truncated, bool)
    ):
        raise TypeError("faithful evidence returned invalid sampling facts")
    raw_contact_sheet = artifacts.get("contact_sheet")
    contact_sheet = _compact_workflow_artifact(
        raw_contact_sheet, "artifacts.contact_sheet"
    )
    report = artifacts.get("report")
    if not isinstance(report, Mapping):
        raise TypeError("faithful evidence returned invalid artifacts.report")
    report_path = _compact_workflow_relative_path(
        report.get("path"), "artifacts.report.path"
    )
    panel_count = raw_contact_sheet.get("panel_count") if isinstance(raw_contact_sheet, Mapping) else None
    if not _is_int(panel_count) or panel_count != len(selected_frames):
        raise TypeError("faithful evidence returned invalid contact-sheet count")
    contact_sheet["panel_count"] = panel_count
    return {
        "schema_version": FAITHFUL_EVIDENCE_SCHEMA_VERSION,
        "operation": "faithful-review-evidence",
        "claim": "human_review_support_only",
        "ocr_used": False,
        "output_dir": _compact_workflow_relative_path(result.get("output_dir"), "output_dir"),
        "provenance": {
            "plan": {"canonical_sha256": plan_sha256},
            "source_sha256": source_sha256,
        },
        "media_facts": _compact_faithful_media_facts(result.get("media_facts")),
        "inventory_count": inventory_count,
        "inventory_covered_frame_count": covered_frame_count,
        "sampling": {
            "max_panels": max_panels,
            "panel_count": panel_count,
            "inventory_without_midpoint_panel": inventory_without_panel,
            "truncated": truncated,
        },
        "artifacts": {
            "contact_sheet": contact_sheet,
            "report": {"path": report_path},
        },
    }


def _compact_nle_qa(value: Any) -> dict[str, Any]:
    """Expose only mechanical NLE QA facts, never FFmpeg messages or traces."""

    if not isinstance(value, Mapping):
        raise TypeError("Jianying delivery returned invalid QA")
    full_decode = value.get("full_decode")
    profile_checks = value.get("profile_checks")
    if not isinstance(full_decode, Mapping) or not isinstance(profile_checks, Mapping):
        raise TypeError("Jianying delivery returned invalid QA")
    decoded_video_frames = full_decode.get("decoded_video_frames")
    decoded_audio = full_decode.get("decoded_audio")
    audio_decode_applicable = full_decode.get("audio_decode_applicable")
    if (
        full_decode.get("passed") is not True
        or full_decode.get("completed") is not True
        or not _is_int(decoded_video_frames)
        or not 1 <= decoded_video_frames <= 7200
        or not isinstance(decoded_audio, bool)
        or not isinstance(audio_decode_applicable, bool)
        or full_decode.get("returncode") != 0
    ):
        raise TypeError("Jianying delivery returned invalid full-decode QA")
    audio = profile_checks.get("audio")
    if (
        profile_checks.get("mp4") is not True
        or profile_checks.get("h264_high_8_bit_yuv420p") is not True
        or profile_checks.get("cfr") is not True
        or profile_checks.get("metadata_cleared") is not True
        or profile_checks.get("chapters_cleared") is not True
        or profile_checks.get("rotation_cleared") is not True
        or profile_checks.get("faststart") is not True
        or not isinstance(audio, Mapping)
        or audio.get("passed") is not True
        or audio.get("mode") not in {"aac-lc-48khz-stereo", "no-audio-preserved"}
    ):
        raise TypeError("Jianying delivery returned invalid profile QA")
    return {
        "full_decode": {
            "passed": True,
            "completed": True,
            "decoded_video_frames": decoded_video_frames,
            "decoded_audio": decoded_audio,
            "audio_decode_applicable": audio_decode_applicable,
            "returncode": 0,
        },
        "profile_checks": {
            "mp4": True,
            "h264_high_8_bit_yuv420p": True,
            "cfr": True,
            "audio": {"passed": True, "mode": audio["mode"]},
            "metadata_cleared": True,
            "chapters_cleared": True,
            "rotation_cleared": True,
            "faststart": True,
        },
    }


def _compact_nle_result(result: Mapping[str, Any], *, exported: bool) -> dict[str, Any]:
    """Return a fixed-profile NLE receipt without source or probe internals."""

    if (
        result.get("schema_version") != NLE_SCHEMA_VERSION
        or result.get("completion") != "nle_compatible_derivative"
        or result.get("bitstream_faithful") is not False
        or result.get("profile") != JIANYING_PROFILE
    ):
        raise TypeError("Jianying delivery returned invalid fixed-profile facts")
    output = _compact_workflow_artifact(result.get("output"), "output")
    media_facts = result.get("media_facts")
    if not isinstance(media_facts, Mapping):
        raise TypeError("Jianying delivery returned invalid media_facts")
    compact: dict[str, Any] = {
        "schema_version": NLE_SCHEMA_VERSION,
        "completion": "nle_compatible_derivative",
        "bitstream_faithful": False,
        "profile": JIANYING_PROFILE,
        "output": output,
        "media_facts": {
            "output": _compact_faithful_media_facts(media_facts.get("output")),
        },
        "qa": _compact_nle_qa(result.get("qa")),
    }
    if not exported:
        if result.get("verified") is not True:
            raise TypeError("Jianying verification returned an invalid receipt")
        compact["verified"] = True
        return compact

    input_sha256 = result.get("input_sha256")
    output_sha256 = result.get("output_sha256")
    if (
        not isinstance(input_sha256, str)
        or not _PUBLIC_SHA256_PATTERN.fullmatch(input_sha256)
        or not isinstance(output_sha256, str)
        or output_sha256 != output["sha256"]
        or not isinstance(result.get("delivery_path"), str)
        or _compact_workflow_relative_path(result["delivery_path"], "delivery_path")
        != output["path"]
    ):
        raise TypeError("Jianying export returned invalid output hashes")
    report_path = _compact_workflow_relative_path(result.get("report_path"), "report_path")
    compact.update(
        {
            "output_dir": _compact_workflow_relative_path(result.get("output_dir"), "output_dir"),
            "delivery_path": output["path"],
            "report_path": report_path,
            "input_sha256": input_sha256,
            "output_sha256": output_sha256,
        }
    )
    compact["media_facts"]["input"] = _compact_faithful_media_facts(
        media_facts.get("input")
    )
    return compact


def run_faithful_evidence(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Build bounded review evidence after a no-core rights preflight."""

    try:
        plan, _plan_input_sha256 = _load_public_json_snapshot(args.plan)
    except Exception:
        return _faithful_workflow_error_payload(
            "faithful evidence", CliArgumentError("invalid faithful plan")
        ), 2
    if not isinstance(plan, Mapping) or plan.get("rights_confirmed") is not True:
        return _faithful_workflow_error_payload(
            "faithful evidence", CliArgumentError("rights confirmation is required")
        ), 2
    try:
        result = _faithful_evidence_module().build_faithful_evidence(
            plan,
            args.project_root,
            args.output_dir,
            ffmpeg=args.ffmpeg,
            ffprobe=args.ffprobe,
            timeout_seconds=args.timeout_seconds,
            max_panels=args.max_panels,
        )
        if not isinstance(result, Mapping):
            raise TypeError("faithful evidence returned an invalid result")
        return _runtime_module().success_payload(_compact_faithful_evidence_result(result)), 0
    except Exception as exc:
        return _faithful_workflow_error_payload("faithful evidence", exc), 2


def _jianying_rights_error(operation: str) -> tuple[dict[str, Any], int]:
    """Reject an unconfirmed delivery before importing its core or touching tools."""

    return _faithful_workflow_error_payload(
        operation, CliArgumentError("rights confirmation is required")
    ), 2


def _jianying_profile_error(operation: str) -> tuple[dict[str, Any], int]:
    """Keep the public CLI pinned to the one audited delivery profile."""

    return _faithful_workflow_error_payload(
        operation, CliArgumentError("unsupported Jianying delivery profile")
    ), 2


def run_jianying_export(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Create a rights-confirmed fixed-profile NLE derivative delivery."""

    operation = "Jianying export"
    if getattr(args, "rights_confirmed", False) is not True:
        return _jianying_rights_error(operation)
    if getattr(args, "profile", JIANYING_PROFILE) != JIANYING_PROFILE:
        return _jianying_profile_error(operation)
    try:
        result = _nle_module().export_nle_delivery(
            args.source,
            project_root=args.project_root,
            rights_confirmed=True,
            output_dir=args.output_dir,
            profile=JIANYING_PROFILE,
            ffmpeg=args.ffmpeg,
            ffprobe=args.ffprobe,
            timeout_seconds=args.timeout_seconds,
        )
        if not isinstance(result, Mapping):
            raise TypeError("Jianying export returned an invalid result")
        return _runtime_module().success_payload(_compact_nle_result(result, exported=True)), 0
    except Exception as exc:
        return _faithful_workflow_error_payload(operation, exc), 2


def run_jianying_verify(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Read-only verify a rights-confirmed fixed-profile NLE delivery."""

    operation = "Jianying verification"
    if getattr(args, "rights_confirmed", False) is not True:
        return _jianying_rights_error(operation)
    if getattr(args, "profile", JIANYING_PROFILE) != JIANYING_PROFILE:
        return _jianying_profile_error(operation)
    try:
        result = _nle_module().verify_nle_delivery(
            args.delivery,
            project_root=args.project_root,
            rights_confirmed=True,
            profile=JIANYING_PROFILE,
            ffmpeg=args.ffmpeg,
            ffprobe=args.ffprobe,
            timeout_seconds=args.timeout_seconds,
        )
        if not isinstance(result, Mapping):
            raise TypeError("Jianying verification returned an invalid result")
        return _runtime_module().success_payload(_compact_nle_result(result, exported=False)), 0
    except Exception as exc:
        return _faithful_workflow_error_payload(operation, exc), 2


def _add_runtime_arguments(
    parser: argparse.ArgumentParser,
    *,
    include_timeout: bool = True,
    timeout_default: float = 30.0,
) -> None:
    parser.add_argument("--ffmpeg", type=Path, help="Explicit local ffmpeg executable")
    parser.add_argument("--ffprobe", type=Path, help="Explicit local ffprobe executable")
    if include_timeout:
        parser.add_argument("--timeout", type=float, default=timeout_default)


def _bounded_cli_integer(minimum: int, maximum: int):
    """Build a small argparse converter for public bounded integer options."""

    def convert(value: str) -> int:
        try:
            converted = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be an integer") from exc
        if not minimum <= converted <= maximum:
            raise argparse.ArgumentTypeError(
                f"must be between {minimum} and {maximum}"
            )
        return converted

    return convert


def build_parser() -> argparse.ArgumentParser:
    parser = _BoundedArgumentParser(prog="video-remix")
    parser.add_argument("--version", action="version", version=f"%(prog)s {CLI_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor", help="Inspect local alpha runtime capabilities")
    _add_runtime_arguments(doctor, include_timeout=False)
    doctor.add_argument("--json", action="store_true", dest="as_json")
    validate_template = subparsers.add_parser("validate-template", help="Validate a Template IR JSON file")
    validate_template.add_argument("template", type=Path)
    validate_template.add_argument("--json", action="store_true", dest="as_json")
    validate_assets = subparsers.add_parser("validate-assets", help="Validate replacement assets against a Template IR")
    validate_assets.add_argument("template", type=Path)
    validate_assets.add_argument("manifest", type=Path)
    validate_assets.add_argument("--project-root", type=Path, help="Allowed project directory; defaults to the manifest directory")
    validate_assets.add_argument("--allow-missing-files", action="store_true", help="Validate structure and path containment without requiring files to exist")
    validate_assets.add_argument("--json", action="store_true", dest="as_json")
    validate_compiler_plan = subparsers.add_parser(
        "validate-compiler-plan",
        help="Validate a frozen fixed-subject-carousel Compiler Plan",
    )
    validate_compiler_plan.add_argument("plan", type=Path)
    validate_compiler_plan.add_argument("--json", action="store_true", dest="as_json")
    validate_proposal = subparsers.add_parser(
        "validate-proposal", help="Validate a v0.4 Compiler Plan proposal packet"
    )
    validate_proposal.add_argument("proposal", type=Path)
    validate_proposal.add_argument("--json", action="store_true", dest="as_json")
    validate_review = subparsers.add_parser(
        "validate-review", help="Validate a v0.4 Compiler Plan review decision"
    )
    validate_review.add_argument("review", type=Path)
    validate_review.add_argument("--json", action="store_true", dest="as_json")
    validate_asset_proposal = subparsers.add_parser(
        "validate-asset-proposal",
        help="Validate a v0.5 local asset-pack proposal packet",
    )
    validate_asset_proposal.add_argument("proposal", type=Path)
    validate_asset_proposal.add_argument("--json", action="store_true", dest="as_json")
    validate_asset_review = subparsers.add_parser(
        "validate-asset-review",
        help="Validate a v0.5 local asset-mapping review packet",
    )
    validate_asset_review.add_argument("review", type=Path)
    validate_asset_review.add_argument("--json", action="store_true", dest="as_json")
    validate_generation_request = subparsers.add_parser(
        "validate-generation-request",
        help="Validate a v0.6 local generation request packet",
    )
    validate_generation_request.add_argument("request", type=Path)
    validate_generation_request.add_argument("--json", action="store_true", dest="as_json")
    validate_generation_plan = subparsers.add_parser(
        "validate-generation-plan",
        help="Validate a v0.6 local generation plan packet",
    )
    validate_generation_plan.add_argument("plan", type=Path)
    validate_generation_plan.add_argument("--json", action="store_true", dest="as_json")
    validate_generation_plan_review = subparsers.add_parser(
        "validate-generation-plan-review",
        help="Validate a v0.6 local generation plan review packet",
    )
    validate_generation_plan_review.add_argument("review", type=Path)
    validate_generation_plan_review.add_argument("--json", action="store_true", dest="as_json")
    validate_generation_results_proposal = subparsers.add_parser(
        "validate-generation-results-proposal",
        help="Validate a v0.6 local generation results proposal packet",
    )
    validate_generation_results_proposal.add_argument("proposal", type=Path)
    validate_generation_results_proposal.add_argument("--json", action="store_true", dest="as_json")
    validate_generation_results_review = subparsers.add_parser(
        "validate-generation-results-review",
        help="Validate a v0.6 local generation results review packet",
    )
    validate_generation_results_review.add_argument("review", type=Path)
    validate_generation_results_review.add_argument("--json", action="store_true", dest="as_json")
    validate_faithful_plan = subparsers.add_parser(
        "validate-faithful-plan",
        help="Validate a v0.9 local faithful rebuild plan",
    )
    validate_faithful_plan.add_argument("plan", type=Path)
    validate_faithful_plan.add_argument("--json", action="store_true", dest="as_json")

    probe = subparsers.add_parser("probe", help="Probe one local media source")
    probe.add_argument("source", type=Path)
    _add_runtime_arguments(probe)
    probe.add_argument("--json", action="store_true", dest="as_json")

    survey = subparsers.add_parser("survey", help="Create a bounded local reference survey")
    survey.add_argument("source", type=Path)
    survey.add_argument("--project-root", type=Path, required=True)
    survey.add_argument("--output-dir", type=Path, default=Path("reference-survey"))
    survey.add_argument("--frame", dest="frame_numbers", type=int, action="append")
    survey.add_argument("--samples", dest="sample_count", type=int, default=12)
    survey.add_argument("--no-contact-sheet", dest="include_contact_sheet", action="store_false")
    survey.add_argument("--no-audio", dest="include_audio", action="store_false")
    survey.add_argument("--contact-sheet-columns", type=int, default=4)
    survey.set_defaults(include_contact_sheet=True, include_audio=True)
    _add_runtime_arguments(survey)
    survey.add_argument("--json", action="store_true", dest="as_json")

    compile_command = subparsers.add_parser(
        "compile",
        help="Compile an authorized local fixed-subject-carousel S1 reference",
    )
    compile_command.add_argument("source", type=Path)
    compile_command.add_argument("plan", type=Path)
    compile_command.add_argument("--project-root", type=Path, required=True)
    compile_command.add_argument("--output-dir", type=Path, default=Path("template-compile"))
    _add_runtime_arguments(compile_command, timeout_default=120.0)
    compile_command.add_argument("--json", action="store_true", dest="as_json")

    propose = subparsers.add_parser(
        "propose",
        help="Create a local review-required fixed-subject-carousel S1 proposal",
    )
    propose.add_argument("reference", type=Path)
    propose.add_argument("--project-root", type=Path, required=True)
    propose.add_argument("--output-dir", type=Path, default=Path("plan-proposal"))
    propose.add_argument("--template-id", required=True)
    propose.add_argument("--slot-count-hint", type=_bounded_cli_integer(1, 64))
    propose.add_argument("--reference-rights-confirmed", action="store_true", required=True)
    propose.add_argument("--audio-rights-confirmed", action="store_true")
    propose.add_argument(
        "--audio-mode",
        choices=("preserve", "replaceable", "mute"),
        default="preserve",
    )
    propose.add_argument(
        "--output-profile",
        dest="output_profiles",
        choices=("720x1280", "1080x1920"),
        action="append",
    )
    propose.add_argument("--analysis-width", type=_bounded_cli_integer(32, 256), default=96)
    propose.add_argument(
        "--max-evidence-frames", type=_bounded_cli_integer(1, 64), default=24
    )
    _add_runtime_arguments(propose, timeout_default=120.0)
    propose.add_argument("--json", action="store_true", dest="as_json")

    freeze_plan = subparsers.add_parser(
        "freeze-plan",
        help="Freeze approved packets named by normalized project-root-relative paths",
    )
    freeze_plan.add_argument(
        "proposal",
        type=Path,
        help="Normalized project-root-relative Proposal path; absolute and UNC paths are rejected",
    )
    freeze_plan.add_argument(
        "review",
        type=Path,
        help="Normalized project-root-relative Review path; absolute and UNC paths are rejected",
    )
    freeze_plan.add_argument("--project-root", type=Path, required=True)
    freeze_plan.add_argument("--output-dir", type=Path, default=Path("frozen-plan"))
    freeze_plan.add_argument("--json", action="store_true", dest="as_json")

    propose_assets = subparsers.add_parser(
        "propose-assets",
        help="Create a local review-required asset-pack proposal",
    )
    propose_assets.add_argument("template", type=Path)
    propose_assets.add_argument("--project-root", type=Path, required=True)
    propose_assets.add_argument(
        "--asset-pack",
        type=Path,
        required=True,
        help="Direct child of project root; verified by the guarded asset core",
    )
    propose_assets.add_argument("--output-dir", type=Path, default=Path("asset-proposal"))
    propose_assets.add_argument(
        "--asset-pack-rights-confirmed",
        action="store_true",
        required=True,
    )
    propose_assets.add_argument("--ffprobe", type=Path, default=Path("ffprobe"))
    propose_assets.add_argument("--timeout", type=float, default=60.0)
    propose_assets.add_argument("--json", action="store_true", dest="as_json")

    freeze_assets = subparsers.add_parser(
        "freeze-assets",
        help="Freeze an approved local asset mapping after a guarded rescan",
    )
    freeze_assets.add_argument("proposal", type=Path)
    freeze_assets.add_argument("review", type=Path)
    freeze_assets.add_argument("--project-root", type=Path, required=True)
    freeze_assets.add_argument("--output-dir", type=Path, default=Path("frozen-assets"))
    freeze_assets.add_argument("--ffprobe", type=Path, default=Path("ffprobe"))
    freeze_assets.add_argument("--timeout", type=float, default=60.0)
    freeze_assets.add_argument("--json", action="store_true", dest="as_json")

    prepare_generation = subparsers.add_parser(
        "prepare-generation",
        help="Prepare a review-required local generation plan",
    )
    prepare_generation.add_argument("template", type=Path)
    prepare_generation.add_argument("request", type=Path)
    prepare_generation.add_argument("--project-root", type=Path, required=True)
    prepare_generation.add_argument(
        "--reference-pack",
        type=Path,
        required=True,
        help="Direct child of project root; verified by the guarded generation core",
    )
    prepare_generation.add_argument("--output-dir", type=Path, default=Path("generation-plan"))
    prepare_generation.add_argument(
        "--generation-rights-confirmed",
        action="store_true",
        required=True,
    )
    prepare_generation.add_argument("--ffprobe", type=Path, default=Path("ffprobe"))
    prepare_generation.add_argument("--timeout", type=float, default=60.0)
    prepare_generation.add_argument("--json", action="store_true", dest="as_json")

    propose_generation_results = subparsers.add_parser(
        "propose-generation-results",
        help="Create a review-required proposal for local generated results",
    )
    propose_generation_results.add_argument("plan", type=Path)
    propose_generation_results.add_argument("plan_review", type=Path)
    propose_generation_results.add_argument("--project-root", type=Path, required=True)
    propose_generation_results.add_argument(
        "--result-pack",
        type=Path,
        required=True,
        help="Direct child of project root; verified by the guarded generation core",
    )
    propose_generation_results.add_argument(
        "--output-dir", type=Path, default=Path("generation-results-proposal")
    )
    propose_generation_results.add_argument(
        "--generation-results-rights-confirmed",
        action="store_true",
        required=True,
    )
    propose_generation_results.add_argument("--ffprobe", type=Path, default=Path("ffprobe"))
    propose_generation_results.add_argument("--timeout", type=float, default=60.0)
    propose_generation_results.add_argument("--json", action="store_true", dest="as_json")

    assemble_generation_pack = subparsers.add_parser(
        "assemble-generation-pack",
        help="Assemble an approved local render-ready asset pack",
    )
    assemble_generation_pack.add_argument("plan", type=Path)
    assemble_generation_pack.add_argument("plan_review", type=Path)
    assemble_generation_pack.add_argument("results_proposal", type=Path)
    assemble_generation_pack.add_argument("results_review", type=Path)
    assemble_generation_pack.add_argument("--project-root", type=Path, required=True)
    assemble_generation_pack.add_argument(
        "--output-dir", type=Path, default=Path("generation-asset-pack")
    )
    assemble_generation_pack.add_argument("--ffprobe", type=Path, default=Path("ffprobe"))
    assemble_generation_pack.add_argument("--timeout", type=float, default=60.0)
    assemble_generation_pack.add_argument("--json", action="store_true", dest="as_json")

    faithful_rebuild = subparsers.add_parser(
        "faithful-rebuild",
        help="Create a metadata-free faithful local source replica from an approved plan",
    )
    faithful_rebuild.add_argument("plan", type=Path)
    faithful_rebuild.add_argument("--project-root", type=Path, required=True)
    faithful_rebuild.add_argument("--output-dir", type=Path, default=Path("faithful-rebuild"))
    faithful_rebuild.add_argument("--ffmpeg", type=Path)
    faithful_rebuild.add_argument("--ffprobe", type=Path)
    faithful_rebuild.add_argument("--timeout-seconds", type=float, default=60.0)
    faithful_rebuild.add_argument("--json", action="store_true", dest="as_json")

    faithful_evidence = subparsers.add_parser(
        "faithful-evidence",
        help="Create bounded no-OCR local review evidence for an approved faithful plan",
    )
    faithful_evidence.add_argument("plan", type=Path)
    faithful_evidence.add_argument("--project-root", type=Path, required=True)
    faithful_evidence.add_argument("--output-dir", type=Path, default=Path("faithful-evidence"))
    faithful_evidence.add_argument("--ffmpeg", type=Path)
    faithful_evidence.add_argument("--ffprobe", type=Path)
    faithful_evidence.add_argument("--timeout-seconds", type=float, default=60.0)
    faithful_evidence.add_argument("--max-panels", type=_bounded_cli_integer(1, 24), default=24)
    faithful_evidence.add_argument("--json", action="store_true", dest="as_json")

    jianying_export = subparsers.add_parser(
        "jianying-export",
        help="Export an explicitly authorized fixed-profile Jianying-compatible MP4",
    )
    jianying_export.add_argument("source", type=Path)
    jianying_export.add_argument("--project-root", type=Path, required=True)
    jianying_export.add_argument("--rights-confirmed", action="store_true", required=True)
    jianying_export.add_argument("--output-dir", type=Path, default=Path("jianying-delivery"))
    jianying_export.add_argument("--profile", default=JIANYING_PROFILE)
    jianying_export.add_argument("--ffmpeg", type=Path)
    jianying_export.add_argument("--ffprobe", type=Path)
    jianying_export.add_argument("--timeout-seconds", type=float, default=60.0)
    jianying_export.add_argument("--json", action="store_true", dest="as_json")

    jianying_verify = subparsers.add_parser(
        "jianying-verify",
        help="Read-only verify an explicitly authorized fixed-profile Jianying delivery",
    )
    jianying_verify.add_argument("delivery", type=Path)
    jianying_verify.add_argument("--project-root", type=Path, required=True)
    jianying_verify.add_argument("--rights-confirmed", action="store_true", required=True)
    jianying_verify.add_argument("--profile", default=JIANYING_PROFILE)
    jianying_verify.add_argument("--ffmpeg", type=Path)
    jianying_verify.add_argument("--ffprobe", type=Path)
    jianying_verify.add_argument("--timeout-seconds", type=float, default=60.0)
    jianying_verify.add_argument("--json", action="store_true", dest="as_json")

    render = subparsers.add_parser("render", help="Render and technically verify an S1 local template")
    render.add_argument("template", type=Path)
    render.add_argument("manifest", type=Path)
    render.add_argument("--project-root", type=Path, required=True)
    render.add_argument("--frame-directory", type=Path, default=Path("render") / "master-frames")
    render.add_argument("--debug-bounds", action="store_true")
    render.add_argument("--summary", type=Path, help="New root-contained JSON summary path; never overwrites")
    _add_runtime_arguments(render, timeout_default=300.0)
    render.add_argument("--json", action="store_true", dest="as_json")

    qa = subparsers.add_parser("qa", help="Technically verify one encoded delivery")
    qa.add_argument("source", type=Path)
    qa.add_argument("--width", dest="expected_width", type=int)
    qa.add_argument("--height", dest="expected_height", type=int)
    qa.add_argument("--fps", dest="expected_fps", type=float)
    qa.add_argument("--frames", dest="expected_frames", type=int)
    audio_group = qa.add_mutually_exclusive_group()
    audio_group.add_argument("--expect-audio", dest="expect_audio", action="store_true")
    audio_group.add_argument("--expect-no-audio", dest="expect_audio", action="store_false")
    qa.set_defaults(expect_audio=None)
    _add_runtime_arguments(qa)
    qa.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one public command with bounded, machine-readable failures."""

    try:
        args = build_parser().parse_args(argv)
    except CliArgumentError as exc:
        _emit_stable_json(_error_payload(exc, invalid_argument=True))
        return 2
    try:
        if args.command == "doctor":
            emit(doctor_payload(ffmpeg=args.ffmpeg, ffprobe=args.ffprobe), args.as_json)
            return 0
        if args.command == "probe":
            _emit_stable_json(run_probe(args))
            return 0
        if args.command == "survey":
            _emit_stable_json(run_survey(args))
            return 0
        if args.command == "compile":
            payload, status = run_compile(args)
            _emit_stable_json(payload)
            return status
        if args.command == "propose":
            payload, status = run_propose(args)
            _emit_stable_json(payload)
            return status
        if args.command == "freeze-plan":
            payload, status = run_freeze(args)
            _emit_stable_json(payload)
            return status
        if args.command == "propose-assets":
            payload, status = run_propose_assets(args)
            _emit_stable_json(payload)
            return status
        if args.command == "freeze-assets":
            payload, status = run_freeze_assets(args)
            _emit_stable_json(payload)
            return status
        if args.command == "prepare-generation":
            payload, status = run_prepare_generation(args)
            _emit_stable_json(payload)
            return status
        if args.command == "propose-generation-results":
            payload, status = run_propose_generation_results(args)
            _emit_stable_json(payload)
            return status
        if args.command == "assemble-generation-pack":
            payload, status = run_assemble_generation_pack(args)
            _emit_stable_json(payload)
            return status
        if args.command == "faithful-rebuild":
            payload, status = run_faithful_rebuild(args)
            _emit_stable_json(payload)
            return status
        if args.command == "faithful-evidence":
            payload, status = run_faithful_evidence(args)
            _emit_stable_json(payload)
            return status
        if args.command == "jianying-export":
            payload, status = run_jianying_export(args)
            _emit_stable_json(payload)
            return status
        if args.command == "jianying-verify":
            payload, status = run_jianying_verify(args)
            _emit_stable_json(payload)
            return status
        if args.command == "render":
            try:
                payload, status = run_render(args)
            except Exception as exc:
                _emit_stable_json(_render_error_payload(_runtime_module(), exc))
                return 2
            _emit_stable_json(payload)
            return status
        if args.command == "qa":
            payload, status = run_qa(args)
            _emit_stable_json(payload)
            return status

        if args.command == "validate-template":
            try:
                template, _template_sha256 = _load_public_json_snapshot(args.template)
            except Exception as exc:
                errors = [_public_json_error(exc)]
            else:
                errors = _validate_public_template_document(template)
        elif args.command == "validate-compiler-plan":
            errors = validate_compiler_plan_data(load_json(args.plan))
        elif args.command == "validate-proposal":
            errors = _validate_packet_file(
                args.proposal, validate_proposal_data, "proposal"
            )
        elif args.command == "validate-review":
            errors = _validate_packet_file(args.review, validate_review_data, "review")
        elif args.command == "validate-asset-proposal":
            errors = _validate_asset_packet_file(
                args.proposal, validate_asset_proposal_data
            )
        elif args.command == "validate-asset-review":
            errors = _validate_asset_packet_file(
                args.review, validate_asset_review_data
            )
        elif args.command == "validate-generation-request":
            errors = _validate_generation_packet_file(
                args.request, validate_generation_request_data
            )
        elif args.command == "validate-generation-plan":
            errors = _validate_generation_packet_file(
                args.plan, validate_generation_plan_data
            )
        elif args.command == "validate-generation-plan-review":
            errors = _validate_generation_packet_file(
                args.review, validate_generation_plan_review_data
            )
        elif args.command == "validate-generation-results-proposal":
            errors = _validate_generation_packet_file(
                args.proposal, validate_generation_results_proposal_data
            )
        elif args.command == "validate-generation-results-review":
            errors = _validate_generation_packet_file(
                args.review, validate_generation_results_review_data
            )
        elif args.command == "validate-faithful-plan":
            errors = _validate_faithful_plan_file(args.plan)
        else:
            try:
                template, _template_sha256 = _load_public_json_snapshot(args.template)
            except Exception as exc:
                errors = [_public_json_error(exc)]
            else:
                try:
                    manifest, _manifest_sha256 = _load_public_json_snapshot(args.manifest)
                except Exception as exc:
                    errors = [_public_json_error(exc)]
                else:
                    errors = _validate_public_asset_documents(
                        template,
                        manifest,
                        args.manifest,
                        check_files=not args.allow_missing_files,
                        project_root=args.project_root,
                    )
    except (ValueError, OSError, TypeError) as exc:
        errors = [str(exc)]
    except Exception as exc:
        _emit_stable_json(_error_payload(exc))
        return 2
    payload = {"status": "pass" if not errors else "fail", "errors": errors}
    emit(payload, args.as_json)
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
