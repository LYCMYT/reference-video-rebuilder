#!/usr/bin/env python3
"""Local-only proposal and explicit-freeze workflow for S1 references.

This module deliberately stops before interpretation: it measures only pixels,
frame timing, and simple geometry.  It produces a candidate Compiler Plan that
must be explicitly reviewed before :func:`freeze_plan` can publish it.

The public JSON documents are intentionally small.  In particular, neither
document contains source filenames, source paths, raw probe data, tool paths,
or a full per-frame analysis dump.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Any, Iterable, Mapping, Sequence

if os.name == "nt":  # pragma: win32-only -- guards use NT directory handles.
    import ctypes
    from ctypes import wintypes

try:  # Direct execution from the Skill's scripts directory.
    import rrv_analyze
    import rrv_compile
    import rrv_runtime
except ImportError:  # pragma: no cover - useful for package-style imports.
    from . import rrv_analyze, rrv_compile, rrv_runtime  # type: ignore[no-redef]


PROPOSAL_SCHEMA_VERSION = "0.4.0"
REVIEW_SCHEMA_VERSION = "0.4.0"
DEFAULT_TIMEOUT_SECONDS = 120.0

_SKILL_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_DIRECTORY = _SKILL_ROOT / "assets" / "schemas"
_PROPOSAL_SCHEMA_PATH = _SCHEMA_DIRECTORY / "compiler-plan-proposal.schema.json"
_REVIEW_SCHEMA_PATH = _SCHEMA_DIRECTORY / "review-decision.schema.json"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_OUTPUT_PROFILES = ("720x1280", "1080x1920")
_OUTPUT_PROFILE_RATIOS = {
    "720x1280": (9, 16),
    "1080x1920": (9, 16),
}
_CONFIRMATION_KEYS = (
    "family",
    "geometry",
    "slot_count",
    "timing",
    "carousel",
    "background",
    "audio",
    "authorization",
)
_MAX_PROPOSAL_JSON_BYTES = 4 * 1024 * 1024
_MAX_ANALYSIS_RAW_BYTES = 512 * 1024 * 1024
_MAX_EVIDENCE_ARTIFACT_BYTES = 128 * 1024 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400


@dataclass(frozen=True)
class _DirectoryIdentity:
    """The stable filesystem identity needed to reject replaced directories."""

    path: Path
    device: int
    inode: int


@dataclass
class _DirectoryGuard:
    """A native directory handle that denies concurrent delete/rename access."""

    path: Path
    handle: int | None
    closed: bool = False


@dataclass(frozen=True)
class _StageDirectory:
    """A private staging directory bound to its creation-time identities."""

    root: _DirectoryIdentity
    directory: _DirectoryIdentity
    root_guard: _DirectoryGuard
    directory_guard: _DirectoryGuard

    @property
    def path(self) -> Path:
        return self.directory.path


def _invalid(message: str, *, details: Mapping[str, Any] | None = None) -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(rrv_runtime.ERR_INVALID_ARGUMENT, message, details)


def _capability(message: str, *, details: Mapping[str, Any] | None = None) -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(rrv_runtime.ERR_CAPABILITY_UNAVAILABLE, message, details)


def _tool_error(message: str, *, details: Mapping[str, Any] | None = None) -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(rrv_runtime.ERR_TOOL_EXECUTION, message, details)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _bounded_float(value: float | int, *, lower: float = 0.0, upper: float = 1.0) -> float:
    """Clamp and round heuristic values before public JSON serialization."""

    numeric = float(value)
    if not math.isfinite(numeric):
        return lower
    return round(min(upper, max(lower, numeric)), 6)


def _stable_number(value: float | int) -> float | int:
    """Remove negative zero and platform-noise beyond useful frame precision."""

    numeric = float(value)
    if not math.isfinite(numeric):
        raise _invalid("numeric proposal values must be finite")
    numeric = round(numeric, 9)
    if numeric == 0:
        return 0
    return int(numeric) if numeric.is_integer() else numeric


def _strict_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value is not allowed: {value}")


def _schema_path(error: Any) -> str:
    path = "$"
    for item in error.absolute_path:
        path += f"[{item}]" if isinstance(item, int) else f".{item}"
    return path


def _find_nonfinite(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        errors.append(f"{path} must be finite (NaN and Infinity are not allowed)")
    elif isinstance(value, Mapping):
        for key, child in value.items():
            _find_nonfinite(child, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _find_nonfinite(child, f"{path}[{index}]", errors)


def _schema_errors(data: Any, schema_path: Path, contract_name: str) -> list[str]:
    """Return deterministic structural errors without accepting a fallback schema."""

    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return [f"{contract_name} JSON Schema validation requires jsonschema"]
    try:
        with schema_path.open("r", encoding="utf-8") as handle:
            schema = json.load(handle, parse_constant=_strict_json_constant)
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
    except (OSError, ValueError) as exc:
        del exc
        return [f"{contract_name} JSON Schema is unavailable"]
    errors = sorted(
        validator.iter_errors(data),
        key=lambda error: (tuple(str(item) for item in error.absolute_path), error.message),
    )
    return [f"{_schema_path(error)}: {error.message}" for error in errors]


def _compiler_plan_errors(plan: Any, prefix: str) -> list[str]:
    if not isinstance(plan, Mapping):
        return [f"{prefix} must be an object"]
    try:
        # This is the frozen 0.3.0 JSON Schema validator used by the compiler.
        rrv_compile._validate_compiler_plan_schema(plan)
    except rrv_runtime.RRVError as exc:
        nested = exc.details.get("errors") if isinstance(exc.details, Mapping) else None
        if isinstance(nested, list) and nested:
            return [f"{prefix}: {str(item)}" for item in nested[:16]]
        return [f"{prefix}: Compiler Plan validation failed"]
    except Exception:  # pragma: no cover - protects the public validator boundary.
        return [f"{prefix}: Compiler Plan validation failed"]
    return []


def _media_from_fingerprint(fingerprint: Mapping[str, Any]) -> Mapping[str, Any] | None:
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
        or not _is_finite_number(fps)
        or float(fps) <= 0
        or not isinstance(has_audio, bool)
    ):
        return None
    streams: list[dict[str, Any]] = [
        {
            "type": "video",
            "width": width,
            "height": height,
            "frame_count": frame_count,
            "frame_rate": float(fps),
            "average_frame_rate": float(fps),
            "exact_duration_seconds": frame_count / float(fps),
            "cfr_confirmed": True,
            "rotation_degrees": 0,
        }
    ]
    if has_audio:
        streams.append({"type": "audio"})
    return {
        "format": {"duration_seconds": frame_count / float(fps)},
        "streams": streams,
    }


def _semantic_compiler_plan_errors(
    plan: Any,
    fingerprint: Any,
    prefix: str,
) -> list[str]:
    """Run frozen compiler semantic checks with only public fingerprint facts."""

    if not isinstance(plan, Mapping) or not isinstance(fingerprint, Mapping):
        return []
    media = _media_from_fingerprint(fingerprint)
    if media is None:
        return []
    try:
        media_info = rrv_compile._media_info(media, require_exact_timing=True)
        rrv_compile._validate_plan(plan, media_info)
    except rrv_runtime.RRVError as exc:
        nested = exc.details.get("errors") if isinstance(exc.details, Mapping) else None
        if isinstance(nested, list) and nested:
            return [f"{prefix}: {str(item)}" for item in nested[:16]]
        return [f"{prefix}: {exc.message}"]
    except Exception:  # pragma: no cover - defensive public-validation boundary.
        return [f"{prefix}: Compiler Plan semantic validation failed"]
    return []


def _unique_errors(errors: Iterable[str]) -> list[str]:
    """Keep errors short, stable, and suitable for a compact local response."""

    ordered: list[str] = []
    seen: set[str] = set()
    for raw in errors:
        value = " ".join(str(raw).split())
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value[:512])
    return ordered


def validate_proposal_data(data: Any) -> list[str]:
    """Validate the strict proposal document and its nested Compiler Plan.

    The public function intentionally returns errors instead of raising so a
    reviewer/editor can display all concise structural problems at once.
    """

    errors: list[str] = []
    _find_nonfinite(data, "$", errors)
    errors.extend(_schema_errors(data, _PROPOSAL_SCHEMA_PATH, "Compiler Plan Proposal"))
    if isinstance(data, Mapping):
        candidate = data.get("candidate_plan")
        fingerprint = data.get("source_fingerprint")
        errors.extend(_compiler_plan_errors(candidate, "$.candidate_plan"))
        errors.extend(_semantic_compiler_plan_errors(candidate, fingerprint, "$.candidate_plan"))
    return _unique_errors(errors)


def validate_review_data(data: Any) -> list[str]:
    """Validate the strict review document and its nested Compiler Plan."""

    errors: list[str] = []
    _find_nonfinite(data, "$", errors)
    errors.extend(_schema_errors(data, _REVIEW_SCHEMA_PATH, "Review Decision"))
    if isinstance(data, Mapping):
        errors.extend(_compiler_plan_errors(data.get("approved_plan"), "$.approved_plan"))
    return _unique_errors(errors)


def _raise_validation_errors(label: str, errors: Sequence[str]) -> None:
    compact = [str(item)[:512] for item in errors[:8]]
    raise _invalid(f"{label} did not pass validation", details={"errors": compact})


def _positive_int(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if not _is_int(value) or not minimum <= value <= maximum:
        raise _invalid(f"{field} must be an integer from {minimum} to {maximum}")
    return value


def _normalize_output_profiles(value: Any) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _invalid("output_profiles must be a non-empty sequence of supported profiles")
    supplied = list(value)
    if not supplied or any(not isinstance(item, str) for item in supplied):
        raise _invalid("output_profiles must be a non-empty sequence of supported profiles")
    if len(supplied) != len(set(supplied)):
        raise _invalid("output_profiles must not contain duplicate profiles")
    if any(item not in _SUPPORTED_OUTPUT_PROFILES for item in supplied):
        raise _invalid("output_profiles supports only 720x1280 and 1080x1920")
    # Canonical ordering makes byte output independent of an equivalent input
    # tuple ordering without broadening the frozen profile set.
    profiles = tuple(item for item in _SUPPORTED_OUTPUT_PROFILES if item in set(supplied))
    ratios = {_OUTPUT_PROFILE_RATIOS[item] for item in profiles}
    if len(ratios) != 1:  # Defensive future-proofing if a profile is added later.
        raise _invalid("output_profiles must share one composition aspect ratio")
    return profiles


def _validate_proposal_arguments(
    *,
    template_id: Any,
    slot_count_hint: Any,
    audio_mode: Any,
    audio_rights_confirmed: Any,
    output_profiles: Any,
    analysis_width: Any,
    max_evidence_frames: Any,
) -> tuple[str, int | None, str, bool, tuple[str, ...], int, int]:
    if not isinstance(template_id, str) or not _ID_RE.fullmatch(template_id):
        raise _invalid("template_id must be a lowercase identifier of at most 64 characters")
    if slot_count_hint is None:
        hint = None
    else:
        hint = _positive_int(slot_count_hint, "slot_count_hint", minimum=1, maximum=64)
    if audio_mode not in {"preserve", "replaceable", "mute"}:
        raise _invalid("audio_mode must be preserve, replaceable, or mute")
    if not isinstance(audio_rights_confirmed, bool):
        raise _invalid("audio_rights_confirmed must be a boolean")
    profiles = _normalize_output_profiles(output_profiles)
    width = _positive_int(analysis_width, "analysis_width", minimum=32, maximum=256)
    evidence = _positive_int(max_evidence_frames, "max_evidence_frames", minimum=1, maximum=64)
    return template_id, hint, audio_mode, audio_rights_confirmed, profiles, width, evidence


def _require_tool_paths(tools: Any) -> tuple[str, str]:
    ffmpeg_info = getattr(tools, "ffmpeg", None)
    ffprobe_info = getattr(tools, "ffprobe", None)
    ffmpeg_path = getattr(ffmpeg_info, "path", None)
    ffprobe_path = getattr(ffprobe_info, "path", None)
    if not isinstance(ffmpeg_path, str) or not ffmpeg_path:
        raise _capability(
            "reference proposals require runnable local FFmpeg",
            details={"capability": "reference_proposal", "missing_tool": "ffmpeg"},
        )
    if not isinstance(ffprobe_path, str) or not ffprobe_path:
        raise _capability(
            "reference proposals require runnable local FFprobe for exact frame timing",
            details={"capability": "reference_proposal", "missing_tool": "ffprobe"},
        )
    return ffmpeg_path, ffprobe_path


def _require_runnable_tools(tools: Any) -> tuple[str, str]:
    """Require both discovered executables to answer a bounded version probe."""

    ffmpeg_path, ffprobe_path = _require_tool_paths(tools)
    for name, executable in (("ffmpeg", ffmpeg_path), ("ffprobe", ffprobe_path)):
        try:
            version = rrv_runtime.probe_tool_version(executable)
        except Exception:  # pragma: no cover - probe_tool_version normally absorbs runtime failures.
            version = None
        if not isinstance(version, str) or not version.strip():
            raise _capability(
                f"reference proposals require runnable local {name}",
                details={"capability": "reference_proposal", "missing_tool": name},
            )
    return ffmpeg_path, ffprobe_path


def _safe_runtime_call(label: str, operation: Any) -> Any:
    """Rewrap runtime failures without leaking a command line or a source path."""

    try:
        return operation()
    except rrv_runtime.RRVError as exc:
        details: dict[str, Any] = {"cause_code": exc.code}
        capability = exc.details.get("capability") if isinstance(exc.details, Mapping) else None
        if isinstance(capability, str):
            details["capability"] = capability
        raise rrv_runtime.RRVError(exc.code, f"{label} failed", details) from exc
    except Exception as exc:
        raise _tool_error(f"{label} failed") from exc


def _merge_exact_timing(media: Mapping[str, Any], timing: Mapping[str, Any]) -> Mapping[str, Any]:
    """Attach exact ffprobe timing without retaining raw probe metadata."""

    frame_count = timing.get("frame_count")
    fps = timing.get("fps")
    duration_seconds = timing.get("duration_seconds")
    if (
        not _is_int(frame_count)
        or frame_count < 1
        or not _is_finite_number(fps)
        or float(fps) <= 0
        or not _is_finite_number(duration_seconds)
        or float(duration_seconds) <= 0
        or timing.get("cfr_confirmed") is not True
    ):
        raise _capability(
            "ffprobe did not confirm exact CFR frame timing",
            details={"capability": "exact_cfr_frame_timing"},
        )
    streams = media.get("streams")
    if not isinstance(streams, list):
        raise _invalid("media probe returned invalid stream data")
    copied_streams: list[Any] = []
    video_count = 0
    for stream in streams:
        if isinstance(stream, Mapping) and stream.get("type") == "video":
            video_count += 1
            copied = dict(stream)
            copied.update(
                {
                    "frame_count": frame_count,
                    "frame_rate": float(fps),
                    "average_frame_rate": float(fps),
                    "exact_duration_seconds": float(duration_seconds),
                    "cfr_confirmed": True,
                }
            )
            copied_streams.append(copied)
        else:
            copied_streams.append(dict(stream) if isinstance(stream, Mapping) else stream)
    if video_count != 1:
        raise _capability(
            "S1 proposals require exactly one video stream",
            details={"capability": "single_video_s1", "video_stream_count": video_count},
        )
    merged = dict(media)
    merged["streams"] = copied_streams
    format_data = media.get("format")
    merged["format"] = (
        {**format_data, "duration_seconds": float(duration_seconds)}
        if isinstance(format_data, Mapping)
        else {"duration_seconds": float(duration_seconds)}
    )
    return merged


def _require_zero_rotation(media: Mapping[str, Any]) -> None:
    """Enforce the v0.4 zero-rotation boundary without normalizing metadata."""

    streams = media.get("streams")
    if not isinstance(streams, list):
        return
    video_streams = [item for item in streams if isinstance(item, Mapping) and item.get("type") == "video"]
    if len(video_streams) != 1:
        return  # The compiler's normal S1 gate reports the stream-count error.
    rotation = video_streams[0].get("rotation_degrees")
    if rotation is None:
        return
    if not _is_finite_number(rotation) or abs(float(rotation)) > 0.01:
        raise _capability(
            "S1 proposals require zero source rotation",
            details={"capability": "unrotated_source"},
        )


def _centered_source_rect(width: int, height: int, profiles: Sequence[str]) -> dict[str, int]:
    """Return the maximal centered integer 9:16 composition crop.

    This is deliberately a composition heuristic, not a claim that any UI or
    source region was semantically identified.  Reducing the common ratio to
    integer units keeps source-rectangle JSON stable and exact.
    """

    if not profiles:
        raise _invalid("output_profiles must not be empty")
    target_width, target_height = _OUTPUT_PROFILE_RATIOS[profiles[0]]
    if any(_OUTPUT_PROFILE_RATIOS[item] != (target_width, target_height) for item in profiles):
        raise _invalid("output_profiles must share one composition aspect ratio")
    units = min(width // target_width, height // target_height)
    if units < 1:
        raise _capability(
            "source dimensions are too small for the requested composition aspect",
            details={"capability": "composition_crop"},
        )
    crop_width = units * target_width
    crop_height = units * target_height
    return {
        "x": (width - crop_width) // 2,
        "y": (height - crop_height) // 2,
        "width": crop_width,
        "height": crop_height,
    }


def _analysis_height(source_rect: Mapping[str, int], analysis_width: int, frame_count: int) -> int:
    width = source_rect["width"]
    height = source_rect["height"]
    scaled_height = max(1, int(round(height * analysis_width / width)))
    raw_bytes = analysis_width * scaled_height * frame_count
    if raw_bytes > _MAX_ANALYSIS_RAW_BYTES:
        raise _capability(
            "downscaled grayscale analysis would exceed the bounded local limit",
            details={"capability": "bounded_grayscale_analysis"},
        )
    return scaled_height


def _is_link_or_reparse(stat_result: os.stat_result) -> bool:
    """Treat POSIX links and Windows reparse points as unsafe path entries."""

    if stat.S_ISLNK(stat_result.st_mode):
        return True
    attributes = getattr(stat_result, "st_file_attributes", 0)
    return isinstance(attributes, int) and bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _lstat_or_none(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _tool_error("could not inspect a local filesystem entry") from exc


def _capture_directory_identity(path: Path, label: str) -> _DirectoryIdentity:
    try:
        stat_result = os.lstat(path)
    except OSError as exc:
        raise _tool_error(f"could not inspect {label}") from exc
    if _is_link_or_reparse(stat_result) or not stat.S_ISDIR(stat_result.st_mode):
        raise _tool_error(f"{label} is not a safe local directory")
    # FAT-style filesystems can report an unusable zero inode.  The cleanup and
    # publication guarantees depend on a stable identity, so fail closed.
    if not isinstance(stat_result.st_ino, int) or stat_result.st_ino == 0:
        raise _tool_error(f"{label} does not expose a stable local identity")
    return _DirectoryIdentity(path=path, device=stat_result.st_dev, inode=stat_result.st_ino)


def _open_directory_guard(path: Path, label: str, *, allow_rename: bool) -> _DirectoryGuard:
    """Open an NT directory handle that blocks external rename/delete.

    A pathname check alone cannot protect an external program that will later
    open an argv output.  On Windows this handle is deliberately opened with
    no ``FILE_SHARE_DELETE``.  A junction replacement requires delete/rename
    access to the directory entry and therefore fails while the stage guard is
    alive.  ``FILE_FLAG_OPEN_REPARSE_POINT`` ensures a substituted junction is
    opened as the entry itself, never followed.

    Non-Windows callers retain identity/reparse checks.  The product's audited
    junction boundary is Windows-specific; callers on systems without this NT
    primitive still fail closed on every observable replacement.
    """

    if os.name != "nt":
        return _DirectoryGuard(path=path, handle=None)
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        desired_access = 0x00000080 | 0x00100000  # FILE_READ_ATTRIBUTES | SYNCHRONIZE
        if allow_rename:
            desired_access |= 0x00010000  # DELETE, required by FileRenameInfo.
        raw_handle = create_file(
            str(path),
            desired_access,
            0x00000001 | 0x00000002,  # FILE_SHARE_READ | FILE_SHARE_WRITE; never DELETE.
            None,
            3,  # OPEN_EXISTING
            0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
            None,
        )
        invalid = ctypes.c_void_p(-1).value
        handle_value = ctypes.c_void_p(raw_handle).value
    except Exception as exc:  # pragma: no cover - defensive ctypes boundary.
        raise _tool_error(f"could not bind {label} against local replacement") from exc
    if handle_value is None or handle_value == invalid:
        raise _tool_error(f"could not bind {label} against local replacement")
    return _DirectoryGuard(path=path, handle=int(handle_value))


def _close_directory_guard(guard: _DirectoryGuard) -> None:
    if guard.handle is None or guard.closed:
        return
    # Mark before calling CloseHandle.  A subsequent cleanup path must never
    # retry a numeric handle after Windows has made it available for reuse.
    guard.closed = True
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        close_handle(wintypes.HANDLE(guard.handle))
    except Exception:
        # This is a best-effort release after an operation has already reached
        # a safe terminal state.  Never turn it into a path-based cleanup.
        return


def _release_stage_guards(stage: _StageDirectory) -> None:
    """Release in child-before-parent order exactly once at a terminal state."""

    _close_directory_guard(stage.directory_guard)
    _close_directory_guard(stage.root_guard)


def _mark_windows_directory_for_delete(guard: _DirectoryGuard) -> bool:
    """Atomically remove an empty guarded directory when its handle closes."""

    if os.name != "nt" or guard.handle is None or guard.closed:
        return False
    try:
        class _FILE_DISPOSITION_INFO(ctypes.Structure):
            # Win32 defines this member as BOOLEAN (one byte), not BOOL.
            _fields_ = [("DeleteFile", ctypes.c_ubyte)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        set_information = kernel32.SetFileInformationByHandle
        set_information.argtypes = [
            wintypes.HANDLE,
            wintypes.INT,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        set_information.restype = wintypes.BOOL
        disposition = _FILE_DISPOSITION_INFO(True)
        return bool(
            set_information(
                wintypes.HANDLE(guard.handle),
                4,  # FileDispositionInfo
                ctypes.byref(disposition),
                ctypes.sizeof(disposition),
            )
        )
    except Exception:  # pragma: no cover - defensive ctypes boundary.
        return False


def _empty_stage_direct_files(stage: _StageDirectory) -> bool:
    """Safely clear a flat private stage without traversing any child links."""

    try:
        _assert_stage_live(stage)
        with os.scandir(stage.path) as scanner:
            entries = [(entry.name, Path(entry.path)) for entry in scanner]
        # The v0.4 stage intentionally contains only direct regular files.  If
        # anything else appears, it may be attacker-controlled: leave it alone.
        for _, child in entries:
            entry = _lstat_or_none(child)
            if entry is None or _is_link_or_reparse(entry) or not stat.S_ISREG(entry.st_mode):
                return False
        for _, child in entries:
            entry = _lstat_or_none(child)
            if entry is None:
                continue
            if _is_link_or_reparse(entry) or not stat.S_ISREG(entry.st_mode):
                return False
            # unlink removes the lexical entry itself; unlike recursive rmtree
            # it never descends through a replacement junction.
            child.unlink()
            _assert_stage_live(stage)
        with os.scandir(stage.path) as scanner:
            return next(scanner, None) is None
    except (OSError, rrv_runtime.RRVError):
        return False


def _assert_directory_identity(identity: _DirectoryIdentity, label: str) -> None:
    current = _capture_directory_identity(identity.path, label)
    if current.device != identity.device or current.inode != identity.inode:
        raise _tool_error(f"{label} changed while the operation was in progress")


def _assert_stage_live(stage: _StageDirectory) -> Path:
    if os.name == "nt" and (stage.root_guard.closed or stage.directory_guard.closed):
        raise _tool_error("local staging directory guard is no longer active")
    _assert_directory_identity(stage.root, "project root")
    _assert_directory_identity(stage.directory, "local staging directory")
    return stage.path


def _relative_parts(value: str, label: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise _invalid(f"{label} must be a normalized relative path")
    candidate = Path(value)
    # A Windows drive-rooted spelling such as ``\\packet.json`` is not
    # ``Path.is_absolute()`` (it has no drive) but would reset ``root / path``.
    # Treat every rooted spelling as non-relative before composing it below.
    if candidate.is_absolute() or candidate.drive or candidate.root:
        raise _invalid(f"{label} must be a normalized relative path")
    parts = candidate.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise _invalid(f"{label} must be a normalized relative path")
    return tuple(parts)


def _stage_path(root: Path, stage: _StageDirectory, name: str) -> Path:
    """Create only normal, non-reparse parents below a verified stage handle."""

    if root != stage.root.path:
        raise _tool_error("local staging directory belongs to a different project root")
    current = _assert_stage_live(stage)
    parts = _relative_parts(name, "staging artifact path")
    for component in parts[:-1]:
        child = current / component
        entry = _lstat_or_none(child)
        if entry is None:
            _assert_stage_live(stage)
            try:
                child.mkdir()
            except OSError as exc:
                raise _tool_error("could not create a local staging subdirectory") from exc
            entry = _lstat_or_none(child)
        if entry is None or _is_link_or_reparse(entry) or not stat.S_ISDIR(entry.st_mode):
            raise _tool_error("local staging subdirectory is not safe")
        current = child
    output = current / parts[-1]
    if _lstat_or_none(output) is not None:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_OUTPUT_EXISTS, "refusing to overwrite an existing output"
        )
    _assert_stage_live(stage)
    return output


def _assert_stage_output_ready(stage: _StageDirectory, path: Path) -> None:
    base = _assert_stage_live(stage)
    try:
        relative = path.relative_to(base)
    except ValueError as exc:
        raise _tool_error("staged artifact escaped its local output directory") from exc
    expected = _stage_path(stage.root.path, stage, relative.as_posix())
    if expected != path:
        raise _tool_error("staged artifact path changed while preparing output")


def _assert_stage_regular_file(stage: _StageDirectory, path: Path, label: str) -> None:
    base = _assert_stage_live(stage)
    try:
        relative = path.relative_to(base)
    except ValueError as exc:
        raise _tool_error("staged artifact escaped its local output directory") from exc
    parts = _relative_parts(relative.as_posix(), "staging artifact path")
    current = base
    for component in parts[:-1]:
        current = current / component
        entry = _lstat_or_none(current)
        if entry is None or _is_link_or_reparse(entry) or not stat.S_ISDIR(entry.st_mode):
            raise _tool_error(f"{label} is not inside a safe local staging directory")
    entry = _lstat_or_none(current / parts[-1])
    if entry is None or _is_link_or_reparse(entry) or not stat.S_ISREG(entry.st_mode):
        raise _tool_error(f"{label} is not a safe local file")
    _assert_stage_live(stage)


def _stage_regular_file_size(stage: _StageDirectory, path: Path, label: str) -> int:
    """Read a staged file size only after its lexical entry is re-verified."""

    _assert_stage_regular_file(stage, path, label)
    entry = _lstat_or_none(path)
    if entry is None or _is_link_or_reparse(entry) or not stat.S_ISREG(entry.st_mode):
        raise _tool_error(f"{label} is not a safe local file")
    _assert_stage_live(stage)
    return int(entry.st_size)


def _stage_tree_is_safe(stage: _StageDirectory, path: Path) -> bool:
    """Inspect a stage tree without resolving or descending through reparse points."""

    try:
        _assert_stage_live(stage)
        pending = [path]
        while pending:
            current = pending.pop()
            entry = os.lstat(current)
            if _is_link_or_reparse(entry):
                return False
            if stat.S_ISDIR(entry.st_mode):
                with os.scandir(current) as children:
                    pending.extend(Path(child.path) for child in children)
        _assert_stage_live(stage)
        return True
    except (OSError, rrv_runtime.RRVError):
        return False


def _remove_stage_file(stage: _StageDirectory, path: Path) -> None:
    _assert_stage_regular_file(stage, path, "transient local staging file")
    try:
        path.unlink()
    except OSError as exc:
        raise _tool_error("could not remove a transient local staging file") from exc
    _assert_stage_live(stage)


def _remove_stage_directory(stage: _StageDirectory, path: Path) -> None:
    _assert_stage_live(stage)
    try:
        relative = path.relative_to(stage.path)
    except ValueError as exc:
        raise _tool_error("staged directory escaped its local output directory") from exc
    _relative_parts(relative.as_posix(), "staging artifact path")
    entry = _lstat_or_none(path)
    if entry is None:
        return
    if _is_link_or_reparse(entry) or not stat.S_ISDIR(entry.st_mode) or not _stage_tree_is_safe(stage, path):
        raise _tool_error("transient local staging directory is not safe to remove")
    try:
        # Deliberately pass the original directory entry, never a resolved
        # target.  The preceding tree scan rejects junctions/symlinks.
        shutil.rmtree(path)
    except OSError as exc:
        raise _tool_error("could not remove a transient local staging directory") from exc
    _assert_stage_live(stage)


def _new_staging_directory(root: Path, role: str) -> _StageDirectory:
    root_guard: _DirectoryGuard | None = None
    stage_guard: _DirectoryGuard | None = None
    try:
        # Bind the root before creating a stage.  This prevents an attacker
        # from moving the root entry and retargeting every later stage pathname.
        root_guard = _open_directory_guard(root, "project root", allow_rename=False)
        root_identity = _capture_directory_identity(root, "project root")
        stage_path = Path(tempfile.mkdtemp(prefix=f".rrv-{role}-", dir=str(root)))
        # Do not resolve here: resolving an entry that has been swapped for a
        # junction would silently turn the private handle into its target.
        stage_path.relative_to(root)
        stage_guard = _open_directory_guard(
            stage_path, "local staging directory", allow_rename=True
        )
        stage_identity = _capture_directory_identity(stage_path, "local staging directory")
        return _StageDirectory(
            root=root_identity,
            directory=stage_identity,
            root_guard=root_guard,
            directory_guard=stage_guard,
        )
    except (OSError, ValueError, RuntimeError, rrv_runtime.RRVError) as exc:
        if stage_guard is not None:
            _close_directory_guard(stage_guard)
        if root_guard is not None:
            _close_directory_guard(root_guard)
        if isinstance(exc, rrv_runtime.RRVError):
            raise
        raise _tool_error("could not create a contained local staging directory") from exc


def _cleanup_directory(root: Path, stage: _StageDirectory | None) -> None:
    """Best-effort cleanup that never resolves or follows a replaced stage."""

    if stage is None:
        return
    if root != stage.root.path:
        _release_stage_guards(stage)
        return
    released = False
    try:
        if not _stage_tree_is_safe(stage, stage.path):
            return
        if os.name == "nt" and stage.directory_guard.handle is not None:
            # Keep the stage guard open while clearing direct files, then ask
            # Windows to delete that *same opened directory* on close.  There
            # is no pathname gap in which a junction can be substituted.
            if not _empty_stage_direct_files(stage):
                return
            if _mark_windows_directory_for_delete(stage.directory_guard):
                _release_stage_guards(stage)
                released = True
            return
        # ``stage.path`` remains the original entry.  If a replacement is
        # observed at any check, leave it untouched rather than risking user
        # content reached through a Windows junction or symlink.
        shutil.rmtree(stage.path)
    except (OSError, rrv_runtime.RRVError):
        return
    finally:
        if not released:
            _release_stage_guards(stage)


def _write_json_new(
    path: Path,
    payload: Mapping[str, Any],
    *,
    label: str,
    stage: _StageDirectory | None = None,
) -> None:
    if stage is not None:
        _assert_stage_output_ready(stage, path)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(rrv_runtime.stable_json_dumps(payload))
            handle.write("\n")
    except FileExistsError as exc:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_OUTPUT_EXISTS, "refusing to overwrite an existing output"
        ) from exc
    except OSError as exc:
        raise _tool_error(f"could not write {label}") from exc

    if stage is not None:
        _assert_stage_regular_file(stage, path, label)


def _lexical_relative_output_path(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise _tool_error("local output path escaped the project root") from exc
    parts = _relative_parts(relative.as_posix(), "output path")
    return "/".join(parts)


def _target_parent_chain(root: Path, target: Path) -> tuple[_DirectoryIdentity, ...]:
    """Bind a direct-child target to the one project-root directory entry.

    Windows path APIs cannot atomically prove a nested parent remains free of
    junction swaps while renaming a completed directory.  v0.4 therefore
    deliberately supports only direct children of ``project_root`` for final
    proposal/freeze outputs.  Staging and final rename then share this one
    captured parent identity and no final parent is ever created.
    """

    if target.parent != root:
        raise _invalid("output_dir must be a direct child of project_root")
    return (_capture_directory_identity(root, "project root"),)


def _direct_child_output_target(root: Path, value: str | os.PathLike[str]) -> Path:
    """Return a lexical direct-child output target without resolving it.

    ``rrv_runtime.resolve_output_path`` quite appropriately resolves general
    output paths for the rest of the product.  This security-sensitive
    publish path intentionally has a narrower contract: a final proposal or
    frozen-plan directory is one literal child entry of ``project_root``.
    Rejecting separators before any path resolution removes nested-parent and
    normalization races instead of discovering them later at publication.
    """

    try:
        raw = os.fspath(value)
    except TypeError as exc:
        raise _invalid("output_dir must be a direct child of project_root") from exc
    if (
        not isinstance(raw, str)
        or not raw
        or "\x00" in raw
        or "/" in raw
        or "\\" in raw
    ):
        raise _invalid("output_dir must be a direct child of project_root")
    try:
        name = Path(raw)
    except (TypeError, ValueError, OSError, RuntimeError) as exc:
        raise _invalid("output_dir must be a direct child of project_root") from exc
    if name.is_absolute() or name.drive or len(name.parts) != 1 or name.name in {"", ".", ".."}:
        raise _invalid("output_dir must be a direct child of project_root")
    target = root / name.name
    _target_parent_chain(root, target)
    if not _target_entry_is_absent(target):
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_OUTPUT_EXISTS, "refusing to overwrite an existing output"
        )
    return target


def _assert_directory_chain(identities: Sequence[_DirectoryIdentity], label: str) -> None:
    for identity in identities:
        _assert_directory_identity(identity, label)


def _target_entry_is_absent(target: Path) -> bool:
    return _lstat_or_none(target) is None


def _rename_bound_stage(stage: _StageDirectory, target: Path, *, label: str) -> None:
    """Rename a guarded stage by handle on Windows, never by source pathname."""

    if target.parent != stage.root.path:
        raise _tool_error("local output target is not a direct project-root child")
    if os.name != "nt":
        try:
            stage.path.rename(target)
            return
        except FileExistsError as exc:
            raise rrv_runtime.RRVError(
                rrv_runtime.ERR_OUTPUT_EXISTS, "refusing to overwrite an existing output"
            ) from exc
        except OSError as exc:
            raise _tool_error(f"could not publish atomic {label} output") from exc
    if (
        stage.directory_guard.handle is None
        or stage.root_guard.handle is None
        or stage.directory_guard.closed
        or stage.root_guard.closed
    ):
        raise _tool_error("could not bind local staging directory for atomic publication")
    try:
        class _FILE_RENAME_INFO(ctypes.Structure):
            _fields_ = [
                # Win32 defines this legacy member as BOOLEAN (one byte).
                ("ReplaceIfExists", ctypes.c_ubyte),
                ("RootDirectory", wintypes.HANDLE),
                ("FileNameLength", wintypes.DWORD),
                ("FileName", wintypes.WCHAR * 1),
            ]

        # SetFileInformationByHandle requires a fully-qualified name when
        # called through this Win32 API.  The root's no-delete guard remains
        # live and ``target`` is a lexical direct child, so this does not
        # reopen a replaceable nested-parent path.
        name_bytes = str(target).encode("utf-16-le")
        # The documented length excludes the terminator, but the Win32 layer
        # also reads a trailing wide NUL on some builds.  Reserve it explicitly
        # so a valid direct-child name cannot acquire adjacent buffer bytes.
        size = _FILE_RENAME_INFO.FileName.offset + len(name_bytes) + ctypes.sizeof(wintypes.WCHAR)
        buffer = ctypes.create_string_buffer(size)
        info = ctypes.cast(buffer, ctypes.POINTER(_FILE_RENAME_INFO)).contents
        info.ReplaceIfExists = False
        info.RootDirectory = None
        info.FileNameLength = len(name_bytes)
        ctypes.memmove(
            ctypes.addressof(buffer) + _FILE_RENAME_INFO.FileName.offset,
            name_bytes,
            len(name_bytes),
        )
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        set_information = kernel32.SetFileInformationByHandle
        set_information.argtypes = [
            wintypes.HANDLE,
            wintypes.INT,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        set_information.restype = wintypes.BOOL
        if set_information(
            wintypes.HANDLE(stage.directory_guard.handle),
            3,  # FileRenameInfo
            ctypes.byref(buffer),
            size,
        ):
            return
        error = ctypes.get_last_error()
    except Exception as exc:  # pragma: no cover - defensive ctypes boundary.
        raise _tool_error(f"could not publish atomic {label} output") from exc
    if error in {80, 183}:  # ERROR_FILE_EXISTS / ERROR_ALREADY_EXISTS
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_OUTPUT_EXISTS, "refusing to overwrite an existing output"
        )
    raise _tool_error(f"could not publish atomic {label} output")


def _stage_file_sha256(stage: _StageDirectory, path: Path) -> str:
    """Hash one stage file through a matching descriptor, never ``resolve``."""

    _assert_stage_regular_file(stage, path, "staged local artifact")
    before = _lstat_or_none(path)
    if before is None:  # Defensive; the preceding verifier already checked it.
        raise _tool_error("staged local artifact disappeared before hashing")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags | nofollow)
    except OSError as exc:
        if nofollow and getattr(exc, "errno", None) in {22, 95}:
            try:
                descriptor = os.open(path, flags)
            except OSError as retry_exc:
                raise _tool_error("could not hash a staged local artifact") from retry_exc
        else:
            raise _tool_error("could not hash a staged local artifact") from exc
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            opened = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != before.st_dev
                or opened.st_ino != before.st_ino
            ):
                raise _tool_error("staged local artifact changed before hashing")
            while chunk := handle.read(1024 * 1024):
                total += len(chunk)
                if total > _MAX_EVIDENCE_ARTIFACT_BYTES:
                    raise _tool_error("staged local artifact exceeds the bounded evidence size")
                digest.update(chunk)
    except rrv_runtime.RRVError:
        raise
    except OSError as exc:
        raise _tool_error("could not hash a staged local artifact") from exc
    _assert_stage_regular_file(stage, path, "staged local artifact")
    return digest.hexdigest()


def _published_artifact(root: Path, stage: _StageDirectory, target: Path, path: Path) -> dict[str, str]:
    _assert_stage_regular_file(stage, path, "staged local artifact")
    try:
        relative = path.relative_to(stage.path)
    except ValueError as exc:  # pragma: no cover - internal containment invariant.
        raise _tool_error("staged artifact escaped its local output directory") from exc
    return {
        "path": _lexical_relative_output_path(root, target / relative),
        "sha256": _stage_file_sha256(stage, path),
    }


def _rollback_publish(stage: _StageDirectory, target: Path, parents: Sequence[_DirectoryIdentity]) -> None:
    """Move a verified stage back only while both directory identities survive."""

    try:
        _assert_directory_chain(parents, "local output parent")
        moved = _capture_directory_identity(target, "published local output")
        if (
            moved.device != stage.directory.device
            or moved.inode != stage.directory.inode
            or _lstat_or_none(stage.path) is not None
        ):
            return
        if os.name == "nt":
            _rename_bound_stage(stage, stage.path, label="local staging rollback")
        else:
            target.rename(stage.path)
    except (OSError, rrv_runtime.RRVError):
        return


def _publish_stage(root: Path, stage: _StageDirectory, target: Path, *, label: str) -> None:
    """Publish one complete directory without following reparse-point parents."""

    if root != stage.root.path:
        raise _tool_error("local staging directory belongs to a different project root")
    parents = _target_parent_chain(root, target)
    try:
        _assert_stage_live(stage)
        _assert_directory_chain(parents, "local output parent")
        if not _stage_tree_is_safe(stage, stage.path):
            raise _tool_error("local staging directory changed while preparing publication")
        if not _target_entry_is_absent(target):
            raise rrv_runtime.RRVError(
                rrv_runtime.ERR_OUTPUT_EXISTS, "refusing to overwrite an existing output"
            )
        # Re-check immediately before rename.  Pathname-based Windows APIs
        # cannot make this cross-directory check and rename one primitive, so
        # any observed identity/reparse change fails closed below.
        _assert_stage_live(stage)
        _assert_directory_chain(parents, "local output parent")
        if not _stage_tree_is_safe(stage, stage.path):
            raise _tool_error("local staging directory changed while preparing publication")
        _rename_bound_stage(stage, target, label=label)
        _assert_directory_chain(parents, "local output parent")
        moved = _capture_directory_identity(target, "published local output")
        if moved.device != stage.directory.device or moved.inode != stage.directory.inode:
            raise _tool_error("published local output changed during atomic publication")
        _release_stage_guards(stage)
    except rrv_runtime.RRVError:
        _rollback_publish(stage, target, parents)
        raise
    except FileExistsError as exc:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_OUTPUT_EXISTS, "refusing to overwrite an existing output"
        ) from exc
    except OSError as exc:
        _rollback_publish(stage, target, parents)
        raise _tool_error(f"could not publish atomic {label} output") from exc


def _open_stage_output_file(stage: _StageDirectory, path: Path, label: str) -> Any:
    """Create an exclusive output handle while the guarded stage is bound."""

    _assert_stage_output_ready(stage, path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags | nofollow, 0o600)
    except FileExistsError as exc:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_OUTPUT_EXISTS, "refusing to overwrite an existing output"
        ) from exc
    except OSError as exc:
        if nofollow and getattr(exc, "errno", None) in {22, 95}:
            try:
                descriptor = os.open(path, flags, 0o600)
            except FileExistsError as retry_exc:
                raise rrv_runtime.RRVError(
                    rrv_runtime.ERR_OUTPUT_EXISTS, "refusing to overwrite an existing output"
                ) from retry_exc
            except OSError as retry_exc:
                raise _tool_error(f"could not prepare {label}") from retry_exc
        else:
            raise _tool_error(f"could not prepare {label}") from exc
    try:
        opened = os.fstat(descriptor)
        entry = _lstat_or_none(path)
        if (
            entry is None
            or _is_link_or_reparse(entry)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != entry.st_dev
            or opened.st_ino != entry.st_ino
        ):
            raise _tool_error(f"{label} changed while preparing output")
        _assert_stage_live(stage)
        return os.fdopen(descriptor, "wb", closefd=True)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _compact_process_text(value: bytes | None) -> str:
    if not value:
        return ""
    return " ".join(value.decode("utf-8", errors="replace").split())[:512]


def _run_argv_to_open_file(command: Sequence[str], output_handle: Any, timeout_seconds: float) -> None:
    """Run argv-only FFmpeg with stdout bound to an already-open stage file."""

    try:
        process = subprocess.Popen(
            list(command),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=output_handle,
            stderr=subprocess.PIPE,
            text=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError as exc:
        raise rrv_runtime.RRVError(rrv_runtime.ERR_TOOL_NOT_FOUND, "local FFmpeg was not found") from exc
    except OSError as exc:
        raise _tool_error("could not start local FFmpeg") from exc
    try:
        _, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_TOOL_TIMEOUT,
            "local FFmpeg exceeded the timeout",
            {"timeout_seconds": timeout_seconds},
        )
    if process.returncode != 0:
        details: dict[str, Any] = {"returncode": process.returncode}
        summary = _compact_process_text(stderr)
        if summary:
            details["output"] = summary
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_TOOL_EXECUTION,
            "local FFmpeg exited with an error",
            details,
        )


def _stdout_pipe_command(command: Sequence[str], *, image_output: bool) -> list[str]:
    """Replace the final output pathname with FFmpeg's stdout pipe endpoint."""

    if not command:
        raise _tool_error("could not prepare local FFmpeg output")
    piped = list(command[:-1])
    if image_output:
        # A pathname extension normally selects PNG's image muxer/encoder.
        # stdout has no extension, so specify both deterministically.
        piped.extend(["-f", "image2pipe", "-c:v", "png"])
    piped.append("pipe:1")
    return piped


