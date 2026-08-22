#!/usr/bin/env python3
"""Public alpha CLI for the ``rebuild-reference-video`` Skill.

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
from pathlib import Path
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


def _compact_error_text(value: object, *, limit: int = 480) -> str:
    text = " ".join(str(value).strip().split())
    if not text:
        return "operation failed"
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


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


def sha256_file(path: Path, chunk_size: int = SHA256_CHUNK_SIZE) -> str:
    """Return a file digest without loading media-sized files into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


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
    has_ffmpeg = bool(tools.ffmpeg.path)
    has_ffprobe = bool(tools.ffprobe.path)
    has_pillow = _pillow_available()
    return {
        "status": "ok",
        "stage": "alpha",
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "runtime": {
            "media_tools": tools.to_dict(),
            "ffmpeg": tools.ffmpeg.to_dict(),
            "ffprobe": tools.ffprobe.to_dict(),
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
            "asset_path_policy_validation": True,
            "asset_media_probe_validation": False,
            "media_probe": has_ffprobe or has_ffmpeg,
            "reference_survey": has_ffmpeg,
            "reference_analysis": False,
            "asset_generation": False,
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
            "Semantic slot analysis remains agent-assisted; render-ready replacement looks must be supplied or generated before this CLI renders.",
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
    privacy_profile = root["privacy_profile"]
    root_path = (project_root or manifest_path.parent).resolve()
    if not root_path.is_dir():
        errors.append(f"project root does not exist or is not a directory: {root_path}")
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
        if isinstance(path_value, str):
            raw_path = Path(path_value)
            resolved = raw_path.resolve() if raw_path.is_absolute() else (root_path / raw_path).resolve()
            try:
                resolved.relative_to(root_path)
            except ValueError:
                errors.append(f"$.assets[{index}].path escapes the project root: {path_value}")
            else:
                if check_files and not resolved.is_file():
                    errors.append(f"$.assets[{index}].path does not exist: {resolved}")
                elif check_files and asset.get("sha256") is not None:
                    expected_sha256 = asset["sha256"]
                    if sha256_file(resolved).lower() != expected_sha256.lower():
                        errors.append(f"$.assets[{index}].sha256 does not match {resolved}")
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


def _render_hashes(
    template_path: Path,
    manifest_path: Path,
    template: Mapping[str, Any],
    manifest: Mapping[str, Any],
    project_root: Path,
    runtime: Any,
) -> dict[str, Any]:
    """Record the immutable inputs that produced a deterministic delivery."""

    source = template.get("source") if isinstance(template.get("source"), Mapping) else {}
    asset_rows: list[dict[str, Any]] = []
    raw_assets = manifest.get("assets") if isinstance(manifest.get("assets"), list) else []
    for asset in sorted(
        (item for item in raw_assets if isinstance(item, Mapping)),
        key=lambda item: str(item.get("slot_id", "")),
    ):
        path_value = asset.get("path")
        if not isinstance(path_value, str):
            # Provider-only assets are not accepted by the local renderer, but
            # keeping this branch makes the provenance function total.
            continue
        raw_path = Path(path_value)
        resolved = raw_path.resolve() if raw_path.is_absolute() else (project_root / raw_path).resolve()
        asset_rows.append(
            {
                "slot_id": asset.get("slot_id"),
                "path": runtime.relative_output_path(project_root, resolved),
                "sha256": sha256_file(resolved),
            }
        )
    return {
        "template_sha256": sha256_file(template_path),
        "manifest_sha256": sha256_file(manifest_path),
        "source_sha256": source.get("source_sha256"),
        "assets": asset_rows,
    }


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


def _require_render_inputs(
    args: argparse.Namespace,
    runtime: Any,
) -> tuple[Path, dict[str, Any], dict[str, Any], list[str]]:
    """Load and fully validate a render request before creating output files."""

    project_root = runtime.require_project_root(args.project_root)
    # Reject an unsafe or existing optional summary before inspecting a project
    # further.  This is read-only and ensures a bad path can never be reached
    # after a costly render.
    if args.summary is not None:
        runtime.resolve_output_path(project_root, args.summary, must_not_exist=True)
    template = load_json(args.template)
    manifest = load_json(args.manifest)
    if not isinstance(template, dict) or not isinstance(manifest, dict):
        return project_root, {}, {}, ["template and manifest must be JSON objects"]
    template_errors = validate_template_data(template)
    asset_errors = validate_assets_data(
        template,
        manifest,
        args.manifest,
        check_files=True,
        project_root=project_root,
    )
    errors = _deduplicate_errors([*template_errors, *asset_errors])
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
            {"reason": _compact_error_text(exc)},
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
        "renderer": renderer_summary,
        "qa": qa_summary,
        "hashes": _render_hashes(
            args.template, args.manifest, template, manifest, project_root, runtime
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


def build_parser() -> argparse.ArgumentParser:
    parser = _BoundedArgumentParser(prog="video-remix")
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
        if args.command == "render":
            payload, status = run_render(args)
            _emit_stable_json(payload)
            return status
        if args.command == "qa":
            payload, status = run_qa(args)
            _emit_stable_json(payload)
            return status

        if args.command == "validate-template":
            template = load_json(args.template)
            errors = validate_template_data(template)
        else:
            template = load_json(args.template)
            errors = validate_assets_data(
                template,
                load_json(args.manifest),
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
