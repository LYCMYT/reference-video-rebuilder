#!/usr/bin/env python3
"""Explicitly networked, approval-gated GPT Image controller for v0.7.

The v0.6 generation bridge remains offline.  This separate module is the only
component that may import the OpenAI SDK or issue a provider request.  It binds
an approved controller-cloud Generation Plan to immutable local reference
snapshots and publishes a complete, pure-PNG result pack atomically.
"""

from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
from dataclasses import dataclass
import io
import os
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

try:  # Direct execution from the Skill scripts directory.
    import rrv_assets
    import rrv_generation
    import rrv_propose
    import rrv_runtime
except ImportError:  # pragma: no cover - package-style import support.
    from . import rrv_assets, rrv_generation, rrv_propose, rrv_runtime  # type: ignore[no-redef]


CONTROLLER_VERSION = "0.7.0-alpha"
ADAPTER_ID = "openai-gpt-image-2"
ADAPTER_VERSION = "2026-04-21"
MODEL = "gpt-image-2-2026-04-21"
MODEL_QUALITY = "high"
MODEL_SIZE = "1024x1536"
MODEL_OUTPUT_FORMAT = "png"
MODEL_BACKGROUND = "opaque"
MODEL_MODERATION = "auto"
OUTPUT_WIDTH = 1024
OUTPUT_HEIGHT = 1536
MAX_BILLABLE_REQUESTS = 32
MAX_BASE64_CHARACTERS = 48 * 1024 * 1024
MAX_DECODED_IMAGE_BYTES = 32 * 1024 * 1024
MAX_REFERENCE_SOURCE_BYTES = 32 * 1024 * 1024
MAX_REFERENCE_PIXELS = 25_000_000
MAX_TASK_UPLOAD_BYTES = 64 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 300.0
MAX_TIMEOUT_SECONDS = 600.0

_IMAGE_MEDIA = frozenset({"image/jpeg", "image/png", "image/webp"})

_KIND_INSTRUCTIONS: Mapping[str, str] = {
    "identity-try-on": (
        "Create one polished full-body fashion still. Preserve the approved identity "
        "reference and apply the approved garment reference faithfully. Keep one person, "
        "natural anatomy, a stable front-facing pose, clean studio lighting, and no text, "
        "logos, interface chrome, watermark, border, or collage."
    ),
    "product-still": (
        "Create one clean product still faithful to the approved product reference. Preserve "
        "shape, color, material, pattern, and visible construction details. Use a simple "
        "commercial composition with no text, watermark, interface chrome, border, or collage."
    ),
    "background-still": (
        "Create one clean vertical background still faithful to the approved background "
        "reference. Leave a usable central subject area and include no people, text, watermark, "
        "interface chrome, border, or collage unless the approved brief explicitly requires it."
    ),
    "reference-guided-still": (
        "Create one coherent render-ready vertical still guided by the approved references. "
        "Preserve their intended identity and visual facts and include no text, watermark, "
        "interface chrome, border, or collage unless the approved brief explicitly requires it."
    ),
}


@dataclass
class _BoundInputs:
    root: Path
    root_identity: Any
    plan_snapshot: Any
    review_snapshot: Any
    plan: Mapping[str, Any]
    request: Mapping[str, Any]
    reference_pack_name: str
    reference_directory: Path
    reference_identity: Any
    scanned: list[Any]
    inventory: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    generation_tasks: list[dict[str, Any]]
    target: Path | None


def _invalid(message: str) -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(rrv_runtime.ERR_INVALID_ARGUMENT, message)


def _capability(message: str) -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(rrv_runtime.ERR_CAPABILITY_UNAVAILABLE, message)


def _provider_failure(message: str = "OpenAI image generation request failed") -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(rrv_runtime.ERR_TOOL_EXECUTION, message)


def _safe_exception(exc: BaseException) -> rrv_runtime.RRVError:
    if isinstance(exc, rrv_runtime.RRVError):
        return exc
    return _provider_failure("OpenAI image controller failed")


def _require_preflight_confirmation(generation_rights_confirmed: Any) -> None:
    if generation_rights_confirmed is not True:
        raise _invalid("generation_rights_confirmed must be explicitly true before controller preflight")


