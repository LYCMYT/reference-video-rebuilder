#!/usr/bin/env python3
"""Fail-closed local hand-off for reviewed temporal replacement files.

This module deliberately does not contact a provider, inspect environment
variables or credentials, start a browser, load CUDA, or generate media.  It
only binds locally dropped files to a reviewed request and makes immutable
byte-copy delivery after review.  Every FFmpeg/FFprobe pathname below is a
private staged snapshot made from a descriptor-bound source read.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

try:  # Direct execution from the Skill scripts directory.
    import rrv_assets
    import rrv_faithful
    import rrv_nle
    import rrv_propose
    import rrv_runtime
    import video_remix
except ImportError:  # pragma: no cover - package-style import support.
    from . import rrv_assets, rrv_faithful, rrv_nle, rrv_propose, rrv_runtime, video_remix  # type: ignore[no-redef]


SCHEMA_VERSION = "0.10.0"
SCANNER_POLICY_VERSION = rrv_assets.SCANNER_POLICY_VERSION
DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_DURATION_SECONDS = 60.0
_MAX_FILE_BYTES = rrv_assets.MAX_FILE_BYTES
_MAX_TECHNICAL_EVIDENCE_BYTES = 256 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_ADAPTER_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_ADAPTER_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MOTION_MODES = frozenset({"pose-transfer", "video-to-video"})
_AUDIO_MODES = frozenset(
    {"mute", "preserve-reference", "replace-upload", "rebuild-sfx", "clone-authorized-voice"}
)
_PLAN_CONFIRMATIONS = (
    "input_contact_sheet_reviewed",
    "request_reviewed",
    "execution_profile_confirmed",
    "full_playback_reviewed",
    "motion_action_confirmed",
    "face_hands_limbs_confirmed",
    "garment_product_confirmed",
    "timing_confirmed",
    "audio_confirmed",
    "rights_confirmed",
    "watermark_reviewed",
)
_RESULT_CONFIRMATIONS = (
    "comparison_contact_sheet_reviewed",
    "technical_sanity_reviewed",
    "full_playback_reviewed",
    "motion_action_confirmed",
    "face_hands_limbs_confirmed",
    "garment_product_confirmed",
    "timing_confirmed",
    "audio_confirmed",
    "rights_confirmed",
    "watermark_absent_confirmed",
)
_GRAY_WIDTH = 64
_GRAY_HEIGHT = 36
_CONTACT_FRAMES = 6
_TECHNICAL_LIMITATION = "Technical temporal metrics are negative checks only and do not prove semantic action or motion reproduction."

_SKILL_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_DIRECTORY = _SKILL_ROOT / "assets" / "schemas"
_REQUEST_SCHEMA_PATH = _SCHEMA_DIRECTORY / "temporal-replacement-request.schema.json"
_PLAN_SCHEMA_PATH = _SCHEMA_DIRECTORY / "temporal-replacement-plan.schema.json"
_PLAN_REVIEW_SCHEMA_PATH = _SCHEMA_DIRECTORY / "temporal-replacement-plan-review.schema.json"
_RESULTS_PROPOSAL_SCHEMA_PATH = _SCHEMA_DIRECTORY / "temporal-results-proposal.schema.json"
_RESULTS_REVIEW_SCHEMA_PATH = _SCHEMA_DIRECTORY / "temporal-results-review.schema.json"
_DELIVERY_REPORT_SCHEMA_PATH = _SCHEMA_DIRECTORY / "temporal-delivery-report.schema.json"
_ASSET_MANIFEST_SCHEMA_PATH = _SCHEMA_DIRECTORY / "asset-manifest.schema.json"


def _invalid(message: str) -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(rrv_runtime.ERR_INVALID_ARGUMENT, message)


def _tool_error(message: str) -> rrv_runtime.RRVError:
    return rrv_runtime.RRVError(rrv_runtime.ERR_TOOL_EXECUTION, message)


def _safe_exception(exc: BaseException) -> rrv_runtime.RRVError:
    """Never publish tool output, paths, source names, or exception internals."""

    if isinstance(exc, rrv_runtime.RRVError):
        if exc.code == rrv_runtime.ERR_OUTPUT_EXISTS:
            return rrv_runtime.RRVError(exc.code, "refusing to overwrite an existing output")
        if exc.code == rrv_runtime.ERR_INVALID_ARGUMENT:
            return _invalid("temporal replacement input was rejected")
        if exc.code == rrv_runtime.ERR_TOOL_NOT_FOUND:
            return rrv_runtime.RRVError(exc.code, "required local media tool is unavailable")
        if exc.code == rrv_runtime.ERR_TOOL_TIMEOUT:
            return rrv_runtime.RRVError(exc.code, "local temporal media operation exceeded its timeout")
        if exc.code == rrv_runtime.ERR_CAPABILITY_UNAVAILABLE:
            return rrv_runtime.RRVError(exc.code, "temporal media does not meet the required local profile")
        return rrv_runtime.RRVError(exc.code, "local temporal replacement operation failed")
    return _tool_error("local temporal replacement operation failed")


def _canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(rrv_runtime.stable_json_dumps(value, indent=None).encode("utf-8")).hexdigest()


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and _ID_RE.fullmatch(value) is not None and rrv_assets._portable_path_component(value)


def _valid_relative_path(value: Any) -> bool:
    return rrv_assets._relative_path_parts(value) is not None


def _valid_direct_child(value: Any) -> bool:
    try:
        rrv_assets._direct_child_name(value, "name")
    except rrv_runtime.RRVError:
        return False
    return True


def _same_direct_child(left: Any, right: Any) -> bool:
    return isinstance(left, str) and isinstance(right, str) and os.path.normcase(left) == os.path.normcase(right)


def _same_directory_identity(left: Any, right: Any) -> bool:
    return (
        isinstance(getattr(left, "device", None), int)
        and isinstance(getattr(left, "inode", None), int)
        and getattr(left, "device") == getattr(right, "device", None)
        and getattr(left, "inode") == getattr(right, "inode", None)
    )


def _unique_errors(errors: Sequence[str]) -> list[str]:
    return rrv_assets._unique_errors(errors)


def _nonfinite_errors(value: Any) -> list[str]:
    errors: list[str] = []
    rrv_assets._find_nonfinite(value, "$", errors)
    return errors


def _schema_errors(data: Any, schema: Path, label: str) -> list[str]:
    return rrv_assets._schema_errors(data, schema, label)


def _sha_error(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        errors.append(f"{path}: sha256")


def _artifact_errors(value: Any, path: str) -> list[str]:
    if not isinstance(value, Mapping):
        return [f"{path}: artifact"]
    errors: list[str] = []
    if not _valid_relative_path(value.get("path")):
        errors.append(f"{path}.path: normalized_relative_path")
    _sha_error(value.get("sha256"), f"{path}.sha256", errors)
    return errors


def _media_facts_errors(value: Any, path: str) -> list[str]:
    if not isinstance(value, Mapping):
        return [f"{path}: media_facts"]
    errors: list[str] = []
    for key in ("width", "height", "frame_count"):
        if not _is_int(value.get(key)) or int(value[key]) < 1:
            errors.append(f"{path}.{key}: positive_integer")
    for key in ("fps", "duration_seconds"):
        if not _is_number(value.get(key)) or float(value[key]) <= 0:
            errors.append(f"{path}.{key}: positive_number")
    if value.get("container") != "mp4" or value.get("cfr") is not True:
        errors.append(f"{path}: mp4_cfr")
    if (
        value.get("video_codec") != "h264"
        or value.get("video_profile") != "High"
        or value.get("pixel_format") != "yuv420p"
        or value.get("bit_depth") != 8
        or value.get("rotation_degrees") != 0
    ):
        errors.append(f"{path}: h264_high_8bit_yuv420p")
    has_audio = value.get("has_audio")
    if not isinstance(has_audio, bool):
        errors.append(f"{path}.has_audio: boolean")
    elif has_audio:
        if (
            value.get("audio_stream_count") != 1
            or value.get("audio_codec") != "aac"
            or value.get("audio_profile") != "LC"
            or value.get("audio_sample_rate") != 48000
            or value.get("audio_channels") != 2
            or value.get("audio_channel_layout") != "stereo"
        ):
            errors.append(f"{path}: aac_lc_48k_stereo")
    elif any(
        value.get(key) is not None
        for key in ("audio_codec", "audio_profile", "audio_sample_rate", "audio_channels", "audio_channel_layout")
    ) or value.get("audio_stream_count") != 0:
        errors.append(f"{path}: no_audio_fields")
    if _is_number(value.get("duration_seconds")) and float(value["duration_seconds"]) > MAX_DURATION_SECONDS + 1e-9:
        errors.append(f"{path}.duration_seconds: max_60_seconds")
    return errors


def _input_assets_errors(value: Any, path: str) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 128:
        return [f"{path}: bounded_array"]
    errors: list[str] = []
    prior = ""
    seen: set[str] = set()
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, Mapping):
            errors.append(f"{item_path}: object")
            continue
        slot = item.get("slot_id")
        if not _valid_id(slot) or slot in seen or (isinstance(slot, str) and prior and slot <= prior):
            errors.append(f"{item_path}.slot_id: sorted_unique_slot")
        elif isinstance(slot, str):
            seen.add(slot)
            prior = slot
        _sha_error(item.get("sha256"), f"{item_path}.sha256", errors)
        if item.get("media_type") not in {
            "image/jpeg", "image/png", "image/webp", "video/mp4", "video/quicktime", "video/webm",
            "audio/wav", "audio/mpeg", "audio/mp4", "audio/x-matroska",
        }:
            errors.append(f"{item_path}.media_type: accepted_media")
    return errors


def _inventory_errors(value: Any, path: str, *, result: bool) -> list[str]:
    expected_id = "temporal-result.0001" if result else "action-reference.0001"
    if not isinstance(value, list) or len(value) != 1:
        return [f"{path}: exactly_one"]
    item = value[0]
    if not isinstance(item, Mapping):
        return [f"{path}[0]: object"]
    errors: list[str] = []
    if item.get("asset_id") != expected_id:
        errors.append(f"{path}[0].asset_id: fixed_identifier")
    _sha_error(item.get("sha256"), f"{path}[0].sha256", errors)
    if not _is_int(item.get("size_bytes")) or not 1 <= int(item["size_bytes"]) <= _MAX_FILE_BYTES:
        errors.append(f"{path}[0].size_bytes: bounded_positive_integer")
    if item.get("media_type") != "video/mp4":
        errors.append(f"{path}[0].media_type: video_mp4")
    errors.extend(_media_facts_errors(item.get("facts"), f"{path}[0].facts"))
    return errors


def _requirements_errors(value: Any, path: str, *, plan: bool) -> list[str]:
    if not isinstance(value, Mapping):
        return [f"{path}: object"]
    errors: list[str] = []
    if value.get("motion_mode") not in _MOTION_MODES:
        errors.append(f"{path}.motion_mode: supported_motion_mode")
    audio_mode = value.get("audio_mode")
    if audio_mode not in _AUDIO_MODES:
        errors.append(f"{path}.audio_mode: supported_audio_mode")
    for key in ("lip_sync_required", "voice_authorization_required"):
        if not isinstance(value.get(key), bool):
            errors.append(f"{path}.{key}: boolean")
    if plan:
        if not isinstance(value.get("voice_likeness_rights_confirmed"), bool):
            errors.append(f"{path}.voice_likeness_rights_confirmed: boolean")
        capability = value.get("capability_declarations")
        if not isinstance(capability, Mapping) or capability.get("motion_supported") is not True or capability.get("audio_supported") is not True:
            errors.append(f"{path}.capability_declarations: capability_binding")
    voice_required = value.get("voice_authorization_required") is True
    voice_hash = value.get("voice_authorization_sha256")
    if voice_required:
        _sha_error(voice_hash, f"{path}.voice_authorization_sha256", errors)
    elif voice_hash is not None:
        errors.append(f"{path}.voice_authorization_sha256: null_when_not_required")
    if audio_mode == "clone-authorized-voice" and not voice_required:
        errors.append(f"{path}.voice_authorization_required: clone_requires_authorization")
    return errors


def _technical_sanity_errors(value: Any, path: str) -> list[str]:
    if not isinstance(value, Mapping):
        return [f"{path}: object"]
    errors: list[str] = []
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("semantic_action_not_proven") is not True
        or value.get("limitations") != _TECHNICAL_LIMITATION
    ):
        errors.append(f"{path}: version_or_limitations")
    for role in ("reference", "result"):
        metrics = value.get(role)
        if not isinstance(metrics, Mapping):
            errors.append(f"{path}.{role}: metrics")
            continue
        for key in ("sampled_frames", "adjacent_pairs", "repeated_adjacent_pairs", "static_adjacent_pairs", "black_frames", "max_freeze_run"):
            if not _is_int(metrics.get(key)) or int(metrics[key]) < 0:
                errors.append(f"{path}.{role}.{key}: nonnegative_integer")
        if not _is_number(metrics.get("mean_frame_difference")) or float(metrics["mean_frame_difference"]) < 0:
            errors.append(f"{path}.{role}.mean_frame_difference: nonnegative_number")
        if not isinstance(metrics.get("extreme_freeze_detected"), bool):
            errors.append(f"{path}.{role}.extreme_freeze_detected: boolean")
    return errors


def _request_semantic_errors(data: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if not _valid_id(data.get("output_id")):
        errors.append("$.output_id: portable_id")
    slots = data.get("input_slot_ids")
    if not isinstance(slots, list) or not 1 <= len(slots) <= 128 or len(set(slots)) != len(slots) or any(not _valid_id(item) for item in slots):
        errors.append("$.input_slot_ids: unique_portable_slots")
    if not isinstance(data.get("adapter_id"), str) or _ADAPTER_ID_RE.fullmatch(data["adapter_id"]) is None:
        errors.append("$.adapter_id: adapter_id")
    if not isinstance(data.get("adapter_version"), str) or _ADAPTER_VERSION_RE.fullmatch(data["adapter_version"]) is None:
        errors.append("$.adapter_version: adapter_version")
    if (
        data.get("privacy_profile") != "local-only"
        or data.get("execution_profile") != "local-file-drop"
        or data.get("cloud_upload_confirmed") is not False
        or "controller_label" in data
    ):
        errors.append("$: local_only_local_file_drop")
    capabilities = data.get("capabilities")
    if isinstance(capabilities, Mapping):
        for key, allowed in (("motion_modes", _MOTION_MODES), ("audio_modes", _AUDIO_MODES)):
            values = capabilities.get(key)
            if not isinstance(values, list) or not values or len(set(values)) != len(values) or any(item not in allowed for item in values):
                errors.append(f"$.capabilities.{key}: supported_unique_values")
        for key in ("lip_sync_supported", "clone_authorized_voice_supported"):
            if not isinstance(capabilities.get(key), bool):
                errors.append(f"$.capabilities.{key}: boolean")
    assertion = data.get("local_authorization_assertion")
    if assertion is not None:
        if not isinstance(assertion, Mapping):
            errors.append("$.local_authorization_assertion: object")
        else:
            if assertion.get("purpose") != "temporal-replacement" or assertion.get("provider") != data.get("adapter_id") or assertion.get("output_id") != data.get("output_id"):
                errors.append("$.local_authorization_assertion: scope_binding")
            if _authorization_expiry(assertion.get("expires_at")) is None:
                errors.append("$.local_authorization_assertion.expires_at: strict_utc")
    return errors


def _authorization_expiry(value: Any) -> datetime | None:
    """Parse the frozen wire format without making pure validation time-dependent."""

    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _assert_current_local_authorization(request: Mapping[str, Any]) -> None:
    """Enforce authorization freshness only for new operational decisions.

    Historical packet validation and delivery verification intentionally call
    no clock-sensitive gate, so an already-reviewed delivery remains
    verifiable after an authorization naturally expires.
    """

    assertion = request.get("local_authorization_assertion")
    if assertion is None:
        return
    expiry = _authorization_expiry(assertion.get("expires_at")) if isinstance(assertion, Mapping) else None
    if expiry is None or expiry <= datetime.now(timezone.utc):
        raise _invalid("local voice authorization is expired or invalid for a new temporal operation")


def _plan_semantic_errors(data: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if (
        data.get("privacy_profile") != "local-only"
        or data.get("execution_profile") != "local-file-drop"
        or data.get("cloud_upload_confirmed") is not False
        or "controller_label" in data
    ):
        errors.append("$: local_only_local_file_drop")
    for key in ("template_path", "manifest_path", "request_path"):
        if not _valid_relative_path(data.get(key)):
            errors.append(f"$.{key}: normalized_relative_path")
    if not _valid_direct_child(data.get("reference_pack")):
        errors.append("$.reference_pack: direct_child")
    for key in ("template_sha256", "manifest_sha256", "request_sha256", "input_assets_sha256", "reference_inventory_sha256"):
        _sha_error(data.get(key), f"$.{key}", errors)
    errors.extend(_input_assets_errors(data.get("input_assets"), "$.input_assets"))
    if isinstance(data.get("input_assets"), list) and data.get("input_assets_sha256") != _canonical_json_sha256(data["input_assets"]):
        errors.append("$.input_assets_sha256: canonical_binding")
    source = data.get("source_spec")
    output = data.get("output")
    if not isinstance(source, Mapping) or not all(_is_int(source.get(key)) and int(source[key]) >= 1 for key in ("width", "height", "frame_count")) or not _is_number(source.get("fps")):
        errors.append("$.source_spec: source_spec")
    if not isinstance(output, Mapping) or not _valid_id(output.get("id")) or not all(_is_int(output.get(key)) and int(output[key]) >= 2 for key in ("width", "height")):
        errors.append("$.output: output_spec")
    errors.extend(_requirements_errors(data.get("requirements"), "$.requirements", plan=True))
    _sha_error(data.get("requirements_sha256"), "$.requirements_sha256", errors)
    if isinstance(data.get("requirements"), Mapping) and data.get("requirements_sha256") != _canonical_json_sha256(data["requirements"]):
        errors.append("$.requirements_sha256: canonical_binding")
    errors.extend(_inventory_errors(data.get("reference_inventory"), "$.reference_inventory", result=False))
    if isinstance(data.get("reference_inventory"), list) and data.get("reference_inventory_sha256") != _canonical_json_sha256(data["reference_inventory"]):
        errors.append("$.reference_inventory_sha256: canonical_binding")
    evidence = data.get("evidence")
    if isinstance(evidence, Mapping):
        errors.extend(_artifact_errors(evidence.get("input_contact_sheet"), "$.evidence.input_contact_sheet"))
    return errors


def _plan_review_semantic_errors(data: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    _sha_error(data.get("plan_sha256"), "$.plan_sha256", errors)
    if data.get("privacy_profile") != "local-only" or data.get("cloud_upload_confirmed") is not False:
        errors.append("$: local_only")
    for key in (*_PLAN_CONFIRMATIONS, "voice_authorization_required", "voice_authorization_confirmed", "lip_sync_required", "lip_sync_confirmed"):
        if not isinstance(data.get(key), bool):
            errors.append(f"$.{key}: boolean")
    required = data.get("voice_authorization_required") is True
    voice_hash = data.get("voice_authorization_sha256")
    if required:
        _sha_error(voice_hash, "$.voice_authorization_sha256", errors)
    elif voice_hash is not None:
        errors.append("$.voice_authorization_sha256: null_when_not_required")
    return errors


def _proposal_semantic_errors(data: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("plan_path", "plan_review_path", "template_path", "manifest_path", "request_path"):
        if not _valid_relative_path(data.get(key)):
            errors.append(f"$.{key}: normalized_relative_path")
    for key in ("reference_pack", "result_pack"):
        if not _valid_direct_child(data.get(key)):
            errors.append(f"$.{key}: direct_child")
    for key in ("plan_sha256", "plan_review_sha256", "template_sha256", "manifest_sha256", "request_sha256", "input_assets_sha256", "reference_inventory_sha256", "result_inventory_sha256"):
        _sha_error(data.get(key), f"$.{key}", errors)
    errors.extend(_inventory_errors(data.get("result_inventory"), "$.result_inventory", result=True))
    if isinstance(data.get("result_inventory"), list) and data.get("result_inventory_sha256") != _canonical_json_sha256(data["result_inventory"]):
        errors.append("$.result_inventory_sha256: canonical_binding")
    output = data.get("output")
    if not isinstance(output, Mapping) or not _valid_id(output.get("id")):
        errors.append("$.output: output_spec")
    errors.extend(_requirements_errors(data.get("requirements"), "$.requirements", plan=False))
    _sha_error(data.get("requirements_sha256"), "$.requirements_sha256", errors)
    if isinstance(data.get("requirements"), Mapping) and data.get("requirements_sha256") != _canonical_json_sha256(data["requirements"]):
        errors.append("$.requirements_sha256: canonical_binding")
    audio = data.get("audio_validation")
    if not isinstance(audio, Mapping) or audio.get("expected_audio_streams") not in {0, 1} or not isinstance(audio.get("preserve_reference_payload_match"), bool):
        errors.append("$.audio_validation: audio_validation")
    else:
        for key in ("source_audio_payload_sha256", "result_audio_payload_sha256"):
            value = audio.get(key)
            if value is not None:
                _sha_error(value, f"$.audio_validation.{key}", errors)
    errors.extend(_technical_sanity_errors(data.get("technical_sanity"), "$.technical_sanity"))
    evidence = data.get("evidence")
    if isinstance(evidence, Mapping):
        errors.extend(_artifact_errors(evidence.get("results_contact_sheet"), "$.evidence.results_contact_sheet"))
        errors.extend(_artifact_errors(evidence.get("technical_sanity"), "$.evidence.technical_sanity"))
    return errors


def _results_review_semantic_errors(data: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    _sha_error(data.get("proposal_sha256"), "$.proposal_sha256", errors)
    for key in (*_RESULT_CONFIRMATIONS, "voice_authorization_required", "voice_likeness_confirmed", "lip_sync_required", "lip_sync_confirmed"):
        if not isinstance(data.get(key), bool):
            errors.append(f"$.{key}: boolean")
    return errors


def _delivery_report_semantic_errors(data: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("plan_path", "plan_review_path", "proposal_path", "results_review_path", "template_path", "manifest_path", "request_path"):
        if not _valid_relative_path(data.get(key)):
            errors.append(f"$.{key}: normalized_relative_path")
    for key in ("plan_sha256", "plan_review_sha256", "proposal_sha256", "results_review_sha256", "template_sha256", "manifest_sha256", "request_sha256", "input_assets_sha256", "reference_inventory_sha256", "result_inventory_sha256", "requirements_sha256"):
        _sha_error(data.get(key), f"$.{key}", errors)
    for key in ("reference_pack", "result_pack"):
        if not _valid_direct_child(data.get(key)):
            errors.append(f"$.{key}: direct_child")
    errors.extend(_artifact_errors(data.get("final_video"), "$.final_video"))
    errors.extend(_media_facts_errors(data.get("media"), "$.media"))
    errors.extend(_technical_sanity_errors(data.get("technical_sanity"), "$.technical_sanity"))
    return errors


def _validate_packet(data: Any, schema: Path, label: str, semantic: Any) -> list[str]:
    errors = _nonfinite_errors(data)
    errors.extend(_schema_errors(data, schema, label))
    if isinstance(data, Mapping):
        try:
            errors.extend(semantic(data))
        except Exception:
            errors.append("$: semantic.invalid")
    return _unique_errors(errors)


def validate_temporal_request_data(data: Any) -> list[str]:
    """Validate a private request without reading project files."""

    return _validate_packet(data, _REQUEST_SCHEMA_PATH, "temporal request", _request_semantic_errors)


def validate_temporal_plan_data(data: Any) -> list[str]:
    """Validate a public temporal plan without reading project files."""

    return _validate_packet(data, _PLAN_SCHEMA_PATH, "temporal plan", _plan_semantic_errors)


def validate_temporal_plan_review_data(data: Any) -> list[str]:
    return _validate_packet(data, _PLAN_REVIEW_SCHEMA_PATH, "temporal plan review", _plan_review_semantic_errors)


def validate_temporal_results_proposal_data(data: Any) -> list[str]:
    return _validate_packet(data, _RESULTS_PROPOSAL_SCHEMA_PATH, "temporal results proposal", _proposal_semantic_errors)


def validate_temporal_results_review_data(data: Any) -> list[str]:
    return _validate_packet(data, _RESULTS_REVIEW_SCHEMA_PATH, "temporal results review", _results_review_semantic_errors)


def validate_temporal_delivery_report_data(data: Any) -> list[str]:
    return _validate_packet(data, _DELIVERY_REPORT_SCHEMA_PATH, "temporal delivery report", _delivery_report_semantic_errors)


def _raise_validation(label: str, errors: Sequence[str]) -> None:
    del errors
    raise _invalid(f"{label} did not pass validation")


def _template_requirements(template: Mapping[str, Any], request: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    """Select the one template output and enforce the v0.10 Template IR gate."""

    if template.get("schema_version") != "0.3.0":
        raise _invalid("Temporal replacement requires Template IR 0.3.0")
    support = template.get("support")
    if not isinstance(support, Mapping) or support.get("review_required") is not False:
        raise _invalid("Temporal replacement requires support.review_required to be false")
    requirements = template.get("rebuild_requirements")
    if not isinstance(requirements, Mapping) or requirements.get("motion_required") is not True or requirements.get("motion_mode") not in _MOTION_MODES:
        raise _invalid("Template does not require a supported temporal motion mode")
    audio_mode = requirements.get("audio_mode")
    if audio_mode not in _AUDIO_MODES:
        raise _invalid("Template has an unsupported temporal audio mode")
    source = template.get("source")
    if not isinstance(source, Mapping) or not all(_is_int(source.get(key)) and int(source[key]) >= 1 for key in ("width", "height", "duration_frames")) or not _is_number(source.get("fps")):
        raise _invalid("Template source timing is invalid")
    output_id = request.get("output_id")
    outputs = template.get("outputs")
    output = next((item for item in outputs if isinstance(item, Mapping) and item.get("id") == output_id), None) if isinstance(outputs, list) else None
    if output is None or not _is_int(output.get("width")) or not _is_int(output.get("height")):
        raise _invalid("Temporal Request output_id does not select a Template output")
    return source, output, requirements


def _validate_manifest_snapshot(
    root: Path,
    template: Mapping[str, Any],
    snapshot: Any,
) -> Mapping[str, Any]:
    manifest = snapshot.data
    if not isinstance(manifest, Mapping) or manifest.get("schema_version") != "0.2.0":
        raise _invalid("Temporal replacement requires Asset Manifest 0.2.0")
    # This checks the frozen local-only Manifest structure and every declared
    # relative path without opening a media payload. Selected payload hashes
    # are verified descriptor-bound below.
    try:
        errors = video_remix.validate_assets_data(template, manifest, root / snapshot.relative_path, False, project_root=root)
    except Exception as exc:
        raise _invalid("Asset Manifest did not pass validation") from exc
    if errors:
        raise _invalid("Asset Manifest did not pass validation")
    return manifest


def _manifest_input_assets(
    root: Path,
    root_identity: Any,
    manifest: Mapping[str, Any],
    request: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Bind only selected frozen Manifest files, without leaking filenames."""

    requested = request.get("input_slot_ids")
    assets = manifest.get("assets")
    if not isinstance(requested, list) or not isinstance(assets, list):
        raise _invalid("Temporal Request or Asset Manifest is invalid")
    by_slot: dict[str, Mapping[str, Any]] = {}
    for item in assets:
        if not isinstance(item, Mapping) or not isinstance(item.get("slot_id"), str) or item["slot_id"] in by_slot:
            raise _invalid("Asset Manifest is invalid")
        by_slot[item["slot_id"]] = item
    bound: list[dict[str, str]] = []
    for slot_id in sorted(requested):
        item = by_slot.get(slot_id) if isinstance(slot_id, str) else None
        if not isinstance(item, Mapping) or item.get("rights_confirmed") is not True or item.get("cloud_upload_allowed") is not False:
            raise _invalid("Temporal Request references an unavailable frozen input slot")
        expected = item.get("sha256")
        media_type = item.get("media_type")
        source_path = item.get("path")
        if not isinstance(expected, str) or _SHA256_RE.fullmatch(expected.lower()) is None or not isinstance(media_type, str):
            raise _invalid("Asset Manifest selected input is invalid")
        try:
            _, raw = rrv_assets._read_project_file_bytes(
                root,
                root_identity,
                source_path,
                label="frozen temporal input asset",
                maximum_bytes=_MAX_FILE_BYTES,
            )
        except rrv_runtime.RRVError as exc:
            raise _invalid("Asset Manifest selected input is unavailable") from exc
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected.lower():
            raise _invalid("Asset Manifest selected input hash does not match")
        bound.append({"slot_id": slot_id, "sha256": actual, "media_type": media_type})
    if not bound:
        raise _invalid("Temporal Request must select frozen input slots")
    return bound


