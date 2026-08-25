import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIRECTORY = REPO_ROOT / "skills" / "reference-video-rebuilder" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIRECTORY))

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

import rrv_assets  # noqa: E402
import rrv_browser_handoff  # noqa: E402
import rrv_runtime  # noqa: E402
import rrv_temporal  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _template(audio_mode="mute"):
    return {
        "schema_version": "0.3.0", "template_id": "browser-handoff-test", "coordinate_space": "canvas-pixels",
        "canvas": {"width": 64, "height": 64, "background": "#ffffff", "source_rect": {"x": 0, "y": 0, "width": 64, "height": 64}},
        "source": {"duration_frames": 10, "fps": 10, "width": 64, "height": 64, "source_sha256": "0" * 64},
        "support": {"level": "S1", "confidence": 1, "review_required": False, "warnings": []},
        "tracks": [{"id": "base", "type": "background", "z_index": 0, "overlap_policy": "forbid"}],
        "slots": [
            {"id": "look.01", "type": "image", "required": True, "accepted_media": ["image/png"]},
            {"id": "audio", "type": "audio", "required": False, "accepted_media": ["audio/wav"]},
        ],
        "layers": [{
            "id": "hero", "track_id": "base", "source": {"slot_id": "look.01", "representation": "raw"},
            "active_ranges": [{"start_frame": 0, "end_frame": 10}],
            "layout": {"box": {"x": 0, "y": 0, "width": 64, "height": 64}, "fit": "contain", "object_position": {"x": 0.5, "y": 0.5}},
            "transform": {"anchor": {"x": 0, "y": 0}, "keyframes": [{"frame": 0, "translate_x": 0, "translate_y": 0, "scale_x": 1, "scale_y": 1, "rotation_deg": 0, "opacity": 1, "easing": {"type": "hold"}}]},
            "mask": None, "blend": {"mode": "normal", "opacity": 1}, "z_offset": 0,
        }],
        "remove_layers": [], "events": [],
        "audio": {"slot_id": "audio", "timeline_start_frame": 0, "timeline_end_frame": 10, "source_in_ms": 0, "source_out_ms": 1000, "playback_rate": 1, "loop": False, "gain_db": 0, "fade_in_frames": 0, "fade_out_frames": 0},
        "rebuild_requirements": {"motion_required": True, "motion_mode": "pose-transfer", "audio_mode": audio_mode, "lip_sync_required": False, "voice_likeness_rights_confirmed": False},
        "outputs": [{"id": "out.01", "width": 64, "height": 64, "codec": "h264", "pixel_format": "yuv420p", "audio_codec": "aac", "filename": "output.mp4", "reframe": {"mode": "contain", "object_position": {"x": 0.5, "y": 0.5}, "background": "#ffffff"}}],
    }