def _run_output(
    stage: _StageDirectory,
    command: Sequence[str],
    output: Path,
    timeout_seconds: float,
    label: str,
    *,
    image_output: bool = False,
) -> None:
    """Run FFmpeg without ever giving it a mutable stage output pathname."""

    try:
        with _open_stage_output_file(stage, output, label) as output_handle:
            _run_argv_to_open_file(
                _stdout_pipe_command(command, image_output=image_output),
                output_handle,
                timeout_seconds,
            )
    except rrv_runtime.RRVError as exc:
        safe_details: dict[str, Any] = {"cause_code": exc.code}
        if isinstance(exc.details, Mapping):
            for key in ("capability", "timeout_seconds", "returncode"):
                value = exc.details.get(key)
                if isinstance(value, (str, int, float, bool)):
                    safe_details[key] = value
        raise rrv_runtime.RRVError(exc.code, f"{label} failed", safe_details) from exc
    except Exception as exc:
        raise _tool_error(f"{label} failed") from exc
    _assert_stage_regular_file(stage, output, f"{label} output")


def _build_evidence_frame_command(
    source: Path,
    ffmpeg: str,
    source_rect: Mapping[str, int],
    frame_number: int,
    output: Path,
) -> list[str]:
    if not _is_int(frame_number) or frame_number < 0:
        raise _invalid("evidence frame number must be a non-negative integer")
    rect = source_rect
    filter_graph = (
        f"select=eq(n\\,{frame_number}),"
        f"crop=w={rect['width']}:h={rect['height']}:x={rect['x']}:y={rect['y']}"
    )
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
        "-vf",
        filter_graph,
        "-frames:v",
        "1",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-n",
        str(output),
    ]