def _same_file_identity(left: Any, right: Any) -> bool:
    """Compare the bounded fields exposed by ``rrv_assets._FileIdentity``."""

    return (
        getattr(left, "device", None) == getattr(right, "device", None)
        and getattr(left, "inode", None) == getattr(right, "inode", None)
        and getattr(left, "size_bytes", None) == getattr(right, "size_bytes", None)
    )


@contextmanager
def _hold_staged_media(stage: Any, path: Path, *, label: str):
    """Bind a staged media pathname for every local tool/hash consumer.

    The stage is already a private descriptor-bound copy.  On the audited
    Windows platform, faithful's guard opens it with read-only sharing, which
    prevents a concurrent replace/delete/write while FFmpeg or FFprobe opens
    the same pathname.  Other platforms retain a descriptor identity and
    check it again after the operation; the staged copy is the immutable
    local snapshot boundary there.
    """

    rrv_propose._assert_stage_regular_file(stage, path, label)
    identity = rrv_assets._safe_regular_file(path, message=f"{label} changed")
    if not 1 <= identity.size_bytes <= _MAX_FILE_BYTES:
        raise _invalid("temporal media file exceeds the local size limit")
    expected = rrv_faithful._FileIdentity(  # type: ignore[attr-defined]
        path=path,
        device=identity.device,
        inode=identity.inode,
    )
    with rrv_faithful._hold_bound_file(stage.root.path, expected, label):  # type: ignore[attr-defined]
        yield identity
    after = rrv_assets._safe_regular_file(path, message=f"{label} changed")
    if not _same_file_identity(identity, after):
        raise _invalid("guarded temporal media changed while a local tool was reading it")
    rrv_propose._assert_stage_regular_file(stage, path, label)


