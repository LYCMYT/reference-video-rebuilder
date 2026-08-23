#!/usr/bin/env python3
"""Strict local asset-pack proposal and explicit asset-freeze workflow.

This module intentionally has a narrow boundary.  It inventories direct files
from a guarded project-local asset pack, makes only exact-filename candidate
links to Template IR slots, and publishes no reusable asset until a human has
explicitly approved every use.  It never uploads a file, reads EXIF/tags/OCR,
or exposes a source filename in a public return value or operational error.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
from typing import Any, BinaryIO, Iterator, Mapping, Sequence
import warnings

try:  # Direct execution from the Skill's scripts directory.
    import rrv_propose
    import rrv_runtime
    import video_remix
except ImportError:  # pragma: no cover - useful for package-style imports.
    from . import rrv_propose, rrv_runtime, video_remix  # type: ignore[no-redef]


SCHEMA_VERSION = "0.5.0"
SCANNER_POLICY_VERSION = "0.5.0"
DEFAULT_TIMEOUT_SECONDS = 60.0

MAX_ENTRIES = 128
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_IMAGE_PIXELS = 100_000_000
MAX_IMAGE_EDGE = 16_384
MAX_AUDIO_SECONDS = 600.0
MAX_PACKET_BYTES = 4 * 1024 * 1024
MAX_CONTACT_SHEET_BYTES = 128 * 1024 * 1024
MAX_TIMEOUT_SECONDS = 60.0

_SKILL_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_DIRECTORY = _SKILL_ROOT / "assets" / "schemas"
_PROPOSAL_SCHEMA_PATH = _SCHEMA_DIRECTORY / "asset-pack-proposal.schema.json"
_REVIEW_SCHEMA_PATH = _SCHEMA_DIRECTORY / "asset-mapping-review.schema.json"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ASSET_ID_RE = re.compile(r"^asset\.(\d{4})$")
_PROCESSOR_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_IMAGE_MEDIA_BY_FORMAT = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}
_CANONICAL_EXTENSION = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "audio/wav": "wav",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/x-matroska": "mka",
}
_ALLOWED_MEDIA = frozenset(_CANONICAL_EXTENSION)
_M4A_FORMAT_NAMES = frozenset({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"})
_EBML_HEADER_ID = b"\x1a\x45\xdf\xa3"
_EBML_DOC_TYPE_ID = b"\x42\x82"
_MAX_EBML_HEADER_BYTES = 4096
_DECLARED_MEDIA = frozenset(
    {
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
)
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
# Keep every path component portable to a Windows project root.  The public
# packets use POSIX separators even on Windows, but those components are later
# materialized through Windows filesystem APIs.  A lexical check here prevents
# a valid POSIX spelling from silently aliasing a device or normalized Win32
# entry on another host.
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
# Test-only seam used to exercise the guarded parent-directory race boundary.
# Production leaves it as ``None`` and never accepts it as a public argument.
_PROJECT_SNAPSHOT_HOOK: Any = None


@dataclass(frozen=True)
class _FileIdentity:
    """A direct regular-file entry and the identity its descriptor must match."""

    path: Path
    device: int
    inode: int
    size_bytes: int


@dataclass(frozen=True)
class _JsonSnapshot:
    """One strict JSON byte snapshot used for parsing and exact hashing."""

    data: Any
    raw: bytes
    sha256: str
    relative_path: str


@dataclass
class _ScannedAsset:
    """Private immutable-byte scanner state; public inventory omits ``name``."""

    name: str
    identity: _FileIdentity
    sha256: str
    media_type: str
    facts: dict[str, Any]
    snapshot: BinaryIO
    closed: bool = False

    def close(self) -> None:
        """Close the private byte snapshot exactly once on every terminal path."""

        if self.closed:
            return
        self.closed = True
        try:
            self.snapshot.close()
        except OSError:
            pass


def _invalid(message: str, details: Mapping[str, Any] | None = None) -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(rrv_runtime.ERR_INVALID_ARGUMENT, message, details)


def _project_root_invalid() -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(
        rrv_runtime.ERR_PROJECT_ROOT_INVALID,
        "project_root must be an existing safe directory",
    )


def _tool_error(message: str) -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(rrv_runtime.ERR_TOOL_EXECUTION, message)


def _probe_error(message: str) -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(rrv_runtime.ERR_PROBE_FAILED, message)


def _is_link_or_reparse(stat_result: os.stat_result) -> bool:
    attributes = getattr(stat_result, "st_file_attributes", 0)
    return stat.S_ISLNK(stat_result.st_mode) or (
        isinstance(attributes, int) and bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)
    )


def _strict_json_constant(_: str) -> None:
    raise ValueError("non-finite JSON value is not allowed")


def _reject_duplicate_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _canonical_json_bytes(value: Any) -> bytes:
    """The one canonical encoding used for inventory binding."""

    return rrv_runtime.stable_json_dumps(value, indent=None).encode("utf-8")


def _canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _find_nonfinite(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        errors.append(f"{path}: finite_number")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _find_nonfinite(item, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _find_nonfinite(item, f"{path}[{index}]", errors)


def _schema_error_path(error: Any) -> str:
    path = "$"
    for part in error.absolute_path:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def _schema_errors(data: Any, schema_path: Path, contract_name: str) -> list[str]:
    """Return bounded shape errors without quoting a private packet value."""

    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return [f"{contract_name}: validation_unavailable"]
    try:
        with schema_path.open("r", encoding="utf-8") as handle:
            schema = json.load(handle, parse_constant=_strict_json_constant)
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
    except (OSError, ValueError):
        return [f"{contract_name}: validation_unavailable"]
    errors = sorted(
        validator.iter_errors(data),
        key=lambda item: (tuple(str(part) for part in item.absolute_path), item.validator),
    )
    return [f"{_schema_error_path(item)}: schema.{item.validator or 'invalid'}" for item in errors]


def _unique_errors(errors: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in errors:
        compact = " ".join(str(item).split())[:360]
        if compact and compact not in seen:
            seen.add(compact)
            result.append(compact)
        if len(result) >= 64:
            break
    return result


def _relative_path_parts(value: Any) -> tuple[str, ...] | None:
    """Recognize a literal, normalized, project-root-relative POSIX path."""

    if isinstance(value, Path):
        raw = value.as_posix()
    else:
        try:
            raw = os.fspath(value)
        except TypeError:
            return None
    if not isinstance(raw, str) or not raw or len(raw) > 512 or "\x00" in raw:
        return None
    # Backslashes, drive/ADS colons and repeated/relative components are all
    # rejected lexically before any filesystem operation.
    if "\\" in raw or ":" in raw or raw.startswith("/") or raw.startswith("//"):
        return None
    parts = tuple(raw.split("/"))
    if not parts or any(not part or part in {".", ".."} for part in parts):
        return None
    if raw != "/".join(parts):
        return None
    if any(not _portable_path_component(part) for part in parts):
        return None
    return parts


def _portable_path_component(component: str) -> bool:
    """Return whether one lexical POSIX component is safe on Windows too."""

    if not component or component.endswith((".", " ")):
        return False
    if any(
        ord(character) < 32
        or 0x7F <= ord(character) <= 0x9F
        or character in _WIN32_INVALID_COMPONENT_CHARACTERS
        for character in component
    ):
        return False
    # Windows reserves these device names regardless of extension.  Strip the
    # part Win32 would normalize just before an extension as well, so names
    # such as ``CON .png`` cannot become an alias on publication.
    stem = component.split(".", 1)[0].rstrip(" .").upper()
    return stem not in _WIN32_RESERVED_DEVICE_STEMS


def _direct_child_name(value: Any, label: str) -> str:
    parts = _relative_path_parts(value)
    if parts is None or len(parts) != 1 or len(parts[0]) > 128:
        raise _invalid(f"{label} must be a direct child of project_root")
    return parts[0]


def _valid_relative_path(value: Any) -> bool:
    return _relative_path_parts(value) is not None


def _safe_project_root(value: str | os.PathLike[str]) -> Path:
    """Bind an existing root without resolving through a link or junction."""

    try:
        raw = os.fspath(value)
    except TypeError as exc:
        raise _project_root_invalid() from exc
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise _project_root_invalid()
    try:
        root = Path(os.path.abspath(raw))
    except (OSError, ValueError, RuntimeError) as exc:
        raise _project_root_invalid() from exc
    # UNC paths are never a safe local project-root boundary for this workflow.
    if str(root.drive).startswith("\\\\"):
        raise _project_root_invalid()
    try:
        anchor = Path(root.anchor)
        if not root.anchor or not stat.S_ISDIR(os.lstat(anchor).st_mode):
            raise OSError("unsafe root anchor")
        current = anchor
        for component in root.parts[1:]:
            current = current / component
            entry = os.lstat(current)
            if _is_link_or_reparse(entry) or not stat.S_ISDIR(entry.st_mode):
                raise OSError("unsafe root component")
        rrv_propose._capture_directory_identity(root, "project root")
    except (OSError, rrv_runtime.RRVError):
        raise _project_root_invalid() from None
    return root


@contextmanager
def _root_guard(root: Path) -> Iterator[rrv_propose._DirectoryIdentity]:
    """Keep the project root non-deletable for the complete operation."""

    guard: rrv_propose._DirectoryGuard | None = None
    try:
        guard = rrv_propose._open_directory_guard(root, "project root", allow_rename=False)
        identity = rrv_propose._capture_directory_identity(root, "project root")
        yield identity
    finally:
        if guard is not None:
            rrv_propose._close_directory_guard(guard)


def _assert_root_live(root_identity: rrv_propose._DirectoryIdentity) -> None:
    rrv_propose._assert_directory_identity(root_identity, "project root")


@contextmanager
def _asset_pack_guard(
    root: Path,
    root_identity: rrv_propose._DirectoryIdentity,
    asset_pack: str,
) -> Iterator[tuple[Path, rrv_propose._DirectoryIdentity]]:
    """Open a literal direct-child pack and keep it non-deletable until exit."""

    guard: rrv_propose._DirectoryGuard | None = None
    _assert_root_live(root_identity)
    pack = root / asset_pack
    try:
        entry = os.lstat(pack)
        if _is_link_or_reparse(entry) or not stat.S_ISDIR(entry.st_mode):
            raise OSError("unsafe asset pack")
        guard = rrv_propose._open_directory_guard(pack, "asset pack", allow_rename=False)
        identity = rrv_propose._capture_directory_identity(pack, "asset pack")
        _assert_root_live(root_identity)
        yield pack, identity
    except rrv_runtime.RRVError:
        raise
    except OSError as exc:
        raise _invalid("asset_pack must name an existing safe directory") from exc
    finally:
        if guard is not None:
            rrv_propose._close_directory_guard(guard)


def _assert_pack_live(
    root_identity: rrv_propose._DirectoryIdentity,
    pack_identity: rrv_propose._DirectoryIdentity,
) -> None:
    _assert_root_live(root_identity)
    rrv_propose._assert_directory_identity(pack_identity, "asset pack")


def _safe_regular_file(path: Path, *, message: str) -> _FileIdentity:
    try:
        entry = os.lstat(path)
    except OSError as exc:
        raise _invalid(message) from exc
    if (
        _is_link_or_reparse(entry)
        or not stat.S_ISREG(entry.st_mode)
        or not isinstance(entry.st_ino, int)
        or entry.st_ino == 0
        or not isinstance(entry.st_nlink, int)
        or entry.st_nlink != 1
    ):
        raise _invalid(message)
    return _FileIdentity(
        path=path,
        device=int(entry.st_dev),
        inode=int(entry.st_ino),
        size_bytes=int(entry.st_size),
    )


@contextmanager
def _open_bound_file(identity: _FileIdentity, *, message: str) -> Iterator[BinaryIO]:
    """Use lstat -> O_NOFOLLOW open -> fstat identity for every media read."""

    current = _safe_regular_file(identity.path, message=message)
    if (
        current.device != identity.device
        or current.inode != identity.inode
        or current.size_bytes != identity.size_bytes
    ):
        raise _invalid(message)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(identity.path, flags | nofollow)
        except OSError as exc:
            # CPython's Windows CRT may not expose/support O_NOFOLLOW.  The
            # descriptor identity check below still rejects a swapped entry.
            if nofollow and getattr(exc, "errno", None) in {22, 95}:
                descriptor = os.open(identity.path, flags)
            else:
                raise
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or int(opened.st_dev) != identity.device
            or int(opened.st_ino) != identity.inode
            or int(opened.st_nlink) != 1
        ):
            raise _invalid(message)
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = None
            yield handle
    except rrv_runtime.RRVError:
        raise
    except OSError as exc:
        raise _invalid(message) from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _read_bound_bytes(identity: _FileIdentity, *, maximum_bytes: int, message: str) -> bytes:
    try:
        with _open_bound_file(identity, message=message) as handle:
            data = bytearray()
            while True:
                chunk = handle.read(min(1024 * 1024, maximum_bytes + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > maximum_bytes:
                    raise _invalid(message)
            if len(data) != identity.size_bytes:
                raise _invalid(message)
            return bytes(data)
    except rrv_runtime.RRVError:
        raise
    except OSError as exc:
        raise _invalid(message) from exc


@contextmanager
def _guard_project_parent_chain(
    root: Path,
    root_identity: rrv_propose._DirectoryIdentity,
    parts: Sequence[str],
    *,
    label: str,
) -> Iterator[Path]:
    """Hold every nested parent against a junction swap during a file read."""

    guards: list[rrv_propose._DirectoryGuard] = []
    identities: list[rrv_propose._DirectoryIdentity] = []
    _assert_root_live(root_identity)
    current = root
    try:
        for part in parts[:-1]:
            current = current / part
            entry = os.lstat(current)
            if _is_link_or_reparse(entry) or not stat.S_ISDIR(entry.st_mode):
                raise OSError("unsafe parent")
            guard = rrv_propose._open_directory_guard(
                current,
                "project-contained artifact directory",
                allow_rename=False,
            )
            guards.append(guard)
            identity = rrv_propose._capture_directory_identity(
                current,
                "project-contained artifact directory",
            )
            identities.append(identity)
            _assert_root_live(root_identity)
            for parent_identity in identities:
                rrv_propose._assert_directory_identity(
                    parent_identity,
                    "project-contained artifact directory",
                )
        hook = _PROJECT_SNAPSHOT_HOOK
        if callable(hook):
            hook()
        _assert_root_live(root_identity)
        for parent_identity in identities:
            rrv_propose._assert_directory_identity(
                parent_identity,
                "project-contained artifact directory",
            )
        yield current
    except rrv_runtime.RRVError:
        raise
    except OSError as exc:
        raise _invalid(f"{label} must name an existing safe file") from exc
    finally:
        # Child first: a parent guard remains live until every child descriptor
        # and child directory guard has been released.
        for guard in reversed(guards):
            rrv_propose._close_directory_guard(guard)


def _read_project_file_bytes(
    root: Path,
    root_identity: rrv_propose._DirectoryIdentity,
    value: Any,
    *,
    label: str,
    maximum_bytes: int,
) -> tuple[str, bytes]:
    parts = _relative_path_parts(value)
    if parts is None:
        raise _invalid(f"{label} must be a normalized relative file path")
    with _guard_project_parent_chain(root, root_identity, parts, label=label) as parent:
        identity = _safe_regular_file(parent / parts[-1], message=f"{label} must name an existing safe file")
        if identity.size_bytes > maximum_bytes:
            raise _invalid(f"{label} exceeds the bounded local packet size")
        raw = _read_bound_bytes(
            identity,
            maximum_bytes=maximum_bytes,
            message=f"{label} could not be read safely",
        )
        _assert_root_live(root_identity)
        return "/".join(parts), raw


def _read_project_json_snapshot(
    root: Path,
    root_identity: rrv_propose._DirectoryIdentity,
    value: Any,
    *,
    label: str,
) -> _JsonSnapshot:
    relative_path, raw = _read_project_file_bytes(
        root,
        root_identity,
        value,
        label=label,
        maximum_bytes=MAX_PACKET_BYTES,
    )
    try:
        data = json.loads(
            raw.decode("utf-8"),
            parse_constant=_strict_json_constant,
            object_pairs_hook=_reject_duplicate_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _invalid(f"{label} is not valid strict JSON") from exc
    return _JsonSnapshot(
        data=data,
        raw=raw,
        sha256=hashlib.sha256(raw).hexdigest(),
        relative_path=relative_path,
    )


def _validate_template_snapshot(snapshot: _JsonSnapshot) -> Mapping[str, Any]:
    if not isinstance(snapshot.data, Mapping):
        raise _invalid("template did not pass validation")
    try:
        errors = video_remix.validate_template_data(snapshot.data)
    except Exception as exc:  # Never surface implementation/parser internals.
        raise _invalid("template did not pass validation") from exc
    if errors:
        raise _invalid("template did not pass validation")
    return snapshot.data


def _parse_timeout(timeout_seconds: Any) -> float:
    try:
        timeout = rrv_runtime.validate_timeout(timeout_seconds)
    except rrv_runtime.RRVError as exc:
        raise _invalid("timeout_seconds must be a positive number no greater than 60") from exc
    if timeout > MAX_TIMEOUT_SECONDS:
        raise _invalid("timeout_seconds must be a positive number no greater than 60")
    return timeout


def _snapshot_bound_asset(identity: _FileIdentity) -> tuple[BinaryIO, str]:
    """Read one source descriptor once into a private immutable-byte snapshot."""

    digest = hashlib.sha256()
    total = 0
    snapshot = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    try:
        with _open_bound_file(identity, message="asset pack file changed while scanning") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    raise _invalid("asset pack file exceeds the 256 MiB limit")
                snapshot.write(chunk)
                digest.update(chunk)
        if total != identity.size_bytes:
            raise _invalid("asset pack file changed while scanning")
        snapshot.seek(0)
        return snapshot, digest.hexdigest()
    except rrv_runtime.RRVError:
        snapshot.close()
        raise
    except OSError as exc:
        snapshot.close()
        raise _invalid("asset pack file could not be read safely") from exc
    except Exception:
        snapshot.close()
        raise


def _load_pillow() -> tuple[Any, Any]:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_CAPABILITY_UNAVAILABLE,
            "local asset scanning requires the Pillow dependency",
            {"capability": "asset_pack_images"},
        ) from exc
    return Image, ImageDraw


def _inspect_image(handle: BinaryIO) -> tuple[str, dict[str, Any]] | None:
    """Return static accepted image facts, deliberately never EXIF or OCR."""

    Image, _ = _load_pillow()
    try:
        handle.seek(0)
        with warnings.catch_warnings():
            warning_type = getattr(Image, "DecompressionBombWarning", Warning)
            warnings.simplefilter("ignore", warning_type)
            with Image.open(handle) as image:
                image_format = str(getattr(image, "format", "") or "").upper()
                media_type = _IMAGE_MEDIA_BY_FORMAT.get(image_format)
                if media_type is None:
                    return None
                frames = int(getattr(image, "n_frames", 1) or 1)
                if bool(getattr(image, "is_animated", False)) or frames != 1:
                    return None
                width, height = image.size
                if (
                    not isinstance(width, int)
                    or not isinstance(height, int)
                    or width < 1
                    or height < 1
                    or width > MAX_IMAGE_EDGE
                    or height > MAX_IMAGE_EDGE
                    or width * height > MAX_IMAGE_PIXELS
                ):
                    raise _invalid("image exceeds the bounded local dimension limit")
                # ``verify`` checks compressed bytes without asking Pillow for
                # EXIF, textual chunks, or pixel analysis output.
                image.verify()
        return media_type, {"kind": "image", "width": width, "height": height, "pixels": width * height}
    except rrv_runtime.RRVError:
        raise
    except Exception:
        # A non-image or a malformed image is simply not an accepted mapping.
        return None


def _run_ffprobe(command: Sequence[str], source_handle: BinaryIO, timeout_seconds: float) -> bytes | None:
    """Run ffprobe over ``pipe:0`` only; never hand it a replaceable pathname."""

    try:
        source_handle.seek(0)
        process = subprocess.Popen(
            list(command),
            shell=False,
            stdin=source_handle,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError as exc:
        raise rrv_runtime.RRVError(rrv_runtime.ERR_TOOL_NOT_FOUND, "local FFprobe was not found") from exc
    except OSError as exc:
        raise _tool_error("could not run local FFprobe") from exc
    try:
        stdout, _stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_TOOL_TIMEOUT,
            "local FFprobe exceeded the timeout",
            {"timeout_seconds": timeout_seconds},
        ) from exc
    # A decode failure is a normal "not an accepted audio input" result.  Do
    # not retain or expose ffprobe stderr, which can contain source paths.
    if process.returncode != 0:
        return None
    return stdout


def _strict_probe_json(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        raw = value.encode("utf-8")
    else:
        return None
    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            parse_constant=_strict_json_constant,
            object_pairs_hook=_reject_duplicate_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
    elif isinstance(value, str):
        try:
            numeric = float(value)
        except ValueError:
            return None
    else:
        return None
    return numeric if math.isfinite(numeric) else None


def _ebml_vint(data: bytes, offset: int) -> tuple[int, int] | None:
    """Read one bounded EBML variable-length integer from a header buffer."""

    if offset >= len(data):
        return None
    first = data[offset]
    marker = 0x80
    width = 1
    while width <= 8 and not first & marker:
        marker >>= 1
        width += 1
    if width > 8 or offset + width > len(data):
        return None
    value = first & (marker - 1)
    for byte in data[offset + 1 : offset + width]:
        value = (value << 8) | byte
    # EBML's all-ones value is an unknown length, which is not safe to use
    # while parsing a bounded structural header.
    if value == (1 << (7 * width)) - 1:
        return None
    return width, value


def _ebml_id_width(first: int) -> int | None:
    """Return the encoded EBML element-ID width without consuming payload data."""

    marker = 0x80
    width = 1
    while width <= 4 and not first & marker:
        marker >>= 1
        width += 1
    return width if width <= 4 else None


def _matroska_doc_type(handle: BinaryIO) -> str | None:
    """Read only the EBML structural header to distinguish MKA from WebM."""

    try:
        handle.seek(0)
        header = handle.read(_MAX_EBML_HEADER_BYTES)
        handle.seek(0)
    except (OSError, ValueError):
        return None
    if not isinstance(header, bytes) or not header.startswith(_EBML_HEADER_ID):
        return None
    header_size = _ebml_vint(header, len(_EBML_HEADER_ID))
    if header_size is None:
        return None
    size_width, payload_size = header_size
    position = len(_EBML_HEADER_ID) + size_width
    end = position + payload_size
    if end > len(header):
        return None
    while position < end:
        id_width = _ebml_id_width(header[position])
        if id_width is None or position + id_width > end:
            return None
        element_id = header[position : position + id_width]
        position += id_width
        element_size = _ebml_vint(header, position)
        if element_size is None:
            return None
        size_width, payload_size = element_size
        position += size_width
        payload_end = position + payload_size
        if payload_end > end:
            return None
        if element_id == _EBML_DOC_TYPE_ID:
            try:
                return header[position:payload_end].decode("ascii").lower()
            except UnicodeDecodeError:
                return None
        position = payload_end
    return None


def _audio_media_type(format_name: Any, handle: BinaryIO | None = None) -> str | None:
    """Map only the policy's audio containers, never a generic media family."""

    if not isinstance(format_name, str):
        return None
    names = {part.strip().lower() for part in format_name.split(",") if part.strip()}
    if names == {"wav"}:
        return "audio/wav"
    if names == {"mp3"}:
        return "audio/mpeg"
    if "m4a" in names and names <= _M4A_FORMAT_NAMES:
        return "audio/mp4"
    if names == {"matroska"}:
        return "audio/x-matroska"
    # FFprobe reports both Matroska and WebM through the same demuxer as
    # ``matroska,webm``.  Only accept that ambiguous label after a bounded
    # structural check proves the EBML DocType is Matroska, never WebM.
    if names == {"matroska", "webm"} and handle is not None and _matroska_doc_type(handle) == "matroska":
        return "audio/x-matroska"
    return None


