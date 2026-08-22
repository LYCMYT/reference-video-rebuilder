#!/usr/bin/env python3
"""Deterministic, bounded reference-media survey built on ``rrv_runtime``.

The module extracts only caller-selected or uniformly sampled frames, composes
an optional contact sheet, and stream-copies the first audio stream.  It makes
no semantic claims about the media and never writes to the source file.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:  # Direct execution from the scripts directory.
    import rrv_runtime
except ImportError:  # pragma: no cover - useful when installed as a package.
    from . import rrv_runtime  # type: ignore[no-redef]


DEFAULT_SAMPLE_COUNT = 12
MAX_SAMPLE_COUNT = 64
SHA256_CHUNK_SIZE = 1024 * 1024


def sha256_file(path: str | os.PathLike[str], *, chunk_size: int = SHA256_CHUNK_SIZE) -> str:
    """Hash a media file without loading it into memory all at once."""

    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_INVALID_ARGUMENT, "chunk_size must be a positive integer"
        )
    source = rrv_runtime.require_source_file(path)
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _as_nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_INVALID_ARGUMENT, f"{field} must be a non-negative integer"
        )
    return value


def _as_sample_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_SAMPLE_COUNT:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_INVALID_ARGUMENT,
            f"sample_count must be an integer from 1 to {MAX_SAMPLE_COUNT}",
        )
    return value


def _absolute_output_path(output: str | os.PathLike[str]) -> Path:
    """Make an FFmpeg output operand unambiguously a path, never an option."""

    try:
        path = Path(output)
        if not str(path) or "\x00" in str(path):
            raise ValueError("empty or NUL output path")
        return path.resolve(strict=False)
    except (TypeError, OSError, RuntimeError, ValueError) as exc:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_INVALID_ARGUMENT, "output must be a valid path"
        ) from exc


def _video_stream(media: Mapping[str, Any]) -> Mapping[str, Any]:
    stream = rrv_runtime.first_stream(media, "video")
    if stream is None:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_CAPABILITY_UNAVAILABLE,
            "frame sampling requires at least one video stream",
            {"capability": "frame_sampling"},
        )
    return stream


def _frame_rate(stream: Mapping[str, Any]) -> float | None:
    for key in ("average_frame_rate", "frame_rate"):
        value = stream.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            candidate = float(value)
            if math.isfinite(candidate) and candidate > 0:
                return candidate
    return None


def _duration_seconds(media: Mapping[str, Any], stream: Mapping[str, Any]) -> float | None:
    format_data = media.get("format")
    values: list[Any] = []
    if isinstance(format_data, Mapping):
        values.append(format_data.get("duration_seconds"))
    values.append(stream.get("duration_seconds"))
    for value in values:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            candidate = float(value)
            if math.isfinite(candidate) and candidate > 0:
                return candidate
    return None


def choose_frame_numbers(
    media: Mapping[str, Any],
    *,
    frame_numbers: Sequence[int] | None = None,
    sample_count: int = DEFAULT_SAMPLE_COUNT,
) -> list[int]:
    """Keep requested frames, or select evenly spaced frame indexes.

    Supplied frame indexes are deduplicated while preserving their supplied
    order.  Uniform sampling includes the first and last known frame.
    """

    stream = _video_stream(media)
    if frame_numbers is not None:
        selected: list[int] = []
        seen: set[int] = set()
        known_frame_count = stream.get("frame_count")
        limit = known_frame_count if isinstance(known_frame_count, int) and known_frame_count > 0 else None
        for item in frame_numbers:
            frame = _as_nonnegative_int(item, "frame_numbers item")
            if limit is not None and frame >= limit:
                raise rrv_runtime.RRVError(
                    rrv_runtime.ERR_INVALID_ARGUMENT,
                    f"requested frame {frame} is outside the known video frame range",
                    {"frame_count": limit},
                )
            if frame not in seen:
                selected.append(frame)
                seen.add(frame)
        if not selected:
            raise rrv_runtime.RRVError(
                rrv_runtime.ERR_INVALID_ARGUMENT, "frame_numbers must not be empty when supplied"
            )
        if len(selected) > MAX_SAMPLE_COUNT:
            raise rrv_runtime.RRVError(
                rrv_runtime.ERR_INVALID_ARGUMENT,
                f"at most {MAX_SAMPLE_COUNT} requested frames are allowed",
            )
        return selected

    count = _as_sample_count(sample_count)
    rate = _frame_rate(stream)
    duration = _duration_seconds(media, stream)
    if rate is None or duration is None:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_CAPABILITY_UNAVAILABLE,
            "uniform sampling requires a positive video duration and frame rate",
            {"capability": "uniform_frame_sampling"},
        )
    raw_frame_count = stream.get("frame_count")
    total_frames = raw_frame_count if isinstance(raw_frame_count, int) and raw_frame_count > 0 else int(round(duration * rate))
    if total_frames <= 0:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_CAPABILITY_UNAVAILABLE,
            "uniform sampling could not determine a positive video frame count",
            {"capability": "uniform_frame_sampling"},
        )
    if count == 1 or total_frames == 1:
        return [0]
    # Integer rounding keeps the sample positions stable across platforms.
    selected = [round(index * (total_frames - 1) / (count - 1)) for index in range(count)]
    return list(dict.fromkeys(selected))


def build_frame_extraction_command(
    source: str | os.PathLike[str],
    ffmpeg: str | os.PathLike[str],
    frame_number: int,
    output: str | os.PathLike[str],
) -> list[str]:
    """Build an exact frame-index extraction command without shell quoting."""

    frame = _as_nonnegative_int(frame_number, "frame_number")
    output_path = _absolute_output_path(output)
    return [
        os.fspath(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(rrv_runtime.require_source_file(source)),
        "-vf",
        f"select=eq(n\\,{frame})",
        "-frames:v",
        "1",
        "-an",
        "-sn",
        "-dn",
        "-update",
        "1",
        "-n",
        str(output_path),
    ]


def build_audio_extraction_command(
    source: str | os.PathLike[str],
    ffmpeg: str | os.PathLike[str],
    output: str | os.PathLike[str],
) -> list[str]:
    """Build a stream-copy command for the first source audio stream."""

    output_path = _absolute_output_path(output)
    return [
        os.fspath(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(rrv_runtime.require_source_file(source)),
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-c:a",
        "copy",
        "-f",
        "matroska",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-n",
        str(output_path),
    ]


def _write_json_new(path: Path, payload: Mapping[str, Any]) -> None:
    """Create (never replace) a deterministic JSON artifact."""

    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(rrv_runtime.stable_json_dumps(payload))
            handle.write("\n")
    except FileExistsError as exc:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_OUTPUT_EXISTS, "refusing to overwrite an existing output"
        ) from exc
    except OSError as exc:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_TOOL_EXECUTION,
            "could not write survey JSON",
            {"reason": str(exc)[:rrv_runtime.MAX_ERROR_TEXT_LENGTH]},
        ) from exc


def _load_pillow() -> tuple[Any, Any]:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_CAPABILITY_UNAVAILABLE,
            "contact sheets require the optional Pillow dependency",
            {"capability": "contact_sheet", "dependency": "Pillow"},
        ) from exc
    return Image, ImageDraw


def create_contact_sheet(
    frames: Iterable[tuple[int, Path]],
    output: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str],
    columns: int = 4,
    cell_width: int = 320,
    cell_height: int = 220,
) -> dict[str, int]:
    """Compose extracted frames into a labeled JPEG inside ``project_root``."""

    if isinstance(columns, bool) or not isinstance(columns, int) or columns < 1:
        raise rrv_runtime.RRVError(rrv_runtime.ERR_INVALID_ARGUMENT, "columns must be a positive integer")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 32
        for value in (cell_width, cell_height)
    ):
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_INVALID_ARGUMENT, "contact sheet cells must be integer dimensions of at least 32px"
        )
    frame_items = list(frames)
    if not frame_items:
        raise rrv_runtime.RRVError(rrv_runtime.ERR_INVALID_ARGUMENT, "contact sheet needs at least one frame")
    output_path = rrv_runtime.resolve_output_path(
        project_root, output, create_parent=True, must_not_exist=True
    )
    Image, ImageDraw = _load_pillow()
    rows = math.ceil(len(frame_items) / columns)
    label_height = 24
    canvas = Image.new("RGB", (columns * cell_width, rows * cell_height), "white")
    draw = ImageDraw.Draw(canvas)
    resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
    try:
        for index, (frame_number, frame_path) in enumerate(frame_items):
            if not isinstance(frame_number, int) or isinstance(frame_number, bool):
                raise rrv_runtime.RRVError(
                    rrv_runtime.ERR_INVALID_ARGUMENT, "contact sheet frame numbers must be integers"
                )
            column, row = index % columns, index // columns
            x, y = column * cell_width, row * cell_height
            with Image.open(frame_path) as source_image:
                image = source_image.convert("RGB")
                image.thumbnail((cell_width - 8, cell_height - label_height - 8), resampling)
                paste_x = x + (cell_width - image.width) // 2
                paste_y = y + label_height + (cell_height - label_height - image.height) // 2
                canvas.paste(image, (paste_x, paste_y))
                image.close()
            draw.rectangle((x, y, x + cell_width, y + label_height), fill="black")
            draw.text((x + 6, y + 5), f"frame {frame_number}", fill="white")
        with output_path.open("xb") as handle:
            canvas.save(handle, format="JPEG", quality=90, optimize=False)
    except FileExistsError as exc:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_OUTPUT_EXISTS, "refusing to overwrite an existing output"
        ) from exc
    except rrv_runtime.RRVError:
        raise
    except OSError as exc:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_TOOL_EXECUTION,
            "could not create contact sheet",
            {"reason": str(exc)[:rrv_runtime.MAX_ERROR_TEXT_LENGTH]},
        ) from exc
    finally:
        canvas.close()
    return {"columns": columns, "rows": rows, "width": columns * cell_width, "height": rows * cell_height}


def _require_ffmpeg(tools: rrv_runtime.RuntimeTools) -> str:
    if tools.ffmpeg.path:
        return tools.ffmpeg.path
    raise rrv_runtime.RRVError(
        rrv_runtime.ERR_CAPABILITY_UNAVAILABLE,
        "reference surveys require ffmpeg for frame and audio extraction",
        {"capability": "reference_survey", "missing_tool": "ffmpeg"},
    )


def _new_survey_directory(project_root: Path, output_dir: str | os.PathLike[str]) -> Path:
    destination = rrv_runtime.resolve_output_path(project_root, output_dir, create_parent=True)
    try:
        destination.mkdir()
    except FileExistsError as exc:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_OUTPUT_EXISTS,
            "refusing to overwrite an existing survey directory",
        ) from exc
    except OSError as exc:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_TOOL_EXECUTION,
            "could not create survey output directory",
            {"reason": str(exc)[:rrv_runtime.MAX_ERROR_TEXT_LENGTH]},
        ) from exc
    # Re-resolve after creation to catch a symlink swap or reparse-point escape.
    return rrv_runtime.resolve_output_path(project_root, destination)


def _output_file(project_root: Path, survey_dir: Path, name: str) -> Path:
    return rrv_runtime.resolve_output_path(project_root, survey_dir / name, must_not_exist=True)


def _frame_timestamp(frame_number: int, media: Mapping[str, Any]) -> float | None:
    stream = _video_stream(media)
    rate = _frame_rate(stream)
    if rate is None:
        return None
    return frame_number / rate


def _run_extraction(command: Sequence[str], output: Path, timeout_seconds: float, label: str) -> None:
    try:
        rrv_runtime.run_command(command, timeout_seconds=timeout_seconds, check=True)
    except rrv_runtime.RRVError as exc:
        raise rrv_runtime.RRVError(
            exc.code,
            f"{label} failed",
            {"cause_code": exc.code, **exc.details},
        ) from exc
    if not output.is_file():
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_TOOL_EXECUTION,
            f"{label} did not create its expected output",
        )


def survey_reference(
    source: str | os.PathLike[str],
    project_root: str | os.PathLike[str],
    *,
    output_dir: str | os.PathLike[str] = "reference-survey",
    frame_numbers: Sequence[int] | None = None,
    sample_count: int = DEFAULT_SAMPLE_COUNT,
    include_contact_sheet: bool = True,
    include_audio: bool = True,
    contact_sheet_columns: int = 4,
    tools: rrv_runtime.RuntimeTools | None = None,
    ffmpeg: str | os.PathLike[str] | None = None,
    ffprobe: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: float = rrv_runtime.DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Create a bounded, local-only survey inside ``project_root``.

    ``source`` may be outside the project root and is used only as an input to
    hashing/probing/FFmpeg reads.  Every created file is checked against the
    supplied root before it is made, and existing artifacts are never replaced.
    """

    source_path = rrv_runtime.require_source_file(source)
    root = rrv_runtime.require_project_root(project_root)
    timeout = rrv_runtime.validate_timeout(timeout_seconds)
    runtime_tools = tools or rrv_runtime.discover_tools(
        ffmpeg=ffmpeg, ffprobe=ffprobe, environment=environment
    )
    ffmpeg_path = _require_ffmpeg(runtime_tools)
    # Fail before generating frames if the requested composed artifact cannot
    # be made in this environment.
    if include_contact_sheet:
        _load_pillow()
    source_summary = {
        "name": source_path.name,
        "size_bytes": source_path.stat().st_size,
        "sha256": sha256_file(source_path),
    }
    probe_result = rrv_runtime.probe_media(
        source_path, tools=runtime_tools, timeout_seconds=timeout
    )
    media = probe_result["media"]
    if not isinstance(media, Mapping):  # Defensive guard for future adapters.
        raise rrv_runtime.RRVError(rrv_runtime.ERR_PROBE_FAILED, "probe returned invalid media metadata")
    selected_frames = choose_frame_numbers(
        media, frame_numbers=frame_numbers, sample_count=sample_count
    )
    survey_dir = _new_survey_directory(root, output_dir)
    frames_dir = rrv_runtime.resolve_output_path(root, survey_dir / "frames", create_parent=True)
    try:
        frames_dir.mkdir()
    except FileExistsError as exc:  # pragma: no cover - survey_dir is new unless raced.
        raise rrv_runtime.RRVError(rrv_runtime.ERR_OUTPUT_EXISTS, "frames output directory already exists") from exc

    media_json_path = _output_file(root, survey_dir, "media.json")
    frame_records: list[dict[str, Any]] = []
    contact_items: list[tuple[int, Path]] = []
    for order, frame_number in enumerate(selected_frames, start=1):
        frame_path = _output_file(root, frames_dir, f"frame-{order:03d}-n{frame_number}.png")
        command = build_frame_extraction_command(source_path, ffmpeg_path, frame_number, frame_path)
        _run_extraction(command, frame_path, timeout, f"frame extraction for frame {frame_number}")
        record: dict[str, Any] = {
            "frame_number": frame_number,
            "path": rrv_runtime.relative_output_path(root, frame_path),
            "timestamp_seconds": _frame_timestamp(frame_number, media),
        }
        frame_records.append(record)
        contact_items.append((frame_number, frame_path))

    contact_sheet: dict[str, Any] | None = None
    if include_contact_sheet:
        contact_path = _output_file(root, survey_dir, "contact-sheet.jpg")
        contact_sheet = {
            "path": rrv_runtime.relative_output_path(root, contact_path),
            **create_contact_sheet(
                contact_items,
                contact_path,
                project_root=root,
                columns=contact_sheet_columns,
            ),
        }

    audio: dict[str, Any]
    audio_stream = rrv_runtime.first_stream(media, "audio")
    if include_audio and audio_stream is not None:
        audio_path = _output_file(root, survey_dir, "audio-original.mka")
        _run_extraction(
            build_audio_extraction_command(source_path, ffmpeg_path, audio_path),
            audio_path,
            timeout,
            "audio stream-copy extraction",
        )
        audio = {
            "status": "extracted",
            "mode": "stream-copy",
            "source_stream_index": audio_stream.get("index"),
            "path": rrv_runtime.relative_output_path(root, audio_path),
            "media_type": "audio/x-matroska",
            "container": "matroska",
            "metadata_stripped": True,
        }
    elif include_audio:
        audio = {"status": "not_available", "reason": "source_has_no_audio_stream"}
    else:
        audio = {"status": "not_requested"}

    media_artifact = {
        "schema_version": rrv_runtime.JSON_SCHEMA_VERSION,
        "source": source_summary,
        "probe": probe_result["probe"],
        "media": media,
    }
    _write_json_new(media_json_path, media_artifact)

    survey_json_path = _output_file(root, survey_dir, "survey.json")
    result: dict[str, Any] = {
        "source": source_summary,
        "probe": probe_result["probe"],
        "media": media,
        "frames": frame_records,
        "contact_sheet": contact_sheet,
        "audio": audio,
        "artifacts": {
            "media_json": rrv_runtime.relative_output_path(root, media_json_path),
            "survey_json": rrv_runtime.relative_output_path(root, survey_json_path),
        },
    }
    _write_json_new(survey_json_path, result)
    return result


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # pragma: no cover - argparse formats each Python version differently.
        raise rrv_runtime.RRVError(rrv_runtime.ERR_INVALID_ARGUMENT, message)