def _stage_media_sha256(stage: Any, path: Path, *, label: str) -> str:
    """Hash a guarded staged media file up to the temporal 256 MiB bound.

    ``rrv_propose._stage_file_sha256`` intentionally caps evidence artifacts at
    128 MiB.  Temporal MP4s use the Asset Manifest's 256 MiB local-media
    bound, so keep this separate descriptor-bound primitive rather than
    accidentally narrowing accepted media or trusting a pathname.
    """

    with _hold_staged_media(stage, path, label=label) as identity:
        digest = hashlib.sha256()
        total = 0
        try:
            with rrv_assets._open_bound_file(identity, message=f"{label} changed") as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _MAX_FILE_BYTES:
                        raise _invalid("temporal media file exceeds the local size limit")
                    digest.update(chunk)
        except rrv_runtime.RRVError:
            raise
        except OSError as exc:
            raise _tool_error("could not hash guarded temporal media") from exc
        if total != identity.size_bytes:
            raise _invalid("guarded temporal media changed while reading")
        return digest.hexdigest()


def _media_artifact(root: Path, stage: Any, target: Path, path: Path) -> dict[str, str]:
    """Publish a video artifact without applying the evidence-file size cap."""

    rrv_propose._assert_stage_regular_file(stage, path, "staged temporal media artifact")
    try:
        relative = path.relative_to(stage.path)
    except ValueError as exc:  # pragma: no cover - internal containment invariant.
        raise _tool_error("staged temporal media artifact escaped its output directory") from exc
    return {
        "path": rrv_propose._lexical_relative_output_path(root, target / relative),
        "sha256": _stage_media_sha256(stage, path, label="staged temporal media artifact"),
    }


def _assert_exact_temporal_stage_files(stage: Any, expected_files: Mapping[str, str], *, label: str) -> None:
    """Require an exact, flat, hashed output set at the temporal publish edge."""

    if not isinstance(expected_files, Mapping) or not expected_files:
        raise _tool_error("temporal publication expected files are invalid")
    expected_names: set[str] = set()
    for name, digest in expected_files.items():
        try:
            canonical = rrv_assets._direct_child_name(name, "temporal publication artifact")
        except rrv_runtime.RRVError as exc:
            raise _tool_error("temporal publication expected files are invalid") from exc
        if canonical in expected_names or not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            raise _tool_error("temporal publication expected files are invalid")
        expected_names.add(canonical)
    rrv_propose._assert_stage_live(stage)
    try:
        with os.scandir(stage.path) as entries:
            actual_names = {entry.name for entry in entries}
    except OSError as exc:
        raise _tool_error("could not inspect temporal publication stage") from exc
    if actual_names != expected_names:
        raise _invalid(f"{label} stage does not contain exactly its published artifacts")
    for name in sorted(expected_names):
        path = stage.path / name
        rrv_propose._assert_stage_regular_file(stage, path, f"{label} staged artifact")
        if _stage_media_sha256(stage, path, label=f"{label} staged artifact") != expected_files[name]:
            raise _invalid(f"{label} staged artifact changed before publication")
    rrv_propose._assert_stage_live(stage)


def _publish_exact_temporal_stage(
    root: Path,
    stage: Any,
    target: Path,
    *,
    label: str,
    expected_files: Mapping[str, str],
) -> None:
    """Apply temporal exact-set verification immediately before atomic publish."""

    _assert_exact_temporal_stage_files(stage, expected_files, label=label)
    rrv_propose._publish_stage(root, stage, target, label=label, expected_files=expected_files)


def _copy_snapshot_to_stage(
    root: Path,
    stage: Any,
    name: str,
    snapshot: Any,
    *,
    expected_sha256: str,
    expected_size: int,
    label: str,
) -> Path:
    """Copy a descriptor-bound private snapshot into its guarded stage."""

    if not _SHA256_RE.fullmatch(expected_sha256) or not _is_int(expected_size) or not 1 <= expected_size <= _MAX_FILE_BYTES:
        raise _invalid("temporal media snapshot metadata is invalid")
    destination = rrv_propose._stage_path(root, stage, name)
    digest = hashlib.sha256()
    total = 0
    try:
        snapshot.seek(0)
        with rrv_propose._open_stage_output_file(stage, destination, label) as handle:
            while True:
                chunk = snapshot.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_FILE_BYTES:
                    raise _invalid("temporal media snapshot exceeds the local size limit")
                digest.update(chunk)
                handle.write(chunk)
    except rrv_runtime.RRVError:
        raise
    except Exception as exc:
        raise _tool_error("could not stage a local temporal media snapshot") from exc
    if total != expected_size or digest.hexdigest() != expected_sha256:
        raise _invalid("temporal media changed while preparing its snapshot")
    rrv_propose._assert_stage_regular_file(stage, destination, label)
    if _stage_media_sha256(stage, destination, label=label) != expected_sha256:
        raise _invalid("temporal media snapshot did not remain stable")
    return destination


def _scan_one_video_pack(
    root: Path,
    root_identity: Any,
    pack: Path,
    pack_identity: Any,
    pack_name: str,
    *,
    stage: Any,
    snapshot_name: str,
    required_filename: str | None,
    asset_id: str,
) -> tuple[dict[str, Any], Path]:
    """Require one direct-child MP4 and return only a staged immutable copy."""

    del pack_name
    try:
        with os.scandir(pack) as entries:
            children = list(entries)
    except OSError as exc:
        raise _invalid("temporal media pack could not be scanned") from exc
    if len(children) != 1:
        raise _invalid("temporal media pack must contain exactly one video and no sidecars")
    child = children[0]
    name = child.name
    if not rrv_assets._portable_path_component(name) or not name.lower().endswith(".mp4"):
        raise _invalid("temporal media pack must contain one direct-child MP4")
    if required_filename is not None and os.path.normcase(name) != os.path.normcase(required_filename):
        raise _invalid("temporal result pack must contain temporal-replacement.mp4 exactly")
    identity = rrv_assets._safe_regular_file(pack / name, message="temporal media pack contains an unsafe file")
    if not 1 <= identity.size_bytes <= _MAX_FILE_BYTES:
        raise _invalid("temporal media file exceeds the local size limit")
    snapshot = None
    try:
        snapshot, digest = rrv_assets._snapshot_bound_asset(identity)
        staged = _copy_snapshot_to_stage(
            root,
            stage,
            snapshot_name,
            snapshot,
            expected_sha256=digest,
            expected_size=identity.size_bytes,
            label="temporal media private snapshot",
        )
    finally:
        if snapshot is not None:
            snapshot.close()
    # The staged snapshot is the sole media input for FFmpeg/FFprobe.  Before
    # returning its inventory, still reject a pack entry that drifted while it
    # was being snapshotted rather than silently accepting a later sidecar,
    # hard-link swap, replacement, or same-size content mutation.
    current = rrv_assets._safe_regular_file(pack / name, message="temporal media pack changed while scanning")
    if (
        current.device != identity.device
        or current.inode != identity.inode
        or current.size_bytes != identity.size_bytes
    ):
        raise _invalid("temporal media pack changed while scanning")
    confirmation = None
    try:
        confirmation, confirmed_digest = rrv_assets._snapshot_bound_asset(current)
        if confirmed_digest != digest:
            raise _invalid("temporal media pack changed while scanning")
    finally:
        if confirmation is not None:
            confirmation.close()
    rrv_assets._assert_pack_live(root_identity, pack_identity)
    return (
        {"asset_id": asset_id, "sha256": digest, "size_bytes": identity.size_bytes, "media_type": "video/mp4"},
        staged,
    )


def _positive_int(value: Any) -> int:
    if not _is_int(value) or int(value) < 1:
        raise _invalid("temporal media facts are invalid")
    return int(value)


def _positive_number(value: Any) -> float:
    if not _is_number(value) or float(value) <= 0:
        raise _invalid("temporal media facts are invalid")
    return float(value)


def _rate(value: Any) -> float:
    if not isinstance(value, str) or "/" not in value:
        raise _invalid("temporal media frame rate is invalid")
    left, right = value.split("/", 1)
    try:
        numerator, denominator = int(left), int(right)
    except ValueError as exc:
        raise _invalid("temporal media frame rate is invalid") from exc
    if numerator <= 0 or denominator <= 0:
        raise _invalid("temporal media frame rate is invalid")
    return numerator / denominator


def _rotation_is_clear(stream: Mapping[str, Any]) -> bool:
    values: list[Any] = []
    tags = stream.get("tags")
    if isinstance(tags, Mapping) and "rotate" in tags:
        values.append(tags.get("rotate"))
    side_data = stream.get("side_data_list")
    if isinstance(side_data, list):
        for item in side_data:
            if isinstance(item, Mapping) and "rotation" in item:
                values.append(item.get("rotation"))
    for value in values:
        try:
            if not math.isclose(float(value), 0.0, abs_tol=1e-9):
                return False
        except (TypeError, ValueError):
            return False
    return True


def _inspect_staged_media(
    stage: Any,
    source: Path,
    *,
    ffprobe: str | os.PathLike[str],
    timeout_seconds: float,
    role: str,
) -> dict[str, Any]:
    """Strict profile inspection performed only against a guarded stage file."""

    try:
        with _hold_staged_media(stage, source, label="temporal media private snapshot"):
            raw = rrv_nle._full_ffprobe_facts(source, os.fspath(ffprobe), timeout_seconds=timeout_seconds)
            timing = rrv_nle._exact_timing(source, os.fspath(ffprobe), timeout_seconds=timeout_seconds)
    except rrv_runtime.RRVError:
        raise
    except Exception as exc:
        raise _tool_error("local temporal media inspection failed") from exc
    if not isinstance(raw, Mapping) or not isinstance(timing, Mapping):
        raise _tool_error("local temporal media inspection returned invalid facts")
    format_data = raw.get("format")
    streams = raw.get("streams")
    chapters = raw.get("chapters")
    programs = raw.get("programs")
    if (
        not isinstance(format_data, Mapping)
        or not isinstance(format_data.get("format_name"), str)
        or "mp4" not in {part.strip().lower() for part in format_data["format_name"].split(",")}
        or not isinstance(streams, list)
        or not streams
        or not isinstance(chapters, list)
        or chapters
        or (programs is not None and (not isinstance(programs, list) or programs))
    ):
        raise _invalid("temporal media does not meet the MP4 profile")
    if not all(isinstance(stream, Mapping) for stream in streams):
        raise _invalid("temporal media stream facts are invalid")
    unsupported = [stream for stream in streams if stream.get("codec_type") not in {"video", "audio"}]
    videos = [stream for stream in streams if stream.get("codec_type") == "video"]
    audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if unsupported or len(videos) != 1 or len(audios) > 1:
        raise _invalid("temporal media must contain only one video and at most one audio stream")
    video = videos[0]
    disposition = video.get("disposition")
    if isinstance(disposition, Mapping) and disposition.get("attached_pic") not in (None, 0):
        raise _invalid("temporal media rejects cover-art video streams")
    if not _rotation_is_clear(video):
        raise _invalid("temporal media rotation metadata must be clear")
    width, height = _positive_int(video.get("width")), _positive_int(video.get("height"))
    if video.get("codec_name") != "h264" or video.get("profile") != "High" or video.get("pix_fmt") != "yuv420p":
        raise _invalid("temporal media must be H.264 High 8-bit yuv420p")
    raw_depth = video.get("bits_per_raw_sample")
    try:
        bit_depth = int(raw_depth)
    except (TypeError, ValueError) as exc:
        raise _invalid("temporal media bit depth is unavailable") from exc
    if bit_depth != 8:
        raise _invalid("temporal media must be 8-bit")
    if timing.get("cfr_confirmed") is not True:
        raise _invalid("temporal media must have exact CFR timing")
    fps = _positive_number(timing.get("fps"))
    frames = _positive_int(timing.get("frame_count"))
    duration = _positive_number(timing.get("duration_seconds"))
    if duration > MAX_DURATION_SECONDS + 1e-9 or not math.isclose(duration, frames / fps, rel_tol=1e-7, abs_tol=1e-6):
        raise _invalid("temporal media timing exceeds its local profile")
    for field in ("r_frame_rate", "avg_frame_rate"):
        if not math.isclose(_rate(video.get(field)), fps, rel_tol=1e-9, abs_tol=1e-12):
            raise _invalid("temporal media declared rate is not exact CFR")
    if audios:
        audio = audios[0]
        if (
            audio.get("codec_name") != "aac"
            or audio.get("profile") != "LC"
            or str(audio.get("sample_rate")) != "48000"
            or audio.get("channels") != 2
            or audio.get("channel_layout") != "stereo"
        ):
            raise _invalid("temporal audio must be AAC-LC 48 kHz stereo")
        audio_fields: dict[str, Any] = {
            "has_audio": True,
            "audio_stream_count": 1,
            "audio_codec": "aac",
            "audio_profile": "LC",
            "audio_sample_rate": 48000,
            "audio_channels": 2,
            "audio_channel_layout": "stereo",
        }
    else:
        audio_fields = {
            "has_audio": False,
            "audio_stream_count": 0,
            "audio_codec": None,
            "audio_profile": None,
            "audio_sample_rate": None,
            "audio_channels": None,
            "audio_channel_layout": None,
        }
    result = {
        "container": "mp4",
        "width": width,
        "height": height,
        "fps": float(fps),
        "frame_count": frames,
        "duration_seconds": float(duration),
        "cfr": True,
        "video_codec": "h264",
        "video_profile": "High",
        "pixel_format": "yuv420p",
        "bit_depth": 8,
        "rotation_degrees": 0,
        **audio_fields,
    }
    errors = _media_facts_errors(result, "$.media")
    if errors:
        raise _invalid("temporal media facts do not meet the required profile")
    return result