def _iter_gray_frames(
    path: Path,
    width: int,
    height: int,
    frame_count: int,
    *,
    stage: _StageDirectory | None = None,
) -> Iterable[bytes]:
    frame_bytes = width * height
    if stage is not None:
        _assert_stage_regular_file(stage, path, "local grayscale analysis")
    try:
        with path.open("rb") as handle:
            for _ in range(frame_count):
                frame = handle.read(frame_bytes)
                if len(frame) != frame_bytes:
                    raise _capability(
                        "grayscale analysis did not produce the exact CFR frame count",
                        details={"capability": "exact_grayscale_analysis"},
                    )
                yield frame
            if handle.read(1):
                raise _capability(
                    "grayscale analysis produced more frames than the exact CFR count",
                    details={"capability": "exact_grayscale_analysis"},
                )
    except rrv_runtime.RRVError:
        raise
    except OSError as exc:
        raise _tool_error("could not read local grayscale analysis") from exc
    if stage is not None:
        _assert_stage_regular_file(stage, path, "local grayscale analysis")


def _temporal_row_activity(
    path: Path,
    width: int,
    height: int,
    frame_count: int,
    *,
    stage: _StageDirectory | None = None,
) -> list[float]:
    """Average adjacent-frame absolute difference for each downscaled row."""

    if frame_count < 2:
        return [0.0] * height
    totals = [0] * height
    previous: bytes | None = None
    for current in _iter_gray_frames(path, width, height, frame_count, stage=stage):
        if previous is not None:
            for row in range(height):
                offset = row * width
                totals[row] += sum(
                    abs(before - after)
                    for before, after in zip(previous[offset : offset + width], current[offset : offset + width])
                )
        previous = current
    divisor = (frame_count - 1) * width * 255
    return [total / divisor for total in totals]


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _boundary_candidates(
    row_activity: Sequence[float],
    canvas_height: int,
) -> list[dict[str, Any]]:
    """Infer a top carousel edge from a compact temporal row-activity curve."""

    height = len(row_activity)
    if height < 3 or canvas_height < 2:
        return [{"y": max(1, canvas_height // 4), "score": 0.1, "method": "proportional-fallback"}]
    maximum = max(row_activity, default=0.0)
    low = max(1, int(math.ceil(height * 0.08)))
    high = min(height - 1, max(low, int(math.floor(height * 0.68))))
    if maximum <= 1e-9 or low > high:
        y = max(1, min(canvas_height - 1, int(round(canvas_height * 0.25))))
        return [{"y": y, "score": 0.1, "method": "proportional-fallback"}]

    ranked: list[tuple[float, int]] = []
    for boundary in range(low, high + 1):
        top = _mean(row_activity[:boundary])
        bottom = _mean(row_activity[boundary:])
        radius = max(1, height // 40)
        before = _mean(row_activity[max(0, boundary - radius) : boundary])
        after = _mean(row_activity[boundary : min(height, boundary + radius)])
        contrast = max(0.0, top - bottom) / maximum
        local_drop = max(0.0, before - after) / maximum
        # A small location term makes a perfectly flat plateaus deterministic
        # without pretending it is a semantic observation.
        location = 1.0 - abs(boundary / height - 0.25)
        ranked.append((0.62 * contrast + 0.30 * local_drop + 0.08 * location, boundary))

    selected: list[tuple[float, int]] = []
    separation = max(1, height // 12)
    for score, boundary in sorted(ranked, key=lambda item: (-item[0], item[1])):
        if all(abs(boundary - prior) >= separation for _, prior in selected):
            selected.append((score, boundary))
        if len(selected) >= 3:
            break
    if not selected:
        selected = [(0.1, max(1, height // 4))]

    converted: dict[int, dict[str, Any]] = {}
    for score, boundary in selected:
        source_y = int(round(boundary * canvas_height / height))
        source_y = max(1, min(canvas_height - 1, source_y))
        candidate = {
            "y": source_y,
            # Do not make an unreviewed temporal edge look authoritative.
            "score": _bounded_float(score, upper=0.6),
            "method": "temporal-row-activity",
        }
        old = converted.get(source_y)
        if old is None or candidate["score"] > old["score"]:
            converted[source_y] = candidate
    return sorted(converted.values(), key=lambda item: (-item["score"], item["y"]))


def _subject_transition_scores(
    path: Path,
    width: int,
    height: int,
    frame_count: int,
    subject_start_canvas: int,
    canvas_height: int,
    *,
    stage: _StageDirectory | None = None,
) -> dict[int, float]:
    """Return normalized adjacent-MAD values for the non-carousel area."""

    analysis_start = int(round(subject_start_canvas * height / canvas_height))
    analysis_start = max(0, min(height - 1, analysis_start))
    pixels = (height - analysis_start) * width
    if frame_count < 2 or pixels < 1:
        return {}
    scores: dict[int, float] = {}
    previous: bytes | None = None
    for frame_number, current in enumerate(
        _iter_gray_frames(path, width, height, frame_count, stage=stage)
    ):
        if previous is not None:
            offset = analysis_start * width
            total = sum(abs(before - after) for before, after in zip(previous[offset:], current[offset:]))
            scores[frame_number] = total / (pixels * 255)
        previous = current
    return scores


def _transition_peaks(scores: Mapping[int, float], fps: float) -> list[dict[str, float | int]]:
    """Pick bounded hard-cut candidates from the compact adjacent-MAD curve."""

    if not scores:
        return []
    frames = sorted(scores)
    values = [max(0.0, float(scores[frame])) for frame in frames]
    baseline = _median(values)
    deviation = _median([abs(value - baseline) for value in values])
    threshold = baseline + max(0.008, 3.0 * deviation)
    possible: list[dict[str, float | int]] = []
    for index, frame in enumerate(frames):
        value = values[index]
        left = values[index - 1] if index else -1.0
        right = values[index + 1] if index + 1 < len(values) else -1.0
        # The strict left comparison breaks flat-score ties toward the earliest
        # frame; this is deterministic across Python implementations.
        if value >= threshold and value > left and value >= right:
            possible.append(
                {
                    "frame": frame,
                    "score": _stable_number(value),
                    "prominence": _stable_number(max(0.0, value - baseline)),
                }
            )
    if not possible:
        index, value = max(enumerate(values), key=lambda item: (item[1], -frames[item[0]]))
        if value >= max(0.02, baseline * 2.0):
            possible.append(
                {
                    "frame": frames[index],
                    "score": _stable_number(value),
                    "prominence": _stable_number(max(0.0, value - baseline)),
                }
            )

    selected: list[dict[str, float | int]] = []
    separation = max(1, int(round(fps * 0.15)))
    for candidate in sorted(
        possible,
        key=lambda item: (-float(item["prominence"]), -float(item["score"]), int(item["frame"])),
    ):
        if all(abs(int(candidate["frame"]) - int(prior["frame"])) >= separation for prior in selected):
            selected.append(candidate)
        if len(selected) >= 64:
            break
    return sorted(selected, key=lambda item: int(item["frame"]))


def _slot_count_candidates(
    *,
    peaks: Sequence[Mapping[str, float | int]],
    frame_count: int,
    fps: float,
    hint: int | None,
) -> tuple[list[dict[str, Any]], int]:
    """Offer only deduplicated numeric count candidates, never a semantic label."""

    candidates: list[dict[str, Any]] = []

    def add(value: int, score: float, method: str) -> None:
        value = max(1, min(64, frame_count, int(value)))
        normalized = _bounded_float(score, upper=0.65)
        for current in candidates:
            if current["value"] == value:
                methods = current["method"].split("+")
                if method not in methods:
                    current["method"] = "+".join([*methods, method])
                current["score"] = max(current["score"], normalized)
                return
        candidates.append({"value": value, "score": normalized, "method": method})

    if hint is not None:
        add(hint, 0.25, "user-hint")
    hard_count = len(peaks) + 1
    if peaks:
        mean_prominence = _mean([float(item["prominence"]) for item in peaks])
        add(hard_count, 0.20 + min(0.42, mean_prominence * 5.0), "hard-cut")
    else:
        add(1, 0.12, "hard-cut")

    if len(peaks) >= 2:
        boundaries = [0, *(int(item["frame"]) for item in peaks), frame_count]
        spacings = [end - start for start, end in zip(boundaries, boundaries[1:])]
        average = _mean([float(item) for item in spacings])
        spread = math.sqrt(_mean([(item - average) ** 2 for item in spacings])) if average else math.inf
        coefficient = spread / average if average else math.inf
        if coefficient <= 0.55:
            add(len(peaks) + 1, 0.32 + max(0.0, 0.26 * (1.0 - coefficient / 0.55)), "periodic-spacing")

    approximately_seconds = max(1, int(round(frame_count / fps)))
    add(approximately_seconds, 0.18, "rough-one-second-cadence")
    if hint is not None:
        return candidates, hint
    chosen = max(candidates, key=lambda item: (item["score"], -item["value"], item["method"]))
    return candidates, int(chosen["value"])


def _manual_timing(
    peaks: Sequence[Mapping[str, float | int]],
    slot_count: int,
    frame_count: int,
) -> tuple[str, list[int], float]:
    """Use manual starts only when strong peaks exactly match the chosen count."""

    if slot_count <= 1 or len(peaks) != slot_count - 1:
        return "uniform", [], 0.16
    switches = [int(item["frame"]) for item in peaks]
    if any(not 0 < frame < frame_count for frame in switches) or switches != sorted(switches):
        return "uniform", [], 0.16
    boundaries = [0, *switches, frame_count]
    segments = [end - start for start, end in zip(boundaries, boundaries[1:])]
    if not segments or min(segments) < 1:
        return "uniform", [], 0.16
    strengths = [max(0.0, float(item["prominence"])) for item in peaks]
    raw_scores = [max(0.0, float(item["score"])) for item in peaks]
    if not strengths or min(raw_scores) < 0.02 or max(strengths) <= 0:
        return "uniform", [], 0.16
    strength_ratio = min(strengths) / max(strengths)
    mean_segment = _mean([float(item) for item in segments])
    segment_spread = math.sqrt(_mean([(item - mean_segment) ** 2 for item in segments]))
    segment_coefficient = segment_spread / mean_segment if mean_segment else math.inf
    # Carousel timing is normally regular; weak/inconsistent sequences remain
    # intentionally uniform until a reviewer chooses otherwise.
    if strength_ratio < 0.30 or segment_coefficient > 0.70:
        return "uniform", [], 0.16
    confidence = _bounded_float(0.30 + 0.25 * strength_ratio + 0.10 * (1.0 - segment_coefficient / 0.70), upper=0.62)
    return "manual", [0, *switches], confidence


def _representative_frames(
    frame_count: int,
    maximum: int,
    manual_starts: Sequence[int],
    peaks: Sequence[Mapping[str, float | int]],
) -> list[int]:
    """Select a bounded, exact, reproducible evidence set without score dumps."""

    ordered: list[int] = []

    def add(frame: int) -> None:
        if 0 <= frame < frame_count and frame not in ordered and len(ordered) < maximum:
            ordered.append(frame)

    add(0)
    add(frame_count - 1)
    for frame in manual_starts:
        add(int(frame))
    for peak in sorted(peaks, key=lambda item: (-float(item["prominence"]), int(item["frame"]))):
        add(int(peak["frame"]))
    if maximum > 1:
        for index in range(maximum):
            add(int(round(index * (frame_count - 1) / (maximum - 1))))
    return sorted(ordered)


def _load_pillow() -> tuple[Any, Any]:
    try:
        return rrv_analyze._load_pillow()
    except rrv_runtime.RRVError as exc:
        raise _capability(
            "proposal evidence requires the local Pillow dependency",
            details={"capability": "proposal_evidence", "dependency": "Pillow"},
        ) from exc


def _save_jpeg(image: Any, path: Path, stage: _StageDirectory) -> None:
    _assert_stage_output_ready(stage, path)
    try:
        with path.open("xb") as handle:
            image.save(
                handle,
                format="JPEG",
                quality=90,
                subsampling=0,
                optimize=False,
                progressive=False,
                exif=b"",
            )
    except FileExistsError as exc:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_OUTPUT_EXISTS, "refusing to overwrite an existing output"
        ) from exc
    except OSError as exc:
        raise _tool_error("could not write local JPEG evidence") from exc
    _assert_stage_regular_file(stage, path, "local JPEG evidence")


def _save_png(image: Any, path: Path, stage: _StageDirectory) -> None:
    _assert_stage_output_ready(stage, path)
    try:
        with path.open("xb") as handle:
            image.save(handle, format="PNG", optimize=False, compress_level=9)
    except FileExistsError as exc:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_OUTPUT_EXISTS, "refusing to overwrite an existing output"
        ) from exc
    except OSError as exc:
        raise _tool_error("could not write local PNG evidence") from exc
    _assert_stage_regular_file(stage, path, "local PNG evidence")


def _create_contact_sheet(
    frame_items: Sequence[tuple[int, Path]], output: Path, stage: _StageDirectory
) -> None:
    Image, _ = _load_pillow()
    columns = min(4, max(1, len(frame_items)))
    cell_width, cell_height = 240, 320
    rows = int(math.ceil(len(frame_items) / columns))
    canvas = Image.new("RGB", (columns * cell_width, rows * cell_height), "white")
    resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
    try:
        for index, (_, frame_path) in enumerate(frame_items):
            _assert_stage_regular_file(stage, frame_path, "exact evidence frame")
            column, row = index % columns, index // columns
            x, y = column * cell_width, row * cell_height
            with Image.open(frame_path) as source:
                image = source.convert("RGB")
                image.thumbnail((cell_width - 8, cell_height - 8), resampling)
                canvas.paste(image, (x + (cell_width - image.width) // 2, y + (cell_height - image.height) // 2))
                image.close()
        _save_jpeg(canvas, output, stage)
    finally:
        canvas.close()


def _create_geometry_preview(
    frame_path: Path,
    output: Path,
    stage: _StageDirectory,
    canvas_width: int,
    canvas_height: int,
    carousel_y: int,
) -> None:
    Image, ImageDraw = _load_pillow()
    _assert_stage_regular_file(stage, frame_path, "exact evidence frame")
    with Image.open(frame_path) as source:
        image = source.convert("RGB")
    try:
        draw = ImageDraw.Draw(image)
        right, bottom = canvas_width - 1, canvas_height - 1
        boundary = max(1, min(canvas_height - 1, carousel_y))
        # Green = deterministic source composition crop, amber = carousel,
        # blue = subject remainder.  There is intentionally no semantic label.
        draw.rectangle((0, 0, right, bottom), outline=(20, 180, 80), width=3)
        draw.rectangle((0, 0, right, boundary - 1), outline=(240, 165, 0), width=3)
        draw.rectangle((0, boundary, right, bottom), outline=(40, 110, 235), width=3)
        _save_jpeg(image, output, stage)
    finally:
        image.close()


def _create_timing_profile(
    scores: Mapping[int, float],
    frame_count: int,
    suggested_starts: Sequence[int],
    output: Path,
    stage: _StageDirectory,
) -> None:
    Image, ImageDraw = _load_pillow()
    width, height = 800, 260
    left, right, top, bottom = 32, width - 20, 18, height - 28
    canvas = Image.new("RGB", (width, height), "white")
    try:
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((left, top, right, bottom), outline=(150, 150, 150), width=1)
        maximum = max(scores.values(), default=0.0)
        points: list[tuple[int, int]] = []
        for frame in sorted(scores):
            x = left + int(round((right - left) * frame / max(1, frame_count - 1)))
            normalized = float(scores[frame]) / maximum if maximum > 0 else 0.0
            y = bottom - int(round((bottom - top) * normalized))
            points.append((x, y))
        if len(points) == 1:
            draw.point(points[0], fill=(45, 85, 190))
        elif len(points) > 1:
            draw.line(points, fill=(45, 85, 190), width=2)
        for frame in sorted(set(int(item) for item in suggested_starts if int(item) > 0)):
            if frame >= frame_count:
                continue
            x = left + int(round((right - left) * frame / max(1, frame_count - 1)))
            draw.line((x, top, x, bottom), fill=(205, 55, 55), width=2)
        _save_png(canvas, output, stage)
    finally:
        canvas.close()


def _background_from_margins(
    frame_paths: Sequence[Path],
    *,
    stage: _StageDirectory,
    subject_y: int,
    canvas_width: int,
    canvas_height: int,
) -> tuple[str, float]:
    """Robust side/corner median, explicitly excluding the lowest fifth."""

    Image, _ = _load_pillow()
    samples: list[tuple[int, int, int]] = []
    lower_exclusion = max(subject_y + 1, int(math.floor(canvas_height * 0.80)))
    y_start = max(subject_y, int(math.floor(canvas_height * 0.08)))
    y_end = min(canvas_height, lower_exclusion)
    margin = max(1, min(canvas_width // 10, 48))
    y_step = max(1, (y_end - y_start) // 48)
    x_step = max(1, margin // 12)
    for frame_path in frame_paths:
        _assert_stage_regular_file(stage, frame_path, "exact evidence frame")
        with Image.open(frame_path) as source:
            image = source.convert("RGB")
        try:
            width, height = image.size
            # Exact evidence frames should have the crop's dimensions.  If an
            # encoder behaved unexpectedly, avoid sampling outside its bounds.
            upper = min(y_end, height)
            for y in range(min(y_start, upper), upper, y_step):
                for x in range(0, min(margin, width), x_step):
                    samples.append(image.getpixel((x, y)))
                for x in range(max(0, width - margin), width, x_step):
                    samples.append(image.getpixel((x, y)))
        finally:
            image.close()
    if not samples:
        return "#FFFFFF", 0.1
    channels = [[pixel[index] for pixel in samples] for index in range(3)]
    median_channels = [int(round(_median(channel))) for channel in channels]
    deviations = [abs(pixel[index] - median_channels[index]) for pixel in samples for index in range(3)]
    dispersion = _median([float(item) for item in deviations])
    confidence = _bounded_float(0.55 * (1.0 - min(1.0, dispersion / 80.0)), lower=0.12, upper=0.55)
    return "#" + "".join(f"{max(0, min(255, value)):02X}" for value in median_channels), confidence


def _candidate_plan(
    *,
    template_id: str,
    source_rect: Mapping[str, int],
    carousel_y: int,
    slot_count: int,
    timing_mode: str,
    manual_starts: Sequence[int],
    background_color: str,
    audio_mode: str,
    audio_rights_confirmed: bool,
    output_profiles: Sequence[str],
    analysis_width: int,
    max_evidence_frames: int,
) -> dict[str, Any]:
    canvas_width, canvas_height = source_rect["width"], source_rect["height"]
    carousel_height = max(1, min(canvas_height - 1, carousel_y))
    subject_height = canvas_height - carousel_height
    item_height = carousel_height
    item_width = max(1, min(canvas_width, int(round(item_height * 0.75))))
    gap = max(0, int(round(item_width * 0.05)))
    content_width = slot_count * item_width + (slot_count - 1) * gap
    plan: dict[str, Any] = {
        "schema_version": "0.3.0",
        "template_id": template_id,
        "family": "fixed-subject-carousel",
        "authorization": {
            "reference_rights_confirmed": True,
            "audio_rights_confirmed": audio_rights_confirmed,
        },
        "privacy": "local-only",
        "geometry": {
            "source_rect": dict(source_rect),
            "carousel_rect": {"x": 0, "y": 0, "width": canvas_width, "height": carousel_height},
            "subject_rect": {"x": 0, "y": carousel_height, "width": canvas_width, "height": subject_height},
        },
        "timing": {
            "slot_count": slot_count,
            "mode": timing_mode,
            "min_segment_frames": 1,
        },
        "carousel": {
            "origin": {"x": 0, "y": 0},
            "item_width": item_width,
            "item_height": item_height,
            "gap": gap,
            "end_offset_x": min(0, canvas_width - content_width),
        },
        "background": {"color": background_color, "replaceable": True},
        "audio": {"mode": audio_mode, "required": audio_mode != "mute"},
        "output_profiles": list(output_profiles),
        "analysis": {
            "width": analysis_width,
            "snap_window_frames": 0,
            "min_prominence": 0.02,
            "max_evidence_frames": max_evidence_frames,
        },
    }
    if timing_mode == "manual":
        plan["timing"]["switch_frames"] = list(manual_starts)
    return plan


def _proposal_confidence(
    *,
    source_rect: Mapping[str, int],
    media_width: int,
    media_height: int,
    boundary_score: float,
    slot_candidates: Sequence[Mapping[str, Any]],
    slot_count: int,
    timing_confidence: float,
    background_confidence: float,
) -> dict[str, float]:
    selected_slot = next((item for item in slot_candidates if item["value"] == slot_count), None)
    slot_score = float(selected_slot["score"]) if selected_slot is not None else 0.1
    full_source = source_rect["width"] == media_width and source_rect["height"] == media_height
    source_confidence = 0.55 if full_source else 0.42
    values = {
        "source_rect": _bounded_float(source_confidence, upper=0.6),
        "carousel_boundary": _bounded_float(boundary_score, upper=0.6),
        "slot_count": _bounded_float(slot_score, upper=0.65),
        "timing": _bounded_float(timing_confidence, upper=0.62),
        "carousel_layout": 0.25,
        "background_color": _bounded_float(background_confidence, upper=0.55),
    }
    # A review-required document should never carry an apparently certain
    # aggregate score, even when several simple measurements agree.
    values["overall"] = _bounded_float(sum(values.values()) / len(values), upper=0.55)
    return {
        "overall": values["overall"],
        "source_rect": values["source_rect"],
        "carousel_boundary": values["carousel_boundary"],
        "slot_count": values["slot_count"],
        "timing": values["timing"],
        "carousel_layout": values["carousel_layout"],
        "background_color": values["background_color"],
    }


def _proposal_limitations(*, audio_mode: str, source_was_cropped: bool, timing_mode: str) -> list[str]:
    crop_note = (
        "Source geometry uses a deterministic centered 9:16 composition crop; it is not a semantic UI-removal decision."
        if source_was_cropped
        else "Source geometry uses the displayed 9:16 frame; it is not a semantic UI-removal decision."
    )
    timing_note = (
        "Manual timing was selected only from strong, regular grayscale transitions and still requires reviewer confirmation."
        if timing_mode == "manual"
        else "Timing falls back to uniform segments because the selected count was not matched by strong, regular grayscale transitions."
    )
    return [
        crop_note,
        "Carousel boundary and carousel layout are temporal/proportional estimates and require review.",
        timing_note,
        "Background color is a robust side/corner margin sample from the upper and middle subject area; the lowest fifth is excluded.",
        f"Audio mode is {audio_mode}; audio treatment and rights require explicit reviewer confirmation.",
        "No OCR, semantic identity, garment, product, or UI classification, cloud processing, or asset generation was performed.",
    ]


def _review_template(proposal_sha256: str, candidate_plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "proposal_sha256": proposal_sha256,
        "decision": "pending",
        "reviewer_confirmed": False,
        "confirmations": {key: False for key in _CONFIRMATION_KEYS},
        "approved_plan": dict(candidate_plan),
        "notes": "",
    }


def propose_reference(
    source: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str],
    template_id: str,
    output_dir: str | os.PathLike[str] = "plan-proposal",
    slot_count_hint: int | None = None,
    audio_mode: str = "preserve",
    reference_rights_confirmed: bool = False,
    audio_rights_confirmed: bool = False,
    output_profiles: Sequence[str] = ("720x1280", "1080x1920"),
    analysis_width: int = 96,
    max_evidence_frames: int = 24,
    ffmpeg: str | os.PathLike[str] | None = None,
    ffprobe: str | os.PathLike[str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Create a local, review-required candidate Compiler Plan.

    The source is only read.  The final proposal directory is published after
    all media gates, evidence generation, schema checks, and nested Compiler
    Plan semantic checks have succeeded.
    """

    # Authorization is deliberately the first gate: no source probing, tool
    # invocation, staging, or final output may happen before it is explicit.
    if reference_rights_confirmed is not True:
        raise _invalid("reference_rights_confirmed must be true before proposing a reference")
    (
        template_id,
        slot_count_hint,
        audio_mode,
        audio_rights_confirmed,
        profiles,
        analysis_width,
        max_evidence_frames,
    ) = _validate_proposal_arguments(
        template_id=template_id,
        slot_count_hint=slot_count_hint,
        audio_mode=audio_mode,
        audio_rights_confirmed=audio_rights_confirmed,
        output_profiles=output_profiles,
        analysis_width=analysis_width,
        max_evidence_frames=max_evidence_frames,
    )
    if audio_mode == "preserve" and audio_rights_confirmed is not True:
        raise _invalid("preserve audio requires audio_rights_confirmed=true")

    root = rrv_runtime.require_project_root(project_root)
    # Validate the target early but do not create it or its parent yet.
    # A final publish never creates a parent directory.  The security boundary
    # is deliberately one literal child of project_root, verified before any
    # source/tool work begins.
    target = _direct_child_output_target(root, output_dir)
    source_path = rrv_runtime.require_source_file(source)
    timeout = rrv_runtime.validate_timeout(timeout_seconds)
    tools = rrv_runtime.discover_tools(ffmpeg=ffmpeg, ffprobe=ffprobe)
    ffmpeg_path, ffprobe_path = _require_runnable_tools(tools)

    probe_result = _safe_runtime_call(
        "structured media probe",
        lambda: rrv_runtime.probe_media(source_path, tools=tools, timeout_seconds=timeout),
    )
    media = probe_result.get("media") if isinstance(probe_result, Mapping) else None
    if not isinstance(media, Mapping):
        raise _tool_error("structured media probe returned invalid media data")
    exact_timing = _safe_runtime_call(
        "exact CFR timing probe",
        lambda: rrv_runtime.probe_exact_video_timing(source_path, ffprobe_path, timeout_seconds=timeout),
    )
    if not isinstance(exact_timing, Mapping):
        raise _tool_error("exact CFR timing probe returned invalid timing data")
    merged_media = _merge_exact_timing(media, exact_timing)
    _require_zero_rotation(merged_media)
    try:
        media_info = rrv_compile._media_info(merged_media, require_exact_timing=True)
    except rrv_runtime.RRVError:
        raise
    except Exception as exc:  # pragma: no cover - guard compiler/private boundary.
        raise _capability("S1 media validation failed") from exc
    if audio_mode == "preserve" and not media_info.audio_available:
        raise _capability(
            "preserve audio requires a source audio stream",
            details={"capability": "preserve_source_audio"},
        )
    if slot_count_hint is not None and slot_count_hint > media_info.duration_frames:
        raise _invalid("slot_count_hint cannot exceed the exact source frame count")

    source_rect = _centered_source_rect(media_info.width, media_info.height, profiles)
    analysis_height = _analysis_height(source_rect, analysis_width, media_info.duration_frames)
    source_sha256 = rrv_analyze.sha256_file(source_path)

    stage: _StageDirectory | None = None
    try:
        stage = _new_staging_directory(root, "proposal")
        raw_path = _stage_path(root, stage, "analysis.gray")
        grayscale_command = rrv_compile.build_grayscale_extraction_command(
            source_path,
            ffmpeg_path,
            source_rect,
            analysis_width,
            analysis_height,
            media_info.duration_frames,
            raw_path,
        )
        _run_output(stage, grayscale_command, raw_path, timeout, "grayscale reference analysis")
        expected_bytes = analysis_width * analysis_height * media_info.duration_frames
        if _stage_regular_file_size(stage, raw_path, "local grayscale analysis") != expected_bytes:
            raise _capability(
                "grayscale analysis did not produce the exact expected frame bytes",
                details={"capability": "exact_grayscale_analysis"},
            )

        row_activity = _temporal_row_activity(
            raw_path,
            analysis_width,
            analysis_height,
            media_info.duration_frames,
            stage=stage,
        )
        carousel_boundaries = _boundary_candidates(row_activity, source_rect["height"])
        carousel_y = int(carousel_boundaries[0]["y"])
        scores = _subject_transition_scores(
            raw_path,
            analysis_width,
            analysis_height,
            media_info.duration_frames,
            carousel_y,
            source_rect["height"],
            stage=stage,
        )
        peaks = _transition_peaks(scores, media_info.fps)
        slot_candidates, slot_count = _slot_count_candidates(
            peaks=peaks,
            frame_count=media_info.duration_frames,
            fps=media_info.fps,
            hint=slot_count_hint,
        )
        timing_mode, manual_starts, timing_confidence = _manual_timing(
            peaks, slot_count, media_info.duration_frames
        )
        if timing_mode == "manual":
            suggested_starts = list(manual_starts)
        else:
            # Even a uniform fallback is a visible timing suggestion for the
            # reviewer; keep it integer-frame and omit the implicit frame 0.
            suggested_starts = [
                end for _, end in rrv_compile.balanced_ranges(media_info.duration_frames, slot_count)[:-1]
            ]
        representative_frames = _representative_frames(
            media_info.duration_frames, max_evidence_frames, manual_starts, peaks
        )

        frame_items: list[tuple[int, Path]] = []
        for index, frame_number in enumerate(representative_frames, start=1):
            # Keep the private stage flat.  Together with its no-delete guard,
            # this avoids an unguarded child-directory reparse surface.
            frame_path = _stage_path(root, stage, f".evidence-frame-{index:03d}.png")
            command = _build_evidence_frame_command(
                source_path, ffmpeg_path, source_rect, frame_number, frame_path
            )
            _run_output(
                stage,
                command,
                frame_path,
                timeout,
                "exact evidence frame extraction",
                image_output=True,
            )
            frame_items.append((frame_number, frame_path))

        overview_path = _stage_path(root, stage, "overview-contact-sheet.jpg")
        geometry_path = _stage_path(root, stage, "geometry-preview.jpg")
        timing_path = _stage_path(root, stage, "timing-profile.png")
        _create_contact_sheet(frame_items, overview_path, stage)
        _create_geometry_preview(
            frame_items[0][1],
            geometry_path,
            stage,
            source_rect["width"],
            source_rect["height"],
            carousel_y,
        )
        _create_timing_profile(scores, media_info.duration_frames, suggested_starts, timing_path, stage)
        background_color, background_confidence = _background_from_margins(
            [path for _, path in frame_items],
            stage=stage,
            subject_y=carousel_y,
            canvas_width=source_rect["width"],
            canvas_height=source_rect["height"],
        )

        candidate_plan = _candidate_plan(
            template_id=template_id,
            source_rect=source_rect,
            carousel_y=carousel_y,
            slot_count=slot_count,
            timing_mode=timing_mode,
            manual_starts=manual_starts,
            background_color=background_color,
            audio_mode=audio_mode,
            audio_rights_confirmed=audio_rights_confirmed,
            output_profiles=profiles,
            analysis_width=analysis_width,
            max_evidence_frames=max_evidence_frames,
        )
        fingerprint = {
            "sha256": source_sha256,
            "width": media_info.width,
            "height": media_info.height,
            "frame_count": media_info.duration_frames,
            "fps": _stable_number(media_info.fps),
            "has_audio": media_info.audio_available,
        }
        nested_errors = _compiler_plan_errors(candidate_plan, "$.candidate_plan")
        nested_errors.extend(_semantic_compiler_plan_errors(candidate_plan, fingerprint, "$.candidate_plan"))
        if nested_errors:
            _raise_validation_errors("candidate Compiler Plan", _unique_errors(nested_errors))

        evidence_artifacts = {
            "overview_contact_sheet": _published_artifact(root, stage, target, overview_path),
            "geometry_preview": _published_artifact(root, stage, target, geometry_path),
            "timing_profile": _published_artifact(root, stage, target, timing_path),
        }
        confidence = _proposal_confidence(
            source_rect=source_rect,
            media_width=media_info.width,
            media_height=media_info.height,
            boundary_score=float(carousel_boundaries[0]["score"]),
            slot_candidates=slot_candidates,
            slot_count=slot_count,
            timing_confidence=timing_confidence,
            background_confidence=background_confidence,
        )
        proposal = {
            "schema_version": PROPOSAL_SCHEMA_VERSION,
            "template_id": template_id,
            "family": "fixed-subject-carousel",
            "privacy": "local-only",
            "review_required": True,
            "source_fingerprint": fingerprint,
            "candidate_plan": candidate_plan,
            "confidence": confidence,
            "candidates": {
                "carousel_boundaries": carousel_boundaries,
                "slot_counts": slot_candidates,
                "switch_frames": [dict(item) for item in peaks],
            },
            "evidence": {
                "representative_frames": representative_frames,
                "artifacts": evidence_artifacts,
            },
            "limitations": _proposal_limitations(
                audio_mode=audio_mode,
                source_was_cropped=(
                    source_rect["x"] != 0
                    or source_rect["y"] != 0
                    or source_rect["width"] != media_info.width
                    or source_rect["height"] != media_info.height
                ),
                timing_mode=timing_mode,
            ),
        }
        proposal_errors = validate_proposal_data(proposal)
        if proposal_errors:
            _raise_validation_errors("generated proposal", proposal_errors)

        proposal_path = _stage_path(root, stage, "compiler-plan-proposal.json")
        _write_json_new(proposal_path, proposal, label="proposal JSON", stage=stage)
        proposal_hash = _stage_file_sha256(stage, proposal_path)
        review = _review_template(proposal_hash, candidate_plan)
        review_errors = validate_review_data(review)
        if review_errors:
            _raise_validation_errors("generated review template", review_errors)
        review_path = _stage_path(root, stage, "review-decision.template.json")
        _write_json_new(review_path, review, label="review template JSON", stage=stage)

        # Exact evidence frames are transient local inputs to the three public
        # evidence artifacts; never publish a source-derived frame dump.
        for _, frame_path in frame_items:
            _remove_stage_file(stage, frame_path)
        _remove_stage_file(stage, raw_path)

        proposal_artifact = _published_artifact(root, stage, target, proposal_path)
        review_artifact = _published_artifact(root, stage, target, review_path)
        _publish_stage(root, stage, target, label="proposal")
        output_relative = _lexical_relative_output_path(root, target)
        stage = None
        return {
            "schema_version": PROPOSAL_SCHEMA_VERSION,
            "template_id": template_id,
            "output_dir": output_relative,
            "review_required": True,
            "candidate_summary": {
                "slot_count": slot_count,
                "carousel_boundary_count": len(carousel_boundaries),
                "switch_frame_count": len(peaks),
            },
            "artifacts": {
                "proposal": proposal_artifact,
                "review_template": review_artifact,
                **evidence_artifacts,
            },
        }
    except Exception:
        _cleanup_directory(root, stage)
        raise


@dataclass(frozen=True)
class _JsonSnapshot:
    """One stable byte snapshot used for both parsing and review hashing."""

    data: Any
    sha256: str


def _project_file_path(root: Path, value: str | os.PathLike[str], label: str) -> Path:
    """Build a strictly relative packet path without touching an absolute candidate.

    Freezing consumes only packets named by normalized paths relative to the
    already-bound ``project_root``.  In particular, absolute local paths and
    UNC paths are rejected before any candidate ``lstat``/network traversal;
    the subsequent descriptor snapshot performs the no-reparse containment
    checks on the rebuilt local path.
    """

    try:
        requested = Path(value)
        if requested.is_absolute() or requested.drive or requested.root:
            raise _invalid(f"{label} must name an existing file within project_root")
        parts = _relative_parts(requested.as_posix(), label)
    except (TypeError, ValueError, OSError, RuntimeError, rrv_runtime.RRVError) as exc:
        if isinstance(exc, rrv_runtime.RRVError):
            raise _invalid(f"{label} must name an existing file within project_root") from exc
        raise _invalid(f"{label} must name an existing file within project_root") from exc
    return root.joinpath(*parts)


def _safe_project_regular_file(root: Path, path: Path, label: str) -> _DirectoryIdentity:
    """Walk the lexical path and reject links/junctions in every component."""

    try:
        relative = path.relative_to(root)
        parts = _relative_parts(relative.as_posix(), label)
    except (ValueError, rrv_runtime.RRVError) as exc:
        raise _invalid(f"{label} must name an existing file within project_root") from exc
    try:
        _capture_directory_identity(root, "project root")
        current = root
        for component in parts[:-1]:
            current = current / component
            _capture_directory_identity(current, "project-contained artifact directory")
        file_path = current / parts[-1]
        entry = os.lstat(file_path)
    except OSError as exc:
        raise _invalid(f"{label} must name an existing regular file within project_root") from exc
    if _is_link_or_reparse(entry) or not stat.S_ISREG(entry.st_mode) or entry.st_ino == 0:
        raise _invalid(f"{label} must name an existing regular file within project_root")
    return _DirectoryIdentity(path=file_path, device=entry.st_dev, inode=entry.st_ino)


def _read_project_file_snapshot(
    root: Path,
    value: str | os.PathLike[str],
    label: str,
    *,
    maximum_bytes: int,
) -> tuple[Path, bytes]:
    """Read one file through a stable descriptor after a no-reparse path walk."""

    path = _project_file_path(root, value, label)
    expected = _safe_project_regular_file(root, path, label)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags | nofollow)
    except OSError as exc:
        # Some Windows CRTs do not expose O_NOFOLLOW.  The fstat identity
        # comparison below still fails closed if a path entry was swapped.
        if nofollow and getattr(exc, "errno", None) in {22, 95}:  # EINVAL / ENOTSUP without platform imports.
            try:
                descriptor = os.open(path, flags)
            except OSError as retry_exc:
                raise _invalid(f"{label} must name an existing regular file within project_root") from retry_exc
        else:
            raise _invalid(f"{label} must name an existing regular file within project_root") from exc
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            opened = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != expected.device
                or opened.st_ino != expected.inode
            ):
                raise _invalid(f"{label} changed while reading")
            data = handle.read(maximum_bytes + 1)
    except rrv_runtime.RRVError:
        raise
    except (OSError, ValueError) as exc:
        raise _invalid(f"{label} could not be read safely") from exc
    if len(data) > maximum_bytes:
        raise _invalid(f"{label} exceeds the bounded local file size")
    return path, data


def _reject_duplicate_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _load_json_snapshot(root: Path, value: str | os.PathLike[str], label: str) -> _JsonSnapshot:
    _, raw = _read_project_file_snapshot(
        root, value, label, maximum_bytes=_MAX_PROPOSAL_JSON_BYTES
    )
    try:
        data = json.loads(
            raw.decode("utf-8"),
            parse_constant=_strict_json_constant,
            object_pairs_hook=_reject_duplicate_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _invalid(f"{label} is not valid strict JSON") from exc
    return _JsonSnapshot(data=data, sha256=hashlib.sha256(raw).hexdigest())


def _validate_evidence_artifacts(proposal: Mapping[str, Any], root: Path) -> None:
    """Bind all review evidence to normal, contained, hash-matching files."""

    evidence = proposal.get("evidence")
    artifacts = evidence.get("artifacts") if isinstance(evidence, Mapping) else None
    if not isinstance(artifacts, Mapping):
        raise _invalid("proposal evidence artifacts are invalid")
    for name in ("overview_contact_sheet", "geometry_preview", "timing_profile"):
        artifact = artifacts.get(name)
        if not isinstance(artifact, Mapping):
            raise _invalid("proposal evidence artifacts are invalid")
        relative_path = artifact.get("path")
        expected_hash = artifact.get("sha256")
        if not isinstance(relative_path, str) or not isinstance(expected_hash, str) or not _SHA256_RE.fullmatch(expected_hash):
            raise _invalid("proposal evidence artifacts are invalid")
        _, raw = _read_project_file_snapshot(
            root,
            relative_path,
            f"proposal evidence {name}",
            maximum_bytes=_MAX_EVIDENCE_ARTIFACT_BYTES,
        )
        if hashlib.sha256(raw).hexdigest() != expected_hash:
            raise _invalid("proposal evidence artifact hash does not match its proposal record")


def _canonical_json_sha256(value: Any) -> str:
    encoded = rrv_runtime.stable_json_dumps(value, indent=None).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _pointer_token(value: Any) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _json_pointer_changes(before: Any, after: Any, path: str = "") -> list[str]:
    """Return deterministic leaf-oriented JSON Pointer paths for reviewer edits."""

    if isinstance(before, Mapping) and isinstance(after, Mapping):
        changes: list[str] = []
        for key in sorted(set(before) | set(after), key=str):
            token = _pointer_token(key)
            child_path = f"{path}/{token}"
            if key not in before or key not in after:
                changes.append(child_path)
            else:
                changes.extend(_json_pointer_changes(before[key], after[key], child_path))
        return changes
    if isinstance(before, list) and isinstance(after, list):
        changes = []
        shared = min(len(before), len(after))
        for index in range(shared):
            changes.extend(_json_pointer_changes(before[index], after[index], f"{path}/{index}"))
        changes.extend(f"{path}/{index}" for index in range(shared, max(len(before), len(after))))
        return changes
    if before != after:
        return [path or "/"]
    return []


def _approved_review_gates(review: Mapping[str, Any]) -> None:
    if review.get("decision") == "rejected":
        raise _invalid("review decision is rejected; no frozen plan was written")
    if review.get("decision") != "approved":
        raise _invalid("review decision must be approved before freezing")
    if review.get("reviewer_confirmed") is not True:
        raise _invalid("approved review requires reviewer_confirmed=true")
    confirmations = review.get("confirmations")
    if not isinstance(confirmations, Mapping) or any(confirmations.get(key) is not True for key in _CONFIRMATION_KEYS):
        raise _invalid("approved review requires every confirmation to be true")


def _freeze_report(
    *,
    proposal_sha256: str,
    candidate_plan: Mapping[str, Any],
    approved_plan: Mapping[str, Any],
    changes: Sequence[str],
) -> dict[str, Any]:
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "proposal_sha256": proposal_sha256,
        "candidate_plan_sha256": _canonical_json_sha256(candidate_plan),
        "approved_plan_sha256": _canonical_json_sha256(approved_plan),
        "changed_json_pointer_paths": list(changes),
    }


def freeze_plan(
    proposal_path: str | os.PathLike[str],
    review_path: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str],
    output_dir: str | os.PathLike[str] = "frozen-plan",
) -> dict[str, Any]:
    """Freeze an explicitly approved local proposal into a Compiler Plan.

    Reviewers may edit ``approved_plan``.  The report records the exact JSON
    Pointer leaves that changed relative to the proposed candidate plan.
    """

    root = rrv_runtime.require_project_root(project_root)
    target = _direct_child_output_target(root, output_dir)
    proposal_snapshot = _load_json_snapshot(root, proposal_path, "proposal")
    review_snapshot = _load_json_snapshot(root, review_path, "review")
    proposal = proposal_snapshot.data
    review = review_snapshot.data

    proposal_errors = validate_proposal_data(proposal)
    if proposal_errors:
        _raise_validation_errors("proposal", proposal_errors)
    review_errors = validate_review_data(review)
    if review_errors:
        _raise_validation_errors("review", review_errors)
    if not isinstance(proposal, Mapping) or not isinstance(review, Mapping):  # Schema errors already cover this.
        _raise_validation_errors("proposal/review", ["documents must be objects"])

    exact_proposal_hash = proposal_snapshot.sha256
    declared_hash = review.get("proposal_sha256")
    if not isinstance(declared_hash, str) or not _SHA256_RE.fullmatch(declared_hash) or declared_hash != exact_proposal_hash:
        raise _invalid("review proposal_sha256 does not match the exact proposal file")
    _validate_evidence_artifacts(proposal, root)
    _approved_review_gates(review)

    candidate_plan = proposal.get("candidate_plan")
    approved_plan = review.get("approved_plan")
    fingerprint = proposal.get("source_fingerprint")
    candidate_errors = _compiler_plan_errors(candidate_plan, "$.candidate_plan")
    candidate_errors.extend(_semantic_compiler_plan_errors(candidate_plan, fingerprint, "$.candidate_plan"))
    approved_errors = _compiler_plan_errors(approved_plan, "$.approved_plan")
    approved_errors.extend(_semantic_compiler_plan_errors(approved_plan, fingerprint, "$.approved_plan"))
    nested_errors = _unique_errors([*candidate_errors, *approved_errors])
    if nested_errors:
        _raise_validation_errors("candidate or approved Compiler Plan", nested_errors)
    if not isinstance(candidate_plan, Mapping) or not isinstance(approved_plan, Mapping):  # Defensive type narrowing.
        _raise_validation_errors("candidate or approved Compiler Plan", ["plan must be an object"])

    changes = _json_pointer_changes(candidate_plan, approved_plan)
    if len(changes) > 512:
        raise _invalid("reviewer plan override has too many changed JSON paths")
    report = _freeze_report(
        proposal_sha256=exact_proposal_hash,
        candidate_plan=candidate_plan,
        approved_plan=approved_plan,
        changes=changes,
    )

    stage: _StageDirectory | None = None
    try:
        stage = _new_staging_directory(root, "freeze")
        plan_output = _stage_path(root, stage, "compiler-plan.json")
        report_output = _stage_path(root, stage, "freeze-report.json")
        _write_json_new(plan_output, approved_plan, label="frozen Compiler Plan JSON", stage=stage)
        _write_json_new(report_output, report, label="freeze report JSON", stage=stage)
        plan_artifact = _published_artifact(root, stage, target, plan_output)
        report_artifact = _published_artifact(root, stage, target, report_output)
        _publish_stage(root, stage, target, label="frozen plan")
        output_relative = _lexical_relative_output_path(root, target)
        stage = None
        return {
            "schema_version": REVIEW_SCHEMA_VERSION,
            "template_id": approved_plan.get("template_id"),
            "output_dir": output_relative,
            "reviewer_override_paths": changes,
            "artifacts": {
                "compiler_plan": plan_artifact,
                "freeze_report": report_artifact,
            },
        }
    except Exception:
        _cleanup_directory(root, stage)
        raise


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "PROPOSAL_SCHEMA_VERSION",
    "REVIEW_SCHEMA_VERSION",
    "freeze_plan",
    "propose_reference",
    "validate_proposal_data",
    "validate_review_data",
]
