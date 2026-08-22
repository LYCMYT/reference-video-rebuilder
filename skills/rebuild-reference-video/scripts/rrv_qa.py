#!/usr/bin/env python3
"""Deterministic, local technical QA for an encoded delivery video.

This module verifies properties that can be established mechanically: stream
metadata, a complete FFmpeg video decode, decoded-frame count, and simple
duration agreement.  It deliberately does not perform OCR, visual-semantic
review, watermark detection, or claims about removed platform elements.

All subprocess calls are delegated to :mod:`rrv_runtime`, which uses argv-only
execution, no shell, bounded timeouts, and inherited-no-input subprocesses.
The FFmpeg output is the null muxer, so delivery verification never writes to
the source file or to a project directory.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
from pathlib import Path
import re
from collections.abc import Mapping, Sequence
from typing import Any

import rrv_runtime


QA_SCHEMA_VERSION = "1.0"
SHA256_CHUNK_SIZE = 1024 * 1024
# A 0.1% tolerance accepts the common 30000/1001 representation when a
# delivery target is specified as 30 fps, while still rejecting a material
# cadence mismatch.
FPS_RELATIVE_TOLERANCE = 0.001
FPS_ABSOLUTE_TOLERANCE = 0.01

ERR_EXPECTATION_INVALID = "delivery_expectation_invalid"
ERR_DECODE_TIMEOUT = "delivery_decode_timeout"
ERR_DECODE_START_FAILED = "delivery_decode_start_failed"

_FRAME_PROGRESS_RE = re.compile(r"^frame=(\d+)$")
_PROGRESS_END = "progress=end"


def sha256_file(path: str | os.PathLike[str], *, chunk_size: int = SHA256_CHUNK_SIZE) -> str:
    """Return a SHA-256 digest while reading the immutable input only."""

    source = rrv_runtime.require_source_file(path)
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_INVALID_ARGUMENT, "chunk_size must be a positive integer"
        )
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _positive_integer_or_none(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise rrv_runtime.RRVError(
            ERR_EXPECTATION_INVALID, f"{field} must be a positive integer or null"
        )
    return value


def _positive_number_or_none(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise rrv_runtime.RRVError(
            ERR_EXPECTATION_INVALID, f"{field} must be a positive finite number or null"
        )
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise rrv_runtime.RRVError(
            ERR_EXPECTATION_INVALID, f"{field} must be a positive finite number or null"
        )
    return number


def _boolean_or_none(value: Any, field: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise rrv_runtime.RRVError(
            ERR_EXPECTATION_INVALID, f"{field} must be true, false, or null"
        )
    return value


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _stream_fps(stream: Mapping[str, Any]) -> float | None:
    """Prefer average cadence, falling back to the stream's nominal rate."""

    for field in ("average_frame_rate", "frame_rate"):
        value = _finite_number(stream.get(field))
        if value is not None and value > 0:
            return value
    return None


def _compact_text(value: str | None) -> str | None:
    if not value:
        return None
    compact = " ".join(value.strip().split())
    if not compact:
        return None
    limit = rrv_runtime.MAX_ERROR_TEXT_LENGTH
    return compact if len(compact) <= limit else f"{compact[: limit - 1]}…"


def _error_summary(error: rrv_runtime.RRVError) -> dict[str, Any]:
    """Use the runtime's bounded error envelope without leaking a traceback."""

    return error.to_dict()


def _check(
    check_id: str,
    *,
    passed: bool,
    expected: Any = None,
    actual: Any = None,
    message: str,
    applicable: bool = True,
) -> dict[str, Any]:
    """Create a stable, explicit technical check record."""

    return {
        "id": check_id,
        "applicable": applicable,
        "passed": bool(passed),
        "status": "pass" if passed else "fail" if applicable else "not_applicable",
        "expected": expected,
        "actual": actual,
        "message": message,
    }


def _not_applicable(check_id: str, message: str) -> dict[str, Any]:
    return _check(
        check_id,
        passed=True,
        expected=None,
        actual=None,
        message=message,
        applicable=False,
    )


def build_full_decode_command(
    source: str | os.PathLike[str], ffmpeg: str | os.PathLike[str]
) -> list[str]:
    """Build a full first-video-stream decode to FFmpeg's null muxer.

    ``-xerror`` turns decoder errors into a non-zero process result where
    FFmpeg supports it.  ``-progress pipe:1`` is intentionally parsed instead
    of trusting header frame counts, which may be absent or stale.
    """

    source_path = rrv_runtime.require_source_file(source)
    return [
        os.fspath(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-xerror",
        "-progress",
        "pipe:1",
        "-nostats",
        "-i",
        str(source_path),
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
        "-f",
        "null",
        "-",
    ]


def parse_decode_progress(progress_text: str) -> dict[str, Any]:
    """Parse FFmpeg machine progress and require a terminal completion event."""

    if not isinstance(progress_text, str):
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_INVALID_ARGUMENT, "progress_text must be a string"
        )
    frame_count: int | None = None
    completed = False
    for raw_line in progress_text.splitlines():
        line = raw_line.strip()
        match = _FRAME_PROGRESS_RE.fullmatch(line)
        if match:
            frame_count = int(match.group(1))
        elif line == _PROGRESS_END:
            completed = True
    return {"frame_count": frame_count, "completed": completed}


