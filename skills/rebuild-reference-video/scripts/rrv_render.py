#!/usr/bin/env python3
"""Deterministic S1 renderer for a validated Template IR 0.2.0 project.

This module deliberately has no command-line interface.  A caller is expected
to validate the Template IR and Asset Manifest with ``video_remix.py`` before
constructing a renderer, then use the importable functions here to write one
master PNG frame sequence and encode the requested delivery profiles.

The implementation is intentionally small and fail-closed: it renders static
Pillow images, normal alpha compositing, rect/polygon masks, and the S1
transform subset.  It never generates a garment or a subject; a garment layer
must already point at a render-ready local asset.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import subprocess
from typing import Any

import rrv_runtime

try:  # Kept lazy enough that importing the module gives a useful dependency error.
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, UnidentifiedImageError
except ImportError:  # pragma: no cover - exercised on runtime-only installations.
    Image = None  # type: ignore[assignment]
    ImageChops = ImageDraw = ImageFilter = ImageOps = None  # type: ignore[assignment]
    UnidentifiedImageError = OSError  # type: ignore[assignment,misc]


RENDERER_ID = "rrv-s1-pillow-0.1"
FRAME_FILENAME_PATTERN = "frame_%06d.png"
DEFAULT_MASTER_DIRECTORY = Path("render") / "master-frames"
DEFAULT_ENCODER_TIMEOUT_SECONDS = 300.0
SUPPORTED_IMAGE_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
SUPPORTED_AUDIO_MEDIA_TYPES = frozenset({"audio/wav", "audio/mpeg", "audio/mp4", "audio/x-matroska"})
SUPPORTED_OUTPUT_SIZES = frozenset({(720, 1280), (1080, 1920)})
SUPPORTED_MASK_TYPES = frozenset({"rect", "polygon"})
_MASTER_FRAME_NAME_RE = re.compile(r"^frame_(\d+)\.png$")


class RenderError(RuntimeError):
    """Base error for deterministic renderer failures."""


class RenderDependencyError(RenderError):
    """Raised when Pillow is not installed."""


class RenderInputError(RenderError):
    """Raised for a malformed or unavailable runtime input."""


class PathPolicyError(RenderInputError):
    """Raised when a read or write path would leave the project root."""


class UnsupportedFeatureError(RenderError):
    """Raised instead of silently approximating an unsupported IR feature."""


class EncoderError(RenderError):
    """Raised when the externally supplied ffmpeg invocation fails."""


@dataclass(frozen=True)
class ResolvedAsset:
    """A manifest asset whose local path has passed the project-root policy."""

    slot_id: str
    path: Path
    media_type: str
    processor: str


@dataclass(frozen=True)
class TransformState:
    """The fully evaluated transform for one integer timeline frame."""

    translate_x: float
    translate_y: float
    scale_x: float
    scale_y: float
    rotation_deg: float
    opacity: float


@dataclass(frozen=True)
class EncodedOutput:
    """Stable information about one requested delivery artifact."""

    output_id: str
    path: Path
    width: int
    height: int
    audio_muxed: bool


@dataclass(frozen=True)
class _PlannedOutput:
    """A fully checked encode invocation that has not written anything yet."""

    output: Mapping[str, Any]
    path: Path
    width: int
    height: int
    command: tuple[str, ...]


EncoderRunner = Callable[[list[str]], Any]


def _require_pillow() -> None:
    if Image is None:
        raise RenderDependencyError(
            "Pillow is required for deterministic S1 rendering; install requirements-runtime.txt"
        )


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RenderInputError(f"{name} must be an object")
    return value


def _require_list(value: Any, name: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise RenderInputError(f"{name} must be an array")
    return value


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RenderInputError(f"{name} must be a finite number")
    return float(value)


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RenderInputError(f"{name} must be an integer")
    return value


def _unit_interval(value: Any, name: str) -> float:
    result = _number(value, name)
    if not 0 <= result <= 1:
        raise RenderInputError(f"{name} must be within [0, 1]")
    return result


def _fmt_number(value: float | int) -> str:
    """Use a locale-independent, stable numeric spelling for ffmpeg filters."""
    number = _number(value, "ffmpeg numeric argument")
    text = f"{number:.9f}".rstrip("0").rstrip(".")
    return text or "0"


def _round_pixel(value: float) -> int:
    """Round half upward rather than relying on Python's banker rounding."""
    return int(math.floor(value + 0.5))


def _project_root(project_root: str | Path) -> Path:
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise PathPolicyError(f"project root does not exist or is not a directory: {root}")
    return root


def _looks_absolute_on_any_supported_platform(path_text: str) -> bool:
    windows = PureWindowsPath(path_text)
    posix = PurePosixPath(path_text)
    return (
        Path(path_text).is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or posix.is_absolute()
    )


def _has_parent_traversal(path_text: str) -> bool:
    # Checking both forms makes a manifest authored on Windows safe to inspect
    # on a non-Windows worker, and vice versa.
    return ".." in PureWindowsPath(path_text).parts or ".." in PurePosixPath(path_text).parts


def resolve_project_path(
    project_root: str | Path,
    value: str | Path,
    *,
    purpose: str,
    allow_absolute: bool = False,
) -> Path:
    """Resolve a project read/write path and prove it remains under its root.

    Manifest asset paths are passed with ``allow_absolute=False``.  Renderer
    API callers may supply an absolute *output* path only when it is already
    inside the explicit project root; it remains subject to the same symlink
    containment check.
    """
    root = _project_root(project_root)
    text = str(value)
    if not text or "\x00" in text:
        raise PathPolicyError(f"{purpose} path is empty or contains a NUL byte")
    if _has_parent_traversal(text):
        raise PathPolicyError(f"{purpose} path must not contain parent traversal: {text}")

    absolute = _looks_absolute_on_any_supported_platform(text)
    if absolute and not allow_absolute:
        raise PathPolicyError(f"{purpose} path must be relative to the project root: {text}")
    candidate = Path(text) if absolute else root / Path(text)
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PathPolicyError(f"{purpose} path escapes the project root: {text}") from exc
    return resolved