def _require_media_matches(
    facts: Mapping[str, Any],
    *,
    width: int,
    height: int,
    fps: float,
    frame_count: int,
    expected_audio_streams: int | None = None,
    role: str,
) -> None:
    if (
        facts.get("width") != width
        or facts.get("height") != height
        or facts.get("frame_count") != frame_count
        or not _is_number(facts.get("fps"))
        or not math.isclose(float(facts["fps"]), float(fps), rel_tol=1e-9, abs_tol=1e-9)
    ):
        raise _invalid(f"{role} timing or dimensions do not match the bound Template")
    if expected_audio_streams is not None and facts.get("audio_stream_count") != expected_audio_streams:
        raise _invalid(f"{role} audio streams do not match the bound audio mode")


def _require_reference_audio_for_mode(facts: Mapping[str, Any], requirements: Mapping[str, Any]) -> None:
    """Fail before Plan publication when preserve-reference lacks its source."""

    if requirements.get("audio_mode") == "preserve-reference" and facts.get("audio_stream_count") != 1:
        raise _invalid("preserve-reference requires one approved AAC-LC action-reference audio stream")


def _reject_result_metadata(
    stage: Any,
    source: Path,
    *,
    ffprobe: str | os.PathLike[str],
    timeout_seconds: float,
) -> None:
    """Allow only FFmpeg's unavoidable muxer tags on a candidate result.

    This is a rejection gate, not a remux: a user-authored title, comment,
    description, unknown global tag, or arbitrary per-stream tag is never
    silently carried into the reviewed byte-copy delivery.
    """

    try:
        with _hold_staged_media(stage, source, label="temporal result private snapshot"):
            raw = rrv_faithful._probe_metadata(  # type: ignore[attr-defined]
                source,
                os.fspath(ffprobe),
                timeout_seconds=timeout_seconds,
                runner=None,
            )
    except rrv_runtime.RRVError:
        raise
    except Exception as exc:
        raise _tool_error("temporal result metadata inspection failed") from exc
    if not _temporal_metadata_is_stripped(raw):
        raise _invalid("temporal result contains disallowed metadata")


def _temporal_metadata_is_stripped(raw: Any) -> bool:
    """Use the faithful gate, with FFmpeg 9's generated libx264 suffix.

    FFmpeg 9 writes ``Lavc<version> libx264`` in its own video-stream
    encoder tag.  That is a generated structural tag, not inherited input
    metadata.  Every other rule remains the faithful gate's whitelist, so
    title/comment/description/custom tags still fail closed.
    """

    if rrv_faithful.metadata_is_stripped(raw):
        return True
    if not isinstance(raw, Mapping):
        return False
    format_data = raw.get("format")
    streams = raw.get("streams")
    if not isinstance(format_data, Mapping) or not isinstance(streams, list) or not streams:
        return False
    format_tags = format_data.get("tags", {})
    if not isinstance(format_tags, Mapping):
        return False
    for key, value in format_tags.items():
        if not isinstance(key, str) or not isinstance(value, str):
            return False
        normalized = key.lower()
        if normalized not in {"major_brand", "minor_version", "compatible_brands", "encoder"}:
            return False
        if normalized == "major_brand" and re.fullmatch(r"[A-Za-z0-9]{4}", value) is None:
            return False
        if normalized == "minor_version" and re.fullmatch(r"[0-9]{1,10}", value) is None:
            return False
        if normalized == "compatible_brands" and re.fullmatch(r"[A-Za-z0-9]{4,64}", value) is None:
            return False
        if normalized == "encoder" and re.fullmatch(r"Lavf[0-9.]+", value) is None:
            return False
    video_streams = 0
    for stream in streams:
        if not isinstance(stream, Mapping):
            return False
        codec_type = stream.get("codec_type")
        if codec_type not in {"video", "audio"}:
            return False
        video_streams += int(codec_type == "video")
        tags = stream.get("tags", {})
        if not isinstance(tags, Mapping):
            return False
        for key, value in tags.items():
            if not isinstance(key, str) or not isinstance(value, str):
                return False
            normalized = key.lower()
            if normalized not in {"handler_name", "vendor_id", "encoder", "language"}:
                return False
            if normalized == "language" and value != "und":
                return False
            if normalized == "handler_name" and value != ("VideoHandler" if codec_type == "video" else "SoundHandler"):
                return False
            if normalized == "vendor_id" and re.fullmatch(r"(?:\[0\]){4}|[A-Za-z0-9]{4}", value) is None:
                return False
            if normalized == "encoder":
                suffix = "libx264" if codec_type == "video" else "aac"
                if re.fullmatch(rf"Lavc[0-9.]+(?: {suffix})?", value) is None:
                    return False
    return video_streams == 1


def _full_decode(
    stage: Any,
    source: Path,
    facts: Mapping[str, Any],
    *,
    ffmpeg: str | os.PathLike[str],
    timeout_seconds: float,
) -> Mapping[str, Any]:
    try:
        with _hold_staged_media(stage, source, label="temporal media private snapshot"):
            qa = rrv_nle._full_decode_qa(
                source,
                os.fspath(ffmpeg),
                timeout_seconds=timeout_seconds,
                expected_frames=int(facts["frame_count"]),
                has_audio=facts.get("has_audio") is True,
            )
    except rrv_runtime.RRVError:
        raise
    except Exception as exc:
        raise _tool_error("full temporal media decode failed") from exc
    return qa


def _audio_payload_hash(
    stage: Any,
    source: Path,
    *,
    ffprobe: str | os.PathLike[str],
    timeout_seconds: float,
) -> str:
    try:
        with _hold_staged_media(stage, source, label="temporal media private snapshot"):
            payload = rrv_faithful.stream_payload_hash(source, ffprobe, "a", timeout_seconds=timeout_seconds)
    except rrv_runtime.RRVError:
        raise
    except Exception as exc:
        raise _tool_error("temporal audio payload inspection failed") from exc
    if not isinstance(payload.sha256, str) or _SHA256_RE.fullmatch(payload.sha256) is None:
        raise _tool_error("temporal audio payload inspection returned invalid facts")
    return payload.sha256


def _gray_analysis_command(source: Path, ffmpeg: str | os.PathLike[str], output: Path) -> list[str]:
    return [
        os.fspath(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-xerror",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
        "-vf",
        f"scale={_GRAY_WIDTH}:{_GRAY_HEIGHT}:flags=area,format=gray",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "-fps_mode",
        "passthrough",
        str(output),
    ]


def _temporal_metrics(
    root: Path,
    stage: Any,
    source: Path,
    facts: Mapping[str, Any],
    *,
    ffmpeg: str | os.PathLike[str],
    timeout_seconds: float,
    name: str,
) -> dict[str, Any]:
    """Produce negative temporal checks; this deliberately proves no action semantics."""

    expected_frames = int(facts["frame_count"])
    raw_path = rrv_propose._stage_path(root, stage, name)
    try:
        with _hold_staged_media(stage, source, label="temporal media private snapshot"):
            rrv_propose._run_output(
                stage,
                _gray_analysis_command(source, ffmpeg, raw_path),
                raw_path,
                timeout_seconds,
                "temporal grayscale analysis",
            )
        identity = rrv_assets._safe_regular_file(raw_path, message="temporal grayscale analysis changed")
        expected_size = expected_frames * _GRAY_WIDTH * _GRAY_HEIGHT
        if identity.size_bytes != expected_size:
            raise _invalid("temporal grayscale analysis did not yield the exact frame count")
        raw = rrv_assets._read_bound_bytes(identity, maximum_bytes=expected_size, message="temporal grayscale analysis could not be read")
        frame_bytes = _GRAY_WIDTH * _GRAY_HEIGHT
        frames = [raw[index * frame_bytes : (index + 1) * frame_bytes] for index in range(expected_frames)]
        black_frames = sum(sum(frame) <= len(frame) * 2 for frame in frames)
        repeated = 0
        static = 0
        difference_sum = 0.0
        freeze_run = 1
        maximum_freeze_run = 1
        for prior, current in zip(frames, frames[1:]):
            if prior == current:
                repeated += 1
            difference = sum(abs(left - right) for left, right in zip(prior, current)) / (len(prior) * 255.0)
            difference_sum += difference
            is_static = difference <= (1.0 / 255.0)
            if is_static:
                static += 1
                freeze_run += 1
            else:
                freeze_run = 1
            maximum_freeze_run = max(maximum_freeze_run, freeze_run)
        adjacent = max(0, expected_frames - 1)
        mean_difference = difference_sum / adjacent if adjacent else 0.0
        extreme = (
            expected_frames <= 1
            or black_frames >= math.ceil(expected_frames * 0.95)
            or (adjacent > 0 and repeated >= math.ceil(adjacent * 0.95))
            or (adjacent > 0 and static >= math.ceil(adjacent * 0.95))
            or maximum_freeze_run >= math.ceil(expected_frames * 0.90)
            or (adjacent > 0 and mean_difference <= 0.0002)
        )
        return {
            "sampled_frames": expected_frames,
            "adjacent_pairs": adjacent,
            "repeated_adjacent_pairs": repeated,
            "static_adjacent_pairs": static,
            "black_frames": black_frames,
            "mean_frame_difference": round(mean_difference, 9),
            "max_freeze_run": maximum_freeze_run,
            "extreme_freeze_detected": extreme,
        }
    finally:
        try:
            if "raw_path" in locals():
                rrv_propose._remove_stage_file(stage, raw_path)
        except Exception:
            pass


def _sample_frames(frame_count: int) -> list[int]:
    if frame_count < 1:
        raise _invalid("temporal frame count is invalid")
    count = min(_CONTACT_FRAMES, frame_count)
    if count == 1:
        return [0]
    return sorted({round(index * (frame_count - 1) / (count - 1)) for index in range(count)})


def _write_contact_sheet(
    root: Path,
    stage: Any,
    output: Path,
    sources: Sequence[tuple[str, Path, Mapping[str, Any]]],
    *,
    ffmpeg: str | os.PathLike[str],
    timeout_seconds: float,
) -> None:
    """Fixed-frame local visual evidence, intentionally without source names."""

    Image, ImageDraw = rrv_assets._load_pillow()
    if not sources:
        raise _invalid("temporal contact sheet needs a staged source")
    frame_count = int(sources[0][2]["frame_count"])
    samples = _sample_frames(frame_count)
    card_width, card_height, heading = 220, 158, 20
    canvas = Image.new("RGB", (len(samples) * card_width, len(sources) * card_height + heading), (245, 247, 250))
    draw = ImageDraw.Draw(canvas)
    temporary: list[Path] = []
    try:
        for row, (label, source, facts) in enumerate(sources):
            if int(facts["frame_count"]) != frame_count:
                raise _invalid("temporal contact sheet sources do not share timing")
            for column, number in enumerate(samples):
                frame_path = rrv_propose._stage_path(root, stage, f".temporal-contact-{row}-{column}.png")
                temporary.append(frame_path)
                with _hold_staged_media(stage, source, label="temporal media private snapshot"):
                    command = rrv_propose._build_evidence_frame_command(
                        source,
                        os.fspath(ffmpeg),
                        {"x": 0, "y": 0, "width": int(facts["width"]), "height": int(facts["height"])},
                        number,
                        frame_path,
                    )
                    rrv_propose._run_output(stage, command, frame_path, timeout_seconds, "temporal contact frame", image_output=True)
                x, y = column * card_width, heading + row * card_height
                draw.rectangle((x + 3, y + 3, x + card_width - 4, y + card_height - 4), fill="white", outline=(185, 193, 204))
                image = None
                thumbnail = None
                try:
                    with Image.open(frame_path) as opened:
                        image = opened.convert("RGB")
                    thumbnail = image.copy()
                    thumbnail.thumbnail((card_width - 12, card_height - 38), Image.Resampling.LANCZOS)
                    canvas.paste(thumbnail, (x + (card_width - thumbnail.width) // 2, y + 7))
                finally:
                    if thumbnail is not None:
                        thumbnail.close()
                    if image is not None:
                        image.close()
                draw.text((x + 7, y + card_height - 24), f"{label}  FRAME {number}", fill=(55, 65, 81))
        draw.text((8, 3), "FIXED TEMPORAL FRAME SAMPLES — HUMAN ACTION REVIEW REQUIRED", fill=(55, 65, 81))
        with rrv_propose._open_stage_output_file(stage, output, "temporal contact sheet") as handle:
            canvas.save(handle, format="PNG", optimize=False, compress_level=9)
        rrv_propose._assert_stage_regular_file(stage, output, "temporal contact sheet")
    except rrv_runtime.RRVError:
        raise
    except Exception as exc:
        raise _tool_error("could not write temporal contact sheet") from exc
    finally:
        canvas.close()
        for path in temporary:
            try:
                rrv_propose._remove_stage_file(stage, path)
            except Exception:
                pass


def _validate_evidence_artifact(
    root: Path,
    root_identity: Any,
    owner_path: str,
    artifact: Any,
    *,
    expected_filename: str,
    label: str,
    maximum_bytes: int,
) -> None:
    if not isinstance(artifact, Mapping):
        raise _invalid(f"{label} evidence is invalid")
    owner_parts = rrv_assets._relative_path_parts(owner_path)
    artifact_parts = rrv_assets._relative_path_parts(artifact.get("path"))
    expected_hash = artifact.get("sha256")
    if (
        owner_parts is None
        or artifact_parts is None
        or artifact_parts != (*owner_parts[:-1], expected_filename)
        or not isinstance(expected_hash, str)
        or _SHA256_RE.fullmatch(expected_hash) is None
    ):
        raise _invalid(f"{label} evidence is invalid")
    try:
        _, raw = rrv_assets._read_project_file_bytes(root, root_identity, artifact.get("path"), label=f"{label} evidence", maximum_bytes=maximum_bytes)
    except rrv_runtime.RRVError as exc:
        raise _invalid(f"{label} evidence is invalid") from exc
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise _invalid(f"{label} evidence hash does not match")


def _plan_review_template(plan_sha256: str, plan: Mapping[str, Any]) -> dict[str, Any]:
    requirements = plan.get("requirements") if isinstance(plan.get("requirements"), Mapping) else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "plan_sha256": plan_sha256,
        "privacy_profile": plan.get("privacy_profile"),
        "cloud_upload_confirmed": plan.get("cloud_upload_confirmed"),
        "decision": "pending",
        **{key: False for key in _PLAN_CONFIRMATIONS},
        "voice_authorization_required": requirements.get("voice_authorization_required") is True,
        "voice_authorization_sha256": requirements.get("voice_authorization_sha256"),
        "voice_authorization_confirmed": False,
        "lip_sync_required": requirements.get("lip_sync_required") is True,
        "lip_sync_confirmed": False,
    }


def _results_review_template(proposal_sha256: str, proposal: Mapping[str, Any]) -> dict[str, Any]:
    requirements = proposal.get("requirements") if isinstance(proposal.get("requirements"), Mapping) else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "proposal_sha256": proposal_sha256,
        "decision": "pending",
        **{key: False for key in _RESULT_CONFIRMATIONS},
        "voice_authorization_required": requirements.get("voice_authorization_required") is True,
        "voice_likeness_confirmed": False,
        "lip_sync_required": requirements.get("lip_sync_required") is True,
        "lip_sync_confirmed": False,
    }


def _approved_plan_review(plan: Mapping[str, Any], plan_sha256: str, review: Mapping[str, Any]) -> None:
    requirements = plan.get("requirements")
    if not isinstance(requirements, Mapping):
        raise _invalid("Temporal Plan requirements are invalid")
    if (
        review.get("plan_sha256") != plan_sha256
        or review.get("privacy_profile") != plan.get("privacy_profile")
        or review.get("cloud_upload_confirmed") != plan.get("cloud_upload_confirmed")
        or review.get("decision") != "approved"
        or any(review.get(key) is not True for key in _PLAN_CONFIRMATIONS)
        or review.get("voice_authorization_required") is not (requirements.get("voice_authorization_required") is True)
        or review.get("voice_authorization_sha256") != requirements.get("voice_authorization_sha256")
        or review.get("lip_sync_required") is not (requirements.get("lip_sync_required") is True)
    ):
        raise _invalid("Temporal Plan Review is not a complete approval of the exact plan")
    if requirements.get("voice_authorization_required") is True and review.get("voice_authorization_confirmed") is not True:
        raise _invalid("Temporal Plan Review lacks the required voice authorization confirmation")
    if requirements.get("lip_sync_required") is True and review.get("lip_sync_confirmed") is not True:
        raise _invalid("Temporal Plan Review lacks the required lip-sync confirmation")


def _approved_results_review(proposal: Mapping[str, Any], proposal_sha256: str, review: Mapping[str, Any]) -> None:
    requirements = proposal.get("requirements")
    if not isinstance(requirements, Mapping):
        raise _invalid("Temporal Results Proposal requirements are invalid")
    if (
        review.get("proposal_sha256") != proposal_sha256
        or review.get("decision") != "approved"
        or any(review.get(key) is not True for key in _RESULT_CONFIRMATIONS)
        or review.get("voice_authorization_required") is not (requirements.get("voice_authorization_required") is True)
        or review.get("lip_sync_required") is not (requirements.get("lip_sync_required") is True)
    ):
        raise _invalid("Temporal Results Review is not a complete approval of the exact proposal")
    if requirements.get("voice_authorization_required") is True and review.get("voice_likeness_confirmed") is not True:
        raise _invalid("Temporal Results Review lacks the required voice confirmation")
    if requirements.get("lip_sync_required") is True and review.get("lip_sync_confirmed") is not True:
        raise _invalid("Temporal Results Review lacks the required lip-sync confirmation")


def _requirements_from_inputs(
    template_requirements: Mapping[str, Any], request: Mapping[str, Any]
) -> dict[str, Any]:
    capabilities = request.get("capabilities")
    if not isinstance(capabilities, Mapping):
        raise _invalid("Temporal Request capabilities are invalid")
    motion_mode = template_requirements.get("motion_mode")
    audio_mode = template_requirements.get("audio_mode")
    if motion_mode not in capabilities.get("motion_modes", []) or audio_mode not in capabilities.get("audio_modes", []):
        raise _invalid("Temporal Request capability declarations do not cover the Template")
    lip_sync = template_requirements.get("lip_sync_required") is True
    voice_required = audio_mode == "clone-authorized-voice"
    if lip_sync and capabilities.get("lip_sync_supported") is not True:
        raise _invalid("Temporal Request lacks the required local lip-sync capability declaration")
    voice_hash: str | None = None
    if voice_required:
        assertion = request.get("local_authorization_assertion")
        if (
            template_requirements.get("voice_likeness_rights_confirmed") is not True
            or capabilities.get("clone_authorized_voice_supported") is not True
            or not isinstance(assertion, Mapping)
        ):
            raise _invalid("Temporal voice replacement lacks a valid local authorization")
        voice_hash = _canonical_json_sha256(assertion)
    return {
        "motion_mode": motion_mode,
        "audio_mode": audio_mode,
        "lip_sync_required": lip_sync,
        "voice_likeness_rights_confirmed": template_requirements.get("voice_likeness_rights_confirmed") is True,
        "voice_authorization_required": voice_required,
        "voice_authorization_sha256": voice_hash,
        "capability_declarations": {
            "motion_supported": True,
            "audio_supported": True,
            "lip_sync_supported": capabilities.get("lip_sync_supported") is True,
            "clone_authorized_voice_supported": capabilities.get("clone_authorized_voice_supported") is True,
        },
    }


def _source_spec(source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "width": int(source["width"]),
        "height": int(source["height"]),
        "fps": float(source["fps"]),
        "frame_count": int(source["duration_frames"]),
    }


def _output_spec(output: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": output["id"], "width": int(output["width"]), "height": int(output["height"])}


def _opaque_inventory(asset: Mapping[str, Any], facts: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"asset_id": asset["asset_id"], "sha256": asset["sha256"], "size_bytes": asset["size_bytes"], "media_type": "video/mp4", "facts": dict(facts)}]


def _validate_plan_static_bindings(
    root: Path,
    root_identity: Any,
    plan: Mapping[str, Any],
    *,
    template_snapshot: Any,
    manifest_snapshot: Any,
    request_snapshot: Any,
    enforce_current_authorization: bool,
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], list[dict[str, str]], dict[str, Any], dict[str, Any], dict[str, Any]]:
    template = rrv_assets._validate_template_snapshot(template_snapshot)
    request = request_snapshot.data
    request_errors = validate_temporal_request_data(request)
    if request_errors or not isinstance(request, Mapping):
        _raise_validation("Temporal Request", request_errors)
    if enforce_current_authorization:
        _assert_current_local_authorization(request)
    source, output, template_requirements = _template_requirements(template, request)
    manifest = _validate_manifest_snapshot(root, template, manifest_snapshot)
    input_assets = _manifest_input_assets(root, root_identity, manifest, request)
    requirements = _requirements_from_inputs(template_requirements, request)
    source_spec, output_spec = _source_spec(source), _output_spec(output)
    if (
        plan.get("template_path") != template_snapshot.relative_path
        or plan.get("template_sha256") != template_snapshot.sha256
        or plan.get("template_id") != template.get("template_id")
        or plan.get("manifest_path") != manifest_snapshot.relative_path
        or plan.get("manifest_sha256") != manifest_snapshot.sha256
        or plan.get("manifest_schema_version") != "0.2.0"
        or plan.get("request_path") != request_snapshot.relative_path
        or plan.get("request_sha256") != request_snapshot.sha256
        or plan.get("privacy_profile") != request.get("privacy_profile")
        or plan.get("execution_profile") != request.get("execution_profile")
        or plan.get("adapter_id") != request.get("adapter_id")
        or plan.get("adapter_version") != request.get("adapter_version")
        or plan.get("cloud_upload_confirmed") is not False
        or plan.get("input_assets") != input_assets
        or plan.get("input_assets_sha256") != _canonical_json_sha256(input_assets)
        or plan.get("source_spec") != source_spec
        or plan.get("output") != output_spec
        or plan.get("requirements") != requirements
        or plan.get("requirements_sha256") != _canonical_json_sha256(requirements)
        or plan.get("scanner_policy_version") != SCANNER_POLICY_VERSION
    ):
        raise _invalid("Temporal Plan inputs changed since plan creation")
    return template, manifest, request, input_assets, source_spec, output_spec, requirements


