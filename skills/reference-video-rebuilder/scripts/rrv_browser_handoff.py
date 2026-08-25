#!/usr/bin/env python3
"""Offline, user-operated Higgsfield Motion Control handoff.

This module deliberately has no browser, network, credential, provider SDK, or
retry surface.  It makes two minimal, exact local upload files after a new
byte-scoped cloud reauthorization, records an *unattested* pre-submit user
confirmation, and normalizes one manually downloaded file into the existing
v0.10 temporal result-pack contract.

The module does not prove that an upload, charge, provider job, download, or
motion transformation occurred.  All provider-facing actions remain manual.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
import hashlib
import math
import os
from pathlib import Path
import re
from typing import Any, Iterator, Mapping, Sequence

try:  # Direct execution from the Skill scripts directory.
    import rrv_assets
    import rrv_nle
    import rrv_propose
    import rrv_runtime
    import rrv_temporal
except ImportError:  # pragma: no cover - package-style import support.
    from . import rrv_assets, rrv_nle, rrv_propose, rrv_runtime, rrv_temporal  # type: ignore[no-redef]


SCHEMA_VERSION = "0.10.1"
DEFAULT_TIMEOUT_SECONDS = 60.0
PROVIDER_ID = "higgsfield-web"
SURFACE = "motion-control"
MODEL = "kling-3.0-motion-control"
RESOLUTION = "720p"
PROVIDER_PROVENANCE = "unattested-user-operated-web"
UPLOAD_PACK_DIRECTORY = "upload"
CHARACTER_FILENAME = "character.png"
MOTION_FILENAME = "motion-reference.mp4"
PLAN_FILENAME = "higgsfield-web-handoff-plan.json"
RECEIPT_FILENAME = "higgsfield-web-browser-receipt.json"
RESULT_FILENAME = "temporal-replacement.mp4"
RECEIPT_CONSUMPTION_PREFIX = ".rrv-higgsfield-web-receipt-use-"
RECEIPT_CONSUMPTION_FILENAME = "receipt-consumption.json"
ACTION_CONSUMPTION_PREFIX = ".rrv-higgsfield-web-action-use-"
ACTION_CONSUMPTION_FILENAME = "action-consumption.json"
MAX_CREDITS = 100_000
MAX_BALANCE = 1_000_000
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MOTION_MODES = frozenset({"pose-transfer", "video-to-video"})
_AUDIO_MODES = frozenset({"mute", "preserve-reference"})

_SKILL_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_DIRECTORY = _SKILL_ROOT / "assets" / "schemas"
_REQUEST_SCHEMA = _SCHEMA_DIRECTORY / "higgsfield-web-handoff-request.schema.json"
_PLAN_SCHEMA = _SCHEMA_DIRECTORY / "higgsfield-web-handoff-plan.schema.json"
_RECEIPT_SCHEMA = _SCHEMA_DIRECTORY / "higgsfield-web-browser-receipt.schema.json"


def _invalid(message: str) -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(rrv_runtime.ERR_INVALID_ARGUMENT, message)


def _tool_error(message: str) -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(rrv_runtime.ERR_TOOL_EXECUTION, message)


def _safe_exception(exc: BaseException) -> rrv_runtime.RRVError:
    """Return a bounded error without source paths, prompts, or tool output."""

    if isinstance(exc, rrv_runtime.RRVError):
        if exc.code == rrv_runtime.ERR_OUTPUT_EXISTS:
            return rrv_runtime.RRVError(exc.code, "refusing to overwrite an existing output")
        if exc.code == rrv_runtime.ERR_INVALID_ARGUMENT:
            return _invalid("browser handoff input was rejected")
        if exc.code == rrv_runtime.ERR_TOOL_NOT_FOUND:
            return rrv_runtime.RRVError(exc.code, "required local media tool is unavailable")
        if exc.code == rrv_runtime.ERR_TOOL_TIMEOUT:
            return rrv_runtime.RRVError(exc.code, "local browser handoff media operation exceeded its timeout")
        if exc.code == rrv_runtime.ERR_CAPABILITY_UNAVAILABLE:
            return rrv_runtime.RRVError(exc.code, "local browser handoff capability is unavailable")
        return rrv_runtime.RRVError(exc.code, "local browser handoff operation failed")
    return _tool_error("local browser handoff operation failed")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(rrv_runtime.stable_json_dumps(value, indent=None).encode("utf-8")).hexdigest()


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _valid_relative_path(value: Any) -> bool:
    return rrv_assets._relative_path_parts(value) is not None


def _schema_errors(data: Any, schema_path: Path, label: str) -> list[str]:
    return rrv_assets._schema_errors(data, schema_path, label)


def _unique_errors(errors: Sequence[str]) -> list[str]:
    return rrv_assets._unique_errors(errors)


def _nonfinite_errors(value: Any) -> list[str]:
    errors: list[str] = []
    rrv_assets._find_nonfinite(value, "$", errors)
    return errors


def _authorization_expiry(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _assert_current_reauthorizations(request: Mapping[str, Any]) -> None:
    """Require both one-upload permissions to remain valid for a new action."""

    authorizations = request.get("upload_authorizations")
    if not isinstance(authorizations, Mapping):
        raise _invalid("browser upload reauthorizations are invalid")
    now = datetime.now(timezone.utc)
    for role in ("character_image", "motion_reference"):
        item = authorizations.get(role)
        expiry = _authorization_expiry(item.get("expires_at")) if isinstance(item, Mapping) else None
        if expiry is None or expiry <= now:
            raise _invalid("browser upload reauthorization is expired or invalid for a new action")


def _request_semantic_errors(data: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if not rrv_temporal._valid_id(data.get("output_id")):
        errors.append("$.output_id: portable_id")
    if not rrv_temporal._valid_id(data.get("character_slot_id")):
        errors.append("$.character_slot_id: portable_id")
    if data.get("motion_mode") not in _MOTION_MODES:
        errors.append("$.motion_mode: supported_motion_mode")
    if data.get("audio_mode") not in _AUDIO_MODES:
        errors.append("$.audio_mode: supported_audio_mode")
    if data.get("lip_sync_requested") is not False or data.get("clone_authorized_voice_requested") is not False:
        errors.append("$: voice_and_lip_are_disabled")
    if not _is_int(data.get("max_credits")) or not 1 <= int(data["max_credits"]) <= MAX_CREDITS:
        errors.append("$.max_credits: bounded_positive_integer")
    authorizations = data.get("upload_authorizations")
    if not isinstance(authorizations, Mapping):
        return errors + ["$.upload_authorizations: object"]
    character, motion = authorizations.get("character_image"), authorizations.get("motion_reference")
    if not isinstance(character, Mapping) or not isinstance(motion, Mapping):
        return errors + ["$.upload_authorizations: exact_authorizations"]
    for name, authorization in (("character_image", character), ("motion_reference", motion)):
        if (
            authorization.get("provider_id") != PROVIDER_ID
            or authorization.get("purpose") != SURFACE
            or authorization.get("output_id") != data.get("output_id")
            or authorization.get("rights_confirmed") is not True
            or authorization.get("cloud_upload_confirmed") is not True
            or not _valid_sha256(authorization.get("source_sha256"))
            or _authorization_expiry(authorization.get("expires_at")) is None
        ):
            errors.append(f"$.upload_authorizations.{name}: exact_scoped_reauthorization")
    if character.get("source_slot_id") != data.get("character_slot_id"):
        errors.append("$.upload_authorizations.character_image.source_slot_id: character_slot_binding")
    if character.get("source_sha256") == motion.get("source_sha256"):
        errors.append("$.upload_authorizations: distinct_source_bytes")
    return errors


def _plan_semantic_errors(data: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in (
        "temporal_plan_path",
        "temporal_plan_review_path",
        "handoff_request_path",
        "template_path",
        "manifest_path",
        "upload_pack",
    ):
        if not _valid_relative_path(data.get(key)):
            errors.append(f"$.{key}: normalized_relative_path")
    for key in (
        "temporal_plan_sha256",
        "temporal_plan_review_sha256",
        "handoff_request_sha256",
        "template_sha256",
        "manifest_sha256",
        "reference_inventory_sha256",
        "upload_inventory_sha256",
    ):
        if not _valid_sha256(data.get(key)):
            errors.append(f"$.{key}: sha256")
    output = data.get("output")
    if not isinstance(output, Mapping) or not rrv_temporal._valid_id(output.get("id")) or not all(
        _is_int(output.get(field)) and int(output[field]) >= 2 for field in ("width", "height")
    ):
        errors.append("$.output: output_spec")
    requirements = data.get("requirements")
    if not isinstance(requirements, Mapping) or (
        requirements.get("motion_mode") not in _MOTION_MODES
        or requirements.get("audio_mode") not in _AUDIO_MODES
        or requirements.get("lip_sync_required") is not False
        or requirements.get("voice_authorization_required") is not False
        or requirements.get("clone_authorized_voice_supported") is not False
    ):
        errors.append("$.requirements: limited_motion_audio_requirements")
    reauthorization = data.get("cloud_reauthorization")
    if not isinstance(reauthorization, Mapping) or (
        reauthorization.get("scope") != "single-user-operated-higgsfield-upload"
        or reauthorization.get("supersedes_manifest_local_only_for_this_upload_only") is not True
        or not _valid_sha256(reauthorization.get("character_authorization_sha256"))
        or not _valid_sha256(reauthorization.get("motion_authorization_sha256"))
    ):
        errors.append("$.cloud_reauthorization: narrow_new_reauthorization")
    inventory = data.get("upload_inventory")
    if not isinstance(inventory, list) or len(inventory) != 2:
        return errors + ["$.upload_inventory: exactly_two"]
    expected = {
        "character-image": (CHARACTER_FILENAME, "image/png"),
        "motion-video": (MOTION_FILENAME, "video/mp4"),
    }
    seen: set[str] = set()
    prefix = str(data.get("upload_pack", "")).rstrip("/")
    for index, item in enumerate(inventory):
        if not isinstance(item, Mapping):
            errors.append(f"$.upload_inventory[{index}]: object")
            continue
        role = item.get("role")
        if role not in expected or role in seen:
            errors.append(f"$.upload_inventory[{index}].role: exact_unique_role")
            continue
        seen.add(str(role))
        filename, media_type = expected[str(role)]
        if item.get("path") != f"{prefix}/{filename}" or item.get("media_type") != media_type:
            errors.append(f"$.upload_inventory[{index}]: canonical_upload_path")
        if not _valid_sha256(item.get("sha256")) or not _is_int(item.get("size_bytes")) or not 1 <= int(item["size_bytes"]) <= rrv_assets.MAX_FILE_BYTES:
            errors.append(f"$.upload_inventory[{index}]: bounded_artifact")
    if seen != set(expected):
        errors.append("$.upload_inventory: exact_roles")
    if isinstance(inventory, list) and data.get("upload_inventory_sha256") != _canonical_sha256(inventory):
        errors.append("$.upload_inventory_sha256: canonical_binding")
    return errors


def _receipt_semantic_errors(data: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("plan_path", "handoff_request_path"):
        if not _valid_relative_path(data.get(key)):
            errors.append(f"$.{key}: normalized_relative_path")
    for key in ("plan_sha256", "handoff_request_sha256", "upload_inventory_sha256"):
        if not _valid_sha256(data.get(key)):
            errors.append(f"$.{key}: sha256")
    for key, limit in (("max_credits", MAX_CREDITS), ("observed_cost_credits", MAX_CREDITS), ("available_credits_before", MAX_BALANCE), ("projected_remaining_credits_after", MAX_BALANCE)):
        if not _is_int(data.get(key)) or not 0 <= int(data[key]) <= limit:
            errors.append(f"$.{key}: bounded_integer")
    maximum, observed, available, remaining = (
        data.get("max_credits"),
        data.get("observed_cost_credits"),
        data.get("available_credits_before"),
        data.get("projected_remaining_credits_after"),
    )
    if all(_is_int(value) for value in (maximum, observed, available, remaining)):
        if int(maximum) < 1 or int(observed) < 1 or int(observed) > int(maximum) or int(available) < int(observed) or int(remaining) != int(available) - int(observed):
            errors.append("$: bounded_credit_arithmetic")
    if _authorization_expiry(data.get("recorded_at")) is None:
        errors.append("$.recorded_at: strict_utc")
    if data.get("motion_mode") not in _MOTION_MODES or data.get("audio_mode") not in _AUDIO_MODES:
        errors.append("$: supported_motion_audio")
    return errors


def validate_higgsfield_web_handoff_request_data(data: Any) -> list[str]:
    errors = _nonfinite_errors(data) + _schema_errors(data, _REQUEST_SCHEMA, "Higgsfield Web Handoff Request")
    if isinstance(data, Mapping):
        errors.extend(_request_semantic_errors(data))
    else:
        errors.append("$: object")
    return _unique_errors(errors)


def validate_higgsfield_web_handoff_plan_data(data: Any) -> list[str]:
    errors = _nonfinite_errors(data) + _schema_errors(data, _PLAN_SCHEMA, "Higgsfield Web Handoff Plan")
    if isinstance(data, Mapping):
        errors.extend(_plan_semantic_errors(data))
    else:
        errors.append("$: object")
    return _unique_errors(errors)


def validate_higgsfield_web_browser_receipt_data(data: Any) -> list[str]:
    errors = _nonfinite_errors(data) + _schema_errors(data, _RECEIPT_SCHEMA, "Higgsfield Web Browser Receipt")
    if isinstance(data, Mapping):
        errors.extend(_receipt_semantic_errors(data))
    else:
        errors.append("$: object")
    return _unique_errors(errors)


def _raise_validation(label: str, errors: Sequence[str]) -> None:
    if errors:
        raise _invalid(f"{label} did not pass strict validation")


@dataclass(frozen=True)
class _HandoffContext:
    temporal_plan: Mapping[str, Any]
    temporal_plan_snapshot: Any
    temporal_review_snapshot: Any
    web_request: Mapping[str, Any]
    web_request_snapshot: Any
    manifest: Mapping[str, Any]
    input_assets: list[dict[str, str]]
    source_spec: Mapping[str, Any]
    output: Mapping[str, Any]
    temporal_requirements: Mapping[str, Any]


def _load_temporal_context(
    root: Path,
    root_identity: Any,
    temporal_plan_path: str | os.PathLike[str],
    temporal_plan_review_path: str | os.PathLike[str],
    *,
    web_request_path: str | os.PathLike[str] | None = None,
) -> _HandoffContext:
    """Revalidate the entire v0.10 input chain before every new handoff step."""

    plan_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, temporal_plan_path, label="Temporal Plan")
    review_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, temporal_plan_review_path, label="Temporal Plan Review")
    plan, review = plan_snapshot.data, review_snapshot.data
    _raise_validation("Temporal Plan", rrv_temporal.validate_temporal_plan_data(plan))
    _raise_validation("Temporal Plan Review", rrv_temporal.validate_temporal_plan_review_data(review))
    if not isinstance(plan, Mapping) or not isinstance(review, Mapping):
        raise _invalid("Temporal Plan packet is invalid")
    rrv_temporal._approved_plan_review(plan, plan_snapshot.sha256, review)
    if (
        plan.get("privacy_profile") != "local-only"
        or plan.get("execution_profile") != "local-file-drop"
        or plan.get("cloud_upload_confirmed") is not False
    ):
        raise _invalid("browser handoff requires a reviewed local-only v0.10 plan")
    template_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, plan.get("template_path"), label="template")
    manifest_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, plan.get("manifest_path"), label="asset manifest")
    temporal_request_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, plan.get("request_path"), label="temporal request")
    _template, manifest, _request, input_assets, source_spec, output, requirements = rrv_temporal._validate_plan_static_bindings(
        root,
        root_identity,
        plan,
        template_snapshot=template_snapshot,
        manifest_snapshot=manifest_snapshot,
        request_snapshot=temporal_request_snapshot,
        enforce_current_authorization=False,
    )
    request_path = web_request_path if web_request_path is not None else None
    if request_path is None:
        raise _invalid("browser handoff request path is required")
    web_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, request_path, label="Higgsfield Web Handoff Request")
    web_request = web_snapshot.data
    _raise_validation("Higgsfield Web Handoff Request", validate_higgsfield_web_handoff_request_data(web_request))
    if not isinstance(web_request, Mapping):
        raise _invalid("Higgsfield Web Handoff Request is invalid")
    return _HandoffContext(
        temporal_plan=plan,
        temporal_plan_snapshot=plan_snapshot,
        temporal_review_snapshot=review_snapshot,
        web_request=web_request,
        web_request_snapshot=web_snapshot,
        manifest=manifest,
        input_assets=input_assets,
        source_spec=source_spec,
        output=output,
        temporal_requirements=requirements,
    )


def _assert_request_matches_context(context: _HandoffContext) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Bind the two new exact-byte reauthorizations to the frozen v0.10 plan."""

    request, plan = context.web_request, context.temporal_plan
    requirements = context.temporal_requirements
    if (
        request.get("output_id") != context.output.get("id")
        or request.get("motion_mode") != requirements.get("motion_mode")
        or request.get("audio_mode") != requirements.get("audio_mode")
        or requirements.get("motion_mode") not in _MOTION_MODES
        or requirements.get("audio_mode") not in _AUDIO_MODES
        or requirements.get("lip_sync_required") is not False
        or requirements.get("voice_authorization_required") is not False
        or request.get("lip_sync_requested") is not False
        or request.get("clone_authorized_voice_requested") is not False
        or request.get("cloud_upload_confirmed") is not True
    ):
        raise _invalid("browser handoff request does not match the limited Temporal Plan requirements")
    character_slot = request.get("character_slot_id")
    character_input = next((item for item in context.input_assets if item.get("slot_id") == character_slot), None)
    if not isinstance(character_input, Mapping) or character_input.get("media_type") not in {"image/jpeg", "image/png", "image/webp"}:
        raise _invalid("browser handoff character source must be one selected frozen image slot")
    inventory = plan.get("reference_inventory")
    reference_hash = inventory[0].get("sha256") if isinstance(inventory, list) and len(inventory) == 1 and isinstance(inventory[0], Mapping) else None
    authorizations = request.get("upload_authorizations")
    character_auth = authorizations.get("character_image") if isinstance(authorizations, Mapping) else None
    motion_auth = authorizations.get("motion_reference") if isinstance(authorizations, Mapping) else None
    if (
        not isinstance(character_auth, Mapping)
        or not isinstance(motion_auth, Mapping)
        or character_auth.get("source_slot_id") != character_slot
        or character_auth.get("source_sha256") != character_input.get("sha256")
        or motion_auth.get("source_sha256") != reference_hash
    ):
        raise _invalid("browser handoff requires two matching exact-byte reauthorizations")
    return character_auth, motion_auth