def _relative_project_path(project_root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(project_root).as_posix()
    except ValueError as exc:  # pragma: no cover - callers always use resolve_project_path.
        raise PathPolicyError(f"path escapes the project root: {path}") from exc


def _slot_definitions(template: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    slots: dict[str, Mapping[str, Any]] = {}
    for index, raw_slot in enumerate(_require_list(template.get("slots"), "template.slots")):
        slot = _require_mapping(raw_slot, f"template.slots[{index}]")
        slot_id = slot.get("id")
        if not isinstance(slot_id, str) or not slot_id:
            raise RenderInputError(f"template.slots[{index}].id must be a non-empty string")
        if slot_id in slots:
            raise RenderInputError(f"template.slots[{index}].id duplicates {slot_id}")
        slots[slot_id] = slot
    return slots


def resolve_local_assets(
    template: Mapping[str, Any], manifest: Mapping[str, Any], project_root: str | Path
) -> dict[str, ResolvedAsset]:
    """Resolve local manifest assets after the existing validator has passed.

    The existing validator owns full Template IR and manifest semantics.  This
    runtime layer repeats only the safety-critical facts it needs: one local
    file per slot, accepted media type, actual file availability, required slot
    coverage, and project-root containment.  Provider assets are intentionally
    unsupported in the local deterministic renderer.
    """
    template = _require_mapping(template, "template")
    manifest = _require_mapping(manifest, "manifest")
    root = _project_root(project_root)
    template_id = template.get("template_id")
    if not isinstance(template_id, str) or not template_id:
        raise RenderInputError("template.template_id must be a non-empty string")
    if manifest.get("template_id") != template_id:
        raise RenderInputError("manifest.template_id does not match template.template_id")

    slots = _slot_definitions(template)
    result: dict[str, ResolvedAsset] = {}
    for index, raw_asset in enumerate(_require_list(manifest.get("assets"), "manifest.assets")):
        asset = _require_mapping(raw_asset, f"manifest.assets[{index}]")
        slot_id = asset.get("slot_id")
        if not isinstance(slot_id, str) or slot_id not in slots:
            raise RenderInputError(f"manifest.assets[{index}].slot_id references an unknown slot")
        if slot_id in result:
            raise RenderInputError(f"manifest.assets[{index}].slot_id duplicates mapping for {slot_id}")
        if "provider_asset_id" in asset:
            raise UnsupportedFeatureError(
                f"slot {slot_id} uses provider_asset_id; local deterministic S1 rendering accepts local paths only"
            )
        raw_path = asset.get("path")
        if not isinstance(raw_path, str):
            raise RenderInputError(f"manifest.assets[{index}].path must be a local path")
        path = resolve_project_path(root, raw_path, purpose=f"asset slot {slot_id}")
        if not path.is_file():
            raise RenderInputError(f"asset for slot {slot_id} does not exist or is not a file: {path}")
        media_type = asset.get("media_type")
        if not isinstance(media_type, str):
            raise RenderInputError(f"manifest.assets[{index}].media_type must be a string")
        accepted = slots[slot_id].get("accepted_media")
        if not isinstance(accepted, list) or media_type not in accepted:
            raise RenderInputError(f"asset media type {media_type} is not accepted by slot {slot_id}")
        processor = asset.get("processor")
        if not isinstance(processor, str):
            raise RenderInputError(f"manifest.assets[{index}].processor must be a string")
        result[slot_id] = ResolvedAsset(slot_id, path, media_type, processor)

    missing = sorted(
        slot_id for slot_id, slot in slots.items() if slot.get("required") is True and slot_id not in result
    )
    if missing:
        raise RenderInputError(f"required slots are not mapped to local assets: {', '.join(missing)}")
    return result


def _rgba(color: Any, name: str) -> tuple[int, int, int, int]:
    if not isinstance(color, str) or not color.startswith("#") or len(color) not in (7, 9):
        raise RenderInputError(f"{name} must be #RRGGBB or #RRGGBBAA")
    hexadecimal = color[1:]
    try:
        values = tuple(int(hexadecimal[index : index + 2], 16) for index in range(0, len(hexadecimal), 2))
    except ValueError as exc:
        raise RenderInputError(f"{name} must be a hexadecimal color") from exc
    if len(values) == 3:
        return values[0], values[1], values[2], 255
    return values[0], values[1], values[2], values[3]


def _position(value: Any, name: str) -> tuple[float, float]:
    mapping = _require_mapping(value, name)
    return _unit_interval(mapping.get("x"), f"{name}.x"), _unit_interval(mapping.get("y"), f"{name}.y")


def _rect(value: Any, name: str) -> tuple[float, float, float, float]:
    mapping = _require_mapping(value, name)
    x = _number(mapping.get("x"), f"{name}.x")
    y = _number(mapping.get("y"), f"{name}.y")
    width = _number(mapping.get("width"), f"{name}.width")
    height = _number(mapping.get("height"), f"{name}.height")
    if width <= 0 or height <= 0:
        raise RenderInputError(f"{name} width and height must be positive")
    return x, y, width, height


def _anchor(transform: Mapping[str, Any], name: str) -> tuple[float, float]:
    return _position_unbounded(transform.get("anchor"), f"{name}.anchor")


def _position_unbounded(value: Any, name: str) -> tuple[float, float]:
    mapping = _require_mapping(value, name)
    return _number(mapping.get("x"), f"{name}.x"), _number(mapping.get("y"), f"{name}.y")


def _keyframe_state(keyframe: Mapping[str, Any], name: str) -> TransformState:
    scale_x = _number(keyframe.get("scale_x"), f"{name}.scale_x")
    scale_y = _number(keyframe.get("scale_y"), f"{name}.scale_y")
    if scale_x <= 0 or scale_y <= 0:
        raise RenderInputError(f"{name} scales must be positive")
    return TransformState(
        translate_x=_number(keyframe.get("translate_x"), f"{name}.translate_x"),
        translate_y=_number(keyframe.get("translate_y"), f"{name}.translate_y"),
        scale_x=scale_x,
        scale_y=scale_y,
        rotation_deg=_number(keyframe.get("rotation_deg"), f"{name}.rotation_deg"),
        opacity=_unit_interval(keyframe.get("opacity"), f"{name}.opacity"),
    )


def _cubic_component(t: float, p1: float, p2: float) -> float:
    inverse = 1.0 - t
    return 3.0 * inverse * inverse * t * p1 + 3.0 * inverse * t * t * p2 + t * t * t


def _ease(easing: Any, progress: float, name: str) -> float:
    easing_map = _require_mapping(easing, name)
    easing_type = easing_map.get("type")
    if easing_type == "hold":
        return 0.0
    if easing_type == "linear":
        return progress
    if easing_type != "cubic-bezier":
        raise UnsupportedFeatureError(f"{name}.type {easing_type!r} is not supported")
    points = _require_list(easing_map.get("control_points"), f"{name}.control_points")
    if len(points) != 4:
        raise RenderInputError(f"{name}.control_points must contain exactly four numbers")
    x1 = _unit_interval(points[0], f"{name}.control_points[0]")
    y1 = _number(points[1], f"{name}.control_points[1]")
    x2 = _unit_interval(points[2], f"{name}.control_points[2]")
    y2 = _number(points[3], f"{name}.control_points[3]")
    # The schema constrains x controls to [0, 1], so binary search is stable
    # and avoids a platform-dependent numerical optimiser.
    low, high = 0.0, 1.0
    for _ in range(32):
        parameter = (low + high) / 2.0
        if _cubic_component(parameter, x1, x2) < progress:
            low = parameter
        else:
            high = parameter
    return _cubic_component((low + high) / 2.0, y1, y2)


def _interpolate(left: TransformState, right: TransformState, amount: float) -> TransformState:
    def lerp(a: float, b: float) -> float:
        return a + (b - a) * amount

    return TransformState(
        translate_x=lerp(left.translate_x, right.translate_x),
        translate_y=lerp(left.translate_y, right.translate_y),
        scale_x=lerp(left.scale_x, right.scale_x),
        scale_y=lerp(left.scale_y, right.scale_y),
        rotation_deg=lerp(left.rotation_deg, right.rotation_deg),
        opacity=lerp(left.opacity, right.opacity),
    )


def evaluate_transform(transform: Mapping[str, Any], frame: int) -> TransformState:
    """Evaluate a hold, linear, or cubic-bezier transform at an integer frame."""
    transform = _require_mapping(transform, "transform")
    if frame < 0:
        raise RenderInputError("frame must not be negative")
    raw_keyframes = _require_list(transform.get("keyframes"), "transform.keyframes")
    if not raw_keyframes:
        raise RenderInputError("transform.keyframes must not be empty")
    keyframes: list[tuple[int, TransformState, Mapping[str, Any]]] = []
    prior_frame: int | None = None
    for index, raw_keyframe in enumerate(raw_keyframes):
        keyframe = _require_mapping(raw_keyframe, f"transform.keyframes[{index}]")
        keyframe_frame = _integer(keyframe.get("frame"), f"transform.keyframes[{index}].frame")
        if keyframe_frame < 0:
            raise RenderInputError(f"transform.keyframes[{index}].frame must not be negative")
        if prior_frame is not None and keyframe_frame <= prior_frame:
            raise RenderInputError("transform.keyframes must be strictly increasing")
        prior_frame = keyframe_frame
        keyframes.append((keyframe_frame, _keyframe_state(keyframe, f"transform.keyframes[{index}]"), keyframe))
    if frame <= keyframes[0][0]:
        return keyframes[0][1]
    if frame >= keyframes[-1][0]:
        return keyframes[-1][1]
    for left, right in zip(keyframes, keyframes[1:]):
        if left[0] <= frame < right[0]:
            raw_progress = (frame - left[0]) / (right[0] - left[0])
            amount = _ease(left[2].get("easing"), raw_progress, "transform.keyframe easing")
            return _interpolate(left[1], right[1], amount)
    raise RenderInputError("unable to locate transform keyframe interval")  # pragma: no cover


def _is_active(layer: Mapping[str, Any], frame: int) -> bool:
    ranges = _require_list(layer.get("active_ranges"), "layer.active_ranges")
    for index, raw_range in enumerate(ranges):
        frame_range = _require_mapping(raw_range, f"layer.active_ranges[{index}]")
        start = _integer(frame_range.get("start_frame"), f"layer.active_ranges[{index}].start_frame")
        end = _integer(frame_range.get("end_frame"), f"layer.active_ranges[{index}].end_frame")
        if start <= frame < end:
            return True
    return False


def _alpha_with_opacity(image: "Image.Image", opacity: float) -> "Image.Image":
    _require_pillow()
    if not 0 <= opacity <= 1:
        raise RenderInputError("combined opacity must be within [0, 1]")
    if opacity == 1:
        return image
    result = image.copy()
    alpha = result.getchannel("A")
    try:
        table = [_round_pixel(value * opacity) for value in range(256)]
        adjusted = alpha.point(table)
        try:
            result.putalpha(adjusted)
        finally:
            adjusted.close()
    finally:
        alpha.close()
    return result


def _layout_image(
    source: "Image.Image", layout: Mapping[str, Any], canvas_size: tuple[int, int]
) -> tuple["Image.Image", tuple[float, float, float, float]]:
    _require_pillow()
    layout = _require_mapping(layout, "layer.layout")
    box = _rect(layout.get("box"), "layer.layout.box")
    fit = layout.get("fit")
    if fit not in {"contain", "cover", "stretch"}:
        raise UnsupportedFeatureError(f"layout.fit {fit!r} is not supported")
    position_x, position_y = _position(layout.get("object_position"), "layer.layout.object_position")
    source_width, source_height = source.size
    box_x, box_y, box_width, box_height = box
    if fit == "stretch":
        scaled_width, scaled_height = box_width, box_height
    else:
        contain_scale = min(box_width / source_width, box_height / source_height)
        cover_scale = max(box_width / source_width, box_height / source_height)
        scale = contain_scale if fit == "contain" else cover_scale
        scaled_width, scaled_height = source_width * scale, source_height * scale
    target_width = max(1, _round_pixel(scaled_width))
    target_height = max(1, _round_pixel(scaled_height))
    resized = source.resize((target_width, target_height), Image.Resampling.LANCZOS)
    try:
        destination_x = box_x + (box_width - target_width) * position_x
        destination_y = box_y + (box_height - target_height) * position_y
        layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
        try:
            layer.alpha_composite(resized, (_round_pixel(destination_x), _round_pixel(destination_y)))

            # Layout boxes are clipping regions.  This is essential for
            # `cover`, where the fitted image is intentionally larger than its
            # destination box.
            layout_mask = Image.new("L", canvas_size, 0)
            try:
                _draw_rect(layout_mask, box_x, box_y, box_width, box_height, 255)
                alpha = layer.getchannel("A")
                try:
                    clipped_alpha = ImageChops.multiply(alpha, layout_mask)
                    try:
                        layer.putalpha(clipped_alpha)
                    finally:
                        clipped_alpha.close()
                finally:
                    alpha.close()
            finally:
                layout_mask.close()
            return layer, box
        except Exception:
            layer.close()
            raise
    finally:
        resized.close()


def _draw_rect(mask: "Image.Image", x: float, y: float, width: float, height: float, fill: int) -> None:
    _require_pillow()
    left = _round_pixel(x)
    top = _round_pixel(y)
    right = _round_pixel(x + width)
    bottom = _round_pixel(y + height)
    if right <= left or bottom <= top:
        return
    ImageDraw.Draw(mask).rectangle((left, top, right - 1, bottom - 1), fill=fill)


def _mask_image(
    mask: Mapping[str, Any],
    canvas_size: tuple[int, int],
    layout_box: tuple[float, float, float, float] | None,
    *,
    context: str,
) -> "Image.Image":
    """Build a monochrome rect or polygon mask in final canvas coordinates."""
    _require_pillow()
    mask = _require_mapping(mask, context)
    mask_type = mask.get("type")
    if mask_type not in SUPPORTED_MASK_TYPES:
        raise UnsupportedFeatureError(
            f"{context}.type {mask_type!r} is not supported; only rect and polygon masks are implemented"
        )
    space = mask.get("space")
    if space not in {"canvas", "layer"}:
        raise UnsupportedFeatureError(f"{context}.space {space!r} is not supported")
    if space == "layer" and layout_box is None:
        raise UnsupportedFeatureError(f"{context} uses layer space where no layer layout exists")
    offset_x, offset_y = (layout_box[0], layout_box[1]) if space == "layer" and layout_box else (0.0, 0.0)
    result = Image.new("L", canvas_size, 0)
    draw = ImageDraw.Draw(result)
    if mask_type == "rect":
        x, y, width, height = _rect(mask.get("rect"), f"{context}.rect")
        _draw_rect(result, offset_x + x, offset_y + y, width, height, 255)
    else:
        raw_points = _require_list(mask.get("points"), f"{context}.points")
        if len(raw_points) < 3:
            raise RenderInputError(f"{context}.points must contain at least three points")
        points: list[tuple[float, float]] = []
        for index, raw_point in enumerate(raw_points):
            x, y = _position_unbounded(raw_point, f"{context}.points[{index}]")
            points.append((offset_x + x, offset_y + y))
        draw.polygon(points, fill=255)
    feather = _number(mask.get("feather_px", 0), f"{context}.feather_px")
    if feather < 0:
        raise RenderInputError(f"{context}.feather_px must not be negative")
    if feather:
        blurred = result.filter(ImageFilter.GaussianBlur(radius=feather))
        result.close()
        result = blurred
    invert = mask.get("invert", False)
    if not isinstance(invert, bool):
        raise RenderInputError(f"{context}.invert must be boolean")
    return ImageChops.invert(result) if invert else result


def _apply_mask(
    image: "Image.Image",
    mask: Mapping[str, Any],
    canvas_size: tuple[int, int],
    layout_box: tuple[float, float, float, float] | None,
    *,
    context: str,
) -> "Image.Image":
    result = image.copy()
    try:
        shape = _mask_image(mask, canvas_size, layout_box, context=context)
        try:
            alpha = result.getchannel("A")
            try:
                masked_alpha = ImageChops.multiply(alpha, shape)
                try:
                    result.putalpha(masked_alpha)
                finally:
                    masked_alpha.close()
            finally:
                alpha.close()
        finally:
            shape.close()
        return result
    except Exception:
        result.close()
        raise


def _transform_coefficients(
    anchor_x: float, anchor_y: float, state: TransformState
) -> tuple[float, float, float, float, float, float]:
    if state.scale_x <= 0 or state.scale_y <= 0:
        raise RenderInputError("transform scales must be positive")
    radians = math.radians(state.rotation_deg)
    cosine, sine = math.cos(radians), math.sin(radians)
    # Positive angles match Pillow's visible counter-clockwise rotation.
    a = cosine * state.scale_x
    b = sine * state.scale_y
    d = -sine * state.scale_x
    e = cosine * state.scale_y
    forward_x = anchor_x + state.translate_x - a * anchor_x - b * anchor_y
    forward_y = anchor_y + state.translate_y - d * anchor_x - e * anchor_y
    determinant = a * e - b * d
    inverse_a, inverse_b = e / determinant, -b / determinant
    inverse_d, inverse_e = -d / determinant, a / determinant
    return (
        inverse_a,
        inverse_b,
        -(inverse_a * forward_x + inverse_b * forward_y),
        inverse_d,
        inverse_e,
        -(inverse_d * forward_x + inverse_e * forward_y),
    )


def _apply_transform(
    image: "Image.Image", state: TransformState, anchor: tuple[float, float]
) -> "Image.Image":
    _require_pillow()
    if (
        state.translate_x == 0
        and state.translate_y == 0
        and state.scale_x == 1
        and state.scale_y == 1
        and state.rotation_deg == 0
    ):
        return image
    return image.transform(
        image.size,
        Image.Transform.AFFINE,
        _transform_coefficients(anchor[0], anchor[1], state),
        resample=Image.Resampling.BICUBIC,
        fillcolor=(0, 0, 0, 0),
    )


def _transform_points(
    points: Sequence[tuple[float, float]], state: TransformState, anchor: tuple[float, float]
) -> list[tuple[float, float]]:
    radians = math.radians(state.rotation_deg)
    cosine, sine = math.cos(radians), math.sin(radians)
    result: list[tuple[float, float]] = []
    for x, y in points:
        local_x = (x - anchor[0]) * state.scale_x
        local_y = (y - anchor[1]) * state.scale_y
        result.append(
            (
                anchor[0] + state.translate_x + cosine * local_x + sine * local_y,
                anchor[1] + state.translate_y - sine * local_x + cosine * local_y,
            )
        )
    return result


def _box_points(box: tuple[float, float, float, float]) -> list[tuple[float, float]]:
    x, y, width, height = box
    return [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]


def _layer_key(layer: Mapping[str, Any], track_z: Mapping[str, int]) -> tuple[int, int, str]:
    track_id = layer.get("track_id")
    layer_id = layer.get("id")
    if not isinstance(track_id, str) or track_id not in track_z:
        raise RenderInputError(f"layer {layer_id!r} references an unknown track")
    z_offset = _integer(layer.get("z_offset"), f"layer {layer_id!r}.z_offset")
    if not isinstance(layer_id, str):
        raise RenderInputError("layer.id must be a string")
    return track_z[track_id], z_offset, layer_id


class S1Renderer:
    """Pillow renderer for the frozen deterministic S1 subset of the IR."""

    def __init__(self, template: Mapping[str, Any], assets: Mapping[str, ResolvedAsset]):
        _require_pillow()
        self.template = _require_mapping(template, "template")
        self.assets = dict(assets)
        if self.template.get("schema_version") != "0.2.0":
            raise RenderInputError("S1 renderer requires Template IR schema_version 0.2.0")
        if self.template.get("coordinate_space") != "canvas-pixels":
            raise UnsupportedFeatureError("only canvas-pixels coordinate_space is supported")
        support = _require_mapping(self.template.get("support"), "template.support")
        if support.get("level") != "S1":
            raise UnsupportedFeatureError("deterministic renderer supports Template IR support.level S1 only")

        canvas = _require_mapping(self.template.get("canvas"), "template.canvas")
        width = _integer(canvas.get("width"), "template.canvas.width")
        height = _integer(canvas.get("height"), "template.canvas.height")
        if width <= 0 or height <= 0:
            raise RenderInputError("template.canvas dimensions must be positive")
        self.canvas_size = (width, height)
        self.background = _rgba(canvas.get("background"), "template.canvas.background")
        if self.background[3] != 255:
            raise UnsupportedFeatureError(
                "template.canvas.background must be opaque; H.264 delivery cannot preserve alpha"
            )

        source = _require_mapping(self.template.get("source"), "template.source")
        self.duration_frames = _integer(source.get("duration_frames"), "template.source.duration_frames")
        if self.duration_frames <= 0:
            raise RenderInputError("template.source.duration_frames must be positive")
        self.fps = _number(source.get("fps"), "template.source.fps")
        if self.fps <= 0:
            raise RenderInputError("template.source.fps must be positive")

        self.slots = _slot_definitions(self.template)
        self.tracks: dict[str, Mapping[str, Any]] = {}
        self.track_z: dict[str, int] = {}
        for index, raw_track in enumerate(_require_list(self.template.get("tracks"), "template.tracks")):
            track = _require_mapping(raw_track, f"template.tracks[{index}]")
            track_id = track.get("id")
            if not isinstance(track_id, str) or not track_id:
                raise RenderInputError(f"template.tracks[{index}].id must be a non-empty string")
            if track_id in self.tracks:
                raise RenderInputError(f"template.tracks[{index}].id duplicates {track_id}")
            self.tracks[track_id] = track
            self.track_z[track_id] = _integer(track.get("z_index"), f"template.tracks[{index}].z_index")

        self.layers: list[Mapping[str, Any]] = []
        self.layers_by_track: dict[str, list[Mapping[str, Any]]] = {}
        for index, raw_layer in enumerate(_require_list(self.template.get("layers"), "template.layers")):
            layer = _require_mapping(raw_layer, f"template.layers[{index}]")
            track_id = layer.get("track_id")
            if not isinstance(track_id, str) or track_id not in self.tracks:
                raise RenderInputError(f"template.layers[{index}].track_id references an unknown track")
            self.layers.append(layer)
            self.layers_by_track.setdefault(track_id, []).append(layer)
        self.ordered_layers = sorted(self.layers, key=lambda layer: _layer_key(layer, self.track_z))
        for track_id, members in self.layers_by_track.items():
            members.sort(key=lambda layer: _layer_key(layer, self.track_z))
        self.carousel_track_ids = {
            track_id for track_id, track in self.tracks.items() if isinstance(track.get("group_layout"), Mapping)
        }
        self.carousel_work_sizes: dict[str, tuple[int, int]] = {}
        self._validate_carousel_tracks()
        self._validate_atomic_carousel_z_order()
        self._render_units = self._build_render_units()

        self._images: dict[str, Image.Image] = {}
        self._validate_and_load_assets()

    def _layer_slot_id(self, layer: Mapping[str, Any]) -> str:
        source = _require_mapping(layer.get("source"), f"layer {layer.get('id')!r}.source")
        slot_id = source.get("slot_id")
        if not isinstance(slot_id, str) or slot_id not in self.slots:
            raise RenderInputError(f"layer {layer.get('id')!r} references an unknown source slot")
        slot = self.slots[slot_id]
        if slot.get("type") == "garment" and source.get("representation") != "render-ready":
            raise RenderInputError(
                f"garment layer {layer.get('id')!r} must use a render-ready asset; generation is outside this renderer"
            )
        return slot_id

    def _validate_supported_mask(self, mask: Any, context: str, *, clip: bool = False) -> None:
        if mask is None:
            return
        mask_map = _require_mapping(mask, context)
        mask_type = mask_map.get("type")
        if mask_type not in SUPPORTED_MASK_TYPES:
            raise UnsupportedFeatureError(
                f"{context}.type {mask_type!r} is unsupported; supported masks are rect and polygon"
            )
        if clip and mask_map.get("space") != "canvas":
            raise UnsupportedFeatureError(f"{context} must use canvas space")
        if mask_map.get("space") not in {"canvas", "layer"}:
            raise UnsupportedFeatureError(f"{context}.space {mask_map.get('space')!r} is unsupported")

    def _validate_supported_layer(self, layer: Mapping[str, Any]) -> None:
        blend = _require_mapping(layer.get("blend"), f"layer {layer.get('id')!r}.blend")
        if blend.get("mode") != "normal":
            raise UnsupportedFeatureError(
                f"layer {layer.get('id')!r} requests blend mode {blend.get('mode')!r}; only normal is implemented"
            )
        _unit_interval(blend.get("opacity"), f"layer {layer.get('id')!r}.blend.opacity")
        self._validate_supported_mask(layer.get("mask"), f"layer {layer.get('id')!r}.mask")

    def _validate_carousel_tracks(self) -> None:
        """Freeze carousel support to a finite horizontal work buffer.

        The Template IR can describe repeated, vertical, or arbitrarily
        transformed groups.  S1 implements only the case needed by the gold
        template: non-negative item boxes that scroll horizontally as one
        opaque group.  Rejecting all other forms prevents a cropped Pillow
        canvas from looking plausibly correct while dropping source pixels.
        """
        canvas_width, canvas_height = self.canvas_size
        for track_id in self.carousel_track_ids:
            track = self.tracks[track_id]
            group_layout = _require_mapping(track.get("group_layout"), f"track {track_id}.group_layout")
            if group_layout.get("type") != "carousel":
                raise UnsupportedFeatureError(f"track {track_id}.group_layout.type must be carousel")
            if group_layout.get("direction") != "horizontal":
                raise UnsupportedFeatureError(
                    f"track {track_id} uses a non-horizontal carousel; S1 supports horizontal scrolling only"
                )
            if group_layout.get("repeat") != "none":
                raise UnsupportedFeatureError(
                    f"track {track_id}.group_layout.repeat must be none in deterministic S1"
                )
            _position_unbounded(group_layout.get("origin"), f"track {track_id}.group_layout.origin")

            members = self.layers_by_track.get(track_id, [])
            if not members:
                raise RenderInputError(f"carousel track {track_id} must contain at least one layer")

            transform = _require_mapping(track.get("group_transform"), f"track {track_id}.group_transform")
            _anchor(transform, f"track {track_id}.group_transform")
            raw_keyframes = _require_list(
                transform.get("keyframes"), f"track {track_id}.group_transform.keyframes"
            )
            if not raw_keyframes:
                raise RenderInputError(f"track {track_id}.group_transform.keyframes must not be empty")
            for index, raw_keyframe in enumerate(raw_keyframes):
                keyframe = _require_mapping(
                    raw_keyframe, f"track {track_id}.group_transform.keyframes[{index}]"
                )
                state = _keyframe_state(
                    keyframe, f"track {track_id}.group_transform.keyframes[{index}]"
                )
                if (
                    state.translate_y != 0
                    or state.scale_x != 1
                    or state.scale_y != 1
                    or state.rotation_deg != 0
                    or state.opacity != 1
                ):
                    raise UnsupportedFeatureError(
                        f"track {track_id}.group_transform only supports horizontal translate_x motion"
                    )
                # Validate all declared easing objects even when the requested
                # render frame lands on a keyframe boundary.
                _ease(keyframe.get("easing"), 0.0, f"track {track_id}.group_transform.keyframe easing")
            # This also checks frame ordering and type contracts once at
            # initialization instead of discovering them after frame writes.
            evaluate_transform(transform, 0)
            self._validate_supported_mask(track.get("clip_mask"), f"track {track_id}.clip_mask", clip=True)

            maximum_x = float(canvas_width)
            maximum_y = float(canvas_height)
            for layer in members:
                mask = layer.get("mask")
                if isinstance(mask, Mapping) and mask.get("space") == "canvas":
                    raise UnsupportedFeatureError(
                        f"carousel layer {layer.get('id')!r} uses a canvas-space mask; only layer-space masks are supported"
                    )
                layout = _require_mapping(layer.get("layout"), f"layer {layer.get('id')!r}.layout")
                box = _rect(layout.get("box"), f"layer {layer.get('id')!r}.layout.box")
                if box[0] < 0 or box[1] < 0:
                    raise UnsupportedFeatureError(
                        f"carousel layer {layer.get('id')!r} must have a non-negative layout x and y"
                    )
                maximum_x = max(maximum_x, box[0] + box[2])
                maximum_y = max(maximum_y, box[1] + box[3])
            self.carousel_work_sizes[track_id] = (
                max(canvas_width, math.ceil(maximum_x)),
                max(canvas_height, math.ceil(maximum_y)),
            )

    def _validate_atomic_carousel_z_order(self) -> None:
        """Prevent an atomic carousel composite from interleaving other tracks."""
        ranges: list[tuple[str, int, int, int]] = []
        for track_id in sorted(self.carousel_track_ids):
            members = self.layers_by_track.get(track_id, [])
            offsets = [_integer(layer.get("z_offset"), f"layer {layer.get('id')!r}.z_offset") for layer in members]
            if not offsets:  # Covered above, retained for defensive callers.
                raise RenderInputError(f"carousel track {track_id} must contain at least one layer")
            ranges.append((track_id, self.track_z[track_id], min(offsets), max(offsets)))

        for index, (track_id, track_z, lower, upper) in enumerate(ranges):
            for other_id, other_z, other_lower, other_upper in ranges[index + 1 :]:
                if track_z == other_z and not (upper < other_lower or other_upper < lower):
                    raise UnsupportedFeatureError(
                        f"carousel tracks {track_id} and {other_id} have interleaving z ranges at track z_index {track_z}"
                    )
            for layer in self.layers:
                other_track_id = layer.get("track_id")
                if not isinstance(other_track_id, str) or other_track_id in self.carousel_track_ids:
                    continue
                if self.track_z[other_track_id] != track_z:
                    continue
                other_offset = _integer(layer.get("z_offset"), f"layer {layer.get('id')!r}.z_offset")
                if lower <= other_offset <= upper:
                    raise UnsupportedFeatureError(
                        f"carousel track {track_id} z range [{lower}, {upper}] interleaves layer {layer.get('id')!r}"
                    )

    def _build_render_units(self) -> list[tuple[tuple[int, int, int, str], str, Any]]:
        """Return complete compositing units ordered without split carousel draws."""
        units: list[tuple[tuple[int, int, int, str], str, Any]] = []
        for track_id in self.carousel_track_ids:
            members = self.layers_by_track[track_id]
            offsets = [_integer(layer.get("z_offset"), f"layer {layer.get('id')!r}.z_offset") for layer in members]
            units.append(((self.track_z[track_id], min(offsets), 0, track_id), "carousel", track_id))
        for layer in self.layers:
            track_id = layer.get("track_id")
            if isinstance(track_id, str) and track_id in self.carousel_track_ids:
                continue
            track_z, z_offset, layer_id = _layer_key(layer, self.track_z)
            units.append(((track_z, z_offset, 1, layer_id), "layer", layer))
        return sorted(units, key=lambda unit: unit[0])

    def _validate_and_load_assets(self) -> None:
        for layer in self.layers:
            slot_id = self._layer_slot_id(layer)
            asset = self.assets.get(slot_id)
            if asset is None:
                # The validator permits absent optional slots.  No feature of a
                # skipped layer is evaluated or approximated.
                if self.slots[slot_id].get("required") is True:
                    raise RenderInputError(f"required layer source slot is unavailable: {slot_id}")
                continue
            self._validate_supported_layer(layer)
            if asset.media_type not in SUPPORTED_IMAGE_MEDIA_TYPES:
                raise UnsupportedFeatureError(
                    f"layer {layer.get('id')!r} uses {asset.media_type}; only static image assets are renderable"
                )
            if slot_id in self._images:
                continue
            try:
                with Image.open(asset.path) as opened:
                    self._images[slot_id] = ImageOps.exif_transpose(opened).convert("RGBA")
            except (OSError, UnidentifiedImageError) as exc:
                raise RenderInputError(f"unable to read static image for slot {slot_id}: {asset.path}") from exc

        for track_id in self.carousel_track_ids:
            if any(self._layer_slot_id(layer) in self._images for layer in self.layers_by_track.get(track_id, [])):
                track = self.tracks[track_id]
                _anchor(_require_mapping(track.get("group_transform"), f"track {track_id}.group_transform"), f"track {track_id}.group_transform")
                self._validate_supported_mask(track.get("clip_mask"), f"track {track_id}.clip_mask", clip=True)

    def _render_layer_image(
        self,
        layer: Mapping[str, Any],
        frame: int,
        *,
        canvas_size: tuple[int, int] | None = None,
    ) -> tuple["Image.Image", tuple[float, float, float, float], TransformState] | None:
        if not _is_active(layer, frame):
            return None
        slot_id = self._layer_slot_id(layer)
        source = self._images.get(slot_id)
        if source is None:
            return None
        target_size = canvas_size or self.canvas_size
        layer_image, layout_box = _layout_image(
            source, _require_mapping(layer.get("layout"), "layer.layout"), target_size
        )
        try:
            mask = layer.get("mask")
            if isinstance(mask, Mapping) and mask.get("space") == "layer":
                masked = _apply_mask(
                    layer_image, mask, target_size, layout_box, context=f"layer {layer.get('id')!r}.mask"
                )
                layer_image.close()
                layer_image = masked
            transform = _require_mapping(layer.get("transform"), f"layer {layer.get('id')!r}.transform")
            state = evaluate_transform(transform, frame)
            blend = _require_mapping(layer.get("blend"), f"layer {layer.get('id')!r}.blend")
            opaque = _alpha_with_opacity(
                layer_image, state.opacity * _unit_interval(blend.get("opacity"), "blend.opacity")
            )
            if opaque is not layer_image:
                layer_image.close()
                layer_image = opaque
            transformed = _apply_transform(
                layer_image, state, _anchor(transform, f"layer {layer.get('id')!r}.transform")
            )
            if transformed is not layer_image:
                layer_image.close()
                layer_image = transformed
            if isinstance(mask, Mapping) and mask.get("space") == "canvas":
                masked = _apply_mask(
                    layer_image, mask, target_size, None, context=f"layer {layer.get('id')!r}.mask"
                )
                layer_image.close()
                layer_image = masked
            return layer_image, layout_box, state
        except Exception:
            layer_image.close()
            raise

    def _render_carousel(self, track_id: str, frame: int) -> "Image.Image | None":
        track = self.tracks[track_id]
        work_size = self.carousel_work_sizes[track_id]
        working = Image.new("RGBA", work_size, (0, 0, 0, 0))
        rendered_any = False
        try:
            # Render each member into the larger coordinate-preserving work
            # buffer.  A product at x > canvas_width can then enter after a
            # negative group translate_x instead of being discarded first.
            for layer in self.layers_by_track.get(track_id, []):
                rendered = self._render_layer_image(layer, frame, canvas_size=work_size)
                if rendered is not None:
                    try:
                        working.alpha_composite(rendered[0])
                        rendered_any = True
                    finally:
                        rendered[0].close()
            if not rendered_any:
                return None
            transform = _require_mapping(track.get("group_transform"), f"track {track_id}.group_transform")
            state = evaluate_transform(transform, frame)
            transformed = _apply_transform(
                working, state, _anchor(transform, f"track {track_id}.group_transform")
            )
            if transformed is not working:
                working.close()
                working = transformed
            opaque = _alpha_with_opacity(working, state.opacity)
            if opaque is not working:
                working.close()
                working = opaque
            cropped = working.crop((0, 0, self.canvas_size[0], self.canvas_size[1]))
            try:
                clip_mask = _require_mapping(track.get("clip_mask"), f"track {track_id}.clip_mask")
                return _apply_mask(
                    cropped, clip_mask, self.canvas_size, None, context=f"track {track_id}.clip_mask"
                )
            finally:
                cropped.close()
        finally:
            working.close()

    @staticmethod
    def _debug_color(identifier: str) -> tuple[int, int, int, int]:
        digest = hashlib.sha256(identifier.encode("utf-8")).digest()
        return 96 + digest[0] // 2, 96 + digest[1] // 2, 96 + digest[2] // 2, 255

    def _draw_debug_bounds(self, canvas: "Image.Image", frame: int) -> None:
        draw = ImageDraw.Draw(canvas)
        drawn_carousels: set[str] = set()
        for layer in self.ordered_layers:
            if not _is_active(layer, frame) or self._layer_slot_id(layer) not in self._images:
                continue
            track_id = str(layer.get("track_id"))
            layout = _require_mapping(layer.get("layout"), "layer.layout")
            box = _rect(layout.get("box"), "layer.layout.box")
            transform = _require_mapping(layer.get("transform"), "layer.transform")
            points = _transform_points(
                _box_points(box), evaluate_transform(transform, frame), _anchor(transform, "layer.transform")
            )
            if track_id in self.carousel_track_ids:
                group_transform = _require_mapping(
                    self.tracks[track_id].get("group_transform"), f"track {track_id}.group_transform"
                )
                points = _transform_points(
                    points,
                    evaluate_transform(group_transform, frame),
                    _anchor(group_transform, f"track {track_id}.group_transform"),
                )
            draw.line(points + [points[0]], fill=self._debug_color(str(layer.get("id"))), width=1)
            if track_id in self.carousel_track_ids and track_id not in drawn_carousels:
                drawn_carousels.add(track_id)
                clip = _require_mapping(self.tracks[track_id].get("clip_mask"), f"track {track_id}.clip_mask")
                clip_type = clip.get("type")
                if clip_type == "rect":
                    x, y, width, height = _rect(clip.get("rect"), f"track {track_id}.clip_mask.rect")
                    draw.line(_box_points((x, y, width, height)) + [(x, y)], fill=(255, 255, 0, 255), width=1)
                elif clip_type == "polygon":
                    points = [
                        _position_unbounded(point, f"track {track_id}.clip_mask.points")
                        for point in _require_list(clip.get("points"), f"track {track_id}.clip_mask.points")
                    ]
                    if points:
                        draw.line(points + [points[0]], fill=(255, 255, 0, 255), width=1)

    def render_frame(self, frame: int, *, debug_bounds: bool = False) -> "Image.Image":
        """Render exactly one integer master-timeline frame as an RGBA image."""
        frame = _integer(frame, "frame")
        if not 0 <= frame < self.duration_frames:
            raise RenderInputError(f"frame must be within [0, {self.duration_frames})")
        canvas = Image.new("RGBA", self.canvas_size, self.background)
        for _, kind, payload in self._render_units:
            if kind == "carousel":
                rendered = self._render_carousel(str(payload), frame)
            else:
                rendered_layer = self._render_layer_image(_require_mapping(payload, "render unit layer"), frame)
                rendered = rendered_layer[0] if rendered_layer is not None else None
            if rendered is not None:
                try:
                    canvas.alpha_composite(rendered)
                finally:
                    rendered.close()
        if debug_bounds:
            self._draw_debug_bounds(canvas, frame)
        return canvas

    def write_master_frames(
        self,
        project_root: str | Path,
        frame_directory: str | Path = DEFAULT_MASTER_DIRECTORY,
        *,
        debug_bounds: bool = False,
    ) -> list[Path]:
        """Write a contiguous, zero-based master PNG sequence under project_root."""
        root = _project_root(project_root)
        destination = resolve_project_path(
            root, frame_directory, purpose="master frame directory", allow_absolute=True
        )
        _validate_new_master_target(self.template, destination)
        try:
            destination.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            # Keep the no-overwrite policy true under a concurrent creator too.
            raise RenderInputError(
                f"master frame directory already exists; refusing to overwrite: {destination}"
            ) from exc
        frames: list[Path] = []
        for frame in range(self.duration_frames):
            path = destination / (FRAME_FILENAME_PATTERN % frame)
            # destination itself has already passed containment.  The fixed
            # filename has no caller-controlled components.
            rendered = self.render_frame(frame, debug_bounds=debug_bounds)
            try:
                rendered.save(path, format="PNG", optimize=False, compress_level=9)
            finally:
                rendered.close()
            frames.append(path)
        return frames


def render_master_frames(
    renderer: S1Renderer,
    project_root: str | Path,
    frame_directory: str | Path = DEFAULT_MASTER_DIRECTORY,
    *,
    debug_bounds: bool = False,
) -> list[Path]:
    """Functional counterpart to :meth:`S1Renderer.write_master_frames`."""
    return renderer.write_master_frames(project_root, frame_directory, debug_bounds=debug_bounds)


def _ffmpeg_background(color: Any, name: str) -> str:
    red, green, blue, alpha = _rgba(color, name)
    if alpha != 255:
        raise UnsupportedFeatureError(
            f"{name} uses transparency, but H.264 delivery profiles cannot preserve an alpha background"
        )
    return f"0x{red:02x}{green:02x}{blue:02x}"


def _output_video_filter(output: Mapping[str, Any]) -> str:
    width = _integer(output.get("width"), "output.width")
    height = _integer(output.get("height"), "output.height")
    reframe = _require_mapping(output.get("reframe"), "output.reframe")
    mode = reframe.get("mode")
    position_x, position_y = _position(reframe.get("object_position"), "output.reframe.object_position")
    background = _ffmpeg_background(reframe.get("background"), "output.reframe.background")
    x_expr = f"(ow-iw)*{_fmt_number(position_x)}"
    y_expr = f"(oh-ih)*{_fmt_number(position_y)}"
    if mode == "stretch":
        return f"scale={width}:{height}:flags=lanczos"
    if mode == "contain":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={width}:{height}:{x_expr}:{y_expr}:color={background}"
        )
    if mode == "cover":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={width}:{height}:(iw-ow)*{_fmt_number(position_x)}:(ih-oh)*{_fmt_number(position_y)}"
        )
    raise UnsupportedFeatureError(f"output.reframe.mode {mode!r} is not supported")