def _validate_plan_reference_binding(plan: Mapping[str, Any], inventory: Sequence[Mapping[str, Any]]) -> None:
    expected = list(inventory)
    if (
        plan.get("reference_inventory") != expected
        or plan.get("reference_inventory_sha256") != _canonical_json_sha256(expected)
    ):
        raise _invalid("Temporal reference pack changed since plan creation")


def _validate_proposal_packet_bindings(
    proposal: Mapping[str, Any],
    *,
    plan_snapshot: Any,
    plan_review_snapshot: Any,
    plan: Mapping[str, Any],
) -> None:
    if (
        proposal.get("plan_path") != plan_snapshot.relative_path
        or proposal.get("plan_sha256") != plan_snapshot.sha256
        or proposal.get("plan_review_path") != plan_review_snapshot.relative_path
        or proposal.get("plan_review_sha256") != plan_review_snapshot.sha256
        or proposal.get("template_path") != plan.get("template_path")
        or proposal.get("template_sha256") != plan.get("template_sha256")
        or proposal.get("template_id") != plan.get("template_id")
        or proposal.get("manifest_path") != plan.get("manifest_path")
        or proposal.get("manifest_sha256") != plan.get("manifest_sha256")
        or proposal.get("request_path") != plan.get("request_path")
        or proposal.get("request_sha256") != plan.get("request_sha256")
        or proposal.get("input_assets_sha256") != plan.get("input_assets_sha256")
        or proposal.get("reference_pack") != plan.get("reference_pack")
        or proposal.get("reference_inventory_sha256") != plan.get("reference_inventory_sha256")
        or proposal.get("scanner_policy_version") != SCANNER_POLICY_VERSION
        or proposal.get("output") != plan.get("output")
        or proposal.get("requirements_sha256") != plan.get("requirements_sha256")
    ):
        raise _invalid("Temporal Results Proposal does not bind the approved Temporal Plan")
    plan_requirements = plan.get("requirements")
    expected_requirements = _proposal_requirements(plan_requirements) if isinstance(plan_requirements, Mapping) else None
    if proposal.get("requirements") != expected_requirements:
        raise _invalid("Temporal Results Proposal requirements do not bind the approved plan")


def _validate_proposal_result_binding(proposal: Mapping[str, Any], inventory: Sequence[Mapping[str, Any]]) -> None:
    expected = list(inventory)
    if proposal.get("result_inventory") != expected or proposal.get("result_inventory_sha256") != _canonical_json_sha256(expected):
        raise _invalid("temporal result pack changed since results proposal")


def _proposal_requirements(plan_requirements: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "motion_mode": plan_requirements["motion_mode"],
        "audio_mode": plan_requirements["audio_mode"],
        "lip_sync_required": plan_requirements["lip_sync_required"],
        "voice_likeness_rights_confirmed": plan_requirements["voice_likeness_rights_confirmed"],
        "voice_authorization_required": plan_requirements["voice_authorization_required"],
        "voice_authorization_sha256": plan_requirements["voice_authorization_sha256"],
        "capability_declarations": dict(plan_requirements["capability_declarations"]),
    }


def _expected_audio_streams(requirements: Mapping[str, Any]) -> int:
    return 0 if requirements.get("audio_mode") == "mute" else 1


def _audio_validation(
    reference_facts: Mapping[str, Any],
    result_facts: Mapping[str, Any],
    requirements: Mapping[str, Any],
    *,
    stage: Any,
    reference_snapshot: Path,
    result_snapshot: Path,
    ffprobe: str | os.PathLike[str],
    timeout_seconds: float,
) -> dict[str, Any]:
    expected = _expected_audio_streams(requirements)
    if result_facts.get("audio_stream_count") != expected:
        raise _invalid("temporal result audio streams do not match the required audio mode")
    if requirements.get("audio_mode") != "preserve-reference":
        return {
            "expected_audio_streams": expected,
            "preserve_reference_payload_match": False,
            "source_audio_payload_sha256": None,
            "result_audio_payload_sha256": None,
        }
    if reference_facts.get("audio_stream_count") != 1 or result_facts.get("audio_stream_count") != 1:
        raise _invalid("preserve-reference requires one approved AAC-LC reference audio stream")
    source_hash = _audio_payload_hash(stage, reference_snapshot, ffprobe=ffprobe, timeout_seconds=timeout_seconds)
    result_hash = _audio_payload_hash(stage, result_snapshot, ffprobe=ffprobe, timeout_seconds=timeout_seconds)
    if source_hash != result_hash:
        raise _invalid("preserve-reference result audio payload does not match the approved reference")
    return {
        "expected_audio_streams": expected,
        "preserve_reference_payload_match": True,
        "source_audio_payload_sha256": source_hash,
        "result_audio_payload_sha256": result_hash,
    }


def _technical_sanity(
    root: Path,
    stage: Any,
    reference_snapshot: Path,
    reference_facts: Mapping[str, Any],
    result_snapshot: Path,
    result_facts: Mapping[str, Any],
    *,
    ffmpeg: str | os.PathLike[str],
    timeout_seconds: float,
) -> dict[str, Any]:
    reference = _temporal_metrics(
        root, stage, reference_snapshot, reference_facts, ffmpeg=ffmpeg, timeout_seconds=timeout_seconds, name=".reference-gray.raw"
    )
    result = _temporal_metrics(
        root, stage, result_snapshot, result_facts, ffmpeg=ffmpeg, timeout_seconds=timeout_seconds, name=".result-gray.raw"
    )
    if result.get("extreme_freeze_detected") is True:
        raise _invalid("temporal result is static, black, single-frame, or extremely frozen")
    return {
        "schema_version": SCHEMA_VERSION,
        "semantic_action_not_proven": True,
        "limitations": _TECHNICAL_LIMITATION,
        "reference": reference,
        "result": result,
    }


def _copy_stage_file(
    root: Path,
    stage: Any,
    source: Path,
    destination_name: str,
    *,
    expected_sha256: str,
) -> Path:
    """Byte-copy a guarded staged result; this function never invokes FFmpeg."""

    destination = rrv_propose._stage_path(root, stage, destination_name)
    digest = hashlib.sha256()
    try:
        with _hold_staged_media(stage, source, label="reviewed temporal result snapshot") as identity:
            with rrv_assets._open_bound_file(identity, message="reviewed temporal result snapshot changed") as source_handle:
                with rrv_propose._open_stage_output_file(stage, destination, "reviewed temporal result byte copy") as destination_handle:
                    while True:
                        chunk = source_handle.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                        destination_handle.write(chunk)
    except rrv_runtime.RRVError:
        raise
    except OSError as exc:
        raise _tool_error("could not byte-copy reviewed temporal result") from exc
    if digest.hexdigest() != expected_sha256:
        raise _invalid("reviewed temporal result changed while copying")
    rrv_propose._assert_stage_regular_file(stage, destination, "reviewed temporal result byte copy")
    if _stage_media_sha256(stage, destination, label="reviewed temporal result byte copy") != expected_sha256:
        raise _invalid("reviewed temporal result copy did not remain stable")
    return destination