def build_parser() -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(prog="rrv-analyze")
    subparsers = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--ffmpeg", type=Path, help="Explicit user-installed ffmpeg executable")
    common.add_argument("--ffprobe", type=Path, help="Explicit user-installed ffprobe executable")
    common.add_argument("--timeout", type=float, default=rrv_runtime.DEFAULT_TIMEOUT_SECONDS)
    common.add_argument("--json", action="store_true", help="Retained for script compatibility; output is always JSON")

    doctor = subparsers.add_parser("doctor", parents=[common], help="Discover local media tools")
    doctor.set_defaults(command="doctor")
    probe = subparsers.add_parser("probe", parents=[common], help="Probe one media source")
    probe.add_argument("source", type=Path)
    survey = subparsers.add_parser("survey", parents=[common], help="Create a bounded local reference survey")
    survey.add_argument("source", type=Path)
    survey.add_argument("--project-root", type=Path, required=True)
    survey.add_argument("--output-dir", type=Path, default=Path("reference-survey"))
    survey.add_argument("--frame", dest="frame_numbers", type=int, action="append")
    survey.add_argument("--samples", dest="sample_count", type=int, default=DEFAULT_SAMPLE_COUNT)
    survey.add_argument("--no-contact-sheet", dest="include_contact_sheet", action="store_false")
    survey.add_argument("--no-audio", dest="include_audio", action="store_false")
    survey.add_argument("--contact-sheet-columns", type=int, default=4)
    return parser