def _atempo_filters(playback_rate: float) -> list[str]:
    if playback_rate <= 0:
        raise RenderInputError("audio.playback_rate must be positive")
    factors: list[float] = []
    remaining = playback_rate
    # Chaining keeps compatibility with ffmpeg versions whose atempo range is
    # [0.5, 2.0].  The stage cap prevents pathological unvalidated input from
    # turning into a giant command line.
    while remaining > 2.0 and len(factors) < 32:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5 and len(factors) < 32:
        factors.append(0.5)
        remaining /= 0.5
    if not 0.5 <= remaining <= 2.0:
        raise UnsupportedFeatureError("audio.playback_rate is too extreme for deterministic ffmpeg atempo")
    factors.append(remaining)
    return [f"atempo={_fmt_number(factor)}" for factor in factors if factor != 1.0]


def _audio_filter(template: Mapping[str, Any]) -> str:
    audio = _require_mapping(template.get("audio"), "template.audio")
    source = _require_mapping(template.get("source"), "template.source")
    fps = _number(source.get("fps"), "template.source.fps")
    duration_frames = _integer(source.get("duration_frames"), "template.source.duration_frames")
    timeline_start = _integer(audio.get("timeline_start_frame"), "audio.timeline_start_frame")
    timeline_end = _integer(audio.get("timeline_end_frame"), "audio.timeline_end_frame")
    if not 0 <= timeline_start < timeline_end <= duration_frames:
        raise RenderInputError("audio timeline must be within the master duration")
    source_in = _number(audio.get("source_in_ms"), "audio.source_in_ms") / 1000.0
    source_out = _number(audio.get("source_out_ms"), "audio.source_out_ms") / 1000.0
    if not 0 <= source_in < source_out:
        raise RenderInputError("audio source trim must have source_out_ms greater than source_in_ms")
    playback_rate = _number(audio.get("playback_rate"), "audio.playback_rate")
    timeline_seconds = (timeline_end - timeline_start) / fps
    full_seconds = duration_frames / fps
    filters = [
        f"atrim=start={_fmt_number(source_in)}:end={_fmt_number(source_out)}",
        "asetpts=PTS-STARTPTS",
        *_atempo_filters(playback_rate),
    ]
    loop = audio.get("loop")
    if not isinstance(loop, bool):
        raise RenderInputError("audio.loop must be boolean")
    if loop:
        # size=0 asks aloop to cache and loop the whole trimmed segment.
        filters.append("aloop=loop=-1:size=0")
    filters.append(f"atrim=duration={_fmt_number(timeline_seconds)}")
    fade_in = _integer(audio.get("fade_in_frames"), "audio.fade_in_frames")
    fade_out = _integer(audio.get("fade_out_frames"), "audio.fade_out_frames")
    if fade_in < 0 or fade_out < 0 or fade_in + fade_out > timeline_end - timeline_start:
        raise RenderInputError("audio fades must fit within the audio timeline")
    if fade_in:
        filters.append(f"afade=t=in:st=0:d={_fmt_number(fade_in / fps)}")
    if fade_out:
        filters.append(
            f"afade=t=out:st={_fmt_number(timeline_seconds - fade_out / fps)}:d={_fmt_number(fade_out / fps)}"
        )
    gain_db = _number(audio.get("gain_db"), "audio.gain_db")
    filters.append(f"volume={_fmt_number(gain_db)}dB")
    if timeline_start:
        filters.append(f"asetpts=PTS+{_fmt_number(timeline_start / fps)}/TB")
    filters.append(f"atrim=end={_fmt_number(full_seconds)}")
    return "[1:a]" + ",".join(filters) + "[aout]"


