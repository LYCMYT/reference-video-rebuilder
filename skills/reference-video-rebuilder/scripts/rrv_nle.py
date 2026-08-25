#!/usr/bin/env python3
"""Fail-closed Jianying-compatible MP4 derivative delivery.

This module owns one deliberately narrow operation: transcode an explicitly
authorized, project-contained MP4 into the fixed ``jianying-compatible-v1``
profile.  It is not a general FFmpeg wrapper.  The source must be a normal,
non-linked, non-hard-linked file below a safe project root; final output is an
atomically published, direct-child delivery directory.

The profile is intentionally a *derivative*, not a faithful replica.  A
successful report therefore always states ``bitstream_faithful: false``.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping, Sequence

try:  # Direct execution from the Skill's scripts directory.
    import rrv_faithful
    import rrv_propose
    import rrv_runtime
except ImportError:  # pragma: no cover - package-style imports.
    from . import rrv_faithful, rrv_propose, rrv_runtime  # type: ignore[no-redef]


NLE_SCHEMA_VERSION = "0.9.1"
NLE_PROFILE = "jianying-compatible-v1"
DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_DURATION_SECONDS = 60.0
DELIVERY_FILENAME = "jianying-compatible-v1.mp4"
REPORT_FILENAME = "nle-delivery-report.json"
SUPPORTED_DIMENSIONS = frozenset(
    {
        (720, 1280),
        (1080, 1920),
        (1280, 720),
        (1920, 1080),
    }
)
SUPPORTED_CFR_FPS = (24, 25, 30, 50, 60)
MAX_FFPROBE_JSON_BYTES = 4 * 1024 * 1024
MAX_MP4_TOP_LEVEL_ATOMS = 128

_ABSOLUTE_PATH_FRAGMENT_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\|(?:^|[\s\"'(<])/(?:[^\s\"')>]*)|file:(?:/{1,3})?)",
    re.IGNORECASE,
)
_FRAME_PROGRESS_RE = re.compile(r"^frame=(\d+)$")
_GENERATED_FORMAT_TAGS = frozenset({"major_brand", "minor_version", "compatible_brands", "encoder"})
_GENERATED_STREAM_TAGS = frozenset({"language", "handler_name", "vendor_id", "encoder"})


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


def _stable_number(value: float | int) -> float | int:
    number = float(value)
    if not math.isfinite(number):  # pragma: no cover - internal invariant guard.
        raise _tool_error("NLE media facts contain a non-finite number")
    if number == 0:
        return 0
    return int(number) if number.is_integer() else number


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise _capability(f"{field} must be a finite number")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError as exc:
            raise _capability(f"{field} must be a finite number") from exc
    else:
        raise _capability(f"{field} must be a finite number")
    if not math.isfinite(number):
        raise _capability(f"{field} must be a finite number")
    return number


def _positive_int(value: Any, field: str) -> int:
    number = _number(value, field)
    if not number.is_integer() or number < 1:
        raise _capability(f"{field} must be a positive integer")
    return int(number)


def _positive_rate(value: Any, field: str) -> float:
    parsed = rrv_runtime.parse_rational(value)
    if parsed is None or not math.isfinite(parsed) or parsed <= 0:
        raise _capability(f"{field} must be a finite positive frame rate")
    return float(parsed)


def _require_profile(profile: Any) -> str:
    if profile != NLE_PROFILE:
        raise _invalid(f"profile must be {NLE_PROFILE}")
    return NLE_PROFILE


def _require_rights(rights_confirmed: Any) -> None:
    """Make authorization the first executable boundary of every public API."""

    if rights_confirmed is not True:
        raise _invalid("rights_confirmed must be true before touching an NLE delivery input")


def _path_argument(value: str | os.PathLike[str], label: str) -> str:
    try:
        raw = os.fspath(value)
    except TypeError as exc:
        raise _invalid(f"{label} must be a filesystem path") from exc
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise _invalid(f"{label} must be a non-empty filesystem path")
    return raw


def _source_below_root(
    root: Path, source: str | os.PathLike[str], *, label: str
) -> tuple[Path, str]:
    """Compose a lexical root-contained source path without resolving links."""

    raw = _path_argument(source, label)
    candidate = Path(raw)
    # A drive-relative or root-relative spelling has host-dependent semantics.
    # It is neither a useful project-relative input nor a fully absolute path.
    if (candidate.drive or candidate.root) and not candidate.is_absolute():
        raise _invalid(f"{label} must be a project-contained MP4 file")
    try:
        composed = Path(os.path.abspath(raw if candidate.is_absolute() else str(root / candidate)))
        relative = composed.relative_to(root)
        parts = rrv_faithful._relative_parts(relative.as_posix(), label)
    except (OSError, RuntimeError, ValueError, rrv_runtime.RRVError) as exc:
        raise _invalid(f"{label} must be a project-contained MP4 file") from exc
    if Path(parts[-1]).suffix.lower() != ".mp4":
        raise _invalid(f"{label} must name an MP4 file")
    return composed, "/".join(parts)


def _require_runtime_tools(
    tools: rrv_runtime.RuntimeTools | None,
    *,
    ffmpeg: str | os.PathLike[str] | None,
    ffprobe: str | os.PathLike[str] | None,
) -> tuple[rrv_runtime.RuntimeTools, str, str]:
    runtime_tools = tools or rrv_runtime.discover_tools(ffmpeg=ffmpeg, ffprobe=ffprobe)
    if not isinstance(runtime_tools, rrv_runtime.RuntimeTools):
        raise _invalid("tools must be an rrv_runtime.RuntimeTools instance")
    ffmpeg_path = runtime_tools.ffmpeg.path
    ffprobe_path = runtime_tools.ffprobe.path
    if not isinstance(ffmpeg_path, str) or not ffmpeg_path:
        raise rrv_runtime.RRVError(rrv_runtime.ERR_TOOL_NOT_FOUND, "local FFmpeg is required")
    if not isinstance(ffprobe_path, str) or not ffprobe_path:
        raise rrv_runtime.RRVError(rrv_runtime.ERR_TOOL_NOT_FOUND, "local FFprobe is required")
    return runtime_tools, ffmpeg_path, ffprobe_path


def _format_is_mp4(value: Any) -> bool:
    return isinstance(value, str) and "mp4" in {item.strip().lower() for item in value.split(",")}


def _normalized_rotation_values(video: Mapping[str, Any], *, label: str) -> tuple[float, ...]:
    values: list[float] = []
    tags = video.get("tags")
    if isinstance(tags, Mapping) and "rotate" in tags:
        values.append(_number(tags["rotate"], f"{label}.tags.rotate") % 360.0)
    side_data = video.get("side_data_list")
    if side_data is not None:
        if not isinstance(side_data, list):
            raise _capability(f"{label}.side_data_list must be an array when present")
        for index, item in enumerate(side_data):
            if not isinstance(item, Mapping):
                raise _capability(f"{label}.side_data_list[{index}] must be an object")
            if "rotation" in item:
                values.append(
                    _number(item["rotation"], f"{label}.side_data_list[{index}].rotation") % 360.0
                )
    return tuple(values)


def _rotation_is_clear(video: Mapping[str, Any]) -> bool:
    tags = video.get("tags")
    if isinstance(tags, Mapping) and "rotate" in tags:
        return False
    side_data = video.get("side_data_list")
    if not isinstance(side_data, list):
        return side_data is None
    return not any(isinstance(item, Mapping) and "rotation" in item for item in side_data)


@dataclass(frozen=True)
class MediaFacts:
    """A report-safe subset of full FFprobe facts plus exact timing facts."""

    format_name: str
    width: int
    height: int
    fps: float
    frame_count: int
    duration_seconds: float
    video_codec: str
    video_profile: str | None
    pixel_format: str | None
    bit_depth: int | None
    has_audio: bool
    audio_stream_count: int
    audio_codec: str | None
    audio_profile: str | None
    audio_sample_rate: int | None
    audio_channels: int | None
    audio_channel_layout: str | None
    rotation_degrees: tuple[float, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_name": self.format_name,
            "container": "mp4",
            "width": self.width,
            "height": self.height,
            "fps": _stable_number(self.fps),
            "frame_count": self.frame_count,
            "duration_seconds": _stable_number(self.duration_seconds),
            "video_codec": self.video_codec,
            "video_profile": self.video_profile,
            "pixel_format": self.pixel_format,
            "bit_depth": self.bit_depth,
            "has_audio": self.has_audio,
            "audio_stream_count": self.audio_stream_count,
            "audio_codec": self.audio_codec,
            "audio_profile": self.audio_profile,
            "audio_sample_rate": self.audio_sample_rate,
            "audio_channels": self.audio_channels,
            "audio_channel_layout": self.audio_channel_layout,
            "rotation_degrees": [_stable_number(value) for value in self.rotation_degrees],
            "cfr_confirmed": True,
        }


def build_full_ffprobe_command(
    source: str | os.PathLike[str], ffprobe: str | os.PathLike[str]
) -> list[str]:
    """Return an argv-only full FFprobe facts command.

    The command intentionally asks for format, stream, chapter, and program
    facts.  The report later replaces FFprobe's absolute ``filename`` field
    with a project-relative spelling.
    """

    source_value = _path_argument(source, "source")
    ffprobe_value = _path_argument(ffprobe, "ffprobe")
    return [
        ffprobe_value,
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        "-show_programs",
        "-of",
        "json",
        source_value,
    ]


def build_nle_transcode_command(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
    ffmpeg: str | os.PathLike[str],
    *,
    fps: float | int,
    has_audio: bool,
) -> list[str]:
    """Build the one frozen Jianying-compatible-v1 transcode command."""

    source_value = _path_argument(source, "source")
    output_value = _path_argument(output, "output")
    ffmpeg_value = _path_argument(ffmpeg, "ffmpeg")
    if not isinstance(has_audio, bool):
        raise _invalid("has_audio must be a boolean")
    selected_fps = _supported_fps(_number(fps, "fps"))
    command = [
        ffmpeg_value,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-n",
        "-i",
        source_value,
        "-map",
        "0:v:0",
    ]
    if has_audio:
        command.extend(["-map", "0:a:0"])
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
            "-c:v",
            "libx264",
            "-profile:v",
            "high",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "18",
            "-preset",
            "medium",
            "-r",
            str(selected_fps),
            "-fps_mode",
            "cfr",
        ]
    )
    if has_audio:
        command.extend(
            [
                "-c:a",
                "aac",
                "-profile:a",
                "aac_low",
                "-ar",
                "48000",
                "-ac",
                "2",
            ]
        )
    else:
        command.append("-an")
    command.extend(["-movflags", "+faststart", "-f", "mp4", output_value])
    return command


def build_full_decode_command(
    source: str | os.PathLike[str], ffmpeg: str | os.PathLike[str]
) -> list[str]:
    """Decode every output video frame and every mapped audio stream to null."""

    source_value = _path_argument(source, "source")
    ffmpeg_value = _path_argument(ffmpeg, "ffmpeg")
    return [
        ffmpeg_value,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-xerror",
        "-progress",
        "pipe:1",
        "-nostats",
        "-i",
        source_value,
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-sn",
        "-dn",
        "-f",
        "null",
        "-",
    ]


def _supported_fps(value: float) -> int:
    for allowed in SUPPORTED_CFR_FPS:
        if math.isclose(value, float(allowed), rel_tol=0.0, abs_tol=1e-9):
            return allowed
    raise _capability("NLE delivery supports only CFR 24, 25, 30, 50, or 60 fps")


def _run_local(
    command: Sequence[str], *, timeout_seconds: float, check: bool, label: str
) -> rrv_runtime.CommandResult:
    """Run a real local command while suppressing tool path-bearing diagnostics."""

    try:
        result = rrv_runtime.run_command(command, timeout_seconds=timeout_seconds, check=check)
    except rrv_runtime.RRVError as exc:
        details: dict[str, Any] = {"cause_code": exc.code}
        for key in ("timeout_seconds", "returncode"):
            value = exc.details.get(key)
            if isinstance(value, (str, int, float, bool)):
                details[key] = value
        raise rrv_runtime.RRVError(exc.code, f"{label} failed", details) from exc
    if not isinstance(result, rrv_runtime.CommandResult):
        raise _tool_error(f"{label} returned an invalid command result")
    return result


def _full_ffprobe_facts(
    source: Path, ffprobe: str, *, timeout_seconds: float
) -> dict[str, Any]:
    result = _run_local(
        build_full_ffprobe_command(source, ffprobe),
        timeout_seconds=timeout_seconds,
        check=True,
        label="full FFprobe inspection",
    )
    if len(result.stdout.encode("utf-8", errors="replace")) > MAX_FFPROBE_JSON_BYTES:
        raise _tool_error("full FFprobe facts exceed the bounded delivery limit")
    try:
        raw = json.loads(
            result.stdout,
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=_strict_json_constant,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        raise _tool_error("full FFprobe inspection returned invalid JSON") from exc
    if not isinstance(raw, dict):
        raise _tool_error("full FFprobe inspection returned invalid facts")
    return raw


def _exact_timing(source: Path, ffprobe: str, *, timeout_seconds: float) -> Mapping[str, Any]:
    try:
        timing = rrv_runtime.probe_exact_video_timing(
            source, ffprobe, timeout_seconds=timeout_seconds
        )
    except rrv_runtime.RRVError as exc:
        details: dict[str, Any] = {"cause_code": exc.code, "capability": "exact_cfr_frame_timing"}
        if isinstance(exc.details.get("timeout_seconds"), (int, float)):
            details["timeout_seconds"] = exc.details["timeout_seconds"]
        raise rrv_runtime.RRVError(
            exc.code,
            "exact CFR timing inspection failed",
            details,
        ) from exc
    if not isinstance(timing, Mapping) or timing.get("cfr_confirmed") is not True:
        raise _capability("NLE delivery requires FFprobe-confirmed CFR timing")
    return timing


def _media_facts(
    raw: Mapping[str, Any],
    timing: Mapping[str, Any],
    *,
    role: str,
    output_profile: bool,
    expected_dimensions: tuple[int, int] | None = None,
    expected_fps: int | None = None,
    expected_frames: int | None = None,
    expected_audio: bool | None = None,
) -> MediaFacts:
    format_data = raw.get("format")
    if not isinstance(format_data, Mapping) or not _format_is_mp4(format_data.get("format_name")):
        raise _capability(f"{role} must be an MP4 container")
    format_name = format_data.get("format_name")
    if not isinstance(format_name, str) or not format_name:
        raise _capability(f"{role} FFprobe format name is unavailable")
    streams = raw.get("streams")
    if not isinstance(streams, list) or not streams:
        raise _capability(f"{role} FFprobe facts must contain streams")
    if not all(isinstance(stream, Mapping) for stream in streams):
        raise _capability(f"{role} FFprobe stream facts are invalid")
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    unsupported = [stream.get("codec_type") for stream in streams if stream.get("codec_type") not in {"video", "audio"}]
    if unsupported:
        raise _capability(f"{role} rejects subtitle, data, attachment, and other non-A/V streams")
    if len(video_streams) != 1:
        raise _capability(f"{role} requires exactly one video stream")
    if len(audio_streams) > 1:
        raise _capability(f"{role} supports at most one audio stream")
    video = video_streams[0]
    width = _positive_int(video.get("width"), f"{role}.video.width")
    height = _positive_int(video.get("height"), f"{role}.video.height")
    if (width, height) not in SUPPORTED_DIMENSIONS:
        raise _capability(f"{role} supports only the four audited dimensions")
    codec = video.get("codec_name")
    if not isinstance(codec, str) or not codec:
        raise _capability(f"{role}.video.codec_name is unavailable")
    profile = video.get("profile") if isinstance(video.get("profile"), str) else None
    pixel_format = video.get("pix_fmt") if isinstance(video.get("pix_fmt"), str) else None
    bit_depth: int | None = None
    if video.get("bits_per_raw_sample") not in (None, "N/A"):
        bit_depth = _positive_int(video.get("bits_per_raw_sample"), f"{role}.video.bits_per_raw_sample")
    rotations = _normalized_rotation_values(video, label=f"{role}.video")

    if not isinstance(timing, Mapping) or timing.get("cfr_confirmed") is not True:
        raise _capability(f"{role} requires FFprobe-confirmed CFR timing")
    fps = _positive_rate(timing.get("fps"), f"{role}.timing.fps")
    frame_count = _positive_int(timing.get("frame_count"), f"{role}.timing.frame_count")
    duration = _number(timing.get("duration_seconds"), f"{role}.timing.duration_seconds")
    if duration <= 0 or duration > MAX_DURATION_SECONDS + 1e-9:
        raise _capability(f"{role} duration must be at most 60 seconds")
    supported_fps = _supported_fps(fps)
    if not math.isclose(duration, frame_count / fps, rel_tol=1e-7, abs_tol=1e-6):
        raise _capability(f"{role} exact timing does not agree with frame count and frame rate")
    for field in ("r_frame_rate", "avg_frame_rate"):
        declared = _positive_rate(video.get(field), f"{role}.video.{field}")
        if not math.isclose(declared, fps, rel_tol=1e-9, abs_tol=1e-12):
            raise _capability(f"{role} declared frame rate does not match exact CFR timing")
    if frame_count > int(MAX_DURATION_SECONDS * supported_fps):
        raise _capability(f"{role} frame count exceeds the bounded CFR duration")

    audio = audio_streams[0] if audio_streams else None
    if audio is None:
        audio_codec = audio_profile = audio_channel_layout = None
        audio_sample_rate = audio_channels = None
    else:
        codec_value = audio.get("codec_name")
        if not isinstance(codec_value, str) or not codec_value:
            raise _capability(f"{role}.audio.codec_name is unavailable")
        audio_codec = codec_value
        audio_profile = audio.get("profile") if isinstance(audio.get("profile"), str) else None
        audio_sample_rate = _positive_int(audio.get("sample_rate"), f"{role}.audio.sample_rate")
        audio_channels = _positive_int(audio.get("channels"), f"{role}.audio.channels")
        audio_channel_layout = (
            audio.get("channel_layout") if isinstance(audio.get("channel_layout"), str) else None
        )

    if output_profile:
        if codec.lower() != "h264":
            raise _tool_error("output video codec is not H.264")
        if profile != "High":
            raise _tool_error("output video profile is not H.264 High")
        if pixel_format != "yuv420p":
            raise _tool_error("output video pixel format is not 8-bit yuv420p")
        if bit_depth != 8:
            raise _tool_error("output video bit depth is not 8-bit")
        if not _rotation_is_clear(video):
            raise _tool_error("output video rotation metadata was not cleared")
        if expected_dimensions is not None and (width, height) != expected_dimensions:
            raise _tool_error("output dimensions do not match the checked source")
        if expected_fps is not None and supported_fps != expected_fps:
            raise _tool_error("output CFR frame rate does not match the checked source")
        if expected_frames is not None and frame_count != expected_frames:
            raise _tool_error("output frame count does not match the checked source")
        if expected_audio is not None and bool(audio) != expected_audio:
            raise _tool_error("output audio presence does not match the checked source")
        if audio is not None:
            if audio_codec.lower() != "aac" or audio_profile != "LC":
                raise _tool_error("output audio is not AAC-LC")
            if audio_sample_rate != 48000 or audio_channels != 2 or audio_channel_layout != "stereo":
                raise _tool_error("output audio is not 48 kHz stereo")
    elif any(not math.isclose(value, 0.0, abs_tol=1e-9) for value in rotations):
        # The frozen command deliberately disables metadata copying rather than
        # attempting an unreviewed geometric rotation transform.
        raise _capability("NLE delivery rejects source video with non-zero rotation metadata")

    return MediaFacts(
        format_name=format_name,
        width=width,
        height=height,
        fps=fps,
        frame_count=frame_count,
        duration_seconds=duration,
        video_codec=codec.lower(),
        video_profile=profile,
        pixel_format=pixel_format,
        bit_depth=bit_depth,
        has_audio=audio is not None,
        audio_stream_count=len(audio_streams),
        audio_codec=audio_codec.lower() if audio_codec else None,
        audio_profile=audio_profile,
        audio_sample_rate=audio_sample_rate,
        audio_channels=audio_channels,
        audio_channel_layout=audio_channel_layout,
        rotation_degrees=rotations,
    )


def _metadata_and_chapters_are_clear(raw: Mapping[str, Any]) -> bool:
    chapters = raw.get("chapters", [])
    if not isinstance(chapters, list) or chapters:
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
    streams = raw.get("streams")
    if not isinstance(streams, list) or not streams:
        return False
    video_count = 0
    for stream in streams:
        if not isinstance(stream, Mapping):
            return False
        stream_type = stream.get("codec_type")
        if stream_type not in {"video", "audio"}:
            return False
        video_count += int(stream_type == "video")
        stream_tags = stream.get("tags", {})
        if not isinstance(stream_tags, Mapping):
            return False
        for key, value in stream_tags.items():
            if not isinstance(key, str) or not isinstance(value, str):
                return False
            normalized = key.lower()
            if normalized not in _GENERATED_STREAM_TAGS:
                return False
            if normalized == "language" and value != "und":
                return False
            if normalized == "handler_name":
                expected = "VideoHandler" if stream_type == "video" else "SoundHandler"
                if value != expected:
                    return False
            if normalized == "vendor_id" and re.fullmatch(r"(?:\[0\]){4}|[A-Za-z0-9]{4}", value) is None:
                return False
            if normalized == "encoder":
                suffix = "libx264" if stream_type == "video" else "aac"
                if re.fullmatch(rf"Lavc[0-9.]+(?: {suffix})?", value) is None:
                    return False
    return video_count == 1


def _parse_decode_progress(progress_text: str) -> dict[str, Any]:
    if not isinstance(progress_text, str):
        raise _tool_error("full decode QA returned invalid progress")
    frame_count: int | None = None
    completed = False
    for raw_line in progress_text.splitlines():
        line = raw_line.strip()
        match = _FRAME_PROGRESS_RE.fullmatch(line)
        if match:
            frame_count = int(match.group(1))
        elif line == "progress=end":
            completed = True
    return {"frame_count": frame_count, "completed": completed}


def _full_decode_qa(
    source: Path,
    ffmpeg: str,
    *,
    timeout_seconds: float,
    expected_frames: int,
    has_audio: bool,
) -> dict[str, Any]:
    result = _run_local(
        build_full_decode_command(source, ffmpeg),
        timeout_seconds=timeout_seconds,
        check=False,
        label="full FFmpeg decode QA",
    )
    progress = _parse_decode_progress(result.stdout)
    if result.returncode != 0:
        raise _tool_error("full FFmpeg decode QA failed", {"returncode": result.returncode})
    if progress["completed"] is not True or not isinstance(progress["frame_count"], int):
        raise _tool_error("full FFmpeg decode QA did not complete")
    if progress["frame_count"] != expected_frames:
        raise _tool_error(
            "full FFmpeg decode QA frame count does not match FFprobe",
            {"expected_frames": expected_frames, "decoded_frames": progress["frame_count"]},
        )
    return {
        "full_decode": {
            "passed": True,
            "completed": True,
            "decoded_video_frames": progress["frame_count"],
            "decoded_audio": has_audio,
            "audio_decode_applicable": has_audio,
            "returncode": 0,
        },
        "checks": [
            {"id": "full_av_decode", "passed": True, "message": "FFmpeg decoded all mapped A/V streams"},
            {
                "id": "decoded_frame_count",
                "passed": True,
                "expected": expected_frames,
                "actual": progress["frame_count"],
            },
        ],
    }


def _faststart_verified(root: Path, identity: Any, *, label: str) -> bool:
    """Confirm the MP4 ``moov`` atom precedes media data without decoding it."""

    rrv_faithful._assert_file_identity(root, identity, label)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(identity.path, flags | nofollow)
    except OSError as exc:
        if nofollow and getattr(exc, "errno", None) in {22, 95}:
            try:
                descriptor = os.open(identity.path, flags)
            except OSError as retry_exc:
                raise _tool_error("could not inspect the MP4 faststart layout") from retry_exc
        else:
            raise _tool_error("could not inspect the MP4 faststart layout") from exc
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            opened = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or opened.st_dev != identity.device
                or opened.st_ino != identity.inode
            ):
                raise _tool_error("MP4 changed while inspecting its faststart layout")
            total_size = int(opened.st_size)
            offset = 0
            moov_offset: int | None = None
            mdat_offset: int | None = None
            atom_count = 0
            while offset < total_size:
                atom_count += 1
                if atom_count > MAX_MP4_TOP_LEVEL_ATOMS:
                    return False
                handle.seek(offset)
                header = handle.read(8)
                if len(header) != 8:
                    return False
                atom_size = int.from_bytes(header[:4], "big")
                atom_type = header[4:]
                header_size = 8
                if atom_size == 1:
                    extended = handle.read(8)
                    if len(extended) != 8:
                        return False
                    atom_size = int.from_bytes(extended, "big")
                    header_size = 16
                elif atom_size == 0:
                    atom_size = total_size - offset
                if atom_size < header_size or offset + atom_size > total_size:
                    return False
                if atom_type == b"moov" and moov_offset is None:
                    moov_offset = offset
                elif atom_type == b"mdat" and mdat_offset is None:
                    mdat_offset = offset
                if moov_offset is not None and mdat_offset is not None:
                    return moov_offset < mdat_offset
                offset += atom_size
            return False
    except rrv_runtime.RRVError:
        raise
    except OSError as exc:
        raise _tool_error("could not inspect the MP4 faststart layout") from exc
    finally:
        rrv_faithful._assert_file_identity(root, identity, label)


def _add_profile_qa(qa: dict[str, Any], facts: MediaFacts, *, faststart: bool) -> None:
    """Record the mechanical profile checks already enforced before publish."""

    checks = qa.get("checks")
    if not isinstance(checks, list):  # pragma: no cover - internal invariant guard.
        raise _tool_error("NLE QA state is invalid")
    profile_checks = {
        "mp4": True,
        "h264_high_8_bit_yuv420p": True,
        "cfr": True,
        "audio": {
            "passed": True,
            "mode": "aac-lc-48khz-stereo" if facts.has_audio else "no-audio-preserved",
        },
        "metadata_cleared": True,
        "chapters_cleared": True,
        "rotation_cleared": True,
        "faststart": faststart,
    }
    qa["profile_checks"] = profile_checks
    checks.extend(
        [
            {"id": "profile", "passed": True, "actual": NLE_PROFILE},
            {"id": "metadata_chapters_rotation", "passed": True},
            {"id": "mp4_faststart", "passed": faststart},
        ]
    )


def _looks_like_absolute_path(value: str) -> bool:
    return bool(_ABSOLUTE_PATH_FRAGMENT_RE.search(value))


def _sanitize_ffprobe_facts(value: Any, *, filename: str, at_root: bool = True) -> Any:
    """Keep full FFprobe structure while removing absolute filesystem paths."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise _tool_error("FFprobe facts contain an invalid object key")
            if at_root and key == "format" and isinstance(child, Mapping):
                result[key] = _sanitize_ffprobe_facts(child, filename=filename, at_root=False)
            elif key == "filename":
                result[key] = filename
            else:
                result[key] = _sanitize_ffprobe_facts(child, filename=filename, at_root=False)
        return result
    if isinstance(value, list):
        return [_sanitize_ffprobe_facts(item, filename=filename, at_root=False) for item in value]
    if isinstance(value, str) and _looks_like_absolute_path(value):
        return "<redacted-absolute-path>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise _tool_error("FFprobe facts contain an unsupported JSON value")


