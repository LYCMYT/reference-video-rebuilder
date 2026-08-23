#!/usr/bin/env python3
"""Bounded local compiler for the fixed-subject-carousel S1 template family.

The compiler intentionally has a small, explicit surface.  It turns an
authorized, fixed-camera reference into a renderer-ready Template IR without
ever making the reference video a composited visual layer.  Analysis data is
kept in a private staging directory and is removed before the final artifact
directory is published.

It uses only the local runtime/analyzer primitives bundled with the skill and
the optional Pillow dependency already used by ``rrv_analyze``.
"""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Callable, Iterable, Mapping, Sequence

try:  # Direct execution from the scripts directory.
    import rrv_analyze
    import rrv_runtime
except ImportError:  # pragma: no cover - useful when installed as a package.
    from . import rrv_analyze, rrv_runtime  # type: ignore[no-redef]


COMPILER_SCHEMA_VERSION = "0.3.0"
TEMPLATE_IR_SCHEMA_VERSION = "0.2.0"
MAX_DURATION_SECONDS = 60.0
# A 256 px wide analysis image can still be very tall for malformed source
# geometry.  This cap keeps the temporary rawvideo artifact bounded without
# constraining ordinary portrait or landscape material.
MAX_ANALYSIS_PIXELS = 1_048_576
MAX_ANALYSIS_RAW_BYTES = 512 * 1024 * 1024
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMPILER_PLAN_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "assets" / "schemas" / "compiler-plan.schema.json"
)
_OUTPUT_PROFILES: dict[str, tuple[int, int, str]] = {
    "720x1280": (720, 1280, "vertical-720"),
    "1080x1920": (1080, 1920, "vertical-1080"),
}
_IMAGE_MEDIA = ["image/jpeg", "image/png", "image/webp"]
_AUDIO_MEDIA = ["audio/wav", "audio/mpeg", "audio/mp4", "audio/x-matroska"]
_compiler_plan_validator: Any | None = None


@dataclass(frozen=True)
class MediaInfo:
    """The deliberately small S1-safe portion of normalized media metadata."""

    duration_frames: int
    duration_seconds: float
    fps: float
    width: int
    height: int
    audio_available: bool


@dataclass(frozen=True)
class TimingDecision:
    """Final timeline boundaries plus compact hybrid-analysis decisions."""

    ranges: tuple[tuple[int, int], ...]
    switch_frames: tuple[int, ...]
    fallback_frames: tuple[int, ...]
    decisions: tuple[dict[str, Any], ...]

    @property
    def review_required(self) -> bool:
        return bool(self.fallback_frames)


def _error(message: str, *, details: Mapping[str, Any] | None = None) -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(rrv_runtime.ERR_INVALID_ARGUMENT, message, details)


def _capability_error(message: str, *, details: Mapping[str, Any] | None = None) -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(rrv_runtime.ERR_CAPABILITY_UNAVAILABLE, message, details)


def _tool_error(message: str, *, details: Mapping[str, Any] | None = None) -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(rrv_runtime.ERR_TOOL_EXECUTION, message, details)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _error(f"{field} must be a finite number")
    return float(value)


def _positive_int(value: Any, field: str) -> int:
    if not _is_int(value) or value < 1:
        raise _error(f"{field} must be a positive integer")
    return value


def _nonnegative_int(value: Any, field: str) -> int:
    if not _is_int(value) or value < 0:
        raise _error(f"{field} must be a non-negative integer")
    return value


