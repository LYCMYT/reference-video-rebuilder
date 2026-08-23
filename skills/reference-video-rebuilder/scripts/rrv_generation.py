#!/usr/bin/env python3
"""Strict local hand-off for reviewed still-generation replacement assets.

This module deliberately does *not* invoke an image/video model, a web API, a
controller, or a shell generator.  It creates a bounded provider-neutral plan
for a separately authorized controller, inventories its local result drop,
requires human review, and publishes a pure-media pack that v0.5 can inspect.
Every public operation is local, fail-closed, and staged before publication.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping, Sequence

try:  # Direct execution from the Skill scripts directory.
    import rrv_assets
    import rrv_propose
    import rrv_runtime
    import video_remix
except ImportError:  # pragma: no cover - package-style import support.
    from . import rrv_assets, rrv_propose, rrv_runtime, video_remix  # type: ignore[no-redef]


SCHEMA_VERSION = "0.6.0"
SCANNER_POLICY_VERSION = rrv_assets.SCANNER_POLICY_VERSION
DEFAULT_TIMEOUT_SECONDS = 60.0

_SKILL_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_DIRECTORY = _SKILL_ROOT / "assets" / "schemas"
_REQUEST_SCHEMA_PATH = _SCHEMA_DIRECTORY / "generation-request.schema.json"
_PLAN_SCHEMA_PATH = _SCHEMA_DIRECTORY / "generation-plan.schema.json"
_PLAN_REVIEW_SCHEMA_PATH = _SCHEMA_DIRECTORY / "generation-plan-review.schema.json"
_RESULTS_PROPOSAL_SCHEMA_PATH = _SCHEMA_DIRECTORY / "generation-results-proposal.schema.json"
_RESULTS_REVIEW_SCHEMA_PATH = _SCHEMA_DIRECTORY / "generation-results-review.schema.json"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SLOT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_TASK_ID_RE = re.compile(r"^task\.(\d{4})$")
_ADAPTER_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_ADAPTER_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CONTROLLER_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")

_TASK_KINDS = frozenset(
    {"identity-try-on", "product-still", "background-still", "reference-guided-still"}
)
_REFERENCE_ROLES = frozenset({"identity", "garment", "product", "background", "reference", "audio"})
_IMAGE_MEDIA = frozenset({"image/jpeg", "image/png", "image/webp"})
_AUDIO_MEDIA = frozenset({"audio/wav", "audio/mpeg", "audio/mp4", "audio/x-matroska"})
_ALL_MEDIA = _IMAGE_MEDIA | _AUDIO_MEDIA
_KIND_REQUIRED_ROLES: Mapping[str, frozenset[str]] = {
    "identity-try-on": frozenset({"identity", "garment"}),
    "product-still": frozenset({"product"}),
    "background-still": frozenset({"background"}),
    "reference-guided-still": frozenset({"reference"}),
}
_KIND_RESULT_CONFIRMATIONS: Mapping[str, tuple[str, ...]] = {
    "identity-try-on": ("identity_confirmed", "garment_confirmed", "pose_confirmed", "render_ready_confirmed", "rights_confirmed"),
    "product-still": ("product_confirmed", "render_ready_confirmed", "rights_confirmed"),
    "background-still": ("background_confirmed", "render_ready_confirmed", "rights_confirmed"),
    "reference-guided-still": (
        "identity_confirmed",
        "garment_confirmed",
        "product_confirmed",
        "background_confirmed",
        "pose_confirmed",
        "render_ready_confirmed",
        "rights_confirmed",
    ),
}


def _invalid(message: str) -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(rrv_runtime.ERR_INVALID_ARGUMENT, message)


def _tool_error(message: str) -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(rrv_runtime.ERR_TOOL_EXECUTION, message)


def _safe_exception(exc: BaseException) -> rrv_runtime.RRVError:
    if isinstance(exc, rrv_runtime.RRVError):
        return exc
    return _tool_error("local generation asset operation failed")


def _canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(rrv_runtime.stable_json_dumps(value, indent=None).encode("utf-8")).hexdigest()


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_relative_path(value: Any) -> bool:
    return rrv_assets._relative_path_parts(value) is not None


def _valid_direct_child(value: Any) -> bool:
    try:
        rrv_assets._direct_child_name(value, "name")
    except rrv_runtime.RRVError:
        return False
    return True


def _valid_output_slot(value: Any) -> bool:
    """Require a safe ID that can also become an exact portable filename."""

    return isinstance(value, str) and _SLOT_RE.fullmatch(value) is not None and rrv_assets._portable_path_component(value)


def _same_direct_child(left: Any, right: Any) -> bool:
    """Compare direct-child names with the host filesystem's case rules."""

    return isinstance(left, str) and isinstance(right, str) and os.path.normcase(left) == os.path.normcase(right)


def _same_directory_identity(left: Any, right: Any) -> bool:
    """Detect distinct lexical pack names that resolve to one directory."""

    left_device, left_inode = getattr(left, "device", None), getattr(left, "inode", None)
    right_device, right_inode = getattr(right, "device", None), getattr(right, "inode", None)
    return (
        isinstance(left_device, int)
        and isinstance(left_inode, int)
        and isinstance(right_device, int)
        and isinstance(right_inode, int)
        and (left_device, left_inode) == (right_device, right_inode)
    )


def _nonfinite_errors(value: Any) -> list[str]:
    errors: list[str] = []
    rrv_assets._find_nonfinite(value, "$", errors)
    return errors


def _schema_errors(data: Any, path: Path, label: str) -> list[str]:
    return rrv_assets._schema_errors(data, path, label)


def _unique_errors(errors: Sequence[str]) -> list[str]:
    return rrv_assets._unique_errors(errors)


def _artifact_errors(value: Any, path: str) -> list[str]:
    if not isinstance(value, Mapping):
        return [f"{path}: artifact"]
    errors: list[str] = []
    if not _valid_relative_path(value.get("path")):
        errors.append(f"{path}.path: normalized_relative_path")
    digest = value.get("sha256")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        errors.append(f"{path}.sha256: sha256")
    return errors


def _inventory_errors(
    inventory: Any,
    *,
    pack_name: Any | None,
    allowed_media: frozenset[str],
    path: str,
    require_source_path: bool = True,
) -> list[str]:
    """Validate an inventory packet without reading a source file.

    The Plan uses opaque entries (no ``source_path``); the result proposal
    retains its output target stems, so it uses the stricter source-path form.
    """

    errors: list[str] = []
    if not isinstance(inventory, list):
        return [f"{path}: array"]
    if len(inventory) > rrv_assets.MAX_ENTRIES:
        errors.append(f"{path}: max_entries")
    seen_ids: set[str] = set()
    source_paths: list[str] = []
    for index, item in enumerate(inventory, start=1):
        item_path = f"{path}[{index - 1}]"
        if not isinstance(item, Mapping):
            errors.append(f"{item_path}: object")
            continue
        asset_id = item.get("asset_id")
        if asset_id != f"asset.{index:04d}" or not isinstance(asset_id, str) or asset_id in seen_ids:
            errors.append(f"{item_path}.asset_id: stable_sequence")
        elif isinstance(asset_id, str):
            seen_ids.add(asset_id)
        if require_source_path:
            source_path = item.get("source_path")
            parts = rrv_assets._relative_path_parts(source_path)
            if parts is None or len(parts) != 2 or parts[0] != pack_name or not _valid_direct_child(parts[-1]):
                errors.append(f"{item_path}.source_path: direct_pack_file")
            elif isinstance(source_path, str):
                source_paths.append(source_path)
        digest = item.get("sha256")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            errors.append(f"{item_path}.sha256: sha256")
        size = item.get("size_bytes")
        if not _is_int(size) or not 1 <= int(size) <= rrv_assets.MAX_FILE_BYTES:
            errors.append(f"{item_path}.size_bytes: bounded_positive_integer")
        media_type = item.get("media_type")
        if media_type not in allowed_media:
            errors.append(f"{item_path}.media_type: allowed_media")
        facts = item.get("facts")
        if not isinstance(facts, Mapping):
            errors.append(f"{item_path}.facts: object")
            continue
        kind = facts.get("kind")
        if kind == "image":
            width, height, pixels = facts.get("width"), facts.get("height"), facts.get("pixels")
            if not all(_is_int(number) for number in (width, height, pixels)):
                errors.append(f"{item_path}.facts: image_facts")
            elif (
                int(width) < 1
                or int(height) < 1
                or int(width) > rrv_assets.MAX_IMAGE_EDGE
                or int(height) > rrv_assets.MAX_IMAGE_EDGE
                or int(width) * int(height) != int(pixels)
                or int(pixels) > rrv_assets.MAX_IMAGE_PIXELS
            ):
                errors.append(f"{item_path}.facts: image_bounds")
            if media_type not in _IMAGE_MEDIA:
                errors.append(f"{item_path}.facts: image_media")
        elif kind == "audio":
            duration = facts.get("duration_seconds")
            streams = facts.get("audio_stream_count")
            if (
                isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or not math.isfinite(float(duration))
                or not 0 < float(duration) <= rrv_assets.MAX_AUDIO_SECONDS
                or not _is_int(streams)
                or not 1 <= int(streams) <= 64
                or facts.get("video_stream_count") != 0
            ):
                errors.append(f"{item_path}.facts: audio_facts")
            if media_type not in _AUDIO_MEDIA:
                errors.append(f"{item_path}.facts: audio_media")
        else:
            errors.append(f"{item_path}.facts.kind: allowed_kind")
    if require_source_path and (source_paths != sorted(source_paths) or len(source_paths) != len(set(source_paths))):
        errors.append(f"{path}: stable_source_order")
    return errors