def _snapshot_project_file_to_stage(
    root: Path,
    root_identity: Any,
    value: Any,
    *,
    stage: Any,
    name: str,
    expected_sha256: str,
    label: str,
) -> Path:
    try:
        _, raw = rrv_assets._read_project_file_bytes(root, root_identity, value, label=label, maximum_bytes=_MAX_FILE_BYTES)
    except rrv_runtime.RRVError as exc:
        raise _invalid(f"{label} is unavailable") from exc
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise _invalid(f"{label} hash does not match")
    from io import BytesIO

    return _copy_snapshot_to_stage(
        root,
        stage,
        name,
        BytesIO(raw),
        expected_sha256=actual,
        expected_size=len(raw),
        label=label,
    )


def prepare_temporal_replacement(
    template: str | os.PathLike[str],
    manifest: str | os.PathLike[str],
    request: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str],
    reference_pack: str | os.PathLike[str],
    temporal_rights_confirmed: bool,
    output_dir: str | os.PathLike[str] = "temporal-plan",
    ffmpeg: str | os.PathLike[str] = "ffmpeg",
    ffprobe: str | os.PathLike[str] = "ffprobe",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Mapping[str, Any]:
    """Prepare a review-required temporal plan without invoking a generator.

    The literal rights gate is intentionally before project-root, path, JSON,
    Pillow, FFmpeg, FFprobe, Manifest asset, or pack access.
    """

    if temporal_rights_confirmed is not True:
        raise _invalid("temporal_rights_confirmed must be explicitly true before local temporal planning")
    root = rrv_assets._safe_project_root(project_root)
    timeout = rrv_assets._parse_timeout(timeout_seconds)
    reference_pack_name = rrv_assets._direct_child_name(reference_pack, "reference_pack")
    stage: Any = None
    try:
        with rrv_assets._root_guard(root) as root_identity:
            target = rrv_assets._direct_output_target(root, output_dir)
            request_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, request, label="temporal request")
            request_data = request_snapshot.data
            request_errors = validate_temporal_request_data(request_data)
            if request_errors or not isinstance(request_data, Mapping):
                _raise_validation("Temporal Request", request_errors)
            _assert_current_local_authorization(request_data)
            template_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, template, label="template")
            template_data = rrv_assets._validate_template_snapshot(template_snapshot)
            source, selected_output, template_requirements = _template_requirements(template_data, request_data)
            manifest_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, manifest, label="asset manifest")
            manifest_data = _validate_manifest_snapshot(root, template_data, manifest_snapshot)
            input_assets = _manifest_input_assets(root, root_identity, manifest_data, request_data)
            requirements = _requirements_from_inputs(template_requirements, request_data)
            source_spec, output_spec = _source_spec(source), _output_spec(selected_output)
            stage = rrv_propose._new_staging_directory(root, "temporal-plan")
            with rrv_assets._asset_pack_guard(root, root_identity, reference_pack_name) as (reference_directory, reference_identity):
                reference_asset, reference_snapshot_path = _scan_one_video_pack(
                    root,
                    root_identity,
                    reference_directory,
                    reference_identity,
                    reference_pack_name,
                    stage=stage,
                    snapshot_name=".reference-action.mp4",
                    required_filename=None,
                    asset_id="action-reference.0001",
                )
                reference_facts = _inspect_staged_media(
                    stage, reference_snapshot_path, ffprobe=ffprobe, timeout_seconds=timeout, role="action reference"
                )
                _require_media_matches(
                    reference_facts,
                    width=source_spec["width"],
                    height=source_spec["height"],
                    fps=source_spec["fps"],
                    frame_count=source_spec["frame_count"],
                    role="action reference",
                )
                _require_reference_audio_for_mode(reference_facts, requirements)
                _full_decode(stage, reference_snapshot_path, reference_facts, ffmpeg=ffmpeg, timeout_seconds=timeout)
                reference_inventory = _opaque_inventory(reference_asset, reference_facts)
                contact_path = rrv_propose._stage_path(root, stage, "temporal-input-contact-sheet.png")
                _write_contact_sheet(
                    root,
                    stage,
                    contact_path,
                    [("ACTION REFERENCE", reference_snapshot_path, reference_facts)],
                    ffmpeg=ffmpeg,
                    timeout_seconds=timeout,
                )
                contact_artifact = rrv_assets._artifact(root, stage, target, contact_path)
                plan_data: dict[str, Any] = {
                    "schema_version": SCHEMA_VERSION,
                    "privacy_profile": request_data.get("privacy_profile"),
                    "cloud_upload_confirmed": False,
                    "execution_profile": request_data.get("execution_profile"),
                    "adapter_id": request_data.get("adapter_id"),
                    "adapter_version": request_data.get("adapter_version"),
                    "temporal_rights_confirmed": True,
                    "review_required": True,
                    "template_path": template_snapshot.relative_path,
                    "template_sha256": template_snapshot.sha256,
                    "template_id": template_data.get("template_id"),
                    "manifest_path": manifest_snapshot.relative_path,
                    "manifest_sha256": manifest_snapshot.sha256,
                    "manifest_schema_version": "0.2.0",
                    "request_path": request_snapshot.relative_path,
                    "request_sha256": request_snapshot.sha256,
                    "input_assets": input_assets,
                    "input_assets_sha256": _canonical_json_sha256(input_assets),
                    "reference_pack": reference_pack_name,
                    "scanner_policy_version": SCANNER_POLICY_VERSION,
                    "source_spec": source_spec,
                    "output": output_spec,
                    "requirements": requirements,
                    "requirements_sha256": _canonical_json_sha256(requirements),
                    "reference_inventory": reference_inventory,
                    "reference_inventory_sha256": _canonical_json_sha256(reference_inventory),
                    "evidence": {"input_contact_sheet": contact_artifact},
                }
                plan_errors = validate_temporal_plan_data(plan_data)
                if plan_errors:
                    _raise_validation("generated Temporal Plan", plan_errors)
                plan_path = rrv_propose._stage_path(root, stage, "temporal-replacement-plan.json")
                rrv_assets._write_json(stage, root, plan_path, plan_data, "Temporal Plan JSON")
                plan_sha256 = rrv_propose._stage_file_sha256(stage, plan_path)
                review_data = _plan_review_template(plan_sha256, plan_data)
                review_errors = validate_temporal_plan_review_data(review_data)
                if review_errors:
                    _raise_validation("generated Temporal Plan Review", review_errors)
                review_path = rrv_propose._stage_path(root, stage, "temporal-replacement-plan-review.template.json")
                rrv_assets._write_json(stage, root, review_path, review_data, "Temporal Plan Review JSON")
                plan_artifact = rrv_assets._artifact(root, stage, target, plan_path)
                review_artifact = rrv_assets._artifact(root, stage, target, review_path)
                rrv_propose._remove_stage_file(stage, reference_snapshot_path)
                rrv_assets._assert_pack_live(root_identity, reference_identity)
                expected_files = {
                    "temporal-input-contact-sheet.png": contact_artifact["sha256"],
                    "temporal-replacement-plan.json": plan_artifact["sha256"],
                    "temporal-replacement-plan-review.template.json": review_artifact["sha256"],
                }
                _publish_exact_temporal_stage(root, stage, target, label="Temporal Plan", expected_files=expected_files)
                stage = None
                return {
                    "schema_version": SCHEMA_VERSION,
                    "review_required": True,
                    "execution_profile": plan_data["execution_profile"],
                    "counts": {"reference_inventory_entries": 1, "input_assets": len(input_assets)},
                    "artifacts": {
                        "temporal_plan": plan_artifact,
                        "review_template": review_artifact,
                        "input_contact_sheet": contact_artifact,
                    },
                }
    except BaseException as exc:
        rrv_propose._cleanup_directory(root, stage)
        raise _safe_exception(exc) from None


def propose_temporal_results(
    plan: str | os.PathLike[str],
    plan_review: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str],
    result_pack: str | os.PathLike[str],
    temporal_results_rights_confirmed: bool,
    output_dir: str | os.PathLike[str] = "temporal-results-proposal",
    ffmpeg: str | os.PathLike[str] = "ffmpeg",
    ffprobe: str | os.PathLike[str] = "ffprobe",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Mapping[str, Any]:
    """Inspect one fresh local temporal result drop after plan approval."""

    if temporal_results_rights_confirmed is not True:
        raise _invalid("temporal_results_rights_confirmed must be explicitly true before local temporal result analysis")
    root = rrv_assets._safe_project_root(project_root)
    timeout = rrv_assets._parse_timeout(timeout_seconds)
    result_pack_name = rrv_assets._direct_child_name(result_pack, "result_pack")
    stage: Any = None
    try:
        with rrv_assets._root_guard(root) as root_identity:
            target = rrv_assets._direct_output_target(root, output_dir)
            plan_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, plan, label="Temporal Plan")
            review_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, plan_review, label="Temporal Plan Review")
            plan_data, review_data = plan_snapshot.data, review_snapshot.data
            plan_errors = validate_temporal_plan_data(plan_data)
            review_errors = validate_temporal_plan_review_data(review_data)
            if plan_errors or not isinstance(plan_data, Mapping):
                _raise_validation("Temporal Plan", plan_errors)
            if review_errors or not isinstance(review_data, Mapping):
                _raise_validation("Temporal Plan Review", review_errors)
            reference_pack_name = rrv_assets._direct_child_name(plan_data.get("reference_pack"), "Temporal Plan reference_pack")
            if _same_direct_child(reference_pack_name, result_pack_name):
                raise _invalid("result_pack must be a new direct child distinct from reference_pack")
            _approved_plan_review(plan_data, plan_snapshot.sha256, review_data)
            template_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, plan_data.get("template_path"), label="template")
            manifest_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, plan_data.get("manifest_path"), label="asset manifest")
            request_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, plan_data.get("request_path"), label="temporal request")
            _, _, _, _, source_spec, output_spec, requirements = _validate_plan_static_bindings(
                root,
                root_identity,
                plan_data,
                template_snapshot=template_snapshot,
                manifest_snapshot=manifest_snapshot,
                request_snapshot=request_snapshot,
                enforce_current_authorization=True,
            )
            evidence = plan_data.get("evidence")
            _validate_evidence_artifact(
                root,
                root_identity,
                plan_snapshot.relative_path,
                evidence.get("input_contact_sheet") if isinstance(evidence, Mapping) else None,
                expected_filename="temporal-input-contact-sheet.png",
                label="Temporal Plan",
                maximum_bytes=rrv_assets.MAX_CONTACT_SHEET_BYTES,
            )
            stage = rrv_propose._new_staging_directory(root, "temporal-results-proposal")
            with rrv_assets._asset_pack_guard(root, root_identity, reference_pack_name) as (reference_directory, reference_identity):
                reference_asset, reference_snapshot_path = _scan_one_video_pack(
                    root, root_identity, reference_directory, reference_identity, reference_pack_name,
                    stage=stage, snapshot_name=".reference-action.mp4", required_filename=None, asset_id="action-reference.0001"
                )
                reference_facts = _inspect_staged_media(stage, reference_snapshot_path, ffprobe=ffprobe, timeout_seconds=timeout, role="action reference")
                _require_media_matches(reference_facts, width=source_spec["width"], height=source_spec["height"], fps=source_spec["fps"], frame_count=source_spec["frame_count"], role="action reference")
                _require_reference_audio_for_mode(reference_facts, requirements)
                _full_decode(stage, reference_snapshot_path, reference_facts, ffmpeg=ffmpeg, timeout_seconds=timeout)
                reference_inventory = _opaque_inventory(reference_asset, reference_facts)
                _validate_plan_reference_binding(plan_data, reference_inventory)
                with rrv_assets._asset_pack_guard(root, root_identity, result_pack_name) as (result_directory, result_identity):
                    if _same_directory_identity(reference_identity, result_identity):
                        raise _invalid("result_pack must be a distinct local directory")
                    result_asset, result_snapshot_path = _scan_one_video_pack(
                        root, root_identity, result_directory, result_identity, result_pack_name,
                        stage=stage, snapshot_name=".temporal-result.mp4", required_filename="temporal-replacement.mp4", asset_id="temporal-result.0001"
                    )
                    result_facts = _inspect_staged_media(stage, result_snapshot_path, ffprobe=ffprobe, timeout_seconds=timeout, role="temporal result")
                    _require_media_matches(
                        result_facts,
                        width=output_spec["width"],
                        height=output_spec["height"],
                        fps=source_spec["fps"],
                        frame_count=source_spec["frame_count"],
                        expected_audio_streams=_expected_audio_streams(requirements),
                        role="temporal result",
                    )
                    _reject_result_metadata(stage, result_snapshot_path, ffprobe=ffprobe, timeout_seconds=timeout)
                    _full_decode(stage, result_snapshot_path, result_facts, ffmpeg=ffmpeg, timeout_seconds=timeout)
                    audio_validation = _audio_validation(
                        reference_facts, result_facts, requirements, stage=stage, reference_snapshot=reference_snapshot_path,
                        result_snapshot=result_snapshot_path, ffprobe=ffprobe, timeout_seconds=timeout
                    )
                    technical_sanity = _technical_sanity(
                        root, stage, reference_snapshot_path, reference_facts, result_snapshot_path, result_facts,
                        ffmpeg=ffmpeg, timeout_seconds=timeout
                    )
                    result_inventory = _opaque_inventory(result_asset, result_facts)
                    contact_path = rrv_propose._stage_path(root, stage, "temporal-results-contact-sheet.png")
                    _write_contact_sheet(
                        root, stage, contact_path,
                        [("ACTION REFERENCE", reference_snapshot_path, reference_facts), ("TEMPORAL RESULT", result_snapshot_path, result_facts)],
                        ffmpeg=ffmpeg, timeout_seconds=timeout
                    )
                    contact_artifact = rrv_assets._artifact(root, stage, target, contact_path)
                    technical_path = rrv_propose._stage_path(root, stage, "temporal-technical-sanity.json")
                    rrv_assets._write_json(stage, root, technical_path, technical_sanity, "temporal technical sanity JSON")
                    technical_artifact = rrv_assets._artifact(root, stage, target, technical_path)
                    proposal_requirements = _proposal_requirements(requirements)
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
                        "manifest_path": plan_data.get("manifest_path"),
                        "manifest_sha256": plan_data.get("manifest_sha256"),
                        "request_path": plan_data.get("request_path"),
                        "request_sha256": plan_data.get("request_sha256"),
                        "input_assets_sha256": plan_data.get("input_assets_sha256"),
                        "reference_pack": reference_pack_name,
                        "reference_inventory_sha256": plan_data.get("reference_inventory_sha256"),
                        "result_pack": result_pack_name,
                        "scanner_policy_version": SCANNER_POLICY_VERSION,
                        "output": output_spec,
                        "requirements": proposal_requirements,
                        "requirements_sha256": _canonical_json_sha256(proposal_requirements),
                        "result_inventory": result_inventory,
                        "result_inventory_sha256": _canonical_json_sha256(result_inventory),
                        "audio_validation": audio_validation,
                        "technical_sanity": technical_sanity,
                        "evidence": {"results_contact_sheet": contact_artifact, "technical_sanity": technical_artifact},
                    }
                    proposal_errors = validate_temporal_results_proposal_data(proposal_data)
                    if proposal_errors:
                        _raise_validation("generated Temporal Results Proposal", proposal_errors)
                    proposal_path = rrv_propose._stage_path(root, stage, "temporal-results-proposal.json")
                    rrv_assets._write_json(stage, root, proposal_path, proposal_data, "Temporal Results Proposal JSON")
                    proposal_sha256 = rrv_propose._stage_file_sha256(stage, proposal_path)
                    results_review = _results_review_template(proposal_sha256, proposal_data)
                    results_review_errors = validate_temporal_results_review_data(results_review)
                    if results_review_errors:
                        _raise_validation("generated Temporal Results Review", results_review_errors)
                    review_path = rrv_propose._stage_path(root, stage, "temporal-results-review.template.json")
                    rrv_assets._write_json(stage, root, review_path, results_review, "Temporal Results Review JSON")
                    proposal_artifact = rrv_assets._artifact(root, stage, target, proposal_path)
                    review_artifact = rrv_assets._artifact(root, stage, target, review_path)
                    rrv_propose._remove_stage_file(stage, reference_snapshot_path)
                    rrv_propose._remove_stage_file(stage, result_snapshot_path)
                    rrv_assets._assert_pack_live(root_identity, reference_identity)
                    rrv_assets._assert_pack_live(root_identity, result_identity)
                    expected_files = {
                        "temporal-results-contact-sheet.png": contact_artifact["sha256"],
                        "temporal-technical-sanity.json": technical_artifact["sha256"],
                        "temporal-results-proposal.json": proposal_artifact["sha256"],
                        "temporal-results-review.template.json": review_artifact["sha256"],
                    }
                    _publish_exact_temporal_stage(root, stage, target, label="Temporal Results Proposal", expected_files=expected_files)
                    stage = None
                    return {
                        "schema_version": SCHEMA_VERSION,
                        "review_required": True,
                        "counts": {"result_inventory_entries": 1},
                        "artifacts": {
                            "proposal": proposal_artifact,
                            "review_template": review_artifact,
                            "results_contact_sheet": contact_artifact,
                            "technical_sanity": technical_artifact,
                        },
                    }
    except BaseException as exc:
        rrv_propose._cleanup_directory(root, stage)
        raise _safe_exception(exc) from None