def _manifest_source_for_slot(manifest: Mapping[str, Any], slot_id: Any) -> Mapping[str, Any]:
    assets = manifest.get("assets")
    asset = next((item for item in assets if isinstance(item, Mapping) and item.get("slot_id") == slot_id), None) if isinstance(assets, list) else None
    if not isinstance(asset, Mapping) or asset.get("media_type") not in {"image/jpeg", "image/png", "image/webp"}:
        raise _invalid("browser handoff selected character source is unavailable")
    return asset


def _write_sanitized_character_png(raw: bytes, *, stage: Any, destination: Path) -> None:
    """Pixel-reconstruct one static source image; omit all source metadata."""

    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_CAPABILITY_UNAVAILABLE,
            "browser handoff image sanitization requires the Pillow dependency",
            {"capability": "browser_handoff_character_sanitization"},
        ) from exc
    source = oriented = converted = clean = None
    try:
        source = Image.open(BytesIO(raw))
        if bool(getattr(source, "is_animated", False)) or int(getattr(source, "n_frames", 1) or 1) != 1:
            raise _invalid("browser handoff character image must be a single still image")
        oriented = ImageOps.exif_transpose(source)
        width, height = oriented.size
        if not isinstance(width, int) or not isinstance(height, int) or width < 1 or height < 1 or width > rrv_assets.MAX_IMAGE_EDGE or height > rrv_assets.MAX_IMAGE_EDGE or width * height > rrv_assets.MAX_IMAGE_PIXELS:
            raise _invalid("browser handoff character image exceeds local limits")
        has_alpha = "A" in oriented.getbands() or "transparency" in oriented.info
        mode = "RGBA" if has_alpha else "RGB"
        converted = oriented.convert(mode)
        clean = Image.frombytes(mode, converted.size, converted.tobytes())
        with rrv_propose._open_stage_output_file(stage, destination, "sanitized browser character image") as handle:
            clean.save(handle, format="PNG", optimize=False, compress_level=9)
        rrv_propose._assert_stage_regular_file(stage, destination, "sanitized browser character image")
    except rrv_runtime.RRVError:
        raise
    except (OSError, ValueError, SyntaxError) as exc:
        raise _invalid("browser handoff character image could not be sanitized") from exc
    finally:
        for image in (clean, converted, oriented, source):
            if image is not None:
                try:
                    image.close()
                except Exception:
                    pass


