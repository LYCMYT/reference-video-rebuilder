#!/usr/bin/env python3
"""Deterministic human-review evidence for an approved faithful plan.

This module samples reviewed source frames and builds a metadata-free contact
sheet.  It deliberately performs no OCR and cannot prove that a human text
inventory is complete.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

try:
    import rrv_faithful
    import rrv_propose
    import rrv_runtime
except ImportError:  # pragma: no cover - package-style imports.
    from . import rrv_faithful, rrv_propose, rrv_runtime  # type: ignore[no-redef]


EVIDENCE_SCHEMA_VERSION = "0.9.1"
MAX_PANELS = 24
UNIFORM_SAMPLE_COUNT = 12
_SKILL_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_PATH = _SKILL_ROOT / "assets" / "schemas" / "faithful-evidence-report.schema.json"
_report_validator: Any | None = None


def _invalid(message: str) -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(rrv_runtime.ERR_INVALID_ARGUMENT, message)


def _tool_error(message: str) -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(rrv_runtime.ERR_TOOL_EXECUTION, message)


def _pillow() -> tuple[Any, Any, Any]:
    try:
        from PIL import Image, ImageDraw, ImageOps
    except ImportError as exc:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_CAPABILITY_UNAVAILABLE,
            "faithful review evidence requires Pillow",
        ) from exc
    return Image, ImageDraw, ImageOps


def _validator() -> Any:
    global _report_validator
    if _report_validator is not None:
        return _report_validator
    try:
        import json
        from jsonschema import Draft202012Validator
    except ImportError as exc:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_CAPABILITY_UNAVAILABLE,
            "faithful evidence validation requires jsonschema",
        ) from exc
    try:
        schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, ValueError, TypeError) as exc:
        raise _tool_error("faithful evidence schema is unavailable") from exc
    _report_validator = Draft202012Validator(schema)
    return _report_validator


def _validate_report(report: Mapping[str, Any]) -> None:
    errors = sorted(_validator().iter_errors(report), key=lambda item: list(item.absolute_path))
    if errors:
        raise _tool_error("faithful evidence report did not pass its schema")


def _uniform_frames(frame_count: int, count: int = UNIFORM_SAMPLE_COUNT) -> list[int]:
    bounded = min(max(1, count), frame_count)
    if bounded == 1:
        return [0]
    last = frame_count - 1
    return [int(round(index * last / (bounded - 1))) for index in range(bounded)]


def _inventory_midpoint(item: Mapping[str, Any]) -> int:
    return (int(item["start_frame"]) + int(item["end_frame"]) - 1) // 2


def select_evidence_frames(plan: Mapping[str, Any], *, max_panels: int = MAX_PANELS) -> dict[str, Any]:
    """Select a bounded deterministic mix of inventory and whole-video frames."""

    rrv_faithful.validate_faithful_plan(plan)
    if isinstance(max_panels, bool) or not isinstance(max_panels, int) or not 1 <= max_panels <= 24:
        raise _invalid("max_panels must be an integer from 1 through 24")
    frame_count = int(plan["source"]["frame_count"])
    inventory = list(plan["text_inventory"])
    midpoint_candidates = sorted({_inventory_midpoint(item) for item in inventory})
    if len(midpoint_candidates) > max_panels:
        # Spread the bounded review surface across all inventory midpoints.
        indexes = _uniform_frames(len(midpoint_candidates), max_panels)
        selected = [midpoint_candidates[index] for index in indexes]
    else:
        selected = list(midpoint_candidates)
        for frame in _uniform_frames(frame_count):
            if len(selected) >= max_panels:
                break
            if frame not in selected:
                selected.append(frame)
    selected = sorted(set(selected))
    included = set(selected)
    inventory_without_midpoint_panel = sum(
        1 for item in inventory if _inventory_midpoint(item) not in included
    )
    return {
        "selected_frames": selected,
        "inventory_midpoint_candidates": midpoint_candidates,
        "inventory_without_midpoint_panel": inventory_without_midpoint_panel,
        "truncated": inventory_without_midpoint_panel > 0,
    }


def _active_inventory(plan: Mapping[str, Any], frame: int) -> list[Mapping[str, Any]]:
    return [
        item
        for item in plan["text_inventory"]
        if int(item["start_frame"]) <= frame < int(item["end_frame"])
    ]


def _write_contact_sheet(
    stage: Any,
    destination: Path,
    frame_paths: Sequence[Path],
    selected_frames: Sequence[int],
    plan: Mapping[str, Any],
) -> None:
    Image, ImageDraw, ImageOps = _pillow()
    columns = 4
    panel_width = 320
    image_box = (300, 200)
    header_height = 48
    panel_height = header_height + image_box[1] + 12
    rows = int(math.ceil(len(frame_paths) / columns))
    sheet = Image.new("RGB", (columns * panel_width, rows * panel_height), (245, 245, 245))
    draw = ImageDraw.Draw(sheet)
    source_width = int(plan["source"]["width"])
    source_height = int(plan["source"]["height"])
    fps = float(plan["source"]["fps"])
    try:
        for index, (path, frame) in enumerate(zip(frame_paths, selected_frames)):
            rrv_propose._assert_stage_regular_file(stage, path, "faithful evidence frame")
            with Image.open(path) as opened:
                opened.verify()
            with Image.open(path) as opened:
                if getattr(opened, "is_animated", False):
                    raise _tool_error("faithful evidence frame was unexpectedly animated")
                opened.load()
                clean = Image.frombytes("RGB", opened.size, opened.convert("RGB").tobytes())
            thumb = ImageOps.contain(clean, image_box)
            column = index % columns
            row = index // columns
            left = column * panel_width
            top = row * panel_height
            draw.rectangle((left, top, left + panel_width - 1, top + panel_height - 1), outline=(180, 180, 180))
            label = f"frame {frame}  t={frame / fps:.3f}s"
            draw.text((left + 8, top + 6), label, fill=(15, 15, 15))
            active = _active_inventory(plan, frame)
            ids = ",".join(str(item["id"]) for item in active)
            if len(ids) > 43:
                ids = ids[:42] + "…"
            draw.text((left + 8, top + 25), ids or "no declared text", fill=(70, 70, 70))
            image_left = left + (panel_width - thumb.width) // 2
            image_top = top + header_height + (image_box[1] - thumb.height) // 2
            sheet.paste(thumb, (image_left, image_top))
            scale_x = thumb.width / source_width
            scale_y = thumb.height / source_height
            for item in active:
                rect = item["region"]
                x0 = image_left + int(round(int(rect["x"]) * scale_x))
                y0 = image_top + int(round(int(rect["y"]) * scale_y))
                x1 = x0 + max(1, int(round(int(rect["width"]) * scale_x)))
                y1 = y0 + max(1, int(round(int(rect["height"]) * scale_y)))
                draw.rectangle((x0, y0, x1, y1), outline=(255, 55, 45), width=2)
        with rrv_propose._open_stage_output_file(
            stage, destination, "faithful review contact sheet"
        ) as handle:
            # The newly-created RGB sheet carries no EXIF, text, source name,
            # or other inherited image metadata.
            sheet.save(handle, format="PNG", optimize=False)
    except rrv_runtime.RRVError:
        raise
    except Exception as exc:
        raise _tool_error("faithful review contact sheet could not be created") from exc
    finally:
        sheet.close()
    rrv_propose._assert_stage_regular_file(stage, destination, "faithful review contact sheet")


def _covered_frame_count(plan: Mapping[str, Any]) -> int:
    ranges = sorted(
        (int(item["start_frame"]), int(item["end_frame"]))
        for item in plan["text_inventory"]
    )
    total = 0
    current_start: int | None = None
    current_end: int | None = None
    for start, end in ranges:
        if current_start is None:
            current_start, current_end = start, end
        elif start <= int(current_end):
            current_end = max(int(current_end), end)
        else:
            total += int(current_end) - current_start
            current_start, current_end = start, end
    if current_start is not None:
        total += int(current_end) - current_start
    return total


def build_faithful_evidence(
    plan: Mapping[str, Any],
    project_root: str | os.PathLike[str],
    output_dir: str | os.PathLike[str] = "faithful-evidence",
    *,
    tools: rrv_runtime.RuntimeTools | None = None,
    ffmpeg: str | os.PathLike[str] | None = None,
    ffprobe: str | os.PathLike[str] | None = None,
    timeout_seconds: float = rrv_faithful.DEFAULT_TIMEOUT_SECONDS,
    max_panels: int = MAX_PANELS,
    probe_media_fn: Callable[..., Mapping[str, Any]] | None = None,
    exact_timing_fn: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Atomically create local review evidence without making semantic claims."""

    if not isinstance(plan, Mapping) or plan.get("rights_confirmed") is not True:
        raise _invalid("rights_confirmed must be true before touching faithful evidence inputs")
    rrv_faithful.validate_faithful_plan(plan)
    selection = select_evidence_frames(plan, max_panels=max_panels)
    timeout = rrv_runtime.validate_timeout(timeout_seconds)
    root = rrv_faithful._safe_project_root(project_root)
    source_relative = str(plan["source"]["path"])
    source_parts = rrv_faithful._relative_parts(source_relative, "plan.source.path")
    source_path = root.joinpath(*source_parts)
    source_identity = rrv_faithful._safe_regular_file(root, source_path, "plan.source.path")
    target = rrv_propose._direct_child_output_target(root, output_dir)
    runtime_tools, ffmpeg_path, ffprobe_path = rrv_faithful._require_runtime_tools(
        tools, ffmpeg=ffmpeg, ffprobe=ffprobe
    )
    with rrv_faithful._hold_bound_file(root, source_identity, "plan.source.path"):
        source_sha256 = rrv_faithful._sha256_bound_file(
            root, source_identity, "plan.source.path"
        )
        if source_sha256 != plan["source"]["sha256"]:
            raise _invalid("plan.source.sha256 does not match the checked local source")
        facts = rrv_faithful._probe_facts(
            source_path,
            runtime_tools=runtime_tools,
            ffprobe=ffprobe_path,
            timeout_seconds=timeout,
            probe_media_fn=probe_media_fn,
            exact_timing_fn=exact_timing_fn,
        )
        rrv_faithful._assert_plan_media_facts(plan, facts)
        if (
            rrv_faithful._sha256_bound_file(root, source_identity, "plan.source.path")
            != source_sha256
        ):
            raise _invalid("source hash changed during faithful evidence preflight")

    stage: Any | None = None
    frame_paths: list[Path] = []
    try:
        stage = rrv_propose._new_staging_directory(root, "faithful-evidence")
        snapshot_path, snapshot_identity, snapshot_sha256 = (
            rrv_faithful._snapshot_bound_file_to_stage(
                root,
                source_identity,
                stage,
                "source-snapshot.mp4",
                label="faithful evidence source",
                expected_sha256=source_sha256,
            )
        )
        source_rect = {
            "x": 0,
            "y": 0,
            "width": int(plan["source"]["width"]),
            "height": int(plan["source"]["height"]),
        }
        with rrv_faithful._hold_bound_file(
            root, snapshot_identity, "faithful evidence source snapshot"
        ):
            for index, frame in enumerate(selection["selected_frames"]):
                frame_path = rrv_propose._stage_path(root, stage, f"sample-{index:02d}.png")
                command = rrv_propose._build_evidence_frame_command(
                    snapshot_path, ffmpeg_path, source_rect, int(frame), frame_path
                )
                rrv_propose._run_output(
                    stage,
                    command,
                    frame_path,
                    timeout,
                    "faithful evidence frame extraction",
                    image_output=True,
                )
                frame_paths.append(frame_path)
            if (
                rrv_faithful._sha256_bound_file(
                    root, snapshot_identity, "faithful evidence source snapshot"
                )
                != snapshot_sha256
            ):
                raise _invalid("faithful evidence source snapshot changed during extraction")
        if rrv_faithful._sha256_bound_file(root, source_identity, "plan.source.path") != source_sha256:
            raise _invalid("source hash changed during faithful evidence extraction")

        sheet_path = rrv_propose._stage_path(root, stage, "contact-sheet.png")
        _write_contact_sheet(stage, sheet_path, frame_paths, selection["selected_frames"], plan)
        for frame_path in frame_paths:
            rrv_propose._remove_stage_file(stage, frame_path)
        frame_paths.clear()
        rrv_propose._remove_stage_file(stage, snapshot_path)
        contact_identity = rrv_faithful._safe_regular_file(
            root, sheet_path, "faithful review contact sheet"
        )
        contact_sha256 = rrv_faithful._sha256_bound_file(
            root, contact_identity, "faithful review contact sheet"
        )
        output_relative = rrv_propose._lexical_relative_output_path(root, target)
        sheet_relative = rrv_propose._lexical_relative_output_path(
            root, target / "contact-sheet.png"
        )
        report_relative = rrv_propose._lexical_relative_output_path(
            root, target / "faithful-evidence.json"
        )
        inventory_rows = []
        selected_set = set(selection["selected_frames"])
        for item in plan["text_inventory"]:
            midpoint = _inventory_midpoint(item)
            inventory_rows.append(
                {
                    "id": item["id"],
                    "start_frame": item["start_frame"],
                    "end_frame": item["end_frame"],
                    "sample_frame": midpoint,
                    "panel_included": midpoint in selected_set,
                    "lines": list(item["lines"]),
                    "region": dict(item["region"]),
                    "human_reviewed": True,
                }
            )
        report: dict[str, Any] = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "operation": "faithful-review-evidence",
            "claim": "human_review_support_only",
            "ocr_used": False,
            "output_dir": output_relative,
            "plan": {"canonical_sha256": rrv_faithful._canonical_plan_sha256(plan)},
            "source": {"path": source_relative, "sha256": source_sha256},
            "media_facts": facts.to_dict(),
            "inventory_count": len(inventory_rows),
            "inventory_covered_frame_count": _covered_frame_count(plan),
            "inventory": inventory_rows,
            "sampling": {
                "max_panels": max_panels,
                "selected_frames": list(selection["selected_frames"]),
                "inventory_without_midpoint_panel": selection[
                    "inventory_without_midpoint_panel"
                ],
                "truncated": selection["truncated"],
            },
            "artifacts": {
                "contact_sheet": {
                    "path": sheet_relative,
                    "sha256": contact_sha256,
                    "panel_count": len(selection["selected_frames"]),
                },
                "report": {"path": report_relative},
            },
            "limitations": [
                "No OCR or semantic inference was performed.",
                "This evidence cannot prove that no visible text item was omitted.",
            ],
        }
        _validate_report(report)
        report_path = rrv_propose._stage_path(root, stage, "faithful-evidence.json")
        rrv_propose._write_json_new(
            report_path, report, label="faithful evidence report", stage=stage
        )
        report_identity = rrv_faithful._safe_regular_file(
            root, report_path, "faithful evidence report"
        )
        report_sha256 = rrv_faithful._sha256_bound_file(
            root, report_identity, "faithful evidence report"
        )
        if (
            rrv_faithful._sha256_bound_file(
                root, contact_identity, "faithful review contact sheet"
            )
            != contact_sha256
        ):
            raise _invalid("faithful review contact sheet changed before publication")
        if rrv_faithful._sha256_bound_file(root, source_identity, "plan.source.path") != source_sha256:
            raise _invalid("source hash changed during faithful evidence creation")
        rrv_propose._publish_stage(
            root,
            stage,
            target,
            label="faithful evidence",
            expected_files={
                "contact-sheet.png": contact_sha256,
                "faithful-evidence.json": report_sha256,
            },
        )
        stage = None
        return report
    except Exception:
        rrv_propose._cleanup_directory(root, stage)
        raise


__all__ = [
    "EVIDENCE_SCHEMA_VERSION",
    "MAX_PANELS",
    "build_faithful_evidence",
    "select_evidence_frames",
]