def _validate_proposal_evidence(
    root: Path,
    root_identity: Any,
    proposal_snapshot: Any,
    proposal: Mapping[str, Any],
) -> None:
    evidence = proposal.get("evidence")
    _validate_evidence_artifact(
        root,
        root_identity,
        proposal_snapshot.relative_path,
        evidence.get("results_contact_sheet") if isinstance(evidence, Mapping) else None,
        expected_filename="temporal-results-contact-sheet.png",
        label="Temporal Results Proposal",
        maximum_bytes=rrv_assets.MAX_CONTACT_SHEET_BYTES,
    )
    _validate_evidence_artifact(
        root,
        root_identity,
        proposal_snapshot.relative_path,
        evidence.get("technical_sanity") if isinstance(evidence, Mapping) else None,
        expected_filename="temporal-technical-sanity.json",
        label="Temporal Results Proposal",
        maximum_bytes=_MAX_TECHNICAL_EVIDENCE_BYTES,
    )
    technical_artifact = evidence.get("technical_sanity") if isinstance(evidence, Mapping) else None
    if not isinstance(technical_artifact, Mapping):
        raise _invalid("Temporal Results Proposal technical evidence is invalid")
    technical_snapshot = rrv_assets._read_project_json_snapshot(
        root, root_identity, technical_artifact.get("path"), label="temporal technical evidence"
    )
    if technical_snapshot.sha256 != technical_artifact.get("sha256") or technical_snapshot.data != proposal.get("technical_sanity"):
        raise _invalid("Temporal Results Proposal technical evidence does not bind the proposal")


def _validate_delivery_packet_bindings(
    report: Mapping[str, Any],
    *,
    report_snapshot: Any,
    plan_snapshot: Any,
    plan_review_snapshot: Any,
    proposal_snapshot: Any,
    results_review_snapshot: Any,
) -> None:
    if (
        report.get("plan_path") != plan_snapshot.relative_path
        or report.get("plan_sha256") != plan_snapshot.sha256
        or report.get("plan_review_path") != plan_review_snapshot.relative_path
        or report.get("plan_review_sha256") != plan_review_snapshot.sha256
        or report.get("proposal_path") != proposal_snapshot.relative_path
        or report.get("proposal_sha256") != proposal_snapshot.sha256
        or report.get("results_review_path") != results_review_snapshot.relative_path
        or report.get("results_review_sha256") != results_review_snapshot.sha256
    ):
        raise _invalid("Temporal Delivery Report does not bind its reviewed packets")
    final_artifact = report.get("final_video")
    report_parts = rrv_assets._relative_path_parts(report_snapshot.relative_path)
    final_parts = rrv_assets._relative_path_parts(final_artifact.get("path")) if isinstance(final_artifact, Mapping) else None
    if report_parts is None or final_parts != (*report_parts[:-1], "temporal-replacement.mp4"):
        raise _invalid("Temporal Delivery Report final video path is invalid")


def _assert_exact_delivery_set(
    root: Path,
    root_identity: Any,
    report_snapshot: Any,
    final_artifact: Mapping[str, Any],
) -> None:
    """Reject any delivery-pack sidecar, nested entry, link, or extra file."""

    report_parts = rrv_assets._relative_path_parts(report_snapshot.relative_path)
    final_parts = rrv_assets._relative_path_parts(final_artifact.get("path"))
    if (
        report_parts is None
        or len(report_parts) != 2
        or final_parts != (report_parts[0], "temporal-replacement.mp4")
        or report_parts[1] != "temporal-delivery-report.json"
    ):
        raise _invalid("Temporal Delivery Report output directory is invalid")
    output_name = rrv_assets._direct_child_name(report_parts[0], "Temporal Delivery output")
    expected_hashes = {
        "temporal-replacement.mp4": final_artifact.get("sha256"),
        "temporal-delivery-report.json": report_snapshot.sha256,
    }
    if any(not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None for value in expected_hashes.values()):
        raise _invalid("Temporal Delivery Report output hashes are invalid")
    expected_names = set(expected_hashes)
    with rrv_assets._asset_pack_guard(root, root_identity, output_name) as (directory, identity):
        try:
            with os.scandir(directory) as entries:
                actual_names = {entry.name for entry in entries}
        except OSError as exc:
            raise _invalid("Temporal Delivery output could not be scanned") from exc
        if actual_names != expected_names:
            raise _invalid("Temporal Delivery output must contain exactly its report and reviewed MP4")
        for name in sorted(expected_names):
            file_identity = rrv_assets._safe_regular_file(
                directory / name, message="Temporal Delivery output contains an unsafe artifact"
            )
            snapshot = None
            try:
                snapshot, digest = rrv_assets._snapshot_bound_asset(file_identity)
            finally:
                if snapshot is not None:
                    snapshot.close()
            if digest != expected_hashes[name]:
                raise _invalid("Temporal Delivery output artifact hash changed")
        rrv_assets._assert_pack_live(root_identity, identity)


def freeze_temporal_delivery(
    plan: str | os.PathLike[str],
    plan_review: str | os.PathLike[str],
    proposal: str | os.PathLike[str],
    results_review: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str],
    output_dir: str | os.PathLike[str] = "temporal-delivery",
    ffmpeg: str | os.PathLike[str] = "ffmpeg",
    ffprobe: str | os.PathLike[str] = "ffprobe",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Mapping[str, Any]:
    """Byte-copy one fully reviewed result to a new atomic delivery directory."""

    root = rrv_assets._safe_project_root(project_root)
    timeout = rrv_assets._parse_timeout(timeout_seconds)
    stage: Any = None
    try:
        with rrv_assets._root_guard(root) as root_identity:
            target = rrv_assets._direct_output_target(root, output_dir)
            plan_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, plan, label="Temporal Plan")
            plan_review_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, plan_review, label="Temporal Plan Review")
            proposal_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, proposal, label="Temporal Results Proposal")
            results_review_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, results_review, label="Temporal Results Review")
            plan_data, plan_review_data = plan_snapshot.data, plan_review_snapshot.data
            proposal_data, results_review_data = proposal_snapshot.data, results_review_snapshot.data
            validators = (
                (validate_temporal_plan_data, plan_data, "Temporal Plan"),
                (validate_temporal_plan_review_data, plan_review_data, "Temporal Plan Review"),
                (validate_temporal_results_proposal_data, proposal_data, "Temporal Results Proposal"),
                (validate_temporal_results_review_data, results_review_data, "Temporal Results Review"),
            )
            for validator, data, label in validators:
                errors = validator(data)
                if errors or not isinstance(data, Mapping):
                    _raise_validation(label, errors)
            if _same_direct_child(plan_data.get("reference_pack"), proposal_data.get("result_pack")):
                raise _invalid("Temporal Results Proposal result_pack must be distinct from reference_pack")
            # Review rejection happens before evidence, Template/Manifest input
            # assets, or media packs are opened.
            _approved_plan_review(plan_data, plan_snapshot.sha256, plan_review_data)
            _validate_proposal_packet_bindings(
                proposal_data, plan_snapshot=plan_snapshot, plan_review_snapshot=plan_review_snapshot, plan=plan_data
            )
            _approved_results_review(proposal_data, proposal_snapshot.sha256, results_review_data)
            template_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, plan_data.get("template_path"), label="template")
            manifest_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, plan_data.get("manifest_path"), label="asset manifest")
            request_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, plan_data.get("request_path"), label="temporal request")
            _, _, _, input_assets, source_spec, output_spec, requirements = _validate_plan_static_bindings(
                root,
                root_identity,
                plan_data,
                template_snapshot=template_snapshot,
                manifest_snapshot=manifest_snapshot,
                request_snapshot=request_snapshot,
                enforce_current_authorization=True,
            )
            plan_evidence = plan_data.get("evidence")
            _validate_evidence_artifact(
                root, root_identity, plan_snapshot.relative_path,
                plan_evidence.get("input_contact_sheet") if isinstance(plan_evidence, Mapping) else None,
                expected_filename="temporal-input-contact-sheet.png", label="Temporal Plan", maximum_bytes=rrv_assets.MAX_CONTACT_SHEET_BYTES
            )
            _validate_proposal_evidence(root, root_identity, proposal_snapshot, proposal_data)
            reference_pack_name = rrv_assets._direct_child_name(plan_data.get("reference_pack"), "Temporal Plan reference_pack")
            result_pack_name = rrv_assets._direct_child_name(proposal_data.get("result_pack"), "Temporal Results Proposal result_pack")
            stage = rrv_propose._new_staging_directory(root, "temporal-delivery")
            with rrv_assets._asset_pack_guard(root, root_identity, reference_pack_name) as (reference_directory, reference_identity):
                reference_asset, reference_snapshot_path = _scan_one_video_pack(
                    root, root_identity, reference_directory, reference_identity, reference_pack_name,
                    stage=stage, snapshot_name=".reference-action.mp4", required_filename=None, asset_id="action-reference.0001"
                )
                reference_facts = _inspect_staged_media(stage, reference_snapshot_path, ffprobe=ffprobe, timeout_seconds=timeout, role="action reference")
                _require_media_matches(reference_facts, width=source_spec["width"], height=source_spec["height"], fps=source_spec["fps"], frame_count=source_spec["frame_count"], role="action reference")
                _require_reference_audio_for_mode(reference_facts, requirements)
                _full_decode(stage, reference_snapshot_path, reference_facts, ffmpeg=ffmpeg, timeout_seconds=timeout)
                reference_inventory = _opaque_inventory(reference_asset, reference_facts)
                _validate_plan_reference_binding(plan_data, reference_inventory)
                with rrv_assets._asset_pack_guard(root, root_identity, result_pack_name) as (result_directory, result_identity):
                    if _same_directory_identity(reference_identity, result_identity):
                        raise _invalid("Temporal result pack must be a distinct local directory")
                    result_asset, result_snapshot_path = _scan_one_video_pack(
                        root, root_identity, result_directory, result_identity, result_pack_name,
                        stage=stage, snapshot_name=".temporal-result.mp4", required_filename="temporal-replacement.mp4", asset_id="temporal-result.0001"
                    )
                    result_facts = _inspect_staged_media(stage, result_snapshot_path, ffprobe=ffprobe, timeout_seconds=timeout, role="temporal result")
                    _require_media_matches(result_facts, width=output_spec["width"], height=output_spec["height"], fps=source_spec["fps"], frame_count=source_spec["frame_count"], expected_audio_streams=_expected_audio_streams(requirements), role="temporal result")
                    _reject_result_metadata(stage, result_snapshot_path, ffprobe=ffprobe, timeout_seconds=timeout)
                    _full_decode(stage, result_snapshot_path, result_facts, ffmpeg=ffmpeg, timeout_seconds=timeout)
                    result_inventory = _opaque_inventory(result_asset, result_facts)
                    _validate_proposal_result_binding(proposal_data, result_inventory)
                    audio_validation = _audio_validation(
                        reference_facts, result_facts, requirements, stage=stage, reference_snapshot=reference_snapshot_path,
                        result_snapshot=result_snapshot_path, ffprobe=ffprobe, timeout_seconds=timeout
                    )
                    if proposal_data.get("audio_validation") != audio_validation:
                        raise _invalid("Temporal Results Proposal audio evidence changed")
                    technical_sanity = _technical_sanity(
                        root, stage, reference_snapshot_path, reference_facts, result_snapshot_path, result_facts,
                        ffmpeg=ffmpeg, timeout_seconds=timeout
                    )
                    if proposal_data.get("technical_sanity") != technical_sanity:
                        raise _invalid("Temporal Results Proposal technical evidence changed")
                    result_hash = result_inventory[0]["sha256"]
                    final_path = _copy_stage_file(root, stage, result_snapshot_path, "temporal-replacement.mp4", expected_sha256=result_hash)
                    final_facts = _inspect_staged_media(stage, final_path, ffprobe=ffprobe, timeout_seconds=timeout, role="final temporal delivery")
                    if final_facts != result_facts:
                        raise _invalid("final temporal byte copy media facts changed")
                    _reject_result_metadata(stage, final_path, ffprobe=ffprobe, timeout_seconds=timeout)
                    _full_decode(stage, final_path, final_facts, ffmpeg=ffmpeg, timeout_seconds=timeout)
                    final_artifact = _media_artifact(root, stage, target, final_path)
                    rrv_propose._remove_stage_file(stage, reference_snapshot_path)
                    rrv_propose._remove_stage_file(stage, result_snapshot_path)
                    report_data: dict[str, Any] = {
                        "schema_version": SCHEMA_VERSION,
                        "completion": "temporal_replacement_reviewed",
                        "review_required": False,
                        "bitstream_faithful": False,
                        "provider_provenance": "unattested-local-file-drop",
                        "plan_path": plan_snapshot.relative_path,
                        "plan_sha256": plan_snapshot.sha256,
                        "plan_review_path": plan_review_snapshot.relative_path,
                        "plan_review_sha256": plan_review_snapshot.sha256,
                        "proposal_path": proposal_snapshot.relative_path,
                        "proposal_sha256": proposal_snapshot.sha256,
                        "results_review_path": results_review_snapshot.relative_path,
                        "results_review_sha256": results_review_snapshot.sha256,
                        "template_path": plan_data.get("template_path"),
                        "template_sha256": plan_data.get("template_sha256"),
                        "template_id": plan_data.get("template_id"),
                        "manifest_path": plan_data.get("manifest_path"),
                        "manifest_sha256": plan_data.get("manifest_sha256"),
                        "request_path": plan_data.get("request_path"),
                        "request_sha256": plan_data.get("request_sha256"),
                        "input_assets_sha256": _canonical_json_sha256(input_assets),
                        "reference_pack": reference_pack_name,
                        "reference_inventory_sha256": plan_data.get("reference_inventory_sha256"),
                        "result_pack": result_pack_name,
                        "result_inventory_sha256": proposal_data.get("result_inventory_sha256"),
                        "requirements_sha256": proposal_data.get("requirements_sha256"),
                        "final_video": final_artifact,
                        "media": final_facts,
                        "technical_sanity": technical_sanity,
                        "verified": True,
                    }
                    report_errors = validate_temporal_delivery_report_data(report_data)
                    if report_errors:
                        _raise_validation("generated Temporal Delivery Report", report_errors)
                    report_path = rrv_propose._stage_path(root, stage, "temporal-delivery-report.json")
                    rrv_assets._write_json(stage, root, report_path, report_data, "Temporal Delivery Report JSON")
                    report_artifact = rrv_assets._artifact(root, stage, target, report_path)
                    rrv_assets._assert_pack_live(root_identity, reference_identity)
                    rrv_assets._assert_pack_live(root_identity, result_identity)
                    expected_files = {"temporal-replacement.mp4": final_artifact["sha256"], "temporal-delivery-report.json": report_artifact["sha256"]}
                    _publish_exact_temporal_stage(root, stage, target, label="Temporal Delivery", expected_files=expected_files)
                    stage = None
                    return {
                        "schema_version": SCHEMA_VERSION,
                        "completion": "temporal_replacement_reviewed",
                        "review_required": False,
                        "bitstream_faithful": False,
                        "provider_provenance": "unattested-local-file-drop",
                        "artifacts": {"temporal_replacement": final_artifact, "delivery_report": report_artifact},
                    }
    except BaseException as exc:
        rrv_propose._cleanup_directory(root, stage)
        raise _safe_exception(exc) from None