def _assert_report_has_no_absolute_paths(value: Any) -> None:
    if isinstance(value, Mapping):
        for child in value.values():
            _assert_report_has_no_absolute_paths(child)
    elif isinstance(value, list):
        for child in value:
            _assert_report_has_no_absolute_paths(child)
    elif isinstance(value, str) and _looks_like_absolute_path(value):
        raise _tool_error("NLE delivery report would expose an absolute path")


def _inspect_bound_media(
    root: Path,
    identity: Any,
    *,
    label: str,
    ffprobe: str,
    timeout_seconds: float,
    output_profile: bool,
    expected_dimensions: tuple[int, int] | None = None,
    expected_fps: int | None = None,
    expected_frames: int | None = None,
    expected_audio: bool | None = None,
) -> tuple[dict[str, Any], Mapping[str, Any], MediaFacts]:
    """Probe, exact-time, then rebind a source whose identity is already known."""

    rrv_faithful._assert_file_identity(root, identity, label)
    raw = _full_ffprobe_facts(identity.path, ffprobe, timeout_seconds=timeout_seconds)
    rrv_faithful._assert_file_identity(root, identity, label)
    timing = _exact_timing(identity.path, ffprobe, timeout_seconds=timeout_seconds)
    rrv_faithful._assert_file_identity(root, identity, label)
    facts = _media_facts(
        raw,
        timing,
        role=label,
        output_profile=output_profile,
        expected_dimensions=expected_dimensions,
        expected_fps=expected_fps,
        expected_frames=expected_frames,
        expected_audio=expected_audio,
    )
    return raw, timing, facts


