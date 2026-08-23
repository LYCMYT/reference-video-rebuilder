import copy
import io
import json
import math
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
    from PIL import Image
except ImportError:  # pragma: no cover - runtime dependency is required by the skill.
    Image = None

import rrv_assets  # noqa: E402
import rrv_runtime  # noqa: E402
import video_remix  # noqa: E402


def template_document(slots):
    """A compact but fully valid Template IR for local asset workflow tests."""

    slots = list(slots)
    if not any(slot["id"] == "audio" for slot in slots):
        slots.append(
            {
                "id": "audio",
                "type": "audio",
                "required": False,
                "accepted_media": ["audio/wav"],
            }
        )
    return {
        "schema_version": "0.2.0",
        "template_id": "asset-pack-test",
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
                "reframe": {
                    "mode": "contain",
                    "object_position": {"x": 0.5, "y": 0.5},
                    "background": "#ffffff",
                },
            }
        ],
    }


@unittest.skipUnless(Image is not None, "Pillow is installed from requirements-runtime.txt")
class AssetPackTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        self.pack = self.root / "asset-pack"
        self.pack.mkdir()

    def write_template(self, slots=None, path="template.ir.json"):
        slots = slots or [
            {"id": "hero", "type": "image", "required": True, "accepted_media": ["image/png"]},
            {"id": "optional", "type": "image", "required": False, "accepted_media": ["image/png"]},
        ]
        document = template_document(slots)
        self.assertEqual(video_remix.validate_template_data(document), [])
        template_path = self.root / path
        template_path.parent.mkdir(parents=True, exist_ok=True)
        template_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        return path, document

    def image(self, name, color=(25, 100, 210)):
        path = self.pack / name
        Image.new("RGB", (9, 7), color).save(path, format="PNG")
        return path

    def proposal(self, template="template.ir.json", **kwargs):
        return rrv_assets.propose_asset_pack(
            template,
            project_root=self.root,
            asset_pack="asset-pack",
            asset_pack_rights_confirmed=True,
            **kwargs,
        )

    def proposal_data(self, result):
        return json.loads((self.root / result["artifacts"]["proposal"]["path"]).read_text(encoding="utf-8"))

    def review_data(self, result):
        return json.loads((self.root / result["artifacts"]["review_template"]["path"]).read_text(encoding="utf-8"))

    def approve(self, review):
        review = copy.deepcopy(review)
        review["decision"] = "approved"
        review["contact_sheet_reviewed"] = True
        review["local_only_confirmed"] = True
        for mapping in review["mappings"]:
            if mapping["action"] == "use":
                mapping.update(
                    {
                        "content_reviewed": True,
                        "media_compatibility_confirmed": True,
                        "render_ready_confirmed": True,
                        "rights_confirmed": True,
                    }
                )
            elif mapping["action"] == "omit":
                mapping["omit_confirmed"] = True
        return review

    def write_review(self, review, relative="asset-proposal/approved-review.json"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
        return relative

    def force_use(self, review, slot_id, asset_id):
        """Turn an unresolved review row into a fully confirmed manual use."""

        mapping = next(item for item in review["mappings"] if item["slot_id"] == slot_id)
        mapping.clear()
        mapping.update(
            {
                "slot_id": slot_id,
                "action": "use",
                "asset_id": asset_id,
                "content_reviewed": True,
                "media_compatibility_confirmed": True,
                "render_ready_confirmed": True,
                "rights_confirmed": True,
                "processor": "direct",
            }
        )
        return review

    def test_portable_asset_pack_components_reject_windows_aliases_before_scan_or_output(self):
        self.write_template()
        self.image("hero.png")
        rejected = (
            "asset-pack.",
            "asset-pack ",
            "CON",
            "NUL.txt",
            "COM1",
            "LPT9.webp",
            "COM¹.png",
            "LPT³.txt",
            "CONIN$.png",
            "bad<name",
            "bad\x1fname",
        )
        for name in rejected:
            with self.subTest(name=repr(name)), mock.patch.object(
                rrv_assets, "_scan_asset_pack", side_effect=AssertionError("must not scan")
            ):
                with self.assertRaises(rrv_runtime.RRVError):
                    rrv_assets.propose_asset_pack(
                        "template.ir.json",
                        project_root=self.root,
                        asset_pack=name,
                        asset_pack_rights_confirmed=True,
                        output_dir="portable-path-rejected",
                    )
            self.assertFalse((self.root / "portable-path-rejected").exists())

        self.assertEqual(rrv_assets._relative_path_parts("look.01.png"), ("look.01.png",))
        self.assertEqual(rrv_assets._relative_path_parts("nested/.keep"), ("nested", ".keep"))

    def test_unsafe_or_casefold_colliding_pack_entries_fail_before_open_or_publish(self):
        self.write_template()
        cases = (
            ("trailing-dot", ("unsafe.",)),
            ("trailing-space", ("unsafe ",)),
            ("reserved-con", ("CON",)),
            ("reserved-nul-extension", ("NUL.txt",)),
            ("reserved-com", ("COM1.png",)),
            ("reserved-com-superscript", ("COM².png",)),
            ("reserved-lpt-superscript", ("LPT³.txt",)),
            ("control", ("bad\x1fname.png",)),
            ("casefold-collision", ("A.png", "a.png")),
        )
        for label, names in cases:
            entries = []
            for name in names:
                entry = mock.Mock()
                entry.name = name
                entries.append(entry)
            scanner = mock.MagicMock()
            scanner.__enter__.return_value = entries
            with self.subTest(label=label), mock.patch.object(
                rrv_assets.os, "scandir", return_value=scanner
            ), mock.patch.object(
                rrv_assets, "_safe_regular_file", side_effect=AssertionError("must not open")
            ):
                with self.assertRaises(rrv_runtime.RRVError):
                    self.proposal(output_dir="unsafe-pack-entry-rejected")
            self.assertFalse((self.root / "unsafe-pack-entry-rejected").exists())

        self.image("A.png")
        accepted = self.proposal(output_dir="single-uppercase-entry")
        self.assertEqual(accepted["counts"]["inventory_entries"], 1)

    def test_thumbnail_applies_exif_orientation_and_reconstructs_metadata_free_pixels(self):
        source = Image.new("RGB", (2, 3))
        source.putdata(
            [
                (255, 0, 0),
                (0, 255, 0),
                (0, 0, 255),
                (255, 255, 0),
                (255, 0, 255),
                (0, 255, 255),
            ]
        )
        exif = Image.Exif()
        exif[274] = 6
        try:
            source.save(self.pack / "oriented.png", format="PNG", exif=exif)
        finally:
            source.close()

        scanned = []
        thumbnail = None
        try:
            with rrv_assets._root_guard(self.root) as root_identity:
                with rrv_assets._asset_pack_guard(self.root, root_identity, "asset-pack") as (pack, pack_identity):
                    scanned, _ = rrv_assets._scan_asset_pack(
                        root_identity,
                        pack,
                        pack_identity,
                        "asset-pack",
                        ffprobe="ignored",
                        timeout_seconds=1,
                    )
                    thumbnail = rrv_assets._thumbnail_for_asset(scanned[0], maximum=(100, 100))
            self.assertIsNotNone(thumbnail)
            assert thumbnail is not None
            self.assertEqual(thumbnail.size, (3, 2))
            self.assertEqual(
                list(thumbnail.getdata()),
                [
                    (255, 0, 255),
                    (0, 0, 255),
                    (255, 0, 0),
                    (0, 255, 255),
                    (255, 255, 0),
                    (0, 255, 0),
                ],
            )
            self.assertEqual(thumbnail.info, {})
            self.assertEqual(dict(thumbnail.getexif()), {})
        finally:
            if thumbnail is not None:
                thumbnail.close()
            rrv_assets._close_scanned_assets(scanned)

    def test_happy_path_freezes_opaque_assets_and_current_validator_consumes_it(self):
        template_path, template = self.write_template()
        source = self.image("hero.png")
        original = source.read_bytes()
        result = self.proposal(template_path)
        proposal = self.proposal_data(result)
        self.assertEqual(rrv_assets.validate_asset_proposal_data(proposal), [])
        self.assertEqual(proposal["privacy_profile"], "local-only")
        self.assertTrue(proposal["analysis_rights_confirmed"])
        self.assertEqual(proposal["inventory"][0]["source_path"], "asset-pack/hero.png")
        self.assertEqual([item["slot_id"] for item in proposal["slot_candidates"]], ["audio", "hero", "optional"])
        self.assertEqual(source.read_bytes(), original)
        for artifact in result["artifacts"].values():
            self.assertFalse(Path(artifact["path"]).is_absolute())
            self.assertTrue((self.root / artifact["path"]).is_file())
        review = self.approve(self.review_data(result))
        review_path = self.write_review(review)
        frozen = rrv_assets.freeze_assets(
            result["artifacts"]["proposal"]["path"],
            review_path,
            project_root=self.root,
        )
        manifest_path = self.root / frozen["artifacts"]["assets_manifest"]["path"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], "0.2.0")
        self.assertEqual([item["slot_id"] for item in manifest["assets"]], ["hero"])
        self.assertEqual(manifest["assets"][0]["path"], "frozen-assets/asset-0001.png")
        self.assertTrue((self.root / manifest["assets"][0]["path"]).is_file())
        self.assertEqual(
            video_remix.validate_assets_data(template, manifest, manifest_path, check_files=True, project_root=self.root),
            [],
        )
        report = json.loads((self.root / frozen["artifacts"]["freeze_report"]["path"]).read_text(encoding="utf-8"))
        self.assertEqual(set(report), {"schema_version", "proposal_sha256", "review_sha256", "template_sha256", "manifest_sha256", "inventory_sha256", "scanner_policy_version", "counts"})

    def test_exact_missing_ambiguous_incompatible_and_no_semantic_guessing(self):
        slots = [
            {"id": "exact", "type": "image", "required": True, "accepted_media": ["image/png"]},
            {"id": "missing", "type": "image", "required": True, "accepted_media": ["image/png"]},
            {"id": "ambiguous", "type": "image", "required": True, "accepted_media": ["image/png"]},
            {"id": "incompatible", "type": "audio", "required": True, "accepted_media": ["audio/wav"]},
        ]
        self.write_template(slots)
        self.image("exact.png")
        self.image("ambiguous.png")
        self.image("ambiguous.webp")
        self.image("incompatible.png")
        self.image("looks-like-a-hero.png")
        proposal = self.proposal_data(self.proposal())
        candidates = {item["slot_id"]: item for item in proposal["slot_candidates"]}
        self.assertEqual(candidates["exact"]["status"], "suggested")
        self.assertEqual(candidates["missing"]["status"], "missing")
        self.assertEqual(candidates["ambiguous"]["status"], "ambiguous")
        self.assertEqual(candidates["incompatible"]["status"], "incompatible")
        self.assertEqual(candidates["incompatible"]["candidate_asset_ids"], [])
        self.assertFalse(any("looks-like" in item["slot_id"] for item in proposal["slot_candidates"]))

    def test_freeze_rejects_manual_use_for_missing_required_exact_name_slot_without_output(self):
        template_path, _ = self.write_template(
            [{"id": "required-slot", "type": "image", "required": True, "accepted_media": ["image/png"]}]
        )
        self.image("wrong-name.png")
        result = self.proposal(template_path)
        proposal = self.proposal_data(result)
        candidate = next(item for item in proposal["slot_candidates"] if item["slot_id"] == "required-slot")
        self.assertEqual(candidate["status"], "missing")
        review = self.force_use(
            self.approve(self.review_data(result)),
            "required-slot",
            proposal["inventory"][0]["asset_id"],
        )
        review_path = self.write_review(review, "asset-proposal/wrong-name-review.json")

        with self.assertRaises(rrv_runtime.RRVError):
            rrv_assets.freeze_assets(
                result["artifacts"]["proposal"]["path"],
                review_path,
                project_root=self.root,
                output_dir="frozen-wrong-name",
            )
        self.assertFalse((self.root / "frozen-wrong-name").exists())

    def test_freeze_rejects_manual_use_for_ambiguous_and_incompatible_candidates_without_output(self):
        ambiguous_template, _ = self.write_template(
            [{"id": "ambiguous", "type": "image", "required": True, "accepted_media": ["image/png"]}],
            path="ambiguous-template.ir.json",
        )
        self.image("ambiguous.png")
        self.image("ambiguous.webp")
        ambiguous_result = self.proposal(ambiguous_template, output_dir="ambiguous-proposal")
        ambiguous_proposal = self.proposal_data(ambiguous_result)
        ambiguous_candidate = next(
            item for item in ambiguous_proposal["slot_candidates"] if item["slot_id"] == "ambiguous"
        )
        self.assertEqual(ambiguous_candidate["status"], "ambiguous")
        ambiguous_review = self.force_use(
            self.approve(self.review_data(ambiguous_result)),
            "ambiguous",
            ambiguous_candidate["candidate_asset_ids"][0],
        )
        ambiguous_review_path = self.write_review(ambiguous_review, "ambiguous-proposal/manual-review.json")

        with self.assertRaises(rrv_runtime.RRVError):
            rrv_assets.freeze_assets(
                ambiguous_result["artifacts"]["proposal"]["path"],
                ambiguous_review_path,
                project_root=self.root,
                output_dir="frozen-ambiguous",
            )
        self.assertFalse((self.root / "frozen-ambiguous").exists())

        incompatible_template, _ = self.write_template(
            [{"id": "incompatible", "type": "image", "required": True, "accepted_media": ["image/jpeg"]}],
            path="incompatible-template.ir.json",
        )
        self.image("incompatible.png")
        Image.new("RGB", (9, 7), (30, 200, 90)).save(self.pack / "wrong-name.jpg", format="JPEG")
        incompatible_result = self.proposal(incompatible_template, output_dir="incompatible-proposal")
        incompatible_proposal = self.proposal_data(incompatible_result)
        incompatible_candidate = next(
            item for item in incompatible_proposal["slot_candidates"] if item["slot_id"] == "incompatible"
        )
        self.assertEqual(incompatible_candidate["status"], "incompatible")
        wrong_name_asset_id = next(
            item["asset_id"]
            for item in incompatible_proposal["inventory"]
            if item["source_path"].endswith("/wrong-name.jpg")
        )
        incompatible_review = self.force_use(
            self.approve(self.review_data(incompatible_result)),
            "incompatible",
            wrong_name_asset_id,
        )
        incompatible_review_path = self.write_review(incompatible_review, "incompatible-proposal/manual-review.json")

        with self.assertRaises(rrv_runtime.RRVError):
            rrv_assets.freeze_assets(
                incompatible_result["artifacts"]["proposal"]["path"],
                incompatible_review_path,
                project_root=self.root,
                output_dir="frozen-incompatible",
            )
        self.assertFalse((self.root / "frozen-incompatible").exists())

    def test_template_slots_may_declare_video_without_expanding_inventory_media(self):
        slots = [{"id": "clip", "type": "video", "required": False, "accepted_media": ["video/mp4"]}]
        self.write_template(slots)
        self.image("other.png")
        proposal = self.proposal_data(self.proposal())
        candidate = next(item for item in proposal["slot_candidates"] if item["slot_id"] == "clip")
        self.assertEqual(candidate["accepted_media"], ["video/mp4"])
        self.assertEqual(candidate["status"], "missing")

    def test_unknown_sidecar_and_animation_fail_closed_without_final_output(self):
        self.write_template()
        self.image("hero.png")
        (self.pack / "sidecar.json").write_text("{}", encoding="utf-8")
        with mock.patch.object(rrv_assets, "_run_ffprobe", return_value=None):
            with self.assertRaises(rrv_runtime.RRVError) as caught:
                self.proposal()
        self.assertEqual(caught.exception.code, rrv_runtime.ERR_INVALID_ARGUMENT)
        self.assertFalse((self.root / "asset-proposal").exists())
        (self.pack / "sidecar.json").unlink()
        animated = self.pack / "animated.gif"
        first = Image.new("RGB", (3, 3), "red")
        second = Image.new("RGB", (3, 3), "blue")
        first.save(animated, save_all=True, append_images=[second], format="GIF", duration=10)
        first.close()
        second.close()
        with self.assertRaises(rrv_runtime.RRVError):
            self.proposal()
        self.assertFalse((self.root / "asset-proposal").exists())

    def test_audio_is_probed_from_open_descriptor_and_records_zero_video_streams(self):
        slots = [{"id": "audio", "type": "audio", "required": True, "accepted_media": ["audio/mp4"]}]
        self.write_template(slots)
        (self.pack / "audio.bin").write_bytes(b"synthetic audio bytes")
        probe = {
            "streams": [{"codec_type": "audio"}],
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "1.25"},
        }
        with mock.patch.object(rrv_assets, "_run_ffprobe", return_value=probe) as runner:
            proposal = self.proposal_data(self.proposal())
        self.assertEqual(proposal["inventory"][0]["media_type"], "audio/mp4")
        self.assertEqual(proposal["inventory"][0]["facts"]["video_stream_count"], 0)
        self.assertIn("pipe:0", runner.call_args.args[0])
        self.assertNotIn(str(self.pack / "audio.bin"), runner.call_args.args[0])

    def test_audio_probe_rejects_non_audio_streams_and_webm_containers(self):
        template_path, _ = self.write_template(
            [{"id": "audio", "type": "audio", "required": True, "accepted_media": ["audio/mp4"]}]
        )
        (self.pack / "audio.bin").write_bytes(b"synthetic audio bytes")
        accepted_probe = {
            "streams": [{"codec_type": "audio"}],
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "1"},
        }
        for codec_type in ("subtitle", "data", "attachment", "unknown", "video"):
            probe = copy.deepcopy(accepted_probe)
            probe["streams"].append({"codec_type": codec_type})
            output_dir = f"extra-stream-{codec_type}"
            with self.subTest(codec_type=codec_type):
                with mock.patch.object(rrv_assets, "_run_ffprobe", return_value=probe):
                    with self.assertRaises(rrv_runtime.RRVError) as caught:
                        self.proposal(template_path, output_dir=output_dir)
                self.assertEqual(caught.exception.code, rrv_runtime.ERR_INVALID_ARGUMENT)
                self.assertFalse((self.root / output_dir).exists())

        webm_probe = {
            "streams": [{"codec_type": "audio"}],
            "format": {"format_name": "matroska,webm", "duration": "1"},
        }
        with mock.patch.object(rrv_assets, "_run_ffprobe", return_value=webm_probe):
            with self.assertRaises(rrv_runtime.RRVError) as caught:
                self.proposal(template_path, output_dir="webm-container")
        self.assertEqual(caught.exception.code, rrv_runtime.ERR_INVALID_ARGUMENT)
        self.assertFalse((self.root / "webm-container").exists())
        self.assertEqual(rrv_assets._audio_media_type("wav"), "audio/wav")
        self.assertEqual(rrv_assets._audio_media_type("mp3"), "audio/mpeg")
        self.assertEqual(rrv_assets._audio_media_type("mov,mp4,m4a,3gp,3g2,mj2"), "audio/mp4")
        self.assertEqual(rrv_assets._audio_media_type("matroska"), "audio/x-matroska")
        self.assertIsNone(rrv_assets._audio_media_type("matroska,webm"))
        self.assertIsNone(rrv_assets._audio_media_type("flac"))

        def ebml_header(doc_type):
            payload = b"\x42\x86\x81\x01\x42\x82" + bytes([0x80 + len(doc_type)]) + doc_type
            return b"\x1a\x45\xdf\xa3" + bytes([0x80 + len(payload)]) + payload

        self.assertEqual(
            rrv_assets._audio_media_type("matroska,webm", io.BytesIO(ebml_header(b"matroska"))),
            "audio/x-matroska",
        )
        self.assertIsNone(rrv_assets._audio_media_type("matroska,webm", io.BytesIO(ebml_header(b"webm"))))

    def test_freeze_accepts_explicit_ffprobe_and_timeout_without_recording_tool_path(self):
        slots = [
            {"id": "hero", "type": "image", "required": True, "accepted_media": ["image/png"]},
            {"id": "audio", "type": "audio", "required": True, "accepted_media": ["audio/mp4"]},
        ]
        self.write_template(slots)
        self.image("hero.png")
        (self.pack / "audio.bin").write_bytes(b"synthetic audio bytes")
        probe = {
            "streams": [{"codec_type": "audio"}],
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "1"},
        }
        with mock.patch.object(rrv_assets, "_run_ffprobe", return_value=probe) as runner:
            result = self.proposal(ffprobe="proposal-probe", timeout_seconds=7)
        review_path = self.write_review(self.approve(self.review_data(result)))
        with mock.patch.object(rrv_assets, "_run_ffprobe", return_value=probe) as runner:
            frozen = rrv_assets.freeze_assets(
                result["artifacts"]["proposal"]["path"],
                review_path,
                project_root=self.root,
                ffprobe="portable-freeze-probe",
                timeout_seconds=8,
            )
        self.assertEqual(runner.call_args.args[0][0], "portable-freeze-probe")
        self.assertTrue((self.root / frozen["artifacts"]["assets_manifest"]["path"]).is_file())
        proposal = self.proposal_data(result)
        report = json.loads((self.root / frozen["artifacts"]["freeze_report"]["path"]).read_text(encoding="utf-8"))
        self.assertNotIn("proposal-probe", json.dumps(proposal))
        self.assertNotIn("portable-freeze-probe", json.dumps(report))

    def test_review_processor_defaults_follow_slot_facts(self):
        slots = [
            {"id": "render", "type": "image", "required": True, "accepted_media": ["image/png"]},
            {"id": "identity", "type": "identity", "required": False, "accepted_media": ["image/png"]},
            {"id": "product", "type": "product-image", "required": False, "accepted_media": ["image/png"]},
            {"id": "background", "type": "background", "required": False, "accepted_media": ["image/png"]},
        ]
        template_path, document = self.write_template(slots)
        document["layers"][0]["source"]["representation"] = "render-ready"
        (self.root / template_path).write_text(json.dumps(document), encoding="utf-8")
        for name in ("render.png", "identity.png", "product.png", "background.png"):
            self.image(name)
        review = self.review_data(self.proposal(template_path))
        processors = {mapping["slot_id"]: mapping["processor"] for mapping in review["mappings"] if mapping["action"] == "use"}
        self.assertEqual(processors["render"], "approved-render-ready")
        self.assertEqual(processors["identity"], "identity-reference")
        self.assertEqual(processors["product"], "deterministic-tile")
        self.assertEqual(processors["background"], "direct")

    def test_scanned_snapshot_survives_source_path_change_and_closes_idempotently(self):
        self.write_template()
        source = self.image("hero.png", color=(240, 20, 30))
        original = source.read_bytes()
        stage = None
        scanned = []
        try:
            with rrv_assets._root_guard(self.root) as root_identity:
                with rrv_assets._asset_pack_guard(self.root, root_identity, "asset-pack") as (pack, pack_identity):
                    scanned, inventory = rrv_assets._scan_asset_pack(
                        root_identity,
                        pack,
                        pack_identity,
                        "asset-pack",
                        ffprobe="ignored",
                        timeout_seconds=1,
                    )
                    replacement = self.pack / "replacement.png"
                    Image.new("RGB", (9, 7), (10, 220, 60)).save(replacement, format="PNG")
                    try:
                        os.replace(replacement, source)
                    except OSError:
                        replacement.unlink(missing_ok=True)
                        source.write_bytes(b"path-replaced-after-scan")
                    stage = rrv_assets.rrv_propose._new_staging_directory(self.root, "snapshot-test")
                    contact = rrv_assets.rrv_propose._stage_path(self.root, stage, "contact.png")
                    candidates = rrv_assets._slot_candidates(template_document([{ "id": "hero", "type": "image", "required": True, "accepted_media": ["image/png"]}]), inventory)
                    rrv_assets._create_contact_sheet(self.root, stage, contact, scanned, inventory, candidates)
                    frozen = rrv_assets.rrv_propose._stage_path(self.root, stage, "asset-0001.png")
                    rrv_assets._copy_snapshot_asset(scanned[0], stage=stage, destination=frozen, expected_sha256=inventory[0]["sha256"])
                    self.assertEqual(frozen.read_bytes(), original)
                    self.assertFalse(scanned[0].closed)
        finally:
            rrv_assets._close_scanned_assets(scanned)
            rrv_assets._close_scanned_assets(scanned)
            self.assertTrue(all(asset.closed for asset in scanned))
            if stage is not None:
                rrv_assets.rrv_propose._cleanup_directory(self.root, stage)

    def test_propose_and_freeze_close_every_scanned_snapshot(self):
        self.write_template()
        self.image("hero.png")
        batches = []
        original_close = rrv_assets._close_scanned_assets

        def record_close(scanned):
            original_close(scanned)
            batches.append(list(scanned))

        with mock.patch.object(rrv_assets, "_close_scanned_assets", side_effect=record_close):
            result = self.proposal()
            review_path = self.write_review(self.approve(self.review_data(result)))
            rrv_assets.freeze_assets(
                result["artifacts"]["proposal"]["path"],
                review_path,
                project_root=self.root,
            )
        self.assertGreaterEqual(len(batches), 2)
        self.assertTrue(all(asset.closed for batch in batches for asset in batch))

    def test_nested_parent_reparse_and_guarded_snapshot_hook_fail_closed(self):
        self.image("hero.png")
        self.write_template(path="nested/template.ir.json")
        target = self.root / "nested"
        linked = self.root / "linked"
        raw_template = (target / "template.ir.json").read_bytes()
        (target / "template.ir.json").unlink()
        target.rmdir()
        linked.mkdir()
        (linked / "template.ir.json").write_bytes(raw_template)
        try:
            os.symlink(linked, target, target_is_directory=True)
        except (OSError, NotImplementedError):
            target.mkdir()
            (target / "template.ir.json").write_bytes(raw_template)
        else:
            with self.assertRaises(rrv_runtime.RRVError):
                self.proposal("nested/template.ir.json")
            target.unlink()
            target.mkdir()
            (target / "template.ir.json").write_bytes(raw_template)
        observed = []
        with mock.patch.object(rrv_assets, "_PROJECT_SNAPSHOT_HOOK", side_effect=lambda: observed.append(True)):
            self.proposal("nested/template.ir.json")
        self.assertTrue(observed)
        with mock.patch.object(
            rrv_assets,
            "_PROJECT_SNAPSHOT_HOOK",
            side_effect=rrv_runtime.RRVError(rrv_runtime.ERR_TOOL_EXECUTION, "synthetic identity mutation"),
        ):
            with self.assertRaises(rrv_runtime.RRVError):
                self.proposal("nested/template.ir.json", output_dir="hook-failure")
        self.assertFalse((self.root / "hook-failure").exists())

    def test_rights_gate_is_zero_touch(self):
        with mock.patch.object(rrv_assets, "_safe_project_root") as root_call:
            with self.assertRaises(rrv_runtime.RRVError) as caught:
                rrv_assets.propose_asset_pack(
                    "template.ir.json",
                    project_root=self.root,
                    asset_pack="asset-pack",
                    asset_pack_rights_confirmed=False,
                )
        self.assertEqual(caught.exception.code, rrv_runtime.ERR_INVALID_ARGUMENT)
        root_call.assert_not_called()

    def test_strict_json_nonfinite_duplicate_and_review_path_injection_are_rejected(self):
        self.write_template()
        self.image("hero.png")
        result = self.proposal()
        review_path = self.root / result["artifacts"]["review_template"]["path"]
        raw = review_path.read_text(encoding="utf-8")
        review_path.write_text(raw[:-2] + ',"decision":"approved"}', encoding="utf-8")
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_assets.freeze_assets(
                result["artifacts"]["proposal"]["path"],
                result["artifacts"]["review_template"]["path"],
                project_root=self.root,
            )
        self.assertTrue(rrv_assets.validate_asset_proposal_data({"schema_version": "0.5.0", "inventory": [math.nan]}))
        review = self.approve(self.review_data(result))
        review["mappings"][0]["path"] = "new/path.png"
        self.assertTrue(rrv_assets.validate_asset_review_data(review))

    def test_pending_rejected_missing_confirmation_required_omit_and_drift_do_not_publish(self):
        template_path, _ = self.write_template()
        self.image("hero.png")
        result = self.proposal(template_path)
        base = self.review_data(result)
        pending_path = self.write_review(base, "asset-proposal/pending.json")
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_assets.freeze_assets(result["artifacts"]["proposal"]["path"], pending_path, project_root=self.root)
        rejected = self.approve(base)
        rejected["decision"] = "rejected"
        rejected_path = self.write_review(rejected, "asset-proposal/rejected.json")
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_assets.freeze_assets(result["artifacts"]["proposal"]["path"], rejected_path, project_root=self.root)
        incomplete = self.approve(base)
        use = next(mapping for mapping in incomplete["mappings"] if mapping["action"] == "use")
        use["rights_confirmed"] = False
        incomplete_path = self.write_review(incomplete, "asset-proposal/incomplete.json")
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_assets.freeze_assets(result["artifacts"]["proposal"]["path"], incomplete_path, project_root=self.root)
        omitted_required = self.approve(base)
        hero = next(mapping for mapping in omitted_required["mappings"] if mapping["slot_id"] == "hero")
        omitted_required["mappings"].remove(hero)
        omitted_required["mappings"].append({"slot_id": "hero", "action": "omit", "omit_confirmed": True})
        omitted_path = self.write_review(omitted_required, "asset-proposal/omitted.json")
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_assets.freeze_assets(result["artifacts"]["proposal"]["path"], omitted_path, project_root=self.root)
        approved = self.approve(base)
        approved_path = self.write_review(approved, "asset-proposal/approved.json")
        (self.root / template_path).write_text(json.dumps(template_document([{ "id": "hero", "type": "image", "required": True, "accepted_media": ["image/png"]}]), indent=2), encoding="utf-8")
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_assets.freeze_assets(result["artifacts"]["proposal"]["path"], approved_path, project_root=self.root)
        self.assertFalse((self.root / "frozen-assets").exists())

    def test_inventory_drift_no_overwrite_and_atomic_failure(self):
        self.write_template()
        source = self.image("hero.png")
        result = self.proposal()
        review = self.approve(self.review_data(result))
        review_path = self.write_review(review)
        source.write_bytes(b"changed bytes")
        with mock.patch.object(rrv_assets, "_run_ffprobe", return_value=None):
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_assets.freeze_assets(result["artifacts"]["proposal"]["path"], review_path, project_root=self.root)
        self.assertFalse((self.root / "frozen-assets").exists())
        with self.assertRaises(rrv_runtime.RRVError) as caught:
            self.proposal(output_dir="asset-proposal")
        self.assertEqual(caught.exception.code, rrv_runtime.ERR_OUTPUT_EXISTS)
        (self.root / "asset-proposal").rename(self.root / "previous-proposal")
        with mock.patch.object(rrv_assets, "_create_contact_sheet", side_effect=rrv_runtime.RRVError(rrv_runtime.ERR_TOOL_EXECUTION, "synthetic")):
            with self.assertRaises(rrv_runtime.RRVError):
                self.proposal()
        self.assertFalse((self.root / "asset-proposal").exists())

    def test_symlink_and_hardlink_are_rejected_when_supported(self):
        self.write_template()
        source = self.image("hero.png")
        link = self.pack / "link.png"
        try:
            os.symlink(source, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable on this platform")
        with self.assertRaises(rrv_runtime.RRVError):
            self.proposal()
        link.unlink()
        hardlink = self.pack / "hardlink.png"
        try:
            os.link(source, hardlink)
        except (OSError, NotImplementedError):
            self.skipTest("hardlink creation is unavailable on this platform")
        with self.assertRaises(rrv_runtime.RRVError):
            self.proposal()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