def _run_ffmpeg(command: Sequence[str | os.PathLike[str]], *, timeout_seconds: float) -> None:
    try:
        rrv_runtime.run_command(command, timeout_seconds=timeout_seconds, check=True)
    except rrv_runtime.RRVError as exc:
        if exc.code in {rrv_runtime.ERR_TOOL_NOT_FOUND, rrv_runtime.ERR_TOOL_TIMEOUT}:
            raise
        raise _tool_error("local FFmpeg operation failed") from exc


def _write_silent_motion_upload(
    stage: Any,
    source: Path,
    destination: Path,
    *,
    ffmpeg: str | os.PathLike[str],
    ffprobe: str | os.PathLike[str],
    timeout_seconds: float,
    source_spec: Mapping[str, Any],
) -> dict[str, Any]:
    """Stream-copy only approved action video and strip its audio/metadata."""

    with rrv_temporal._hold_staged_media(stage, source, label="browser handoff action snapshot"):
        _run_ffmpeg(
            [
                os.fspath(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-xerror",
                "-i", str(source), "-map", "0:v:0", "-an", "-sn", "-dn",
                "-map_metadata", "-1", "-map_chapters", "-1", "-c:v", "copy",
                "-movflags", "+faststart", "-n", str(destination),
            ],
            timeout_seconds=timeout_seconds,
        )
    rrv_propose._assert_stage_regular_file(stage, destination, "silent browser motion upload")
    facts = rrv_temporal._inspect_staged_media(stage, destination, ffprobe=ffprobe, timeout_seconds=timeout_seconds, role="silent browser motion upload")
    rrv_temporal._require_media_matches(
        facts,
        width=int(source_spec["width"]), height=int(source_spec["height"]), fps=float(source_spec["fps"]), frame_count=int(source_spec["frame_count"]), expected_audio_streams=0,
        role="silent browser motion upload",
    )
    rrv_temporal._reject_result_metadata(stage, destination, ffprobe=ffprobe, timeout_seconds=timeout_seconds)
    rrv_temporal._full_decode(stage, destination, facts, ffmpeg=ffmpeg, timeout_seconds=timeout_seconds)
    return facts


def _stage_hash(stage: Any, path: Path, *, label: str) -> str:
    return rrv_temporal._stage_media_sha256(stage, path, label=label)


def _assert_exact_handoff_tree(stage: Any, expected_hashes: Mapping[str, str]) -> None:
    """Reject a sidecar, nested surprise, link, or changed upload before publish."""

    expected = set(expected_hashes)
    actual: set[str] = set()
    pending = [(stage.path, "")]
    while pending:
        directory, prefix = pending.pop()
        try:
            children = list(os.scandir(directory))
        except OSError as exc:
            raise _tool_error("could not inspect browser handoff publication stage") from exc
        for child in children:
            relative = f"{prefix}/{child.name}" if prefix else child.name
            entry = os.lstat(child.path)
            if rrv_propose._is_link_or_reparse(entry):
                raise _invalid("browser handoff publication contains an unsafe link")
            if child.is_dir(follow_symlinks=False):
                pending.append((Path(child.path), relative))
            elif child.is_file(follow_symlinks=False) and entry.st_nlink == 1:
                actual.add(relative)
            else:
                raise _invalid("browser handoff publication contains an unsafe artifact")
    if actual != expected:
        raise _invalid("browser handoff publication does not contain its exact artifacts")
    for relative, expected_hash in expected_hashes.items():
        path = stage.path / Path(relative)
        rrv_propose._assert_stage_regular_file(stage, path, "browser handoff staged artifact")
        if _stage_hash(stage, path, label="browser handoff staged artifact") != expected_hash:
            raise _invalid("browser handoff staged artifact changed before publication")
    rrv_propose._assert_stage_live(stage)


def _assert_exact_published_handoff_tree(target: Path, expected_hashes: Mapping[str, str]) -> None:
    """Recheck the renamed nested tree without resolving or following it."""

    expected = set(expected_hashes)
    actual: set[str] = set()
    pending = [(target, "")]
    while pending:
        directory, prefix = pending.pop()
        try:
            children = list(os.scandir(directory))
        except OSError as exc:
            raise _tool_error("could not inspect published browser handoff") from exc
        for child in children:
            relative = f"{prefix}/{child.name}" if prefix else child.name
            entry = os.lstat(child.path)
            if rrv_propose._is_link_or_reparse(entry):
                raise _tool_error("published browser handoff contains an unsafe link")
            if child.is_dir(follow_symlinks=False):
                pending.append((Path(child.path), relative))
            elif child.is_file(follow_symlinks=False) and entry.st_nlink == 1:
                actual.add(relative)
            else:
                raise _tool_error("published browser handoff contains an unsafe artifact")
    if actual != expected:
        raise _tool_error("published browser handoff does not contain its exact artifacts")
    for relative, expected_hash in expected_hashes.items():
        actual_hash = rrv_propose._hash_regular_file_no_follow(target / Path(relative), "published browser handoff artifact")
        if actual_hash != expected_hash:
            raise _tool_error("published browser handoff artifact changed during publication")


def _publish_exact_handoff_stage(root: Path, stage: Any, target: Path, *, expected_hashes: Mapping[str, str]) -> None:
    """Atomically publish a nested two-file pack with a final pre-rename hash gate.

    ``rrv_propose._publish_stage`` deliberately accepts only a flat
    ``expected_files`` map.  This handoff has an isolated nested upload pack,
    so mirror its guarded publication sequence and perform the exact nested
    tree/hash assertion immediately before the one rename operation.
    """

    if root != stage.root.path:
        raise _tool_error("browser handoff stage belongs to a different project root")
    parents = rrv_propose._target_parent_chain(root, target)
    try:
        rrv_propose._assert_stage_live(stage)
        rrv_propose._assert_directory_chain(parents, "local output parent")
        if not rrv_propose._stage_tree_is_safe(stage, stage.path):
            raise _tool_error("browser handoff staging directory changed before publication")
        if not rrv_propose._target_entry_is_absent(target):
            raise rrv_runtime.RRVError(rrv_runtime.ERR_OUTPUT_EXISTS, "refusing to overwrite an existing output")
        _assert_exact_handoff_tree(stage, expected_hashes)
        # This is intentionally adjacent to the rename.  A mutation after the
        # earlier source/output checks is rejected here, before publication.
        rrv_propose._assert_stage_live(stage)
        rrv_propose._assert_directory_chain(parents, "local output parent")
        if not rrv_propose._stage_tree_is_safe(stage, stage.path):
            raise _tool_error("browser handoff staging directory changed before publication")
        if not rrv_propose._target_entry_is_absent(target):
            raise rrv_runtime.RRVError(rrv_runtime.ERR_OUTPUT_EXISTS, "refusing to overwrite an existing output")
        _assert_exact_handoff_tree(stage, expected_hashes)
        rrv_propose._rename_bound_stage(stage, target, label="Higgsfield Web Handoff")
        rrv_propose._assert_directory_chain(parents, "local output parent")
        moved = rrv_propose._capture_directory_identity(target, "published browser handoff")
        if moved.device != stage.directory.device or moved.inode != stage.directory.inode:
            raise _tool_error("published browser handoff changed during atomic publication")
        _assert_exact_published_handoff_tree(target, expected_hashes)
        rrv_propose._release_stage_guards(stage)
    except rrv_runtime.RRVError:
        rrv_propose._rollback_publish(stage, target, parents)
        raise
    except FileExistsError as exc:
        rrv_propose._rollback_publish(stage, target, parents)
        raise rrv_runtime.RRVError(rrv_runtime.ERR_OUTPUT_EXISTS, "refusing to overwrite an existing output") from exc
    except OSError as exc:
        rrv_propose._rollback_publish(stage, target, parents)
        raise _tool_error("could not publish atomic browser handoff output") from exc


@contextmanager
def _nested_pack_guard(root: Path, root_identity: Any, relative_path: Any, *, label: str) -> Iterator[Path]:
    """Guard a normalized nested directory without relaxing root containment."""

    parts = rrv_assets._relative_path_parts(relative_path)
    if parts is None or not parts:
        raise _invalid(f"{label} path is invalid")
    # The final artificial leaf causes the private helper to guard every real
    # component, including the pack directory, while never opening that leaf.
    with rrv_assets._guard_project_parent_chain(root, root_identity, (*parts, "_rrv_guard"), label=label) as directory:
        yield directory


def _verify_upload_pack(root: Path, root_identity: Any, plan: Mapping[str, Any]) -> dict[str, bytes]:
    """Read the only two upload files through bound descriptors and exact names."""

    upload_path = plan.get("upload_pack")
    inventory = plan.get("upload_inventory")
    if not isinstance(upload_path, str) or not isinstance(inventory, list):
        raise _invalid("browser handoff upload pack is invalid")
    expected_by_name: dict[str, Mapping[str, Any]] = {}
    for item in inventory:
        if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
            raise _invalid("browser handoff upload inventory is invalid")
        name = Path(item["path"]).name
        if name in expected_by_name:
            raise _invalid("browser handoff upload inventory is invalid")
        expected_by_name[name] = item
    if set(expected_by_name) != {CHARACTER_FILENAME, MOTION_FILENAME}:
        raise _invalid("browser handoff upload inventory is invalid")
    result: dict[str, bytes] = {}
    with _nested_pack_guard(root, root_identity, upload_path, label="browser handoff upload pack") as pack:
        try:
            entries = list(os.scandir(pack))
        except OSError as exc:
            raise _invalid("browser handoff upload pack could not be scanned") from exc
        if {entry.name for entry in entries} != set(expected_by_name) or len(entries) != 2:
            raise _invalid("browser handoff upload pack must contain exactly two approved files")
        for name in (CHARACTER_FILENAME, MOTION_FILENAME):
            identity = rrv_assets._safe_regular_file(pack / name, message="browser handoff upload file is unsafe")
            if not 1 <= identity.size_bytes <= rrv_assets.MAX_FILE_BYTES:
                raise _invalid("browser handoff upload file exceeds local limits")
            raw = rrv_assets._read_bound_bytes(identity, maximum_bytes=rrv_assets.MAX_FILE_BYTES, message="browser handoff upload file changed while reading")
            item = expected_by_name[name]
            if len(raw) != item.get("size_bytes") or hashlib.sha256(raw).hexdigest() != item.get("sha256"):
                raise _invalid("browser handoff upload file does not match the approved plan")
            result[name] = raw
        try:
            after_names = {entry.name for entry in os.scandir(pack)}
        except OSError as exc:
            raise _invalid("browser handoff upload pack changed while reading") from exc
        if after_names != set(expected_by_name):
            raise _invalid("browser handoff upload pack changed while reading")
        rrv_assets._assert_root_live(root_identity)
    return result


def _assert_plan_matches_context(plan: Mapping[str, Any], plan_snapshot: Any, context: _HandoffContext) -> None:
    """Bind a generated web plan to current source packets without trusting it."""

    expected = context.temporal_plan
    plan_parts = rrv_assets._relative_path_parts(plan_snapshot.relative_path)
    expected_upload_pack = f"{plan_parts[0]}/{UPLOAD_PACK_DIRECTORY}" if plan_parts is not None and len(plan_parts) == 2 and plan_parts[1] == PLAN_FILENAME else None
    if (
        expected_upload_pack is None
        or plan.get("upload_pack") != expected_upload_pack
        or plan.get("temporal_plan_path") != context.temporal_plan_snapshot.relative_path
        or plan.get("temporal_plan_sha256") != context.temporal_plan_snapshot.sha256
        or plan.get("temporal_plan_review_path") != context.temporal_review_snapshot.relative_path
        or plan.get("temporal_plan_review_sha256") != context.temporal_review_snapshot.sha256
        or plan.get("handoff_request_path") != context.web_request_snapshot.relative_path
        or plan.get("handoff_request_sha256") != context.web_request_snapshot.sha256
        or plan.get("template_id") != expected.get("template_id")
        or plan.get("template_path") != expected.get("template_path")
        or plan.get("template_sha256") != expected.get("template_sha256")
        or plan.get("manifest_path") != expected.get("manifest_path")
        or plan.get("manifest_sha256") != expected.get("manifest_sha256")
        or plan.get("reference_pack") != expected.get("reference_pack")
        or plan.get("reference_inventory_sha256") != expected.get("reference_inventory_sha256")
        or plan.get("output") != context.output
    ):
        raise _invalid("browser handoff plan does not bind its approved temporal packets")
    limited = {
        "motion_mode": context.temporal_requirements.get("motion_mode"),
        "audio_mode": context.temporal_requirements.get("audio_mode"),
        "lip_sync_required": False,
        "voice_authorization_required": False,
        "clone_authorized_voice_supported": False,
    }
    if plan.get("requirements") != limited:
        raise _invalid("browser handoff plan requirements are not limited to the approved web route")
    character_auth, motion_auth = _assert_request_matches_context(context)
    reauthorization = plan.get("cloud_reauthorization")
    if not isinstance(reauthorization, Mapping) or (
        reauthorization.get("character_authorization_sha256") != _canonical_sha256(character_auth)
        or reauthorization.get("motion_authorization_sha256") != _canonical_sha256(motion_auth)
    ):
        raise _invalid("browser handoff plan lacks the exact new cloud reauthorizations")
    del plan_snapshot  # The caller has already bound the plan snapshot hash.


def _load_web_plan_context(root: Path, root_identity: Any, plan_path: str | os.PathLike[str]) -> tuple[Mapping[str, Any], Any, _HandoffContext]:
    plan_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, plan_path, label="Higgsfield Web Handoff Plan")
    web_plan = plan_snapshot.data
    _raise_validation("Higgsfield Web Handoff Plan", validate_higgsfield_web_handoff_plan_data(web_plan))
    if not isinstance(web_plan, Mapping):
        raise _invalid("Higgsfield Web Handoff Plan is invalid")
    context = _load_temporal_context(
        root,
        root_identity,
        web_plan.get("temporal_plan_path"),
        web_plan.get("temporal_plan_review_path"),
        web_request_path=web_plan.get("handoff_request_path"),
    )
    if web_plan.get("handoff_request_sha256") != context.web_request_snapshot.sha256:
        raise _invalid("browser handoff request changed since plan creation")
    _assert_plan_matches_context(web_plan, plan_snapshot, context)
    return web_plan, plan_snapshot, context