def _validate_output_profile(output: Mapping[str, Any]) -> tuple[int, int, str]:
    output_id = output.get("id")
    if not isinstance(output_id, str) or not output_id:
        raise RenderInputError("output.id must be a non-empty string")
    width = _integer(output.get("width"), f"output {output_id}.width")
    height = _integer(output.get("height"), f"output {output_id}.height")
    if (width, height) not in SUPPORTED_OUTPUT_SIZES:
        raise UnsupportedFeatureError(
            f"output {output_id} is {width}x{height}; frozen S1 delivery profiles are 720x1280 and 1080x1920"
        )
    if output.get("codec") != "h264":
        raise UnsupportedFeatureError(f"output {output_id} requests {output.get('codec')!r}; only H.264 is implemented")
    if output.get("pixel_format") != "yuv420p":
        raise UnsupportedFeatureError(
            f"output {output_id} requests {output.get('pixel_format')!r}; only yuv420p H.264 is implemented"
        )
    audio_codec = output.get("audio_codec")
    if audio_codec not in {"aac", "opus"}:
        raise UnsupportedFeatureError(f"output {output_id} requests unsupported audio codec {audio_codec!r}")
    return width, height, str(audio_codec)


def _audio_asset(template: Mapping[str, Any], assets: Mapping[str, ResolvedAsset]) -> ResolvedAsset | None:
    audio = _require_mapping(template.get("audio"), "template.audio")
    slot_id = audio.get("slot_id")
    if not isinstance(slot_id, str):
        raise RenderInputError("audio.slot_id must be a string")
    asset = assets.get(slot_id)
    if asset is None:
        slots = _slot_definitions(template)
        if slots.get(slot_id, {}).get("required") is True:
            raise RenderInputError(f"required audio slot is unavailable: {slot_id}")
        return None
    if asset.media_type not in SUPPORTED_AUDIO_MEDIA_TYPES:
        raise UnsupportedFeatureError(f"audio slot {slot_id} uses unsupported media type {asset.media_type}")
    return asset


