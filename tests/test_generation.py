import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIRECTORY = REPO_ROOT / "skills" / "reference-video-rebuilder" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIRECTORY))

try:
    from PIL import Image, PngImagePlugin
except ImportError:  # pragma: no cover - runtime dependency is required by the Skill.
    Image = None
    PngImagePlugin = None

import rrv_assets  # noqa: E402
import rrv_generation  # noqa: E402
import rrv_propose  # noqa: E402
import rrv_runtime  # noqa: E402
import video_remix  # noqa: E402


def template_document(slots):
    slots = list(slots)
    if not any(slot["id"] == "audio" for slot in slots):
        slots.append({"id": "audio", "type": "audio", "required": False, "accepted_media": ["audio/wav"]})
    return {
        "schema_version": "0.2.0",
        "template_id": "generation-test",
        "coordinate_space": "canvas-pixels",
        "canvas": {
            "width": 8,
            "height": 8,
            "background": "#ffffff",
            "source_rect": {"x": 0, "y": 0, "width": 8, "height": 8},
        },
        "source": {
            "duration_frames": 3,
            "fps": 10,
            "width": 8,
            "height": 8,
            "source_sha256": "0" * 64,
        },
        "support": {"level": "S1", "confidence": 1, "warnings": []},
        "tracks": [{"id": "base", "type": "background", "z_index": 0, "overlap_policy": "forbid"}],
        "slots": slots,
        "layers": [
            {
                "id": "hero-layer",
                "track_id": "base",
                "source": {"slot_id": slots[0]["id"], "representation": "raw"},
                "active_ranges": [{"start_frame": 0, "end_frame": 3}],
                "layout": {
                    "box": {"x": 0, "y": 0, "width": 8, "height": 8},
                    "fit": "contain",
                    "object_position": {"x": 0.5, "y": 0.5},
                },
                "transform": {
                    "anchor": {"x": 0, "y": 0},
                    "keyframes": [
                        {
                            "frame": 0,
                            "translate_x": 0,
                            "translate_y": 0,
                            "scale_x": 1,
                            "scale_y": 1,
                            "rotation_deg": 0,
                            "opacity": 1,
                            "easing": {"type": "hold"},
                        }
                    ],
                },
                "mask": None,
                "blend": {"mode": "normal", "opacity": 1},
                "z_offset": 0,
            }
        ],
        "remove_layers": [],
        "events": [],
        "audio": {
            "slot_id": "audio",
            "timeline_start_frame": 0,
            "timeline_end_frame": 3,
            "source_in_ms": 0,
            "source_out_ms": 300,
            "playback_rate": 1,
            "loop": False,
            "gain_db": 0,
            "fade_in_frames": 0,
            "fade_out_frames": 0,
        },
        "outputs": [
            {
                "id": "vertical-720",
                "width": 720,
                "height": 1280,
                "codec": "h264",
                "pixel_format": "yuv420p",
                "audio_codec": "aac",
                "filename": "deliveries/default.mp4",
                "reframe": {"mode": "contain", "object_position": {"x": 0.5, "y": 0.5}, "background": "#ffffff"},
            }
        ],
    }