def _inspect_audio(
    handle: BinaryIO,
    *,
    ffprobe: str | os.PathLike[str],
    timeout_seconds: float,
) -> tuple[str, dict[str, Any]] | None:
    try:
        executable = os.fspath(ffprobe)
    except TypeError as exc:
        raise _invalid("ffprobe must be an executable path or command name") from exc
    if not isinstance(executable, str) or not executable or "\x00" in executable:
        raise _invalid("ffprobe must be an executable path or command name")
    command = [
        executable,
        "-v",
        "error",
        "-show_entries",
        "format=format_name,duration:stream=codec_type",
        "-of",
        "json",
        "-i",
        "pipe:0",
    ]
    raw_probe = _run_ffprobe(command, handle, timeout_seconds)
    if raw_probe is None:
        return None
    probe = _strict_probe_json(raw_probe)
    if probe is None:
        raise _probe_error("local FFprobe returned invalid probe data")
    streams = probe.get("streams")
    format_data = probe.get("format")
    if not isinstance(streams, list) or not isinstance(format_data, Mapping):
        return None
    if not streams or any(not isinstance(item, Mapping) or item.get("codec_type") != "audio" for item in streams):
        return None
    audio_streams = list(streams)
    media_type = _audio_media_type(format_data.get("format_name"), handle)
    duration = _number(format_data.get("duration"))
    if (
        media_type is None
        or not audio_streams
        or len(audio_streams) > 64
        or duration is None
        or duration <= 0
        or duration > MAX_AUDIO_SECONDS
    ):
        return None
    # Round only the public technical duration, never preserve ffprobe's raw
    # textual value or its container/stream metadata.
    stable_duration = round(duration, 6)
    if stable_duration <= 0 or stable_duration > MAX_AUDIO_SECONDS:
        return None
    return media_type, {
        "kind": "audio",
        "duration_seconds": stable_duration,
        "audio_stream_count": len(audio_streams),
        "video_stream_count": 0,
    }