def _reference_snapshot_from_plan(
    root: Path,
    root_identity: Any,
    plan: Mapping[str, Any],
    context: _HandoffContext,
    reference_pack: str | os.PathLike[str],
    *,
    stage: Any,
    ffmpeg: str | os.PathLike[str],
    ffprobe: str | os.PathLike[str],
    timeout_seconds: float,
) -> tuple[dict[str, Any], Path]:
    reference_name = rrv_assets._direct_child_name(reference_pack, "reference_pack")
    if not rrv_temporal._same_direct_child(reference_name, plan.get("reference_pack")):
        raise _invalid("browser handoff reference pack does not match the approved temporal plan")
    with rrv_assets._asset_pack_guard(root, root_identity, reference_name) as (directory, identity):
        asset, snapshot = rrv_temporal._scan_one_video_pack(
            root, root_identity, directory, identity, reference_name, stage=stage,
            snapshot_name=".reference-action.mp4", required_filename=None, asset_id="action-reference.0001",
        )
        facts = rrv_temporal._inspect_staged_media(stage, snapshot, ffprobe=ffprobe, timeout_seconds=timeout_seconds, role="browser handoff action reference")
        rrv_temporal._require_media_matches(
            facts,
            width=int(context.source_spec["width"]), height=int(context.source_spec["height"]), fps=float(context.source_spec["fps"]), frame_count=int(context.source_spec["frame_count"]),
            role="browser handoff action reference",
        )
        rrv_temporal._require_reference_audio_for_mode(facts, context.temporal_requirements)
        rrv_temporal._full_decode(stage, snapshot, facts, ffmpeg=ffmpeg, timeout_seconds=timeout_seconds)
        inventory = rrv_temporal._opaque_inventory(asset, facts)
        rrv_temporal._validate_plan_reference_binding(context.temporal_plan, inventory)
        rrv_assets._assert_pack_live(root_identity, identity)
        return facts, snapshot


