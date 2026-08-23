#!/usr/bin/env python3
"""Small, local-only runtime primitives for reference-video analysis.

This module deliberately owns only deterministic media plumbing.  It does not
interpret video content, download tools, or modify an input media file.  The
public functions are suitable for importing from a future ``video_remix.py``
integration as well as for use by ``rrv_analyze.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Mapping, Sequence


JSON_SCHEMA_VERSION = "1.0"
DEFAULT_TIMEOUT_SECONDS = 30.0
VERSION_TIMEOUT_SECONDS = 5.0
MAX_ERROR_TEXT_LENGTH = 480

ERR_INVALID_ARGUMENT = "invalid_argument"
ERR_PROJECT_ROOT_INVALID = "project_root_invalid"
ERR_OUTPUT_PATH_OUTSIDE_ROOT = "output_path_outside_project_root"
ERR_OUTPUT_EXISTS = "output_already_exists"
ERR_SOURCE_NOT_FOUND = "source_not_found"
ERR_TOOL_NOT_FOUND = "tool_not_found"
ERR_TOOL_EXECUTION = "tool_execution_failed"
ERR_TOOL_TIMEOUT = "tool_timeout"
ERR_PROBE_FAILED = "probe_failed"
ERR_CAPABILITY_UNAVAILABLE = "capability_unavailable"

_ENVIRONMENT_VARIABLES: dict[str, tuple[str, ...]] = {
    "ffmpeg": ("RRV_FFMPEG", "FFMPEG_BINARY", "FFMPEG_PATH"),
    "ffprobe": ("RRV_FFPROBE", "FFPROBE_BINARY", "FFPROBE_PATH"),
}


class RRVError(RuntimeError):
    """A bounded, machine-readable error that is safe to expose in JSON."""

    def __init__(
        self,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


@dataclass(frozen=True)
class CommandResult:
    """Result of an argv-only subprocess invocation."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ToolInfo:
    """One discovered executable; ``path`` is never guessed or downloaded."""

    name: str
    path: str | None
    source: str | None
    version: str | None = None

    @property
    def available(self) -> bool:
        return self.path is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "path": self.path,
            "source": self.source,
            "version": self.version,
        }


@dataclass(frozen=True)
class RuntimeTools:
    """Discovered FFmpeg tools, with their independent provenance."""

    ffmpeg: ToolInfo
    ffprobe: ToolInfo

    def to_dict(self) -> dict[str, Any]:
        return {"ffmpeg": self.ffmpeg.to_dict(), "ffprobe": self.ffprobe.to_dict()}


def stable_json_dumps(payload: Any, *, indent: int | None = 2) -> str:
    """Serialize deterministic, standards-compliant JSON for CLI/API results."""

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=indent,
        allow_nan=False,
    )


def success_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    """Wrap a successful result in the common stable response envelope."""

    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "status": "ok",
        "result": dict(result),
    }


def error_payload(error: RRVError) -> dict[str, Any]:
    """Wrap an expected runtime error in the common stable response envelope."""

    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "status": "error",
        "error": error.to_dict(),
    }


def _compact_text(value: str | None, *, limit: int = MAX_ERROR_TEXT_LENGTH) -> str | None:
    if not value:
        return None
    compact = " ".join(value.strip().split())
    if not compact:
        return None
    if len(compact) > limit:
        return f"{compact[: limit - 1]}…"
    return compact


def _tool_label(executable: str) -> str:
    name = Path(executable).name
    return name or executable


def validate_timeout(timeout_seconds: float) -> float:
    """Validate and normalize a subprocess timeout shared by public APIs."""

    if isinstance(timeout_seconds, bool):
        raise RRVError(ERR_INVALID_ARGUMENT, "timeout_seconds must be a positive number")
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError) as exc:
        raise RRVError(ERR_INVALID_ARGUMENT, "timeout_seconds must be a positive number") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise RRVError(ERR_INVALID_ARGUMENT, "timeout_seconds must be a positive number")
    return timeout