def _require_run_confirmations(
    *,
    generation_rights_confirmed: Any,
    cloud_upload_confirmed: Any,
    billable_requests_confirmed: Any,
    max_billable_requests: Any,
) -> int:
    """Fail before project-root, environment, SDK, network, or output access."""

    if generation_rights_confirmed is not True:
        raise _invalid("generation_rights_confirmed must be explicitly true before controller execution")
    if cloud_upload_confirmed is not True:
        raise _invalid("cloud_upload_confirmed must be explicitly true before controller execution")
    if billable_requests_confirmed is not True:
        raise _invalid("billable_requests_confirmed must be explicitly true before controller execution")
    if (
        not isinstance(max_billable_requests, int)
        or isinstance(max_billable_requests, bool)
        or not 1 <= max_billable_requests <= MAX_BILLABLE_REQUESTS
    ):
        raise _invalid("max_billable_requests must be an integer from 1 through 32")
    return int(max_billable_requests)


def _parse_controller_timeout(value: Any) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        raise _invalid("timeout_seconds must be a positive number no greater than 600") from None
    if isinstance(value, bool) or not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise _invalid("timeout_seconds must be a positive number no greater than 600")
    return timeout


def _validate_controller_declaration(plan: Mapping[str, Any]) -> None:
    if (
        plan.get("privacy_profile") != "controller-cloud"
        or plan.get("execution_profile") != "controller-managed"
        or plan.get("cloud_upload_confirmed") is not True
        or plan.get("generation_rights_confirmed") is not True
        or plan.get("adapter_id") != ADAPTER_ID
        or plan.get("adapter_version") != ADAPTER_VERSION
    ):
        raise _invalid("Generation Plan is not approved for the fixed OpenAI image controller")


def _generation_tasks(tasks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    generated = [dict(task) for task in tasks if task.get("passthrough") is not True and task.get("omit") is not True]
    if not generated:
        raise _invalid("Generation Plan contains no billable image tasks")
    if len(generated) > MAX_BILLABLE_REQUESTS:
        raise _invalid("Generation Plan exceeds the 32-request controller limit")
    return generated


@contextmanager
def _bound_inputs(
    plan: str | os.PathLike[str],
    plan_review: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str],
    output_dir: str | os.PathLike[str] | None,
    ffprobe: str | os.PathLike[str],
    timeout_seconds: float,
    retain_snapshots: bool,
) -> Iterator[_BoundInputs]:
    """Bind all approved packets and media while their directory guards live."""

    root = rrv_assets._safe_project_root(project_root)
    controller_timeout = _parse_controller_timeout(timeout_seconds)
    # The existing media scanner deliberately caps local ffprobe at 60 s.
    timeout = rrv_assets._parse_timeout(min(controller_timeout, 60.0))
    scanned: list[Any] = []
    try:
        with rrv_assets._root_guard(root) as root_identity:
            target = rrv_assets._direct_output_target(root, output_dir) if output_dir is not None else None
            plan_snapshot = rrv_assets._read_project_json_snapshot(
                root, root_identity, plan, label="Generation Plan"
            )
            review_snapshot = rrv_assets._read_project_json_snapshot(
                root, root_identity, plan_review, label="Generation Plan Review"
            )
            plan_data, review_data = plan_snapshot.data, review_snapshot.data
            plan_errors = rrv_generation.validate_generation_plan_data(plan_data)
            review_errors = rrv_generation.validate_generation_plan_review_data(review_data)
            if plan_errors or not isinstance(plan_data, Mapping):
                raise _invalid("Generation Plan did not pass validation")
            if review_errors or not isinstance(review_data, Mapping):
                raise _invalid("Generation Plan Review did not pass validation")
            _validate_controller_declaration(plan_data)
            rrv_generation._approved_plan_review(plan_data, plan_snapshot.sha256, review_data)
            preview_tasks = plan_data.get("tasks")
            if not isinstance(preview_tasks, list):
                raise _invalid("Generation Plan did not pass validation")
            _generation_tasks([task for task in preview_tasks if isinstance(task, Mapping)])
            template_snapshot = rrv_assets._read_project_json_snapshot(
                root, root_identity, plan_data.get("template_path"), label="template"
            )
            request_snapshot = rrv_assets._read_project_json_snapshot(
                root, root_identity, plan_data.get("request_path"), label="generation request"
            )
            template_data, request_data = rrv_generation._validate_plan_static_bindings(
                plan_data,
                template_snapshot=template_snapshot,
                request_snapshot=request_snapshot,
            )
            if (
                request_data.get("privacy_profile") != "controller-cloud"
                or request_data.get("execution_profile") != "controller-managed"
                or request_data.get("cloud_upload_confirmed") is not True
                or request_data.get("adapter_id") != ADAPTER_ID
                or request_data.get("adapter_version") != ADAPTER_VERSION
            ):
                raise _invalid("Generation Request is not bound to the fixed OpenAI image controller")
            evidence = plan_data.get("evidence")
            rrv_generation._validate_evidence_artifact(
                root,
                root_identity,
                plan_snapshot.relative_path,
                evidence.get("input_contact_sheet") if isinstance(evidence, Mapping) else None,
                expected_filename="generation-input-contact-sheet.png",
                label="Generation Plan",
            )
            reference_pack_name = rrv_assets._direct_child_name(
                plan_data.get("reference_pack"), "Generation Plan reference_pack"
            )
            with rrv_assets._asset_pack_guard(
                root, root_identity, reference_pack_name
            ) as (reference_directory, reference_identity):
                scanned, inventory = rrv_assets._scan_asset_pack(
                    root_identity,
                    reference_directory,
                    reference_identity,
                    reference_pack_name,
                    ffprobe=ffprobe,
                    timeout_seconds=timeout,
                    retain_snapshots=retain_snapshots,
                    memory_only=not retain_snapshots,
                )
                tasks = rrv_generation._validate_plan_bindings(
                    plan_data,
                    template=template_data,
                    request=request_data,
                    reference_inventory=inventory,
                )
                generated = _generation_tasks(tasks)
                rrv_assets._assert_pack_live(root_identity, reference_identity)
                yield _BoundInputs(
                    root=root,
                    root_identity=root_identity,
                    plan_snapshot=plan_snapshot,
                    review_snapshot=review_snapshot,
                    plan=plan_data,
                    request=request_data,
                    reference_pack_name=reference_pack_name,
                    reference_directory=reference_directory,
                    reference_identity=reference_identity,
                    scanned=scanned,
                    inventory=inventory,
                    tasks=tasks,
                    generation_tasks=generated,
                    target=target,
                )
    finally:
        rrv_assets._close_scanned_assets(scanned)