def _validate_master_sequence(template: Mapping[str, Any], frame_directory: Path) -> None:
    """Require an exact zero-based master sequence before passing it to ffmpeg."""
    source = _require_mapping(template.get("source"), "template.source")
    duration = _integer(source.get("duration_frames"), "template.source.duration_frames")
    if duration <= 0:
        raise RenderInputError("template.source.duration_frames must be positive")
    if not frame_directory.is_dir():
        raise RenderInputError(f"master frame directory does not exist or is not a directory: {frame_directory}")
    discovered: dict[int, Path] = {}
    for candidate in frame_directory.iterdir():
        match = _MASTER_FRAME_NAME_RE.fullmatch(candidate.name)
        if match is None:
            continue
        if candidate.is_symlink():
            raise PathPolicyError(f"master sequence frame must not be a symlink: {candidate.name}")
        if not candidate.is_file():
            raise RenderInputError(f"master sequence member is not a regular frame file: {candidate.name}")
        index = int(match.group(1))
        expected_name = FRAME_FILENAME_PATTERN % index
        if candidate.name != expected_name:
            raise RenderInputError(f"master sequence has a malformed frame filename: {candidate.name}")
        if index in discovered:  # Impossible on a normal filesystem, retained for defensive callers.
            raise RenderInputError(f"master sequence has duplicate frame index {index}")
        discovered[index] = candidate
    expected = set(range(duration))
    actual = set(discovered)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        detail: list[str] = []
        if missing:
            detail.append("missing " + ", ".join(str(index) for index in missing[:8]))
        if unexpected:
            detail.append("unexpected " + ", ".join(str(index) for index in unexpected[:8]))
        raise RenderInputError(
            "master sequence must contain exactly contiguous frame indices 0.."
            f"{duration - 1}" + (f" ({'; '.join(detail)})" if detail else "")
        )