def _normalize_argv(command: Sequence[str | os.PathLike[str]]) -> tuple[str, ...]:
    if isinstance(command, (str, bytes)) or not command:
        raise RRVError(ERR_INVALID_ARGUMENT, "command must be a non-empty argv sequence")
    argv: list[str] = []
    for value in command:
        try:
            item = os.fspath(value)
        except TypeError as exc:
            raise RRVError(ERR_INVALID_ARGUMENT, "command arguments must be strings or paths") from exc
        if isinstance(item, bytes):
            raise RRVError(ERR_INVALID_ARGUMENT, "byte command arguments are not supported")
        if not item or "\x00" in item:
            raise RRVError(ERR_INVALID_ARGUMENT, "command arguments must be non-empty and contain no NUL")
        argv.append(item)
    return tuple(argv)


def run_command(
    command: Sequence[str | os.PathLike[str]],
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    check: bool = True,
) -> CommandResult:
    """Run an argv-only command with no shell, inherited stdin, or long errors.

    The caller receives captured stdout/stderr only on success or when
    ``check=False``.  Failures expose a short normalized stderr summary rather
    than arbitrary tool output.
    """

    argv = _normalize_argv(command)
    timeout = validate_timeout(timeout_seconds)
    try:
        process = subprocess.Popen(
            argv,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError as exc:
        raise RRVError(
            ERR_TOOL_NOT_FOUND,
            f"executable was not found: {_tool_label(argv[0])}",
            {"tool": _tool_label(argv[0])},
        ) from exc
    except OSError as exc:
        raise RRVError(
            ERR_TOOL_EXECUTION,
            f"could not start {_tool_label(argv[0])}",
            {"tool": _tool_label(argv[0]), "reason": _compact_text(str(exc))},
        ) from exc

    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        del stdout, stderr
        raise RRVError(
            ERR_TOOL_TIMEOUT,
            f"{_tool_label(argv[0])} exceeded the timeout",
            {"tool": _tool_label(argv[0]), "timeout_seconds": timeout},
        )

    result = CommandResult(argv=argv, returncode=process.returncode, stdout=stdout, stderr=stderr)
    if check and result.returncode != 0:
        details: dict[str, Any] = {
            "tool": _tool_label(argv[0]),
            "returncode": result.returncode,
        }
        summary = _compact_text(result.stderr or result.stdout)
        if summary:
            details["output"] = summary
        raise RRVError(
            ERR_TOOL_EXECUTION,
            f"{_tool_label(argv[0])} exited with code {result.returncode}",
            details,
        )
    return result


def _resolve_executable(
    candidate: str | os.PathLike[str] | None,
    *,
    search_path: str | None = None,
) -> str | None:
    if candidate is None:
        return None
    try:
        value = os.fspath(candidate)
    except TypeError:
        return None
    if isinstance(value, bytes):
        return None
    value = value.strip()
    if not value or "\x00" in value:
        return None
    path = Path(value)
    if path.is_file():
        return str(path.resolve())
    found = shutil.which(value, path=search_path)
    return str(Path(found).resolve()) if found else None


def _discover_one(
    name: str,
    explicit: str | os.PathLike[str] | None,
    environment: Mapping[str, str],
) -> ToolInfo:
    candidates: list[tuple[str, str | os.PathLike[str] | None]] = [("explicit", explicit)]
    candidates.extend((f"env:{key}", environment.get(key)) for key in _ENVIRONMENT_VARIABLES[name])
    candidates.append(("PATH", name))
    configured_path = environment.get("PATH")
    search_path = configured_path if isinstance(configured_path, str) else None
    for source, candidate in candidates:
        resolved = _resolve_executable(candidate, search_path=search_path)
        if resolved:
            return ToolInfo(name=name, path=resolved, source=source)
    return ToolInfo(name=name, path=None, source=None)


def discover_tools(
    *,
    ffmpeg: str | os.PathLike[str] | None = None,
    ffprobe: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
    probe_versions: bool = False,
) -> RuntimeTools:
    """Discover explicit paths first, then RRV env vars, then ``PATH``.

    Discovery only locates user-installed programs.  It never downloads or
    vendors a binary, and no private installation path is assumed.
    """

    effective_environment: Mapping[str, str] = os.environ if environment is None else environment
    tools = RuntimeTools(
        ffmpeg=_discover_one("ffmpeg", ffmpeg, effective_environment),
        ffprobe=_discover_one("ffprobe", ffprobe, effective_environment),
    )
    if not probe_versions:
        return tools
    return RuntimeTools(
        ffmpeg=_with_version(tools.ffmpeg),
        ffprobe=_with_version(tools.ffprobe),
    )


def _with_version(tool: ToolInfo) -> ToolInfo:
    return ToolInfo(
        name=tool.name,
        path=tool.path,
        source=tool.source,
        version=probe_tool_version(tool.path) if tool.path else None,
    )


def probe_tool_version(executable: str | os.PathLike[str] | None) -> str | None:
    """Return the first version line, or ``None`` when a detected tool cannot run."""

    if executable is None:
        return None
    try:
        result = run_command([executable, "-version"], timeout_seconds=VERSION_TIMEOUT_SECONDS)
    except RRVError:
        return None
    lines = (result.stdout or result.stderr).strip().splitlines()
    return _compact_text(lines[0]) if lines else None


def require_source_file(source: str | os.PathLike[str]) -> Path:
    """Resolve a read-only source file.  This function never constrains inputs."""

    try:
        path = Path(source).resolve(strict=True)
    except (TypeError, OSError, RuntimeError) as exc:
        raise RRVError(ERR_SOURCE_NOT_FOUND, "source file does not exist") from exc
    if not path.is_file():
        raise RRVError(ERR_SOURCE_NOT_FOUND, "source must be a regular file")
    return path


def require_project_root(project_root: str | os.PathLike[str]) -> Path:
    """Resolve an existing project root used to contain all generated outputs."""

    try:
        root = Path(project_root).resolve(strict=True)
    except (TypeError, OSError, RuntimeError) as exc:
        raise RRVError(ERR_PROJECT_ROOT_INVALID, "project_root must be an existing directory") from exc
    if not root.is_dir():
        raise RRVError(ERR_PROJECT_ROOT_INVALID, "project_root must be an existing directory")
    return root


def resolve_output_path(
    project_root: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    *,
    create_parent: bool = False,
    must_not_exist: bool = False,
) -> Path:
    """Resolve an output path and reject traversal or symlink escape from root."""

    root = require_project_root(project_root)
    try:
        requested = Path(output_path)
    except TypeError as exc:
        raise RRVError(ERR_INVALID_ARGUMENT, "output_path must be a path") from exc
    if not str(requested) or "\x00" in str(requested):
        raise RRVError(ERR_INVALID_ARGUMENT, "output_path must be a non-empty path")
    candidate = requested if requested.is_absolute() else root / requested
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (ValueError, OSError, RuntimeError) as exc:
        raise RRVError(
            ERR_OUTPUT_PATH_OUTSIDE_ROOT,
            "output_path must stay within project_root",
        ) from exc
    if resolved == root:
        raise RRVError(ERR_INVALID_ARGUMENT, "output_path must name a file or subdirectory below project_root")
    if create_parent:
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            # A concurrent symlink swap must not turn the newly-created target
            # into an escape after the first containment check.
            resolved = resolved.resolve(strict=False)
            resolved.relative_to(root)
        except (ValueError, OSError, RuntimeError) as exc:
            raise RRVError(
                ERR_OUTPUT_PATH_OUTSIDE_ROOT,
                "output_path must stay within project_root",
            ) from exc
    if must_not_exist and resolved.exists():
        raise RRVError(ERR_OUTPUT_EXISTS, "refusing to overwrite an existing output")
    return resolved


def relative_output_path(project_root: str | os.PathLike[str], path: str | os.PathLike[str]) -> str:
    """Return a portable, root-relative path for stable JSON output."""

    root = require_project_root(project_root)
    try:
        return Path(path).resolve(strict=False).relative_to(root).as_posix()
    except (ValueError, OSError, RuntimeError) as exc:
        raise RRVError(ERR_OUTPUT_PATH_OUTSIDE_ROOT, "path must stay within project_root") from exc


def _strict_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value is not allowed: {value}")


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate or candidate.upper() == "N/A":
            return None
        try:
            parsed = float(candidate)
        except ValueError:
            return None
        return parsed if math.isfinite(parsed) else None
    return None


def parse_rational(value: Any) -> float | None:
    """Parse FFmpeg's ``numerator/denominator`` form without raising."""

    if not isinstance(value, str):
        return _number(value)
    match = re.fullmatch(r"\s*(-?\d+)\s*/\s*(-?\d+)\s*", value)
    if not match:
        return _number(value)
    numerator, denominator = int(match.group(1)), int(match.group(2))
    if denominator == 0:
        return None
    result = numerator / denominator
    return result if math.isfinite(result) else None


def _integer(value: Any) -> int | None:
    parsed = _number(value)
    if parsed is None or isinstance(parsed, float) and not parsed.is_integer():
        return None
    return int(parsed)


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _empty_format(source: Path) -> dict[str, Any]:
    return {
        "format_name": None,
        "format_long_name": None,
        "duration_seconds": None,
        "start_time_seconds": None,
        "bit_rate": None,
        "size_bytes": source.stat().st_size,
    }


def _normalize_ffprobe_stream(stream: Mapping[str, Any]) -> dict[str, Any]:
    stream_type = _optional_string(stream.get("codec_type"))
    normalized: dict[str, Any] = {
        "index": _integer(stream.get("index")),
        "type": stream_type,
        "codec_name": _optional_string(stream.get("codec_name")),
        "codec_long_name": _optional_string(stream.get("codec_long_name")),
        "profile": _optional_string(stream.get("profile")),
        "duration_seconds": _number(stream.get("duration")),
        "start_time_seconds": _number(stream.get("start_time")),
        "bit_rate": _integer(stream.get("bit_rate")),
    }
    if stream_type == "video":
        normalized.update(
            {
                "width": _integer(stream.get("width")),
                "height": _integer(stream.get("height")),
                "pixel_format": _optional_string(stream.get("pix_fmt")),
                "frame_rate": parse_rational(stream.get("r_frame_rate")),
                "average_frame_rate": parse_rational(stream.get("avg_frame_rate")),
                "frame_count": _integer(stream.get("nb_frames")),
                "rotation_degrees": _rotation_from_stream(stream),
            }
        )
    elif stream_type == "audio":
        normalized.update(
            {
                "sample_rate": _integer(stream.get("sample_rate")),
                "channels": _integer(stream.get("channels")),
                "channel_layout": _optional_string(stream.get("channel_layout")),
            }
        )
    return normalized


def _rotation_from_stream(stream: Mapping[str, Any]) -> int | float | None:
    tags = stream.get("tags")
    if isinstance(tags, Mapping):
        rotation = _number(tags.get("rotate"))
        if rotation is not None:
            return rotation
    side_data = stream.get("side_data_list")
    if isinstance(side_data, list):
        for item in side_data:
            if isinstance(item, Mapping):
                rotation = _number(item.get("rotation"))
                if rotation is not None:
                    return rotation
    return None


def normalize_ffprobe_json(raw: Any, source: str | os.PathLike[str]) -> dict[str, Any]:
    """Normalize ffprobe JSON to the stable, intentionally small media model."""

    source_path = require_source_file(source)
    if not isinstance(raw, Mapping):
        raise RRVError(ERR_PROBE_FAILED, "ffprobe returned a JSON value other than an object")
    raw_format = raw.get("format")
    format_data = raw_format if isinstance(raw_format, Mapping) else {}
    format_summary = _empty_format(source_path)
    format_summary.update(
        {
            "format_name": _optional_string(format_data.get("format_name")),
            "format_long_name": _optional_string(format_data.get("format_long_name")),
            "duration_seconds": _number(format_data.get("duration")),
            "start_time_seconds": _number(format_data.get("start_time")),
            "bit_rate": _integer(format_data.get("bit_rate")),
            "size_bytes": _integer(format_data.get("size")) or source_path.stat().st_size,
        }
    )
    raw_streams = raw.get("streams")
    if not isinstance(raw_streams, list):
        raise RRVError(ERR_PROBE_FAILED, "ffprobe JSON does not contain a streams array")
    streams = [_normalize_ffprobe_stream(item) for item in raw_streams if isinstance(item, Mapping)]
    return {
        "source_name": source_path.name,
        "format": format_summary,
        "stream_count": len(streams),
        "streams": streams,
    }


def build_ffprobe_command(source: str | os.PathLike[str], ffprobe: str | os.PathLike[str]) -> list[str]:
    """Build the structured probe command; arguments are never shell-joined."""

    return [
        os.fspath(ffprobe),
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(require_source_file(source)),
    ]


def build_ffprobe_exact_timing_command(
    source: str | os.PathLike[str], ffprobe: str | os.PathLike[str]
) -> list[str]:
    """Build a bounded, argv-only exact video-count and PTS inspection command.

    ``nb_frames`` is container metadata and may be absent or stale.  This
    command asks ffprobe to read frames (``-count_frames``) and emits the
    presentation timestamps needed to reject VFR or otherwise ambiguous input
    before a frame-indexed compiler publishes a timeline.
    """

    return [
        os.fspath(ffprobe),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-count_frames",
        "-show_streams",
        "-show_frames",
        "-show_entries",
        "stream=index,codec_type,nb_read_frames,nb_frames,r_frame_rate,avg_frame_rate,time_base:frame=best_effort_timestamp,duration,pkt_duration",
        "-of",
        "json",
        str(require_source_file(source)),
    ]


def _exact_timing_error(message: str) -> RRVError:
    return RRVError(
        ERR_PROBE_FAILED,
        message,
        {"capability": "exact_cfr_frame_timing"},
    )


def _strict_positive_integer(value: Any) -> int | None:
    parsed = _integer(value)
    return parsed if parsed is not None and parsed > 0 else None


def parse_ffprobe_exact_timing_json(raw: Any) -> dict[str, Any]:
    """Validate ffprobe's counted-frame PTS sequence as exact CFR timing.

    The returned count is specifically ``nb_read_frames`` rather than the
    container's optional ``nb_frames`` metadata.  The parser fails closed when
    a timestamp, frame count, rate, time base, or constant PTS step cannot be
    established.
    """

    if not isinstance(raw, Mapping):
        raise _exact_timing_error("ffprobe exact timing result must be an object")
    streams = raw.get("streams")
    if not isinstance(streams, list):
        raise _exact_timing_error("ffprobe exact timing result has no streams array")
    video_streams = [
        stream
        for stream in streams
        if isinstance(stream, Mapping) and stream.get("codec_type") == "video"
    ]
    if len(video_streams) != 1:
        raise _exact_timing_error("ffprobe exact timing requires exactly one selected video stream")
    stream = video_streams[0]
    frame_count = _strict_positive_integer(stream.get("nb_read_frames"))
    if frame_count is None:
        raise _exact_timing_error("ffprobe did not provide a positive nb_read_frames count")
    frame_rate = parse_rational(stream.get("r_frame_rate"))
    average_frame_rate = parse_rational(stream.get("avg_frame_rate"))
    time_base = parse_rational(stream.get("time_base"))
    if (
        frame_rate is None
        or average_frame_rate is None
        or time_base is None
        or frame_rate <= 0
        or average_frame_rate <= 0
        or time_base <= 0
        or not math.isclose(frame_rate, average_frame_rate, rel_tol=1e-9, abs_tol=1e-12)
    ):
        raise _exact_timing_error("ffprobe could not confirm matching positive CFR stream rates")

    frames = raw.get("frames")
    if not isinstance(frames, list) or len(frames) != frame_count:
        raise _exact_timing_error("ffprobe counted frames do not match its PTS frame records")
    timestamps: list[int] = []
    # Some current ffprobe builds omit pkt_duration from frame records even
    # when they expose a complete best-effort PTS sequence.  For multi-frame
    # inputs, that sequence is itself the decisive CFR proof; durations remain
    # an optional consistency check instead of making valid CFR media fail.
    durations: list[int | None] = []
    for frame in frames:
        if not isinstance(frame, Mapping):
            raise _exact_timing_error("ffprobe PTS frame record is invalid")
        timestamp = _integer(frame.get("best_effort_timestamp"))
        # FFprobe 9 emits ``duration`` here, while older builds commonly use
        # ``pkt_duration``.  Ask for and accept both without relaxing cadence
        # validation.
        duration = _strict_positive_integer(frame.get("duration"))
        if duration is None:
            duration = _strict_positive_integer(frame.get("pkt_duration"))
        if timestamp is None:
            raise _exact_timing_error("ffprobe could not confirm a frame PTS")
        timestamps.append(timestamp)
        durations.append(duration)

    if frame_count == 1:
        step = durations[0]
        if step is None:
            raise _exact_timing_error("ffprobe could not confirm a single-frame PTS duration")
    else:
        step = timestamps[1] - timestamps[0]
        if step <= 0 or any(
            timestamps[index] - timestamps[index - 1] != step
            for index in range(2, len(timestamps))
        ):
            raise _exact_timing_error("ffprobe PTS sequence is not constant-frame-rate")
    if any(duration is not None and duration != step for duration in durations):
        raise _exact_timing_error("ffprobe packet durations do not match the constant PTS step")
    pts_fps = 1.0 / (step * time_base)
    if not math.isclose(pts_fps, frame_rate, rel_tol=1e-9, abs_tol=1e-12):
        raise _exact_timing_error("ffprobe PTS cadence does not match the declared frame rate")
    return {
        "frame_count": frame_count,
        "frame_count_source": "ffprobe-nb_read_frames",
        "fps": pts_fps,
        "pts_step": step,
        "time_base": time_base,
        "duration_seconds": frame_count * step * time_base,
        "cfr_confirmed": True,
    }


def probe_exact_video_timing(
    source: str | os.PathLike[str],
    ffprobe: str | os.PathLike[str],
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Read exact frame count and PTS cadence with a caller-provided ffprobe."""

    source_path = require_source_file(source)
    timeout = validate_timeout(timeout_seconds)
    try:
        result = run_command(
            build_ffprobe_exact_timing_command(source_path, ffprobe),
            timeout_seconds=timeout,
            check=True,
        )
    except RRVError as exc:
        raise RRVError(
            ERR_PROBE_FAILED,
            "ffprobe could not confirm exact CFR frame timing",
            {"capability": "exact_cfr_frame_timing", "cause_code": exc.code},
        ) from exc
    try:
        raw = json.loads(result.stdout, parse_constant=_strict_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise _exact_timing_error("ffprobe returned invalid exact timing JSON") from exc
    return parse_ffprobe_exact_timing_json(raw)


def build_ffmpeg_fallback_probe_command(
    source: str | os.PathLike[str], ffmpeg: str | os.PathLike[str]
) -> list[str]:
    """Build the metadata-only fallback command used when ffprobe is absent."""

    return [os.fspath(ffmpeg), "-hide_banner", "-i", str(require_source_file(source))]


_FFMPEG_INPUT_RE = re.compile(r"^\s*Input\s+#\d+,\s*(.+?),\s*from\s+", re.IGNORECASE)
_FFMPEG_DURATION_RE = re.compile(
    r"Duration:\s*(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?|N/A),\s*start:\s*([^,]+),\s*bitrate:\s*([^\s]+)",
    re.IGNORECASE,
)
_FFMPEG_STREAM_RE = re.compile(
    r"^\s*Stream\s+#\d+:(\d+)(?:\[[^\]]*\])?(?:\([^)]*\))?:\s*(Video|Audio|Subtitle|Data):\s*([^,\s]+)(.*)$",
    re.IGNORECASE,
)
_DIMENSIONS_RE = re.compile(r"\b(\d{2,6})x(\d{2,6})\b")
_FPS_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*fps\b", re.IGNORECASE)
_SAMPLE_RATE_RE = re.compile(r"\b(\d+)\s*Hz\b", re.IGNORECASE)
_CHANNELS_RE = re.compile(r"\b(\d+)\s+channels?\b", re.IGNORECASE)
_BITRATE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*kb/s$", re.IGNORECASE)


def _duration_seconds(hours: str, minutes: str, seconds: str) -> float | None:
    try:
        result = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def _ffmpeg_bitrate(value: str) -> int | None:
    match = _BITRATE_RE.match(value.strip())
    if not match:
        return None
    return int(round(float(match.group(1)) * 1000))


def parse_ffmpeg_fallback_output(text: str, source: str | os.PathLike[str]) -> dict[str, Any]:
    """Parse only basic header fields from ffmpeg's human-oriented fallback."""

    source_path = require_source_file(source)
    format_summary = _empty_format(source_path)
    streams: list[dict[str, Any]] = []
    saw_input = False
    for line in text.splitlines():
        input_match = _FFMPEG_INPUT_RE.match(line)
        if input_match:
            saw_input = True
            format_summary["format_name"] = input_match.group(1).strip() or None
            continue
        duration_match = _FFMPEG_DURATION_RE.search(line)
        if duration_match:
            format_summary["duration_seconds"] = _duration_seconds(
                duration_match.group(1), duration_match.group(2), duration_match.group(3)
            )
            format_summary["start_time_seconds"] = _number(duration_match.group(4))
            format_summary["bit_rate"] = _ffmpeg_bitrate(duration_match.group(5))
            continue
        stream_match = _FFMPEG_STREAM_RE.match(line)
        if not stream_match:
            continue
        stream_index, kind, codec, rest = stream_match.groups()
        stream_type = kind.lower()
        stream: dict[str, Any] = {
            "index": int(stream_index),
            "type": stream_type,
            "codec_name": codec,
            "codec_long_name": None,
            "profile": None,
            "duration_seconds": None,
            "start_time_seconds": None,
            "bit_rate": None,
        }
        if stream_type == "video":
            dimensions = _DIMENSIONS_RE.search(rest)
            fps = _FPS_RE.search(rest)
            stream.update(
                {
                    "width": int(dimensions.group(1)) if dimensions else None,
                    "height": int(dimensions.group(2)) if dimensions else None,
                    "pixel_format": None,
                    "frame_rate": float(fps.group(1)) if fps else None,
                    "average_frame_rate": None,
                    "frame_count": None,
                    "rotation_degrees": None,
                }
            )
        elif stream_type == "audio":
            sample_rate = _SAMPLE_RATE_RE.search(rest)
            channels = _CHANNELS_RE.search(rest)
            channel_layout = "stereo" if "stereo" in rest.lower() else "mono" if "mono" in rest.lower() else None
            stream.update(
                {
                    "sample_rate": int(sample_rate.group(1)) if sample_rate else None,
                    "channels": int(channels.group(1)) if channels else 2 if channel_layout == "stereo" else 1 if channel_layout == "mono" else None,
                    "channel_layout": channel_layout,
                }
            )
        streams.append(stream)
    if not saw_input and not streams:
        raise RRVError(ERR_PROBE_FAILED, "ffmpeg fallback did not return recognizable media metadata")
    return {
        "source_name": source_path.name,
        "format": format_summary,
        "stream_count": len(streams),
        "streams": streams,
    }


def _probe_with_ffprobe(source: Path, ffprobe: str, timeout_seconds: float) -> dict[str, Any]:
    try:
        result = run_command(
            build_ffprobe_command(source, ffprobe), timeout_seconds=timeout_seconds, check=True
        )
    except RRVError as exc:
        raise RRVError(
            ERR_PROBE_FAILED,
            "ffprobe could not inspect the source media",
            {"backend": "ffprobe", "cause_code": exc.code},
        ) from exc
    try:
        raw = json.loads(result.stdout, parse_constant=_strict_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise RRVError(
            ERR_PROBE_FAILED,
            "ffprobe returned invalid JSON",
            {"backend": "ffprobe"},
        ) from exc
    media = normalize_ffprobe_json(raw, source)
    return {
        "probe": {"backend": "ffprobe", "capability_level": "full", "limitations": []},
        "media": media,
    }


def _probe_with_ffmpeg_fallback(source: Path, ffmpeg: str, timeout_seconds: float) -> dict[str, Any]:
    try:
        result = run_command(
            build_ffmpeg_fallback_probe_command(source, ffmpeg),
            timeout_seconds=timeout_seconds,
            check=False,
        )
        media = parse_ffmpeg_fallback_output(f"{result.stderr}\n{result.stdout}", source)
    except RRVError as exc:
        if exc.code == ERR_PROBE_FAILED:
            raise
        raise RRVError(
            ERR_PROBE_FAILED,
            "ffmpeg could not inspect the source media",
            {"backend": "ffmpeg-fallback", "cause_code": exc.code},
        ) from exc
    return {
        "probe": {
            "backend": "ffmpeg-fallback",
            "capability_level": "minimal",
            "limitations": [
                "ffprobe is unavailable; stream tags, dispositions, exact per-stream durations, frame counts, and structured metadata are unavailable."
            ],
        },
        "media": media,
    }


def probe_media(
    source: str | os.PathLike[str],
    *,
    tools: RuntimeTools | None = None,
    ffmpeg: str | os.PathLike[str] | None = None,
    ffprobe: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Probe one read-only media input, preferring structured ffprobe JSON.

    If ffprobe is not discoverable but ffmpeg is, a deliberately limited header
    parser is used.  Callers can inspect ``probe.capability_level`` and
    ``probe.limitations`` instead of assuming the results are equivalent.
    """

    source_path = require_source_file(source)
    runtime_tools = tools or discover_tools(
        ffmpeg=ffmpeg, ffprobe=ffprobe, environment=environment
    )
    timeout = validate_timeout(timeout_seconds)
    if runtime_tools.ffprobe.path:
        return _probe_with_ffprobe(source_path, runtime_tools.ffprobe.path, timeout)
    if runtime_tools.ffmpeg.path:
        return _probe_with_ffmpeg_fallback(source_path, runtime_tools.ffmpeg.path, timeout)
    raise RRVError(
        ERR_TOOL_NOT_FOUND,
        "neither ffprobe nor ffmpeg is available; install one locally or pass an explicit path",
        {"required_tools": ["ffprobe", "ffmpeg"]},
    )


def first_stream(media: Mapping[str, Any], stream_type: str) -> Mapping[str, Any] | None:
    """Return the first normalized stream of a requested type."""

    streams = media.get("streams")
    if not isinstance(streams, list):
        return None
    for stream in streams:
        if isinstance(stream, Mapping) and stream.get("type") == stream_type:
            return stream
    return None


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "JSON_SCHEMA_VERSION",
    "MAX_ERROR_TEXT_LENGTH",
    "VERSION_TIMEOUT_SECONDS",
    "CommandResult",
    "RRVError",
    "RuntimeTools",
    "ToolInfo",
    "build_ffmpeg_fallback_probe_command",
    "build_ffprobe_command",
    "build_ffprobe_exact_timing_command",
    "discover_tools",
    "error_payload",
    "first_stream",
    "normalize_ffprobe_json",
    "parse_ffmpeg_fallback_output",
    "parse_ffprobe_exact_timing_json",
    "parse_rational",
    "probe_media",
    "probe_exact_video_timing",
    "probe_tool_version",
    "relative_output_path",
    "require_project_root",
    "require_source_file",
    "resolve_output_path",
    "run_command",
    "stable_json_dumps",
    "success_payload",
    "validate_timeout",
]