@unittest.skipUnless(Image is not None, "Pillow is installed from requirements-runtime.txt")
class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        self.references = self.root / "reference-pack"
        self.references.mkdir()
        self.results = self.root / "result-pack"
        self.results.mkdir()

    def write_template(self, slots=None, path="template.ir.json"):
        slots = slots or [{"id": "look.01", "type": "image", "required": True, "accepted_media": ["image/png"]}]
        document = template_document(slots)
        self.assertEqual(video_remix.validate_template_data(document), [])
        (self.root / path).write_text(json.dumps(document), encoding="utf-8")
        return path, document

    def image(self, directory, name, color=(20, 100, 220), metadata=False):
        target = directory / name
        image = Image.new("RGB", (10, 8), color)
        try:
            if metadata:
                info = PngImagePlugin.PngInfo()
                info.add_text("private-note", "must-not-survive")
                image.save(target, format="PNG", pnginfo=info)
            else:
                image.save(target, format="PNG")
        finally:
            image.close()
        return target

    def write_json(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return relative

    def request(self, targets=("look.01",), *, profile="local-only", managed=False):
        request = {
            "schema_version": "0.6.0",
            "privacy_profile": profile,
            "execution_profile": "controller-managed" if managed else "local-file-drop",
            "adapter_id": "local-file-drop" if not managed else "controller-bridge",
            "adapter_version": "1.0.0",
            "cloud_upload_confirmed": False,
            "tasks": [],
        }
        if profile == "controller-cloud":
            request["cloud_upload_confirmed"] = True
        if managed:
            request["controller_label"] = "Local Controller"
        for index, target in enumerate(targets, start=1):
            request["tasks"].append(
                {
                    "target_slot_id": target,
                    "kind": "reference-guided-still",
                    "references": [{"source_filename": f"reference-{index:02d}.png", "role": "reference"}],
                    "instructions": "Create a clean render-ready still.",
                    "passthrough": False,
                    "omit": False,
                }
            )
        return request

    def prepare(self, request_path="generation-request.json", **kwargs):
        return rrv_generation.prepare_generation(
            "template.ir.json",
            request_path,
            project_root=self.root,
            reference_pack="reference-pack",
            generation_rights_confirmed=True,
            **kwargs,
        )

    def approve_plan(self, prepared):
        review = json.loads((self.root / prepared["artifacts"]["review_template"]["path"]).read_text(encoding="utf-8"))
        review.update(
            {
                "decision": "approved",
                "input_contact_sheet_reviewed": True,
                "request_reviewed": True,
                "execution_profile_confirmed": True,
            }
        )
        for task in review["tasks"]:
            task.update(
                {
                    "decision": "accept",
                    "references_confirmed": True,
                    "instruction_scope_confirmed": True,
                    "rights_confirmed": True,
                }
            )
        return self.write_json("generation-plan/approved-plan-review.json", review)

    def propose_results(self, prepared, plan_review_path, **kwargs):
        return rrv_generation.propose_generation_results(
            prepared["artifacts"]["generation_plan"]["path"],
            plan_review_path,
            project_root=self.root,
            result_pack="result-pack",
            generation_results_rights_confirmed=True,
            **kwargs,
        )

    def approve_results(self, proposal):
        review = json.loads((self.root / proposal["artifacts"]["review_template"]["path"]).read_text(encoding="utf-8"))
        review["decision"] = "approved"
        review["comparison_contact_sheet_reviewed"] = True
        for task in review["tasks"]:
            task.update(
                {
                    "decision": "accept",
                    "identity_confirmed": True,
                    "garment_confirmed": True,
                    "product_confirmed": True,
                    "background_confirmed": True,
                    "pose_confirmed": True,
                    "render_ready_confirmed": True,
                    "rights_confirmed": True,
                }
            )
        return self.write_json("generation-results-proposal/approved-results-review.json", review)

    def assemble(self, prepared, plan_review, proposal, results_review, **kwargs):
        return rrv_generation.assemble_generation_pack(
            prepared["artifacts"]["generation_plan"]["path"],
            plan_review,
            proposal["artifacts"]["proposal"]["path"],
            results_review,
            project_root=self.root,
            **kwargs,
        )

    def test_twelve_task_happy_path_is_pure_media_and_strips_png_metadata(self):
        targets = tuple(f"look.{index:02d}" for index in range(1, 13))
        self.write_template([{"id": target, "type": "image", "required": True, "accepted_media": ["image/png"]} for target in targets])
        for index, target in enumerate(targets, start=1):
            self.image(self.references, f"reference-{index:02d}.png", (index * 11, 40, 190))
            self.image(self.results, f"{target}.png", (20, index * 9, 220), metadata=index == 1)
        self.write_json("generation-request.json", self.request(targets))
        prepared = self.prepare()
        self.assertEqual(prepared["counts"]["generation_tasks"], 12)
        plan = json.loads((self.root / prepared["artifacts"]["generation_plan"]["path"]).read_text(encoding="utf-8"))
        self.assertEqual(rrv_generation.validate_generation_plan_data(plan), [])
        self.assertNotIn("instructions", json.dumps(plan))
        plan_review = self.approve_plan(prepared)
        proposal = self.propose_results(prepared, plan_review)
        self.assertEqual(proposal["counts"]["result_inventory_entries"], 12)
        results_review = self.approve_results(proposal)
        assembled = self.assemble(prepared, plan_review, proposal, results_review)
        self.assertEqual(assembled["counts"]["output_assets"], 12)
        output = self.root / assembled["output_dir"]
        self.assertEqual(len(list(output.iterdir())), 12)
        self.assertTrue(all(path.suffix == ".png" for path in output.iterdir()))
        with Image.open(output / "look.01.png") as image:
            self.assertNotIn("private-note", image.info)
            self.assertEqual(dict(image.getexif()), {})
        v05_handoff = rrv_assets.propose_asset_pack(
            "template.ir.json",
            project_root=self.root,
            asset_pack=assembled["output_dir"],
            asset_pack_rights_confirmed=True,
            output_dir="v05-handoff-proposal",
        )
        self.assertEqual(v05_handoff["counts"]["suggested_slots"], 12)

    def test_v05_gold_template_semantic_slots_prepare_from_accepted_media(self):
        """The shipped v0.5 template labels slots by purpose, not media kind."""

        gold_template = json.loads(
            (
                REPO_ROOT
                / "skills"
                / "reference-video-rebuilder"
                / "assets"
                / "project-template"
                / "template.ir.example.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(video_remix.validate_template_data(gold_template), [])
        self.write_json("template.ir.json", gold_template)

        tasks = []
        self.image(self.references, "identity-source.png", (30, 70, 190))
        tasks.append(
            {
                "target_slot_id": "model.identity",
                "kind": "reference-guided-still",
                "references": [{"source_filename": "identity-source.png", "role": "reference"}],
                "instructions": "Create the model identity still.",
                "passthrough": False,
                "omit": False,
            }
        )
        for index in range(1, 13):
            garment_name = f"garment-{index:02d}.png"
            self.image(self.references, garment_name, (index * 10, 80, 160))
            tasks.append(
                {
                    "target_slot_id": f"outfit.{index:02d}",
                    "kind": "identity-try-on",
                    "references": [
                        {"source_filename": "identity-source.png", "role": "identity"},
                        {"source_filename": garment_name, "role": "garment"},
                    ],
                    "instructions": "Create the outfit render.",
                    "passthrough": False,
                    "omit": False,
                }
            )
        for index in range(1, 13):
            product_name = f"product-{index:02d}.png"
            self.image(self.references, product_name, (170, index * 9, 60))
            tasks.append(
                {
                    "target_slot_id": f"product.{index:02d}",
                    "kind": "product-still",
                    "references": [{"source_filename": product_name, "role": "product"}],
                    "instructions": "Create the product still.",
                    "passthrough": False,
                    "omit": False,
                }
            )
        self.image(self.references, "background-source.png", (20, 130, 80))
        tasks.append(
            {
                "target_slot_id": "background",
                "kind": "background-still",
                "references": [{"source_filename": "background-source.png", "role": "background"}],
                "instructions": "Create the background still.",
                "passthrough": False,
                "omit": False,
            }
        )
        (self.references / "audio-source.wav").write_bytes(b"not-a-real-wav")
        tasks.append(
            {
                "target_slot_id": "audio",
                "kind": "reference-guided-still",
                "references": [{"source_filename": "audio-source.wav", "role": "audio"}],
                "instructions": "Keep the supplied audio.",
                "passthrough": True,
                "omit": False,
            }
        )
        request = self.request(())
        request["tasks"] = tasks
        self.write_json("generation-request.json", request)
        probe = {"streams": [{"codec_type": "audio"}], "format": {"format_name": "wav", "duration": "1"}}
        with mock.patch.object(rrv_assets, "_run_ffprobe", return_value=probe):
            prepared = self.prepare()
        self.assertEqual(prepared["counts"], {"reference_inventory_entries": 27, "tasks": 27, "generation_tasks": 26, "passthrough_tasks": 1, "omitted_tasks": 0})
        plan = json.loads((self.root / prepared["artifacts"]["generation_plan"]["path"]).read_text(encoding="utf-8"))
        self.assertEqual({task["target_slot_id"] for task in plan["tasks"]}, {task["target_slot_id"] for task in tasks})

    def test_prepare_rights_gate_is_zero_touch(self):
        with mock.patch.object(rrv_assets, "_safe_project_root", side_effect=AssertionError("touched")):
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_generation.prepare_generation(
                    "template.ir.json",
                    "generation-request.json",
                    project_root=self.root,
                    reference_pack="reference-pack",
                    generation_rights_confirmed=1,
                )
        self.assertEqual(list(self.root.iterdir()), [self.references, self.results])

    def test_nonportable_template_slot_is_rejected_before_reference_scan(self):
        self.image(self.references, "reference-01.png")
        for slot_id in ("con", "look."):
            with self.subTest(slot_id=slot_id):
                self.write_json(
                    "template.ir.json",
                    template_document([{"id": slot_id, "type": "image", "required": True, "accepted_media": ["image/png"]}]),
                )
                self.write_json("generation-request.json", self.request((slot_id,)))
                with mock.patch.object(rrv_assets, "_scan_asset_pack", side_effect=AssertionError("must not scan")):
                    with self.assertRaises(rrv_runtime.RRVError):
                        self.prepare()
                self.assertFalse((self.root / "generation-plan").exists())

    def test_standalone_validators_reject_nonportable_target_slot_ids(self):
        self.write_template()
        self.image(self.references, "reference-01.png")
        self.image(self.results, "look.01.png")
        self.write_json("generation-request.json", self.request())
        prepared = self.prepare()
        plan = json.loads((self.root / prepared["artifacts"]["generation_plan"]["path"]).read_text(encoding="utf-8"))
        plan_review = json.loads((self.root / prepared["artifacts"]["review_template"]["path"]).read_text(encoding="utf-8"))
        approved_plan_review = self.approve_plan(prepared)
        proposal = self.propose_results(prepared, approved_plan_review)
        results_proposal = json.loads((self.root / proposal["artifacts"]["proposal"]["path"]).read_text(encoding="utf-8"))
        results_review = json.loads((self.root / proposal["artifacts"]["review_template"]["path"]).read_text(encoding="utf-8"))
        for unsafe_slot in ("con", "look."):
            with self.subTest(slot_id=unsafe_slot):
                request = self.request()
                request["tasks"][0]["target_slot_id"] = unsafe_slot
                self.assertTrue(rrv_generation.validate_generation_request_data(request))
                mutated_plan = copy.deepcopy(plan)
                mutated_plan["tasks"][0]["target_slot_id"] = unsafe_slot
                self.assertTrue(rrv_generation.validate_generation_plan_data(mutated_plan))
                mutated_plan_review = copy.deepcopy(plan_review)
                mutated_plan_review["tasks"][0]["target_slot_id"] = unsafe_slot
                self.assertTrue(rrv_generation.validate_generation_plan_review_data(mutated_plan_review))
                mutated_proposal = copy.deepcopy(results_proposal)
                mutated_proposal["tasks"][0]["target_slot_id"] = unsafe_slot
                self.assertTrue(rrv_generation.validate_generation_results_proposal_data(mutated_proposal))
                mutated_results_review = copy.deepcopy(results_review)
                mutated_results_review["tasks"][0]["target_slot_id"] = unsafe_slot
                self.assertTrue(rrv_generation.validate_generation_results_review_data(mutated_results_review))

    def test_paletted_png_transparency_and_text_metadata_are_safely_reencoded(self):
        self.write_template()
        self.image(self.references, "reference-01.png")
        palette = Image.new("P", (3, 1))
        palette.putpalette([255, 0, 0, 0, 0, 255] + [0] * (768 - 6))
        palette.putdata([0, 1, 0])
        info = PngImagePlugin.PngInfo()
        info.add_text("private-note", "must-not-survive")
        info.add_itxt("XML:com.adobe.xmp", "must-not-survive")
        palette.info["transparency"] = 0
        try:
            palette.save(self.results / "look.01.png", format="PNG", pnginfo=info)
        finally:
            palette.close()
        self.write_json("generation-request.json", self.request())
        prepared = self.prepare()
        plan_review = self.approve_plan(prepared)
        proposal = self.propose_results(prepared, plan_review)
        results_review = self.approve_results(proposal)
        assembled = self.assemble(prepared, plan_review, proposal, results_review)
        with Image.open(self.root / assembled["output_dir"] / "look.01.png") as output:
            self.assertEqual(output.mode, "RGBA")
            self.assertEqual(output.getchannel("A").getdata()[0], 0)
            self.assertEqual(output.getchannel("A").getdata()[1], 255)
            self.assertNotIn("private-note", output.info)
            self.assertNotIn("XML:com.adobe.xmp", output.info)
            self.assertNotIn("exif", output.info)

    def test_result_rights_gate_is_zero_touch(self):
        with mock.patch.object(rrv_assets, "_safe_project_root", side_effect=AssertionError("touched")):
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_generation.propose_generation_results(
                    "plan.json",
                    "review.json",
                    project_root=self.root,
                    result_pack="result-pack",
                    generation_results_rights_confirmed=False,
                )

    def test_strict_request_and_adapter_contract_rejects_duplicates_urls_and_cloud_without_consent(self):
        duplicate = '{"schema_version":"0.6.0","privacy_profile":"local-only","execution_profile":"local-file-drop","adapter_id":"local-drop","adapter_version":"1","tasks":[],"tasks":[]}'
        (self.root / "generation-request.json").write_text(duplicate, encoding="utf-8")
        with self.assertRaises(rrv_runtime.RRVError):
            self.prepare()
        self.assertFalse((self.root / "generation-plan").exists())
        bad = self.request()
        bad["adapter_version"] = "https://provider.invalid"
        self.assertTrue(rrv_generation.validate_generation_request_data(bad))
        cloud = self.request(profile="controller-cloud")
        del cloud["cloud_upload_confirmed"]
        self.assertTrue(rrv_generation.validate_generation_request_data(cloud))
        trailing_alias = self.request()
        trailing_alias["tasks"][0]["references"][0]["source_filename"] = "reference-01.png "
        self.assertTrue(rrv_generation.validate_generation_request_data(trailing_alias))

    def test_controller_cloud_consent_is_bound_in_the_approved_plan_review(self):
        self.write_template()
        self.image(self.references, "reference-01.png")
        self.image(self.results, "look.01.png")
        self.write_json("generation-request.json", self.request(profile="controller-cloud", managed=True))
        prepared = self.prepare()
        plan = json.loads((self.root / prepared["artifacts"]["generation_plan"]["path"]).read_text(encoding="utf-8"))
        self.assertTrue(plan["cloud_upload_confirmed"])
        approved = self.approve_plan(prepared)
        denied = json.loads((self.root / approved).read_text(encoding="utf-8"))
        denied["cloud_upload_confirmed"] = False
        denied_path = self.write_json("generation-plan/cloud-consent-false.json", denied)
        with mock.patch.object(rrv_assets, "_scan_asset_pack", side_effect=AssertionError("must not scan")):
            with self.assertRaises(rrv_runtime.RRVError):
                self.propose_results(prepared, denied_path)
        self.assertFalse((self.root / "generation-results-proposal").exists())
        proposal = self.propose_results(prepared, approved)
        self.assertTrue((self.root / proposal["artifacts"]["proposal"]["path"]).is_file())

    def test_controller_cloud_requires_controller_managed_at_every_boundary(self):
        self.write_template()
        self.image(self.references, "reference-01.png")
        self.image(self.results, "look.01.png")
        invalid_cloud_request = self.request(profile="controller-cloud")
        self.write_json("invalid-cloud-request.json", invalid_cloud_request)
        self.assertTrue(rrv_generation.validate_generation_request_data(invalid_cloud_request))
        with mock.patch.object(rrv_assets, "_scan_asset_pack", side_effect=AssertionError("must not scan")):
            with self.assertRaises(rrv_runtime.RRVError):
                self.prepare("invalid-cloud-request.json")
        self.assertFalse((self.root / "generation-plan").exists())

        valid_cloud_request = self.request(profile="controller-cloud", managed=True)
        self.write_json("generation-request.json", valid_cloud_request)
        prepared = self.prepare()
        plan = json.loads((self.root / prepared["artifacts"]["generation_plan"]["path"]).read_text(encoding="utf-8"))
        invalid_plan = copy.deepcopy(plan)
        invalid_plan["execution_profile"] = "local-file-drop"
        invalid_plan.pop("controller_label", None)
        self.assertTrue(rrv_generation.validate_generation_plan_data(invalid_plan))
        plan_review = self.approve_plan(prepared)

        self.write_json("generation-request.json", invalid_cloud_request)
        with mock.patch.object(rrv_assets, "_scan_asset_pack", side_effect=AssertionError("must not scan")):
            with self.assertRaises(rrv_runtime.RRVError):
                self.propose_results(prepared, plan_review)
        self.assertFalse((self.root / "generation-results-proposal").exists())

        self.write_json("generation-request.json", valid_cloud_request)
        proposal = self.propose_results(prepared, plan_review)
        results_review = self.approve_results(proposal)
        self.write_json("generation-request.json", invalid_cloud_request)
        with mock.patch.object(rrv_assets, "_scan_asset_pack", side_effect=AssertionError("must not scan")):
            with self.assertRaises(rrv_runtime.RRVError):
                self.assemble(prepared, plan_review, proposal, results_review)
        self.assertFalse((self.root / "generation-asset-pack").exists())

    def test_template_and_request_drift_are_rejected_before_any_pack_scan(self):
        self.write_template()
        self.image(self.references, "reference-01.png")
        self.image(self.results, "look.01.png")
        self.write_json("generation-request.json", self.request())
        prepared = self.prepare()
        plan_review = self.approve_plan(prepared)

        drifted_request = self.request()
        drifted_request["adapter_version"] = "1.0.1"
        self.write_json("generation-request.json", drifted_request)
        with mock.patch.object(rrv_assets, "_scan_asset_pack", side_effect=AssertionError("must not scan")):
            with self.assertRaises(rrv_runtime.RRVError):
                self.propose_results(prepared, plan_review)
        self.assertFalse((self.root / "generation-results-proposal").exists())

        # Restore the exact request so the result proposal is valid, then
        # drift the current Template before final assembly.
        self.write_json("generation-request.json", self.request())
        proposal = self.propose_results(prepared, plan_review)
        results_review = self.approve_results(proposal)
        template = json.loads((self.root / "template.ir.json").read_text(encoding="utf-8"))
        template["template_id"] = "generation-drift"
        self.write_json("template.ir.json", template)
        with mock.patch.object(rrv_assets, "_scan_asset_pack", side_effect=AssertionError("must not scan")):
            with self.assertRaises(rrv_runtime.RRVError):
                self.assemble(prepared, plan_review, proposal, results_review)
        self.assertFalse((self.root / "generation-asset-pack").exists())

    def test_generated_stills_reject_audio_masquerading_as_visual_roles(self):
        self.write_template()
        cases = {
            "identity": (
                "identity-try-on",
                [
                    {"source_filename": "fake.wav", "role": "identity"},
                    {"source_filename": "garment.png", "role": "garment"},
                ],
            ),
            "garment": (
                "identity-try-on",
                [
                    {"source_filename": "identity.png", "role": "identity"},
                    {"source_filename": "fake.wav", "role": "garment"},
                ],
            ),
            "reference": (
                "reference-guided-still",
                [{"source_filename": "fake.wav", "role": "reference"}],
            ),
        }
        probe = {"streams": [{"codec_type": "audio"}], "format": {"format_name": "wav", "duration": "1"}}
        for role, (kind, references) in cases.items():
            with self.subTest(role=role):
                for source in list(self.references.iterdir()):
                    source.unlink()
                (self.references / "fake.wav").write_bytes(b"not-a-real-wav")
                if role == "identity":
                    self.image(self.references, "garment.png")
                elif role == "garment":
                    self.image(self.references, "identity.png")
                request = self.request()
                request["tasks"][0].update({"kind": kind, "references": references})
                self.write_json("generation-request.json", request)
                with mock.patch.object(rrv_assets, "_run_ffprobe", return_value=probe):
                    with self.assertRaises(rrv_runtime.RRVError):
                        self.prepare()
                self.assertFalse((self.root / "generation-plan").exists())

    def test_plan_and_input_contact_sheet_do_not_echo_private_reference_basenames(self):
        self.write_template()
        private_name = "private-model-source-991.png"
        self.image(self.references, private_name)
        self.image(self.results, "look.01.png")
        request = self.request()
        request["tasks"][0]["references"] = [{"source_filename": private_name, "role": "reference"}]
        self.write_json("generation-request.json", request)
        prepared = self.prepare()
        plan_path = self.root / prepared["artifacts"]["generation_plan"]["path"]
        contact_path = self.root / prepared["artifacts"]["input_contact_sheet"]["path"]
        plan_bytes = plan_path.read_bytes()
        contact_bytes = contact_path.read_bytes()
        self.assertNotIn(private_name.encode("utf-8"), plan_bytes)
        self.assertNotIn(private_name.encode("utf-8"), contact_bytes)
        plan = json.loads(plan_bytes.decode("utf-8"))
        self.assertNotIn("source_filename", json.dumps(plan))
        self.assertTrue(all("source_path" not in entry for entry in plan["reference_inventory"]))
        plan_review = self.approve_plan(prepared)
        proposal = self.propose_results(prepared, plan_review)
        proposal_path = self.root / proposal["artifacts"]["proposal"]["path"]
        comparison_path = self.root / proposal["artifacts"]["comparison_contact_sheet"]["path"]
        self.assertNotIn(private_name.encode("utf-8"), proposal_path.read_bytes())
        self.assertNotIn(private_name.encode("utf-8"), comparison_path.read_bytes())

    def test_unknown_missing_and_animated_result_files_fail_without_proposal(self):
        self.write_template()
        self.image(self.references, "reference-01.png")
        self.write_json("generation-request.json", self.request())
        prepared = self.prepare()
        plan_review = self.approve_plan(prepared)
        (self.results / "sidecar.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(rrv_runtime.RRVError):
            self.propose_results(prepared, plan_review)
        self.assertFalse((self.root / "generation-results-proposal").exists())
        (self.results / "sidecar.json").unlink()
        first = Image.new("RGB", (3, 3), "red")
        second = Image.new("RGB", (3, 3), "blue")
        first.save(self.results / "look.01.gif", save_all=True, append_images=[second], format="GIF")
        first.close()
        second.close()
        with self.assertRaises(rrv_runtime.RRVError):
            self.propose_results(prepared, plan_review)
        self.assertFalse((self.root / "generation-results-proposal").exists())

    def test_pending_retry_and_inventory_drift_block_assembly_without_partial_output(self):
        self.write_template()
        self.image(self.references, "reference-01.png")
        self.image(self.results, "look.01.png")
        self.write_json("generation-request.json", self.request())
        prepared = self.prepare()
        plan_review = self.approve_plan(prepared)
        proposal = self.propose_results(prepared, plan_review)
        pending = proposal["artifacts"]["review_template"]["path"]
        with mock.patch.object(rrv_assets, "_scan_asset_pack", side_effect=AssertionError("must not scan")):
            with self.assertRaises(rrv_runtime.RRVError):
                self.assemble(prepared, plan_review, proposal, pending)
        self.assertFalse((self.root / "generation-asset-pack").exists())
        retry = json.loads((self.root / pending).read_text(encoding="utf-8"))
        retry["decision"] = "approved"
        retry["comparison_contact_sheet_reviewed"] = True
        retry["tasks"][0]["decision"] = "retry"
        retry_path = self.write_json("generation-results-proposal/retry.json", retry)
        with mock.patch.object(rrv_assets, "_scan_asset_pack", side_effect=AssertionError("must not scan")):
            with self.assertRaises(rrv_runtime.RRVError):
                self.assemble(prepared, plan_review, proposal, retry_path)
        self.assertFalse((self.root / "generation-asset-pack").exists())
        denied = self.approve_results(proposal)
        denied_value = json.loads((self.root / denied).read_text(encoding="utf-8"))
        denied_value["tasks"][0]["rights_confirmed"] = False
        denied_path = self.write_json("generation-results-proposal/rights-false.json", denied_value)
        with mock.patch.object(rrv_assets, "_scan_asset_pack", side_effect=AssertionError("must not scan")):
            with self.assertRaises(rrv_runtime.RRVError):
                self.assemble(prepared, plan_review, proposal, denied_path)
        self.assertFalse((self.root / "generation-asset-pack").exists())
        approved = self.approve_results(proposal)
        self.image(self.results, "look.01.png", (200, 20, 20))
        with self.assertRaises(rrv_runtime.RRVError):
            self.assemble(prepared, plan_review, proposal, approved)
        self.assertFalse((self.root / "generation-asset-pack").exists())

    def test_reference_hardlink_is_rejected_when_supported(self):
        self.write_template()
        source = self.image(self.references, "reference-01.png")
        self.write_json("generation-request.json", self.request())
        hardlink = self.references / "reference-copy.png"
        try:
            os.link(source, hardlink)
        except (OSError, NotImplementedError):
            self.skipTest("hardlink creation is unavailable")
        with self.assertRaises(rrv_runtime.RRVError):
            self.prepare()
        self.assertFalse((self.root / "generation-plan").exists())

    def test_result_pack_must_be_new_and_distinct_from_reference_pack(self):
        self.write_template()
        self.image(self.references, "reference-01.png")
        self.write_json("generation-request.json", self.request())
        prepared = self.prepare()
        plan_review = self.approve_plan(prepared)
        with mock.patch.object(rrv_assets, "_scan_asset_pack", side_effect=AssertionError("must not scan")):
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_generation.propose_generation_results(
                    prepared["artifacts"]["generation_plan"]["path"],
                    plan_review,
                    project_root=self.root,
                    result_pack="reference-pack",
                    generation_results_rights_confirmed=True,
                )
        if os.path.normcase("REFERENCE-PACK") == os.path.normcase("reference-pack"):
            with mock.patch.object(rrv_assets, "_scan_asset_pack", side_effect=AssertionError("must not scan")):
                with self.assertRaises(rrv_runtime.RRVError):
                    rrv_generation.propose_generation_results(
                        prepared["artifacts"]["generation_plan"]["path"],
                        plan_review,
                        project_root=self.root,
                        result_pack="REFERENCE-PACK",
                        generation_results_rights_confirmed=True,
                    )
        self.assertFalse((self.root / "generation-results-proposal").exists())

    def test_unsafe_direct_child_arguments_reject_before_scan_or_output(self):
        self.write_template()
        self.image(self.references, "reference-01.png")
        self.image(self.results, "look.01.png")
        self.write_json("generation-request.json", self.request())
        unsafe_names = ("reference-pack.", "reference-pack ", "CON", "NUL.txt", "COM1")
        for unsafe_name in unsafe_names:
            with self.subTest(phase="prepare", name=unsafe_name):
                with mock.patch.object(rrv_assets, "_scan_asset_pack", side_effect=AssertionError("must not scan")):
                    with self.assertRaises(rrv_runtime.RRVError):
                        rrv_generation.prepare_generation(
                            "template.ir.json",
                            "generation-request.json",
                            project_root=self.root,
                            reference_pack=unsafe_name,
                            generation_rights_confirmed=True,
                        )
                self.assertFalse((self.root / "generation-plan").exists())

        prepared = self.prepare()
        plan_review = self.approve_plan(prepared)
        for unsafe_name in unsafe_names:
            with self.subTest(phase="propose", name=unsafe_name):
                with mock.patch.object(rrv_assets, "_scan_asset_pack", side_effect=AssertionError("must not scan")):
                    with self.assertRaises(rrv_runtime.RRVError):
                        rrv_generation.propose_generation_results(
                            prepared["artifacts"]["generation_plan"]["path"],
                            plan_review,
                            project_root=self.root,
                            result_pack=unsafe_name,
                            generation_results_rights_confirmed=True,
                        )
                self.assertFalse((self.root / "generation-results-proposal").exists())

    def test_unsafe_pack_entries_abort_generation_workflows_without_publication(self):
        self.write_template()
        self.image(self.references, "reference-01.png")
        self.image(self.results, "look.01.png")
        self.write_json("generation-request.json", self.request())
        unsafe_entries = ("CON", "NUL.txt", "unsafe-reference.png ")
        self.assertTrue(all(not rrv_assets._portable_path_component(name) for name in unsafe_entries))

        for unsafe_name in unsafe_entries:
            with self.subTest(phase="prepare", entry=unsafe_name):
                def reject_unsafe_reference(*_args, **_kwargs):
                    raise rrv_runtime.RRVError(rrv_runtime.ERR_INVALID_ARGUMENT, "asset pack contains an unsafe entry")

                with mock.patch.object(rrv_assets, "_scan_asset_pack", side_effect=reject_unsafe_reference):
                    with self.assertRaises(rrv_runtime.RRVError):
                        self.prepare()
                self.assertFalse((self.root / "generation-plan").exists())

        prepared = self.prepare()
        plan_review = self.approve_plan(prepared)
        original_scan = rrv_assets._scan_asset_pack
        for unsafe_name in unsafe_entries:
            with self.subTest(phase="propose", entry=unsafe_name):
                def reject_unsafe_result(*args, **kwargs):
                    if args[3] == "result-pack":
                        raise rrv_runtime.RRVError(rrv_runtime.ERR_INVALID_ARGUMENT, "asset pack contains an unsafe entry")
                    return original_scan(*args, **kwargs)

                with mock.patch.object(rrv_assets, "_scan_asset_pack", side_effect=reject_unsafe_result):
                    with self.assertRaises(rrv_runtime.RRVError):
                        self.propose_results(prepared, plan_review)
                self.assertFalse((self.root / "generation-results-proposal").exists())

    def test_casefold_colliding_reference_entries_abort_prepare_without_publication(self):
        self.write_template()
        self.image(self.references, "reference-01.png")
        self.image(self.references, "CaseFold.png")
        self.image(self.references, "casefold.png")
        self.write_json("generation-request.json", self.request())
        names = {path.name for path in self.references.iterdir()}
        if {"CaseFold.png", "casefold.png"}.issubset(names):
            # Case-sensitive hosts can realize both spellings; scanner policy
            # still rejects them because a Windows project root cannot safely
            # distinguish the two entries.
            with self.assertRaises(rrv_runtime.RRVError):
                self.prepare()
        else:
            # NTFS typically cannot materialize both aliases.  Exercise the
            # same public zero-publication boundary with a scanner rejection.
            def reject_casefold_collision(*_args, **_kwargs):
                raise rrv_runtime.RRVError(rrv_runtime.ERR_INVALID_ARGUMENT, "asset pack contains colliding entries")

            with mock.patch.object(rrv_assets, "_scan_asset_pack", side_effect=reject_casefold_collision):
                with self.assertRaises(rrv_runtime.RRVError):
                    self.prepare()
        self.assertFalse((self.root / "generation-plan").exists())

    def test_casefold_colliding_result_entries_abort_proposal_without_publication(self):
        self.write_template()
        self.image(self.references, "reference-01.png")
        self.image(self.results, "look.01.png")
        self.image(self.results, "ResultAlias.png")
        self.image(self.results, "resultalias.png")
        self.write_json("generation-request.json", self.request())
        prepared = self.prepare()
        plan_review = self.approve_plan(prepared)
        names = {path.name for path in self.results.iterdir()}
        if {"ResultAlias.png", "resultalias.png"}.issubset(names):
            with self.assertRaises(rrv_runtime.RRVError):
                self.propose_results(prepared, plan_review)
        else:
            original_scan = rrv_assets._scan_asset_pack

            def reject_casefold_collision(*args, **kwargs):
                if args[3] == "result-pack":
                    raise rrv_runtime.RRVError(rrv_runtime.ERR_INVALID_ARGUMENT, "asset pack contains colliding entries")
                return original_scan(*args, **kwargs)

            with mock.patch.object(rrv_assets, "_scan_asset_pack", side_effect=reject_casefold_collision):
                with self.assertRaises(rrv_runtime.RRVError):
                    self.propose_results(prepared, plan_review)
        self.assertFalse((self.root / "generation-results-proposal").exists())

    def test_same_directory_identity_alias_is_rejected_before_result_scan_or_publish(self):
        self.write_template()
        self.image(self.references, "reference-01.png")
        self.image(self.results, "look.01.png")
        self.write_json("generation-request.json", self.request())
        prepared = self.prepare()
        plan_review = self.approve_plan(prepared)
        original_scan = rrv_assets._scan_asset_pack
        scanned_pack_names = []

        def record_scan(*args, **kwargs):
            scanned_pack_names.append(args[3])
            return original_scan(*args, **kwargs)

        # Simulate an NTFS 8.3/other filesystem alias: the lexical names are
        # different, but their simultaneously-held directory identities match.
        with mock.patch.object(rrv_generation, "_same_directory_identity", return_value=True):
            with mock.patch.object(rrv_assets, "_scan_asset_pack", side_effect=record_scan):
                with self.assertRaises(rrv_runtime.RRVError):
                    self.propose_results(prepared, plan_review)
        self.assertEqual(scanned_pack_names, ["reference-pack"])
        self.assertFalse((self.root / "generation-results-proposal").exists())

        proposal = self.propose_results(prepared, plan_review)
        results_review = self.approve_results(proposal)
        scanned_pack_names.clear()
        with mock.patch.object(rrv_generation, "_same_directory_identity", return_value=True):
            with mock.patch.object(rrv_assets, "_scan_asset_pack", side_effect=record_scan):
                with self.assertRaises(rrv_runtime.RRVError):
                    self.assemble(prepared, plan_review, proposal, results_review)
        self.assertEqual(scanned_pack_names, ["reference-pack"])
        self.assertFalse((self.root / "generation-asset-pack").exists())

    def test_assembly_rejects_over_limit_staged_output_before_publish(self):
        self.write_template()
        self.image(self.references, "reference-01.png")
        self.image(self.results, "look.01.png")
        self.write_json("generation-request.json", self.request())
        prepared = self.prepare()
        plan_review = self.approve_plan(prepared)
        proposal = self.propose_results(prepared, plan_review)
        results_review = self.approve_results(proposal)
        with mock.patch.object(
            rrv_propose,
            "_stage_regular_file_size",
            return_value=rrv_assets.MAX_FILE_BYTES + 1,
        ):
            with self.assertRaises(rrv_runtime.RRVError):
                self.assemble(prepared, plan_review, proposal, results_review)
        self.assertFalse((self.root / "generation-asset-pack").exists())

    def test_assembly_enforces_kind_specific_confirmations_after_packet_validation(self):
        self.write_template()
        self.image(self.references, "identity.png")
        self.image(self.references, "garment.png")
        self.image(self.results, "look.01.png")
        request = self.request()
        request["tasks"][0].update(
            {
                "kind": "identity-try-on",
                "references": [
                    {"source_filename": "identity.png", "role": "identity"},
                    {"source_filename": "garment.png", "role": "garment"},
                ],
            }
        )
        self.write_json("generation-request.json", request)
        prepared = self.prepare()
        plan_review = self.approve_plan(prepared)
        proposal = self.propose_results(prepared, plan_review)
        approved = json.loads((self.root / self.approve_results(proposal)).read_text(encoding="utf-8"))
        self.assertEqual(rrv_generation.validate_generation_results_review_data(approved), [])
        for confirmation in ("identity_confirmed", "garment_confirmed"):
            with self.subTest(confirmation=confirmation):
                review = copy.deepcopy(approved)
                review["tasks"][0][confirmation] = False
                # The standalone schema cannot infer task kind from a proposal,
                # so final assembly is the binding-aware enforcement point.
                self.assertEqual(rrv_generation.validate_generation_results_review_data(review), [])
                review_path = self.write_json(f"generation-results-proposal/{confirmation}-false.json", review)
                with mock.patch.object(rrv_assets, "_scan_asset_pack", side_effect=AssertionError("must not scan")):
                    with self.assertRaises(rrv_runtime.RRVError):
                        self.assemble(prepared, plan_review, proposal, review_path)
                self.assertFalse((self.root / "generation-asset-pack").exists())

    def test_identity_try_on_contact_sheet_mosaics_identity_and_garment_references(self):
        self.write_template([{"id": "look.01", "type": "image", "required": True, "accepted_media": ["image/png"]}])
        self.image(self.references, "identity.png", (220, 90, 60))
        self.image(self.references, "garment.png", (40, 100, 210))
        self.image(self.results, "look.01.png", (60, 180, 100))
        request = self.request()
        request["tasks"][0].update(
            {
                "kind": "identity-try-on",
                "references": [
                    {"source_filename": "identity.png", "role": "identity"},
                    {"source_filename": "garment.png", "role": "garment"},
                ],
            }
        )
        self.write_json("generation-request.json", request)
        prepared = self.prepare()
        plan_review = self.approve_plan(prepared)
        seen_names = []
        original = rrv_assets._thumbnail_for_asset

        def record_thumbnail(asset, *, maximum):
            seen_names.append(asset.name)
            return original(asset, maximum=maximum)

        with mock.patch.object(rrv_assets, "_thumbnail_for_asset", side_effect=record_thumbnail):
            proposal = self.propose_results(prepared, plan_review)
        self.assertIn("identity.png", seen_names)
        self.assertIn("garment.png", seen_names)
        with Image.open(self.root / proposal["artifacts"]["comparison_contact_sheet"]["path"]) as contact:
            self.assertEqual(contact.size, (1800, 225))

    def test_identity_passthrough_needs_identity_not_garment_confirmation(self):
        self.write_template()
        self.image(self.references, "identity.png")
        request = self.request()
        request["tasks"][0].update(
            {
                "kind": "identity-try-on",
                "references": [{"source_filename": "identity.png", "role": "identity"}],
                "passthrough": True,
            }
        )
        self.write_json("generation-request.json", request)
        prepared = self.prepare()
        plan_review = self.approve_plan(prepared)
        proposal = self.propose_results(prepared, plan_review)
        review = json.loads((self.root / proposal["artifacts"]["review_template"]["path"]).read_text(encoding="utf-8"))
        review["decision"] = "approved"
        review["comparison_contact_sheet_reviewed"] = True
        task = review["tasks"][0]
        task.update({"decision": "accept", "identity_confirmed": True, "render_ready_confirmed": True, "rights_confirmed": True})
        self.assertFalse(task["garment_confirmed"])
        review_path = self.write_json("generation-results-proposal/identity-passthrough-review.json", review)
        assembled = self.assemble(prepared, plan_review, proposal, review_path)
        self.assertTrue((self.root / assembled["output_dir"] / "look.01.png").is_file())

    def test_audio_passthrough_is_snapshotted_and_image_passthrough_is_sanitized(self):
        slots = [
            {"id": "still", "type": "image", "required": True, "accepted_media": ["image/png"]},
            {"id": "track", "type": "audio", "required": True, "accepted_media": ["audio/wav"]},
        ]
        self.write_template(slots)
        self.image(self.references, "still-source.png", metadata=True)
        (self.references / "track.wav").write_bytes(b"not-a-real-wav")
        request = {
            "schema_version": "0.6.0",
            "privacy_profile": "local-only",
            "execution_profile": "local-file-drop",
            "adapter_id": "local-file-drop",
            "adapter_version": "1",
            "cloud_upload_confirmed": False,
            "tasks": [
                {"target_slot_id": "still", "kind": "reference-guided-still", "references": [{"source_filename": "still-source.png", "role": "reference"}], "instructions": "copy", "passthrough": True, "omit": False},
                {"target_slot_id": "track", "kind": "reference-guided-still", "references": [{"source_filename": "track.wav", "role": "audio"}], "instructions": "copy", "passthrough": True, "omit": False},
            ],
        }
        self.write_json("generation-request.json", request)
        # Scanner intentionally refuses the malformed WAV; the test documents fail-closed audio handling.
        with mock.patch.object(rrv_assets, "_run_ffprobe", return_value={"streams": [{"codec_type": "audio"}], "format": {"format_name": "wav", "duration": "1"}}):
            prepared = self.prepare()
            plan_review = self.approve_plan(prepared)
            proposal = self.propose_results(prepared, plan_review)
            review = json.loads((self.root / proposal["artifacts"]["review_template"]["path"]).read_text(encoding="utf-8"))
            review["decision"] = "approved"
            review["comparison_contact_sheet_reviewed"] = True
            for task in review["tasks"]:
                task.update({"decision": "accept", "render_ready_confirmed": True, "rights_confirmed": True})
            # The still uses a generic reference and the audio is role=audio:
            # neither requires invented garment/product/background assertions.
            self.assertFalse(any(task["garment_confirmed"] for task in review["tasks"]))
            results_review = self.write_json("generation-results-proposal/minimal-passthrough-review.json", review)
            assembled = self.assemble(prepared, plan_review, proposal, results_review)
        output = self.root / assembled["output_dir"]
        self.assertTrue((output / "still.png").is_file())
        self.assertTrue((output / "track.wav").is_file())
        with Image.open(output / "still.png") as image:
            self.assertNotIn("private-note", image.info)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