def _inspect_asset(
    identity: _FileIdentity,
    *,
    ffprobe: str | os.PathLike[str],
    timeout_seconds: float,
) -> tuple[str, dict[str, Any], str, BinaryIO] | None:
    """Classify only a private snapshot made from one bound source descriptor."""

    snapshot, digest = _snapshot_bound_asset(identity)
    try:
        image = _inspect_image(snapshot)
        if image is not None:
            media_type, facts = image
            return media_type, facts, digest, snapshot
        audio = _inspect_audio(snapshot, ffprobe=ffprobe, timeout_seconds=timeout_seconds)
        if audio is not None:
            media_type, facts = audio
            return media_type, facts, digest, snapshot
        snapshot.close()
        return None
    except Exception:
        snapshot.close()
        raise


def _scanned_source_path(asset_pack: str, name: str) -> str:
    # Both components were obtained from lexical checks, never ``resolve``.
    return f"{asset_pack}/{name}"


def _scan_asset_pack(
    root_identity: rrv_propose._DirectoryIdentity,
    pack: Path,
    pack_identity: rrv_propose._DirectoryIdentity,
    asset_pack: str,
    *,
    ffprobe: str | os.PathLike[str],
    timeout_seconds: float,
) -> tuple[list[_ScannedAsset], list[dict[str, Any]]]:
    """Scan only direct regular files and build deterministic accepted inventory."""

    _assert_pack_live(root_identity, pack_identity)
    try:
        with os.scandir(pack) as scanner:
            names = [entry.name for entry in scanner]
    except OSError as exc:
        raise _invalid("asset_pack could not be enumerated safely") from exc
    if len(names) > MAX_ENTRIES:
        raise _invalid("asset pack exceeds the 128-entry limit")
    if len(set(names)) != len(names):  # Defensive against unusual filesystem adapters.
        raise _invalid("asset pack contains duplicate direct entries")
    # A case-sensitive source filesystem can hold two direct names that alias
    # to one Win32 entry when this local pack is moved or reopened on Windows.
    # Reject them before opening any pack entry or publishing an inventory.
    if len({name.casefold() for name in names}) != len(names):
        raise _invalid("asset pack contains colliding portable entry names")
    ordered_names = sorted(names)
    identities: list[tuple[str, _FileIdentity]] = []
    total_size = 0
    for name in ordered_names:
        if name in {".", ".."} or not _portable_path_component(name):
            raise _invalid("asset pack contains an unsafe entry")
        identity = _safe_regular_file(pack / name, message="asset pack contains an unsafe entry")
        if identity.size_bytes < 1:
            raise _invalid("asset pack contains an empty file")
        if identity.size_bytes > MAX_FILE_BYTES:
            raise _invalid("asset pack file exceeds the 256 MiB limit")
        total_size += identity.size_bytes
        if total_size > MAX_TOTAL_BYTES:
            raise _invalid("asset pack exceeds the 1 GiB total limit")
        identities.append((name, identity))
    _assert_pack_live(root_identity, pack_identity)

    scanned: list[_ScannedAsset] = []
    try:
        for name, identity in identities:
            _assert_pack_live(root_identity, pack_identity)
            classified = _inspect_asset(identity, ffprobe=ffprobe, timeout_seconds=timeout_seconds)
            if classified is None:
                # A direct ordinary file is part of the immutable pack boundary.
                # Unknown, animated, sidecar, video, and unsupported inputs must
                # fail the whole proposal rather than silently escape its hash.
                raise _invalid("asset pack contains unsupported media")
            media_type, facts, digest, snapshot = classified
            scanned.append(
                _ScannedAsset(
                    name=name,
                    identity=identity,
                    sha256=digest,
                    media_type=media_type,
                    facts=facts,
                    snapshot=snapshot,
                )
            )
        _assert_pack_live(root_identity, pack_identity)
        scanned.sort(key=lambda item: _scanned_source_path(asset_pack, item.name))
        inventory: list[dict[str, Any]] = []
        for index, item in enumerate(scanned, start=1):
            inventory.append(
                {
                    "asset_id": f"asset.{index:04d}",
                    "source_path": _scanned_source_path(asset_pack, item.name),
                    "sha256": item.sha256,
                    "size_bytes": item.identity.size_bytes,
                    "media_type": item.media_type,
                    "facts": dict(item.facts),
                }
            )
        return scanned, inventory
    except Exception:
        _close_scanned_assets(scanned)
        raise