def _decode_full_video(
    source: Path,
    ffmpeg: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Decode every video frame and return a bounded success/failure record."""

    command = build_full_decode_command(source, ffmpeg)
    try:
        command_result = rrv_runtime.run_command(
            command, timeout_seconds=timeout_seconds, check=False
        )
    except rrv_runtime.RRVError as exc:
        if exc.code == rrv_runtime.ERR_TOOL_TIMEOUT:
            raise rrv_runtime.RRVError(
                ERR_DECODE_TIMEOUT,
                "full video decode exceeded the timeout",
                {"cause_code": exc.code, **exc.details},
            ) from exc
        if exc.code in (rrv_runtime.ERR_TOOL_NOT_FOUND, rrv_runtime.ERR_TOOL_EXECUTION):
            raise rrv_runtime.RRVError(
                ERR_DECODE_START_FAILED,
                "could not start the full video decode",
                {"cause_code": exc.code, **exc.details},
            ) from exc
        raise

    progress = parse_decode_progress(command_result.stdout)
    diagnostics = _compact_text(command_result.stderr)
    if command_result.returncode != 0:
        return {
            "attempted": True,
            "passed": False,
            "frame_count": progress["frame_count"],
            "completed": progress["completed"],
            "returncode": command_result.returncode,
            "error": {
                "code": "ffmpeg_decode_failed",
                "message": "ffmpeg reported a failed full video decode",
                "details": {
                    "returncode": command_result.returncode,
                    **({"output": diagnostics} if diagnostics else {}),
                },
            },
        }
    if not progress["completed"] or progress["frame_count"] is None:
        return {
            "attempted": True,
            "passed": False,
            "frame_count": progress["frame_count"],
            "completed": progress["completed"],
            "returncode": command_result.returncode,
            "error": {
                "code": "decode_progress_incomplete",
                "message": "ffmpeg completed without a terminal decoded-frame count",
                "details": {
                    "completed": progress["completed"],
                    **({"output": diagnostics} if diagnostics else {}),
                },
            },
        }
    return {
        "attempted": True,
        "passed": True,
        "frame_count": progress["frame_count"],
        "completed": True,
        "returncode": command_result.returncode,
    }


def _source_summary(source: Path) -> dict[str, Any]:
    return {
        "name": source.name,
        "size_bytes": source.stat().st_size,
        "sha256": sha256_file(source),
    }


def _base_result(
    source: Path,
    *,
    expected_width: int | None,
    expected_height: int | None,
    expected_fps: float | None,
    expected_frames: int | None,
    expect_audio: bool | None,
) -> dict[str, Any]:
    return {
        "schema_version": QA_SCHEMA_VERSION,
        "source": _source_summary(source),
        "expectations": {
            "width": expected_width,
            "height": expected_height,
            "fps": expected_fps,
            "frames": expected_frames,
            "audio": expect_audio,
        },
    }


def _finish(result: dict[str, Any], checks: list[dict[str, Any]]) -> dict[str, Any]:
    result["checks"] = checks
    passed = all(item["passed"] for item in checks if item["applicable"])
    result["passed"] = passed
    result["status"] = "pass" if passed else "fail"
    return result


def _probe_failure_result(
    result: dict[str, Any], error: rrv_runtime.RRVError
) -> dict[str, Any]:
    result["probe"] = {"backend": None, "error": _error_summary(error)}
    result["probe_backend"] = None
    result["decode"] = {"attempted": False, "passed": False, "frame_count": None}
    result["decode_frame_count"] = None
    return _finish(
        result,
        [
            _check(
                "probe_metadata",
                passed=False,
                message="media metadata could not be probed",
                actual=_error_summary(error),
            ),
            _not_applicable("full_video_decode", "full decode was not attempted after probe failure"),
        ],
    )


def verify_delivery(
    source: str | os.PathLike[str],
    expected_width: int | None = None,
    expected_height: int | None = None,
    expected_fps: float | None = None,
    expected_frames: int | None = None,
    expect_audio: bool | None = None,
    *,
    ffmpeg: str | os.PathLike[str] | None = None,
    ffprobe: str | os.PathLike[str] | None = None,
    tools: rrv_runtime.RuntimeTools | None = None,
    timeout_seconds: float = rrv_runtime.DEFAULT_TIMEOUT_SECONDS,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Verify one local delivery without modifying it.

    Content mismatches and a failed FFmpeg decode are reported as a normal,
    structured ``status: fail`` result.  Invalid API arguments, inaccessible
    input, unavailable tools, and timeouts remain bounded :class:`RRVError`
    failures because a verification result cannot be established in those
    situations.
    """

    width = _positive_integer_or_none(expected_width, "expected_width")
    height = _positive_integer_or_none(expected_height, "expected_height")
    fps = _positive_number_or_none(expected_fps, "expected_fps")
    frames = _positive_integer_or_none(expected_frames, "expected_frames")
    audio_expectation = _boolean_or_none(expect_audio, "expect_audio")
    # ``timeout`` is the compact public spelling in the QA contract; retain
    # ``timeout_seconds`` to match the shared runtime API and existing callers.
    if timeout is not None:
        if timeout_seconds != rrv_runtime.DEFAULT_TIMEOUT_SECONDS:
            normalized_explicit = rrv_runtime.validate_timeout(timeout_seconds)
            normalized_alias = rrv_runtime.validate_timeout(timeout)
            if normalized_explicit != normalized_alias:
                raise rrv_runtime.RRVError(
                    ERR_EXPECTATION_INVALID,
                    "timeout and timeout_seconds must agree when both are supplied",
                )
        timeout_seconds = timeout
    timeout_value = rrv_runtime.validate_timeout(timeout_seconds)
    source_path = rrv_runtime.require_source_file(source)
    runtime_tools = tools or rrv_runtime.discover_tools(ffmpeg=ffmpeg, ffprobe=ffprobe)
    if not runtime_tools.ffmpeg.path:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_CAPABILITY_UNAVAILABLE,
            "delivery verification requires a local ffmpeg executable for full decode",
            {"capability": "full_video_decode", "missing_tool": "ffmpeg"},
        )

    result = _base_result(
        source_path,
        expected_width=width,
        expected_height=height,
        expected_fps=fps,
        expected_frames=frames,
        expect_audio=audio_expectation,
    )
    try:
        probe_result = rrv_runtime.probe_media(
            source_path, tools=runtime_tools, timeout_seconds=timeout_value
        )
    except rrv_runtime.RRVError as exc:
        # A malformed/corrupt container is a verification failure, while a
        # missing executable and a timeout stay operational errors for callers.
        if exc.code in (rrv_runtime.ERR_PROBE_FAILED, rrv_runtime.ERR_TOOL_EXECUTION):
            return _probe_failure_result(result, exc)
        raise

    probe = probe_result.get("probe")
    media = probe_result.get("media")
    if not isinstance(probe, Mapping) or not isinstance(media, Mapping):
        return _probe_failure_result(
            result,
            rrv_runtime.RRVError(
                rrv_runtime.ERR_PROBE_FAILED, "probe returned an invalid media result"
            ),
        )
    result["probe"] = dict(probe)
    result["probe_backend"] = probe.get("backend")

    video_stream = rrv_runtime.first_stream(media, "video")
    audio_stream = rrv_runtime.first_stream(media, "audio")
    if video_stream is None:
        result["decode"] = {"attempted": False, "passed": False, "frame_count": None}
        result["decode_frame_count"] = None
        return _finish(
            result,
            [
                _check(
                    "video_stream_present",
                    passed=False,
                    expected=True,
                    actual=False,
                    message="delivery has no video stream to decode",
                ),
                _not_applicable("full_video_decode", "no video stream is available to decode"),
            ],
        )

    decode = _decode_full_video(
        source_path, runtime_tools.ffmpeg.path, timeout_seconds=timeout_value
    )
    result["decode"] = decode
    result["decode_frame_count"] = decode.get("frame_count")

    checks: list[dict[str, Any]] = [
        _check(
            "video_stream_present",
            passed=True,
            expected=True,
            actual=True,
            message="delivery contains a video stream",
        ),
        _check(
            "full_video_decode",
            passed=bool(decode["passed"]),
            expected="complete ffmpeg decode",
            actual={
                "completed": decode.get("completed"),
                "frame_count": decode.get("frame_count"),
                "returncode": decode.get("returncode"),
            },
            message=(
                "ffmpeg decoded the complete video stream"
                if decode["passed"]
                else str(decode.get("error", {}).get("message", "full video decode failed"))
            ),
        ),
    ]

    actual_width = video_stream.get("width")
    if width is None:
        checks.append(_not_applicable("width", "no expected width was supplied"))
    else:
        checks.append(
            _check(
                "width",
                passed=actual_width == width,
                expected=width,
                actual=actual_width,
                message="video width matches the expected delivery width",
            )
        )

    actual_height = video_stream.get("height")
    if height is None:
        checks.append(_not_applicable("height", "no expected height was supplied"))
    else:
        checks.append(
            _check(
                "height",
                passed=actual_height == height,
                expected=height,
                actual=actual_height,
                message="video height matches the expected delivery height",
            )
        )

    actual_fps = _stream_fps(video_stream)
    if fps is None:
        checks.append(_not_applicable("fps", "no expected fps was supplied"))
    elif actual_fps is None:
        checks.append(
            _check(
                "fps",
                passed=False,
                expected=fps,
                actual=None,
                message="video frame rate is unavailable from probe metadata",
            )
        )
    else:
        tolerance = max(FPS_ABSOLUTE_TOLERANCE, fps * FPS_RELATIVE_TOLERANCE)
        checks.append(
            _check(
                "fps",
                passed=abs(actual_fps - fps) <= tolerance,
                expected={"value": fps, "tolerance": tolerance},
                actual=actual_fps,
                message="video frame rate is within delivery tolerance",
            )
        )

    decoded_frames = decode.get("frame_count")
    if frames is None:
        checks.append(_not_applicable("frame_count", "no expected frame count was supplied"))
    else:
        checks.append(
            _check(
                "frame_count",
                passed=decode["passed"] and decoded_frames == frames,
                expected=frames,
                actual=decoded_frames,
                message="decoded frame count exactly matches the expected frame count",
            )
        )

    actual_audio = audio_stream is not None
    if audio_expectation is None:
        checks.append(_not_applicable("audio_presence", "no audio expectation was supplied"))
    else:
        checks.append(
            _check(
                "audio_presence",
                passed=actual_audio == audio_expectation,
                expected=audio_expectation,
                actual=actual_audio,
                message="audio stream presence matches the delivery expectation",
            )
        )

    format_data = media.get("format")
    container_duration = (
        _finite_number(format_data.get("duration_seconds"))
        if isinstance(format_data, Mapping)
        else None
    )
    video_duration = _finite_number(video_stream.get("duration_seconds"))
    duration_fps = actual_fps or fps
    if (
        container_duration is None
        or video_duration is None
        or duration_fps is None
        or duration_fps <= 0
    ):
        checks.append(
            _not_applicable(
                "container_video_duration",
                "container/video duration comparison is unavailable from probe metadata",
            )
        )
    else:
        maximum_delta = 1.0 / duration_fps
        actual_delta = abs(container_duration - video_duration)
        checks.append(
            _check(
                "container_video_duration",
                passed=actual_delta <= maximum_delta + 1e-12,
                expected={"maximum_delta_seconds": maximum_delta},
                actual={
                    "container_seconds": container_duration,
                    "video_seconds": video_duration,
                    "delta_seconds": actual_delta,
                },
                message="container and video durations differ by no more than one frame",
            )
        )

    return _finish(result, checks)


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # pragma: no cover - Python formatting varies.
        raise rrv_runtime.RRVError(rrv_runtime.ERR_INVALID_ARGUMENT, message)