def prepare_higgsfield_web_handoff(
    temporal_plan: str | os.PathLike[str],
    temporal_plan_review: str | os.PathLike[str],
    handoff_request: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str],
    reference_pack: str | os.PathLike[str],
    web_handoff_rights_confirmed: bool,
    output_dir: str | os.PathLike[str] = "higgsfield-web-handoff",
    ffmpeg: str | os.PathLike[str] = "ffmpeg",
    ffprobe: str | os.PathLike[str] = "ffprobe",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Mapping[str, Any]:
    """Create one exact two-file upload pack; never contact a browser/provider."""

    if web_handoff_rights_confirmed is not True:
        raise _invalid("web_handoff_rights_confirmed must be explicitly true before browser handoff preparation")
    root = rrv_assets._safe_project_root(project_root)
    timeout = rrv_assets._parse_timeout(timeout_seconds)
    stage: Any = None
    try:
        with rrv_assets._root_guard(root) as root_identity:
            target = rrv_assets._direct_output_target(root, output_dir)
            context = _load_temporal_context(root, root_identity, temporal_plan, temporal_plan_review, web_request_path=handoff_request)
            character_auth, motion_auth = _assert_request_matches_context(context)
            _assert_current_reauthorizations(context.web_request)
            character_source = _manifest_source_for_slot(context.manifest, context.web_request.get("character_slot_id"))
            source_path = character_source.get("path")
            source_sha = character_source.get("sha256")
            if not isinstance(source_path, str) or not isinstance(source_sha, str):
                raise _invalid("browser handoff character source is invalid")
            _relative, character_raw = rrv_assets._read_project_file_bytes(
                root, root_identity, source_path, label="browser handoff character source", maximum_bytes=rrv_assets.MAX_FILE_BYTES
            )
            if hashlib.sha256(character_raw).hexdigest() != source_sha.lower():
                raise _invalid("browser handoff character source changed since the frozen Manifest")
            if character_auth.get("source_sha256") != hashlib.sha256(character_raw).hexdigest():
                raise _invalid("browser handoff character reauthorization does not bind current bytes")
            stage = rrv_propose._new_staging_directory(root, "higgsfield-web-handoff")
            _reference_facts, reference_snapshot = _reference_snapshot_from_plan(
                root, root_identity, context.temporal_plan, context, reference_pack, stage=stage, ffmpeg=ffmpeg, ffprobe=ffprobe, timeout_seconds=timeout
            )
            if motion_auth.get("source_sha256") != rrv_temporal._stage_media_sha256(stage, reference_snapshot, label="browser handoff action snapshot"):
                raise _invalid("browser handoff motion reauthorization does not bind current bytes")
            character_output = rrv_propose._stage_path(root, stage, f"{UPLOAD_PACK_DIRECTORY}/{CHARACTER_FILENAME}")
            _write_sanitized_character_png(character_raw, stage=stage, destination=character_output)
            motion_output = rrv_propose._stage_path(root, stage, f"{UPLOAD_PACK_DIRECTORY}/{MOTION_FILENAME}")
            _write_silent_motion_upload(
                stage, reference_snapshot, motion_output, ffmpeg=ffmpeg, ffprobe=ffprobe, timeout_seconds=timeout, source_spec=context.source_spec
            )
            character_sha = _stage_hash(stage, character_output, label="sanitized browser character image")
            motion_sha = _stage_hash(stage, motion_output, label="silent browser motion upload")
            upload_pack = rrv_propose._lexical_relative_output_path(root, target / UPLOAD_PACK_DIRECTORY)
            upload_inventory = [
                {"role": "character-image", "path": f"{upload_pack}/{CHARACTER_FILENAME}", "sha256": character_sha, "media_type": "image/png", "size_bytes": rrv_propose._stage_regular_file_size(stage, character_output, "sanitized browser character image")},
                {"role": "motion-video", "path": f"{upload_pack}/{MOTION_FILENAME}", "sha256": motion_sha, "media_type": "video/mp4", "size_bytes": rrv_propose._stage_regular_file_size(stage, motion_output, "silent browser motion upload")},
            ]
            requirements = {
                "motion_mode": context.temporal_requirements["motion_mode"], "audio_mode": context.temporal_requirements["audio_mode"],
                "lip_sync_required": False, "voice_authorization_required": False, "clone_authorized_voice_supported": False,
            }
            plan_data: dict[str, Any] = {
                "schema_version": SCHEMA_VERSION, "provider_id": PROVIDER_ID, "surface": SURFACE, "model": MODEL, "resolution": RESOLUTION,
                "temporal_plan_path": context.temporal_plan_snapshot.relative_path, "temporal_plan_sha256": context.temporal_plan_snapshot.sha256,
                "temporal_plan_review_path": context.temporal_review_snapshot.relative_path, "temporal_plan_review_sha256": context.temporal_review_snapshot.sha256,
                "handoff_request_path": context.web_request_snapshot.relative_path, "handoff_request_sha256": context.web_request_snapshot.sha256,
                "template_id": context.temporal_plan["template_id"], "template_path": context.temporal_plan["template_path"], "template_sha256": context.temporal_plan["template_sha256"],
                "manifest_path": context.temporal_plan["manifest_path"], "manifest_sha256": context.temporal_plan["manifest_sha256"],
                "reference_pack": context.temporal_plan["reference_pack"], "reference_inventory_sha256": context.temporal_plan["reference_inventory_sha256"],
                "output": dict(context.output), "requirements": requirements,
                "cloud_reauthorization": {
                    "scope": "single-user-operated-higgsfield-upload", "supersedes_manifest_local_only_for_this_upload_only": True,
                    "character_authorization_sha256": _canonical_sha256(character_auth), "motion_authorization_sha256": _canonical_sha256(motion_auth),
                },
                "upload_pack": upload_pack, "upload_inventory": upload_inventory, "upload_inventory_sha256": _canonical_sha256(upload_inventory),
                "provider_provenance": PROVIDER_PROVENANCE,
            }
            _raise_validation("generated Higgsfield Web Handoff Plan", validate_higgsfield_web_handoff_plan_data(plan_data))
            plan_output = rrv_propose._stage_path(root, stage, PLAN_FILENAME)
            rrv_assets._write_json(stage, root, plan_output, plan_data, "Higgsfield Web Handoff Plan JSON")
            plan_sha = rrv_propose._stage_file_sha256(stage, plan_output)
            rrv_propose._remove_stage_file(stage, reference_snapshot)
            expected_hashes = {PLAN_FILENAME: plan_sha, f"{UPLOAD_PACK_DIRECTORY}/{CHARACTER_FILENAME}": character_sha, f"{UPLOAD_PACK_DIRECTORY}/{MOTION_FILENAME}": motion_sha}
            _publish_exact_handoff_stage(root, stage, target, expected_hashes=expected_hashes)
            stage = None
            return {
                "schema_version": SCHEMA_VERSION, "operation": "prepare-higgsfield-web-handoff", "review_required": True,
                "provider_provenance": PROVIDER_PROVENANCE, "counts": {"upload_assets": 2},
                "artifacts": {"handoff_plan": {"path": f"{target.name}/{PLAN_FILENAME}", "sha256": plan_sha}, "upload_pack": {"path": upload_pack, "upload_inventory_sha256": _canonical_sha256(upload_inventory)}},
            }
    except BaseException as exc:
        rrv_propose._cleanup_directory(root, stage)
        raise _safe_exception(exc) from None