def _validate_new_master_target(template: Mapping[str, Any], frame_directory: Path) -> None:
    """Reject a master destination before any directory or frame is created."""
    source = _require_mapping(template.get("source"), "template.source")
    duration = _integer(source.get("duration_frames"), "template.source.duration_frames")
    if duration <= 0:
        raise RenderInputError("template.source.duration_frames must be positive")
    expected_paths = [frame_directory / (FRAME_FILENAME_PATTERN % frame) for frame in range(duration)]
    if frame_directory.exists() or any(path.exists() for path in expected_paths):
        raise RenderInputError(
            f"master frame directory or target frame already exists; refusing to overwrite: {frame_directory}"
        )


def _paths_overlap_as_files(left: Path, right: Path) -> bool:
    """Return true when two future file paths are equal or one nests under the other."""
    return left == right or left in right.parents or right in left.parents


def _bounded_error_text(value: Any, *, limit: int = 480) -> str:
    text = " ".join(str(value).split())
    if not text:
        return "unknown encoder error"
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _output_label(output: Mapping[str, Any]) -> str:
    return _bounded_error_text(output.get("id"), limit=120)


def _build_encode_command(
    template: Mapping[str, Any],
    output: Mapping[str, Any],
    frame_directory: Path,
    output_path: Path,
    audio_asset: ResolvedAsset | None,
    *,
    ffmpeg_bin: str | Path,
) -> list[str]:
    width, height, audio_codec = _validate_output_profile(output)
    source = _require_mapping(template.get("source"), "template.source")
    fps = _number(source.get("fps"), "template.source.fps")
    duration = _integer(source.get("duration_frames"), "template.source.duration_frames")
    if duration <= 0 or fps <= 0:
        raise RenderInputError("source duration and fps must be positive")
    frame_pattern = frame_directory / FRAME_FILENAME_PATTERN
    command = [
        str(ffmpeg_bin),
        "-n",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-framerate",
        _fmt_number(fps),
        "-start_number",
        "0",
        "-i",
        str(frame_pattern),
    ]
    if audio_asset is not None:
        command.extend(
            [
                "-i",
                str(audio_asset.path),
                "-filter_complex",
                _audio_filter(template),
                "-map",
                "0:v:0",
                "-map",
                "[aout]",
            ]
        )
    else:
        command.extend(["-map", "0:v:0", "-an"])
    command.extend(
        [
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-vf",
            _output_video_filter(output),
            "-frames:v",
            str(duration),
            "-r",
            _fmt_number(fps),
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-preset",
            "medium",
            "-pix_fmt",
            "yuv420p",
        ]
    )
    if audio_asset is not None:
        command.extend(["-c:a", "aac" if audio_codec == "aac" else "libopus"])
    command.extend(["-movflags", "+faststart", str(output_path)])
    return command


