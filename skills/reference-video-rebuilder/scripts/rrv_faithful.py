#!/usr/bin/env python3
"""Fail-closed, local, bitstream-preserving reference-video replicas.

This module deliberately supports one narrow operation: copying an authorized
H.264 MP4 into a metadata-free MP4 without touching its video payload.  It is
not a renderer, caption editor, transcoder, or generic remuxer.  The frozen
plan binds the one source file, its measured media facts, and a manually
reviewed inventory of the burned-in text that must remain exactly present.

No public CLI is defined here.  A caller must load a plan, explicitly call the
executor, and handle :class:`rrv_runtime.RRVError` in its own interface.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable, Mapping, Sequence

try:  # Direct execution from the Skill's scripts directory.
    import rrv_propose
    import rrv_runtime
except ImportError:  # pragma: no cover - package-style imports.
    from . import rrv_propose, rrv_runtime  # type: ignore[no-redef]


FAITHFUL_SCHEMA_VERSION = "0.9.0"
MAX_DURATION_SECONDS = 60.0
DEFAULT_TIMEOUT_SECONDS = 60.0
SHA256_CHUNK_SIZE = 1024 * 1024
_SKILL_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_PATH = _SKILL_ROOT / "assets" / "schemas" / "faithful-rebuild-plan.schema.json"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DATA_HASH_RE = re.compile(r"^SHA256:([0-9a-fA-F]{64})$")
_AUDITED_DIMENSIONS = frozenset(
    {
        (720, 1280),
        (1080, 1920),
        (1280, 720),
        (1920, 1080),
    }
)
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
_WIN32_INVALID_COMPONENT_CHARACTERS = frozenset('<>:"/\\|?*')
_WIN32_RESERVED_DEVICE_STEMS = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CONIN$",
        "CONOUT$",
        "CLOCK$",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
        *(f"COM{suffix}" for suffix in "¹²³"),
        *(f"LPT{suffix}" for suffix in "¹²³"),
    }
)
# MP4 muxers may synthesize these structural labels.  They are not inherited
# source metadata and are the only tags allowed by the post-remux check.
_GENERATED_FORMAT_TAGS = frozenset({"major_brand", "minor_version", "compatible_brands", "encoder"})
_GENERATED_STREAM_TAGS = frozenset({"handler_name", "vendor_id", "encoder"})
_plan_validator: Any | None = None


@dataclass(frozen=True)
class _FileIdentity:
    path: Path
    device: int
    inode: int


@dataclass(frozen=True)
class MediaFacts:
    """The limited fact set that binds a faithful-preservation plan."""

    width: int
    height: int
    fps: float
    frame_count: int
    duration_seconds: float
    has_audio: bool
    audio_stream_count: int
    video_codec: str
    container: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "fps": _stable_number(self.fps),
            "frame_count": self.frame_count,
            "duration_seconds": _stable_number(self.duration_seconds),
            "has_audio": self.has_audio,
            "audio_stream_count": self.audio_stream_count,
            "video_codec": self.video_codec,
            "container": self.container,
        }


@dataclass(frozen=True)
class PayloadHash:
    """A digest of exact FFprobe packet-payload digests for one stream set."""

    sha256: str
    packet_count: int

    def to_dict(self) -> dict[str, Any]:
        return {"sha256": self.sha256, "packet_count": self.packet_count}


def _invalid(message: str, details: Mapping[str, Any] | None = None) -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(rrv_runtime.ERR_INVALID_ARGUMENT, message, details)


def _capability(message: str, details: Mapping[str, Any] | None = None) -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(rrv_runtime.ERR_CAPABILITY_UNAVAILABLE, message, details)


def _tool_error(message: str, details: Mapping[str, Any] | None = None) -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(rrv_runtime.ERR_TOOL_EXECUTION, message, details)


def _strict_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value is not allowed: {value}")


def _reject_duplicate_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def load_plan_json(raw: str | bytes | bytearray) -> dict[str, Any]:
    """Parse one plan while rejecting duplicate object keys and NaN/Infinity.

    A Python mapping cannot retain duplicate JSON keys.  Call this boundary
    when a plan originates as JSON text, then pass the returned mapping to
    :func:`execute_faithful_rebuild`.
    """

    if isinstance(raw, bytearray):
        raw = bytes(raw)
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _invalid("faithful rebuild plan must be UTF-8 JSON") from exc
    if not isinstance(raw, str):
        raise _invalid("faithful rebuild plan JSON must be text or UTF-8 bytes")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=_strict_json_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _invalid("faithful rebuild plan is not strict JSON") from exc
    if not isinstance(value, dict):
        raise _invalid("faithful rebuild plan must be a JSON object")
    validate_faithful_plan(value)
    return value


def _find_nonfinite(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        errors.append(f"{path} must be finite")
    elif isinstance(value, Mapping):
        for key, child in value.items():
            _find_nonfinite(child, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _find_nonfinite(child, f"{path}[{index}]", errors)


def _schema_error_path(error: Any) -> str:
    path = "$"
    for item in error.absolute_path:
        path += f"[{item}]" if isinstance(item, int) else f".{item}"
    return path


def _validator() -> Any:
    global _plan_validator
    if _plan_validator is not None:
        return _plan_validator
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:
        raise _capability("faithful plan JSON Schema validation requires jsonschema") from exc
    try:
        with _SCHEMA_PATH.open("r", encoding="utf-8") as handle:
            schema = json.load(handle, parse_constant=_strict_json_constant)
        Draft202012Validator.check_schema(schema)
        _plan_validator = Draft202012Validator(schema)
    except (OSError, ValueError) as exc:
        raise _capability("faithful rebuild plan schema is unavailable") from exc
    return _plan_validator


def _stable_number(value: float | int) -> float | int:
    number = float(value)
    if not math.isfinite(number):  # pragma: no cover - internal invariant guard.
        raise _invalid("faithful rebuild values must be finite")
    if number == 0:
        return 0
    return int(number) if number.is_integer() else number


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite_positive(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid(f"{field} must be a finite positive number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise _invalid(f"{field} must be a finite positive number")
    return number


def _portable_component(component: str) -> bool:
    if not component or component.endswith((".", " ")):
        return False
    if any(
        ord(character) < 32
        or 0x7F <= ord(character) <= 0x9F
        or character in _WIN32_INVALID_COMPONENT_CHARACTERS
        for character in component
    ):
        return False
    stem = component.split(".", 1)[0].rstrip(" .").upper()
    return stem not in _WIN32_RESERVED_DEVICE_STEMS


def _relative_parts(value: Any, field: str) -> tuple[str, ...]:
    """Accept one literal normalized POSIX path below ``project_root``."""

    if not isinstance(value, str) or not value or len(value) > 512 or "\x00" in value:
        raise _invalid(f"{field} must be a normalized project-root-relative path")
    if "\\" in value or ":" in value or value.startswith("/") or value.startswith("//"):
        raise _invalid(f"{field} must be a normalized project-root-relative path")
    parts = tuple(value.split("/"))
    if (
        not parts
        or value != "/".join(parts)
        or any(part in {"", ".", ".."} or not _portable_component(part) for part in parts)
    ):
        raise _invalid(f"{field} must be a normalized project-root-relative path")
    return parts


def _validate_plan_semantics(plan: Mapping[str, Any]) -> None:
    source = plan["source"]
    if not isinstance(source, Mapping):  # Schema invariant, retained for direct callers.
        raise _invalid("plan.source must be an object")
    _relative_parts(source["path"], "plan.source.path")
    fps = _finite_positive(source["fps"], "plan.source.fps")
    duration = _finite_positive(source["duration_seconds"], "plan.source.duration_seconds")
    frame_count = source["frame_count"]
    if not _is_int(frame_count) or frame_count < 1:
        raise _invalid("plan.source.frame_count must be a positive integer")
    if duration > MAX_DURATION_SECONDS:
        raise _invalid("plan.source.duration_seconds must not exceed 60 seconds")
    expected_duration = frame_count / fps
    if not math.isclose(duration, expected_duration, rel_tol=1e-7, abs_tol=1e-6):
        raise _invalid("plan.source.duration_seconds must equal frame_count / fps")

    inventory = plan["text_inventory"]
    if not isinstance(inventory, list):  # Schema invariant.
        raise _invalid("plan.text_inventory must be an array")
    source_width = source["width"]
    source_height = source["height"]
    identifiers: set[str] = set()
    for index, item in enumerate(inventory):
        if not isinstance(item, Mapping):
            raise _invalid(f"plan.text_inventory[{index}] must be an object")
        identifier = item["id"]
        if identifier in identifiers:
            raise _invalid("plan.text_inventory ids must be unique")
        identifiers.add(identifier)
        start = item["start_frame"]
        end = item["end_frame"]
        if not _is_int(start) or not _is_int(end) or start < 0 or start >= end:
            raise _invalid(f"plan.text_inventory[{index}] must use a non-empty half-open frame range")
        if end > frame_count:
            raise _invalid(f"plan.text_inventory[{index}] frame range exceeds plan.source.frame_count")
        region = item["region"]
        if not isinstance(region, Mapping):
            raise _invalid(f"plan.text_inventory[{index}].region must be an object")
        x, y, width, height = (region["x"], region["y"], region["width"], region["height"])
        if not all(_is_int(value) for value in (x, y, width, height)) or x < 0 or y < 0 or width < 1 or height < 1:
            raise _invalid(f"plan.text_inventory[{index}].region must use positive pixel coordinates")
        if x + width > source_width or y + height > source_height:
            raise _invalid(f"plan.text_inventory[{index}].region must remain within plan.source dimensions")
        if item["human_reviewed"] is not True:
            raise _invalid(f"plan.text_inventory[{index}] must be human reviewed")


def validate_faithful_plan(plan: Mapping[str, Any]) -> None:
    """Validate the frozen 0.9.0 shape and semantic text/timing constraints."""

    if not isinstance(plan, Mapping):
        raise _invalid("faithful rebuild plan must be an object")
    nonfinite: list[str] = []
    _find_nonfinite(plan, "$", nonfinite)
    if nonfinite:
        raise _invalid("faithful rebuild plan contains non-finite numbers", {"errors": nonfinite[:8]})
    validator = _validator()
    errors = sorted(
        validator.iter_errors(plan),
        key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
    )
    if errors:
        raise _invalid(
            "faithful rebuild plan did not pass JSON Schema validation",
            {"errors": [f"{_schema_error_path(item)}: {item.message}" for item in errors[:16]]},
        )
    _validate_plan_semantics(plan)


def _is_link_or_reparse(stat_result: os.stat_result) -> bool:
    attributes = getattr(stat_result, "st_file_attributes", 0)
    return stat.S_ISLNK(stat_result.st_mode) or (
        isinstance(attributes, int) and bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)
    )


def _safe_directory(path: Path, label: str) -> _FileIdentity:
    try:
        entry = os.lstat(path)
    except OSError as exc:
        raise _invalid(f"{label} must be an existing safe directory") from exc
    if _is_link_or_reparse(entry) or not stat.S_ISDIR(entry.st_mode):
        raise _invalid(f"{label} must be an existing safe directory")
    if not isinstance(entry.st_ino, int) or entry.st_ino == 0:
        raise _capability(f"{label} must expose a stable local filesystem identity")
    return _FileIdentity(path=path, device=entry.st_dev, inode=entry.st_ino)


def _safe_project_root(value: str | os.PathLike[str]) -> Path:
    """Bind a physical local root without following symlink/reparse components."""

    try:
        raw = os.fspath(value)
    except TypeError as exc:
        raise _invalid("project_root must be an existing safe directory") from exc
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise _invalid("project_root must be an existing safe directory")
    try:
        root = Path(os.path.abspath(raw))
    except (OSError, ValueError, RuntimeError) as exc:
        raise _invalid("project_root must be an existing safe directory") from exc
    if str(root.drive).startswith("\\\\"):
        raise _invalid("project_root must be a local directory")
    anchor = Path(root.anchor)
    if not root.anchor:
        raise _invalid("project_root must be an existing safe directory")
    _safe_directory(anchor, "project root anchor")
    current = anchor
    anchor_part_count = len(anchor.parts)
    try:
        components = root.parts[anchor_part_count:]
    except (AttributeError, TypeError) as exc:  # pragma: no cover - Path invariant.
        raise _invalid("project_root must be an existing safe directory") from exc
    for component in components:
        current = current / component
        _safe_directory(current, "project root")
    return root


def _safe_regular_file(root: Path, path: Path, label: str) -> _FileIdentity:
    """Walk an already-composed root-relative path without following links."""

    try:
        relative = path.relative_to(root)
        parts = _relative_parts(relative.as_posix(), label)
    except (ValueError, rrv_runtime.RRVError) as exc:
        raise _invalid(f"{label} must name a project-contained regular file") from exc
    _safe_directory(root, "project root")
    current = root
    for component in parts[:-1]:
        current = current / component
        _safe_directory(current, f"{label} parent")
    candidate = current / parts[-1]
    try:
        entry = os.lstat(candidate)
    except OSError as exc:
        raise _invalid(f"{label} must name an existing regular file") from exc
    if (
        _is_link_or_reparse(entry)
        or not stat.S_ISREG(entry.st_mode)
        or entry.st_ino == 0
        or entry.st_nlink != 1
    ):
        raise _invalid(f"{label} must name an existing non-reparse regular file")
    return _FileIdentity(path=candidate, device=entry.st_dev, inode=entry.st_ino)


def _assert_file_identity(root: Path, expected: _FileIdentity, label: str) -> None:
    current = _safe_regular_file(root, expected.path, label)
    if current.device != expected.device or current.inode != expected.inode:
        raise _invalid(f"{label} changed while the faithful rebuild was running")


def _sha256_bound_file(root: Path, expected: _FileIdentity, label: str) -> str:
    """Hash a descriptor only after it is bound to the checked file identity."""

    _assert_file_identity(root, expected, label)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(expected.path, flags | nofollow)
    except OSError as exc:
        if nofollow and getattr(exc, "errno", None) in {22, 95}:
            try:
                descriptor = os.open(expected.path, flags)
            except OSError as retry_exc:
                raise _invalid(f"{label} could not be opened safely") from retry_exc
        else:
            raise _invalid(f"{label} could not be opened safely") from exc
    digest = hashlib.sha256()
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            opened = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or opened.st_dev != expected.device
                or opened.st_ino != expected.inode
            ):
                raise _invalid(f"{label} changed while it was being read")
            while chunk := handle.read(SHA256_CHUNK_SIZE):
                digest.update(chunk)
    except rrv_runtime.RRVError:
        raise
    except OSError as exc:
        raise _invalid(f"{label} could not be read safely") from exc
    _assert_file_identity(root, expected, label)
    return digest.hexdigest()


def _canonical_plan_sha256(plan: Mapping[str, Any]) -> str:
    try:
        encoded = rrv_runtime.stable_json_dumps(plan, indent=None).encode("utf-8")
    except (TypeError, ValueError) as exc:  # pragma: no cover - validation already rejects these.
        raise _invalid("faithful rebuild plan cannot be canonically hashed") from exc
    return hashlib.sha256(encoded).hexdigest()


def _require_runtime_tools(
    tools: rrv_runtime.RuntimeTools | None,
    *,
    ffmpeg: str | os.PathLike[str] | None,
    ffprobe: str | os.PathLike[str] | None,
) -> tuple[rrv_runtime.RuntimeTools, str, str]:
    runtime_tools = tools or rrv_runtime.discover_tools(ffmpeg=ffmpeg, ffprobe=ffprobe)
    if not isinstance(runtime_tools, rrv_runtime.RuntimeTools):
        raise _invalid("tools must be an rrv_runtime.RuntimeTools instance")
    if not runtime_tools.ffmpeg.path:
        raise rrv_runtime.RRVError(rrv_runtime.ERR_TOOL_NOT_FOUND, "local FFmpeg is required")
    if not runtime_tools.ffprobe.path:
        raise rrv_runtime.RRVError(rrv_runtime.ERR_TOOL_NOT_FOUND, "local FFprobe is required")
    return runtime_tools, runtime_tools.ffmpeg.path, runtime_tools.ffprobe.path


def _finite_rate(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _capability(f"{field} must be a finite positive number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise _capability(f"{field} must be a finite positive number")
    return number


def _positive_int(value: Any, field: str) -> int:
    if not _is_int(value) or value < 1:
        raise _capability(f"{field} must be a positive integer")
    return value


def _format_is_mp4(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return "mp4" in {item.strip().lower() for item in value.split(",")}


def _extract_media_facts(probe_result: Mapping[str, Any], exact_timing: Mapping[str, Any]) -> MediaFacts:
    """Fail closed unless FFprobe established the narrow source capability."""

    if not isinstance(probe_result, Mapping):
        raise _tool_error("media probe returned invalid data")
    media = probe_result.get("media")
    if not isinstance(media, Mapping):
        raise _tool_error("media probe returned no normalized media facts")
    format_data = media.get("format")
    if not isinstance(format_data, Mapping) or not _format_is_mp4(format_data.get("format_name")):
        raise _capability("faithful rebuild supports only MP4 containers")
    streams = media.get("streams")
    if not isinstance(streams, list):
        raise _tool_error("media probe returned no stream array")
    videos = [item for item in streams if isinstance(item, Mapping) and item.get("type") == "video"]
    if len(videos) != 1:
        raise _capability("faithful rebuild requires exactly one video stream")
    video = videos[0]
    codec = video.get("codec_name")
    if not isinstance(codec, str) or codec.lower() != "h264":
        raise _capability("faithful rebuild supports only H.264 video")
    width = _positive_int(video.get("width"), "media.video.width")
    height = _positive_int(video.get("height"), "media.video.height")
    if (width, height) not in _AUDITED_DIMENSIONS:
        raise _capability("faithful rebuild supports only one of the four audited dimensions")
    rotation = video.get("rotation_degrees")
    if rotation is not None:
        if isinstance(rotation, bool) or not isinstance(rotation, (int, float)) or not math.isfinite(float(rotation)):
            raise _capability("media.video.rotation_degrees must be finite when present")
        normalized_rotation = float(rotation) % 360.0
        if not math.isclose(normalized_rotation, 0.0, abs_tol=1e-9):
            raise _capability("faithful rebuild rejects rotated source video")
    declared_rates = [
        _finite_rate(video[key], f"media.video.{key}")
        for key in ("frame_rate", "average_frame_rate")
        if video.get(key) is not None
    ]
    if not declared_rates:
        raise _capability("faithful rebuild requires a declared constant frame rate")
    if len(declared_rates) == 2 and not math.isclose(
        declared_rates[0], declared_rates[1], rel_tol=1e-7, abs_tol=1e-7
    ):
        raise _capability("faithful rebuild rejects variable-frame-rate video")
    if not isinstance(exact_timing, Mapping) or exact_timing.get("cfr_confirmed") is not True:
        raise _capability("faithful rebuild requires FFprobe-confirmed CFR timing")
    fps = _finite_rate(exact_timing.get("fps"), "exact_timing.fps")
    frame_count = _positive_int(exact_timing.get("frame_count"), "exact_timing.frame_count")
    duration = _finite_rate(exact_timing.get("duration_seconds"), "exact_timing.duration_seconds")
    if duration > MAX_DURATION_SECONDS + 1e-9:
        raise _capability("faithful rebuild supports source videos of at most 60 seconds")
    if not math.isclose(fps, declared_rates[-1], rel_tol=1e-7, abs_tol=1e-7):
        raise _capability("FFprobe CFR timing does not match the declared frame rate")
    if not math.isclose(duration, frame_count / fps, rel_tol=1e-7, abs_tol=1e-6):
        raise _capability("FFprobe CFR timing does not agree with frame count and frame rate")
    audio_count = sum(1 for item in streams if isinstance(item, Mapping) and item.get("type") == "audio")
    unsupported = [
        item.get("type")
        for item in streams
        if isinstance(item, Mapping) and item.get("type") not in {"video", "audio"}
    ]
    if unsupported:
        raise _capability("faithful rebuild does not omit subtitle, data, or attachment streams")
    return MediaFacts(
        width=width,
        height=height,
        fps=fps,
        frame_count=frame_count,
        duration_seconds=duration,
        has_audio=audio_count > 0,
        audio_stream_count=audio_count,
        video_codec="h264",
        container="mp4",
    )


def _assert_plan_media_facts(plan: Mapping[str, Any], facts: MediaFacts) -> None:
    expected = plan["source"]
    mismatches: list[str] = []
    for field in ("width", "height", "frame_count", "has_audio"):
        if expected[field] != getattr(facts, field):
            mismatches.append(field)
    if not math.isclose(float(expected["fps"]), facts.fps, rel_tol=1e-7, abs_tol=1e-7):
        mismatches.append("fps")
    if not math.isclose(
        float(expected["duration_seconds"]), facts.duration_seconds, rel_tol=1e-7, abs_tol=1e-6
    ):
        mismatches.append("duration_seconds")
    if mismatches:
        raise _invalid("source media facts do not match the frozen faithful rebuild plan", {"fields": mismatches})


def _assert_replica_media_facts(source: MediaFacts, replica: MediaFacts, audio_mode: str) -> None:
    mismatches: list[str] = []
    for field in ("width", "height", "frame_count", "video_codec", "container"):
        if getattr(source, field) != getattr(replica, field):
            mismatches.append(field)
    if not math.isclose(source.fps, replica.fps, rel_tol=1e-7, abs_tol=1e-7):
        mismatches.append("fps")
    if not math.isclose(source.duration_seconds, replica.duration_seconds, rel_tol=1e-7, abs_tol=1e-6):
        mismatches.append("duration_seconds")
    if audio_mode == "preserve-bitstream":
        if source.audio_stream_count != replica.audio_stream_count:
            mismatches.append("audio_stream_count")
    elif replica.has_audio:
        mismatches.append("audio_mode")
    if mismatches:
        raise _tool_error("replica media facts do not preserve the approved source", {"fields": mismatches})


def build_faithful_remux_command(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
    ffmpeg: str | os.PathLike[str],
    *,
    audio_mode: str,
) -> list[str]:
    """Return an argv-only stream-copy command with no input transformation."""

    if audio_mode not in {"preserve-bitstream", "mute"}:
        raise _invalid("audio_mode must be preserve-bitstream or mute")
    try:
        source_value = os.fspath(source)
        output_value = os.fspath(output)
        ffmpeg_value = os.fspath(ffmpeg)
    except TypeError as exc:
        raise _invalid("source, output, and ffmpeg must be filesystem paths") from exc
    if not all(isinstance(item, str) and item and "\x00" not in item for item in (source_value, output_value, ffmpeg_value)):
        raise _invalid("source, output, and ffmpeg must be non-empty paths")
    command = [
        ffmpeg_value,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-n",
        "-copyts",
        "-i",
        source_value,
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
    ]
    if audio_mode == "preserve-bitstream":
        command.extend(["-map", "0:a?", "-c:a", "copy"])
    else:
        command.append("-an")
    # Disable automatic global and per-stream metadata copying, then remove
    # chapters.  No video filters, rate changes, text overlays, or encoders
    # are allowed in this branch.
    command.extend(
        [
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-map_metadata:s:v",
            "-1",
            "-map_metadata:s:a",
            "-1",
            "-map_chapters",
            "-1",
            output_value,
        ]
    )
    return command


def build_payload_hash_command(
    source: str | os.PathLike[str], ffprobe: str | os.PathLike[str], stream_selector: str
) -> list[str]:
    """Return an argv-only FFprobe packet-payload hashing command."""

    if stream_selector not in {"v:0", "a"}:
        raise _invalid("stream_selector must be v:0 or a")
    try:
        source_value = os.fspath(source)
        ffprobe_value = os.fspath(ffprobe)
    except TypeError as exc:
        raise _invalid("source and ffprobe must be filesystem paths") from exc
    if not all(isinstance(item, str) and item and "\x00" not in item for item in (source_value, ffprobe_value)):
        raise _invalid("source and ffprobe must be non-empty paths")
    return [
        ffprobe_value,
        "-v",
        "error",
        "-select_streams",
        stream_selector,
        "-show_packets",
        "-show_entries",
        "packet=stream_index,pts,dts,duration,data_hash",
        "-show_data_hash",
        "sha256",
        "-of",
        "json",
        source_value,
    ]


def _payload_hash_from_ffprobe_json(raw: Any) -> PayloadHash:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("packets"), list):
        raise _tool_error("FFprobe payload hash output is invalid")
    packets = raw["packets"]
    if not packets:
        raise _tool_error("FFprobe found no packets for a required payload hash")
    stream_indices = sorted(
        {
            packet.get("stream_index")
            for packet in packets
            if isinstance(packet, Mapping) and _is_int(packet.get("stream_index"))
        }
    )
    if not stream_indices:
        raise _tool_error("FFprobe payload hash packet records are invalid")
    # An MP4 remux may harmlessly renumber streams. Bind each selected stream
    # to a stable ordinal rather than its container-level absolute index.
    stream_ordinals = {stream_index: ordinal for ordinal, stream_index in enumerate(stream_indices)}
    digest = hashlib.sha256()
    for index, packet in enumerate(packets):
        if not isinstance(packet, Mapping) or not _is_int(packet.get("stream_index")):
            raise _tool_error("FFprobe payload hash packet record is invalid")
        timing = tuple(packet.get(field) for field in ("pts", "dts", "duration"))
        if not all(_is_int(value) for value in timing):
            raise _tool_error("FFprobe payload hash packet timing is incomplete")
        data_hash = packet.get("data_hash")
        match = _DATA_HASH_RE.fullmatch(data_hash) if isinstance(data_hash, str) else None
        if not match:
            raise _tool_error("FFprobe did not emit SHA-256 packet payload hashes")
        # Preserve selected-stream identity and packet order while allowing a
        # source audio-0/video-1 layout to become video-0/audio-1 on remux.
        digest.update(stream_ordinals[packet["stream_index"]].to_bytes(4, byteorder="big"))
        digest.update(index.to_bytes(8, byteorder="big", signed=False))
        for value in timing:
            digest.update(value.to_bytes(8, byteorder="big", signed=True))
        digest.update(bytes.fromhex(match.group(1)))
    return PayloadHash(sha256=digest.hexdigest(), packet_count=len(packets))


def _run_command(
    command: Sequence[str],
    *,
    timeout_seconds: float,
    runner: Callable[..., Any] | None,
) -> Any:
    try:
        result = (
            runner(command, timeout_seconds=timeout_seconds, check=True)
            if runner is not None
            else rrv_runtime.run_command(command, timeout_seconds=timeout_seconds, check=True)
        )
    except rrv_runtime.RRVError:
        raise
    except Exception as exc:
        raise _tool_error("local media command could not be executed") from exc
    returncode = getattr(result, "returncode", 0)
    if isinstance(returncode, int) and returncode != 0:
        raise _tool_error("local media command returned a non-zero status", {"returncode": returncode})
    return result


def stream_payload_hash(
    source: str | os.PathLike[str],
    ffprobe: str | os.PathLike[str],
    stream_selector: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    runner: Callable[..., Any] | None = None,
) -> PayloadHash:
    """Hash compressed packet payloads, never decoded video or audio samples."""

    timeout = rrv_runtime.validate_timeout(timeout_seconds)
    result = _run_command(
        build_payload_hash_command(source, ffprobe, stream_selector),
        timeout_seconds=timeout,
        runner=runner,
    )
    stdout = getattr(result, "stdout", None)
    if not isinstance(stdout, str):
        raise _tool_error("FFprobe payload hash command returned no JSON output")
    try:
        raw = json.loads(stdout, parse_constant=_strict_json_constant)
    except (ValueError, json.JSONDecodeError) as exc:
        raise _tool_error("FFprobe payload hash command returned invalid JSON") from exc
    return _payload_hash_from_ffprobe_json(raw)


def build_metadata_probe_command(
    source: str | os.PathLike[str], ffprobe: str | os.PathLike[str]
) -> list[str]:
    """Return an argv-only command that exposes only output tag maps."""

    try:
        source_value = os.fspath(source)
        ffprobe_value = os.fspath(ffprobe)
    except TypeError as exc:
        raise _invalid("source and ffprobe must be filesystem paths") from exc
    if not all(isinstance(item, str) and item and "\x00" not in item for item in (source_value, ffprobe_value)):
        raise _invalid("source and ffprobe must be non-empty paths")
    return [
        ffprobe_value,
        "-v",
        "error",
        "-show_entries",
        "format_tags:stream=index,codec_type:stream_tags",
        "-of",
        "json",
        source_value,
    ]


def metadata_is_stripped(raw: Any) -> bool:
    """Return whether a metadata probe contains only unavoidable muxer tags."""

    if not isinstance(raw, Mapping):
        return False
    format_data = raw.get("format")
    if not isinstance(format_data, Mapping):
        return False
    tags = format_data.get("tags", {})
    if not isinstance(tags, Mapping):
        return False
    for key, value in tags.items():
        if not isinstance(key, str) or not isinstance(value, str):
            return False
        normalized = key.lower()
        if normalized not in _GENERATED_FORMAT_TAGS:
            return False
        if normalized == "major_brand" and re.fullmatch(r"[A-Za-z0-9]{4}", value) is None:
            return False
        if normalized == "minor_version" and re.fullmatch(r"[0-9]{1,10}", value) is None:
            return False
        if normalized == "compatible_brands" and re.fullmatch(r"[A-Za-z0-9]{4,64}", value) is None:
            return False
        if normalized == "encoder" and re.fullmatch(r"Lavf[0-9.]+", value) is None:
            return False
    streams = raw.get("streams", [])
    if not isinstance(streams, list) or not streams:
        return False
    video_streams = 0
    for stream in streams:
        if not isinstance(stream, Mapping):
            return False
        codec_type = stream.get("codec_type")
        if codec_type not in {"video", "audio"}:
            return False
        video_streams += int(codec_type == "video")
        tags = stream.get("tags", {})
        if not isinstance(tags, Mapping):
            return False
        for key, value in tags.items():
            if not isinstance(key, str) or not isinstance(value, str):
                return False
            normalized = key.lower()
            if normalized not in _GENERATED_STREAM_TAGS | {"language"}:
                return False
            if normalized == "language" and value != "und":
                return False
            if normalized == "handler_name":
                expected_handler = "VideoHandler" if codec_type == "video" else "SoundHandler"
                if value != expected_handler:
                    return False
            if normalized == "vendor_id" and re.fullmatch(r"(?:\[0\]){4}|[A-Za-z0-9]{4}", value) is None:
                return False
            if normalized == "encoder" and re.fullmatch(r"Lavc[0-9.]+", value) is None:
                return False
    return video_streams == 1


def _probe_metadata(
    source: Path,
    ffprobe: str,
    *,
    timeout_seconds: float,
    runner: Callable[..., Any] | None,
) -> Mapping[str, Any]:
    result = _run_command(
        build_metadata_probe_command(source, ffprobe), timeout_seconds=timeout_seconds, runner=runner
    )
    stdout = getattr(result, "stdout", None)
    if not isinstance(stdout, str):
        raise _tool_error("FFprobe metadata command returned no JSON output")
    try:
        raw = json.loads(stdout, parse_constant=_strict_json_constant)
    except (ValueError, json.JSONDecodeError) as exc:
        raise _tool_error("FFprobe metadata command returned invalid JSON") from exc
    if not isinstance(raw, Mapping):
        raise _tool_error("FFprobe metadata command returned invalid data")
    return raw


def _probe_facts(
    source: Path,
    *,
    runtime_tools: rrv_runtime.RuntimeTools,
    ffprobe: str,
    timeout_seconds: float,
    probe_media_fn: Callable[..., Mapping[str, Any]] | None,
    exact_timing_fn: Callable[..., Mapping[str, Any]] | None,
) -> MediaFacts:
    media_probe = probe_media_fn or rrv_runtime.probe_media
    timing_probe = exact_timing_fn or rrv_runtime.probe_exact_video_timing
    try:
        probe_result = media_probe(source, tools=runtime_tools, timeout_seconds=timeout_seconds)
        exact_timing = timing_probe(source, ffprobe, timeout_seconds=timeout_seconds)
    except rrv_runtime.RRVError:
        raise
    except Exception as exc:
        raise _tool_error("local media inspection failed") from exc
    return _extract_media_facts(probe_result, exact_timing)


def _payload_hash(
    source: Path,
    selector: str,
    *,
    ffprobe: str,
    timeout_seconds: float,
    runner: Callable[..., Any] | None,
    payload_hash_fn: Callable[..., PayloadHash] | None,
) -> PayloadHash:
    if payload_hash_fn is not None:
        try:
            value = payload_hash_fn(source, ffprobe, selector, timeout_seconds=timeout_seconds)
        except rrv_runtime.RRVError:
            raise
        except Exception as exc:
            raise _tool_error("local payload hashing failed") from exc
        if not isinstance(value, PayloadHash):
            raise _tool_error("payload_hash_fn must return a PayloadHash")
        return value
    return stream_payload_hash(
        source, ffprobe, selector, timeout_seconds=timeout_seconds, runner=runner
    )


def _assert_payload_equal(kind: str, source: PayloadHash, replica: PayloadHash) -> None:
    if source.sha256 != replica.sha256 or source.packet_count != replica.packet_count:
        raise _tool_error(f"replica {kind} packet payload does not match the source bitstream")


def execute_faithful_rebuild(
    plan: Mapping[str, Any],
    project_root: str | os.PathLike[str],
    output_dir: str | os.PathLike[str] = "faithful-rebuild",
    *,
    tools: rrv_runtime.RuntimeTools | None = None,
    ffmpeg: str | os.PathLike[str] | None = None,
    ffprobe: str | os.PathLike[str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    runner: Callable[..., Any] | None = None,
    probe_media_fn: Callable[..., Mapping[str, Any]] | None = None,
    exact_timing_fn: Callable[..., Mapping[str, Any]] | None = None,
    payload_hash_fn: Callable[..., PayloadHash] | None = None,
    metadata_probe_fn: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Atomically publish a metadata-free bitstream copy of one approved source.

    The authorization gate is deliberately first.  When it is absent or
    false, this function does not resolve a root, touch a source path, inspect
    tools, create a stage, or create a visible output directory.
    """

    if not isinstance(plan, Mapping) or plan.get("rights_confirmed") is not True:
        raise _invalid("rights_confirmed must be true before touching a faithful rebuild input")
    validate_faithful_plan(plan)
    timeout = rrv_runtime.validate_timeout(timeout_seconds)
    root = _safe_project_root(project_root)
    source_relative = str(plan["source"]["path"])
    source_parts = _relative_parts(source_relative, "plan.source.path")
    if not source_parts[-1].lower().endswith(".mp4"):
        raise _invalid("plan.source.path must name a local MP4 file")
    source_path = root.joinpath(*source_parts)
    source_identity = _safe_regular_file(root, source_path, "plan.source.path")
    # Validate the final target before any tool or media work.  This neither
    # creates it nor permits a nested/reparse parent.
    target = rrv_propose._direct_child_output_target(root, output_dir)
    runtime_tools, ffmpeg_path, ffprobe_path = _require_runtime_tools(
        tools, ffmpeg=ffmpeg, ffprobe=ffprobe
    )

    source_sha256 = _sha256_bound_file(root, source_identity, "plan.source.path")
    if source_sha256 != plan["source"]["sha256"]:
        raise _invalid("plan.source.sha256 does not match the checked local source")
    source_facts = _probe_facts(
        source_path,
        runtime_tools=runtime_tools,
        ffprobe=ffprobe_path,
        timeout_seconds=timeout,
        probe_media_fn=probe_media_fn,
        exact_timing_fn=exact_timing_fn,
    )
    _assert_plan_media_facts(plan, source_facts)
    # Rebind and rehash after external media inspection to catch in-place
    # content changes as well as pathname/reparse replacement.
    if _sha256_bound_file(root, source_identity, "plan.source.path") != source_sha256:
        raise _invalid("source hash changed during faithful rebuild preflight")

    source_video_payload = _payload_hash(
        source_path,
        "v:0",
        ffprobe=ffprobe_path,
        timeout_seconds=timeout,
        runner=runner,
        payload_hash_fn=payload_hash_fn,
    )
    source_audio_payload = (
        _payload_hash(
            source_path,
            "a",
            ffprobe=ffprobe_path,
            timeout_seconds=timeout,
            runner=runner,
            payload_hash_fn=payload_hash_fn,
        )
        if source_facts.has_audio
        else None
    )
    if _sha256_bound_file(root, source_identity, "plan.source.path") != source_sha256:
        raise _invalid("source hash changed during faithful rebuild preflight")

    stage: Any | None = None
    try:
        stage = rrv_propose._new_staging_directory(root, "faithful")
        replica_path = rrv_propose._stage_path(root, stage, "replica.mp4")
        command = build_faithful_remux_command(
            source_path, replica_path, ffmpeg_path, audio_mode=plan["audio_mode"]
        )
        _run_command(command, timeout_seconds=timeout, runner=runner)
        rrv_propose._assert_stage_regular_file(stage, replica_path, "faithful replica")
        if _sha256_bound_file(root, source_identity, "plan.source.path") != source_sha256:
            raise _invalid("source hash changed during faithful rebuild execution")

        replica_facts = _probe_facts(
            replica_path,
            runtime_tools=runtime_tools,
            ffprobe=ffprobe_path,
            timeout_seconds=timeout,
            probe_media_fn=probe_media_fn,
            exact_timing_fn=exact_timing_fn,
        )
        _assert_replica_media_facts(source_facts, replica_facts, str(plan["audio_mode"]))
        replica_video_payload = _payload_hash(
            replica_path,
            "v:0",
            ffprobe=ffprobe_path,
            timeout_seconds=timeout,
            runner=runner,
            payload_hash_fn=payload_hash_fn,
        )
        _assert_payload_equal("video", source_video_payload, replica_video_payload)
        if plan["audio_mode"] == "preserve-bitstream" and source_audio_payload is not None:
            replica_audio_payload = _payload_hash(
                replica_path,
                "a",
                ffprobe=ffprobe_path,
                timeout_seconds=timeout,
                runner=runner,
                payload_hash_fn=payload_hash_fn,
            )
            _assert_payload_equal("audio", source_audio_payload, replica_audio_payload)
        else:
            replica_audio_payload = None

        if metadata_probe_fn is not None:
            try:
                metadata = metadata_probe_fn(replica_path, ffprobe_path, timeout_seconds=timeout)
            except rrv_runtime.RRVError:
                raise
            except Exception as exc:
                raise _tool_error("local metadata inspection failed") from exc
        else:
            metadata = _probe_metadata(
                replica_path,
                ffprobe_path,
                timeout_seconds=timeout,
                runner=runner,
            )
        if not metadata_is_stripped(metadata):
            raise _tool_error("replica metadata was not fully stripped")

        replica_sha256 = _sha256_bound_file(
            root,
            _safe_regular_file(root, replica_path, "staged faithful replica"),
            "staged faithful replica",
        )
        output_relative = rrv_propose._lexical_relative_output_path(root, target)
        replica_relative = rrv_propose._lexical_relative_output_path(root, target / "replica.mp4")
        summary_relative = rrv_propose._lexical_relative_output_path(
            root, target / "rebuild-summary.json"
        )
        summary: dict[str, Any] = {
            "schema_version": FAITHFUL_SCHEMA_VERSION,
            "completion": "faithful_source_preservation",
            "output_dir": output_relative,
            "replica_path": replica_relative,
            "rebuild_summary_path": summary_relative,
            "replica_sha256": replica_sha256,
            "plan_sha256": _canonical_plan_sha256(plan),
            "source": {
                "path": source_relative,
                "sha256": source_sha256,
            },
            "media_facts": source_facts.to_dict(),
            "payload_hashes": {
                "video": {
                    "source": source_video_payload.to_dict(),
                    "replica": replica_video_payload.to_dict(),
                },
                "audio": {
                    "mode": plan["audio_mode"],
                    "source": source_audio_payload.to_dict() if source_audio_payload else None,
                    "replica": replica_audio_payload.to_dict() if replica_audio_payload else None,
                },
            },
            "text_inventory_count": len(plan["text_inventory"]),
            "visible_text_policy": "preserve-exact",
            "metadata": {"strip_all": True, "verified": True},
        }
        summary_path = rrv_propose._stage_path(root, stage, "rebuild-summary.json")
        rrv_propose._write_json_new(
            summary_path,
            summary,
            label="faithful rebuild summary",
            stage=stage,
        )
        rrv_propose._publish_stage(root, stage, target, label="faithful rebuild")
        stage = None
        return summary
    except Exception:
        rrv_propose._cleanup_directory(root, stage)
        raise


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "FAITHFUL_SCHEMA_VERSION",
    "MAX_DURATION_SECONDS",
    "MediaFacts",
    "PayloadHash",
    "build_faithful_remux_command",
    "build_metadata_probe_command",
    "build_payload_hash_command",
    "execute_faithful_rebuild",
    "load_plan_json",
    "metadata_is_stripped",
    "stream_payload_hash",
    "validate_faithful_plan",
]