def record_higgsfield_web_action(
    handoff_plan: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str],
    max_credits: int,
    observed_cost_credits: int,
    available_credits_before: int,
    cloud_upload_confirmed: bool,
    billable_action_confirmed: bool,
    output_dir: str | os.PathLike[str] = "higgsfield-web-browser-receipt",
) -> Mapping[str, Any]:
    """Write an unattested local action card before the user manually clicks Generate."""

    if cloud_upload_confirmed is not True or billable_action_confirmed is not True:
        raise _invalid("cloud_upload_confirmed and billable_action_confirmed must both be explicitly true")
    if not all(_is_int(value) for value in (max_credits, observed_cost_credits, available_credits_before)):
        raise _invalid("browser credit values must be integers")
    root = rrv_assets._safe_project_root(project_root)
    stage: Any = None
    try:
        with rrv_assets._root_guard(root) as root_identity:
            target = rrv_assets._direct_output_target(root, output_dir)
            plan, plan_snapshot, context = _load_web_plan_context(root, root_identity, handoff_plan)
            _assert_current_reauthorizations(context.web_request)
            _verify_upload_pack(root, root_identity, plan)
            request_max = context.web_request.get("max_credits")
            if max_credits != request_max or not 1 <= max_credits <= MAX_CREDITS or not 1 <= observed_cost_credits <= max_credits or not 0 <= available_credits_before <= MAX_BALANCE or available_credits_before < observed_cost_credits:
                raise _invalid("browser displayed credit cost exceeds the reviewed cap or available balance")
            _consume_handoff_request_action_once(root, plan_snapshot, context)
            receipt: dict[str, Any] = {
                "schema_version": SCHEMA_VERSION, "event": "browser-action-confirmed",
                "plan_path": plan_snapshot.relative_path, "plan_sha256": plan_snapshot.sha256,
                "handoff_request_path": context.web_request_snapshot.relative_path, "handoff_request_sha256": context.web_request_snapshot.sha256,
                "upload_inventory_sha256": plan["upload_inventory_sha256"],
                "provider_id": PROVIDER_ID, "surface": SURFACE, "model": MODEL, "resolution": RESOLUTION,
                "motion_mode": plan["requirements"]["motion_mode"], "audio_mode": plan["requirements"]["audio_mode"],
                "max_credits": max_credits, "observed_cost_credits": observed_cost_credits, "available_credits_before": available_credits_before,
                "projected_remaining_credits_after": available_credits_before - observed_cost_credits,
                "cloud_upload_confirmed": True, "billable_action_confirmed": True, "new_cloud_reauthorization_confirmed": True,
                "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "browser_submission_attested": False, "provider_provenance": PROVIDER_PROVENANCE,
            }
            _raise_validation("generated Higgsfield Web Browser Receipt", validate_higgsfield_web_browser_receipt_data(receipt))
            stage = rrv_propose._new_staging_directory(root, "higgsfield-web-receipt")
            receipt_path = rrv_propose._stage_path(root, stage, RECEIPT_FILENAME)
            rrv_assets._write_json(stage, root, receipt_path, receipt, "Higgsfield Web Browser Receipt JSON")
            digest = rrv_propose._stage_file_sha256(stage, receipt_path)
            rrv_temporal._publish_exact_temporal_stage(root, stage, target, label="Higgsfield Web Browser Receipt", expected_files={RECEIPT_FILENAME: digest})
            stage = None
            return {
                "schema_version": SCHEMA_VERSION, "operation": "record-higgsfield-web-action", "provider_provenance": PROVIDER_PROVENANCE,
                "browser_submission_attested": False, "projected_remaining_credits_after": receipt["projected_remaining_credits_after"],
                "artifacts": {"browser_receipt": {"path": f"{target.name}/{RECEIPT_FILENAME}", "sha256": digest}},
            }
    except BaseException as exc:
        rrv_propose._cleanup_directory(root, stage)
        raise _safe_exception(exc) from None