def _preflight_encode_outputs(
    template: Mapping[str, Any],
    assets: Mapping[str, ResolvedAsset],
    project_root: Path,
    frame_directory: str | Path,
    *,
    ffmpeg_bin: str | Path,
    timeout_seconds: float,
    require_master_sequence: bool,
) -> tuple[Path, float, ResolvedAsset | None, list[_PlannedOutput]]:
    """Build a complete encode plan without creating directories or files.

    ``render_project`` calls this before writing the master sequence, while
    ``encode_outputs`` calls it again after frames exist.  The repeated check
    is intentional: project preflight prevents deterministic late failures,
    and encode preflight remains the race-aware second line of defense.
    """
    template = _require_mapping(template, "template")
    frames = resolve_project_path(
        project_root, frame_directory, purpose="master frame directory", allow_absolute=True
    )
    if require_master_sequence:
        _validate_master_sequence(template, frames)
    else:
        _validate_new_master_target(template, frames)

    try:
        timeout = rrv_runtime.validate_timeout(timeout_seconds)
    except rrv_runtime.RRVError as exc:
        raise RenderInputError(f"timeout_seconds is invalid: {_bounded_error_text(exc.message)}") from exc

    executable = str(ffmpeg_bin)
    if not executable or "\x00" in executable:
        raise RenderInputError("ffmpeg_bin must be a non-empty path without NUL bytes")

    audio_asset = _audio_asset(template, assets)
    if audio_asset is not None:
        # Validate the complete trim/rate/fade/gain contract before any output
        # parent or master-frame directory can be created.
        _audio_filter(template)

    asset_paths = {asset.path.resolve(strict=False) for asset in assets.values()}
    if not require_master_sequence:
        for asset_path in asset_paths:
            if frames == asset_path or asset_path in frames.parents:
                raise PathPolicyError(
                    f"master frame directory must not be equal to or nested beneath an input asset: {frames}"
                )

    planned: list[_PlannedOutput] = []
    seen_paths: list[Path] = []
    for index, raw_output in enumerate(_require_list(template.get("outputs"), "template.outputs")):
        output = _require_mapping(raw_output, f"template.outputs[{index}]")
        width, height, _ = _validate_output_profile(output)
        filename = output.get("filename")
        if not isinstance(filename, str):
            raise RenderInputError(f"output {output.get('id')!r}.filename must be a string")
        path = resolve_project_path(
            project_root, filename, purpose=f"output {output.get('id')!r}", allow_absolute=True
        )
        if path.name.startswith("-"):
            raise PathPolicyError(f"output {output.get('id')!r} filename must not begin with a dash")
        if path in seen_paths:
            raise RenderInputError(f"output {output.get('id')!r} duplicates another output filename")
        if any(_paths_overlap_as_files(path, prior) for prior in seen_paths):
            raise PathPolicyError(
                f"output {output.get('id')!r} path conflicts with another output path"
            )
        seen_paths.append(path)
        if any(_paths_overlap_as_files(path, asset_path) for asset_path in asset_paths):
            raise PathPolicyError(f"output {output.get('id')!r} path conflicts with an input asset: {path}")
        if path.exists():
            raise RenderInputError(f"output already exists; refusing to overwrite: {path}")
        if _paths_overlap_as_files(path, frames):
            raise PathPolicyError(
                f"output {output.get('id')!r} must not overlap the master frame directory"
            )
        command = _build_encode_command(
            template, output, frames, path, audio_asset, ffmpeg_bin=ffmpeg_bin
        )
        planned.append(
            _PlannedOutput(
                output=output,
                path=path,
                width=width,
                height=height,
                command=tuple(command),
            )
        )
    return frames, timeout, audio_asset, planned


