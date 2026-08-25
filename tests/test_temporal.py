import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timezone


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIRECTORY = REPO_ROOT / "skills" / "reference-video-rebuilder" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIRECTORY))

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

import rrv_assets  # noqa: E402
import rrv_nle  # noqa: E402
import rrv_propose  # noqa: E402
import rrv_runtime  # noqa: E402
import rrv_temporal  # noqa: E402


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _template():
    return {
        "schema_version": "0.3.0",
        "template_id": "temporal-test",
        "coordinate_space": "canvas-pixels",
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
        "rebuild_requirements": {"motion_required": True, "motion_mode": "pose-transfer", "audio_mode": "mute", "lip_sync_required": False, "voice_likeness_rights_confirmed": False},
        "outputs": [{"id": "out.01", "width": 64, "height": 64, "codec": "h264", "pixel_format": "yuv420p", "audio_codec": "aac", "filename": "output.mp4", "reframe": {"mode": "contain", "object_position": {"x": 0.5, "y": 0.5}, "background": "#ffffff"}}],
    }


@unittest.skipUnless(Image is not None, "Pillow is required")
class TemporalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        (self.root / "reference-pack").mkdir()
        (self.root / "result-pack").mkdir()
        (self.root / "inputs").mkdir()
        self.ffmpeg = shutil.which("ffmpeg")
        self.ffprobe = shutil.which("ffprobe")

    def write_json(self, relative, value):
        (self.root / relative).write_text(json.dumps(value), encoding="utf-8")
        return relative

    def set_up_inputs(self):
        image_path = self.root / "inputs" / "look.png"
        image = Image.new("RGB", (4, 4), (20, 100, 220))
        image.save(image_path, format="PNG")
        image.close()
        template_path = self.write_json("template.json", _template())
        manifest_path = self.write_json("manifest.json", {
            "schema_version": "0.2.0", "template_id": "temporal-test", "privacy_profile": "local-only",
            "assets": [{"slot_id": "look.01", "path": "inputs/look.png", "sha256": _sha(image_path), "media_type": "image/png", "rights_confirmed": True, "cloud_upload_allowed": False, "processor": "local"}],
        })
        request_path = self.write_json("request.json", {
            "schema_version": "0.10.0", "output_id": "out.01", "input_slot_ids": ["look.01"],
            "privacy_profile": "local-only", "execution_profile": "local-file-drop", "adapter_id": "local-drop", "adapter_version": "1.0.0", "cloud_upload_confirmed": False,
            "instructions": "Private action instruction that must not be copied into public packets.",
            "capabilities": {"motion_modes": ["pose-transfer"], "audio_modes": ["mute"], "lip_sync_supported": False, "clone_authorized_voice_supported": False},
        })
        return template_path, manifest_path, request_path

    def video(self, target, *, static=False, metadata_title=None):
        if not self.ffmpeg:
            self.skipTest("ffmpeg unavailable")
        source = "color=c=black:s=64x64:r=10:d=1" if static else "testsrc2=s=64x64:r=10:d=1"
        command = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", source, "-an", "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", "10", "-frames:v", "10"]
        if metadata_title is not None:
            command.extend(["-metadata", f"title={metadata_title}"])
        command.extend(["-movflags", "+faststart", "-y", str(target)])
        subprocess.run(command, check=True)

    def audio_video(self, target):
        if not self.ffmpeg:
            self.skipTest("ffmpeg unavailable")
        subprocess.run([
            self.ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=64x64:r=10:d=1",
            "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000:d=1", "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", "10", "-frames:v", "10",
            "-c:a", "aac", "-profile:a", "aac_low", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", "-y", str(target),
        ], check=True)

    def prepare(self):
        template, manifest, request = self.set_up_inputs()
        self.video(self.root / "reference-pack" / "action.mp4")
        return rrv_temporal.prepare_temporal_replacement(template, manifest, request, project_root=self.root, reference_pack="reference-pack", temporal_rights_confirmed=True, ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe")

    def approve_plan(self):
        self.prepare()
        review_path = self.root / "temporal-plan" / "temporal-replacement-plan-review.template.json"
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review["decision"] = "approved"
        for key in rrv_temporal._PLAN_CONFIRMATIONS:
            review[key] = True
        review_path.write_text(json.dumps(review), encoding="utf-8")
        return review_path

    def test_strict_request_validation(self):
        self.assertTrue(rrv_temporal.validate_temporal_request_data({}))
        self.assertIn("$.schema_version: finite_number", rrv_temporal.validate_temporal_request_data({"schema_version": float("nan")}))
        _, _, request = self.set_up_inputs()
        request_data = json.loads((self.root / request).read_text(encoding="utf-8"))
        request_data.update({"privacy_profile": "controller-cloud", "execution_profile": "controller-managed", "cloud_upload_confirmed": True, "controller_label": "remote"})
        self.assertTrue(rrv_temporal.validate_temporal_request_data(request_data))

    def test_rights_gate_is_zero_touch(self):
        with mock.patch.object(rrv_assets, "_safe_project_root", side_effect=AssertionError("touched")):
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_temporal.prepare_temporal_replacement("template.json", "manifest.json", "request.json", project_root=self.root, reference_pack="reference-pack", temporal_rights_confirmed=False)

    def test_strict_json_duplicate_and_snapshot_hook_fail_closed(self):
        (self.root / "request.json").write_text('{"schema_version":"0.10.0","schema_version":"0.10.0"}', encoding="utf-8")
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_temporal.prepare_temporal_replacement(
                "template.json", "manifest.json", "request.json", project_root=self.root,
                reference_pack="reference-pack", temporal_rights_confirmed=True,
            )
        self.assertFalse((self.root / "temporal-plan").exists())
        template, manifest, request = self.set_up_inputs()
        with mock.patch.object(
            rrv_assets,
            "_PROJECT_SNAPSHOT_HOOK",
            side_effect=rrv_runtime.RRVError(rrv_runtime.ERR_TOOL_EXECUTION, "synthetic mutation"),
        ):
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_temporal.prepare_temporal_replacement(
                    template, manifest, request, project_root=self.root,
                    reference_pack="reference-pack", temporal_rights_confirmed=True,
                )
        self.assertFalse((self.root / "temporal-plan").exists())

    def test_e2e_and_verify(self):
        if not self.ffprobe:
            self.skipTest("ffprobe unavailable")
        review_path = self.approve_plan()
        self.assertEqual(
            {path.name for path in (self.root / "temporal-plan").iterdir()},
            {"temporal-input-contact-sheet.png", "temporal-replacement-plan.json", "temporal-replacement-plan-review.template.json"},
        )
        self.video(self.root / "result-pack" / "temporal-replacement.mp4")
        rrv_temporal.propose_temporal_results(
            "temporal-plan/temporal-replacement-plan.json", "temporal-plan/temporal-replacement-plan-review.template.json",
            project_root=self.root, result_pack="result-pack", temporal_results_rights_confirmed=True,
            ffmpeg=self.ffmpeg, ffprobe=self.ffprobe,
        )
        self.assertEqual(
            {path.name for path in (self.root / "temporal-results-proposal").iterdir()},
            {"temporal-results-contact-sheet.png", "temporal-technical-sanity.json", "temporal-results-proposal.json", "temporal-results-review.template.json"},
        )
        results_review_path = self.root / "temporal-results-proposal" / "temporal-results-review.template.json"
        results_review = json.loads(results_review_path.read_text(encoding="utf-8"))
        results_review["decision"] = "approved"
        for key in rrv_temporal._RESULT_CONFIRMATIONS:
            results_review[key] = True
        results_review_path.write_text(json.dumps(results_review), encoding="utf-8")
        frozen = rrv_temporal.freeze_temporal_delivery(
            "temporal-plan/temporal-replacement-plan.json", "temporal-plan/temporal-replacement-plan-review.template.json",
            "temporal-results-proposal/temporal-results-proposal.json", "temporal-results-proposal/temporal-results-review.template.json",
            project_root=self.root, ffmpeg=self.ffmpeg, ffprobe=self.ffprobe,
        )
        self.assertFalse(frozen["bitstream_faithful"])
        self.assertEqual(
            {path.name for path in (self.root / "temporal-delivery").iterdir()},
            {"temporal-replacement.mp4", "temporal-delivery-report.json"},
        )
        report = "temporal-delivery/temporal-delivery-report.json"
        self.assertTrue(rrv_temporal.verify_temporal_delivery(report, project_root=self.root, ffmpeg=self.ffmpeg, ffprobe=self.ffprobe)["verified"])
        sidecar = self.root / "temporal-delivery" / "unexpected-sidecar.txt"
        sidecar.write_bytes(b"sidecar")
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_temporal.verify_temporal_delivery(report, project_root=self.root, ffmpeg=self.ffmpeg, ffprobe=self.ffprobe)
        sidecar.unlink()
        renamed = self.root / "temporal-delivery" / "copied-report.json"
        (self.root / report).rename(renamed)
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_temporal.verify_temporal_delivery(
                "temporal-delivery/copied-report.json", project_root=self.root, ffmpeg=self.ffmpeg, ffprobe=self.ffprobe
            )

    def test_static_result_fails_before_proposal_output(self):
        if not self.ffprobe:
            self.skipTest("ffprobe unavailable")
        self.approve_plan()
        self.video(self.root / "result-pack" / "temporal-replacement.mp4", static=True)
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_temporal.propose_temporal_results(
                "temporal-plan/temporal-replacement-plan.json", "temporal-plan/temporal-replacement-plan-review.template.json",
                project_root=self.root, result_pack="result-pack", temporal_results_rights_confirmed=True,
                ffmpeg=self.ffmpeg, ffprobe=self.ffprobe,
            )
        self.assertFalse((self.root / "temporal-results-proposal").exists())

    def test_preserve_reference_requires_exact_staged_audio_payload(self):
        if not self.ffprobe:
            self.skipTest("ffprobe unavailable")
        template, manifest, request = self.set_up_inputs()
        template_data = json.loads((self.root / template).read_text(encoding="utf-8"))
        template_data["rebuild_requirements"]["audio_mode"] = "preserve-reference"
        (self.root / template).write_text(json.dumps(template_data), encoding="utf-8")
        request_data = json.loads((self.root / request).read_text(encoding="utf-8"))
        request_data["capabilities"]["audio_modes"] = ["preserve-reference"]
        (self.root / request).write_text(json.dumps(request_data), encoding="utf-8")
        source = self.root / "reference-pack" / "action.mp4"
        self.audio_video(source)
        shutil.copyfile(source, self.root / "result-pack" / "temporal-replacement.mp4")
        result = rrv_temporal.prepare_temporal_replacement(
            template, manifest, request, project_root=self.root, reference_pack="reference-pack",
            temporal_rights_confirmed=True, ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
        )
        review_path = self.root / result["artifacts"]["review_template"]["path"]
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review["decision"] = "approved"
        for key in rrv_temporal._PLAN_CONFIRMATIONS:
            review[key] = True
        review_path.write_text(json.dumps(review), encoding="utf-8")
        proposed = rrv_temporal.propose_temporal_results(
            "temporal-plan/temporal-replacement-plan.json", "temporal-plan/temporal-replacement-plan-review.template.json",
            project_root=self.root, result_pack="result-pack", temporal_results_rights_confirmed=True,
            ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
        )
        proposal = json.loads((self.root / proposed["artifacts"]["proposal"]["path"]).read_text(encoding="utf-8"))
        self.assertTrue(proposal["audio_validation"]["preserve_reference_payload_match"])
        self.assertEqual(proposal["audio_validation"]["source_audio_payload_sha256"], proposal["audio_validation"]["result_audio_payload_sha256"])

    def test_preserve_reference_without_action_audio_rejects_plan_publication(self):
        if not self.ffprobe:
            self.skipTest("ffprobe unavailable")
        template, manifest, request = self.set_up_inputs()
        template_data = json.loads((self.root / template).read_text(encoding="utf-8"))
        template_data["rebuild_requirements"]["audio_mode"] = "preserve-reference"
        (self.root / template).write_text(json.dumps(template_data), encoding="utf-8")
        request_data = json.loads((self.root / request).read_text(encoding="utf-8"))
        request_data["capabilities"]["audio_modes"] = ["preserve-reference"]
        (self.root / request).write_text(json.dumps(request_data), encoding="utf-8")
        self.video(self.root / "reference-pack" / "action.mp4")
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_temporal.prepare_temporal_replacement(
                template, manifest, request, project_root=self.root, reference_pack="reference-pack",
                temporal_rights_confirmed=True, ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
            )
        self.assertFalse((self.root / "temporal-plan").exists())

    def test_private_metadata_is_rejected_before_results_proposal(self):
        if not self.ffprobe:
            self.skipTest("ffprobe unavailable")
        self.approve_plan()
        self.video(self.root / "result-pack" / "temporal-replacement.mp4", metadata_title="PRIVATE")
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_temporal.propose_temporal_results(
                "temporal-plan/temporal-replacement-plan.json", "temporal-plan/temporal-replacement-plan-review.template.json",
                project_root=self.root, result_pack="result-pack", temporal_results_rights_confirmed=True,
                ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
            )
        self.assertFalse((self.root / "temporal-results-proposal").exists())

    def test_exact_stage_set_rejects_a_sidecar(self):
        stage = rrv_propose._new_staging_directory(self.root, "temporal-test")
        try:
            expected_path = rrv_propose._stage_path(self.root, stage, "expected.json")
            with rrv_propose._open_stage_output_file(stage, expected_path, "test expected artifact") as handle:
                handle.write(b"expected")
            sidecar_path = rrv_propose._stage_path(self.root, stage, "sidecar.bin")
            with rrv_propose._open_stage_output_file(stage, sidecar_path, "test sidecar") as handle:
                handle.write(b"sidecar")
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_temporal._assert_exact_temporal_stage_files(
                    stage,
                    {"expected.json": hashlib.sha256(b"expected").hexdigest()},
                    label="Temporal Test",
                )
        finally:
            rrv_propose._cleanup_directory(self.root, stage)

    def test_staged_media_hold_blocks_or_detects_a_tool_path_swap(self):
        if not self.ffprobe:
            self.skipTest("ffprobe unavailable")
        original = self.root / "original.mp4"
        alternate = self.root / "alternate.mp4"
        self.video(original)
        self.video(alternate, static=True)
        stage = rrv_propose._new_staging_directory(self.root, "temporal-swap")
        try:
            def stage_copy(input_path, name):
                identity = rrv_assets._safe_regular_file(input_path, message="test source")
                snapshot, digest = rrv_assets._snapshot_bound_asset(identity)
                try:
                    return rrv_temporal._copy_snapshot_to_stage(
                        self.root, stage, name, snapshot, expected_sha256=digest,
                        expected_size=identity.size_bytes, label="test staged media",
                    )
                finally:
                    snapshot.close()

            staged = stage_copy(original, "stable.mp4")
            staged_alternate = stage_copy(alternate, "alternate.mp4")
            original_probe = rrv_nle._full_ffprobe_facts
            outcome = {"swapped": False, "blocked": False}

            def swap_then_probe(path, ffprobe, *, timeout_seconds):
                try:
                    os.replace(staged_alternate, staged)
                    outcome["swapped"] = True
                except OSError:
                    outcome["blocked"] = True
                return original_probe(path, ffprobe, timeout_seconds=timeout_seconds)

            with mock.patch.object(rrv_nle, "_full_ffprobe_facts", side_effect=swap_then_probe):
                if os.name == "nt":
                    try:
                        rrv_temporal._inspect_staged_media(
                            stage, staged, ffprobe=self.ffprobe or "ffprobe", timeout_seconds=60, role="swap test"
                        )
                    except rrv_runtime.RRVError:
                        self.assertTrue(outcome["swapped"])
                    else:
                        self.assertTrue(outcome["blocked"])
                else:
                    with self.assertRaises(rrv_runtime.RRVError):
                        rrv_temporal._inspect_staged_media(
                            stage, staged, ffprobe=self.ffprobe or "ffprobe", timeout_seconds=60, role="swap test"
                        )
                    self.assertTrue(outcome["swapped"])
        finally:
            rrv_propose._cleanup_directory(self.root, stage)

    def test_expired_authorization_blocks_new_operations_but_not_historical_verify(self):
        if not self.ffprobe:
            self.skipTest("ffprobe unavailable")
        template, manifest, request = self.set_up_inputs()
        request_data = json.loads((self.root / request).read_text(encoding="utf-8"))
        request_data["local_authorization_assertion"] = {
            "subject": "local authorization only", "purpose": "temporal-replacement", "provider": "local-drop",
            "output_id": "out.01", "expires_at": "2000-01-01T00:00:00Z",
        }
        (self.root / request).write_text(json.dumps(request_data), encoding="utf-8")
        self.assertEqual(rrv_temporal.validate_temporal_request_data(request_data), [])
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_temporal.prepare_temporal_replacement(
                template, manifest, request, project_root=self.root, reference_pack="reference-pack", temporal_rights_confirmed=True,
                ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
            )

        class BeforeExpiry(datetime):
            @classmethod
            def now(cls, tz=None):
                value = cls(1999, 1, 1, tzinfo=timezone.utc)
                return value if tz is not None else value.replace(tzinfo=None)

        self.video(self.root / "reference-pack" / "action.mp4")
        self.video(self.root / "result-pack" / "temporal-replacement.mp4")
        with mock.patch.object(rrv_temporal, "datetime", BeforeExpiry):
            rrv_temporal.prepare_temporal_replacement(
                template, manifest, request, project_root=self.root, reference_pack="reference-pack", temporal_rights_confirmed=True,
                ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
            )
            review_path = self.root / "temporal-plan" / "temporal-replacement-plan-review.template.json"
            review = json.loads(review_path.read_text(encoding="utf-8"))
            review["decision"] = "approved"
            for key in rrv_temporal._PLAN_CONFIRMATIONS:
                review[key] = True
            review_path.write_text(json.dumps(review), encoding="utf-8")
            rrv_temporal.propose_temporal_results(
                "temporal-plan/temporal-replacement-plan.json", "temporal-plan/temporal-replacement-plan-review.template.json",
                project_root=self.root, result_pack="result-pack", temporal_results_rights_confirmed=True,
                ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
            )
            results_review_path = self.root / "temporal-results-proposal" / "temporal-results-review.template.json"
            results_review = json.loads(results_review_path.read_text(encoding="utf-8"))
            results_review["decision"] = "approved"
            for key in rrv_temporal._RESULT_CONFIRMATIONS:
                results_review[key] = True
            results_review_path.write_text(json.dumps(results_review), encoding="utf-8")
            rrv_temporal.freeze_temporal_delivery(
                "temporal-plan/temporal-replacement-plan.json", "temporal-plan/temporal-replacement-plan-review.template.json",
                "temporal-results-proposal/temporal-results-proposal.json", "temporal-results-proposal/temporal-results-review.template.json",
                project_root=self.root, ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
            )
        self.assertTrue(rrv_temporal.verify_temporal_delivery(
            "temporal-delivery/temporal-delivery-report.json", project_root=self.root,
            ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
        )["verified"])

    def test_clone_voice_authorization_is_hashed_and_review_gated(self):
        if not self.ffprobe:
            self.skipTest("ffprobe unavailable")
        template, manifest, request = self.set_up_inputs()
        template_data = json.loads((self.root / template).read_text(encoding="utf-8"))
        template_data["rebuild_requirements"].update({"audio_mode": "clone-authorized-voice", "voice_likeness_rights_confirmed": True})
        (self.root / template).write_text(json.dumps(template_data), encoding="utf-8")
        request_data = json.loads((self.root / request).read_text(encoding="utf-8"))
        request_data["capabilities"].update({"audio_modes": ["clone-authorized-voice"], "clone_authorized_voice_supported": True})
        request_data["local_authorization_assertion"] = {
            "subject": "authorized local subject", "purpose": "temporal-replacement", "provider": "local-drop",
            "output_id": "out.01", "expires_at": "2099-01-01T00:00:00Z",
        }
        (self.root / request).write_text(json.dumps(request_data), encoding="utf-8")
        self.video(self.root / "reference-pack" / "action.mp4")
        self.audio_video(self.root / "result-pack" / "temporal-replacement.mp4")
        prepared = rrv_temporal.prepare_temporal_replacement(
            template, manifest, request, project_root=self.root, reference_pack="reference-pack",
            temporal_rights_confirmed=True, ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
        )
        plan = json.loads((self.root / prepared["artifacts"]["temporal_plan"]["path"]).read_text(encoding="utf-8"))
        self.assertTrue(plan["requirements"]["voice_authorization_required"])
        self.assertNotIn("authorized local subject", json.dumps(plan))
        review_path = self.root / prepared["artifacts"]["review_template"]["path"]
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review["decision"] = "approved"
        for key in rrv_temporal._PLAN_CONFIRMATIONS:
            review[key] = True
        review_path.write_text(json.dumps(review), encoding="utf-8")
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_temporal.propose_temporal_results(
                "temporal-plan/temporal-replacement-plan.json", "temporal-plan/temporal-replacement-plan-review.template.json",
                project_root=self.root, result_pack="result-pack", temporal_results_rights_confirmed=True,
                ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
            )
        review["voice_authorization_confirmed"] = True
        review_path.write_text(json.dumps(review), encoding="utf-8")
        proposed = rrv_temporal.propose_temporal_results(
            "temporal-plan/temporal-replacement-plan.json", "temporal-plan/temporal-replacement-plan-review.template.json",
            project_root=self.root, result_pack="result-pack", temporal_results_rights_confirmed=True,
            output_dir="voice-proposal", ffmpeg=self.ffmpeg or "ffmpeg", ffprobe=self.ffprobe or "ffprobe",
        )
        results_review = json.loads((self.root / proposed["artifacts"]["review_template"]["path"]).read_text(encoding="utf-8"))
        self.assertTrue(results_review["voice_authorization_required"])