def _assert_receipt_matches_plan(receipt: Mapping[str, Any], receipt_snapshot: Any, plan: Mapping[str, Any], plan_snapshot: Any, context: _HandoffContext) -> None:
    if (
        receipt.get("plan_path") != plan_snapshot.relative_path
        or receipt.get("plan_sha256") != plan_snapshot.sha256
        or receipt.get("handoff_request_path") != context.web_request_snapshot.relative_path
        or receipt.get("handoff_request_sha256") != context.web_request_snapshot.sha256
        or receipt.get("upload_inventory_sha256") != plan.get("upload_inventory_sha256")
        or receipt.get("motion_mode") != plan.get("requirements", {}).get("motion_mode")
        or receipt.get("audio_mode") != plan.get("requirements", {}).get("audio_mode")
        or receipt.get("max_credits") != context.web_request.get("max_credits")
        or receipt.get("cloud_upload_confirmed") is not True
        or receipt.get("billable_action_confirmed") is not True
        or receipt.get("new_cloud_reauthorization_confirmed") is not True
        or receipt.get("browser_submission_attested") is not False
        or receipt.get("provider_provenance") != PROVIDER_PROVENANCE
    ):
        raise _invalid("browser receipt does not bind the approved web handoff plan")
    del receipt_snapshot


def _receipt_consumption_output_name(receipt_sha256: Any) -> str:
    """Return the one private, direct-child state target for a receipt hash."""

    if not _valid_sha256(receipt_sha256):
        raise _tool_error("browser receipt snapshot digest is invalid")
    return f"{RECEIPT_CONSUMPTION_PREFIX}{receipt_sha256}"


def _handoff_request_action_consumption_output_name(request_sha256: Any) -> str:
    """Return the private, direct-child action state target for one Request."""

    if not _valid_sha256(request_sha256):
        raise _tool_error("browser handoff request snapshot digest is invalid")
    return f"{ACTION_CONSUMPTION_PREFIX}{request_sha256}"