def _close_scanned_assets(scanned: Sequence[_ScannedAsset]) -> None:
    """Release every private source snapshot; repeated calls are harmless."""

    for asset in scanned:
        asset.close()


def _representation_requirements(template: Mapping[str, Any]) -> dict[str, str]:
    requirements: dict[str, str] = {}
    slots = template.get("slots")
    if isinstance(slots, list):
        for slot in slots:
            if isinstance(slot, Mapping) and isinstance(slot.get("id"), str):
                requirements[slot["id"]] = "raw"
    layers = template.get("layers")
    if isinstance(layers, list):
        for layer in layers:
            source = layer.get("source") if isinstance(layer, Mapping) else None
            if not isinstance(source, Mapping):
                continue
            slot_id = source.get("slot_id")
            if isinstance(slot_id, str) and source.get("representation") == "render-ready":
                requirements[slot_id] = "render-ready"
    return requirements


def _slot_candidates(template: Mapping[str, Any], inventory: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Build candidates exclusively from exact ``Path.stem == slot_id`` matches."""

    requirements = _representation_requirements(template)
    candidate_slots: list[dict[str, Any]] = []
    raw_slots = template.get("slots")
    if not isinstance(raw_slots, list):  # Template validation already ruled this out.
        return candidate_slots
    for raw_slot in raw_slots:
        if not isinstance(raw_slot, Mapping):
            continue
        slot_id = raw_slot.get("id")
        accepted_media = raw_slot.get("accepted_media")
        if (
            not isinstance(slot_id, str)
            or not isinstance(accepted_media, list)
            or not isinstance(raw_slot.get("required"), bool)
            or not isinstance(raw_slot.get("type"), str)
        ):
            continue
        candidates: list[str] = []
        exact_assets: list[str] = []
        for item in inventory:
            source_path = item.get("source_path")
            media_type = item.get("media_type")
            asset_id = item.get("asset_id")
            if (
                isinstance(source_path, str)
                and isinstance(media_type, str)
                and isinstance(asset_id, str)
                and PurePosixPath(source_path).name
                and PurePosixPath(source_path).stem == slot_id
            ):
                exact_assets.append(asset_id)
                if media_type in accepted_media:
                    candidates.append(asset_id)
        candidates.sort()
        status = (
            "missing"
            if not candidates and not exact_assets
            else "incompatible"
            if not candidates
            else "suggested"
            if len(candidates) == 1
            else "ambiguous"
        )
        candidate_slots.append(
            {
                "slot_id": slot_id,
                "required": raw_slot["required"],
                "type": raw_slot["type"],
                "accepted_media": list(accepted_media),
                "representation_requirement": requirements.get(slot_id, "raw"),
                "status": status,
                "candidate_asset_ids": candidates,
            }
        )
    return sorted(candidate_slots, key=lambda item: str(item["slot_id"]))


def _review_template(proposal_sha256: str, slot_candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    mappings: list[dict[str, Any]] = []
    for candidate in sorted(slot_candidates, key=lambda item: str(item.get("slot_id", ""))):
        slot_id = candidate.get("slot_id")
        ids = candidate.get("candidate_asset_ids")
        required = candidate.get("required")
        status = candidate.get("status")
        if not isinstance(slot_id, str) or not isinstance(ids, list) or not isinstance(required, bool):
            raise _invalid("generated slot candidates are invalid")
        if status == "suggested" and len(ids) == 1 and isinstance(ids[0], str):
            representation = candidate.get("representation_requirement")
            slot_type = candidate.get("type")
            processor = (
                "approved-render-ready"
                if representation == "render-ready"
                else "identity-reference"
                if slot_type == "identity"
                else "deterministic-tile"
                if slot_type == "product-image"
                else "direct"
            )
            mappings.append(
                {
                    "slot_id": slot_id,
                    "action": "use",
                    "asset_id": ids[0],
                    "content_reviewed": False,
                    "media_compatibility_confirmed": False,
                    "render_ready_confirmed": False,
                    "rights_confirmed": False,
                    "processor": processor,
                }
            )
        elif not required and status == "missing":
            mappings.append({"slot_id": slot_id, "action": "omit", "omit_confirmed": False})
        else:
            mappings.append({"slot_id": slot_id, "action": "unresolved"})
    return {
        "schema_version": SCHEMA_VERSION,
        "proposal_sha256": proposal_sha256,
        "decision": "pending",
        "contact_sheet_reviewed": False,
        "local_only_confirmed": False,
        "mappings": mappings,
    }


def _asset_suggestion_labels(slot_candidates: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    labels: dict[str, list[str]] = {}
    for candidate in slot_candidates:
        slot_id = candidate.get("slot_id")
        asset_ids = candidate.get("candidate_asset_ids")
        status = candidate.get("status")
        if not isinstance(slot_id, str) or not isinstance(asset_ids, list):
            continue
        for asset_id in asset_ids:
            if isinstance(asset_id, str):
                labels.setdefault(asset_id, []).append(slot_id if status == "suggested" else "review")
    result: dict[str, str] = {}
    for asset_id, values in labels.items():
        unique = sorted(set(values))
        result[asset_id] = unique[0] if len(unique) == 1 else "review"
    return result


def _thumbnail_for_asset(asset: _ScannedAsset, *, maximum: tuple[int, int]) -> Any | None:
    if asset.facts.get("kind") != "image":
        return None
    Image, _ = _load_pillow()
    try:
        from PIL import ImageOps

        if asset.closed:
            raise _invalid("asset snapshot is no longer available")
        asset.snapshot.seek(0)
        with Image.open(asset.snapshot) as source:
            try:
                source.draft("RGB", maximum)
            except Exception:
                pass
            oriented = ImageOps.exif_transpose(source)
            try:
                converted = oriented.convert("RGB")
                try:
                    # Reconstruct pixels rather than returning a converted
                    # view: contact-sheet evidence must not inherit EXIF,
                    # XMP, ICC, text chunks, or arbitrary Pillow metadata.
                    image = Image.frombytes("RGB", converted.size, converted.tobytes())
                finally:
                    converted.close()
            finally:
                if oriented is not source:
                    oriented.close()
        try:
            resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
            image.thumbnail(maximum, resampling)
            return image
        except Exception:
            image.close()
            raise
    except rrv_runtime.RRVError:
        raise
    except Exception as exc:
        raise _invalid("asset pack file could not create static evidence") from exc


def _create_contact_sheet(
    root: Path,
    stage: rrv_propose._StageDirectory,
    output: Path,
    scanned: Sequence[_ScannedAsset],
    inventory: Sequence[Mapping[str, Any]],
    slot_candidates: Sequence[Mapping[str, Any]],
) -> None:
    """Create a fixed-size, opaque-ID-only contact sheet in the private stage."""

    del root  # Stage binding, not this lexical value, controls output safety.
    Image, ImageDraw = _load_pillow()
    by_name = {item.name: item for item in scanned}
    suggestions = _asset_suggestion_labels(slot_candidates)
    columns, cell_width, cell_height = 4, 300, 230
    rows = max(1, math.ceil(len(inventory) / columns))
    canvas = Image.new("RGB", (columns * cell_width, rows * cell_height), (245, 247, 250))
    draw = ImageDraw.Draw(canvas)
    try:
        if not inventory:
            draw.text((18, 18), "NO ELIGIBLE ASSETS", fill=(55, 65, 81))
        for index, item in enumerate(inventory):
            asset_id = item.get("asset_id")
            source_path = item.get("source_path")
            if not isinstance(asset_id, str) or not isinstance(source_path, str):
                raise _invalid("generated inventory is invalid")
            col, row = index % columns, index // columns
            x, y = col * cell_width, row * cell_height
            draw.rectangle((x + 4, y + 4, x + cell_width - 5, y + cell_height - 5), fill="white", outline=(185, 193, 204))
            source_name = PurePosixPath(source_path).name
            scanned_asset = by_name.get(source_name)
            if scanned_asset is None:
                raise _invalid("generated inventory is invalid")
            thumbnail = _thumbnail_for_asset(scanned_asset, maximum=(cell_width - 20, 155))
            try:
                if thumbnail is not None:
                    canvas.paste(thumbnail, (x + (cell_width - thumbnail.width) // 2, y + 10))
                else:
                    draw.rectangle((x + 18, y + 20, x + cell_width - 18, y + 145), fill=(227, 232, 240))
                    draw.text((x + 112, y + 72), "AUDIO", fill=(51, 65, 85))
            finally:
                if thumbnail is not None:
                    thumbnail.close()
            facts = item.get("facts") if isinstance(item.get("facts"), Mapping) else {}
            if facts.get("kind") == "image":
                technical = f"{item.get('media_type')} {facts.get('width')}x{facts.get('height')}"
            else:
                technical = f"{item.get('media_type')} {facts.get('duration_seconds')}s"
            draw.text((x + 10, y + 170), asset_id, fill=(17, 24, 39))
            draw.text((x + 10, y + 188), technical[:42], fill=(71, 85, 105))
            draw.text((x + 10, y + 206), f"suggested: {suggestions.get(asset_id, 'none')}"[:42], fill=(71, 85, 105))
        with rrv_propose._open_stage_output_file(stage, output, "local asset contact sheet") as handle:
            canvas.save(handle, format="PNG", optimize=False, compress_level=9)
        rrv_propose._assert_stage_regular_file(stage, output, "local asset contact sheet")
    except rrv_runtime.RRVError:
        raise
    except OSError as exc:
        raise _tool_error("could not write local asset contact sheet") from exc
    finally:
        canvas.close()


def _artifact(root: Path, stage: rrv_propose._StageDirectory, target: Path, path: Path) -> dict[str, str]:
    return rrv_propose._published_artifact(root, stage, target, path)


def _write_json(stage: rrv_propose._StageDirectory, root: Path, path: Path, payload: Mapping[str, Any], label: str) -> None:
    del root
    rrv_propose._write_json_new(path, payload, label=label, stage=stage)


def _direct_output_target(root: Path, value: Any) -> Path:
    name = _direct_child_name(value, "output_dir")
    target = root / name
    try:
        existing = os.lstat(target)
    except FileNotFoundError:
        return target
    except OSError as exc:
        raise _tool_error("could not inspect local output target") from exc
    del existing
    raise rrv_runtime.RRVError(rrv_runtime.ERR_OUTPUT_EXISTS, "refusing to overwrite an existing output")


def _proposal_semantic_errors(data: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    template_path = data.get("template_path")
    asset_pack = data.get("asset_pack")
    if not _valid_relative_path(template_path):
        errors.append("$.template_path: normalized_relative_path")
    if _relative_path_parts(asset_pack) is None or len(_relative_path_parts(asset_pack) or ()) != 1:
        errors.append("$.asset_pack: direct_child")
    inventory = data.get("inventory")
    if not isinstance(inventory, list):
        return errors
    ids: set[str] = set()
    paths: list[str] = []
    by_id: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(inventory, start=1):
        path = f"$.inventory[{index - 1}]"
        if not isinstance(item, Mapping):
            continue
        asset_id = item.get("asset_id")
        source_path = item.get("source_path")
        media_type = item.get("media_type")
        facts = item.get("facts")
        if asset_id != f"asset.{index:04d}" or not isinstance(asset_id, str) or asset_id in ids:
            errors.append(f"{path}.asset_id: stable_sequence")
        elif isinstance(asset_id, str):
            ids.add(asset_id)
            by_id[asset_id] = item
        if not _valid_relative_path(source_path):
            errors.append(f"{path}.source_path: normalized_relative_path")
        elif isinstance(source_path, str):
            parts = source_path.split("/")
            if not isinstance(asset_pack, str) or len(parts) != 2 or parts[0] != asset_pack:
                errors.append(f"{path}.source_path: direct_asset_pack_file")
            paths.append(source_path)
        if media_type not in _ALLOWED_MEDIA:
            errors.append(f"{path}.media_type: accepted_media")
        if isinstance(facts, Mapping):
            kind = facts.get("kind")
            if kind == "image":
                width, height, pixels = facts.get("width"), facts.get("height"), facts.get("pixels")
                if not all(isinstance(value, int) and not isinstance(value, bool) for value in (width, height, pixels)):
                    errors.append(f"{path}.facts: image_facts")
                elif width * height != pixels or width > MAX_IMAGE_EDGE or height > MAX_IMAGE_EDGE or pixels > MAX_IMAGE_PIXELS:
                    errors.append(f"{path}.facts: image_bounds")
                if media_type not in {"image/jpeg", "image/png", "image/webp"}:
                    errors.append(f"{path}.facts: media_kind")
            elif kind == "audio":
                duration = facts.get("duration_seconds")
                streams = facts.get("audio_stream_count")
                video_stream_count = facts.get("video_stream_count")
                if (
                    not isinstance(duration, (int, float))
                    or isinstance(duration, bool)
                    or not math.isfinite(float(duration))
                    or not 0 < float(duration) <= MAX_AUDIO_SECONDS
                    or not isinstance(streams, int)
                    or isinstance(streams, bool)
                    or not 1 <= streams <= 64
                    or video_stream_count != 0
                ):
                    errors.append(f"{path}.facts: audio_bounds")
                if media_type not in {"audio/wav", "audio/mpeg", "audio/mp4", "audio/x-matroska"}:
                    errors.append(f"{path}.facts: media_kind")
            else:
                errors.append(f"{path}.facts: kind")
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        errors.append("$.inventory: stable_source_order")
    try:
        inventory_hash = _canonical_json_sha256(inventory)
    except (TypeError, ValueError):
        inventory_hash = None
    if data.get("inventory_sha256") != inventory_hash:
        errors.append("$.inventory_sha256: canonical_inventory_hash")

    candidates = data.get("slot_candidates")
    if not isinstance(candidates, list):
        return errors
    slot_ids: list[str] = []
    for index, candidate in enumerate(candidates):
        path = f"$.slot_candidates[{index}]"
        if not isinstance(candidate, Mapping):
            continue
        slot_id = candidate.get("slot_id")
        asset_ids = candidate.get("candidate_asset_ids")
        status = candidate.get("status")
        accepted = candidate.get("accepted_media")
        if not isinstance(slot_id, str):
            continue
        slot_ids.append(slot_id)
        if not isinstance(asset_ids, list) or any(not isinstance(value, str) for value in asset_ids):
            errors.append(f"{path}.candidate_asset_ids: asset_ids")
            continue
        if asset_ids != sorted(asset_ids) or len(asset_ids) != len(set(asset_ids)):
            errors.append(f"{path}.candidate_asset_ids: stable_unique_order")
        exact_exists = any(
            isinstance(item.get("source_path"), str)
            and PurePosixPath(item["source_path"]).stem == slot_id
            for item in inventory
            if isinstance(item, Mapping)
        )
        expected_status = (
            "missing"
            if not asset_ids and not exact_exists
            else "incompatible"
            if not asset_ids
            else "suggested"
            if len(asset_ids) == 1
            else "ambiguous"
        )
        if status != expected_status:
            errors.append(f"{path}.status: candidate_count")
        for asset_id in asset_ids:
            item = by_id.get(asset_id)
            if item is None:
                errors.append(f"{path}.candidate_asset_ids: unknown_asset")
                continue
            source_path = item.get("source_path")
            media_type = item.get("media_type")
            if not isinstance(source_path, str) or PurePosixPath(source_path).stem != slot_id:
                errors.append(f"{path}.candidate_asset_ids: exact_filename_only")
            if not isinstance(accepted, list) or media_type not in accepted:
                errors.append(f"{path}.candidate_asset_ids: accepted_media")
    if slot_ids != sorted(slot_ids) or len(slot_ids) != len(set(slot_ids)):
        errors.append("$.slot_candidates: stable_slot_order")
    evidence = data.get("evidence")
    artifact = evidence.get("asset_contact_sheet") if isinstance(evidence, Mapping) else None
    if not isinstance(artifact, Mapping) or not _valid_relative_path(artifact.get("path")):
        errors.append("$.evidence.asset_contact_sheet.path: normalized_relative_path")
    return errors


def _review_semantic_errors(data: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    mappings = data.get("mappings")
    if not isinstance(mappings, list):
        return errors
    slot_ids: set[str] = set()
    approved = data.get("decision") == "approved"
    if approved and (data.get("contact_sheet_reviewed") is not True or data.get("local_only_confirmed") is not True):
        errors.append("$: approved_review_confirmations")
    for index, mapping in enumerate(mappings):
        path = f"$.mappings[{index}]"
        if not isinstance(mapping, Mapping):
            continue
        slot_id = mapping.get("slot_id")
        action = mapping.get("action")
        if not isinstance(slot_id, str) or slot_id in slot_ids:
            errors.append(f"{path}.slot_id: unique")
        elif isinstance(slot_id, str):
            slot_ids.add(slot_id)
        if action == "use":
            if not isinstance(mapping.get("processor"), str) or not _PROCESSOR_RE.fullmatch(mapping["processor"]):
                errors.append(f"{path}.processor: safe_slug")
            if approved and any(
                mapping.get(key) is not True
                for key in (
                    "content_reviewed",
                    "media_compatibility_confirmed",
                    "render_ready_confirmed",
                    "rights_confirmed",
                )
            ):
                errors.append(f"{path}: use_confirmations")
        elif action == "omit":
            if approved and mapping.get("omit_confirmed") is not True:
                errors.append(f"{path}.omit_confirmed: required")
        elif action == "unresolved":
            if approved:
                errors.append(f"{path}.action: unresolved_approved")
    return errors


def validate_asset_proposal_data(data: Any) -> list[str]:
    """Validate a local asset-pack proposal without touching project files."""

    errors: list[str] = []
    _find_nonfinite(data, "$", errors)
    errors.extend(_schema_errors(data, _PROPOSAL_SCHEMA_PATH, "asset proposal"))
    if isinstance(data, Mapping):
        try:
            errors.extend(_proposal_semantic_errors(data))
        except Exception:
            errors.append("$: semantic.invalid")
    return _unique_errors(errors)


def validate_asset_review_data(data: Any) -> list[str]:
    """Validate a local asset-mapping review without touching project files."""

    errors: list[str] = []
    _find_nonfinite(data, "$", errors)
    errors.extend(_schema_errors(data, _REVIEW_SCHEMA_PATH, "asset review"))
    if isinstance(data, Mapping):
        try:
            errors.extend(_review_semantic_errors(data))
        except Exception:
            errors.append("$: semantic.invalid")
    return _unique_errors(errors)


def _raise_validation(label: str, errors: Sequence[str]) -> None:
    del errors
    raise _invalid(f"{label} did not pass validation")


def _proposal_evidence_artifact(
    root: Path,
    root_identity: rrv_propose._DirectoryIdentity,
    proposal_path: str,
    proposal: Mapping[str, Any],
) -> None:
    evidence = proposal.get("evidence")
    artifact = evidence.get("asset_contact_sheet") if isinstance(evidence, Mapping) else None
    if not isinstance(artifact, Mapping):
        raise _invalid("proposal evidence is invalid")
    evidence_path = artifact.get("path")
    expected_sha256 = artifact.get("sha256")
    if not isinstance(evidence_path, str) or not isinstance(expected_sha256, str) or not _SHA256_RE.fullmatch(expected_sha256):
        raise _invalid("proposal evidence is invalid")
    proposal_parts = _relative_path_parts(proposal_path)
    evidence_parts = _relative_path_parts(evidence_path)
    if (
        proposal_parts is None
        or evidence_parts is None
        or proposal_parts[-1] != "asset-pack-proposal.json"
        or evidence_parts != (*proposal_parts[:-1], "asset-contact-sheet.png")
    ):
        raise _invalid("proposal evidence is invalid")
    try:
        _, raw = _read_project_file_bytes(
            root,
            root_identity,
            evidence_path,
            label="proposal evidence",
            maximum_bytes=MAX_CONTACT_SHEET_BYTES,
        )
    except rrv_runtime.RRVError as exc:
        raise _invalid("proposal evidence is invalid") from exc
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise _invalid("proposal evidence hash does not match")


def _approved_mappings(
    template: Mapping[str, Any],
    inventory: Sequence[Mapping[str, Any]],
    review: Mapping[str, Any],
    proposal_slot_candidates: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    if review.get("decision") == "rejected":
        raise _invalid("review decision is rejected; no frozen assets were written")
    if review.get("decision") != "approved":
        raise _invalid("review decision must be approved before freezing")
    if review.get("contact_sheet_reviewed") is not True or review.get("local_only_confirmed") is not True:
        raise _invalid("approved review requires local-only contact-sheet confirmation")
    raw_slots = template.get("slots")
    raw_mappings = review.get("mappings")
    if not isinstance(raw_slots, list) or not isinstance(raw_mappings, list):
        raise _invalid("review mappings are invalid")
    slots = {
        item.get("id"): item
        for item in raw_slots
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    mappings: dict[str, Mapping[str, Any]] = {}
    for mapping in raw_mappings:
        if not isinstance(mapping, Mapping) or not isinstance(mapping.get("slot_id"), str):
            raise _invalid("review mappings are invalid")
        slot_id = mapping["slot_id"]
        if slot_id in mappings or slot_id not in slots:
            raise _invalid("review mappings must match Template slots exactly")
        mappings[slot_id] = mapping
    if set(mappings) != set(slots):
        raise _invalid("review mappings must match Template slots exactly")
    proposal_candidates: dict[str, Mapping[str, Any]] = {}
    for candidate in proposal_slot_candidates:
        if not isinstance(candidate, Mapping) or not isinstance(candidate.get("slot_id"), str):
            raise _invalid("proposal slot candidates are invalid")
        slot_id = candidate["slot_id"]
        if slot_id in proposal_candidates or slot_id not in slots:
            raise _invalid("proposal slot candidates must match Template slots exactly")
        proposal_candidates[slot_id] = candidate
    if set(proposal_candidates) != set(slots):
        raise _invalid("proposal slot candidates must match Template slots exactly")
    by_asset_id = {
        item.get("asset_id"): item
        for item in inventory
        if isinstance(item, Mapping) and isinstance(item.get("asset_id"), str)
    }
    approved: list[Mapping[str, Any]] = []
    for slot_id in sorted(slots):
        slot = slots[slot_id]
        mapping = mappings[slot_id]
        action = mapping.get("action")
        if action == "unresolved":
            raise _invalid("approved review cannot contain unresolved mappings")
        if action == "omit":
            if slot.get("required") is True or mapping.get("omit_confirmed") is not True:
                raise _invalid("omit mappings require an explicitly confirmed optional slot")
            continue
        if action != "use":
            raise _invalid("review mappings are invalid")
        asset_id = mapping.get("asset_id")
        candidate = proposal_candidates[slot_id]
        candidate_asset_ids = candidate.get("candidate_asset_ids")
        if (
            candidate.get("status") != "suggested"
            or not isinstance(candidate_asset_ids, list)
            or len(candidate_asset_ids) != 1
            or candidate_asset_ids[0] != asset_id
        ):
            raise _invalid("approved use mapping must match a unique suggested proposal candidate")
        if not isinstance(asset_id, str):
            raise _invalid("review use mapping references an unknown inventory asset")
        item = by_asset_id.get(asset_id)
        if item is None:
            raise _invalid("review use mapping references an unknown inventory asset")
        if any(
            mapping.get(key) is not True
            for key in (
                "content_reviewed",
                "media_compatibility_confirmed",
                "render_ready_confirmed",
                "rights_confirmed",
            )
        ):
            raise _invalid("approved use mappings require every explicit confirmation")
        processor = mapping.get("processor")
        if not isinstance(processor, str) or not _PROCESSOR_RE.fullmatch(processor):
            raise _invalid("approved use mapping processor must be a safe slug")
        accepted = slot.get("accepted_media")
        if not isinstance(accepted, list) or item.get("media_type") not in accepted:
            raise _invalid("approved use mapping media is not accepted by its Template slot")
        approved.append(mapping)
    return approved


def _copy_snapshot_asset(
    asset: _ScannedAsset,
    *,
    stage: rrv_propose._StageDirectory,
    destination: Path,
    expected_sha256: str,
) -> None:
    """Copy one approved immutable-byte snapshot while re-hashing it."""

    digest = hashlib.sha256()
    total = 0
    try:
        if asset.closed:
            raise _invalid("approved asset snapshot is no longer available")
        asset.snapshot.seek(0)
        with rrv_propose._open_stage_output_file(stage, destination, "frozen local asset") as target:
            while True:
                chunk = asset.snapshot.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    raise _invalid("approved asset exceeds the bounded local file limit")
                target.write(chunk)
                digest.update(chunk)
        if total != asset.identity.size_bytes or digest.hexdigest() != expected_sha256:
            raise _invalid("approved asset changed before freezing")
        rrv_propose._assert_stage_regular_file(stage, destination, "frozen local asset")
    except rrv_runtime.RRVError:
        raise
    except OSError as exc:
        raise _tool_error("could not copy approved local asset") from exc


def _staged_manifest_validation(
    template: Mapping[str, Any],
    manifest: Mapping[str, Any],
    stage: rrv_propose._StageDirectory,
    manifest_path: Path,
) -> None:
    """Invoke the existing validator against the private stage before publish."""

    staged_assets: list[dict[str, Any]] = []
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        raise _invalid("generated assets manifest is invalid")
    for asset in assets:
        if not isinstance(asset, Mapping) or not isinstance(asset.get("path"), str):
            raise _invalid("generated assets manifest is invalid")
        staged = dict(asset)
        staged["path"] = PurePosixPath(asset["path"]).name
        staged_assets.append(staged)
    staged_manifest = dict(manifest)
    staged_manifest["assets"] = staged_assets
    try:
        errors = video_remix.validate_assets_data(
            template,
            staged_manifest,
            manifest_path,
            check_files=True,
            project_root=stage.path,
        )
    except Exception as exc:
        raise _invalid("generated assets manifest is incompatible") from exc
    if errors:
        raise _invalid("generated assets manifest is incompatible")


def _safe_exception(exc: BaseException) -> rrv_runtime.RRVError:
    """Convert unforeseen failures to one non-reflective public error."""

    if isinstance(exc, rrv_runtime.RRVError):
        return exc
    return _tool_error("local asset operation failed")


def propose_asset_pack(
    template: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str],
    asset_pack: str | os.PathLike[str],
    asset_pack_rights_confirmed: bool,
    output_dir: str | os.PathLike[str] = "asset-proposal",
    ffprobe: str | os.PathLike[str] = "ffprobe",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Mapping[str, Any]:
    """Create an exact-name-only local asset-pack proposal and review template.

    ``asset_pack_rights_confirmed`` is deliberately the first executable
    boundary: a value other than literal ``True`` causes no root access,
    template/pack enumeration, stage creation, Pillow import, or ffprobe call.
    """

    if asset_pack_rights_confirmed is not True:
        raise _invalid("asset_pack_rights_confirmed must be explicitly true before local asset analysis")
    root = _safe_project_root(project_root)
    timeout = _parse_timeout(timeout_seconds)
    pack_name = _direct_child_name(asset_pack, "asset_pack")
    stage: rrv_propose._StageDirectory | None = None
    try:
        with _root_guard(root) as root_identity:
            target = _direct_output_target(root, output_dir)
            with _asset_pack_guard(root, root_identity, pack_name) as (pack, pack_identity):
                template_snapshot = _read_project_json_snapshot(root, root_identity, template, label="template")
                template_data = _validate_template_snapshot(template_snapshot)
                scanned: list[_ScannedAsset] = []
                try:
                    scanned, inventory = _scan_asset_pack(
                        root_identity,
                        pack,
                        pack_identity,
                        pack_name,
                        ffprobe=ffprobe,
                        timeout_seconds=timeout,
                    )
                    candidates = _slot_candidates(template_data, inventory)
                    stage = rrv_propose._new_staging_directory(root, "asset-proposal")
                    contact_path = rrv_propose._stage_path(root, stage, "asset-contact-sheet.png")
                    _create_contact_sheet(root, stage, contact_path, scanned, inventory, candidates)
                    contact_artifact = _artifact(root, stage, target, contact_path)
                    proposal_data: dict[str, Any] = {
                        "schema_version": SCHEMA_VERSION,
                        "privacy_profile": "local-only",
                        "analysis_rights_confirmed": True,
                        "review_required": True,
                        "template_path": template_snapshot.relative_path,
                        "template_sha256": template_snapshot.sha256,
                        "template_id": template_data.get("template_id"),
                        "asset_pack": pack_name,
                        "scanner_policy_version": SCANNER_POLICY_VERSION,
                        "inventory": inventory,
                        "inventory_sha256": _canonical_json_sha256(inventory),
                        "slot_candidates": candidates,
                        "evidence": {"asset_contact_sheet": contact_artifact},
                    }
                    proposal_errors = validate_asset_proposal_data(proposal_data)
                    if proposal_errors:
                        _raise_validation("generated asset proposal", proposal_errors)
                    proposal_path = rrv_propose._stage_path(root, stage, "asset-pack-proposal.json")
                    _write_json(stage, root, proposal_path, proposal_data, "asset proposal JSON")
                    proposal_sha256 = rrv_propose._stage_file_sha256(stage, proposal_path)
                    review_data = _review_template(proposal_sha256, candidates)
                    review_errors = validate_asset_review_data(review_data)
                    if review_errors:
                        _raise_validation("generated asset review template", review_errors)
                    review_path = rrv_propose._stage_path(root, stage, "asset-review-decision.template.json")
                    _write_json(stage, root, review_path, review_data, "asset review template JSON")
                    proposal_artifact = _artifact(root, stage, target, proposal_path)
                    review_artifact = _artifact(root, stage, target, review_path)
                    _assert_pack_live(root_identity, pack_identity)
                    rrv_propose._publish_stage(root, stage, target, label="asset proposal")
                    stage = None
                    return {
                        "schema_version": SCHEMA_VERSION,
                        "review_required": True,
                        "counts": {
                            "inventory_entries": len(inventory),
                            "template_slots": len(candidates),
                            "suggested_slots": sum(item["status"] == "suggested" for item in candidates),
                        },
                        "artifacts": {
                            "proposal": proposal_artifact,
                            "review_template": review_artifact,
                            "contact_sheet": contact_artifact,
                        },
                    }
                finally:
                    _close_scanned_assets(scanned)
    except BaseException as exc:
        rrv_propose._cleanup_directory(root, stage)
        raise _safe_exception(exc) from None


def freeze_assets(
    proposal: str | os.PathLike[str],
    review: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str],
    output_dir: str | os.PathLike[str] = "frozen-assets",
    ffprobe: str | os.PathLike[str] = "ffprobe",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Mapping[str, Any]:
    """Publish a flat opaque local asset set after a fully confirmed review."""

    root = _safe_project_root(project_root)
    timeout = _parse_timeout(timeout_seconds)
    stage: rrv_propose._StageDirectory | None = None
    scanned: list[_ScannedAsset] = []
    try:
        with _root_guard(root) as root_identity:
            target = _direct_output_target(root, output_dir)
            proposal_snapshot = _read_project_json_snapshot(root, root_identity, proposal, label="proposal")
            review_snapshot = _read_project_json_snapshot(root, root_identity, review, label="review")
            proposal_data = proposal_snapshot.data
            review_data = review_snapshot.data
            proposal_errors = validate_asset_proposal_data(proposal_data)
            if proposal_errors:
                _raise_validation("proposal", proposal_errors)
            review_errors = validate_asset_review_data(review_data)
            if review_errors:
                _raise_validation("review", review_errors)
            if not isinstance(proposal_data, Mapping) or not isinstance(review_data, Mapping):
                raise _invalid("proposal and review must be JSON objects")
            if review_data.get("proposal_sha256") != proposal_snapshot.sha256:
                raise _invalid("review proposal_sha256 does not match the exact proposal file")
            _proposal_evidence_artifact(root, root_identity, proposal_snapshot.relative_path, proposal_data)
            template_path = proposal_data.get("template_path")
            template_snapshot = _read_project_json_snapshot(root, root_identity, template_path, label="template")
            template_data = _validate_template_snapshot(template_snapshot)
            if (
                proposal_data.get("template_sha256") != template_snapshot.sha256
                or proposal_data.get("template_id") != template_data.get("template_id")
            ):
                raise _invalid("template changed since the asset proposal was created")
            pack_name = _direct_child_name(proposal_data.get("asset_pack"), "proposal asset_pack")
            with _asset_pack_guard(root, root_identity, pack_name) as (pack, pack_identity):
                scanned, re_inventory = _scan_asset_pack(
                    root_identity,
                    pack,
                    pack_identity,
                    pack_name,
                    ffprobe=ffprobe,
                    timeout_seconds=timeout,
                )
                proposed_inventory = proposal_data.get("inventory")
                if (
                    not isinstance(proposed_inventory, list)
                    or _canonical_json_bytes(re_inventory) != _canonical_json_bytes(proposed_inventory)
                    or proposal_data.get("inventory_sha256") != _canonical_json_sha256(re_inventory)
                ):
                    raise _invalid("asset pack inventory changed since the proposal was created")
                expected_candidates = _slot_candidates(template_data, re_inventory)
                if proposal_data.get("slot_candidates") != expected_candidates:
                    raise _invalid("Template slots changed since the asset proposal was created")
                proposal_candidates = proposal_data.get("slot_candidates")
                if not isinstance(proposal_candidates, list):
                    raise _invalid("proposal slot candidates are invalid")
                approved = _approved_mappings(template_data, re_inventory, review_data, proposal_candidates)
                by_id: dict[str, tuple[_ScannedAsset, Mapping[str, Any]]] = {}
                for scanned_asset, inventory_item in zip(scanned, re_inventory):
                    asset_id = inventory_item.get("asset_id")
                    if isinstance(asset_id, str):
                        by_id[asset_id] = (scanned_asset, inventory_item)
                stage = rrv_propose._new_staging_directory(root, "asset-freeze")
                copied_paths: dict[str, str] = {}
                used_ids = sorted({str(mapping["asset_id"]) for mapping in approved})
                for asset_id in used_ids:
                    source = by_id.get(asset_id)
                    match = _ASSET_ID_RE.fullmatch(asset_id)
                    if source is None or match is None:
                        raise _invalid("approved review references an unknown inventory asset")
                    scanned_asset, inventory_item = source
                    media_type = inventory_item.get("media_type")
                    expected_hash = inventory_item.get("sha256")
                    if not isinstance(media_type, str) or not isinstance(expected_hash, str) or media_type not in _CANONICAL_EXTENSION:
                        raise _invalid("approved inventory asset is invalid")
                    destination_name = f"asset-{match.group(1)}.{_CANONICAL_EXTENSION[media_type]}"
                    destination = rrv_propose._stage_path(root, stage, destination_name)
                    _copy_snapshot_asset(
                        scanned_asset,
                        stage=stage,
                        destination=destination,
                        expected_sha256=expected_hash,
                    )
                    copied_paths[asset_id] = rrv_propose._lexical_relative_output_path(root, target / destination_name)
                manifest_assets: list[dict[str, Any]] = []
                for mapping in sorted(approved, key=lambda item: str(item["slot_id"])):
                    asset_id = mapping.get("asset_id")
                    source = by_id.get(asset_id)
                    if not isinstance(asset_id, str) or source is None:
                        raise _invalid("approved review references an unknown inventory asset")
                    _, inventory_item = source
                    manifest_assets.append(
                        {
                            "slot_id": mapping["slot_id"],
                            "path": copied_paths[asset_id],
                            "media_type": inventory_item["media_type"],
                            "sha256": inventory_item["sha256"],
                            "rights_confirmed": True,
                            "cloud_upload_allowed": False,
                            "processor": mapping["processor"],
                        }
                    )
                manifest: dict[str, Any] = {
                    "schema_version": "0.2.0",
                    "template_id": template_data.get("template_id"),
                    "privacy_profile": "local-only",
                    "assets": manifest_assets,
                }
                manifest_path = rrv_propose._stage_path(root, stage, "assets.json")
                _write_json(stage, root, manifest_path, manifest, "frozen assets manifest")
                _staged_manifest_validation(template_data, manifest, stage, manifest_path)
                manifest_sha256 = rrv_propose._stage_file_sha256(stage, manifest_path)
                report = {
                    "schema_version": SCHEMA_VERSION,
                    "proposal_sha256": proposal_snapshot.sha256,
                    "review_sha256": review_snapshot.sha256,
                    "template_sha256": template_snapshot.sha256,
                    "manifest_sha256": manifest_sha256,
                    "inventory_sha256": proposal_data["inventory_sha256"],
                    "scanner_policy_version": SCANNER_POLICY_VERSION,
                    "counts": {
                        "inventory_entries": len(re_inventory),
                        "mapped_slots": len(manifest_assets),
                        "omitted_slots": len(expected_candidates) - len(manifest_assets),
                        "copied_assets": len(copied_paths),
                    },
                }
                report_path = rrv_propose._stage_path(root, stage, "asset-freeze-report.json")
                _write_json(stage, root, report_path, report, "asset freeze report")
                manifest_artifact = _artifact(root, stage, target, manifest_path)
                report_artifact = _artifact(root, stage, target, report_path)
                _assert_pack_live(root_identity, pack_identity)
                rrv_propose._publish_stage(root, stage, target, label="frozen assets")
                stage = None
                return {
                    "schema_version": SCHEMA_VERSION,
                    "review_required": False,
                    "counts": dict(report["counts"]),
                    "artifacts": {
                        "assets_manifest": manifest_artifact,
                        "freeze_report": report_artifact,
                    },
                }
    except BaseException as exc:
        rrv_propose._cleanup_directory(root, stage)
        raise _safe_exception(exc) from None
    finally:
        _close_scanned_assets(scanned)


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "SCANNER_POLICY_VERSION",
    "SCHEMA_VERSION",
    "freeze_assets",
    "propose_asset_pack",
    "validate_asset_proposal_data",
    "validate_asset_review_data",
]