def _verification_report(
    *,
    delivery_relative: str,
    output_sha256: str,
    output_raw: Mapping[str, Any],
    output_facts: MediaFacts,
    qa: Mapping[str, Any],
) -> dict[str, Any]:
    report = {
        "schema_version": NLE_SCHEMA_VERSION,
        "completion": "nle_compatible_derivative",
        "bitstream_faithful": False,
        "profile": NLE_PROFILE,
        "output": {"path": delivery_relative, "sha256": output_sha256},
        "media_facts": {"output": output_facts.to_dict()},
        "ffprobe_facts": {
            "output": _sanitize_ffprobe_facts(output_raw, filename=delivery_relative)
        },
        "qa": dict(qa),
        "verified": True,
    }
    _assert_report_has_no_absolute_paths(report)
    return report


def verify_nle_delivery(
    delivery: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str],
    rights_confirmed: bool,
    profile: str = NLE_PROFILE,
    ffmpeg: str | os.PathLike[str] | None = None,
    ffprobe: str | os.PathLike[str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    tools: rrv_runtime.RuntimeTools | None = None,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Read-only validation of one authorized, project-contained NLE delivery.

    A custom command runner is deliberately rejected.  A runner that can
    fabricate FFprobe or FFmpeg success would erase the production QA claim;
    unit tests should patch local primitives instead of changing this boundary.
    """

    _require_rights(rights_confirmed)
    _require_profile(profile)
    if runner is not None:
        raise _invalid("custom command runners are not supported for NLE delivery verification")
    timeout = rrv_runtime.validate_timeout(timeout_seconds)
    root = rrv_faithful._safe_project_root(project_root)
    delivery_path, delivery_relative = _source_below_root(root, delivery, label="delivery")
    delivery_identity = rrv_faithful._safe_regular_file(root, delivery_path, "delivery")
    _, ffmpeg_path, ffprobe_path = _require_runtime_tools(
        tools, ffmpeg=ffmpeg, ffprobe=ffprobe
    )
    with rrv_faithful._hold_bound_file(root, delivery_identity, "delivery"):
        output_hash = rrv_faithful._sha256_bound_file(root, delivery_identity, "delivery")
        output_raw, _, output_facts = _inspect_bound_media(
            root,
            delivery_identity,
            label="delivery",
            ffprobe=ffprobe_path,
            timeout_seconds=timeout,
            output_profile=True,
        )
        if not _metadata_and_chapters_are_clear(output_raw):
            raise _tool_error("delivery metadata or chapters were not fully cleared")
        faststart = _faststart_verified(root, delivery_identity, label="delivery")
        if not faststart:
            raise _tool_error("delivery MP4 faststart layout was not verified")
        qa = _full_decode_qa(
            delivery_path,
            ffmpeg_path,
            timeout_seconds=timeout,
            expected_frames=output_facts.frame_count,
            has_audio=output_facts.has_audio,
        )
        _add_profile_qa(qa, output_facts, faststart=faststart)
        if rrv_faithful._sha256_bound_file(root, delivery_identity, "delivery") != output_hash:
            raise _invalid("delivery hash changed during verification")
    return _verification_report(
        delivery_relative=delivery_relative,
        output_sha256=output_hash,
        output_raw=output_raw,
        output_facts=output_facts,
        qa=qa,
    )


def export_nle_delivery(
    source: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str],
    rights_confirmed: bool,
    output_dir: str | os.PathLike[str] = "jianying-delivery",
    profile: str = NLE_PROFILE,
    ffmpeg: str | os.PathLike[str] | None = None,
    ffprobe: str | os.PathLike[str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    tools: rrv_runtime.RuntimeTools | None = None,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Atomically publish a fully verified Jianying-compatible MP4 derivative.

    The rights gate occurs before root resolution, source handling, output
    checks, tool discovery, or staging.  Any subsequent failure cleans the
    private stage and never publishes a delivery directory.
    """

    _require_rights(rights_confirmed)
    _require_profile(profile)
    if runner is not None:
        raise _invalid("custom command runners are not supported for NLE delivery export")
    timeout = rrv_runtime.validate_timeout(timeout_seconds)
    root = rrv_faithful._safe_project_root(project_root)
    source_path, source_relative = _source_below_root(root, source, label="source")
    source_identity = rrv_faithful._safe_regular_file(root, source_path, "source")
    # Reject a visible collision before probing or executing any local tools.
    target = rrv_propose._direct_child_output_target(root, output_dir)
    _, ffmpeg_path, ffprobe_path = _require_runtime_tools(
        tools, ffmpeg=ffmpeg, ffprobe=ffprobe
    )

    with rrv_faithful._hold_bound_file(root, source_identity, "source"):
        input_hash = rrv_faithful._sha256_bound_file(root, source_identity, "source")
        input_raw, _, input_facts = _inspect_bound_media(
            root,
            source_identity,
            label="source",
            ffprobe=ffprobe_path,
            timeout_seconds=timeout,
            output_profile=False,
        )
        if rrv_faithful._sha256_bound_file(root, source_identity, "source") != input_hash:
            raise _invalid("source hash changed during NLE delivery preflight")

    stage: Any | None = None
    try:
        stage = rrv_propose._new_staging_directory(root, "nle")
        snapshot_path, snapshot_identity, snapshot_sha256 = (
            rrv_faithful._snapshot_bound_file_to_stage(
                root,
                source_identity,
                stage,
                "input-snapshot.mp4",
                label="NLE delivery source",
                expected_sha256=input_hash,
            )
        )
        delivery_path = rrv_propose._stage_path(root, stage, DELIVERY_FILENAME)
        command = build_nle_transcode_command(
            snapshot_path,
            delivery_path,
            ffmpeg_path,
            fps=input_facts.fps,
            has_audio=input_facts.has_audio,
        )
        with rrv_faithful._hold_bound_file(
            root, snapshot_identity, "NLE delivery source snapshot"
        ):
            _run_local(
                command,
                timeout_seconds=timeout,
                check=True,
                label="Jianying-compatible transcode",
            )
            if (
                rrv_faithful._sha256_bound_file(
                    root, snapshot_identity, "NLE delivery source snapshot"
                )
                != snapshot_sha256
            ):
                raise _invalid("NLE delivery source snapshot changed during transcode")
        rrv_propose._remove_stage_file(stage, snapshot_path)
        rrv_propose._assert_stage_regular_file(stage, delivery_path, "staged NLE delivery")
        output_identity = rrv_faithful._safe_regular_file(
            root, delivery_path, "staged NLE delivery"
        )
        with rrv_faithful._hold_bound_file(
            root, output_identity, "staged NLE delivery"
        ):
            output_hash = rrv_faithful._sha256_bound_file(
                root, output_identity, "staged NLE delivery"
            )
            if rrv_faithful._sha256_bound_file(root, source_identity, "source") != input_hash:
                raise _invalid("source hash changed during NLE delivery transcode")

            output_raw, _, output_facts = _inspect_bound_media(
                root,
                output_identity,
                label="staged NLE delivery",
                ffprobe=ffprobe_path,
                timeout_seconds=timeout,
                output_profile=True,
                expected_dimensions=(input_facts.width, input_facts.height),
                expected_fps=_supported_fps(input_facts.fps),
                expected_frames=input_facts.frame_count,
                expected_audio=input_facts.has_audio,
            )
            if not _metadata_and_chapters_are_clear(output_raw):
                raise _tool_error("staged NLE delivery metadata or chapters were not fully cleared")
            faststart = _faststart_verified(root, output_identity, label="staged NLE delivery")
            if not faststart:
                raise _tool_error("staged NLE delivery MP4 faststart layout was not verified")
            qa = _full_decode_qa(
                delivery_path,
                ffmpeg_path,
                timeout_seconds=timeout,
                expected_frames=output_facts.frame_count,
                has_audio=output_facts.has_audio,
            )
            _add_profile_qa(qa, output_facts, faststart=faststart)
            rrv_propose._assert_stage_regular_file(stage, delivery_path, "staged NLE delivery")
            if (
                rrv_faithful._sha256_bound_file(root, output_identity, "staged NLE delivery")
                != output_hash
            ):
                raise _invalid("staged NLE delivery hash changed during verification")
        if rrv_faithful._sha256_bound_file(root, source_identity, "source") != input_hash:
            raise _invalid("source hash changed during NLE delivery verification")

        output_relative = rrv_propose._lexical_relative_output_path(root, target / DELIVERY_FILENAME)
        report_relative = rrv_propose._lexical_relative_output_path(root, target / REPORT_FILENAME)
        report = _verification_report(
            delivery_relative=output_relative,
            output_sha256=output_hash,
            output_raw=output_raw,
            output_facts=output_facts,
            qa=qa,
        )
        report.update(
            {
                "output_dir": rrv_propose._lexical_relative_output_path(root, target),
                "delivery_path": output_relative,
                "report_path": report_relative,
                "input_sha256": input_hash,
                "output_sha256": output_hash,
                "input": {"path": source_relative, "sha256": input_hash},
                "media_facts": {
                    "input": input_facts.to_dict(),
                    "output": output_facts.to_dict(),
                },
                "ffprobe_facts": {
                    "input": _sanitize_ffprobe_facts(input_raw, filename=source_relative),
                    "output": _sanitize_ffprobe_facts(output_raw, filename=output_relative),
                },
            }
        )
        _assert_report_has_no_absolute_paths(report)
        report_path = rrv_propose._stage_path(root, stage, REPORT_FILENAME)
        rrv_propose._write_json_new(
            report_path,
            report,
            label="NLE delivery report",
            stage=stage,
        )
        report_identity = rrv_faithful._safe_regular_file(
            root, report_path, "NLE delivery report"
        )
        report_sha256 = rrv_faithful._sha256_bound_file(
            root, report_identity, "NLE delivery report"
        )
        rrv_propose._publish_stage(
            root,
            stage,
            target,
            label="NLE delivery",
            expected_files={
                DELIVERY_FILENAME: output_hash,
                REPORT_FILENAME: report_sha256,
            },
        )
        stage = None
        return report
    except Exception:
        rrv_propose._cleanup_directory(root, stage)
        raise


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "DELIVERY_FILENAME",
    "MAX_DURATION_SECONDS",
    "NLE_PROFILE",
    "NLE_SCHEMA_VERSION",
    "REPORT_FILENAME",
    "SUPPORTED_CFR_FPS",
    "SUPPORTED_DIMENSIONS",
    "MediaFacts",
    "build_full_decode_command",
    "build_full_ffprobe_command",
    "build_nle_transcode_command",
    "export_nle_delivery",
    "verify_nle_delivery",
]