def _doctor_result(args: argparse.Namespace) -> dict[str, Any]:
    tools = rrv_runtime.discover_tools(
        ffmpeg=args.ffmpeg, ffprobe=args.ffprobe, probe_versions=True
    )
    return {
        "tools": tools.to_dict(),
        "capabilities": {
            "media_probe": bool(tools.ffprobe.path or tools.ffmpeg.path),
            "structured_media_probe": bool(tools.ffprobe.path),
            "minimal_media_probe_fallback": bool(not tools.ffprobe.path and tools.ffmpeg.path),
            "reference_survey": bool(tools.ffmpeg.path),
            "contact_sheet": _pillow_available(),
            "original_audio_stream_copy": bool(tools.ffmpeg.path),
        },
    }


def _pillow_available() -> bool:
    try:
        _load_pillow()
    except rrv_runtime.RRVError:
        return False
    return True


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.command == "doctor":
            result = _doctor_result(args)
        elif args.command == "probe":
            result = rrv_runtime.probe_media(
                args.source, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe, timeout_seconds=args.timeout
            )
        else:
            result = survey_reference(
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
        payload = rrv_runtime.success_payload(result)
        status = 0
    except rrv_runtime.RRVError as exc:
        payload = rrv_runtime.error_payload(exc)
        status = 2
    print(rrv_runtime.stable_json_dumps(payload))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