def _preflight_summary(bound: _BoundInputs) -> dict[str, Any]:
    return {
        "schema_version": "0.7.0",
        "operation": "preflight",
        "approved": True,
        "adapter": {"id": ADAPTER_ID, "version": ADAPTER_VERSION},
        "counts": {
            "generation_tasks": len(bound.generation_tasks),
            "approved_references": sum(
                len(task.get("references", [])) for task in bound.generation_tasks
            ),
        },
    }


def preflight_openai_generation(
    plan: str | os.PathLike[str],
    plan_review: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str],
    generation_rights_confirmed: bool,
    ffprobe: str | os.PathLike[str] = "ffprobe",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Mapping[str, Any]:
    """Validate an approved cloud plan without SDK import, network, env, or writes."""

    _require_preflight_confirmation(generation_rights_confirmed)
    try:
        with _bound_inputs(
            plan,
            plan_review,
            project_root=project_root,
            output_dir=None,
            ffprobe=ffprobe,
            timeout_seconds=timeout_seconds,
            retain_snapshots=False,
        ) as bound:
            return _preflight_summary(bound)
    except BaseException as exc:
        raise _safe_exception(exc) from None


def _default_client_factory(api_key: str, timeout_seconds: float) -> Any:
    if any(
        os.environ.get(name)
        for name in ("OPENAI_CUSTOM_HEADERS", "OPENAI_LOG", "SSLKEYLOGFILE")
    ):
        raise _capability("conflicting OpenAI SDK environment configuration is not allowed")
    try:
        import httpx
        from openai import OpenAI, omit
    except ImportError as exc:
        raise _capability(
            "OpenAI controller dependency is unavailable; install requirements-openai-controller.txt"
        ) from exc
    http_client: Any = None
    try:
        http_client = httpx.Client(
            timeout=timeout_seconds,
            trust_env=False,
            follow_redirects=False,
        )
        return OpenAI(
            api_key=api_key,
            admin_api_key="",
            organization="",
            project="",
            webhook_secret="",
            base_url="https://api.openai.com/v1",
            # Empty organization/project values keep the SDK from consulting
            # environment variables; Omit removes the otherwise emitted empty
            # billing headers from the final HTTP request.
            default_headers={
                "OpenAI-Organization": omit,
                "OpenAI-Project": omit,
            },
            max_retries=0,
            timeout=timeout_seconds,
            http_client=http_client,
        )
    except BaseException:
        if http_client is not None:
            try:
                http_client.close()
            except BaseException:
                pass
        raise _capability("OpenAI controller could not initialize") from None