@unittest.skipUnless(Image is not None, "Pillow is required")
class BrowserHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        for name in ("reference-pack", "downloaded-pack", "inputs"):
            (self.root / name).mkdir()
        self.ffmpeg = os.environ.get("RRV_TEST_FFMPEG") or shutil.which("ffmpeg")
        self.ffprobe = os.environ.get("RRV_TEST_FFPROBE") or shutil.which("ffprobe")

    def write_json(self, relative, value):
        (self.root / relative).write_text(json.dumps(value), encoding="utf-8")
        return relative

    def video(self, target: Path, *, audio=False, title=None):
        if not self.ffmpeg:
            self.skipTest("ffmpeg unavailable")
        command = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=64x64:r=10:d=1"]
        if audio:
            command += ["-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000:d=1", "-map", "0:v:0", "-map", "1:a:0"]
        else:
            command += ["-an"]
        command += ["-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", "10", "-frames:v", "10"]
        if audio:
            command += ["-c:a", "aac", "-profile:a", "aac_low", "-ar", "48000", "-ac", "2"]
        if title is not None:
            command += ["-metadata", f"title={title}"]
        command += ["-movflags", "+faststart", "-y", str(target)]
        subprocess.run(command, check=True)

    def prepare_temporal(self, *, audio_mode="mute"):
        image_path = self.root / "inputs" / "look.png"
        image = Image.new("RGB", (4, 4), (20, 100, 220))
        image.save(image_path, format="PNG")
        image.close()
        template = self.write_json("template.json", _template(audio_mode))
        manifest = self.write_json("manifest.json", {
            "schema_version": "0.2.0", "template_id": "browser-handoff-test", "privacy_profile": "local-only",
            "assets": [{"slot_id": "look.01", "path": "inputs/look.png", "sha256": _sha(image_path), "media_type": "image/png", "rights_confirmed": True, "cloud_upload_allowed": False, "processor": "local"}],
        })
        request = self.write_json("temporal-request.json", {
            "schema_version": "0.10.0", "output_id": "out.01", "input_slot_ids": ["look.01"], "privacy_profile": "local-only", "execution_profile": "local-file-drop",
            "adapter_id": "local-drop", "adapter_version": "1.0.0", "cloud_upload_confirmed": False, "instructions": "private temporal prompt",
            "capabilities": {"motion_modes": ["pose-transfer"], "audio_modes": [audio_mode], "lip_sync_supported": False, "clone_authorized_voice_supported": False},
        })
        self.video(self.root / "reference-pack" / "action.mp4", audio=audio_mode == "preserve-reference")
        rrv_temporal.prepare_temporal_replacement(template, manifest, request, project_root=self.root, reference_pack="reference-pack", temporal_rights_confirmed=True, ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe")
        review_path = self.root / "temporal-plan" / "temporal-replacement-plan-review.template.json"
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review["decision"] = "approved"
        for key in rrv_temporal._PLAN_CONFIRMATIONS:
            review[key] = True
        review_path.write_text(json.dumps(review), encoding="utf-8")
        return json.loads((self.root / "temporal-plan" / "temporal-replacement-plan.json").read_text(encoding="utf-8"))

    def handoff_request(self, temporal_plan, *, cap=9, relative="handoff-request.json"):
        character_hash = temporal_plan["input_assets"][0]["sha256"]
        motion_hash = temporal_plan["reference_inventory"][0]["sha256"]
        return self.write_json(relative, {
            "schema_version": "0.10.1", "provider_id": "higgsfield-web", "surface": "motion-control", "model": "kling-3.0-motion-control", "resolution": "720p",
            "output_id": "out.01", "character_slot_id": "look.01", "motion_mode": "pose-transfer", "audio_mode": temporal_plan["requirements"]["audio_mode"],
            "lip_sync_requested": False, "clone_authorized_voice_requested": False, "max_credits": cap, "cloud_upload_confirmed": True, "prompt": "private provider prompt should not appear in the plan",
            "upload_authorizations": {
                "character_image": {"source_slot_id": "look.01", "source_sha256": character_hash, "provider_id": "higgsfield-web", "purpose": "motion-control", "output_id": "out.01", "expires_at": "2099-01-01T00:00:00Z", "rights_confirmed": True, "cloud_upload_confirmed": True},
                "motion_reference": {"source_sha256": motion_hash, "provider_id": "higgsfield-web", "purpose": "motion-control", "output_id": "out.01", "expires_at": "2099-01-01T00:00:00Z", "rights_confirmed": True, "cloud_upload_confirmed": True},
            },
        })

    def prepare_handoff(self, temporal_plan, *, cap=9, output="higgsfield-handoff"):
        request = self.handoff_request(temporal_plan, cap=cap, relative=f"{output}-request.json")
        return rrv_browser_handoff.prepare_higgsfield_web_handoff(
            "temporal-plan/temporal-replacement-plan.json", "temporal-plan/temporal-replacement-plan-review.template.json", request,
            project_root=self.root, reference_pack="reference-pack", web_handoff_rights_confirmed=True, output_dir=output,
            ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
        )

    def test_strict_request_and_rights_gate(self):
        self.assertTrue(rrv_browser_handoff.validate_higgsfield_web_handoff_request_data({}))
        self.assertIn("$.schema_version: finite_number", rrv_browser_handoff.validate_higgsfield_web_handoff_request_data({"schema_version": float("nan")}))
        with mock.patch.object(rrv_assets, "_safe_project_root", side_effect=AssertionError("touched")):
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_browser_handoff.prepare_higgsfield_web_handoff(
                    "a", "b", "c", project_root=self.root, reference_pack="reference-pack", web_handoff_rights_confirmed=False
                )

    def test_prepare_cost_cap_and_normalize_into_v010_result_pack(self):
        if not self.ffprobe:
            self.skipTest("ffprobe unavailable")
        temporal_plan = self.prepare_temporal()
        self.prepare_handoff(temporal_plan, cap=7, output="handoff-over-cap")
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_browser_handoff.record_higgsfield_web_action(
                "handoff-over-cap/higgsfield-web-handoff-plan.json", project_root=self.root, max_credits=7, observed_cost_credits=9,
                available_credits_before=10, cloud_upload_confirmed=True, billable_action_confirmed=True, output_dir="receipt-over-cap",
            )
        self.assertFalse((self.root / "receipt-over-cap").exists())
        self.assertEqual(list(self.root.glob(".rrv-higgsfield-web-receipt-*")), [])
        self.assertEqual(list(self.root.glob(".rrv-higgsfield-web-action-*")), [])
        self.prepare_handoff(temporal_plan, cap=9, output="handoff-ok")
        handoff_dir = self.root / "handoff-ok"
        self.assertEqual({item.name for item in handoff_dir.iterdir()}, {"upload", "higgsfield-web-handoff-plan.json"})
        self.assertEqual({item.name for item in (handoff_dir / "upload").iterdir()}, {"character.png", "motion-reference.mp4"})
        plan_text = (handoff_dir / "higgsfield-web-handoff-plan.json").read_text(encoding="utf-8")
        self.assertNotIn("private provider prompt", plan_text)
        self.assertNotIn(
            hashlib.sha256(b"private provider prompt should not appear in the plan").hexdigest(),
            plan_text,
        )
        receipt = rrv_browser_handoff.record_higgsfield_web_action(
            "handoff-ok/higgsfield-web-handoff-plan.json", project_root=self.root, max_credits=9, observed_cost_credits=9,
            available_credits_before=10, cloud_upload_confirmed=True, billable_action_confirmed=True,
        )
        self.assertEqual(receipt["projected_remaining_credits_after"], 1)
        self.video(self.root / "downloaded-pack" / "provider-download.mp4", title="untrusted downloaded title")
        normalized = rrv_browser_handoff.normalize_higgsfield_download(
            "handoff-ok/higgsfield-web-handoff-plan.json", "higgsfield-web-browser-receipt/higgsfield-web-browser-receipt.json",
            project_root=self.root, downloaded_pack="downloaded-pack", reference_pack="reference-pack", downloaded_result_rights_confirmed=True,
            output_result_pack="higgsfield-result", ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
        )
        self.assertFalse(normalized["browser_submission_attested"])
        self.assertEqual({item.name for item in (self.root / "higgsfield-result").iterdir()}, {"temporal-replacement.mp4"})
        proposed = rrv_temporal.propose_temporal_results(
            "temporal-plan/temporal-replacement-plan.json", "temporal-plan/temporal-replacement-plan-review.template.json",
            project_root=self.root, result_pack="higgsfield-result", temporal_results_rights_confirmed=True,
            output_dir="v010-proposal", ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
        )
        self.assertEqual(proposed["counts"]["result_inventory_entries"], 1)

    def test_preserve_reference_audio_is_grafted_exactly(self):
        if not self.ffprobe:
            self.skipTest("ffprobe unavailable")
        temporal_plan = self.prepare_temporal(audio_mode="preserve-reference")
        self.prepare_handoff(temporal_plan, cap=9, output="audio-handoff")
        rrv_browser_handoff.record_higgsfield_web_action(
            "audio-handoff/higgsfield-web-handoff-plan.json", project_root=self.root, max_credits=9, observed_cost_credits=9,
            available_credits_before=10, cloud_upload_confirmed=True, billable_action_confirmed=True, output_dir="audio-receipt",
        )
        self.video(self.root / "downloaded-pack" / "provider.mp4", audio=True)
        rrv_browser_handoff.normalize_higgsfield_download(
            "audio-handoff/higgsfield-web-handoff-plan.json", "audio-receipt/higgsfield-web-browser-receipt.json",
            project_root=self.root, downloaded_pack="downloaded-pack", reference_pack="reference-pack", downloaded_result_rights_confirmed=True,
            output_result_pack="audio-result", ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
        )
        proposed = rrv_temporal.propose_temporal_results(
            "temporal-plan/temporal-replacement-plan.json", "temporal-plan/temporal-replacement-plan-review.template.json",
            project_root=self.root, result_pack="audio-result", temporal_results_rights_confirmed=True,
            output_dir="audio-v010-proposal", ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
        )
        proposal = json.loads((self.root / proposed["artifacts"]["proposal"]["path"]).read_text(encoding="utf-8"))
        self.assertTrue(proposal["audio_validation"]["preserve_reference_payload_match"])

    def test_one_handoff_request_can_issue_only_one_action_receipt(self):
        if not self.ffprobe:
            self.skipTest("ffprobe unavailable")
        temporal_plan = self.prepare_temporal()
        request = self.handoff_request(temporal_plan, cap=9, relative="shared-handoff-request.json")
        for output in ("shared-handoff-a", "shared-handoff-b"):
            rrv_browser_handoff.prepare_higgsfield_web_handoff(
                "temporal-plan/temporal-replacement-plan.json", "temporal-plan/temporal-replacement-plan-review.template.json", request,
                project_root=self.root, reference_pack="reference-pack", web_handoff_rights_confirmed=True, output_dir=output,
                ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
            )
        rrv_browser_handoff.record_higgsfield_web_action(
            "shared-handoff-a/higgsfield-web-handoff-plan.json", project_root=self.root, max_credits=9, observed_cost_credits=9,
            available_credits_before=10, cloud_upload_confirmed=True, billable_action_confirmed=True, output_dir="first-receipt",
        )
        request_sha256 = _sha(self.root / request)
        marker_directory = self.root / rrv_browser_handoff._handoff_request_action_consumption_output_name(request_sha256)
        self.assertEqual({item.name for item in marker_directory.iterdir()}, {rrv_browser_handoff.ACTION_CONSUMPTION_FILENAME})
        marker_text = (marker_directory / rrv_browser_handoff.ACTION_CONSUMPTION_FILENAME).read_text(encoding="utf-8")
        self.assertNotIn("private provider prompt", marker_text)
        self.assertNotIn('"path"', marker_text)
        marker = json.loads(marker_text)
        self.assertEqual(marker["handoff_request_sha256"], request_sha256)
        self.assertEqual(marker["handoff_plan_sha256"], _sha(self.root / "shared-handoff-a" / "higgsfield-web-handoff-plan.json"))
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_browser_handoff.record_higgsfield_web_action(
                "shared-handoff-b/higgsfield-web-handoff-plan.json", project_root=self.root, max_credits=9, observed_cost_credits=9,
                available_credits_before=10, cloud_upload_confirmed=True, billable_action_confirmed=True, output_dir="second-receipt",
            )
        self.assertFalse((self.root / "second-receipt").exists())
        self.assertEqual(list(self.root.glob(".rrv-higgsfield-web-receipt-*")), [])

    def test_receipt_is_terminal_after_prepublication_normalization_failure(self):
        if not self.ffprobe:
            self.skipTest("ffprobe unavailable")
        temporal_plan = self.prepare_temporal()
        self.prepare_handoff(temporal_plan, output="failure-handoff")
        rrv_browser_handoff.record_higgsfield_web_action(
            "failure-handoff/higgsfield-web-handoff-plan.json", project_root=self.root, max_credits=9, observed_cost_credits=9,
            available_credits_before=10, cloud_upload_confirmed=True, billable_action_confirmed=True, output_dir="failure-receipt",
        )
        receipt = self.root / "failure-receipt" / "higgsfield-web-browser-receipt.json"
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_browser_handoff.normalize_higgsfield_download(
                "failure-handoff/higgsfield-web-handoff-plan.json", "failure-receipt/higgsfield-web-browser-receipt.json",
                project_root=self.root, downloaded_pack="downloaded-pack", reference_pack="reference-pack", downloaded_result_rights_confirmed=True,
                output_result_pack="failed-result", ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
            )
        receipt_sha256 = _sha(receipt)
        marker_directory = self.root / rrv_browser_handoff._receipt_consumption_output_name(receipt_sha256)
        self.assertEqual({item.name for item in marker_directory.iterdir()}, {rrv_browser_handoff.RECEIPT_CONSUMPTION_FILENAME})
        marker_text = (marker_directory / rrv_browser_handoff.RECEIPT_CONSUMPTION_FILENAME).read_text(encoding="utf-8")
        self.assertNotIn("private provider prompt", marker_text)
        self.assertNotIn('"path"', marker_text)
        self.video(self.root / "downloaded-pack" / "valid-after-failure.mp4")
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_browser_handoff.normalize_higgsfield_download(
                "failure-handoff/higgsfield-web-handoff-plan.json", "failure-receipt/higgsfield-web-browser-receipt.json",
                project_root=self.root, downloaded_pack="downloaded-pack", reference_pack="reference-pack", downloaded_result_rights_confirmed=True,
                output_result_pack="retry-result", ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
            )
        self.assertFalse((self.root / "failed-result").exists())
        self.assertFalse((self.root / "retry-result").exists())

    def test_receipt_reuse_after_success_is_rejected_before_result_work(self):
        if not self.ffprobe:
            self.skipTest("ffprobe unavailable")
        temporal_plan = self.prepare_temporal()
        self.prepare_handoff(temporal_plan, output="single-use-handoff")
        rrv_browser_handoff.record_higgsfield_web_action(
            "single-use-handoff/higgsfield-web-handoff-plan.json", project_root=self.root, max_credits=9, observed_cost_credits=9,
            available_credits_before=10, cloud_upload_confirmed=True, billable_action_confirmed=True, output_dir="single-use-receipt",
        )
        self.video(self.root / "downloaded-pack" / "provider.mp4")
        rrv_browser_handoff.normalize_higgsfield_download(
            "single-use-handoff/higgsfield-web-handoff-plan.json", "single-use-receipt/higgsfield-web-browser-receipt.json",
            project_root=self.root, downloaded_pack="downloaded-pack", reference_pack="reference-pack", downloaded_result_rights_confirmed=True,
            output_result_pack="first-result", ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
        )
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_browser_handoff.normalize_higgsfield_download(
                "single-use-handoff/higgsfield-web-handoff-plan.json", "single-use-receipt/higgsfield-web-browser-receipt.json",
                project_root=self.root, downloaded_pack="downloaded-pack", reference_pack="reference-pack", downloaded_result_rights_confirmed=True,
                output_result_pack="second-result", ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
            )
        self.assertTrue((self.root / "first-result" / "temporal-replacement.mp4").is_file())
        self.assertFalse((self.root / "second-result").exists())

    def test_receipt_claim_allows_only_one_real_concurrent_normalization(self):
        if not self.ffprobe:
            self.skipTest("ffprobe unavailable")
        temporal_plan = self.prepare_temporal()
        self.prepare_handoff(temporal_plan, output="concurrent-handoff")
        rrv_browser_handoff.record_higgsfield_web_action(
            "concurrent-handoff/higgsfield-web-handoff-plan.json", project_root=self.root, max_credits=9, observed_cost_credits=9,
            available_credits_before=10, cloud_upload_confirmed=True, billable_action_confirmed=True, output_dir="concurrent-receipt",
        )
        self.video(self.root / "downloaded-pack" / "provider.mp4")
        barrier = threading.Barrier(2, timeout=20)
        lock = threading.Lock()
        downstream_calls = {"count": 0}
        original_claim = rrv_browser_handoff._consume_browser_receipt_once
        original_reference = rrv_browser_handoff._reference_snapshot_from_plan

        def synchronized_claim(*args, **kwargs):
            barrier.wait()
            return original_claim(*args, **kwargs)

        def count_downstream(*args, **kwargs):
            with lock:
                downstream_calls["count"] += 1
            return original_reference(*args, **kwargs)

        def normalize(output):
            try:
                rrv_browser_handoff.normalize_higgsfield_download(
                    "concurrent-handoff/higgsfield-web-handoff-plan.json", "concurrent-receipt/higgsfield-web-browser-receipt.json",
                    project_root=self.root, downloaded_pack="downloaded-pack", reference_pack="reference-pack", downloaded_result_rights_confirmed=True,
                    output_result_pack=output, ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
                )
                return "ok"
            except rrv_runtime.RRVError:
                return "rejected"

        with mock.patch.object(rrv_browser_handoff, "_consume_browser_receipt_once", side_effect=synchronized_claim), mock.patch.object(
            rrv_browser_handoff, "_reference_snapshot_from_plan", side_effect=count_downstream
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = [
                    executor.submit(normalize, output)
                    for output in ("concurrent-result-a", "concurrent-result-b")
                ]
                states = [future.result(timeout=120) for future in outcomes]
        self.assertEqual(states.count("ok"), 1)
        self.assertEqual(states.count("rejected"), 1)
        self.assertEqual(downstream_calls["count"], 1)
        result_directories = [
            output for output in ("concurrent-result-a", "concurrent-result-b")
            if (self.root / output / "temporal-replacement.mp4").is_file()
        ]
        self.assertEqual(len(result_directories), 1)

    def test_upload_sidecar_or_expired_reauthorization_blocks_action(self):
        if not self.ffprobe:
            self.skipTest("ffprobe unavailable")
        temporal_plan = self.prepare_temporal()
        self.prepare_handoff(temporal_plan)
        (self.root / "higgsfield-handoff" / "upload" / "sidecar.txt").write_text("no", encoding="utf-8")
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_browser_handoff.record_higgsfield_web_action(
                "higgsfield-handoff/higgsfield-web-handoff-plan.json", project_root=self.root, max_credits=9, observed_cost_credits=9,
                available_credits_before=10, cloud_upload_confirmed=True, billable_action_confirmed=True,
            )
        self.assertFalse((self.root / "higgsfield-web-browser-receipt").exists())
        self.assertEqual(list(self.root.glob(".rrv-higgsfield-web-action-*")), [])
        expired = self.handoff_request(temporal_plan, cap=9, relative="expired-handoff-request.json")
        expired_data = json.loads((self.root / expired).read_text(encoding="utf-8"))
        expired_data["upload_authorizations"]["character_image"]["expires_at"] = "2000-01-01T00:00:00Z"
        expired_data["upload_authorizations"]["motion_reference"]["expires_at"] = "2000-01-01T00:00:00Z"
        (self.root / expired).write_text(json.dumps(expired_data), encoding="utf-8")
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_browser_handoff.prepare_higgsfield_web_handoff(
                "temporal-plan/temporal-replacement-plan.json", "temporal-plan/temporal-replacement-plan-review.template.json", expired,
                project_root=self.root, reference_pack="reference-pack", web_handoff_rights_confirmed=True,
                output_dir="expired-handoff", ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
            )
        self.assertFalse((self.root / "expired-handoff").exists())
        self.assertEqual(list(self.root.glob(".rrv-higgsfield-web-action-*")), [])

    def test_nested_upload_tree_is_rechecked_immediately_before_publication(self):
        if not self.ffprobe:
            self.skipTest("ffprobe unavailable")
        temporal_plan = self.prepare_temporal()
        request = self.handoff_request(temporal_plan, relative="race-request.json")
        original = rrv_browser_handoff._assert_exact_handoff_tree
        calls = {"count": 0}

        def mutate_after_first_exact_check(stage, expected):
            calls["count"] += 1
            original(stage, expected)
            if calls["count"] == 1:
                (stage.path / "upload" / "late-sidecar.txt").write_text("late", encoding="utf-8")

        with mock.patch.object(rrv_browser_handoff, "_assert_exact_handoff_tree", side_effect=mutate_after_first_exact_check):
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_browser_handoff.prepare_higgsfield_web_handoff(
                    "temporal-plan/temporal-replacement-plan.json", "temporal-plan/temporal-replacement-plan-review.template.json", request,
                    project_root=self.root, reference_pack="reference-pack", web_handoff_rights_confirmed=True,
                    output_dir="race-handoff", ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
                )
        self.assertGreaterEqual(calls["count"], 2)
        self.assertFalse((self.root / "race-handoff").exists())

    def test_expiry_blocks_new_receipt_but_not_historical_normalization(self):
        if not self.ffprobe:
            self.skipTest("ffprobe unavailable")
        temporal_plan = self.prepare_temporal()
        self.prepare_handoff(temporal_plan, output="historical-handoff")
        rrv_browser_handoff.record_higgsfield_web_action(
            "historical-handoff/higgsfield-web-handoff-plan.json", project_root=self.root, max_credits=9, observed_cost_credits=9,
            available_credits_before=10, cloud_upload_confirmed=True, billable_action_confirmed=True, output_dir="historical-receipt",
        )
        self.video(self.root / "downloaded-pack" / "provider.mp4")

        class AfterExpiry(datetime):
            @classmethod
            def now(cls, tz=None):
                value = cls(2100, 1, 1, tzinfo=timezone.utc)
                return value if tz is not None else value.replace(tzinfo=None)

        with mock.patch.object(rrv_browser_handoff, "datetime", AfterExpiry):
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_browser_handoff.record_higgsfield_web_action(
                    "historical-handoff/higgsfield-web-handoff-plan.json", project_root=self.root, max_credits=9, observed_cost_credits=9,
                    available_credits_before=10, cloud_upload_confirmed=True, billable_action_confirmed=True, output_dir="expired-receipt",
                )
            normalized = rrv_browser_handoff.normalize_higgsfield_download(
                "historical-handoff/higgsfield-web-handoff-plan.json", "historical-receipt/higgsfield-web-browser-receipt.json",
                project_root=self.root, downloaded_pack="downloaded-pack", reference_pack="reference-pack", downloaded_result_rights_confirmed=True,
                output_result_pack="historical-result", ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
            )
        self.assertFalse((self.root / "expired-receipt").exists())
        self.assertEqual(normalized["counts"]["result_assets"], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