def _opaque_inventory(inventory: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Publish stable media facts without any reference-pack filename/path."""

    opaque: list[dict[str, Any]] = []
    for item in inventory:
        if not isinstance(item, Mapping):
            raise _invalid("local generation inventory is invalid")
        facts = item.get("facts")
        if not isinstance(facts, Mapping):
            raise _invalid("local generation inventory is invalid")
        asset_id = item.get("asset_id")
        digest = item.get("sha256")
        size_bytes = item.get("size_bytes")
        media_type = item.get("media_type")
        if (
            not isinstance(asset_id, str)
            or not isinstance(digest, str)
            or not _is_int(size_bytes)
            or not isinstance(media_type, str)
        ):
            raise _invalid("local generation inventory is invalid")
        opaque.append(
            {
                "asset_id": asset_id,
                "sha256": digest,
                "size_bytes": size_bytes,
                "media_type": media_type,
                "facts": dict(facts),
            }
        )
    return opaque


def _adapter_errors(data: Mapping[str, Any], path: str = "$") -> list[str]:
    errors: list[str] = []
    adapter_id = data.get("adapter_id")
    adapter_version = data.get("adapter_version")
    profile = data.get("execution_profile")
    label = data.get("controller_label")
    if not isinstance(adapter_id, str) or not _ADAPTER_ID_RE.fullmatch(adapter_id):
        errors.append(f"{path}.adapter_id: safe_slug")
    if not isinstance(adapter_version, str) or not _ADAPTER_VERSION_RE.fullmatch(adapter_version):
        errors.append(f"{path}.adapter_version: bounded_token")
    if profile == "controller-managed":
        if not isinstance(label, str) or not _CONTROLLER_LABEL_RE.fullmatch(label):
            errors.append(f"{path}.controller_label: bounded_label")
    elif label is not None:
        errors.append(f"{path}.controller_label: local_file_drop_forbids_label")
    return errors


def _privacy_errors(data: Mapping[str, Any], path: str = "$") -> list[str]:
    profile = data.get("privacy_profile")
    consent = data.get("cloud_upload_confirmed")
    execution_profile = data.get("execution_profile")
    if profile == "controller-cloud" and consent is not True:
        return [f"{path}.cloud_upload_confirmed: required_true"]
    # Plan Review deliberately carries the consent fact but not an executor
    # declaration; Request and Plan carry both and must never authorize a
    # cloud upload through a local-file-drop route.
    if profile == "controller-cloud" and execution_profile is not None and execution_profile != "controller-managed":
        return [f"{path}.execution_profile: cloud_requires_controller_managed"]
    if profile == "local-only" and consent is not False and consent is not None:
        return [f"{path}.cloud_upload_confirmed: local_false"]
    return []


def _request_semantic_errors(data: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    errors.extend(_adapter_errors(data))
    errors.extend(_privacy_errors(data))
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return errors
    targets: set[str] = set()
    for index, task in enumerate(tasks):
        task_path = f"$.tasks[{index}]"
        if not isinstance(task, Mapping):
            continue
        target = task.get("target_slot_id")
        if not _valid_output_slot(target) or target in targets:
            errors.append(f"{task_path}.target_slot_id: unique_safe_slot")
        elif isinstance(target, str):
            targets.add(target)
        kind = task.get("kind")
        if kind not in _TASK_KINDS:
            errors.append(f"{task_path}.kind: known_kind")
        passthrough, omit = task.get("passthrough"), task.get("omit")
        if not isinstance(passthrough, bool) or not isinstance(omit, bool) or (passthrough and omit):
            errors.append(f"{task_path}: mutually_exclusive_passthrough_omit")
        instructions = task.get("instructions")
        if not isinstance(instructions, str) or len(instructions) > 2000:
            errors.append(f"{task_path}.instructions: bounded_text")
        elif not omit and not instructions.strip():
            errors.append(f"{task_path}.instructions: nonempty_for_used_task")
        references = task.get("references")
        if not isinstance(references, list):
            continue
        seen_references: set[tuple[str, str]] = set()
        roles: set[str] = set()
        for reference_index, reference in enumerate(references):
            reference_path = f"{task_path}.references[{reference_index}]"
            if not isinstance(reference, Mapping):
                continue
            filename, role = reference.get("source_filename"), reference.get("role")
            if not _valid_direct_child(filename):
                errors.append(f"{reference_path}.source_filename: direct_child")
            if role not in _REFERENCE_ROLES:
                errors.append(f"{reference_path}.role: known_role")
            if isinstance(filename, str) and isinstance(role, str):
                key = (filename, role)
                if key in seen_references:
                    errors.append(f"{reference_path}: unique_reference_role")
                seen_references.add(key)
                roles.add(role)
        # A generation task only consumes still references.  The scanner is
        # still the authority for the actual media kind below, but an explicit
        # audio role must never be used to smuggle an audio reference into a
        # generated still request.
        if passthrough is False and omit is False and "audio" in roles:
            errors.append(f"{task_path}.references: generated_stills_forbid_audio")
        if omit:
            if references:
                errors.append(f"{task_path}.references: omit_has_no_references")
        elif passthrough:
            if len(references) != 1:
                errors.append(f"{task_path}.references: passthrough_exactly_one")
        elif kind in _KIND_REQUIRED_ROLES and not _KIND_REQUIRED_ROLES[str(kind)].issubset(roles):
            errors.append(f"{task_path}.references: kind_required_roles")
    return errors


def _plan_task_errors(tasks: Any, *, inventory: Sequence[Mapping[str, Any]], path: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(tasks, list):
        return [f"{path}: array"]
    inventory_by_id = {
        item.get("asset_id"): item
        for item in inventory
        if isinstance(item, Mapping) and isinstance(item.get("asset_id"), str)
    }
    inventory_ids = set(inventory_by_id)
    seen_tasks: set[str] = set()
    targets: list[str] = []
    for index, task in enumerate(tasks, start=1):
        task_path = f"{path}[{index - 1}]"
        if not isinstance(task, Mapping):
            continue
        task_id = task.get("task_id")
        if task_id != f"task.{index:04d}" or not isinstance(task_id, str) or task_id in seen_tasks:
            errors.append(f"{task_path}.task_id: stable_sequence")
        elif isinstance(task_id, str):
            seen_tasks.add(task_id)
        target = task.get("target_slot_id")
        if not _valid_output_slot(target):
            errors.append(f"{task_path}.target_slot_id: safe_slot")
        elif target in targets:
            errors.append(f"{task_path}.target_slot_id: unique")
        else:
            targets.append(target)
        if task.get("kind") not in _TASK_KINDS:
            errors.append(f"{task_path}.kind: known_kind")
        passthrough, omit, required = task.get("passthrough"), task.get("omit"), task.get("required")
        if not isinstance(passthrough, bool) or not isinstance(omit, bool) or not isinstance(required, bool) or (passthrough and omit):
            errors.append(f"{task_path}: task_flags")
        if omit and required is True:
            errors.append(f"{task_path}.omit: required_slot")
        references = task.get("references")
        if not isinstance(references, list):
            continue
        reference_keys: set[tuple[str, str]] = set()
        for reference_index, reference in enumerate(references):
            reference_path = f"{task_path}.references[{reference_index}]"
            if not isinstance(reference, Mapping):
                continue
            role, asset_id = reference.get("role"), reference.get("asset_id")
            if role not in _REFERENCE_ROLES:
                errors.append(f"{reference_path}.role: known_role")
            if not isinstance(asset_id, str) or asset_id not in inventory_ids:
                errors.append(f"{reference_path}.asset_id: known_inventory")
            if isinstance(role, str) and isinstance(asset_id, str):
                key = (role, asset_id)
                if key in reference_keys:
                    errors.append(f"{reference_path}: unique_reference")
                reference_keys.add(key)
                source = inventory_by_id.get(asset_id)
                if passthrough is False and omit is False:
                    facts = source.get("facts") if isinstance(source, Mapping) else None
                    if (
                        not isinstance(source, Mapping)
                        or source.get("media_type") not in _IMAGE_MEDIA
                        or not isinstance(facts, Mapping)
                        or facts.get("kind") != "image"
                    ):
                        errors.append(f"{reference_path}: generated_still_requires_image")
                    if role == "audio":
                        errors.append(f"{reference_path}.role: generated_stills_forbid_audio")
        if omit and references:
            errors.append(f"{task_path}.references: omit_has_no_references")
        if passthrough and len(references) != 1:
            errors.append(f"{task_path}.references: passthrough_exactly_one")
    if targets != sorted(targets):
        errors.append(f"{path}: stable_target_order")
    return errors


def _plan_semantic_errors(data: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    errors.extend(_adapter_errors(data))
    errors.extend(_privacy_errors(data))
    for key in ("template_path", "request_path"):
        if not _valid_relative_path(data.get(key)):
            errors.append(f"$.{key}: normalized_relative_path")
    if not _valid_direct_child(data.get("reference_pack")):
        errors.append("$.reference_pack: direct_child")
    inventory = data.get("reference_inventory")
    errors.extend(
        _inventory_errors(
            inventory,
            pack_name=None,
            allowed_media=_ALL_MEDIA,
            path="$.reference_inventory",
            require_source_path=False,
        )
    )
    if isinstance(inventory, list):
        try:
            if data.get("reference_inventory_sha256") != _canonical_json_sha256(inventory):
                errors.append("$.reference_inventory_sha256: canonical_inventory")
        except (TypeError, ValueError):
            errors.append("$.reference_inventory_sha256: canonical_inventory")
        errors.extend(_plan_task_errors(data.get("tasks"), inventory=inventory, path="$.tasks"))
    else:
        errors.extend(_plan_task_errors(data.get("tasks"), inventory=(), path="$.tasks"))
    evidence = data.get("evidence")
    if isinstance(evidence, Mapping):
        errors.extend(_artifact_errors(evidence.get("input_contact_sheet"), "$.evidence.input_contact_sheet"))
    else:
        errors.append("$.evidence: object")
    return errors


def _plan_review_semantic_errors(data: Mapping[str, Any]) -> list[str]:
    errors = _privacy_errors(data)
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return errors
    seen: set[str] = set()
    targets: list[str] = []
    approved = data.get("decision") == "approved"
    if approved and any(data.get(key) is not True for key in ("input_contact_sheet_reviewed", "request_reviewed", "execution_profile_confirmed")):
        errors.append("$: approved_global_confirmations")
    for index, task in enumerate(tasks, start=1):
        task_path = f"$.tasks[{index - 1}]"
        if not isinstance(task, Mapping):
            continue
        task_id = task.get("task_id")
        if task_id != f"task.{index:04d}" or not isinstance(task_id, str) or task_id in seen:
            errors.append(f"{task_path}.task_id: stable_sequence")
        elif isinstance(task_id, str):
            seen.add(task_id)
        target = task.get("target_slot_id")
        if not _valid_output_slot(target):
            errors.append(f"{task_path}.target_slot_id: safe_slot")
        else:
            targets.append(target)
        if approved and (
            task.get("decision") != "accept"
            or any(task.get(key) is not True for key in ("references_confirmed", "instruction_scope_confirmed", "rights_confirmed"))
        ):
            errors.append(f"{task_path}: approved_task_confirmations")
    if len(targets) != len(set(targets)) or targets != sorted(targets):
        errors.append("$.tasks: stable_target_order")
    return errors


def _results_proposal_semantic_errors(data: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("plan_path", "plan_review_path", "template_path", "request_path"):
        if not _valid_relative_path(data.get(key)):
            errors.append(f"$.{key}: normalized_relative_path")
    for key in ("reference_pack", "result_pack"):
        if not _valid_direct_child(data.get(key)):
            errors.append(f"$.{key}: direct_child")
    inventory = data.get("result_inventory")
    errors.extend(_inventory_errors(inventory, pack_name=data.get("result_pack"), allowed_media=_IMAGE_MEDIA, path="$.result_inventory"))
    if isinstance(inventory, list):
        try:
            if data.get("result_inventory_sha256") != _canonical_json_sha256(inventory):
                errors.append("$.result_inventory_sha256: canonical_inventory")
        except (TypeError, ValueError):
            errors.append("$.result_inventory_sha256: canonical_inventory")
    inventory_ids = {
        item.get("asset_id")
        for item in inventory
        if isinstance(inventory, list) and isinstance(item, Mapping) and isinstance(item.get("asset_id"), str)
    }
    tasks = data.get("tasks")
    if isinstance(tasks, list):
        seen: set[str] = set()
        targets: list[str] = []
        result_ids: set[str] = set()
        for index, task in enumerate(tasks, start=1):
            task_path = f"$.tasks[{index - 1}]"
            if not isinstance(task, Mapping):
                continue
            task_id = task.get("task_id")
            if task_id != f"task.{index:04d}" or not isinstance(task_id, str) or task_id in seen:
                errors.append(f"{task_path}.task_id: stable_sequence")
            elif isinstance(task_id, str):
                seen.add(task_id)
            target = task.get("target_slot_id")
            if not _valid_output_slot(target):
                errors.append(f"{task_path}.target_slot_id: safe_slot")
            else:
                targets.append(target)
            passthrough, omit = task.get("passthrough"), task.get("omit")
            asset_id = task.get("result_asset_id")
            if passthrough is True or omit is True:
                if asset_id is not None:
                    errors.append(f"{task_path}.result_asset_id: null_for_non_generated")
            elif not isinstance(asset_id, str) or asset_id not in inventory_ids:
                errors.append(f"{task_path}.result_asset_id: known_result_inventory")
            elif asset_id in result_ids:
                errors.append(f"{task_path}.result_asset_id: unique")
            else:
                result_ids.add(asset_id)
        if len(targets) != len(set(targets)) or targets != sorted(targets):
            errors.append("$.tasks: stable_target_order")
        if result_ids != inventory_ids:
            errors.append("$.result_inventory: exact_task_results")
    evidence = data.get("evidence")
    if isinstance(evidence, Mapping):
        errors.extend(_artifact_errors(evidence.get("comparison_contact_sheet"), "$.evidence.comparison_contact_sheet"))
    else:
        errors.append("$.evidence: object")
    return errors


def _results_review_semantic_errors(data: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return errors
    approved = data.get("decision") == "approved"
    if approved and data.get("comparison_contact_sheet_reviewed") is not True:
        errors.append("$.comparison_contact_sheet_reviewed: required_for_approval")
    seen: set[str] = set()
    targets: list[str] = []
    for index, task in enumerate(tasks, start=1):
        task_path = f"$.tasks[{index - 1}]"
        if not isinstance(task, Mapping):
            continue
        task_id = task.get("task_id")
        if task_id != f"task.{index:04d}" or not isinstance(task_id, str) or task_id in seen:
            errors.append(f"{task_path}.task_id: stable_sequence")
        elif isinstance(task_id, str):
            seen.add(task_id)
        target = task.get("target_slot_id")
        if not _valid_output_slot(target):
            errors.append(f"{task_path}.target_slot_id: safe_slot")
        else:
            targets.append(target)
        if approved and task.get("decision") != "accept":
            errors.append(f"{task_path}.decision: accept_for_approval")
    if len(targets) != len(set(targets)) or targets != sorted(targets):
        errors.append("$.tasks: stable_target_order")
    return errors


def validate_generation_request_data(data: Any) -> list[str]:
    """Validate a private Generation Request without touching project files."""

    errors = _nonfinite_errors(data)
    errors.extend(_schema_errors(data, _REQUEST_SCHEMA_PATH, "generation request"))
    if isinstance(data, Mapping):
        try:
            errors.extend(_request_semantic_errors(data))
        except Exception:
            errors.append("$: semantic.invalid")
    return _unique_errors(errors)


def validate_generation_plan_data(data: Any) -> list[str]:
    """Validate a local Generation Plan without touching project files."""

    errors = _nonfinite_errors(data)
    errors.extend(_schema_errors(data, _PLAN_SCHEMA_PATH, "generation plan"))
    if isinstance(data, Mapping):
        try:
            errors.extend(_plan_semantic_errors(data))
        except Exception:
            errors.append("$: semantic.invalid")
    return _unique_errors(errors)


def validate_generation_plan_review_data(data: Any) -> list[str]:
    """Validate a local Generation Plan Review without touching project files."""

    errors = _nonfinite_errors(data)
    errors.extend(_schema_errors(data, _PLAN_REVIEW_SCHEMA_PATH, "generation plan review"))
    if isinstance(data, Mapping):
        try:
            errors.extend(_plan_review_semantic_errors(data))
        except Exception:
            errors.append("$: semantic.invalid")
    return _unique_errors(errors)


def validate_generation_results_proposal_data(data: Any) -> list[str]:
    """Validate a local Generation Results Proposal without touching files."""

    errors = _nonfinite_errors(data)
    errors.extend(_schema_errors(data, _RESULTS_PROPOSAL_SCHEMA_PATH, "generation results proposal"))
    if isinstance(data, Mapping):
        try:
            errors.extend(_results_proposal_semantic_errors(data))
        except Exception:
            errors.append("$: semantic.invalid")
    return _unique_errors(errors)


def validate_generation_results_review_data(data: Any) -> list[str]:
    """Validate a local Generation Results Review without touching files."""

    errors = _nonfinite_errors(data)
    errors.extend(_schema_errors(data, _RESULTS_REVIEW_SCHEMA_PATH, "generation results review"))
    if isinstance(data, Mapping):
        try:
            errors.extend(_results_review_semantic_errors(data))
        except Exception:
            errors.append("$: semantic.invalid")
    return _unique_errors(errors)


def _raise_validation(label: str, errors: Sequence[str]) -> None:
    del errors
    raise _invalid(f"{label} did not pass validation")


def _validate_template_snapshot(snapshot: Any) -> Mapping[str, Any]:
    template = rrv_assets._validate_template_snapshot(snapshot)
    # A Template slot becomes an exact output filename during assembly.  Keep
    # every slot portable before a plan is created instead of discovering a
    # Win32 device/trailing-component rejection after a reviewed result drop.
    _template_slots(template)
    return template


def _template_slots(template: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    slots = template.get("slots")
    if not isinstance(slots, list):
        raise _invalid("Template did not pass validation")
    result: dict[str, Mapping[str, Any]] = {}
    for slot in slots:
        if not isinstance(slot, Mapping) or not isinstance(slot.get("id"), str):
            raise _invalid("Template did not pass validation")
        if slot["id"] in result or not rrv_assets._portable_path_component(slot["id"]):
            raise _invalid("Template did not pass validation")
        result[slot["id"]] = slot
    return result


def _inventory_by_filename(inventory: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for item in inventory:
        source_path = item.get("source_path")
        if not isinstance(source_path, str):
            raise _invalid("reference inventory is invalid")
        filename = PurePosixPath(source_path).name
        if not filename or filename in result:
            raise _invalid("reference inventory is invalid")
        result[filename] = item
    return result


def _inventory_by_asset_id(inventory: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for item in inventory:
        asset_id = item.get("asset_id")
        if not isinstance(asset_id, str) or asset_id in result:
            raise _invalid("asset inventory is invalid")
        result[asset_id] = item
    return result


def _request_plan_tasks(
    template: Mapping[str, Any],
    request: Mapping[str, Any],
    inventory: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Bind private source filenames to opaque inventory identifiers once."""

    slots = _template_slots(template)
    by_filename = _inventory_by_filename(inventory)
    by_asset_id = _inventory_by_asset_id(inventory)
    request_tasks = request.get("tasks")
    if not isinstance(request_tasks, list):
        raise _invalid("Generation Request did not pass validation")
    bound_by_target: dict[str, dict[str, Any]] = {}
    for request_task in request_tasks:
        if not isinstance(request_task, Mapping):
            raise _invalid("Generation Request did not pass validation")
        target = request_task.get("target_slot_id")
        if not isinstance(target, str) or target not in slots or target in bound_by_target:
            raise _invalid("Generation Request does not map Template slots safely")
        kind = request_task.get("kind")
        passthrough = request_task.get("passthrough")
        omit = request_task.get("omit")
        if kind not in _TASK_KINDS or not isinstance(passthrough, bool) or not isinstance(omit, bool) or (passthrough and omit):
            raise _invalid("Generation Request task is invalid")
        references_value = request_task.get("references")
        if not isinstance(references_value, list):
            raise _invalid("Generation Request task is invalid")
        references: list[dict[str, str]] = []
        roles: set[str] = set()
        for reference in references_value:
            if not isinstance(reference, Mapping):
                raise _invalid("Generation Request task is invalid")
            filename, role = reference.get("source_filename"), reference.get("role")
            item = by_filename.get(filename) if isinstance(filename, str) else None
            asset_id = item.get("asset_id") if isinstance(item, Mapping) else None
            if role not in _REFERENCE_ROLES or not isinstance(asset_id, str):
                raise _invalid("Generation Request reference does not match the local reference pack")
            references.append({"role": str(role), "asset_id": asset_id})
            roles.add(str(role))
        slot = slots[target]
        required = slot.get("required") is True
        accepted = slot.get("accepted_media")
        if not isinstance(accepted, list):
            raise _invalid("Template did not pass validation")
        if omit:
            if required or references:
                raise _invalid("only an optional Template slot may be omitted")
        elif passthrough:
            if len(references) != 1:
                raise _invalid("passthrough tasks require exactly one reference")
            source = by_asset_id.get(references[0]["asset_id"])
            if source is None:
                raise _invalid("passthrough reference is invalid")
            media_type = source.get("media_type")
            facts = source.get("facts")
            if not isinstance(facts, Mapping):
                raise _invalid("passthrough reference is invalid")
            if facts.get("kind") == "image":
                if (
                    media_type not in _IMAGE_MEDIA
                    or "image/png" not in accepted
                    or references[0]["role"] == "audio"
                ):
                    raise _invalid("Template slot cannot receive a sanitized image passthrough")
            elif facts.get("kind") == "audio":
                if (
                    media_type not in _AUDIO_MEDIA
                    or media_type not in accepted
                    or references[0]["role"] != "audio"
                ):
                    raise _invalid("Template slot cannot receive the requested audio passthrough")
            else:
                raise _invalid("passthrough reference is invalid")
        else:
            # ``type`` in Template IR is a semantic label (for example
            # ``identity``, ``garment`` or ``product-image``), not a media
            # declaration.  The generated controller output is always
            # sanitized PNG, so accepted_media is the authoritative
            # compatibility boundary here.
            if "image/png" not in accepted:
                raise _invalid("generated still slots must accept sanitized PNG output")
            required_roles = _KIND_REQUIRED_ROLES.get(str(kind))
            if required_roles is None or not required_roles.issubset(roles):
                raise _invalid("Generation Request task lacks required references")
            for reference in references:
                source = by_asset_id.get(reference["asset_id"])
                facts = source.get("facts") if isinstance(source, Mapping) else None
                if (
                    not isinstance(source, Mapping)
                    or source.get("media_type") not in _IMAGE_MEDIA
                    or not isinstance(facts, Mapping)
                    or facts.get("kind") != "image"
                ):
                    raise _invalid("generated still references must be static images")
        bound_by_target[target] = {
            "target_slot_id": target,
            "required": required,
            "kind": str(kind),
            "passthrough": passthrough,
            "omit": omit,
            "references": references,
        }
    required_slots = {slot_id for slot_id, slot in slots.items() if slot.get("required") is True}
    if not required_slots.issubset(bound_by_target):
        raise _invalid("Generation Request must cover every required Template slot exactly once")
    ordered = [bound_by_target[target] for target in sorted(bound_by_target)]
    for index, task in enumerate(ordered, start=1):
        task["task_id"] = f"task.{index:04d}"
    return [
        {
            "task_id": task["task_id"],
            "target_slot_id": task["target_slot_id"],
            "required": task["required"],
            "kind": task["kind"],
            "passthrough": task["passthrough"],
            "omit": task["omit"],
            "references": list(task["references"]),
        }
        for task in ordered
    ]


def _plan_review_template(plan_sha256: str, plan: Mapping[str, Any]) -> dict[str, Any]:
    raw_tasks = plan.get("tasks")
    if not isinstance(raw_tasks, list):
        raise _invalid("generated Generation Plan is invalid")
    tasks: list[dict[str, Any]] = []
    for task in raw_tasks:
        if not isinstance(task, Mapping):
            raise _invalid("generated Generation Plan is invalid")
        tasks.append(
            {
                "task_id": task.get("task_id"),
                "target_slot_id": task.get("target_slot_id"),
                "decision": "pending",
                "references_confirmed": False,
                "instruction_scope_confirmed": False,
                "rights_confirmed": False,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "plan_sha256": plan_sha256,
        "privacy_profile": plan.get("privacy_profile"),
        "cloud_upload_confirmed": plan.get("cloud_upload_confirmed"),
        "decision": "pending",
        "input_contact_sheet_reviewed": False,
        "request_reviewed": False,
        "execution_profile_confirmed": False,
        "tasks": tasks,
    }


def _approved_plan_review(
    plan: Mapping[str, Any], plan_sha256: str, review: Mapping[str, Any]
) -> None:
    if review.get("plan_sha256") != plan_sha256:
        raise _invalid("Generation Plan Review does not bind the exact Generation Plan")
    if review.get("privacy_profile") != plan.get("privacy_profile") or review.get("cloud_upload_confirmed") != plan.get("cloud_upload_confirmed"):
        raise _invalid("Generation Plan Review does not bind the approved privacy consent")
    if review.get("decision") != "approved":
        raise _invalid("Generation Plan Review must be approved before result proposal")
    if any(review.get(key) is not True for key in ("input_contact_sheet_reviewed", "request_reviewed", "execution_profile_confirmed")):
        raise _invalid("approved Generation Plan Review lacks required confirmations")
    plan_tasks = plan.get("tasks")
    review_tasks = review.get("tasks")
    if not isinstance(plan_tasks, list) or not isinstance(review_tasks, list) or len(plan_tasks) != len(review_tasks):
        raise _invalid("Generation Plan Review tasks do not match the Generation Plan")
    for plan_task, review_task in zip(plan_tasks, review_tasks):
        if not isinstance(plan_task, Mapping) or not isinstance(review_task, Mapping):
            raise _invalid("Generation Plan Review tasks do not match the Generation Plan")
        if (
            review_task.get("task_id") != plan_task.get("task_id")
            or review_task.get("target_slot_id") != plan_task.get("target_slot_id")
            or review_task.get("decision") != "accept"
            or any(
                review_task.get(key) is not True
                for key in ("references_confirmed", "instruction_scope_confirmed", "rights_confirmed")
            )
        ):
            raise _invalid("Generation Plan Review tasks are not fully approved")


def _validate_evidence_artifact(
    root: Path,
    root_identity: Any,
    owner_path: str,
    artifact: Any,
    *,
    expected_filename: str,
    label: str,
) -> None:
    if not isinstance(artifact, Mapping):
        raise _invalid(f"{label} evidence is invalid")
    relative_path = artifact.get("path")
    expected_hash = artifact.get("sha256")
    owner_parts = rrv_assets._relative_path_parts(owner_path)
    artifact_parts = rrv_assets._relative_path_parts(relative_path)
    if (
        owner_parts is None
        or artifact_parts is None
        or artifact_parts != (*owner_parts[:-1], expected_filename)
        or not isinstance(expected_hash, str)
        or not _SHA256_RE.fullmatch(expected_hash)
    ):
        raise _invalid(f"{label} evidence is invalid")
    try:
        _, raw = rrv_assets._read_project_file_bytes(
            root,
            root_identity,
            relative_path,
            label=f"{label} evidence",
            maximum_bytes=rrv_assets.MAX_CONTACT_SHEET_BYTES,
        )
    except rrv_runtime.RRVError as exc:
        raise _invalid(f"{label} evidence is invalid") from exc
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise _invalid(f"{label} evidence hash does not match")


def _create_input_contact_sheet(
    stage: Any,
    output: Path,
    scanned: Sequence[Any],
    inventory: Sequence[Mapping[str, Any]],
    tasks: Sequence[Mapping[str, Any]],
) -> None:
    """Render reviewed input evidence without source filenames or prompts."""

    Image, ImageDraw = rrv_assets._load_pillow()
    assets_by_id = _scanned_by_asset_id(scanned, inventory)
    bindings: dict[str, list[str]] = {}
    for task in tasks:
        if not isinstance(task, Mapping):
            raise _invalid("Generation Plan task is invalid")
        task_id = task.get("task_id")
        references = task.get("references")
        if not isinstance(task_id, str) or not isinstance(references, list):
            raise _invalid("Generation Plan task is invalid")
        for reference in references:
            if not isinstance(reference, Mapping):
                raise _invalid("Generation Plan task is invalid")
            asset_id, role = reference.get("asset_id"), reference.get("role")
            if not isinstance(asset_id, str) or not isinstance(role, str):
                raise _invalid("Generation Plan task is invalid")
            bindings.setdefault(asset_id, []).append(f"{task_id} {role.upper()}")
    columns, card_width, card_height = 4, 300, 230
    rows = max(1, math.ceil(len(inventory) / columns))
    canvas = Image.new("RGB", (columns * card_width, rows * card_height), (245, 247, 250))
    draw = ImageDraw.Draw(canvas)
    try:
        if not inventory:
            draw.text((18, 18), "NO APPROVED REFERENCES", fill=(55, 65, 81))
        for index, item in enumerate(inventory):
            if not isinstance(item, Mapping):
                raise _invalid("local generation inventory is invalid")
            asset_id = item.get("asset_id")
            facts = item.get("facts")
            if not isinstance(asset_id, str) or not isinstance(facts, Mapping):
                raise _invalid("local generation inventory is invalid")
            col, row = index % columns, index // columns
            x, y = col * card_width, row * card_height
            draw.rectangle((x + 4, y + 4, x + card_width - 5, y + card_height - 5), fill="white", outline=(185, 193, 204))
            thumbnail = None
            try:
                source = assets_by_id.get(asset_id)
                if facts.get("kind") == "image" and source is not None:
                    thumbnail = rrv_assets._thumbnail_for_asset(source, maximum=(card_width - 20, 145))
                if thumbnail is not None:
                    canvas.paste(thumbnail, (x + (card_width - thumbnail.width) // 2, y + 10))
                else:
                    draw.rectangle((x + 18, y + 20, x + card_width - 18, y + 145), fill=(227, 232, 240))
                    draw.text((x + 110, y + 72), "AUDIO", fill=(51, 65, 85))
            finally:
                if thumbnail is not None:
                    thumbnail.close()
            if facts.get("kind") == "image":
                technical = f"{item.get('media_type')} {facts.get('width')}x{facts.get('height')}"
            else:
                technical = f"{item.get('media_type')} {facts.get('duration_seconds')}s"
            labels = bindings.get(asset_id, [])
            mapping = " | ".join(labels[:2]) if labels else "UNBOUND"
            if len(labels) > 2:
                mapping = f"{mapping} +{len(labels) - 2}"
            draw.text((x + 10, y + 162), asset_id, fill=(17, 24, 39))
            draw.text((x + 10, y + 180), technical[:42], fill=(71, 85, 105))
            draw.text((x + 10, y + 202), mapping[:42], fill=(71, 85, 105))
        with rrv_propose._open_stage_output_file(stage, output, "generation input contact sheet") as handle:
            canvas.save(handle, format="PNG", optimize=False, compress_level=9)
        rrv_propose._assert_stage_regular_file(stage, output, "generation input contact sheet")
    except rrv_runtime.RRVError:
        raise
    except OSError as exc:
        raise _tool_error("could not write local generation input contact sheet") from exc
    finally:
        canvas.close()


def _scanned_by_asset_id(scanned: Sequence[Any], inventory: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if len(scanned) != len(inventory):
        raise _invalid("scanned asset inventory is invalid")
    result: dict[str, Any] = {}
    for scanned_asset, item in zip(scanned, inventory):
        asset_id = item.get("asset_id")
        if not isinstance(asset_id, str) or asset_id in result:
            raise _invalid("scanned asset inventory is invalid")
        result[asset_id] = scanned_asset
    return result


def _first_image_reference(task: Mapping[str, Any], inventory_by_id: Mapping[str, Mapping[str, Any]]) -> str | None:
    references = task.get("references")
    if not isinstance(references, list):
        return None
    for reference in references:
        if not isinstance(reference, Mapping):
            continue
        asset_id = reference.get("asset_id")
        item = inventory_by_id.get(asset_id) if isinstance(asset_id, str) else None
        if isinstance(item, Mapping) and item.get("media_type") in _IMAGE_MEDIA:
            return asset_id
    return None


def _create_comparison_contact_sheet(
    stage: Any,
    output: Path,
    *,
    tasks: Sequence[Mapping[str, Any]],
    reference_scanned: Sequence[Any],
    reference_inventory: Sequence[Mapping[str, Any]],
    result_scanned: Sequence[Any],
    result_inventory: Sequence[Mapping[str, Any]],
) -> None:
    """Write a fixed-card reference-mosaic/result review image without prompts."""

    Image, ImageDraw = rrv_assets._load_pillow()
    reference_items = _inventory_by_asset_id(reference_inventory)
    reference_assets = _scanned_by_asset_id(reference_scanned, reference_inventory)
    result_assets = _scanned_by_asset_id(result_scanned, result_inventory)
    # Three bounded cards per row keep 128 task contact sheets below the
    # image-edge and memory limits while giving every task up to eight image
    # references (four-by-two role-labelled mosaic) plus an independent result.
    card_columns, reference_width, result_width, card_height = 3, 360, 240, 225
    card_width = reference_width + result_width
    rows = max(1, math.ceil(len(tasks) / card_columns))
    canvas = Image.new("RGB", (card_columns * card_width, rows * card_height), (245, 247, 250))
    draw = ImageDraw.Draw(canvas)
    try:
        for index, task in enumerate(tasks):
            card_column, card_row = index % card_columns, index // card_columns
            x = card_column * card_width
            y = card_row * card_height
            task_id = str(task.get("task_id", "task"))
            target = str(task.get("target_slot_id", "slot"))
            image_references: list[tuple[str, str]] = []
            raw_references = task.get("references")
            if isinstance(raw_references, list):
                for reference in raw_references:
                    if not isinstance(reference, Mapping):
                        continue
                    role, asset_id = reference.get("role"), reference.get("asset_id")
                    item = reference_items.get(asset_id) if isinstance(asset_id, str) else None
                    if isinstance(role, str) and isinstance(asset_id, str) and isinstance(item, Mapping) and item.get("media_type") in _IMAGE_MEDIA:
                        image_references.append((role, asset_id))
            reference_id = image_references[0][1] if image_references else None
            result_id = task.get("result_asset_id")
            if result_id is None and task.get("passthrough") is True:
                result_id = reference_id
            draw.rectangle((x + 4, y + 4, x + card_width - 5, y + card_height - 5), fill="white", outline=(185, 193, 204))
            draw.text((x + 10, y + 10), "REFERENCES", fill=(55, 65, 81))
            result_heading = "RESULT"
            if task.get("passthrough") is True:
                result_heading = "PASSTHROUGH"
            elif task.get("omit") is True:
                result_heading = "OMIT"
            draw.text((x + reference_width + 10, y + 10), result_heading, fill=(55, 65, 81))
            if image_references:
                for reference_index, (role, asset_id) in enumerate(image_references[:8]):
                    tile_column, tile_row = reference_index % 4, reference_index // 4
                    tile_x = x + 10 + tile_column * 86
                    tile_y = y + 30 + tile_row * 76
                    draw.rectangle((tile_x, tile_y, tile_x + 78, tile_y + 68), fill=(247, 249, 252), outline=(214, 220, 229))
                    thumbnail = None
                    try:
                        source = reference_assets.get(asset_id)
                        if source is not None:
                            thumbnail = rrv_assets._thumbnail_for_asset(source, maximum=(70, 45))
                        if thumbnail is not None:
                            canvas.paste(thumbnail, (tile_x + (78 - thumbnail.width) // 2, tile_y + 4))
                        else:
                            draw.text((tile_x + 13, tile_y + 20), "NO IMAGE", fill=(100, 116, 139))
                    finally:
                        if thumbnail is not None:
                            thumbnail.close()
                    draw.text((tile_x + 3, tile_y + 52), role.upper()[:11], fill=(71, 85, 105))
            else:
                draw.rectangle((x + 12, y + 34, x + reference_width - 12, y + 174), fill=(227, 232, 240))
                reference_label = "AUDIO REFERENCE" if task.get("passthrough") is True else "NO IMAGE REFERENCES"
                draw.text((x + 104, y + 96), reference_label, fill=(71, 85, 105))
            result_x = x + reference_width
            draw.rectangle((result_x + 6, y + 30, result_x + result_width - 10, y + 174), fill=(247, 249, 252), outline=(214, 220, 229))
            thumbnail = None
            source = None
            if isinstance(result_id, str):
                if task.get("passthrough") is True:
                    source = reference_assets.get(result_id)
                else:
                    source = result_assets.get(result_id)
            try:
                if source is not None:
                    thumbnail = rrv_assets._thumbnail_for_asset(source, maximum=(result_width - 28, 132))
                if thumbnail is not None:
                    canvas.paste(thumbnail, (result_x + (result_width - thumbnail.width) // 2, y + 35))
                else:
                    label = "OMIT" if task.get("omit") is True else "AUDIO" if task.get("passthrough") is True else "NO IMAGE"
                    draw.text((result_x + 83, y + 95), label, fill=(71, 85, 105))
            finally:
                if thumbnail is not None:
                    thumbnail.close()
            draw.text((x + 10, y + 188), task_id[:26], fill=(17, 24, 39))
            draw.text((x + 10, y + 205), target[:38], fill=(71, 85, 105))
        with rrv_propose._open_stage_output_file(stage, output, "generation comparison contact sheet") as handle:
            canvas.save(handle, format="PNG", optimize=False, compress_level=9)
        rrv_propose._assert_stage_regular_file(stage, output, "generation comparison contact sheet")
    except rrv_runtime.RRVError:
        raise
    except OSError as exc:
        raise _tool_error("could not write local generation comparison contact sheet") from exc
    finally:
        canvas.close()


def _expected_results_tasks(
    plan_tasks: Sequence[Mapping[str, Any]],
    result_inventory: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Require exactly one static result file for every non-passthrough task."""

    by_stem: dict[str, Mapping[str, Any]] = {}
    for item in result_inventory:
        source_path = item.get("source_path")
        asset_id = item.get("asset_id")
        media_type = item.get("media_type")
        facts = item.get("facts")
        if (
            not isinstance(source_path, str)
            or not isinstance(asset_id, str)
            or media_type not in _IMAGE_MEDIA
            or not isinstance(facts, Mapping)
            or facts.get("kind") != "image"
        ):
            raise _invalid("generation result pack contains unsupported media")
        stem = PurePosixPath(source_path).stem
        if not stem or stem in by_stem:
            raise _invalid("generation result pack has ambiguous target filenames")
        by_stem[stem] = item
    expected_targets = {
        task.get("target_slot_id")
        for task in plan_tasks
        if isinstance(task, Mapping) and task.get("passthrough") is not True and task.get("omit") is not True
    }
    if not all(isinstance(target, str) for target in expected_targets) or set(by_stem) != expected_targets:
        raise _invalid("generation result pack must contain exactly the required target-slot images")
    tasks: list[dict[str, Any]] = []
    for task in plan_tasks:
        if not isinstance(task, Mapping):
            raise _invalid("Generation Plan tasks are invalid")
        copied = dict(task)
        target = copied.get("target_slot_id")
        if copied.get("passthrough") is True or copied.get("omit") is True:
            copied["result_asset_id"] = None
        else:
            item = by_stem.get(target) if isinstance(target, str) else None
            asset_id = item.get("asset_id") if isinstance(item, Mapping) else None
            if not isinstance(asset_id, str):
                raise _invalid("generation result pack does not match the Generation Plan")
            copied["result_asset_id"] = asset_id
        tasks.append(copied)
    return tasks


def _results_review_template(proposal_sha256: str, proposal: Mapping[str, Any]) -> dict[str, Any]:
    raw_tasks = proposal.get("tasks")
    if not isinstance(raw_tasks, list):
        raise _invalid("generated result proposal is invalid")
    tasks: list[dict[str, Any]] = []
    for task in raw_tasks:
        if not isinstance(task, Mapping):
            raise _invalid("generated result proposal is invalid")
        tasks.append(
            {
                "task_id": task.get("task_id"),
                "target_slot_id": task.get("target_slot_id"),
                "decision": "pending",
                "identity_confirmed": False,
                "garment_confirmed": False,
                "product_confirmed": False,
                "background_confirmed": False,
                "pose_confirmed": False,
                "render_ready_confirmed": False,
                "rights_confirmed": False,
                "omission_confirmed": False,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "proposal_sha256": proposal_sha256,
        "decision": "pending",
        "comparison_contact_sheet_reviewed": False,
        "tasks": tasks,
    }


def _approved_results_review(
    proposal: Mapping[str, Any], proposal_sha256: str, review: Mapping[str, Any]
) -> None:
    if review.get("proposal_sha256") != proposal_sha256:
        raise _invalid("Generation Results Review does not bind the exact proposal")
    if review.get("decision") != "approved" or review.get("comparison_contact_sheet_reviewed") is not True:
        raise _invalid("Generation Results Review must be approved after contact-sheet review")
    proposal_tasks = proposal.get("tasks")
    review_tasks = review.get("tasks")
    if not isinstance(proposal_tasks, list) or not isinstance(review_tasks, list) or len(proposal_tasks) != len(review_tasks):
        raise _invalid("Generation Results Review tasks do not match the proposal")
    for proposal_task, review_task in zip(proposal_tasks, review_tasks):
        if not isinstance(proposal_task, Mapping) or not isinstance(review_task, Mapping):
            raise _invalid("Generation Results Review tasks do not match the proposal")
        if (
            review_task.get("task_id") != proposal_task.get("task_id")
            or review_task.get("target_slot_id") != proposal_task.get("target_slot_id")
            or review_task.get("decision") != "accept"
        ):
            raise _invalid("Generation Results Review contains a pending or retry task")
        if proposal_task.get("omit") is True:
            required_keys = ("omission_confirmed", "rights_confirmed")
        elif proposal_task.get("passthrough") is True:
            references = proposal_task.get("references")
            if not isinstance(references, list) or len(references) != 1 or not isinstance(references[0], Mapping):
                raise _invalid("Generation Results Proposal passthrough task is invalid")
            role = references[0].get("role")
            passthrough_role_confirmation = {
                "identity": "identity_confirmed",
                "garment": "garment_confirmed",
                "product": "product_confirmed",
                "background": "background_confirmed",
            }.get(role)
            required_keys = ("render_ready_confirmed", "rights_confirmed") + (
                (passthrough_role_confirmation,) if passthrough_role_confirmation is not None else ()
            )
        else:
            required_keys = _KIND_RESULT_CONFIRMATIONS.get(str(proposal_task.get("kind")), ())
        if not required_keys or any(review_task.get(key) is not True for key in required_keys):
            raise _invalid("Generation Results Review lacks applicable human confirmations")


def _validate_plan_static_bindings(
    plan: Mapping[str, Any],
    *,
    template_snapshot: Any,
    request_snapshot: Any,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Reject Template/Request drift before reading evidence or media packs.

    The plan's inventory and task binding intentionally remain a separate
    check: they cannot be recomputed until the corresponding direct-child
    reference pack has been scanned.  Keeping these checks apart makes the
    cheaper immutable-input boundary run first.
    """

    template = _validate_template_snapshot(template_snapshot)
    request = request_snapshot.data
    request_errors = validate_generation_request_data(request)
    if request_errors or not isinstance(request, Mapping):
        _raise_validation("Generation Request", request_errors)
    if (
        plan.get("template_sha256") != template_snapshot.sha256
        or plan.get("template_id") != template.get("template_id")
        or plan.get("request_sha256") != request_snapshot.sha256
        or plan.get("privacy_profile") != request.get("privacy_profile")
        or plan.get("execution_profile") != request.get("execution_profile")
        or plan.get("adapter_id") != request.get("adapter_id")
        or plan.get("adapter_version") != request.get("adapter_version")
        or plan.get("controller_label") != request.get("controller_label")
        or plan.get("cloud_upload_confirmed") is not (request.get("cloud_upload_confirmed") is True)
        or plan.get("scanner_policy_version") != SCANNER_POLICY_VERSION
    ):
        raise _invalid("Generation Plan inputs changed since plan creation")
    return template, request


def _validate_plan_bindings(
    plan: Mapping[str, Any],
    *,
    template: Mapping[str, Any],
    request: Mapping[str, Any],
    reference_inventory: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Validate pack-dependent inventory and task bindings after a scan."""

    opaque_inventory = _opaque_inventory(reference_inventory)
    expected_inventory_hash = _canonical_json_sha256(opaque_inventory)
    if (
        plan.get("reference_inventory") != opaque_inventory
        or plan.get("reference_inventory_sha256") != expected_inventory_hash
    ):
        raise _invalid("reference pack inventory changed since Generation Plan creation")
    expected_tasks = _request_plan_tasks(template, request, reference_inventory)
    if plan.get("tasks") != expected_tasks:
        raise _invalid("Generation Request or Template slot binding changed since plan creation")
    return expected_tasks


def _validate_results_proposal_bindings(
    proposal: Mapping[str, Any],
    *,
    plan_snapshot: Any,
    plan_review_snapshot: Any,
    plan: Mapping[str, Any],
    result_inventory: Sequence[Mapping[str, Any]],
    expected_tasks: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    _validate_results_proposal_packet_bindings(
        proposal,
        plan_snapshot=plan_snapshot,
        plan_review_snapshot=plan_review_snapshot,
        plan=plan,
    )
    expected_inventory_hash = _canonical_json_sha256(result_inventory)
    if (
        proposal.get("result_inventory") != list(result_inventory)
        or proposal.get("result_inventory_sha256") != expected_inventory_hash
    ):
        raise _invalid("generation result pack inventory changed since proposal creation")
    expected_result_tasks = _expected_results_tasks(expected_tasks, result_inventory)
    if proposal.get("tasks") != expected_result_tasks:
        raise _invalid("generation result pack does not match the Generation Results Proposal")
    return expected_result_tasks


def _validate_results_proposal_packet_bindings(
    proposal: Mapping[str, Any],
    *,
    plan_snapshot: Any,
    plan_review_snapshot: Any,
    plan: Mapping[str, Any],
) -> None:
    """Bind the four review packets before any evidence or media access."""

    if (
        proposal.get("plan_path") != plan_snapshot.relative_path
        or proposal.get("plan_sha256") != plan_snapshot.sha256
        or proposal.get("plan_review_path") != plan_review_snapshot.relative_path
        or proposal.get("plan_review_sha256") != plan_review_snapshot.sha256
        or proposal.get("template_path") != plan.get("template_path")
        or proposal.get("template_sha256") != plan.get("template_sha256")
        or proposal.get("template_id") != plan.get("template_id")
        or proposal.get("request_path") != plan.get("request_path")
        or proposal.get("request_sha256") != plan.get("request_sha256")
        or proposal.get("reference_pack") != plan.get("reference_pack")
        or proposal.get("reference_inventory_sha256") != plan.get("reference_inventory_sha256")
        or proposal.get("scanner_policy_version") != SCANNER_POLICY_VERSION
    ):
        raise _invalid("Generation Results Proposal does not bind the approved Generation Plan")


def prepare_generation(
    template: str | os.PathLike[str],
    request: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str],
    reference_pack: str | os.PathLike[str],
    generation_rights_confirmed: bool,
    output_dir: str | os.PathLike[str] = "generation-plan",
    ffprobe: str | os.PathLike[str] = "ffprobe",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Mapping[str, Any]:
    """Create a reviewed, controller-neutral Generation Plan.

    ``generation_rights_confirmed`` is intentionally the first executable
    boundary.  Any value other than literal ``True`` causes no project-root
    access, template/request read, pack scan, Pillow import, or ffprobe call.
    The routine never starts a generation process.
    """

    if generation_rights_confirmed is not True:
        raise _invalid("generation_rights_confirmed must be explicitly true before local generation planning")
    root = rrv_assets._safe_project_root(project_root)
    timeout = rrv_assets._parse_timeout(timeout_seconds)
    reference_pack_name = rrv_assets._direct_child_name(reference_pack, "reference_pack")
    stage: Any = None
    scanned: list[Any] = []
    try:
        with rrv_assets._root_guard(root) as root_identity:
            target = rrv_assets._direct_output_target(root, output_dir)
            request_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, request, label="generation request")
            request_data = request_snapshot.data
            request_errors = validate_generation_request_data(request_data)
            if request_errors or not isinstance(request_data, Mapping):
                _raise_validation("Generation Request", request_errors)
            template_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, template, label="template")
            template_data = _validate_template_snapshot(template_snapshot)
            with rrv_assets._asset_pack_guard(root, root_identity, reference_pack_name) as (pack, pack_identity):
                scanned, inventory = rrv_assets._scan_asset_pack(
                    root_identity,
                    pack,
                    pack_identity,
                    reference_pack_name,
                    ffprobe=ffprobe,
                    timeout_seconds=timeout,
                )
                plan_tasks = _request_plan_tasks(template_data, request_data, inventory)
                opaque_inventory = _opaque_inventory(inventory)
                stage = rrv_propose._new_staging_directory(root, "generation-plan")
                contact_path = rrv_propose._stage_path(root, stage, "generation-input-contact-sheet.png")
                _create_input_contact_sheet(stage, contact_path, scanned, inventory, plan_tasks)
                contact_artifact = rrv_assets._artifact(root, stage, target, contact_path)
                plan_data: dict[str, Any] = {
                    "schema_version": SCHEMA_VERSION,
                    "privacy_profile": request_data.get("privacy_profile"),
                    "cloud_upload_confirmed": request_data.get("cloud_upload_confirmed") is True,
                    "execution_profile": request_data.get("execution_profile"),
                    "adapter_id": request_data.get("adapter_id"),
                    "adapter_version": request_data.get("adapter_version"),
                    "generation_rights_confirmed": True,
                    "review_required": True,
                    "template_path": template_snapshot.relative_path,
                    "template_sha256": template_snapshot.sha256,
                    "template_id": template_data.get("template_id"),
                    "request_path": request_snapshot.relative_path,
                    "request_sha256": request_snapshot.sha256,
                    "reference_pack": reference_pack_name,
                    "scanner_policy_version": SCANNER_POLICY_VERSION,
                    "reference_inventory": opaque_inventory,
                    "reference_inventory_sha256": _canonical_json_sha256(opaque_inventory),
                    "tasks": plan_tasks,
                    "evidence": {"input_contact_sheet": contact_artifact},
                }
                if request_data.get("execution_profile") == "controller-managed":
                    plan_data["controller_label"] = request_data.get("controller_label")
                plan_errors = validate_generation_plan_data(plan_data)
                if plan_errors:
                    _raise_validation("generated Generation Plan", plan_errors)
                plan_path = rrv_propose._stage_path(root, stage, "generation-plan.json")
                rrv_assets._write_json(stage, root, plan_path, plan_data, "Generation Plan JSON")
                plan_sha256 = rrv_propose._stage_file_sha256(stage, plan_path)
                review_data = _plan_review_template(plan_sha256, plan_data)
                review_errors = validate_generation_plan_review_data(review_data)
                if review_errors:
                    _raise_validation("generated Generation Plan Review", review_errors)
                review_path = rrv_propose._stage_path(root, stage, "generation-plan-review.template.json")
                rrv_assets._write_json(stage, root, review_path, review_data, "Generation Plan Review JSON")
                plan_artifact = rrv_assets._artifact(root, stage, target, plan_path)
                review_artifact = rrv_assets._artifact(root, stage, target, review_path)
                rrv_assets._assert_pack_live(root_identity, pack_identity)
                rrv_propose._publish_stage(root, stage, target, label="Generation Plan")
                stage = None
                counts = {
                    "reference_inventory_entries": len(inventory),
                    "tasks": len(plan_tasks),
                    "generation_tasks": sum(task["passthrough"] is not True and task["omit"] is not True for task in plan_tasks),
                    "passthrough_tasks": sum(task["passthrough"] is True for task in plan_tasks),
                    "omitted_tasks": sum(task["omit"] is True for task in plan_tasks),
                }
                return {
                    "schema_version": SCHEMA_VERSION,
                    "review_required": True,
                    "execution_profile": plan_data["execution_profile"],
                    "counts": counts,
                    "artifacts": {
                        "generation_plan": plan_artifact,
                        "review_template": review_artifact,
                        "input_contact_sheet": contact_artifact,
                    },
                }
    except BaseException as exc:
        rrv_propose._cleanup_directory(root, stage)
        raise _safe_exception(exc) from None
    finally:
        rrv_assets._close_scanned_assets(scanned)


def propose_generation_results(
    plan: str | os.PathLike[str],
    plan_review: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str],
    result_pack: str | os.PathLike[str],
    generation_results_rights_confirmed: bool,
    output_dir: str | os.PathLike[str] = "generation-results-proposal",
    ffprobe: str | os.PathLike[str] = "ffprobe",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Mapping[str, Any]:
    """Inventory a local controller result drop after plan approval.

    This is a file-drop verifier only.  It does not launch a provider, a local
    model, a shell command, or a network request.  A literal rights gate runs
    before every root/path/media operation.
    """

    if generation_results_rights_confirmed is not True:
        raise _invalid("generation_results_rights_confirmed must be explicitly true before local result analysis")
    root = rrv_assets._safe_project_root(project_root)
    timeout = rrv_assets._parse_timeout(timeout_seconds)
    result_pack_name = rrv_assets._direct_child_name(result_pack, "result_pack")
    stage: Any = None
    reference_scanned: list[Any] = []
    result_scanned: list[Any] = []
    try:
        with rrv_assets._root_guard(root) as root_identity:
            target = rrv_assets._direct_output_target(root, output_dir)
            plan_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, plan, label="Generation Plan")
            review_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, plan_review, label="Generation Plan Review")
            plan_data = plan_snapshot.data
            review_data = review_snapshot.data
            plan_errors = validate_generation_plan_data(plan_data)
            review_errors = validate_generation_plan_review_data(review_data)
            if plan_errors or not isinstance(plan_data, Mapping):
                _raise_validation("Generation Plan", plan_errors)
            if review_errors or not isinstance(review_data, Mapping):
                _raise_validation("Generation Plan Review", review_errors)
            reference_pack_name = rrv_assets._direct_child_name(plan_data.get("reference_pack"), "Generation Plan reference_pack")
            if _same_direct_child(result_pack_name, reference_pack_name):
                raise _invalid("result_pack must be a new direct child distinct from reference_pack")
            _approved_plan_review(plan_data, plan_snapshot.sha256, review_data)
            template_snapshot = rrv_assets._read_project_json_snapshot(
                root, root_identity, plan_data.get("template_path"), label="template"
            )
            request_snapshot = rrv_assets._read_project_json_snapshot(
                root, root_identity, plan_data.get("request_path"), label="generation request"
            )
            template_data, request_data = _validate_plan_static_bindings(
                plan_data,
                template_snapshot=template_snapshot,
                request_snapshot=request_snapshot,
            )
            plan_evidence = plan_data.get("evidence")
            _validate_evidence_artifact(
                root,
                root_identity,
                plan_snapshot.relative_path,
                plan_evidence.get("input_contact_sheet") if isinstance(plan_evidence, Mapping) else None,
                expected_filename="generation-input-contact-sheet.png",
                label="Generation Plan",
            )
            with rrv_assets._asset_pack_guard(root, root_identity, reference_pack_name) as (reference_directory, reference_identity):
                reference_scanned, reference_inventory = rrv_assets._scan_asset_pack(
                    root_identity,
                    reference_directory,
                    reference_identity,
                    reference_pack_name,
                    ffprobe=ffprobe,
                    timeout_seconds=timeout,
                )
                expected_tasks = _validate_plan_bindings(
                    plan_data,
                    template=template_data,
                    request=request_data,
                    reference_inventory=reference_inventory,
                )
                with rrv_assets._asset_pack_guard(root, root_identity, result_pack_name) as (result_directory, result_identity):
                    # Lexical direct-child checks cannot prove that an NTFS
                    # 8.3 alias is a different directory.  Both guards are
                    # live here, so reject a shared filesystem identity before
                    # opening or scanning the result drop.
                    if _same_directory_identity(reference_identity, result_identity):
                        raise _invalid("result_pack must be a distinct local directory")
                    result_scanned, result_inventory = rrv_assets._scan_asset_pack(
                        root_identity,
                        result_directory,
                        result_identity,
                        result_pack_name,
                        ffprobe=ffprobe,
                        timeout_seconds=timeout,
                    )
                    proposal_tasks = _expected_results_tasks(expected_tasks, result_inventory)
                    stage = rrv_propose._new_staging_directory(root, "generation-results-proposal")
                    comparison_path = rrv_propose._stage_path(root, stage, "generation-results-contact-sheet.png")
                    _create_comparison_contact_sheet(
                        stage,
                        comparison_path,
                        tasks=proposal_tasks,
                        reference_scanned=reference_scanned,
                        reference_inventory=reference_inventory,
                        result_scanned=result_scanned,
                        result_inventory=result_inventory,
                    )
                    comparison_artifact = rrv_assets._artifact(root, stage, target, comparison_path)
                    proposal_data: dict[str, Any] = {
                        "schema_version": SCHEMA_VERSION,
                        "review_required": True,
                        "plan_path": plan_snapshot.relative_path,
                        "plan_sha256": plan_snapshot.sha256,
                        "plan_review_path": review_snapshot.relative_path,
                        "plan_review_sha256": review_snapshot.sha256,
                        "template_path": plan_data.get("template_path"),
                        "template_sha256": plan_data.get("template_sha256"),
                        "template_id": plan_data.get("template_id"),
                        "request_path": plan_data.get("request_path"),
                        "request_sha256": plan_data.get("request_sha256"),
                        "reference_pack": reference_pack_name,
                        "reference_inventory_sha256": plan_data.get("reference_inventory_sha256"),
                        "result_pack": result_pack_name,
                        "scanner_policy_version": SCANNER_POLICY_VERSION,
                        "result_inventory": result_inventory,
                        "result_inventory_sha256": _canonical_json_sha256(result_inventory),
                        "tasks": proposal_tasks,
                        "evidence": {"comparison_contact_sheet": comparison_artifact},
                    }
                    proposal_errors = validate_generation_results_proposal_data(proposal_data)
                    if proposal_errors:
                        _raise_validation("generated Generation Results Proposal", proposal_errors)
                    proposal_path = rrv_propose._stage_path(root, stage, "generation-results-proposal.json")
                    rrv_assets._write_json(stage, root, proposal_path, proposal_data, "Generation Results Proposal JSON")
                    proposal_sha256 = rrv_propose._stage_file_sha256(stage, proposal_path)
                    results_review = _results_review_template(proposal_sha256, proposal_data)
                    results_review_errors = validate_generation_results_review_data(results_review)
                    if results_review_errors:
                        _raise_validation("generated Generation Results Review", results_review_errors)
                    review_path = rrv_propose._stage_path(root, stage, "generation-results-review.template.json")
                    rrv_assets._write_json(stage, root, review_path, results_review, "Generation Results Review JSON")
                    proposal_artifact = rrv_assets._artifact(root, stage, target, proposal_path)
                    review_artifact = rrv_assets._artifact(root, stage, target, review_path)
                    rrv_assets._assert_pack_live(root_identity, reference_identity)
                    rrv_assets._assert_pack_live(root_identity, result_identity)
                    rrv_propose._publish_stage(root, stage, target, label="Generation Results Proposal")
                    stage = None
                    counts = {
                        "result_inventory_entries": len(result_inventory),
                        "tasks": len(proposal_tasks),
                        "generation_tasks": sum(task["passthrough"] is not True and task["omit"] is not True for task in proposal_tasks),
                        "passthrough_tasks": sum(task["passthrough"] is True for task in proposal_tasks),
                        "omitted_tasks": sum(task["omit"] is True for task in proposal_tasks),
                    }
                    return {
                        "schema_version": SCHEMA_VERSION,
                        "review_required": True,
                        "counts": counts,
                        "artifacts": {
                            "proposal": proposal_artifact,
                            "review_template": review_artifact,
                            "comparison_contact_sheet": comparison_artifact,
                        },
                    }
    except BaseException as exc:
        rrv_propose._cleanup_directory(root, stage)
        raise _safe_exception(exc) from None
    finally:
        rrv_assets._close_scanned_assets(reference_scanned)
        rrv_assets._close_scanned_assets(result_scanned)


def _write_sanitized_png(asset: Any, *, stage: Any, destination: Path) -> None:
    """Reconstruct pixels after EXIF orientation and emit a metadata-free PNG."""

    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise rrv_runtime.RRVError(
            rrv_runtime.ERR_CAPABILITY_UNAVAILABLE,
            "local generation assembly requires the Pillow dependency",
            {"capability": "generation_asset_sanitization"},
        ) from exc
    clean = None
    oriented = None
    converted = None
    try:
        if getattr(asset, "closed", True):
            raise _invalid("generation source snapshot is no longer available")
        asset.snapshot.seek(0)
        with Image.open(asset.snapshot) as source:
            oriented = ImageOps.exif_transpose(source)
            # Paletted PNG transparency is stored in ``info`` rather than an
            # alpha band.  Treat it as alpha before pixel reconstruction so
            # transparent palette entries cannot become opaque RGB pixels.
            has_alpha = "A" in oriented.getbands() or "transparency" in oriented.info
            mode = "RGBA" if has_alpha else "RGB"
            converted = oriented.convert(mode)
            # ``frombytes`` deliberately reconstructs only pixels, dropping
            # text chunks, EXIF, ICC, XMP, and arbitrary Pillow info metadata.
            clean = Image.frombytes(mode, converted.size, converted.tobytes())
        with rrv_propose._open_stage_output_file(stage, destination, "sanitized generation image") as handle:
            clean.save(handle, format="PNG", optimize=False, compress_level=9)
        rrv_propose._assert_stage_regular_file(stage, destination, "sanitized generation image")
    except rrv_runtime.RRVError:
        raise
    except (OSError, ValueError, SyntaxError) as exc:
        raise _tool_error("could not sanitize a local generation image") from exc
    finally:
        if clean is not None:
            clean.close()
        if converted is not None:
            converted.close()
        if oriented is not None and oriented is not converted:
            try:
                oriented.close()
            except Exception:
                pass


def _published_output_asset(
    root: Path,
    stage: Any,
    target: Path,
    destination: Path,
    *,
    slot_id: str,
    media_type: str,
) -> dict[str, str]:
    size_bytes = rrv_propose._stage_regular_file_size(stage, destination, "generation output asset")
    if not 1 <= size_bytes <= rrv_assets.MAX_FILE_BYTES:
        raise _invalid("generation output asset exceeds the bounded local file limit")
    artifact = rrv_propose._published_artifact(root, stage, target, destination)
    return {
        "slot_id": slot_id,
        "path": artifact["path"],
        "sha256": artifact["sha256"],
        "media_type": media_type,
    }


def assemble_generation_pack(
    plan: str | os.PathLike[str],
    plan_review: str | os.PathLike[str],
    results_proposal: str | os.PathLike[str],
    results_review: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str],
    output_dir: str | os.PathLike[str] = "generation-asset-pack",
    ffprobe: str | os.PathLike[str] = "ffprobe",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Mapping[str, Any]:
    """Publish a new pure-media exact-slot pack after both reviews approve.

    The function re-reads every bound JSON snapshot and rescans both direct
    child packs.  Any template/request/plan/review/inventory drift is rejected
    before a staged output directory is published.  Generated and image
    passthrough assets become metadata-free PNG files; audio passthrough is a
    descriptor-bound snapshot copy.  The published directory contains media
    only, so it can be supplied directly to v0.5 ``propose-assets``.
    """

    root = rrv_assets._safe_project_root(project_root)
    timeout = rrv_assets._parse_timeout(timeout_seconds)
    stage: Any = None
    reference_scanned: list[Any] = []
    result_scanned: list[Any] = []
    try:
        with rrv_assets._root_guard(root) as root_identity:
            target = rrv_assets._direct_output_target(root, output_dir)
            plan_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, plan, label="Generation Plan")
            plan_review_snapshot = rrv_assets._read_project_json_snapshot(
                root, root_identity, plan_review, label="Generation Plan Review"
            )
            results_snapshot = rrv_assets._read_project_json_snapshot(
                root, root_identity, results_proposal, label="Generation Results Proposal"
            )
            results_review_snapshot = rrv_assets._read_project_json_snapshot(
                root, root_identity, results_review, label="Generation Results Review"
            )
            plan_data = plan_snapshot.data
            plan_review_data = plan_review_snapshot.data
            proposal_data = results_snapshot.data
            results_review_data = results_review_snapshot.data
            plan_errors = validate_generation_plan_data(plan_data)
            plan_review_errors = validate_generation_plan_review_data(plan_review_data)
            proposal_errors = validate_generation_results_proposal_data(proposal_data)
            results_review_errors = validate_generation_results_review_data(results_review_data)
            if plan_errors or not isinstance(plan_data, Mapping):
                _raise_validation("Generation Plan", plan_errors)
            if plan_review_errors or not isinstance(plan_review_data, Mapping):
                _raise_validation("Generation Plan Review", plan_review_errors)
            if proposal_errors or not isinstance(proposal_data, Mapping):
                _raise_validation("Generation Results Proposal", proposal_errors)
            if results_review_errors or not isinstance(results_review_data, Mapping):
                _raise_validation("Generation Results Review", results_review_errors)
            if _same_direct_child(proposal_data.get("result_pack"), plan_data.get("reference_pack")):
                raise _invalid("Generation Results Proposal result_pack must be distinct from reference_pack")
            _approved_plan_review(plan_data, plan_snapshot.sha256, plan_review_data)
            # Reject a pending/retry/unapproved result review, including all
            # per-task rights gates, before opening evidence, Template/Request,
            # or either media pack.  This preserves the review boundary even
            # for large or hostile file drops.
            _validate_results_proposal_packet_bindings(
                proposal_data,
                plan_snapshot=plan_snapshot,
                plan_review_snapshot=plan_review_snapshot,
                plan=plan_data,
            )
            _approved_results_review(proposal_data, results_snapshot.sha256, results_review_data)
            template_snapshot = rrv_assets._read_project_json_snapshot(
                root, root_identity, plan_data.get("template_path"), label="template"
            )
            request_snapshot = rrv_assets._read_project_json_snapshot(
                root, root_identity, plan_data.get("request_path"), label="generation request"
            )
            template_data, request_data = _validate_plan_static_bindings(
                plan_data,
                template_snapshot=template_snapshot,
                request_snapshot=request_snapshot,
            )
            plan_evidence = plan_data.get("evidence")
            _validate_evidence_artifact(
                root,
                root_identity,
                plan_snapshot.relative_path,
                plan_evidence.get("input_contact_sheet") if isinstance(plan_evidence, Mapping) else None,
                expected_filename="generation-input-contact-sheet.png",
                label="Generation Plan",
            )
            results_evidence = proposal_data.get("evidence")
            _validate_evidence_artifact(
                root,
                root_identity,
                results_snapshot.relative_path,
                results_evidence.get("comparison_contact_sheet") if isinstance(results_evidence, Mapping) else None,
                expected_filename="generation-results-contact-sheet.png",
                label="Generation Results Proposal",
            )
            reference_pack_name = rrv_assets._direct_child_name(plan_data.get("reference_pack"), "Generation Plan reference_pack")
            result_pack_name = rrv_assets._direct_child_name(proposal_data.get("result_pack"), "Generation Results Proposal result_pack")
            with rrv_assets._asset_pack_guard(root, root_identity, reference_pack_name) as (reference_directory, reference_identity):
                reference_scanned, reference_inventory = rrv_assets._scan_asset_pack(
                    root_identity,
                    reference_directory,
                    reference_identity,
                    reference_pack_name,
                    ffprobe=ffprobe,
                    timeout_seconds=timeout,
                )
                expected_tasks = _validate_plan_bindings(
                    plan_data,
                    template=template_data,
                    request=request_data,
                    reference_inventory=reference_inventory,
                )
                with rrv_assets._asset_pack_guard(root, root_identity, result_pack_name) as (result_directory, result_identity):
                    if _same_directory_identity(reference_identity, result_identity):
                        raise _invalid("Generation Results Proposal result_pack must be a distinct local directory")
                    result_scanned, result_inventory = rrv_assets._scan_asset_pack(
                        root_identity,
                        result_directory,
                        result_identity,
                        result_pack_name,
                        ffprobe=ffprobe,
                        timeout_seconds=timeout,
                    )
                    expected_results_tasks = _validate_results_proposal_bindings(
                        proposal_data,
                        plan_snapshot=plan_snapshot,
                        plan_review_snapshot=plan_review_snapshot,
                        plan=plan_data,
                        result_inventory=result_inventory,
                        expected_tasks=expected_tasks,
                    )
                    # The comparison review must apply to precisely this current task list.
                    if proposal_data.get("tasks") != expected_results_tasks:
                        raise _invalid("Generation Results Proposal tasks changed since review")
                    slots = _template_slots(template_data)
                    reference_items = _inventory_by_asset_id(reference_inventory)
                    result_items = _inventory_by_asset_id(result_inventory)
                    reference_assets = _scanned_by_asset_id(reference_scanned, reference_inventory)
                    result_assets = _scanned_by_asset_id(result_scanned, result_inventory)
                    stage = rrv_propose._new_staging_directory(root, "generation-asset-pack")
                    assets: list[dict[str, str]] = []
                    generation_results = 0
                    image_passthrough = 0
                    audio_passthrough = 0
                    omitted_tasks = 0
                    for task in expected_results_tasks:
                        target_slot = task.get("target_slot_id")
                        if not isinstance(target_slot, str) or target_slot not in slots:
                            raise _invalid("Generation Plan task target is invalid")
                        if task.get("omit") is True:
                            omitted_tasks += 1
                            continue
                        if task.get("passthrough") is True:
                            references = task.get("references")
                            if not isinstance(references, list) or len(references) != 1 or not isinstance(references[0], Mapping):
                                raise _invalid("Generation Plan passthrough task is invalid")
                            asset_id = references[0].get("asset_id")
                            source_item = reference_items.get(asset_id) if isinstance(asset_id, str) else None
                            source_asset = reference_assets.get(asset_id) if isinstance(asset_id, str) else None
                            if not isinstance(source_item, Mapping) or source_asset is None:
                                raise _invalid("Generation Plan passthrough reference is invalid")
                            media_type = source_item.get("media_type")
                            facts = source_item.get("facts")
                            if not isinstance(facts, Mapping):
                                raise _invalid("Generation Plan passthrough reference is invalid")
                            if facts.get("kind") == "image":
                                destination = rrv_propose._stage_path(root, stage, f"{target_slot}.png")
                                _write_sanitized_png(source_asset, stage=stage, destination=destination)
                                assets.append(
                                    _published_output_asset(
                                        root, stage, target, destination, slot_id=target_slot, media_type="image/png"
                                    )
                                )
                                image_passthrough += 1
                            elif facts.get("kind") == "audio" and isinstance(media_type, str) and media_type in _AUDIO_MEDIA:
                                extension = rrv_assets._CANONICAL_EXTENSION.get(media_type)
                                if not isinstance(extension, str):
                                    raise _invalid("Generation Plan passthrough reference is invalid")
                                destination = rrv_propose._stage_path(root, stage, f"{target_slot}.{extension}")
                                source_hash = source_item.get("sha256")
                                if not isinstance(source_hash, str):
                                    raise _invalid("Generation Plan passthrough reference is invalid")
                                rrv_assets._copy_snapshot_asset(
                                    source_asset, stage=stage, destination=destination, expected_sha256=source_hash
                                )
                                assets.append(
                                    _published_output_asset(
                                        root, stage, target, destination, slot_id=target_slot, media_type=media_type
                                    )
                                )
                                audio_passthrough += 1
                            else:
                                raise _invalid("Generation Plan passthrough reference is invalid")
                            continue
                        result_asset_id = task.get("result_asset_id")
                        result_item = result_items.get(result_asset_id) if isinstance(result_asset_id, str) else None
                        result_asset = result_assets.get(result_asset_id) if isinstance(result_asset_id, str) else None
                        if not isinstance(result_item, Mapping) or result_asset is None or result_item.get("media_type") not in _IMAGE_MEDIA:
                            raise _invalid("Generation result asset is invalid")
                        destination = rrv_propose._stage_path(root, stage, f"{target_slot}.png")
                        _write_sanitized_png(result_asset, stage=stage, destination=destination)
                        assets.append(
                            _published_output_asset(root, stage, target, destination, slot_id=target_slot, media_type="image/png")
                        )
                        generation_results += 1
                    assets.sort(key=lambda item: item["slot_id"])
                    rrv_assets._assert_pack_live(root_identity, reference_identity)
                    rrv_assets._assert_pack_live(root_identity, result_identity)
                    rrv_propose._publish_stage(root, stage, target, label="Generation Asset Pack")
                    output_relative = rrv_propose._lexical_relative_output_path(root, target)
                    stage = None
                    return {
                        "schema_version": SCHEMA_VERSION,
                        "review_required": False,
                        "output_dir": output_relative,
                        "counts": {
                            "output_assets": len(assets),
                            "generation_results": generation_results,
                            "image_passthrough": image_passthrough,
                            "audio_passthrough": audio_passthrough,
                            "omitted_tasks": omitted_tasks,
                        },
                        "assets": assets,
                    }
    except BaseException as exc:
        rrv_propose._cleanup_directory(root, stage)
        raise _safe_exception(exc) from None
    finally:
        rrv_assets._close_scanned_assets(reference_scanned)
        rrv_assets._close_scanned_assets(result_scanned)


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "SCANNER_POLICY_VERSION",
    "SCHEMA_VERSION",
    "assemble_generation_pack",
    "prepare_generation",
    "propose_generation_results",
    "validate_generation_plan_data",
    "validate_generation_plan_review_data",
    "validate_generation_request_data",
    "validate_generation_results_proposal_data",
    "validate_generation_results_review_data",
]