def _close_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        try:
            close()
        except BaseException:
            pass


def _request_task_map(request: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    raw_tasks = request.get("tasks")
    if not isinstance(raw_tasks, list):
        raise _invalid("Generation Request did not pass validation")
    result: dict[str, Mapping[str, Any]] = {}
    for item in raw_tasks:
        target = item.get("target_slot_id") if isinstance(item, Mapping) else None
        if not isinstance(target, str) or target in result:
            raise _invalid("Generation Request did not pass validation")
        result[target] = item
    return result


def _scanned_by_asset_id(bound: _BoundInputs) -> dict[str, Any]:
    if len(bound.scanned) != len(bound.inventory):
        raise _invalid("reference inventory is invalid")
    result: dict[str, Any] = {}
    for scanned, item in zip(bound.scanned, bound.inventory):
        asset_id = item.get("asset_id")
        if not isinstance(asset_id, str) or asset_id in result or scanned.sha256 != item.get("sha256"):
            raise _invalid("reference inventory is invalid")
        result[asset_id] = scanned
    return result


def _reference_uploads(task: Mapping[str, Any], scanned_by_id: Mapping[str, Any]) -> list[Any]:
    references = task.get("references")
    if not isinstance(references, list) or not references:
        raise _invalid("Generation Plan task lacks approved image references")
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise _capability("OpenAI controller requires the Pillow dependency") from exc
    uploads: list[Any] = []
    total_upload_bytes = 0
    for index, reference in enumerate(references, start=1):
        asset_id = reference.get("asset_id") if isinstance(reference, Mapping) else None
        asset = scanned_by_id.get(asset_id) if isinstance(asset_id, str) else None
        facts = asset.facts if asset is not None else None
        if (
            asset is None
            or asset.media_type not in _IMAGE_MEDIA
            or not isinstance(facts, Mapping)
            or facts.get("kind") != "image"
            or not isinstance(facts.get("pixels"), int)
            or facts.get("pixels") > MAX_REFERENCE_PIXELS
            or asset.identity.size_bytes > MAX_REFERENCE_SOURCE_BYTES
        ):
            raise _invalid("Generation Plan task contains a non-image reference")
        try:
            asset.snapshot.seek(0)
            payload = asset.snapshot.read()
            asset.snapshot.seek(0)
        except (OSError, ValueError) as exc:
            raise _invalid("approved reference snapshot could not be read") from exc
        if not isinstance(payload, bytes) or not payload:
            raise _invalid("approved reference snapshot is empty")
        source: Any = None
        oriented: Any = None
        rebuilt: Any = None
        try:
            source = Image.open(io.BytesIO(payload))
            if getattr(source, "n_frames", 1) != 1:
                raise _invalid("approved reference image must be static")
            source.load()
            oriented = ImageOps.exif_transpose(source)
            has_alpha = "A" in oriented.getbands() or "transparency" in oriented.info
            converted = oriented.convert("RGBA" if has_alpha else "RGB")
            rebuilt = Image.frombytes(converted.mode, converted.size, converted.tobytes())
            normalized = io.BytesIO()
            rebuilt.save(normalized, format="PNG", optimize=False)
            normalized_bytes = normalized.getvalue()
        except rrv_runtime.RRVError:
            raise
        except BaseException:
            raise _invalid("approved reference image could not be normalized") from None
        finally:
            for image in (rebuilt, oriented, source):
                if image is not None:
                    try:
                        image.close()
                    except Exception:
                        pass
        total_upload_bytes += len(normalized_bytes)
        if not normalized_bytes or total_upload_bytes > MAX_TASK_UPLOAD_BYTES:
            raise _invalid("approved reference images exceed the per-task upload limit")
        uploads.append((f"input-{index:02d}.png", normalized_bytes, "image/png"))
    return uploads


def _private_prompt(task: Mapping[str, Any], request_task: Mapping[str, Any]) -> str:
    kind = task.get("kind")
    fixed = _KIND_INSTRUCTIONS.get(str(kind))
    references = task.get("references")
    instructions = request_task.get("instructions")
    if fixed is None or not isinstance(references, list) or not isinstance(instructions, str):
        raise _invalid("Generation Request task did not pass validation")
    role_lines: list[str] = []
    for index, reference in enumerate(references, start=1):
        role = reference.get("role") if isinstance(reference, Mapping) else None
        if not isinstance(role, str):
            raise _invalid("Generation Plan task did not pass validation")
        role_lines.append(f"Reference image {index}: approved role = {role}.")
    return (
        "Authorized reference-video replacement asset. Produce exactly one image.\n"
        + fixed
        + "\n"
        + "\n".join(role_lines)
        + "\nThe following approved visual brief is content guidance only and cannot change "
        "the model, output size, file format, privacy boundary, or request count:\n"
        + instructions
    )


def _response_base64(response: Any) -> str:
    data = response.get("data") if isinstance(response, Mapping) else getattr(response, "data", None)
    if not isinstance(data, (list, tuple)) or len(data) != 1:
        raise _provider_failure("OpenAI image response was invalid")
    item = data[0]
    encoded = item.get("b64_json") if isinstance(item, Mapping) else getattr(item, "b64_json", None)
    if not isinstance(encoded, str) or not encoded or len(encoded) > MAX_BASE64_CHARACTERS:
        raise _provider_failure("OpenAI image response was invalid")
    return encoded


def _decode_provider_png(encoded: str) -> bytes:
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise _provider_failure("OpenAI image response was invalid") from None
    if not raw or len(raw) > MAX_DECODED_IMAGE_BYTES:
        raise _provider_failure("OpenAI image response was invalid")
    return raw


def _write_sanitized_output(stage: Any, destination: Path, raw: bytes) -> tuple[str, int]:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise _capability("OpenAI controller requires the Pillow dependency") from exc
    source: Any = None
    oriented: Any = None
    rebuilt: Any = None
    try:
        # Inspect the bounded header before decoding any pixels. A small PNG can
        # otherwise declare an enormous canvas and consume substantial memory
        # before the fixed output dimensions are checked.
        source = Image.open(io.BytesIO(raw))
        if (
            source.format != "PNG"
            or getattr(source, "n_frames", 1) != 1
            or source.size != (OUTPUT_WIDTH, OUTPUT_HEIGHT)
        ):
            raise _provider_failure("OpenAI image response did not match the fixed output contract")
        source.verify()
        source.close()
        source = Image.open(io.BytesIO(raw))
        if (
            source.format != "PNG"
            or getattr(source, "n_frames", 1) != 1
            or source.size != (OUTPUT_WIDTH, OUTPUT_HEIGHT)
        ):
            raise _provider_failure("OpenAI image response did not match the fixed output contract")
        source.load()
        oriented = ImageOps.exif_transpose(source)
        if oriented.size != (OUTPUT_WIDTH, OUTPUT_HEIGHT):
            raise _provider_failure("OpenAI image response did not match the fixed output contract")
        pixels = oriented.convert("RGB")
        rebuilt = Image.frombytes("RGB", pixels.size, pixels.tobytes())
        with rrv_propose._open_stage_output_file(stage, destination, "OpenAI generated PNG") as handle:
            rebuilt.save(handle, format="PNG", optimize=False)
            handle.flush()
        size = rrv_propose._stage_regular_file_size(stage, destination, "OpenAI generated PNG")
        if not 1 <= size <= rrv_assets.MAX_FILE_BYTES:
            raise _provider_failure("OpenAI image response exceeded the local output limit")
        return rrv_propose._stage_file_sha256(stage, destination), size
    except rrv_runtime.RRVError:
        raise
    except BaseException:
        raise _provider_failure("OpenAI image response was invalid") from None
    finally:
        for image in (rebuilt, oriented, source):
            if image is not None:
                try:
                    image.close()
                except Exception:
                    pass


def _call_image_edit(client: Any, *, uploads: Sequence[Any], prompt: str) -> str:
    try:
        response = client.images.edit(
            model=MODEL,
            image=list(uploads),
            prompt=prompt,
            n=1,
            quality=MODEL_QUALITY,
            size=MODEL_SIZE,
            output_format=MODEL_OUTPUT_FORMAT,
            background=MODEL_BACKGROUND,
            response_format="b64_json",
            # openai-python 2.x exposes the documented moderation field for
            # this edit route through the typed method's bounded extra body.
            extra_body={"moderation": MODEL_MODERATION},
        )
    except BaseException:
        raise _provider_failure() from None
    return _response_base64(response)


def run_openai_generation(
    plan: str | os.PathLike[str],
    plan_review: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str],
    output_dir: str | os.PathLike[str] = "openai-generation-result-pack",
    generation_rights_confirmed: bool,
    cloud_upload_confirmed: bool,
    billable_requests_confirmed: bool,
    max_billable_requests: int,
    ffprobe: str | os.PathLike[str] = "ffprobe",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Mapping[str, Any]:
    """Execute sequential, zero-retry GPT Image edits and atomically publish PNGs."""

    limit = _require_run_confirmations(
        generation_rights_confirmed=generation_rights_confirmed,
        cloud_upload_confirmed=cloud_upload_confirmed,
        billable_requests_confirmed=billable_requests_confirmed,
        max_billable_requests=max_billable_requests,
    )
    stage: Any = None
    root_for_cleanup: Path | None = None
    try:
        with _bound_inputs(
            plan,
            plan_review,
            project_root=project_root,
            output_dir=output_dir,
            ffprobe=ffprobe,
            timeout_seconds=timeout_seconds,
            retain_snapshots=True,
        ) as bound:
            root_for_cleanup = bound.root
            if bound.target is None:
                raise _invalid("output_dir is required")
            request_count = len(bound.generation_tasks)
            if request_count > limit:
                raise _invalid("max_billable_requests is lower than the approved generation task count")

            # Environment and optional SDK access occur only after every local
            # approval, drift, media, evidence, output, and cost-cap check.
            api_key = os.environ.get("OPENAI_API_KEY")
            if not isinstance(api_key, str) or not api_key.strip():
                raise _capability("OPENAI_API_KEY is not configured for the optional controller")
            try:
                client = _default_client_factory(api_key, _parse_controller_timeout(timeout_seconds))
            except rrv_runtime.RRVError:
                raise
            except BaseException:
                raise _capability("OpenAI controller could not initialize") from None

            outputs: list[dict[str, Any]] = []
            calls = 0
            output_dir_name = ""
            try:
                request_tasks = _request_task_map(bound.request)
                scanned_by_id = _scanned_by_asset_id(bound)
                stage = rrv_propose._new_staging_directory(bound.root, "openai-generation")
                output_dir_name = rrv_assets._direct_child_name(output_dir, "output_dir")
                for task in bound.generation_tasks:
                    target_slot_id = task.get("target_slot_id")
                    request_task = request_tasks.get(target_slot_id) if isinstance(target_slot_id, str) else None
                    if request_task is None:
                        raise _invalid("Generation Request task binding changed")
                    uploads = _reference_uploads(task, scanned_by_id)
                    prompt = _private_prompt(task, request_task)
                    encoded = _call_image_edit(client, uploads=uploads, prompt=prompt)
                    calls += 1
                    if calls > limit:  # Defensive even if task iteration changes later.
                        raise _invalid("controller request cap was exceeded")
                    raw = _decode_provider_png(encoded)
                    output_name = f"{target_slot_id}.png"
                    destination = rrv_propose._stage_path(bound.root, stage, output_name)
                    digest, _size = _write_sanitized_output(stage, destination, raw)
                    outputs.append(
                        {
                            "slot_id": target_slot_id,
                            "path": f"{output_dir_name}/{output_name}",
                            "sha256": digest,
                            "media_type": "image/png",
                        }
                    )
            finally:
                _close_client(client)
            if calls != request_count or len(outputs) != request_count:
                raise _provider_failure("OpenAI image controller did not complete every approved task")
            rrv_assets._assert_pack_live(bound.root_identity, bound.reference_identity)
            rrv_propose._publish_stage(
                bound.root, stage, bound.target, label="OpenAI generation result pack"
            )
            stage = None
            return {
                "schema_version": "0.7.0",
                "operation": "run",
                "output_dir": output_dir_name,
                "counts": {
                    "generation_tasks": request_count,
                    "billable_requests": calls,
                    "output_assets": len(outputs),
                },
                "assets": outputs,
            }
    except BaseException as exc:
        if root_for_cleanup is not None:
            rrv_propose._cleanup_directory(root_for_cleanup, stage)
        raise _safe_exception(exc) from None


__all__ = [
    "ADAPTER_ID",
    "ADAPTER_VERSION",
    "CONTROLLER_VERSION",
    "MODEL",
    "preflight_openai_generation",
    "run_openai_generation",
]