def _default_encoder_runner(command: list[str], *, timeout_seconds: float) -> None:
    """Run FFmpeg through the shared argv-only, bounded local runtime."""
    rrv_runtime.run_command(command, timeout_seconds=timeout_seconds, check=True)


def encode_outputs(
    template: Mapping[str, Any],
    assets: Mapping[str, ResolvedAsset],
    project_root: str | Path,
    frame_directory: str | Path = DEFAULT_MASTER_DIRECTORY,
    *,
    ffmpeg_bin: str | Path = "ffmpeg",
    runner: EncoderRunner | None = None,
    timeout_seconds: float = DEFAULT_ENCODER_TIMEOUT_SECONDS,
) -> list[EncodedOutput]:
    """Call an externally installed ffmpeg to encode the requested profiles.

    No shell is used: every invocation is a parameter array.  ``runner`` is a
    narrow test seam for a fake encoder; production callers normally leave it
    unset and use the host's ``ffmpeg`` executable.
    """
    template = _require_mapping(template, "template")
    root = _project_root(project_root)
    _, timeout, audio_asset, planned_outputs = _preflight_encode_outputs(
        template,
        assets,
        root,
        frame_directory,
        ffmpeg_bin=ffmpeg_bin,
        timeout_seconds=timeout_seconds,
        require_master_sequence=True,
    )

    encoded: list[EncodedOutput] = []
    for plan in planned_outputs:
        plan.path.parent.mkdir(parents=True, exist_ok=True)
        command = list(plan.command)
        try:
            if runner is None:
                _default_encoder_runner(command, timeout_seconds=timeout)
            else:
                runner(command)
        except FileNotFoundError as exc:
            raise EncoderError(
                f"ffmpeg executable was not found: {_bounded_error_text(ffmpeg_bin, limit=240)}"
            ) from exc
        except rrv_runtime.RRVError as exc:
            if exc.code == rrv_runtime.ERR_TOOL_TIMEOUT:
                raise EncoderError(
                    f"ffmpeg timed out for output {_output_label(plan.output)!r} after {_fmt_number(timeout)} seconds"
                ) from exc
            raise EncoderError(
                f"ffmpeg failed for output {_output_label(plan.output)!r}: {_bounded_error_text(exc.message)}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise EncoderError(
                f"ffmpeg timed out for output {_output_label(plan.output)!r} after {_fmt_number(timeout)} seconds"
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise EncoderError(
                f"ffmpeg failed for output {_output_label(plan.output)!r} with exit code {exc.returncode}"
            ) from exc
        except OSError as exc:
            raise EncoderError(
                f"ffmpeg could not run for output {_output_label(plan.output)!r}: {_bounded_error_text(exc)}"
            ) from exc
        except Exception as exc:
            raise EncoderError(
                f"ffmpeg failed for output {_output_label(plan.output)!r}: {_bounded_error_text(exc)}"
            ) from exc
        encoded.append(
            EncodedOutput(
                output_id=str(plan.output.get("id")),
                path=plan.path,
                width=plan.width,
                height=plan.height,
                audio_muxed=audio_asset is not None,
            )
        )
    return encoded


def build_run_summary(
    template: Mapping[str, Any],
    assets: Mapping[str, ResolvedAsset],
    project_root: str | Path,
    frame_directory: str | Path,
    outputs: Sequence[EncodedOutput],
    *,
    debug_bounds: bool,
) -> dict[str, Any]:
    """Return a timestamp-free payload suitable for stable JSON logging."""
    template = _require_mapping(template, "template")
    root = _project_root(project_root)
    source = _require_mapping(template.get("source"), "template.source")
    canvas = _require_mapping(template.get("canvas"), "template.canvas")
    frames = resolve_project_path(
        root, frame_directory, purpose="master frame directory", allow_absolute=True
    )
    asset_rows = [
        {
            "slot_id": slot_id,
            "media_type": asset.media_type,
            "path": _relative_project_path(root, asset.path),
        }
        for slot_id, asset in sorted(assets.items())
    ]
    skipped_optional_slots = sorted(
        slot_id
        for slot_id, slot in _slot_definitions(template).items()
        if slot.get("required") is False and slot_id not in assets
    )
    output_rows = [
        {
            "id": output.output_id,
            "path": _relative_project_path(root, output.path),
            "width": output.width,
            "height": output.height,
            "codec": "h264",
            "audio_muxed": output.audio_muxed,
        }
        for output in sorted(outputs, key=lambda item: item.output_id)
    ]
    return {
        "status": "ok",
        "renderer": RENDERER_ID,
        "template": {
            "id": template.get("template_id"),
            "schema_version": template.get("schema_version"),
        },
        "master": {
            "directory": _relative_project_path(root, frames),
            "frame_pattern": FRAME_FILENAME_PATTERN,
            "frame_count": _integer(source.get("duration_frames"), "template.source.duration_frames"),
            "fps": _number(source.get("fps"), "template.source.fps"),
            "width": _integer(canvas.get("width"), "template.canvas.width"),
            "height": _integer(canvas.get("height"), "template.canvas.height"),
            "debug_bounds": bool(debug_bounds),
        },
        "assets": asset_rows,
        "outputs": output_rows,
        "warnings": [f"optional slot skipped: {slot_id}" for slot_id in skipped_optional_slots],
    }


def stable_summary_json(summary: Mapping[str, Any]) -> str:
    """Serialize a run summary without timestamps, whitespace drift, or NaN."""
    return json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def render_project(
    template: Mapping[str, Any],
    manifest: Mapping[str, Any],
    project_root: str | Path,
    *,
    frame_directory: str | Path = DEFAULT_MASTER_DIRECTORY,
    debug_bounds: bool = False,
    ffmpeg_bin: str | Path = "ffmpeg",
    encoder_runner: EncoderRunner | None = None,
    timeout_seconds: float = DEFAULT_ENCODER_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Resolve local assets, render one master sequence, encode, and summarize."""
    root = _project_root(project_root)
    assets = resolve_local_assets(template, manifest, root)
    _preflight_encode_outputs(
        template,
        assets,
        root,
        frame_directory,
        ffmpeg_bin=ffmpeg_bin,
        timeout_seconds=timeout_seconds,
        require_master_sequence=False,
    )
    renderer = S1Renderer(template, assets)
    renderer.write_master_frames(root, frame_directory, debug_bounds=debug_bounds)
    outputs = encode_outputs(
        template,
        assets,
        root,
        frame_directory,
        ffmpeg_bin=ffmpeg_bin,
        runner=encoder_runner,
        timeout_seconds=timeout_seconds,
    )
    return build_run_summary(
        template,
        assets,
        root,
        frame_directory,
        outputs,
        debug_bounds=debug_bounds,
    )


# A compact alias is convenient to future orchestration code while preserving
# the descriptive public name above.
render = render_project


__all__ = [
    "DEFAULT_ENCODER_TIMEOUT_SECONDS",
    "DEFAULT_MASTER_DIRECTORY",
    "FRAME_FILENAME_PATTERN",
    "RENDERER_ID",
    "SUPPORTED_OUTPUT_SIZES",
    "EncodedOutput",
    "EncoderError",
    "PathPolicyError",
    "RenderDependencyError",
    "RenderError",
    "RenderInputError",
    "ResolvedAsset",
    "S1Renderer",
    "TransformState",
    "UnsupportedFeatureError",
    "build_run_summary",
    "encode_outputs",
    "evaluate_transform",
    "render",
    "render_master_frames",
    "render_project",
    "resolve_local_assets",
    "resolve_project_path",
    "stable_summary_json",
]