def _consume_handoff_request_action_once(
    root: Path,
    plan_snapshot: Any,
    context: _HandoffContext,
) -> None:
    """Atomically make one single-upload Request terminal before receipt work.

    The marker key is the Handoff Request digest rather than the Plan digest:
    multiple local preparations can derive separate packs from a Request, but
    that Request still authorizes only one user-operated upload/action.  The
    state stays terminal after any later local failure to fail closed across a
    crash or ambiguous receipt publication.
    """

    plan_sha256 = getattr(plan_snapshot, "sha256", None)
    request_sha256 = getattr(context.web_request_snapshot, "sha256", None)
    if not all(_valid_sha256(value) for value in (plan_sha256, request_sha256)):
        raise _tool_error("browser handoff action binding is invalid")
    target = rrv_assets._direct_output_target(
        root, _handoff_request_action_consumption_output_name(request_sha256)
    )
    stage: Any = None
    try:
        stage = rrv_propose._new_staging_directory(root, "higgsfield-web-action-use")
        payload = {
            "schema_version": SCHEMA_VERSION,
            "event": "browser-handoff-request-action-consumed",
            "handoff_request_sha256": request_sha256,
            "handoff_plan_sha256": plan_sha256,
            "consumed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        marker = rrv_propose._stage_path(root, stage, ACTION_CONSUMPTION_FILENAME)
        rrv_assets._write_json(stage, root, marker, payload, "browser action consumption marker")
        marker_sha256 = rrv_propose._stage_file_sha256(stage, marker)
        rrv_temporal._publish_exact_temporal_stage(
            root,
            stage,
            target,
            label="Higgsfield Web Handoff Action Consumption",
            expected_files={ACTION_CONSUMPTION_FILENAME: marker_sha256},
        )
        stage = None
    except BaseException:
        rrv_propose._cleanup_directory(root, stage)
        raise


def _consume_browser_receipt_once(
    root: Path,
    receipt_snapshot: Any,
    plan_snapshot: Any,
    context: _HandoffContext,
) -> None:
    """Atomically make one pre-submit receipt terminal before result work.

    The private no-replace marker is intentionally written before any download
    processing.  A crash, local media failure, or suspicious filesystem change
    therefore leaves the receipt consumed rather than creating an ambiguous
    second normalization attempt.  A new action-time receipt is required to
    make another attempt.
    """

    receipt_sha256 = getattr(receipt_snapshot, "sha256", None)
    plan_sha256 = getattr(plan_snapshot, "sha256", None)
    request_sha256 = getattr(context.web_request_snapshot, "sha256", None)
    if not all(_valid_sha256(value) for value in (receipt_sha256, plan_sha256, request_sha256)):
        raise _tool_error("browser handoff receipt binding is invalid")
    target = rrv_assets._direct_output_target(
        root, _receipt_consumption_output_name(receipt_sha256)
    )
    stage: Any = None
    try:
        stage = rrv_propose._new_staging_directory(root, "higgsfield-web-receipt-use")
        payload = {
            "schema_version": SCHEMA_VERSION,
            "event": "browser-receipt-consumed",
            "receipt_sha256": receipt_sha256,
            "handoff_plan_sha256": plan_sha256,
            "handoff_request_sha256": request_sha256,
            "consumed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        marker = rrv_propose._stage_path(root, stage, RECEIPT_CONSUMPTION_FILENAME)
        rrv_assets._write_json(stage, root, marker, payload, "browser receipt consumption marker")
        marker_sha256 = rrv_propose._stage_file_sha256(stage, marker)
        rrv_temporal._publish_exact_temporal_stage(
            root,
            stage,
            target,
            label="Higgsfield Web Browser Receipt Consumption",
            expected_files={RECEIPT_CONSUMPTION_FILENAME: marker_sha256},
        )
        stage = None
    except BaseException:
        rrv_propose._cleanup_directory(root, stage)
        raise


def _inspect_downloaded_video(stage: Any, source: Path, *, ffmpeg: str | os.PathLike[str], ffprobe: str | os.PathLike[str], timeout_seconds: float) -> tuple[int, float, bool]:
    """Accept only one locally decodable CFR video; its metadata is discarded."""

    try:
        with rrv_temporal._hold_staged_media(stage, source, label="browser downloaded video snapshot"):
            raw = rrv_nle._full_ffprobe_facts(source, os.fspath(ffprobe), timeout_seconds=timeout_seconds)
            timing = rrv_nle._exact_timing(source, os.fspath(ffprobe), timeout_seconds=timeout_seconds)
    except rrv_runtime.RRVError:
        raise
    except Exception as exc:
        raise _tool_error("browser downloaded video inspection failed") from exc
    streams = raw.get("streams") if isinstance(raw, Mapping) else None
    chapters = raw.get("chapters") if isinstance(raw, Mapping) else None
    if not isinstance(streams, list) or (chapters is not None and chapters != []):
        raise _invalid("browser downloaded video has unsupported stream structure")
    videos = [stream for stream in streams if isinstance(stream, Mapping) and stream.get("codec_type") == "video"]
    audios = [stream for stream in streams if isinstance(stream, Mapping) and stream.get("codec_type") == "audio"]
    if len(videos) != 1 or len(audios) > 1 or len(videos) + len(audios) != len(streams):
        raise _invalid("browser downloaded video must contain one video and at most one audio stream")
    frame_count = timing.get("frame_count") if isinstance(timing, Mapping) else None
    fps = timing.get("fps") if isinstance(timing, Mapping) else None
    if not _is_int(frame_count) or not _is_number(fps) or int(frame_count) < 1 or float(fps) <= 0 or timing.get("cfr_confirmed") is not True:
        raise _invalid("browser downloaded video does not have exact CFR timing")
    try:
        with rrv_temporal._hold_staged_media(stage, source, label="browser downloaded video snapshot"):
            rrv_nle._full_decode_qa(source, os.fspath(ffmpeg), timeout_seconds=timeout_seconds, expected_frames=int(frame_count), has_audio=bool(audios))
    except rrv_runtime.RRVError:
        raise
    except Exception as exc:
        raise _tool_error("browser downloaded video could not be fully decoded") from exc
    return int(frame_count), float(fps), bool(audios)


def _normalize_download(
    stage: Any,
    downloaded: Path,
    reference: Path,
    destination: Path,
    *,
    requirements: Mapping[str, Any],
    output: Mapping[str, Any],
    source_spec: Mapping[str, Any],
    ffmpeg: str | os.PathLike[str],
    timeout_seconds: float,
) -> None:
    """Re-encode pixels only; timing is prechecked and exact source audio is copied."""

    fps = float(source_spec["fps"])
    command: list[str] = [
        os.fspath(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-xerror", "-i", str(downloaded),
    ]
    preserve = requirements.get("audio_mode") == "preserve-reference"
    if preserve:
        command.extend(["-i", str(reference)])
    command.extend([
        "-map", "0:v:0", "-map_metadata", "-1", "-map_chapters", "-1", "-sn", "-dn",
        "-vf", f"scale={int(output['width'])}:{int(output['height'])}:flags=lanczos,setsar=1",
        "-r", format(fps, ".12g"), "-fps_mode", "cfr", "-frames:v", str(int(source_spec["frame_count"])),
        "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "18",
    ])
    if preserve:
        command.extend(["-map", "1:a:0", "-c:a", "copy"])
    else:
        command.append("-an")
    command.extend(["-movflags", "+faststart", "-n", str(destination)])
    with ExitStack() as stack:
        stack.enter_context(rrv_temporal._hold_staged_media(stage, downloaded, label="browser downloaded video snapshot"))
        stack.enter_context(rrv_temporal._hold_staged_media(stage, reference, label="browser action reference snapshot"))
        _run_ffmpeg(command, timeout_seconds=timeout_seconds)


def normalize_higgsfield_download(
    handoff_plan: str | os.PathLike[str],
    browser_receipt: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str],
    downloaded_pack: str | os.PathLike[str],
    reference_pack: str | os.PathLike[str],
    downloaded_result_rights_confirmed: bool,
    output_result_pack: str | os.PathLike[str] = "higgsfield-temporal-result",
    ffmpeg: str | os.PathLike[str] = "ffmpeg",
    ffprobe: str | os.PathLike[str] = "ffprobe",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Mapping[str, Any]:
    """Normalize one manual download into the exact one-file v0.10 result pack."""

    if downloaded_result_rights_confirmed is not True:
        raise _invalid("downloaded_result_rights_confirmed must be explicitly true before result normalization")
    root = rrv_assets._safe_project_root(project_root)
    timeout = rrv_assets._parse_timeout(timeout_seconds)
    downloaded_name = rrv_assets._direct_child_name(downloaded_pack, "downloaded_pack")
    stage: Any = None
    try:
        with rrv_assets._root_guard(root) as root_identity:
            target = rrv_assets._direct_output_target(root, output_result_pack)
            plan, plan_snapshot, context = _load_web_plan_context(root, root_identity, handoff_plan)
            receipt_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, browser_receipt, label="Higgsfield Web Browser Receipt")
            receipt = receipt_snapshot.data
            _raise_validation("Higgsfield Web Browser Receipt", validate_higgsfield_web_browser_receipt_data(receipt))
            if not isinstance(receipt, Mapping):
                raise _invalid("Higgsfield Web Browser Receipt is invalid")
            _assert_receipt_matches_plan(receipt, receipt_snapshot, plan, plan_snapshot, context)
            _consume_browser_receipt_once(root, receipt_snapshot, plan_snapshot, context)
            _verify_upload_pack(root, root_identity, plan)
            stage = rrv_propose._new_staging_directory(root, "higgsfield-web-normalize")
            _reference_facts, reference_snapshot = _reference_snapshot_from_plan(
                root, root_identity, context.temporal_plan, context, reference_pack, stage=stage, ffmpeg=ffmpeg, ffprobe=ffprobe, timeout_seconds=timeout
            )
            with rrv_assets._asset_pack_guard(root, root_identity, downloaded_name) as (directory, identity):
                _download_asset, downloaded_snapshot = rrv_temporal._scan_one_video_pack(
                    root, root_identity, directory, identity, downloaded_name, stage=stage,
                    snapshot_name=".browser-downloaded.mp4", required_filename=None, asset_id="browser-download.0001",
                )
                frames, fps, _has_audio = _inspect_downloaded_video(stage, downloaded_snapshot, ffmpeg=ffmpeg, ffprobe=ffprobe, timeout_seconds=timeout)
                if frames != int(context.source_spec["frame_count"]) or not math.isclose(fps, float(context.source_spec["fps"]), rel_tol=1e-9, abs_tol=1e-9):
                    raise _invalid("browser downloaded video timing does not exactly match the approved source")
                rrv_assets._assert_pack_live(root_identity, identity)
            result_path = rrv_propose._stage_path(root, stage, RESULT_FILENAME)
            _normalize_download(
                stage, downloaded_snapshot, reference_snapshot, result_path, requirements=plan["requirements"], output=context.output,
                source_spec=context.source_spec, ffmpeg=ffmpeg, timeout_seconds=timeout,
            )
            rrv_propose._assert_stage_regular_file(stage, result_path, "normalized browser result")
            result_facts = rrv_temporal._inspect_staged_media(stage, result_path, ffprobe=ffprobe, timeout_seconds=timeout, role="normalized browser result")
            expected_audio = 1 if plan["requirements"].get("audio_mode") == "preserve-reference" else 0
            rrv_temporal._require_media_matches(
                result_facts,
                width=int(context.output["width"]), height=int(context.output["height"]), fps=float(context.source_spec["fps"]), frame_count=int(context.source_spec["frame_count"]),
                expected_audio_streams=expected_audio, role="normalized browser result",
            )
            rrv_temporal._reject_result_metadata(stage, result_path, ffprobe=ffprobe, timeout_seconds=timeout)
            rrv_temporal._full_decode(stage, result_path, result_facts, ffmpeg=ffmpeg, timeout_seconds=timeout)
            if expected_audio == 1:
                if rrv_temporal._audio_payload_hash(stage, reference_snapshot, ffprobe=ffprobe, timeout_seconds=timeout) != rrv_temporal._audio_payload_hash(stage, result_path, ffprobe=ffprobe, timeout_seconds=timeout):
                    raise _invalid("normalized browser result does not preserve the exact approved audio payload")
            digest = _stage_hash(stage, result_path, label="normalized browser result")
            rrv_propose._remove_stage_file(stage, reference_snapshot)
            rrv_propose._remove_stage_file(stage, downloaded_snapshot)
            rrv_temporal._publish_exact_temporal_stage(root, stage, target, label="Normalized Higgsfield Result Pack", expected_files={RESULT_FILENAME: digest})
            stage = None
            return {
                "schema_version": SCHEMA_VERSION, "operation": "normalize-higgsfield-download", "provider_provenance": PROVIDER_PROVENANCE,
                "browser_submission_attested": False, "counts": {"result_assets": 1},
                "artifacts": {"temporal_result": {"path": f"{target.name}/{RESULT_FILENAME}", "sha256": digest}},
            }
    except BaseException as exc:
        rrv_propose._cleanup_directory(root, stage)
        raise _safe_exception(exc) from None


__all__ = [
    "SCHEMA_VERSION", "PROVIDER_ID", "SURFACE", "MODEL", "RESOLUTION", "PROVIDER_PROVENANCE",
    "validate_higgsfield_web_handoff_request_data", "validate_higgsfield_web_handoff_plan_data", "validate_higgsfield_web_browser_receipt_data",
    "prepare_higgsfield_web_handoff", "record_higgsfield_web_action", "normalize_higgsfield_download",
]