def build_parser() -> argparse.ArgumentParser:
    """Build the stable JSON CLI parser."""

    parser = _JsonArgumentParser(prog="rrv-qa")
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify", help="Verify one encoded delivery video")
    verify.add_argument("source", type=Path)
    verify.add_argument("--width", dest="expected_width", type=int)
    verify.add_argument("--height", dest="expected_height", type=int)
    verify.add_argument("--fps", dest="expected_fps", type=float)
    verify.add_argument("--frames", dest="expected_frames", type=int)
    audio_group = verify.add_mutually_exclusive_group()
    audio_group.add_argument("--expect-audio", dest="expect_audio", action="store_true")
    audio_group.add_argument("--expect-no-audio", dest="expect_audio", action="store_false")
    verify.set_defaults(expect_audio=None)
    verify.add_argument("--ffmpeg", type=Path, help="Explicit local ffmpeg executable")
    verify.add_argument("--ffprobe", type=Path, help="Explicit local ffprobe executable")
    verify.add_argument("--timeout", type=float, default=rrv_runtime.DEFAULT_TIMEOUT_SECONDS)
    verify.add_argument("--json", action="store_true", help="Output is always stable JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``verify`` and return conventional verification/operational codes."""

    try:
        args = build_parser().parse_args(argv)
        result = verify_delivery(
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
        payload = rrv_runtime.success_payload(result)
        status = 0 if result["passed"] else 1
    except rrv_runtime.RRVError as exc:
        payload = rrv_runtime.error_payload(exc)
        status = 2
    print(rrv_runtime.stable_json_dumps(payload))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