def verify_temporal_delivery(
    report: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str],
    ffmpeg: str | os.PathLike[str] = "ffmpeg",
    ffprobe: str | os.PathLike[str] = "ffprobe",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Mapping[str, Any]:
    """Revalidate all packet bindings, final bytes, strict media, and decode."""

    root = rrv_assets._safe_project_root(project_root)
    timeout = rrv_assets._parse_timeout(timeout_seconds)
    stage: Any = None
    try:
        with rrv_assets._root_guard(root) as root_identity:
            report_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, report, label="Temporal Delivery Report")
            report_data = report_snapshot.data
            report_errors = validate_temporal_delivery_report_data(report_data)
            if report_errors or not isinstance(report_data, Mapping):
                _raise_validation("Temporal Delivery Report", report_errors)
            plan_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, report_data.get("plan_path"), label="Temporal Plan")
            plan_review_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, report_data.get("plan_review_path"), label="Temporal Plan Review")
            proposal_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, report_data.get("proposal_path"), label="Temporal Results Proposal")
            results_review_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, report_data.get("results_review_path"), label="Temporal Results Review")
            plan_data, plan_review_data = plan_snapshot.data, plan_review_snapshot.data
            proposal_data, results_review_data = proposal_snapshot.data, results_review_snapshot.data
            for validator, data, label in (
                (validate_temporal_plan_data, plan_data, "Temporal Plan"),
                (validate_temporal_plan_review_data, plan_review_data, "Temporal Plan Review"),
                (validate_temporal_results_proposal_data, proposal_data, "Temporal Results Proposal"),
                (validate_temporal_results_review_data, results_review_data, "Temporal Results Review"),
            ):
                errors = validator(data)
                if errors or not isinstance(data, Mapping):
                    _raise_validation(label, errors)
            _validate_delivery_packet_bindings(
                report_data,
                report_snapshot=report_snapshot,
                plan_snapshot=plan_snapshot,
                plan_review_snapshot=plan_review_snapshot,
                proposal_snapshot=proposal_snapshot,
                results_review_snapshot=results_review_snapshot,
            )
            final_bound_artifact = report_data.get("final_video")
            if not isinstance(final_bound_artifact, Mapping):
                raise _invalid("Temporal Delivery Report final video is invalid")
            _assert_exact_delivery_set(root, root_identity, report_snapshot, final_bound_artifact)
            _approved_plan_review(plan_data, plan_snapshot.sha256, plan_review_data)
            _validate_proposal_packet_bindings(proposal_data, plan_snapshot=plan_snapshot, plan_review_snapshot=plan_review_snapshot, plan=plan_data)
            _approved_results_review(proposal_data, proposal_snapshot.sha256, results_review_data)
            template_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, plan_data.get("template_path"), label="template")
            manifest_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, plan_data.get("manifest_path"), label="asset manifest")
            request_snapshot = rrv_assets._read_project_json_snapshot(root, root_identity, plan_data.get("request_path"), label="temporal request")
            _, _, _, input_assets, source_spec, output_spec, requirements = _validate_plan_static_bindings(
                root,
                root_identity,
                plan_data,
                template_snapshot=template_snapshot,
                manifest_snapshot=manifest_snapshot,
                request_snapshot=request_snapshot,
                enforce_current_authorization=False,
            )
            if (
                report_data.get("template_path") != plan_data.get("template_path")
                or report_data.get("template_sha256") != plan_data.get("template_sha256")
                or report_data.get("template_id") != plan_data.get("template_id")
                or report_data.get("manifest_path") != plan_data.get("manifest_path")
                or report_data.get("manifest_sha256") != plan_data.get("manifest_sha256")
                or report_data.get("request_path") != plan_data.get("request_path")
                or report_data.get("request_sha256") != plan_data.get("request_sha256")
                or report_data.get("input_assets_sha256") != _canonical_json_sha256(input_assets)
                or report_data.get("reference_pack") != plan_data.get("reference_pack")
                or report_data.get("reference_inventory_sha256") != plan_data.get("reference_inventory_sha256")
                or report_data.get("result_pack") != proposal_data.get("result_pack")
                or report_data.get("result_inventory_sha256") != proposal_data.get("result_inventory_sha256")
                or report_data.get("requirements_sha256") != proposal_data.get("requirements_sha256")
            ):
                raise _invalid("Temporal Delivery Report static bindings changed")
            plan_evidence = plan_data.get("evidence")
            _validate_evidence_artifact(
                root, root_identity, plan_snapshot.relative_path,
                plan_evidence.get("input_contact_sheet") if isinstance(plan_evidence, Mapping) else None,
                expected_filename="temporal-input-contact-sheet.png", label="Temporal Plan", maximum_bytes=rrv_assets.MAX_CONTACT_SHEET_BYTES
            )
            _validate_proposal_evidence(root, root_identity, proposal_snapshot, proposal_data)
            reference_pack_name = rrv_assets._direct_child_name(plan_data.get("reference_pack"), "Temporal Plan reference_pack")
            result_pack_name = rrv_assets._direct_child_name(proposal_data.get("result_pack"), "Temporal Results Proposal result_pack")
            if _same_direct_child(reference_pack_name, result_pack_name):
                raise _invalid("Temporal result pack must be distinct from reference pack")
            final_artifact = report_data.get("final_video")
            if not isinstance(final_artifact, Mapping):
                raise _invalid("Temporal Delivery Report final video is invalid")
            stage = rrv_propose._new_staging_directory(root, "temporal-verify")
            final_snapshot_path = _snapshot_project_file_to_stage(
                root, root_identity, final_artifact.get("path"), stage=stage, name=".delivery-result.mp4",
                expected_sha256=final_artifact.get("sha256"), label="temporal delivery video"
            )
            with rrv_assets._asset_pack_guard(root, root_identity, reference_pack_name) as (reference_directory, reference_identity):
                reference_asset, reference_snapshot_path = _scan_one_video_pack(
                    root, root_identity, reference_directory, reference_identity, reference_pack_name,
                    stage=stage, snapshot_name=".reference-action.mp4", required_filename=None, asset_id="action-reference.0001"
                )
                reference_facts = _inspect_staged_media(stage, reference_snapshot_path, ffprobe=ffprobe, timeout_seconds=timeout, role="action reference")
                _require_media_matches(reference_facts, width=source_spec["width"], height=source_spec["height"], fps=source_spec["fps"], frame_count=source_spec["frame_count"], role="action reference")
                _require_reference_audio_for_mode(reference_facts, requirements)
                _full_decode(stage, reference_snapshot_path, reference_facts, ffmpeg=ffmpeg, timeout_seconds=timeout)
                reference_inventory = _opaque_inventory(reference_asset, reference_facts)
                _validate_plan_reference_binding(plan_data, reference_inventory)
                with rrv_assets._asset_pack_guard(root, root_identity, result_pack_name) as (result_directory, result_identity):
                    if _same_directory_identity(reference_identity, result_identity):
                        raise _invalid("Temporal result pack must be a distinct local directory")
                    result_asset, result_snapshot_path = _scan_one_video_pack(
                        root, root_identity, result_directory, result_identity, result_pack_name,
                        stage=stage, snapshot_name=".temporal-result.mp4", required_filename="temporal-replacement.mp4", asset_id="temporal-result.0001"
                    )
                    result_facts = _inspect_staged_media(stage, result_snapshot_path, ffprobe=ffprobe, timeout_seconds=timeout, role="temporal result")
                    _require_media_matches(result_facts, width=output_spec["width"], height=output_spec["height"], fps=source_spec["fps"], frame_count=source_spec["frame_count"], expected_audio_streams=_expected_audio_streams(requirements), role="temporal result")
                    _reject_result_metadata(stage, result_snapshot_path, ffprobe=ffprobe, timeout_seconds=timeout)
                    _full_decode(stage, result_snapshot_path, result_facts, ffmpeg=ffmpeg, timeout_seconds=timeout)
                    result_inventory = _opaque_inventory(result_asset, result_facts)
                    _validate_proposal_result_binding(proposal_data, result_inventory)
                    audio_validation = _audio_validation(
                        reference_facts, result_facts, requirements, stage=stage, reference_snapshot=reference_snapshot_path,
                        result_snapshot=result_snapshot_path, ffprobe=ffprobe, timeout_seconds=timeout
                    )
                    if proposal_data.get("audio_validation") != audio_validation:
                        raise _invalid("Temporal Results Proposal audio evidence changed")
                    technical_sanity = _technical_sanity(
                        root, stage, reference_snapshot_path, reference_facts, result_snapshot_path, result_facts,
                        ffmpeg=ffmpeg, timeout_seconds=timeout
                    )
                    if proposal_data.get("technical_sanity") != technical_sanity or report_data.get("technical_sanity") != technical_sanity:
                        raise _invalid("Temporal technical evidence changed")
                    final_facts = _inspect_staged_media(stage, final_snapshot_path, ffprobe=ffprobe, timeout_seconds=timeout, role="final temporal delivery")
                    _require_media_matches(final_facts, width=output_spec["width"], height=output_spec["height"], fps=source_spec["fps"], frame_count=source_spec["frame_count"], expected_audio_streams=_expected_audio_streams(requirements), role="final temporal delivery")
                    _reject_result_metadata(stage, final_snapshot_path, ffprobe=ffprobe, timeout_seconds=timeout)
                    _full_decode(stage, final_snapshot_path, final_facts, ffmpeg=ffmpeg, timeout_seconds=timeout)
                    if (
                        final_facts != result_facts
                        or final_facts != report_data.get("media")
                        or final_artifact.get("sha256") != result_inventory[0].get("sha256")
                    ):
                        raise _invalid("final temporal delivery bytes do not match the reviewed result")
                    rrv_assets._assert_pack_live(root_identity, reference_identity)
                    rrv_assets._assert_pack_live(root_identity, result_identity)
                    _assert_exact_delivery_set(root, root_identity, report_snapshot, final_artifact)
                    verified = {
                        "schema_version": SCHEMA_VERSION,
                        "verified": True,
                        "completion": "temporal_replacement_reviewed",
                        "final_video": {"path": final_artifact.get("path"), "sha256": final_artifact.get("sha256")},
                    }
                    rrv_propose._cleanup_directory(root, stage)
                    stage = None
                    return verified
    except BaseException as exc:
        rrv_propose._cleanup_directory(root, stage)
        raise _safe_exception(exc) from None


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "SCANNER_POLICY_VERSION",
    "SCHEMA_VERSION",
    "freeze_temporal_delivery",
    "prepare_temporal_replacement",
    "propose_temporal_results",
    "validate_temporal_delivery_report_data",
    "validate_temporal_plan_data",
    "validate_temporal_plan_review_data",
    "validate_temporal_request_data",
    "validate_temporal_results_proposal_data",
    "validate_temporal_results_review_data",
    "verify_temporal_delivery",
]