def _require_object(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _error(f"{field} must be an object")
    return value


def _require_exact_keys(
    value: Mapping[str, Any],
    field: str,
    required: Iterable[str],
    optional: Iterable[str] = (),
) -> None:
    required_set = set(required)
    allowed = required_set | set(optional)
    present = set(value.keys())
    missing = sorted(required_set - present)
    unknown = sorted(str(item) for item in present - allowed)
    if missing:
        raise _error(f"{field} is missing required fields: {', '.join(missing)}")
    if unknown:
        raise _error(f"{field} contains unsupported fields: {', '.join(unknown)}")


def _compiler_plan_validator_instance() -> Any:
    global _compiler_plan_validator
    if _compiler_plan_validator is not None:
        return _compiler_plan_validator
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:
        raise _capability_error(
            "Compiler Plan JSON Schema validation requires the jsonschema runtime dependency",
            details={"capability": "compiler_plan_schema_validation"},
        ) from exc
    try:
        with _COMPILER_PLAN_SCHEMA_PATH.open("r", encoding="utf-8") as handle:
            schema = json.load(handle)
        Draft202012Validator.check_schema(schema)
        _compiler_plan_validator = Draft202012Validator(schema)
    except (OSError, ValueError) as exc:
        raise _capability_error(
            "Compiler Plan JSON Schema is unavailable",
            details={"capability": "compiler_plan_schema_validation"},
        ) from exc
    return _compiler_plan_validator


def _schema_error_path(error: Any) -> str:
    path = "$"
    for item in error.absolute_path:
        path += f"[{item}]" if isinstance(item, int) else f".{item}"
    return path


def _validate_compiler_plan_schema(plan: Mapping[str, Any]) -> None:
    """Execute the frozen JSON Schema before semantic compiler validation."""

    validator = _compiler_plan_validator_instance()
    errors = sorted(
        validator.iter_errors(plan),
        key=lambda error: (tuple(str(item) for item in error.absolute_path), error.message),
    )
    if errors:
        raise _error(
            "Compiler Plan did not pass JSON Schema validation",
            details={"errors": [f"{_schema_error_path(error)}: {error.message}" for error in errors[:8]]},
        )


def _pixel_rect(value: Any, field: str) -> dict[str, int]:
    data = _require_object(value, field)
    _require_exact_keys(data, field, ("x", "y", "width", "height"))
    x = _nonnegative_int(data["x"], f"{field}.x")
    y = _nonnegative_int(data["y"], f"{field}.y")
    width = _positive_int(data["width"], f"{field}.width")
    height = _positive_int(data["height"], f"{field}.height")
    return {"x": x, "y": y, "width": width, "height": height}


def _point(value: Any, field: str) -> dict[str, float | int]:
    data = _require_object(value, field)
    _require_exact_keys(data, field, ("x", "y"))
    x = _finite_number(data["x"], f"{field}.x")
    y = _finite_number(data["y"], f"{field}.y")
    # Preserve integer values in serialized IR when the input was integral.
    return {
        "x": int(x) if x.is_integer() else x,
        "y": int(y) if y.is_integer() else y,
    }


def _rect_within(rect: Mapping[str, int], width: int, height: int, field: str) -> None:
    if rect["x"] + rect["width"] > width or rect["y"] + rect["height"] > height:
        raise _error(f"{field} must stay within its declared coordinate space")


def _format_number(value: float | int) -> float | int:
    """Normalize -0.0 and preserve integral values in deterministic JSON."""

    numeric = float(value)
    if numeric == 0:
        return 0
    return int(numeric) if numeric.is_integer() else numeric


def balanced_ranges(duration_frames: int, slot_count: int) -> list[tuple[int, int]]:
    """Return contiguous non-empty balanced half-open ranges.

    Remainder frames are assigned to the earliest ranges.  For example,
    ``balanced_ranges(347, 12)`` begins ``(0, 29), (29, 58)`` and ends
    ``(319, 347)``.
    """

    duration = _positive_int(duration_frames, "duration_frames")
    slots = _positive_int(slot_count, "slot_count")
    if slots > duration:
        raise _error("slot_count cannot exceed duration_frames because every segment must contain a frame")
    base, remainder = divmod(duration, slots)
    start = 0
    ranges: list[tuple[int, int]] = []
    for index in range(slots):
        length = base + (1 if index < remainder else 0)
        end = start + length
        ranges.append((start, end))
        start = end
    return ranges


def _switches_from_ranges(ranges: Sequence[tuple[int, int]]) -> tuple[int, ...]:
    return tuple(end for _, end in ranges[:-1])


def _ranges_from_switches(
    duration_frames: int,
    slot_count: int,
    switch_frames: Sequence[int],
    min_segment_frames: int,
    *,
    field: str = "switch_frames",
) -> list[tuple[int, int]]:
    duration = _positive_int(duration_frames, "duration_frames")
    slots = _positive_int(slot_count, "slot_count")
    minimum = _positive_int(min_segment_frames, "min_segment_frames")
    if len(switch_frames) != slots - 1:
        raise _error(f"{field} must contain exactly slot_count - 1 frames")
    boundaries = [0]
    prior = 0
    for index, frame in enumerate(switch_frames):
        if not _is_int(frame):
            raise _error(f"{field}[{index}] must be an integer frame number")
        if not 0 < frame < duration:
            raise _error(f"{field}[{index}] must be strictly inside the source duration")
        if frame <= prior:
            raise _error(f"{field} must be strictly increasing")
        boundaries.append(frame)
        prior = frame
    boundaries.append(duration)
    ranges = list(zip(boundaries, boundaries[1:]))
    if any(end - start < minimum for start, end in ranges):
        raise _error(f"{field} creates a segment shorter than min_segment_frames")
    return ranges


def _media_info(media: Mapping[str, Any], *, require_exact_timing: bool = False) -> MediaInfo:
    data = _require_object(media, "media")
    streams = data.get("streams")
    if not isinstance(streams, list):
        raise _error("media.streams must be an array")
    video_streams = [stream for stream in streams if isinstance(stream, Mapping) and stream.get("type") == "video"]
    if len(video_streams) != 1:
        raise _capability_error(
            "S1 compilation requires exactly one video stream",
            details={"capability": "single_video_s1", "video_stream_count": len(video_streams)},
        )
    video = video_streams[0]
    width = _positive_int(video.get("width"), "media.video.width")
    height = _positive_int(video.get("height"), "media.video.height")

    rate_values: list[float] = []
    for key in ("frame_rate", "average_frame_rate"):
        value = video.get(key)
        if value is None:
            continue
        candidate = _finite_number(value, f"media.video.{key}")
        if candidate <= 0:
            raise _capability_error("S1 compilation requires a positive video frame rate")
        rate_values.append(candidate)
    if not rate_values:
        raise _capability_error("S1 compilation requires a known constant frame rate")
    if len(rate_values) == 2 and not math.isclose(rate_values[0], rate_values[1], rel_tol=1e-6, abs_tol=1e-6):
        raise _capability_error(
            "S1 compilation rejects variable-frame-rate media",
            details={"capability": "constant_frame_rate"},
        )
    fps = rate_values[-1]  # Prefer ffprobe's average rate when both agree.

    rotation = video.get("rotation_degrees")
    if rotation is not None:
        rotation_value = _finite_number(rotation, "media.video.rotation_degrees")
        normalized = rotation_value % 360.0
        if min(abs(normalized), abs(normalized - 360.0)) > 0.01:
            raise _capability_error(
                "S1 compilation rejects material source rotation",
                details={"capability": "unrotated_source"},
            )

    raw_count = video.get("frame_count")
    if not _is_int(raw_count) or raw_count < 1:
        raise _capability_error(
            "S1 compilation requires an exact positive video frame count",
            details={"capability": "exact_cfr_frame_timing"},
        )
    if require_exact_timing and video.get("cfr_confirmed") is not True:
        raise _capability_error(
            "S1 compilation requires confirmed CFR PTS timing",
            details={"capability": "exact_cfr_frame_timing"},
        )

    format_data = data.get("format")
    # Once compile_reference has verified frame records, use their PTS span
    # rather than a container duration that may have been rounded or include
    # edit-list padding.  Direct Template IR construction keeps accepting its
    # caller's ordinary normalized-media duration.
    duration_candidates: list[Any] = []
    if require_exact_timing:
        duration_candidates.append(video.get("exact_duration_seconds"))
    if isinstance(format_data, Mapping):
        duration_candidates.append(format_data.get("duration_seconds"))
    duration_candidates.append(video.get("duration_seconds"))
    if not require_exact_timing:
        duration_candidates.append(video.get("exact_duration_seconds"))
    duration_metadata: float | None = None
    for candidate in duration_candidates:
        if candidate is None:
            continue
        value = _finite_number(candidate, "media.duration_seconds")
        if value > 0:
            duration_metadata = value
            break
    duration_frames = raw_count
    duration_seconds = duration_metadata if duration_metadata is not None else duration_frames / fps
    if duration_seconds > MAX_DURATION_SECONDS + 1e-9:
        raise _capability_error(
            "S1 compilation supports reference videos of at most 60 seconds",
            details={"capability": "duration_limit", "max_seconds": MAX_DURATION_SECONDS},
        )
    # A gross mismatch is a useful VFR/timing-integrity signal.  One source
    # frame of tolerance accommodates container duration rounding.
    if duration_metadata is not None and abs(duration_frames / fps - duration_metadata) > 1.0 / fps + 1e-6:
        raise _capability_error(
            "S1 compilation requires frame count and duration to agree for CFR timing",
            details={"capability": "constant_frame_rate"},
        )
    return MediaInfo(
        duration_frames=duration_frames,
        duration_seconds=duration_seconds,
        fps=fps,
        width=width,
        height=height,
        audio_available=any(isinstance(stream, Mapping) and stream.get("type") == "audio" for stream in streams),
    )


def _validate_plan(plan: Mapping[str, Any], media: MediaInfo) -> dict[str, Any]:
    _validate_compiler_plan_schema(plan)
    data = _require_object(plan, "plan")
    _require_exact_keys(
        data,
        "plan",
        (
            "schema_version",
            "template_id",
            "family",
            "authorization",
            "privacy",
            "geometry",
            "timing",
            "carousel",
            "background",
            "audio",
            "output_profiles",
            "analysis",
        ),
    )
    if data.get("schema_version") != COMPILER_SCHEMA_VERSION:
        raise _error(f"plan.schema_version must be {COMPILER_SCHEMA_VERSION!r}")
    template_id = data.get("template_id")
    if not isinstance(template_id, str) or not _ID_RE.fullmatch(template_id):
        raise _error("plan.template_id must be a Template IR-compatible identifier")
    if data.get("family") != "fixed-subject-carousel":
        raise _capability_error(
            "this compiler supports only the fixed-subject-carousel S1 family",
            details={"capability": "fixed_subject_carousel"},
        )

    authorization = _require_object(data.get("authorization"), "plan.authorization")
    _require_exact_keys(
        authorization,
        "plan.authorization",
        ("reference_rights_confirmed", "audio_rights_confirmed"),
    )
    if authorization.get("reference_rights_confirmed") is not True:
        raise _error("plan.authorization.reference_rights_confirmed must be true")
    if not isinstance(authorization.get("audio_rights_confirmed"), bool):
        raise _error("plan.authorization.audio_rights_confirmed must be a boolean")
    if data.get("privacy") != "local-only":
        raise _error("plan.privacy must be 'local-only'")

    geometry = _require_object(data.get("geometry"), "plan.geometry")
    _require_exact_keys(geometry, "plan.geometry", ("source_rect", "carousel_rect", "subject_rect"))
    source_rect = _pixel_rect(geometry["source_rect"], "plan.geometry.source_rect")
    carousel_rect = _pixel_rect(geometry["carousel_rect"], "plan.geometry.carousel_rect")
    subject_rect = _pixel_rect(geometry["subject_rect"], "plan.geometry.subject_rect")
    _rect_within(source_rect, media.width, media.height, "plan.geometry.source_rect")
    canvas_width, canvas_height = source_rect["width"], source_rect["height"]
    _rect_within(carousel_rect, canvas_width, canvas_height, "plan.geometry.carousel_rect")
    _rect_within(subject_rect, canvas_width, canvas_height, "plan.geometry.subject_rect")

    timing = _require_object(data.get("timing"), "plan.timing")
    _require_exact_keys(
        timing,
        "plan.timing",
        ("slot_count", "mode", "min_segment_frames"),
        ("switch_frames",),
    )
    slot_count = _positive_int(timing["slot_count"], "plan.timing.slot_count")
    if slot_count > 64:
        raise _error("plan.timing.slot_count must be between 1 and 64")
    timing_mode = timing.get("mode")
    if timing_mode not in {"uniform", "hybrid", "manual"}:
        raise _error("plan.timing.mode must be uniform, hybrid, or manual")
    min_segment_frames = _positive_int(
        timing["min_segment_frames"], "plan.timing.min_segment_frames"
    )
    if slot_count * min_segment_frames > media.duration_frames:
        raise _error("plan.timing cannot fit the requested minimum segment length in the source duration")
    raw_manual_switches = timing.get("switch_frames")
    if timing_mode == "manual":
        if not isinstance(raw_manual_switches, list):
            raise _error("plan.timing.switch_frames is required for manual timing")
        # Frozen plans express every segment start, including the mandatory
        # first start at frame zero.  Internally the IR builder keeps only the
        # nonzero boundaries because its ranges already imply frame zero.
        manual_starts = tuple(raw_manual_switches)
        if len(manual_starts) != slot_count:
            raise _error("plan.timing.switch_frames must contain exactly slot_count segment starts")
        if not manual_starts or not _is_int(manual_starts[0]) or manual_starts[0] != 0:
            raise _error("plan.timing.switch_frames must begin with frame 0")
        manual_switches = manual_starts[1:]
        _ranges_from_switches(
            media.duration_frames,
            slot_count,
            manual_switches,
            min_segment_frames,
            field="plan.timing.switch_frames",
        )
    else:
        if "switch_frames" in timing:
            raise _error("plan.timing.switch_frames is allowed only when timing.mode is manual")
        manual_switches = ()
        manual_starts = ()

    carousel = _require_object(data.get("carousel"), "plan.carousel")
    _require_exact_keys(
        carousel,
        "plan.carousel",
        ("origin", "item_width", "item_height", "gap"),
        ("end_offset_x",),
    )
    origin = _point(carousel["origin"], "plan.carousel.origin")
    item_width = _finite_number(carousel["item_width"], "plan.carousel.item_width")
    item_height = _finite_number(carousel["item_height"], "plan.carousel.item_height")
    gap = _finite_number(carousel["gap"], "plan.carousel.gap")
    if item_width <= 0 or item_height <= 0 or gap < 0:
        raise _error("plan.carousel item_width/item_height must be positive and gap must be non-negative")
    origin_x, origin_y = float(origin["x"]), float(origin["y"])
    if not (0 <= origin_x <= canvas_width and 0 <= origin_y <= canvas_height):
        raise _error("plan.carousel.origin must be a point inside the canvas")
    # The initial item must be fully visible within the declared carousel
    # viewport.  Later items are intentionally allowed to extend horizontally
    # beyond it and are clipped by the generated carousel track.
    if not (
        carousel_rect["x"] <= origin_x
        and carousel_rect["y"] <= origin_y
        and origin_x + item_width <= carousel_rect["x"] + carousel_rect["width"]
        and origin_y + item_height <= carousel_rect["y"] + carousel_rect["height"]
    ):
        raise _error("plan.carousel.origin and item size must fit within plan.geometry.carousel_rect")
    if "end_offset_x" in carousel:
        end_offset_x = _finite_number(carousel["end_offset_x"], "plan.carousel.end_offset_x")
        if end_offset_x > 0:
            raise _error("plan.carousel.end_offset_x must be less than or equal to zero")
    else:
        content_width = slot_count * item_width + (slot_count - 1) * gap
        end_offset_x = min(
            0.0,
            float(carousel_rect["x"] + carousel_rect["width"]) - (origin_x + content_width),
        )

    background = _require_object(data.get("background"), "plan.background")
    _require_exact_keys(background, "plan.background", ("color", "replaceable"))
    color = background.get("color")
    if not isinstance(color, str) or not _COLOR_RE.fullmatch(color):
        raise _error("plan.background.color must be a #RRGGBB color")
    if not isinstance(background.get("replaceable"), bool):
        raise _error("plan.background.replaceable must be a boolean")

    audio = _require_object(data.get("audio"), "plan.audio")
    _require_exact_keys(audio, "plan.audio", ("mode", "required"))
    audio_mode = audio.get("mode")
    if audio_mode not in {"preserve", "replaceable", "mute"}:
        raise _error("plan.audio.mode must be preserve, replaceable, or mute")
    if not isinstance(audio.get("required"), bool):
        raise _error("plan.audio.required must be a boolean")
    if audio_mode == "preserve":
        if audio["required"] is not True:
            raise _error("preserve audio requires plan.audio.required=true")
        if authorization["audio_rights_confirmed"] is not True:
            raise _error("preserve audio requires plan.authorization.audio_rights_confirmed=true")
        if not media.audio_available:
            raise _capability_error(
                "preserve audio requires a source audio stream",
                details={"capability": "preserve_source_audio"},
            )
    elif audio_mode == "mute" and audio["required"] is not False:
        raise _error("mute audio requires plan.audio.required=false")

    profiles = data.get("output_profiles")
    if not isinstance(profiles, list) or not profiles:
        raise _error("plan.output_profiles must be a non-empty array")
    if any(not isinstance(profile, str) for profile in profiles):
        raise _error("plan.output_profiles must contain profile strings")
    if len(profiles) != len(set(profiles)):
        raise _error("plan.output_profiles must be unique")
    if any(profile not in _OUTPUT_PROFILES for profile in profiles):
        raise _error("plan.output_profiles must be a subset of 720x1280 and 1080x1920")

    analysis = _require_object(data.get("analysis"), "plan.analysis")
    _require_exact_keys(
        analysis,
        "plan.analysis",
        ("width", "snap_window_frames", "min_prominence", "max_evidence_frames"),
    )
    analysis_width = _positive_int(analysis["width"], "plan.analysis.width")
    if not 32 <= analysis_width <= 256:
        raise _error("plan.analysis.width must be between 32 and 256")
    snap_window_frames = _nonnegative_int(
        analysis["snap_window_frames"], "plan.analysis.snap_window_frames"
    )
    min_prominence = _finite_number(analysis["min_prominence"], "plan.analysis.min_prominence")
    if min_prominence < 0:
        raise _error("plan.analysis.min_prominence must be non-negative")
    max_evidence_frames = _positive_int(
        analysis["max_evidence_frames"], "plan.analysis.max_evidence_frames"
    )
    if max_evidence_frames > 64:
        raise _error("plan.analysis.max_evidence_frames must be between 1 and 64")

    return {
        "template_id": template_id,
        "authorization": {
            "reference_rights_confirmed": True,
            "audio_rights_confirmed": authorization["audio_rights_confirmed"],
        },
        "source_rect": source_rect,
        "carousel_rect": carousel_rect,
        "subject_rect": subject_rect,
        "slot_count": slot_count,
        "timing_mode": timing_mode,
        "min_segment_frames": min_segment_frames,
        "manual_switches": tuple(int(frame) for frame in manual_switches),
        "manual_starts": tuple(int(frame) for frame in manual_starts),
        "origin": origin,
        "item_width": _format_number(item_width),
        "item_height": _format_number(item_height),
        "gap": _format_number(gap),
        "end_offset_x": _format_number(end_offset_x),
        "background_color": color.upper(),
        "background_replaceable": background["replaceable"],
        "audio_mode": audio_mode,
        "audio_required": audio["required"],
        "output_profiles": tuple(profiles),
        "analysis_width": analysis_width,
        "snap_window_frames": snap_window_frames,
        "min_prominence": min_prominence,
        "max_evidence_frames": max_evidence_frames,
    }


def _outfit_id(index: int, slot_count: int) -> str:
    return f"outfit.{index:0{max(2, len(str(slot_count)))}d}"


def _product_id(index: int, slot_count: int) -> str:
    return f"product.{index:0{max(2, len(str(slot_count)))}d}"


def _static_transform(frame: int, anchor: Mapping[str, float | int]) -> dict[str, Any]:
    return {
        "anchor": {"x": anchor["x"], "y": anchor["y"]},
        "keyframes": [
            {
                "frame": frame,
                "translate_x": 0,
                "translate_y": 0,
                "scale_x": 1,
                "scale_y": 1,
                "rotation_deg": 0,
                "opacity": 1,
                "easing": {"type": "hold"},
            }
        ],
    }


def _carousel_transform(duration_frames: int, origin: Mapping[str, float | int], end_offset_x: float | int) -> dict[str, Any]:
    first = {
        "frame": 0,
        "translate_x": 0,
        "translate_y": 0,
        "scale_x": 1,
        "scale_y": 1,
        "rotation_deg": 0,
        "opacity": 1,
        "easing": {"type": "linear" if duration_frames > 1 else "hold"},
    }
    keyframes = [first]
    if duration_frames > 1:
        keyframes.append(
            {
                "frame": duration_frames - 1,
                "translate_x": end_offset_x,
                "translate_y": 0,
                "scale_x": 1,
                "scale_y": 1,
                "rotation_deg": 0,
                "opacity": 1,
                "easing": {"type": "hold"},
            }
        )
    return {"anchor": {"x": origin["x"], "y": origin["y"]}, "keyframes": keyframes}


def _normalized_switches_for_template(
    spec: Mapping[str, Any], media: MediaInfo, switch_frames: Sequence[int]
) -> tuple[list[tuple[int, int]], tuple[int, ...]]:
    if isinstance(switch_frames, (str, bytes)) or not isinstance(switch_frames, Sequence):
        raise _error("switch_frames must be a sequence of frame numbers")
    switches = tuple(switch_frames)
    mode = spec["timing_mode"]
    if mode == "manual":
        # Public callers commonly supply the frozen segment-start list while
        # the compiler's timing helper supplies internal boundaries.  Both
        # spellings denote the exact same manual plan; any other value fails.
        if switches == spec["manual_starts"]:
            switches = tuple(spec["manual_switches"])
        elif switches != spec["manual_switches"]:
            raise _error("manual timing must use plan.timing.switch_frames exactly")
    if mode == "uniform":
        expected = _switches_from_ranges(balanced_ranges(media.duration_frames, spec["slot_count"]))
        if switches != expected:
            raise _error("uniform timing must use the deterministic balanced switch frames")
    ranges = _ranges_from_switches(
        media.duration_frames,
        spec["slot_count"],
        switches,
        spec["min_segment_frames"],
    )
    return ranges, tuple(int(frame) for frame in switches)


def build_template(
    plan: Mapping[str, Any],
    media: Mapping[str, Any],
    source_sha256: str,
    switch_frames: Sequence[int],
    audio_available: bool,
) -> dict[str, Any]:
    """Build one legal, renderer-ready Template IR 0.2.0 document.

    ``switch_frames`` are internal slot-switch boundaries, not a list that
    includes frame zero or the terminal duration frame.  Manual plans must
    match their declared frames exactly; uniform plans must use
    :func:`balanced_ranges`; hybrid plans may provide analysed boundaries.
    """

    # This public direct API must enforce the same frozen structural contract
    # as compile_reference, before it reaches media-dependent semantics.
    _validate_compiler_plan_schema(plan)
    if not isinstance(source_sha256, str) or not _SHA256_RE.fullmatch(source_sha256):
        raise _error("source_sha256 must be a lowercase 64-character SHA-256 hex digest")
    if not isinstance(audio_available, bool):
        raise _error("audio_available must be a boolean")
    media_info = _media_info(media)
    spec = _validate_plan(plan, media_info)
    if spec["audio_mode"] == "preserve" and not audio_available:
        raise _capability_error("preserve audio requires a source audio stream")
    ranges, normalized_switches = _normalized_switches_for_template(spec, media_info, switch_frames)
    slot_count = spec["slot_count"]
    duration = media_info.duration_frames
    canvas_width = spec["source_rect"]["width"]
    canvas_height = spec["source_rect"]["height"]
    subject_rect = spec["subject_rect"]
    carousel_rect = spec["carousel_rect"]
    origin = spec["origin"]

    slots: list[dict[str, Any]] = [
        {
            "id": "model.identity",
            "type": "identity",
            "required": True,
            "accepted_media": list(_IMAGE_MEDIA),
        }
    ]
    tracks: list[dict[str, Any]] = []
    layers: list[dict[str, Any]] = []
    if spec["background_replaceable"]:
        slots.append(
            {
                "id": "background",
                "type": "background",
                "required": False,
                "accepted_media": list(_IMAGE_MEDIA),
            }
        )
        tracks.append({"id": "background", "type": "background", "z_index": 0, "overlap_policy": "forbid"})
        layers.append(
            {
                "id": "background-fill",
                "track_id": "background",
                "source": {"slot_id": "background", "representation": "raw"},
                "active_ranges": [{"start_frame": 0, "end_frame": duration}],
                "layout": {
                    "box": {"x": 0, "y": 0, "width": canvas_width, "height": canvas_height},
                    "fit": "cover",
                    "object_position": {"x": 0.5, "y": 0.5},
                },
                "transform": _static_transform(0, {"x": canvas_width / 2, "y": canvas_height / 2}),
                "mask": None,
                "blend": {"mode": "normal", "opacity": 1},
                "z_offset": 0,
            }
        )

    tracks.append({"id": "model", "type": "subject", "z_index": 10, "overlap_policy": "forbid"})
    subject_anchor = {
        "x": _format_number(subject_rect["x"] + subject_rect["width"] / 2),
        "y": _format_number(subject_rect["y"] + subject_rect["height"] / 2),
    }
    events: list[dict[str, Any]] = []
    for index, (start, end) in enumerate(ranges, start=1):
        outfit_id = _outfit_id(index, slot_count)
        suffix = outfit_id.rsplit(".", 1)[1]
        slots.append(
            {
                "id": outfit_id,
                "type": "garment",
                "required": True,
                "accepted_media": list(_IMAGE_MEDIA),
            }
        )
        layers.append(
            {
                "id": f"outfit-render.{suffix}",
                "track_id": "model",
                "source": {"slot_id": outfit_id, "representation": "render-ready"},
                "active_ranges": [{"start_frame": start, "end_frame": end}],
                "layout": {
                    "box": dict(subject_rect),
                    "fit": "contain",
                    "object_position": {"x": 0.5, "y": 0.5},
                },
                "transform": _static_transform(start, subject_anchor),
                "mask": None,
                "blend": {"mode": "normal", "opacity": 1},
                "z_offset": 0,
            }
        )
        events.append(
            {
                "id": f"outfit-switch.{suffix}",
                "frame": start,
                "type": "slot-switch",
                "track_id": "model",
                "slot_id": outfit_id,
                "transition": {"type": "cut", "duration_frames": 0},
            }
        )

    product_slots = [_product_id(index, slot_count) for index in range(1, slot_count + 1)]
    tracks.append(
        {
            "id": "product-carousel",
            "type": "carousel",
            "z_index": 20,
            "overlap_policy": "allow",
            "group_layout": {
                "type": "carousel",
                "origin": {"x": origin["x"], "y": origin["y"]},
                "item_slots": product_slots,
                "item_width": spec["item_width"],
                "item_height": spec["item_height"],
                "gap": spec["gap"],
                "direction": "horizontal",
                "repeat": "none",
            },
            "group_transform": _carousel_transform(duration, origin, spec["end_offset_x"]),
            "clip_mask": {
                "type": "rect",
                "space": "canvas",
                "rect": dict(carousel_rect),
                "feather_px": 0,
                "invert": False,
            },
        }
    )
    for index, product_id in enumerate(product_slots):
        slots.append(
            {
                "id": product_id,
                "type": "product",
                "required": True,
                "accepted_media": list(_IMAGE_MEDIA),
            }
        )
        item_x = _format_number(float(origin["x"]) + index * (float(spec["item_width"]) + float(spec["gap"])))
        item_y = _format_number(float(origin["y"]))
        layers.append(
            {
                "id": f"product-item.{product_id.rsplit('.', 1)[1]}",
                "track_id": "product-carousel",
                "source": {"slot_id": product_id, "representation": "raw"},
                "active_ranges": [{"start_frame": 0, "end_frame": duration}],
                "layout": {
                    "box": {
                        "x": item_x,
                        "y": item_y,
                        "width": spec["item_width"],
                        "height": spec["item_height"],
                    },
                    "fit": "contain",
                    "object_position": {"x": 0.5, "y": 0.5},
                },
                "transform": _static_transform(
                    0,
                    {
                        "x": _format_number(float(item_x) + float(spec["item_width"]) / 2),
                        "y": _format_number(float(item_y) + float(spec["item_height"]) / 2),
                    },
                ),
                "mask": None,
                "blend": {"mode": "normal", "opacity": 1},
                "z_offset": index,
            }
        )

    if spec["audio_mode"] == "preserve":
        audio_media = ["audio/x-matroska"]
        audio_required = True
    elif spec["audio_mode"] == "replaceable":
        audio_media = list(_AUDIO_MEDIA)
        audio_required = spec["audio_required"]
    else:
        audio_media = list(_AUDIO_MEDIA)
        audio_required = False
    slots.append(
        {
            "id": "audio",
            "type": "audio",
            "required": audio_required,
            "accepted_media": audio_media,
        }
    )
    source_out_ms = duration * 1000.0 / media_info.fps
    outputs: list[dict[str, Any]] = []
    for profile in spec["output_profiles"]:
        width, height, output_id = _OUTPUT_PROFILES[profile]
        outputs.append(
            {
                "id": output_id,
                "width": width,
                "height": height,
                "codec": "h264",
                "pixel_format": "yuv420p",
                "audio_codec": "aac",
                "filename": f"deliveries/{spec['template_id']}-{profile}.mp4",
                "reframe": {
                    "mode": "contain",
                    "object_position": {"x": 0.5, "y": 0.5},
                    "background": spec["background_color"],
                },
            }
        )
    return {
        "schema_version": TEMPLATE_IR_SCHEMA_VERSION,
        "template_id": spec["template_id"],
        "coordinate_space": "canvas-pixels",
        "canvas": {
            "width": canvas_width,
            "height": canvas_height,
            "background": spec["background_color"],
            "source_rect": dict(spec["source_rect"]),
        },
        "source": {
            "duration_frames": duration,
            "fps": _format_number(media_info.fps),
            "width": media_info.width,
            "height": media_info.height,
            "source_sha256": source_sha256,
        },
        "support": {
            "level": "S1",
            "confidence": 1,
            "review_required": False,
            "warnings": [],
        },
        "tracks": tracks,
        "slots": slots,
        "layers": layers,
        "remove_layers": [
            {
                "id": "crop-source",
                "policy": "crop-source-before-analysis",
                "regions": [
                    {
                        "active_range": {"start_frame": 0, "end_frame": duration},
                        "operation": "keep",
                        "geometry": {"type": "rect", "space": "source", "rect": dict(spec["source_rect"])},
                    }
                ],
            }
        ],
        "events": events,
        "audio": {
            "slot_id": "audio",
            "timeline_start_frame": 0,
            "timeline_end_frame": duration,
            "source_in_ms": 0,
            "source_out_ms": _format_number(source_out_ms),
            "playback_rate": 1,
            "loop": False,
            "gain_db": 0,
            "fade_in_frames": 0,
            "fade_out_frames": 0,
        },
        "outputs": outputs,
    }


def build_grayscale_extraction_command(
    source: str | os.PathLike[str],
    ffmpeg: str | os.PathLike[str],
    source_rect: Mapping[str, int],
    analysis_width: int,
    analysis_height: int,
    duration_frames: int,
    output: str | os.PathLike[str],
) -> list[str]:
    """Build an argv-only crop/scale/grayscale FFmpeg extraction command."""

    rect = _pixel_rect(source_rect, "source_rect")
    width = _positive_int(analysis_width, "analysis_width")
    height = _positive_int(analysis_height, "analysis_height")
    frames = _positive_int(duration_frames, "duration_frames")
    try:
        output_path = Path(output).resolve(strict=False)
    except (TypeError, OSError, RuntimeError) as exc:
        raise _error("output must be a valid path") from exc
    if not str(output_path) or "\x00" in str(output_path):
        raise _error("output must be a valid path")
    filter_graph = (
        f"crop=w={rect['width']}:h={rect['height']}:x={rect['x']}:y={rect['y']},"
        f"scale=w={width}:h={height}:flags=bilinear,format=gray"
    )
    return [
        os.fspath(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(rrv_runtime.require_source_file(source)),
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
        "-vf",
        filter_graph,
        "-frames:v",
        str(frames),
        "-pix_fmt",
        "gray",
        "-f",
        "rawvideo",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-n",
        str(output_path),
    ]


def build_evidence_frame_extraction_command(
    source: str | os.PathLike[str],
    ffmpeg: str | os.PathLike[str],
    source_rect: Mapping[str, int],
    frame_number: int,
    output: str | os.PathLike[str],
) -> list[str]:
    """Build an exact, source-cropped evidence-frame extraction command.

    Compiler evidence must reflect the confirmed reconstruction viewport, not
    phone chrome outside ``source_rect``.  The source itself remains local and
    is never used as a rendered layer.
    """

    rect = _pixel_rect(source_rect, "source_rect")
    frame = _nonnegative_int(frame_number, "frame_number")
    try:
        output_path = Path(output).resolve(strict=False)
    except (TypeError, OSError, RuntimeError) as exc:
        raise _error("output must be a valid path") from exc
    filter_graph = (
        f"select=eq(n\\,{frame}),"
        f"crop=w={rect['width']}:h={rect['height']}:x={rect['x']}:y={rect['y']}"
    )
    return [
        os.fspath(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(rrv_runtime.require_source_file(source)),
        "-map",
        "0:v:0",
        "-vf",
        filter_graph,
        "-frames:v",
        "1",
        "-an",
        "-sn",
        "-dn",
        "-update",
        "1",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-n",
        str(output_path),
    ]


def _analysis_geometry(spec: Mapping[str, Any]) -> tuple[int, int, tuple[int, int, int, int]]:
    source_rect = spec["source_rect"]
    subject_rect = spec["subject_rect"]
    analysis_width = spec["analysis_width"]
    canvas_width, canvas_height = source_rect["width"], source_rect["height"]
    analysis_height = max(1, int(round(canvas_height * analysis_width / canvas_width)))
    if analysis_width * analysis_height > MAX_ANALYSIS_PIXELS:
        raise _capability_error(
            "analysis geometry is too large for the bounded compiler",
            details={"capability": "bounded_grayscale_analysis"},
        )
    left = max(0, int(math.floor(subject_rect["x"] * analysis_width / canvas_width)))
    top = max(0, int(math.floor(subject_rect["y"] * analysis_height / canvas_height)))
    right = min(
        analysis_width,
        int(math.ceil((subject_rect["x"] + subject_rect["width"]) * analysis_width / canvas_width)),
    )
    bottom = min(
        analysis_height,
        int(math.ceil((subject_rect["y"] + subject_rect["height"]) * analysis_height / canvas_height)),
    )
    if right <= left or bottom <= top:
        raise _capability_error("subject ROI became empty after bounded analysis scaling")
    return analysis_width, analysis_height, (left, top, right - left, bottom - top)


def _adjacent_roi_mad(
    rawvideo: Path,
    analysis_width: int,
    analysis_height: int,
    duration_frames: int,
    roi: tuple[int, int, int, int],
) -> dict[int, float]:
    frame_bytes = analysis_width * analysis_height
    expected = frame_bytes * duration_frames
    try:
        actual = rawvideo.stat().st_size
    except OSError as exc:
        raise _tool_error("grayscale analysis did not create its expected output") from exc
    if actual != expected:
        raise _tool_error(
            "grayscale analysis produced an unexpected frame count",
            details={"expected_bytes": expected, "actual_bytes": actual},
        )
    left, top, roi_width, roi_height = roi
    pixels = roi_width * roi_height
    scores: dict[int, float] = {}
    with rawvideo.open("rb") as handle:
        previous = handle.read(frame_bytes)
        if len(previous) != frame_bytes:
            raise _tool_error("grayscale analysis ended before its first frame")
        for frame in range(1, duration_frames):
            current = handle.read(frame_bytes)
            if len(current) != frame_bytes:
                raise _tool_error("grayscale analysis ended before its declared duration")
            difference = 0
            for row in range(top, top + roi_height):
                offset = row * analysis_width + left
                left_row = previous[offset : offset + roi_width]
                right_row = current[offset : offset + roi_width]
                difference += sum(abs(first - second) for first, second in zip(left_row, right_row))
            scores[frame] = difference / pixels
            previous = current
        if handle.read(1):  # Defensive: stat already checked this, keep the invariant local.
            raise _tool_error("grayscale analysis contains unexpected trailing data")
    return scores


def _prominence(scores: Mapping[int, float], frame: int, window: int, duration_frames: int) -> float:
    nearby = [
        scores.get(candidate, 0.0)
        for candidate in range(max(1, frame - window), min(duration_frames - 1, frame + window) + 1)
        if candidate != frame
    ]
    baseline = sum(nearby) / len(nearby) if nearby else 0.0
    return max(0.0, scores.get(frame, 0.0) - baseline)


def _hybrid_timing(
    duration_frames: int,
    slot_count: int,
    min_segment_frames: int,
    snap_window_frames: int,
    min_prominence: float,
    scores: Mapping[int, float],
) -> TimingDecision:
    base_ranges = balanced_ranges(duration_frames, slot_count)
    baseline_switches = _switches_from_ranges(base_ranges)
    selected: list[int] = []
    fallback: list[int] = []
    decisions: list[dict[str, Any]] = []
    previous = 0
    for boundary_index, target in enumerate(baseline_switches):
        remaining_segments = slot_count - boundary_index - 1
        minimum = previous + min_segment_frames
        maximum = duration_frames - remaining_segments * min_segment_frames
        candidates = [
            frame
            for frame in range(max(1, target - snap_window_frames), min(duration_frames - 1, target + snap_window_frames) + 1)
            if minimum <= frame <= maximum
        ]
        if candidates:
            best = min(
                candidates,
                key=lambda frame: (-scores.get(frame, 0.0), abs(frame - target), frame),
            )
            score = scores.get(best, 0.0)
            prominence = _prominence(scores, best, snap_window_frames, duration_frames)
        else:  # Defensive; the balanced target itself is normally feasible.
            best, score, prominence = None, 0.0, 0.0
        if best is not None and prominence >= min_prominence:
            chosen = best
            decision = "snapped" if chosen != target else "confirmed-uniform"
            used_fallback = False
        else:
            # Retain the deterministic uniform boundary whenever prior snaps
            # permit it.  The clamp is only needed after a nearby earlier snap.
            chosen = min(max(target, minimum), maximum)
            decision = "uniform-fallback"
            used_fallback = True
            fallback.append(target)
        selected.append(chosen)
        decisions.append(
            {
                "target_frame": target,
                "selected_frame": chosen,
                "decision": decision,
                "mad": _format_number(score),
                "prominence": _format_number(prominence),
            }
        )
        previous = chosen
    ranges = _ranges_from_switches(
        duration_frames,
        slot_count,
        selected,
        min_segment_frames,
        field="hybrid switch_frames",
    )
    return TimingDecision(
        ranges=tuple(ranges),
        switch_frames=tuple(selected),
        fallback_frames=tuple(fallback),
        decisions=tuple(decisions),
    )


def _uniform_timing(media: MediaInfo, spec: Mapping[str, Any]) -> TimingDecision:
    ranges = balanced_ranges(media.duration_frames, spec["slot_count"])
    if any(end - start < spec["min_segment_frames"] for start, end in ranges):
        raise _error("uniform timing creates a segment shorter than min_segment_frames")
    return TimingDecision(tuple(ranges), _switches_from_ranges(ranges), (), ())


def _manual_timing(media: MediaInfo, spec: Mapping[str, Any]) -> TimingDecision:
    ranges = _ranges_from_switches(
        media.duration_frames,
        spec["slot_count"],
        spec["manual_switches"],
        spec["min_segment_frames"],
        field="plan.timing.switch_frames",
    )
    return TimingDecision(tuple(ranges), tuple(spec["manual_switches"]), (), ())


def _require_ffmpeg(tools: rrv_runtime.RuntimeTools) -> str:
    if tools.ffmpeg.path:
        return tools.ffmpeg.path
    raise _capability_error(
        "reference compilation requires local FFmpeg",
        details={"capability": "reference_compile", "missing_tool": "ffmpeg"},
    )


def _require_ffprobe(tools: rrv_runtime.RuntimeTools) -> str:
    if tools.ffprobe.path:
        return tools.ffprobe.path
    raise _capability_error(
        "reference compilation requires local FFprobe for exact frame timing",
        details={"capability": "reference_compile", "missing_tool": "ffprobe"},
    )


def _call_runner(
    runner: Callable[..., Any] | None, command: Sequence[str], timeout_seconds: float
) -> Any:
    argv = list(command)
    if runner is None:
        return rrv_runtime.run_command(argv, timeout_seconds=timeout_seconds, check=True)
    # Existing renderer tests use a one-argument fake runner, while passing
    # rrv_runtime.run_command directly should retain the requested timeout.
    try:
        signature = inspect.signature(runner)
        signature.bind(argv, timeout_seconds=timeout_seconds)
    except (TypeError, ValueError):
        return runner(argv)
    return runner(argv, timeout_seconds=timeout_seconds)


def _run_artifact(
    command: Sequence[str], output: Path, timeout_seconds: float, runner: Callable[..., Any] | None, label: str
) -> Any:
    try:
        result = _call_runner(runner, command, timeout_seconds)
    except rrv_runtime.RRVError as exc:
        safe_details = {
            key: value
            for key, value in exc.details.items()
            if key in {"tool", "returncode", "timeout_seconds", "capability", "missing_tool"}
            and isinstance(value, (str, int, float, bool))
        }
        raise rrv_runtime.RRVError(
            exc.code,
            f"{label} failed",
            {"cause_code": exc.code, **safe_details},
        ) from exc
    except Exception as exc:
        raise _tool_error(f"{label} failed") from exc
    if not output.is_file():
        raise _tool_error(f"{label} did not create its expected output")
    return result


def _probe_with_runner(
    source: Path,
    tools: rrv_runtime.RuntimeTools,
    timeout_seconds: float,
    runner: Callable[..., Any] | None,
) -> dict[str, Any]:
    """Use runtime probing by default; allow an injected runner for tests."""

    if runner is None:
        return rrv_runtime.probe_media(source, tools=tools, timeout_seconds=timeout_seconds)
    if tools.ffprobe.path:
        result = _call_runner(
            runner,
            rrv_runtime.build_ffprobe_command(source, tools.ffprobe.path),
            timeout_seconds,
        )
        if isinstance(result, Mapping) and isinstance(result.get("media"), Mapping):
            return {"probe": dict(result.get("probe", {"backend": "injected"})), "media": dict(result["media"])}
        stdout = getattr(result, "stdout", None)
        if not isinstance(stdout, str):
            raise _tool_error("injected probe runner must return ffprobe JSON or a result with stdout")
        try:
            raw = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise _tool_error("injected probe runner returned invalid ffprobe JSON") from exc
        return {
            "probe": {"backend": "ffprobe", "capability_level": "full", "limitations": []},
            "media": rrv_runtime.normalize_ffprobe_json(raw, source),
        }
    # The runtime's fallback parser is intentionally reused instead of
    # duplicating its media-header assumptions.
    return rrv_runtime.probe_media(source, tools=tools, timeout_seconds=timeout_seconds)


def _exact_timing_with_runner(
    source: Path,
    ffprobe: str,
    timeout_seconds: float,
    runner: Callable[..., Any] | None,
) -> dict[str, Any]:
    """Require ffprobe's counted frame records and constant PTS cadence."""

    if runner is None:
        return rrv_runtime.probe_exact_video_timing(
            source, ffprobe, timeout_seconds=timeout_seconds
        )
    try:
        result = _call_runner(
            runner,
            rrv_runtime.build_ffprobe_exact_timing_command(source, ffprobe),
            timeout_seconds,
        )
    except rrv_runtime.RRVError:
        raise
    except Exception as exc:
        raise _tool_error("ffprobe exact timing inspection failed") from exc
    if isinstance(result, Mapping):
        raw: Any = result
    else:
        stdout = getattr(result, "stdout", None)
        if not isinstance(stdout, str):
            raise _tool_error("injected exact timing runner must return ffprobe JSON")
        try:
            raw = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise _tool_error("injected exact timing runner returned invalid ffprobe JSON") from exc
    try:
        return rrv_runtime.parse_ffprobe_exact_timing_json(raw)
    except rrv_runtime.RRVError:
        raise
    except Exception as exc:  # pragma: no cover - defensive normalization boundary.
        raise _tool_error("ffprobe exact timing inspection returned invalid data") from exc


def _merge_exact_timing(media: Mapping[str, Any], timing: Mapping[str, Any]) -> dict[str, Any]:
    """Attach verified count/cadence to normalized media without source paths."""

    data = _require_object(media, "media")
    frame_count = timing.get("frame_count")
    fps = timing.get("fps")
    duration_seconds = timing.get("duration_seconds")
    if not _is_int(frame_count) or frame_count < 1:
        raise _capability_error("ffprobe exact timing did not return a positive frame count")
    if (
        isinstance(fps, bool)
        or not isinstance(fps, (int, float))
        or not math.isfinite(fps)
        or fps <= 0
        or isinstance(duration_seconds, bool)
        or not isinstance(duration_seconds, (int, float))
        or not math.isfinite(duration_seconds)
        or duration_seconds <= 0
        or timing.get("cfr_confirmed") is not True
    ):
        raise _capability_error("ffprobe exact timing did not confirm CFR PTS cadence")
    streams = data.get("streams")
    if not isinstance(streams, list):
        raise _error("media.streams must be an array")
    copied_streams: list[Any] = []
    video_count = 0
    for stream in streams:
        if not isinstance(stream, Mapping):
            copied_streams.append(stream)
            continue
        copied = dict(stream)
        if copied.get("type") == "video":
            video_count += 1
            copied.update(
                {
                    "frame_count": frame_count,
                    "frame_count_source": "ffprobe-nb_read_frames",
                    "frame_rate": float(fps),
                    "average_frame_rate": float(fps),
                    "exact_duration_seconds": float(duration_seconds),
                    "cfr_confirmed": True,
                }
            )
        copied_streams.append(copied)
    if video_count != 1:
        raise _capability_error("S1 compilation requires exactly one video stream")
    merged = dict(data)
    merged["streams"] = copied_streams
    # Make the counted PTS span authoritative everywhere downstream, including
    # build_template's direct media normalization.  Container duration can
    # legitimately include edit-list padding and must not replace exact frame
    # timing after it has been confirmed.
    format_data = data.get("format")
    if isinstance(format_data, Mapping):
        copied_format = dict(format_data)
        copied_format["duration_seconds"] = float(duration_seconds)
        merged["format"] = copied_format
    return merged


def _new_staging_directory(root: Path, target: Path, role: str) -> Path:
    # ``resolve_output_path`` is called only after semantic preflight.  It
    # creates a permitted parent but never the user-visible target itself.
    target = rrv_runtime.resolve_output_path(root, target, create_parent=True, must_not_exist=True)
    try:
        stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.{role}-", dir=str(target.parent))).resolve()
        stage.relative_to(root)
    except (OSError, ValueError, RuntimeError) as exc:
        raise _tool_error("could not create a contained compiler staging directory") from exc
    return stage


def _cleanup_directory(root: Path, path: Path | None) -> None:
    if path is None:
        return
    try:
        resolved = path.resolve(strict=False)
        resolved.relative_to(root)
        if resolved.exists():
            shutil.rmtree(resolved)
    except (OSError, ValueError, RuntimeError):
        # Preserve the original compiler failure.  Staging paths are opaque,
        # unreported implementation details and never a user target.
        return


def _safe_stage_path(root: Path, stage: Path, relative: str) -> Path:
    return rrv_runtime.resolve_output_path(root, stage / relative, create_parent=True, must_not_exist=True)


def _write_json_new(path: Path, payload: Mapping[str, Any]) -> None:
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(rrv_runtime.stable_json_dumps(payload))
            handle.write("\n")
    except FileExistsError as exc:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_OUTPUT_EXISTS, "refusing to overwrite an existing output"
        ) from exc
    except OSError as exc:
        raise _tool_error("could not write compiler JSON", details={"reason": str(exc)[:rrv_runtime.MAX_ERROR_TEXT_LENGTH]}) from exc


def _pillow_available() -> bool:
    try:
        rrv_analyze._load_pillow()
    except rrv_runtime.RRVError:
        return False
    return True


def _selected_centers(ranges: Sequence[tuple[int, int]], maximum: int) -> list[int]:
    centers = [(start + end - 1) // 2 for start, end in ranges]
    if len(centers) <= maximum:
        return centers
    if maximum == 1:
        return [centers[len(centers) // 2]]
    indexes = [round(index * (len(centers) - 1) / (maximum - 1)) for index in range(maximum)]
    return [centers[index] for index in dict.fromkeys(indexes)]


def _artifact(root: Path, path: Path, **extra: Any) -> dict[str, Any]:
    return {
        "path": rrv_runtime.relative_output_path(root, path),
        "sha256": rrv_analyze.sha256_file(path),
        **extra,
    }


def _published_artifact(
    root: Path, stage: Path, target: Path, path: Path, **extra: Any
) -> dict[str, Any]:
    """Hash a staged file while reporting its eventual, stable final path."""

    artifact = _artifact(root, path, **extra)
    try:
        relative = path.resolve(strict=False).relative_to(stage.resolve(strict=False))
    except (ValueError, OSError, RuntimeError) as exc:  # pragma: no cover - internal invariant.
        raise _tool_error("compiler artifact escaped its staging directory") from exc
    artifact["path"] = rrv_runtime.relative_output_path(root, target / relative)
    return artifact


def _validator() -> Callable[[Mapping[str, Any]], Any]:
    try:
        import video_remix
    except ImportError:  # pragma: no cover - package import path.
        from . import video_remix  # type: ignore[no-redef]
    return video_remix.validate_template_data


def _validate_template(template: Mapping[str, Any], template_validator: Callable[[Mapping[str, Any]], Any] | None) -> None:
    validator = template_validator or _validator()
    try:
        result = validator(template)
    except rrv_runtime.RRVError:
        raise
    except Exception as exc:
        raise _tool_error("template validation failed", details={"reason": str(exc)[:rrv_runtime.MAX_ERROR_TEXT_LENGTH]}) from exc
    if result is None or result is True:
        return
    if result is False:
        errors = ["template validator returned false"]
    elif isinstance(result, str):
        errors = [result] if result else []
    else:
        try:
            errors = [str(item) for item in result]
        except TypeError:
            errors = [str(result)]
    if errors:
        raise _error(
            "generated Template IR did not pass validation",
            details={"errors": errors[:8]},
        )


def _compact_evidence(
    scores: Mapping[int, float],
    decisions: Sequence[Mapping[str, Any]],
    maximum: int,
) -> list[dict[str, Any]]:
    selected = {int(item["selected_frame"]) for item in decisions}
    score_frames = sorted(scores, key=lambda frame: (-scores[frame], frame))
    ordered: list[int] = []
    for frame in sorted(selected):
        if frame not in ordered:
            ordered.append(frame)
    for frame in score_frames:
        if len(ordered) >= maximum:
            break
        if frame not in ordered:
            ordered.append(frame)
    prominence_by_frame = {int(item["selected_frame"]): item.get("prominence", 0) for item in decisions}
    return [
        {
            "frame": frame,
            "mad": _format_number(scores.get(frame, 0.0)),
            "prominence": prominence_by_frame.get(frame, 0),
            "selected": frame in selected,
        }
        for frame in ordered[:maximum]
    ]


def _asset_requirements(template: Mapping[str, Any]) -> list[dict[str, Any]]:
    slots = template.get("slots")
    if not isinstance(slots, list):  # Defensive; validator has already checked the document.
        return []
    return [
        {
            "id": slot["id"],
            "type": slot["type"],
            "required": slot["required"],
            "accepted_media": list(slot["accepted_media"]),
        }
        for slot in slots
        if isinstance(slot, Mapping)
    ]


def _reported_switch_frames(spec: Mapping[str, Any], timing: TimingDecision) -> list[int]:
    """Keep manual review output in the same segment-start form as its plan."""

    if spec["timing_mode"] == "manual":
        return list(spec["manual_starts"])
    return list(timing.switch_frames)


def _compile_report(
    *,
    template: Mapping[str, Any],
    media: MediaInfo,
    source_sha256: str,
    timing: TimingDecision,
    spec: Mapping[str, Any],
    analysis_height: int | None,
    analysis_roi: tuple[int, int, int, int] | None,
    scores: Mapping[int, float],
    artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    source_rect = spec["source_rect"]
    report: dict[str, Any] = {
        "schema_version": COMPILER_SCHEMA_VERSION,
        "template_id": template["template_id"],
        "review_required": timing.review_required,
        "support": {"level": "S1", "review_required": timing.review_required},
        "source": {
            "sha256": source_sha256,
            "duration_frames": media.duration_frames,
            "duration_seconds": _format_number(media.duration_seconds),
            "fps": _format_number(media.fps),
            "width": media.width,
            "height": media.height,
            "source_rect": dict(source_rect),
        },
        "timing": {
            "mode": spec["timing_mode"],
            "slot_count": spec["slot_count"],
            "ranges": [
                {"start_frame": start, "end_frame": end} for start, end in timing.ranges
            ],
            "switch_frames": _reported_switch_frames(spec, timing),
            "fallback_frames": list(timing.fallback_frames),
            "decisions": [dict(item) for item in timing.decisions],
        },
        "analysis": {
            "enabled": spec["timing_mode"] == "hybrid",
            "width": spec["analysis_width"],
            "height": analysis_height,
            "subject_roi": (
                {"x": analysis_roi[0], "y": analysis_roi[1], "width": analysis_roi[2], "height": analysis_roi[3]}
                if analysis_roi is not None
                else None
            ),
            "score_count": len(scores),
            "evidence": _compact_evidence(scores, timing.decisions, spec["max_evidence_frames"]),
        },
        "asset_requirements": _asset_requirements(template),
        "artifacts": dict(artifacts),
    }
    return report


def compile_reference(
    reference: str | os.PathLike[str],
    plan: Mapping[str, Any],
    project_root: str | os.PathLike[str],
    tools: rrv_runtime.RuntimeTools | None,
    output_dir: str | os.PathLike[str] = "template-compile",
    timeout_seconds: float = 120,
    template_validator: Callable[[Mapping[str, Any]], Any] | None = None,
    runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Compile one authorized local reference into an atomic S1 artifact set.

    The returned result contains only root-relative artifact paths and hashes.
    Neither it nor ``compile-report.json`` exposes the source file name,
    absolute source path, or local tool installation paths.
    """

    root = rrv_runtime.require_project_root(project_root)
    source = rrv_runtime.require_source_file(reference)
    # Do this before media work and before staging writes.  It catches output
    # traversal and preserves the no-overwrite contract even for invalid input.
    target = rrv_runtime.resolve_output_path(root, output_dir, must_not_exist=True)
    timeout = rrv_runtime.validate_timeout(timeout_seconds)
    _validate_compiler_plan_schema(plan)
    runtime_tools = tools or rrv_runtime.discover_tools()
    if not isinstance(runtime_tools, rrv_runtime.RuntimeTools):
        raise _error("tools must be an rrv_runtime.RuntimeTools instance")
    ffmpeg = _require_ffmpeg(runtime_tools)
    ffprobe = _require_ffprobe(runtime_tools)

    probe_result = _probe_with_runner(source, runtime_tools, timeout, runner)
    media = probe_result.get("media") if isinstance(probe_result, Mapping) else None
    if not isinstance(media, Mapping):
        raise _tool_error("media probe returned invalid metadata")
    media = _merge_exact_timing(
        media,
        _exact_timing_with_runner(source, ffprobe, timeout, runner),
    )
    media_info = _media_info(media, require_exact_timing=True)
    spec = _validate_plan(plan, media_info)
    source_sha256 = rrv_analyze.sha256_file(source)

    scores: dict[int, float] = {}
    analysis_height: int | None = None
    analysis_roi: tuple[int, int, int, int] | None = None
    analysis_stage: Path | None = None
    if spec["timing_mode"] == "uniform":
        timing = _uniform_timing(media_info, spec)
    elif spec["timing_mode"] == "manual":
        timing = _manual_timing(media_info, spec)
    else:
        analysis_width, analysis_height, analysis_roi = _analysis_geometry(spec)
        raw_bytes = analysis_width * analysis_height * media_info.duration_frames
        if raw_bytes > MAX_ANALYSIS_RAW_BYTES:
            raise _capability_error(
                "analysis video is too large for the bounded compiler",
                details={"capability": "bounded_grayscale_analysis"},
            )
        try:
            analysis_stage = _new_staging_directory(root, target, "analysis")
            rawvideo = _safe_stage_path(root, analysis_stage, "analysis.gray")
            command = build_grayscale_extraction_command(
                source,
                ffmpeg,
                spec["source_rect"],
                analysis_width,
                analysis_height,
                media_info.duration_frames,
                rawvideo,
            )
            _run_artifact(command, rawvideo, timeout, runner, "grayscale reference analysis")
            scores = _adjacent_roi_mad(
                rawvideo,
                analysis_width,
                analysis_height,
                media_info.duration_frames,
                analysis_roi,
            )
            # Explicitly remove raw grayscale before any final-artifact stage.
            rawvideo.unlink()
            timing = _hybrid_timing(
                media_info.duration_frames,
                spec["slot_count"],
                spec["min_segment_frames"],
                spec["snap_window_frames"],
                spec["min_prominence"],
                scores,
            )
        finally:
            _cleanup_directory(root, analysis_stage)

    template = build_template(
        plan,
        media,
        source_sha256,
        timing.switch_frames,
        media_info.audio_available,
    )
    template["support"]["review_required"] = timing.review_required
    if timing.review_required:
        template["support"]["warnings"].append(
            "Hybrid timing fell back to one or more uniform boundaries; review is required."
        )
    # Validate in memory before a user-visible output directory or any final
    # artifact is written.  The default is the repository's existing validator.
    _validate_template(template, template_validator)

    stage: Path | None = None
    try:
        stage = _new_staging_directory(root, target, "staging")
        artifacts: dict[str, Any] = {}
        audio_artifact: dict[str, Any] | None = None
        if spec["audio_mode"] == "preserve":
            audio_path = _safe_stage_path(root, stage, "audio-original.mka")
            _run_artifact(
                rrv_analyze.build_audio_extraction_command(source, ffmpeg, audio_path),
                audio_path,
                timeout,
                runner,
                "source audio stream-copy extraction",
            )
            audio_artifact = _published_artifact(
                root,
                stage,
                target,
                audio_path,
                media_type="audio/x-matroska",
                container="matroska",
                metadata_stripped=True,
            )
            artifacts["audio_original"] = audio_artifact

        frame_artifacts: list[dict[str, Any]] = []
        contact_sheet_artifact: dict[str, Any] | None = None
        if _pillow_available():
            selected_frames = _selected_centers(timing.ranges, spec["max_evidence_frames"])
            frame_items: list[tuple[int, Path]] = []
            for index, frame_number in enumerate(selected_frames, start=1):
                frame_path = _safe_stage_path(root, stage, f"frames/center-{index:03d}-n{frame_number}.png")
                _run_artifact(
                    build_evidence_frame_extraction_command(
                        source,
                        ffmpeg,
                        spec["source_rect"],
                        frame_number,
                        frame_path,
                    ),
                    frame_path,
                    timeout,
                    runner,
                    f"review center-frame extraction for frame {frame_number}",
                )
                frame_artifacts.append(
                    _published_artifact(root, stage, target, frame_path, frame=frame_number)
                )
                frame_items.append((frame_number, frame_path))
            if frame_items:
                contact_path = _safe_stage_path(root, stage, "contact-sheet.jpg")
                contact_metrics = rrv_analyze.create_contact_sheet(
                    frame_items,
                    contact_path,
                    project_root=root,
                )
                contact_sheet_artifact = _published_artifact(
                    root, stage, target, contact_path, **contact_metrics
                )
                artifacts["contact_sheet"] = contact_sheet_artifact
            artifacts["center_frames"] = frame_artifacts

        template_path = _safe_stage_path(root, stage, "template.ir.json")
        _write_json_new(template_path, template)
        artifacts["template_ir"] = _published_artifact(root, stage, target, template_path)

        report_path = _safe_stage_path(root, stage, "compile-report.json")
        artifacts["compile_report"] = {
            "path": rrv_runtime.relative_output_path(root, target / "compile-report.json")
        }
        report = _compile_report(
            template=template,
            media=media_info,
            source_sha256=source_sha256,
            timing=timing,
            spec=spec,
            analysis_height=analysis_height,
            analysis_roi=analysis_roi,
            scores=scores,
            artifacts=artifacts,
        )
        _write_json_new(report_path, report)
        artifacts["compile_report"] = _published_artifact(root, stage, target, report_path)

        # Re-check just before publication; rename never replaces an existing
        # target, so a concurrent producer cannot be overwritten.
        if target.exists() or target.is_symlink():
            raise rrv_runtime.RRVError(
                rrv_runtime.ERR_OUTPUT_EXISTS, "refusing to overwrite an existing output"
            )
        try:
            stage.rename(target)
        except FileExistsError as exc:
            raise rrv_runtime.RRVError(
                rrv_runtime.ERR_OUTPUT_EXISTS, "refusing to overwrite an existing output"
            ) from exc
        except OSError as exc:
            raise _tool_error("could not publish atomic compiler output") from exc
        output_relative = rrv_runtime.relative_output_path(root, target)
        result = {
            "schema_version": COMPILER_SCHEMA_VERSION,
            "template_id": template["template_id"],
            "output_dir": output_relative,
            "review_required": timing.review_required,
            "switch_frames": _reported_switch_frames(spec, timing),
            "artifacts": artifacts,
        }
        stage = None
        return result
    except Exception:
        _cleanup_directory(root, stage)
        raise


__all__ = [
    "COMPILER_SCHEMA_VERSION",
    "MAX_DURATION_SECONDS",
    "TEMPLATE_IR_SCHEMA_VERSION",
    "balanced_ranges",
    "build_evidence_frame_extraction_command",
    "build_grayscale_extraction_command",
    "build_template",
    "compile_reference",
]
