import copy
import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "skills" / "rebuild-reference-video"
SCRIPT_PATH = SKILL_ROOT / "scripts" / "video_remix.py"
TEMPLATE_PATH = SKILL_ROOT / "assets" / "project-template" / "template.ir.example.json"
ASSETS_PATH = SKILL_ROOT / "assets" / "project-template" / "assets.example.json"

SPEC = importlib.util.spec_from_file_location("video_remix", SCRIPT_PATH)
assert SPEC and SPEC.loader
video_remix = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(video_remix)


class TemplateIRValidationTests(unittest.TestCase):
    def setUp(self):
        self.template = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
        self.assets = json.loads(ASSETS_PATH.read_text(encoding="utf-8"))

    def validate(self, template):
        return video_remix.validate_template_data(template)

    def test_schema_example_passes_and_models_renderer_contract(self):
        self.assertEqual(self.validate(self.template), [])
        self.assertEqual(self.template["schema_version"], "0.2.0")
        self.assertEqual(self.template["coordinate_space"], "canvas-pixels")
        self.assertEqual(self.template["canvas"]["source_rect"], {"x": 0, "y": 128, "width": 576, "height": 1024})
        outfit_layers = [layer for layer in self.template["layers"] if layer["id"].startswith("outfit-render.")]
        self.assertEqual(len(outfit_layers), 12)
        self.assertTrue(all(layer["track_id"] == "model" for layer in outfit_layers))
        self.assertTrue(all(layer["source"]["representation"] == "render-ready" for layer in outfit_layers))
        self.assertFalse(any(layer["source"]["slot_id"] == "model.identity" for layer in self.template["layers"]))
        self.assertTrue(all(output["reframe"]["background"] == "#FFFFFF" for output in self.template["outputs"]))
        remove_rects = {
            layer["id"]: layer["regions"][0]["geometry"]["rect"]
            for layer in self.template["remove_layers"]
        }
        self.assertEqual(remove_rects["platform-top"], {"x": 0, "y": 0, "width": 576, "height": 140})
        self.assertEqual(remove_rects["platform-right"], {"x": 490, "y": 441, "width": 86, "height": 537})
        self.assertEqual(remove_rects["platform-bottom"], {"x": 0, "y": 992, "width": 576, "height": 288})
        self.assertTrue(next(slot for slot in self.template["slots"] if slot["id"] == "audio")["required"])
        self.assertTrue(any(asset["slot_id"] == "audio" for asset in self.assets["assets"]))

    def test_unknown_property_fails_schema_validation(self):
        broken = copy.deepcopy(self.template)
        broken["layers"][0]["unexpected"] = True
        errors = self.validate(broken)
        self.assertTrue(any("Additional properties are not allowed" in error for error in errors), errors)

    def test_slot_accepted_media_is_limited_to_supported_media_types(self):
        broken = copy.deepcopy(self.template)
        broken["slots"][0]["accepted_media"] = ["text/plain"]
        errors = self.validate(broken)
        self.assertTrue(any("is not one of" in error for error in errors), errors)

    def test_matroska_audio_is_a_supported_manifest_media_type(self):
        self.assertIn("audio/x-matroska", video_remix.MEDIA_TYPES)

    def test_range_overlap_and_out_of_bounds_fail(self):
        broken = copy.deepcopy(self.template)
        broken["layers"][0]["active_ranges"] = [
            {"start_frame": 0, "end_frame": 100},
            {"start_frame": 50, "end_frame": 400},
        ]
        errors = self.validate(broken)
        self.assertTrue(any("overlaps" in error for error in errors), errors)
        self.assertTrue(any("within [0, 347)" in error for error in errors), errors)

    def test_canvas_source_rect_requires_matching_aspect_ratio(self):
        broken = copy.deepcopy(self.template)
        broken["canvas"]["source_rect"]["width"] = 575
        errors = self.validate(broken)
        self.assertTrue(any("aspect ratio must match canvas" in error for error in errors), errors)

    def test_duplicate_keyframe_fails(self):
        broken = copy.deepcopy(self.template)
        keyframe = copy.deepcopy(broken["layers"][0]["transform"]["keyframes"][0])
        broken["layers"][0]["transform"]["keyframes"].append(keyframe)
        errors = self.validate(broken)
        self.assertTrue(any("keyframes[1].frame must be strictly increasing" in error for error in errors), errors)

    def test_unknown_layer_track_and_slot_fail(self):
        broken = copy.deepcopy(self.template)
        broken["layers"][0]["track_id"] = "missing-track"
        broken["layers"][0]["source"]["slot_id"] = "missing-slot"
        errors = self.validate(broken)
        self.assertTrue(any("unknown track missing-track" in error for error in errors), errors)
        self.assertTrue(any("unknown slot missing-slot" in error for error in errors), errors)

    def test_same_track_same_frame_slot_switch_fails(self):
        broken = copy.deepcopy(self.template)
        duplicate = copy.deepcopy(broken["events"][0])
        duplicate["id"] = "outfit-switch.duplicate"
        duplicate["slot_id"] = "outfit.02"
        broken["events"].append(duplicate)
        errors = self.validate(broken)
        self.assertTrue(any("duplicates a slot-switch" in error for error in errors), errors)

    def test_events_must_be_ordered_and_match_subject_layers(self):
        broken = copy.deepcopy(self.template)
        broken["events"][0], broken["events"][1] = broken["events"][1], broken["events"][0]
        errors = self.validate(broken)
        self.assertTrue(any("strictly ordered by (frame, id)" in error for error in errors), errors)

        broken = copy.deepcopy(self.template)
        broken["events"][0]["slot_id"] = "outfit.02"
        errors = self.validate(broken)
        self.assertTrue(any("must match exactly one same-track layer" in error for error in errors), errors)
        self.assertTrue(any("is missing slot-switch events" in error for error in errors), errors)

    def test_subject_track_event_consistency_is_name_independent_and_per_range(self):
        renamed = copy.deepcopy(self.template)
        next(track for track in renamed["tracks"] if track["id"] == "model")["id"] = "subject-main"
        for layer in renamed["layers"]:
            if layer["track_id"] == "model":
                layer["track_id"] = "subject-main"
        for event in renamed["events"]:
            if event["track_id"] == "model":
                event["track_id"] = "subject-main"
        self.assertEqual(self.validate(renamed), [])

        broken = copy.deepcopy(self.template)
        subject_track = copy.deepcopy(next(track for track in broken["tracks"] if track["id"] == "model"))
        subject_track["id"] = "subject-alt"
        broken["tracks"].append(subject_track)
        layer = copy.deepcopy(next(layer for layer in broken["layers"] if layer["id"] == "outfit-render.01"))
        layer["id"] = "outfit-render.alt"
        layer["track_id"] = "subject-alt"
        layer["active_ranges"] = [
            {"start_frame": 0, "end_frame": 10},
            {"start_frame": 20, "end_frame": 25},
        ]
        broken["layers"].append(layer)
        event = copy.deepcopy(broken["events"][0])
        event.update({"id": "subject-alt-switch.01", "track_id": "subject-alt", "frame": 0})
        broken["events"].append(event)
        broken["events"].sort(key=lambda item: (item["frame"], item["id"]))
        errors = self.validate(broken)
        self.assertTrue(any("active-range starts [20]" in error for error in errors), errors)

    def test_crop_requires_legal_static_rect_geometry(self):
        broken = copy.deepcopy(self.template)
        crop_layer = next(layer for layer in broken["remove_layers"] if layer["policy"] == "crop-source-before-analysis")
        crop_layer["regions"][0]["geometry"] = {
            "type": "polygon",
            "space": "source",
            "points": [{"x": 0, "y": 128}, {"x": 576, "y": 128}, {"x": 0, "y": 1152}],
        }
        errors = self.validate(broken)
        self.assertTrue(any("crop policy requires a static rect geometry" in error for error in errors), errors)

    def test_remove_regions_can_overlap_in_time_but_crop_is_unique_and_matches_canvas(self):
        broken = copy.deepcopy(self.template)
        platform_top = next(layer for layer in broken["remove_layers"] if layer["id"] == "platform-top")
        platform_top["regions"].append(
            {
                "active_range": {"start_frame": 0, "end_frame": 347},
                "operation": "remove",
                "geometry": {"type": "rect", "space": "source", "rect": {"x": 0, "y": 140, "width": 576, "height": 1}},
            }
        )
        self.assertEqual(self.validate(broken), [])

        no_crop = copy.deepcopy(self.template)
        no_crop["remove_layers"] = [layer for layer in no_crop["remove_layers"] if layer["policy"] != "crop-source-before-analysis"]
        self.assertEqual(self.validate(no_crop), [])

        broken = copy.deepcopy(self.template)
        crop_layer = next(layer for layer in broken["remove_layers"] if layer["policy"] == "crop-source-before-analysis")
        crop_layer["regions"][0]["geometry"]["rect"]["width"] = 575
        errors = self.validate(broken)
        self.assertTrue(any("must exactly match $.canvas.source_rect" in error for error in errors), errors)

        broken = copy.deepcopy(self.template)
        extra_crop = copy.deepcopy(next(layer for layer in broken["remove_layers"] if layer["policy"] == "crop-source-before-analysis"))
        extra_crop["id"] = "second-crop"
        broken["remove_layers"].append(extra_crop)
        errors = self.validate(broken)
        self.assertTrue(any("at most one crop-source-before-analysis" in error for error in errors), errors)

    def test_audio_non_loop_coverage_must_be_sufficient(self):
        broken = copy.deepcopy(self.template)
        broken["audio"]["source_out_ms"] = 1000
        errors = self.validate(broken)
        self.assertTrue(any("non-looping source coverage" in error for error in errors), errors)

    def test_nan_and_infinity_are_rejected_in_data_and_json_input(self):
        for value in (math.nan, math.inf, -math.inf):
            broken = copy.deepcopy(self.template)
            broken["audio"]["gain_db"] = value
            errors = self.validate(broken)
            self.assertTrue(any("must be finite" in error for error in errors), errors)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "non-finite.json"
            path.write_text('{"value": NaN}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "non-finite JSON number"):
                video_remix.load_json(path)

    def test_example_assets_are_valid_without_path_checks(self):
        self.assertEqual(
            video_remix.validate_assets_data(self.template, self.assets, ASSETS_PATH, check_files=False),
            [],
        )

    def test_asset_manifest_schema_rejects_unknown_properties(self):
        broken = copy.deepcopy(self.assets)
        broken["unexpected"] = True
        errors = video_remix.validate_assets_data(self.template, broken, ASSETS_PATH, check_files=False)
        self.assertTrue(any("Additional properties are not allowed" in error for error in errors), errors)

        broken = copy.deepcopy(self.assets)
        broken["assets"][0]["unexpected"] = True
        errors = video_remix.validate_assets_data(self.template, broken, ASSETS_PATH, check_files=False)
        self.assertTrue(any("Additional properties are not allowed" in error for error in errors), errors)

    def test_asset_media_type_must_be_accepted_by_its_slot(self):
        broken = copy.deepcopy(self.assets)
        broken["assets"][0]["media_type"] = "audio/mpeg"
        errors = video_remix.validate_assets_data(self.template, broken, ASSETS_PATH, check_files=False)
        self.assertIn(
            "$.assets[0].media_type audio/mpeg is not accepted by slot model.identity",
            errors,
        )

    def test_asset_path_cannot_escape_project_root_without_file_checks(self):
        broken = copy.deepcopy(self.assets)
        broken["assets"][0]["path"] = "../escape.png"
        errors = video_remix.validate_assets_data(self.template, broken, ASSETS_PATH, check_files=False)
        self.assertIn("$.assets[0].path escapes the project root: ../escape.png", errors)

    def test_streaming_sha256_file_uses_known_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "small.bin"
            path.write_bytes(b"abc")
            self.assertEqual(
                video_remix.sha256_file(path),
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            )

    def test_provider_assets_require_cloud_upload_and_remain_forbidden_locally(self):
        broken = copy.deepcopy(self.assets)
        broken["privacy_profile"] = "cloud-assisted"
        asset = broken["assets"][0]
        asset.pop("path")
        asset["provider_asset_id"] = "provider-asset-1"
        asset["cloud_upload_allowed"] = False
        errors = video_remix.validate_assets_data(self.template, broken, ASSETS_PATH, check_files=False)
        self.assertTrue(any("True was expected" in error for error in errors), errors)

        broken = copy.deepcopy(self.assets)
        asset = broken["assets"][0]
        asset.pop("path")
        asset["provider_asset_id"] = "provider-asset-1"
        asset["cloud_upload_allowed"] = True
        errors = video_remix.validate_assets_data(self.template, broken, ASSETS_PATH, check_files=False)
        self.assertTrue(any("provider_asset_id" in error for error in errors), errors)

    def test_missing_required_asset_fails(self):
        broken = copy.deepcopy(self.assets)
        broken["assets"] = [item for item in broken["assets"] if item["slot_id"] != "outfit.12"]
        errors = video_remix.validate_assets_data(self.template, broken, ASSETS_PATH, check_files=False)
        self.assertIn("required slot is not mapped: outfit.12", errors)

    def test_doctor_does_not_claim_unimplemented_stages(self):
        payload = video_remix.doctor_payload()
        capabilities = payload["capabilities"]
        self.assertTrue(capabilities["template_validation"])
        self.assertTrue(capabilities["asset_manifest_structure_validation"])
        self.assertFalse(capabilities["reference_analysis"])
        self.assertFalse(capabilities["asset_generation"])
        self.assertIsInstance(payload["runtime"]["jsonschema_version"], str)
        self.assertIsInstance(payload["runtime"]["pillow_version"], str)
        # Deterministic S1 rendering is implemented; availability truthfully
        # depends on FFmpeg, Pillow, and both JSON Schema validators.
        self.assertIsInstance(capabilities["timeline_render"], bool)

    def test_doctor_disables_timeline_render_when_jsonschema_is_unavailable(self):
        def tool(path: str) -> SimpleNamespace:
            return SimpleNamespace(path=path, to_dict=lambda: {"path": path})

        tools = SimpleNamespace(
            ffmpeg=tool("fake-ffmpeg"),
            ffprobe=tool("fake-ffprobe"),
            to_dict=lambda: {"ffmpeg": {"path": "fake-ffmpeg"}, "ffprobe": {"path": "fake-ffprobe"}},
        )
        runtime = SimpleNamespace(discover_tools=mock.Mock(return_value=tools))
        try:
            with mock.patch.object(video_remix, "_runtime_module", return_value=runtime), mock.patch.object(
                video_remix, "_pillow_available", return_value=True
            ), mock.patch.object(video_remix, "Draft202012Validator", None):
                video_remix._schema_validators.clear()
                video_remix._schema_validator_errors.clear()
                payload = video_remix.doctor_payload()
                self.assertFalse(payload["runtime"]["jsonschema"])
                self.assertIsNone(payload["runtime"]["jsonschema_version"])
                self.assertTrue(payload["runtime"]["pillow"])
                self.assertFalse(payload["capabilities"]["template_validation"])
                self.assertFalse(payload["capabilities"]["asset_manifest_structure_validation"])
                self.assertFalse(payload["capabilities"]["timeline_render"])
                errors = video_remix.validate_template_data(self.template)
                self.assertTrue(any("requirements-runtime.txt" in error for error in errors), errors)
                error_text = "\n".join(errors)
                self.assertIn("python -m pip install -r requirements-runtime.txt", error_text)
                self.assertNotIn(str(video_remix.SKILL_ROOT), error_text)
        finally:
            video_remix._schema_validators.clear()
            video_remix._schema_validator_errors.clear()


if __name__ == "__main__":
    unittest.main()
