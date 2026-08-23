import contextlib
import copy
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "skills" / "reference-video-rebuilder"
SCRIPTS = SKILL_ROOT / "scripts"
TEMPLATE_PATH = SKILL_ROOT / "assets" / "project-template" / "template.ir.example.json"
ASSETS_PATH = SKILL_ROOT / "assets" / "project-template" / "assets.example.json"
COMPILER_PLAN_PATH = SKILL_ROOT / "assets" / "project-template" / "compiler.plan.example.json"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import rrv_runtime  # noqa: E402
import rrv_propose  # noqa: E402
import rrv_assets  # noqa: E402
import video_remix  # noqa: E402


class PublicCliIntegrationTests(unittest.TestCase):
    def _render_args(self, root: Path, template: Path, manifest: Path, **overrides):
        values = {
            "template": template,
            "manifest": manifest,
            "project_root": root,
            "frame_directory": Path("render") / "master-frames",
            "debug_bounds": False,
            "summary": None,
            "ffmpeg": None,
            "ffprobe": None,
            "timeout": 30.0,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def _compile_args(self, root: Path, source: Path, plan: Path, **overrides):
        values = {
            "source": source,
            "plan": plan,
            "project_root": root,
            "output_dir": Path("template-compile"),
            "ffmpeg": None,
            "ffprobe": None,
            "timeout": 120.0,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def _proposal_packet(self):
        plan = json.loads(COMPILER_PLAN_PATH.read_text(encoding="utf-8"))
        return {
            "schema_version": "0.4.0",
            "template_id": plan["template_id"],
            "family": "fixed-subject-carousel",
            "privacy": "local-only",
            "review_required": True,
            "source_fingerprint": {
                "sha256": "a" * 64,
                "width": 576,
                "height": 1280,
                "frame_count": 347,
                "fps": 30.0,
                "has_audio": True,
            },
            "candidate_plan": plan,
            "confidence": {
                "overall": 0.5,
                "source_rect": 0.5,
                "carousel_boundary": 0.5,
                "slot_count": 0.5,
                "timing": 0.5,
                "carousel_layout": 0.5,
                "background_color": 0.5,
            },
            "candidates": {
                "carousel_boundaries": [{"y": 200, "score": 0.5, "method": "local"}],
                "slot_counts": [{"value": 12, "score": 0.5, "method": "local"}],
                "switch_frames": [{"frame": 0, "score": 1.0, "prominence": 1.0}],
            },
            "evidence": {
                "representative_frames": [0, 100],
                "artifacts": {
                    "overview_contact_sheet": {"path": "plan-proposal/overview.png", "sha256": "b" * 64},
                    "geometry_preview": {"path": "plan-proposal/geometry.png", "sha256": "c" * 64},
                    "timing_profile": {"path": "plan-proposal/timing.json", "sha256": "d" * 64},
                },
            },
            "limitations": ["Human review is required."],
        }

    def _review_packet(self, proposal=None):
        proposal = proposal or self._proposal_packet()
        return {
            "schema_version": "0.4.0",
            "proposal_sha256": "e" * 64,
            "decision": "pending",
            "reviewer_confirmed": False,
            "confirmations": {
                "family": False,
                "geometry": False,
                "slot_count": False,
                "timing": False,
                "carousel": False,
                "background": False,
                "audio": False,
                "authorization": False,
            },
            "approved_plan": copy.deepcopy(proposal["candidate_plan"]),
            "notes": "Awaiting review.",
        }

    def _write_approved_freeze_packets(self, root: Path) -> tuple[Path, Path, Path]:
        """Write one fully valid packet pair and its hash-bound evidence."""

        packets = root / "packets"
        packets.mkdir()
        proposal = self._proposal_packet()
        for name, filename, content in (
            ("overview_contact_sheet", "overview.bin", b"overview"),
            ("geometry_preview", "geometry.bin", b"geometry"),
            ("timing_profile", "timing.bin", b"timing"),
        ):
            evidence_path = packets / filename
            evidence_path.write_bytes(content)
            proposal["evidence"]["artifacts"][name] = {
                "path": f"packets/{filename}",
                "sha256": hashlib.sha256(content).hexdigest(),
            }

        proposal_path = packets / "proposal.json"
        proposal_bytes = (rrv_runtime.stable_json_dumps(proposal) + "\n").encode("utf-8")
        proposal_path.write_bytes(proposal_bytes)

        review = self._review_packet(proposal)
        review["proposal_sha256"] = hashlib.sha256(proposal_bytes).hexdigest()
        review["decision"] = "approved"
        review["reviewer_confirmed"] = True
        review["confirmations"] = {name: True for name in review["confirmations"]}
        review_path = packets / "review.json"
        review_path.write_text(rrv_runtime.stable_json_dumps(review) + "\n", encoding="utf-8")
        return packets, proposal_path, review_path

    def _propose_args(self, root: Path, reference: Path, **overrides):
        values = {
            "reference": reference,
            "project_root": root,
            "output_dir": Path("plan-proposal"),
            "template_id": "authorized-gold-carousel",
            "slot_count_hint": None,
            "reference_rights_confirmed": True,
            "audio_rights_confirmed": True,
            "audio_mode": "preserve",
            "output_profiles": None,
            "analysis_width": 96,
            "max_evidence_frames": 24,
            "ffmpeg": None,
            "ffprobe": None,
            "timeout": 120.0,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def _asset_proposal_packet(self):
        return {
            "schema_version": "0.5.0",
            "privacy_profile": "local-only",
            "analysis_rights_confirmed": True,
            "review_required": True,
            "template_path": "template.ir.json",
            "template_sha256": "a" * 64,
            "template_id": "asset-pack-test",
            "asset_pack": "asset-pack",
            "scanner_policy_version": "0.5.0",
            "inventory": [],
            "inventory_sha256": hashlib.sha256(b"[]").hexdigest(),
            "slot_candidates": [],
            "evidence": {
                "asset_contact_sheet": {
                    "path": "asset-proposal/asset-contact-sheet.png",
                    "sha256": "b" * 64,
                }
            },
        }

    def _asset_review_packet(self):
        return {
            "schema_version": "0.5.0",
            "proposal_sha256": "a" * 64,
            "decision": "pending",
            "contact_sheet_reviewed": False,
            "local_only_confirmed": False,
            "mappings": [],
        }

    def _propose_assets_args(self, root: Path, **overrides):
        values = {
            "template": Path("template.ir.json"),
            "project_root": root,
            "asset_pack": Path("asset-pack"),
            "asset_pack_rights_confirmed": True,
            "output_dir": Path("asset-proposal"),
            "ffprobe": Path("ffprobe"),
            "timeout": 60.0,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def _freeze_assets_args(self, root: Path, **overrides):
        values = {
            "proposal": Path("asset-proposal/asset-pack-proposal.json"),
            "review": Path("asset-proposal/approved-review.json"),
            "project_root": root,
            "output_dir": Path("frozen-assets"),
            "ffprobe": Path("ffprobe"),
            "timeout": 60.0,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def _prepare_generation_args(self, root: Path, **overrides):
        values = {
            "template": Path("template.ir.json"),
            "request": Path("generation-request.json"),
            "project_root": root,
            "reference_pack": Path("reference-pack"),
            "generation_rights_confirmed": True,
            "output_dir": Path("generation-plan"),
            "ffprobe": Path("ffprobe"),
            "timeout": 60.0,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def _propose_generation_results_args(self, root: Path, **overrides):
        values = {
            "plan": Path("generation-plan/generation-plan.json"),
            "plan_review": Path("generation-plan/approved-review.json"),
            "project_root": root,
            "result_pack": Path("generated-results"),
            "generation_results_rights_confirmed": True,
            "output_dir": Path("generation-results-proposal"),
            "ffprobe": Path("ffprobe"),
            "timeout": 60.0,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def _assemble_generation_pack_args(self, root: Path, **overrides):
        values = {
            "plan": Path("generation-plan/generation-plan.json"),
            "plan_review": Path("generation-plan/approved-review.json"),
            "results_proposal": Path("generation-results-proposal/results-proposal.json"),
            "results_review": Path("generation-results-proposal/approved-review.json"),
            "project_root": root,
            "output_dir": Path("generation-asset-pack"),
            "ffprobe": Path("ffprobe"),
            "timeout": 60.0,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_render_validates_template_and_assets_before_loading_renderer_or_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "template.json"
            manifest = root / "assets.json"
            template.write_text("{}", encoding="utf-8")
            manifest.write_text("{}", encoding="utf-8")
            args = self._render_args(root, template, manifest)
            with mock.patch.object(video_remix, "validate_template_data", return_value=["template invalid"]), mock.patch.object(
                video_remix, "validate_assets_data", return_value=["asset invalid"]
            ), mock.patch.object(video_remix, "_render_module", side_effect=AssertionError("renderer must not load")):
                payload, status = video_remix.run_render(args)
            self.assertEqual(status, 2)
            self.assertEqual(payload["status"], "fail")
            self.assertEqual(payload["errors"], ["$: validation.invalid"])
            self.assertFalse((root / "render").exists())

    def test_render_rejects_unresolved_template_review_before_asset_or_renderer_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "template.json"
            manifest = root / "assets.json"
            template_data = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
            template_data["support"]["review_required"] = True
            template.write_text(json.dumps(template_data), encoding="utf-8")
            manifest.write_text("{}", encoding="utf-8")
            args = self._render_args(root, template, manifest)
            with mock.patch.object(
                video_remix,
                "validate_assets_data",
                side_effect=AssertionError("assets must not be inspected before review resolves"),
            ), mock.patch.object(
                video_remix,
                "_render_module",
                side_effect=AssertionError("renderer must not load before review resolves"),
            ):
                payload, status = video_remix.run_render(args)
            self.assertEqual(status, 2)
            self.assertEqual(payload["status"], "fail")
            self.assertEqual(
                payload["errors"],
                ["$.support.review_required must be false before rendering"],
            )
            self.assertFalse((root / "render").exists())

    def test_render_strict_loader_rejects_duplicate_decision_fields_before_writes(self):
        """Duplicate review/rights gates are rejected before any renderer work."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = (
                (
                    '{"support":{"review_required":false,"review_required":true}}',
                    "{}",
                ),
                (
                    "{}",
                    '{"assets":[{"rights_confirmed":false,"rights_confirmed":true}]}',
                ),
            )
            for index, (template_text, manifest_text) in enumerate(cases):
                with self.subTest(index=index):
                    template = root / f"template-{index}.json"
                    manifest = root / f"assets-{index}.json"
                    template.write_text(template_text, encoding="utf-8")
                    manifest.write_text(manifest_text, encoding="utf-8")
                    args = self._render_args(root, template, manifest)
                    with mock.patch.object(
                        video_remix,
                        "_render_module",
                        side_effect=AssertionError("renderer must not load for duplicate JSON"),
                    ):
                        payload, status = video_remix.run_render(args)
                    self.assertEqual(status, 2)
                    self.assertEqual(payload, {"status": "fail", "errors": ["$: json.duplicate_key"]})
            self.assertFalse((root / "render").exists())

    def test_render_validation_errors_redact_input_values_and_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "template.json"
            manifest = root / "assets.json"
            template.write_text("{}", encoding="utf-8")
            manifest.write_text("{}", encoding="utf-8")
            for private_error in (
                "schema rejected C:/PRIVATE/source/template.json",
                "schema rejected private-source-name.png with invalid value secret-label",
            ):
                with self.subTest(private_error=private_error):
                    args = self._render_args(root, template, manifest)
                    with mock.patch.object(
                        video_remix,
                        "validate_template_data",
                        return_value=[private_error],
                    ), mock.patch.object(
                        video_remix,
                        "_render_module",
                        side_effect=AssertionError("renderer must not load for invalid input"),
                    ):
                        payload, status = video_remix.run_render(args)
                    self.assertEqual(status, 2)
                    self.assertEqual(payload, {"status": "fail", "errors": ["$: validation.invalid"]})
                    self.assertNotIn("PRIVATE", json.dumps(payload))
                    self.assertNotIn("private-source-name.png", json.dumps(payload))
                    self.assertNotIn("secret-label", json.dumps(payload))
            self.assertFalse((root / "render").exists())

    def test_render_provenance_uses_original_strict_input_bytes_after_replacement(self):
        """Renderer-side path replacement cannot alter consumed input hashes."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "template.json"
            manifest = root / "assets.json"
            template_bytes = (
                b'{\n  "source": {"source_sha256": "' + b"a" * 64 + b'"},\n  "name": "original"\n}\n'
            )
            manifest_bytes = b'{ "schema_version": "0.1.0", "assets": [], "name": "original" }\n'
            template.write_bytes(template_bytes)
            manifest.write_bytes(manifest_bytes)
            expected_template_sha256 = hashlib.sha256(template_bytes).hexdigest()
            expected_manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
            args = self._render_args(root, template, manifest)
            tools = rrv_runtime.RuntimeTools(
                ffmpeg=rrv_runtime.ToolInfo("ffmpeg", "ffmpeg", "PATH", "ffmpeg 7"),
                ffprobe=rrv_runtime.ToolInfo("ffprobe", None, None, None),
            )

            def replace_inputs(*_args, **_kwargs):
                template.write_bytes(b'{"name":"replacement"}')
                manifest.write_bytes(b'{"name":"replacement"}')
                return {"assets": []}

            render_module = SimpleNamespace(
                RenderError=RuntimeError,
                render_project=mock.Mock(side_effect=replace_inputs),
            )
            with mock.patch.object(video_remix, "validate_template_data", return_value=[]), mock.patch.object(
                video_remix, "validate_assets_data", return_value=[]
            ), mock.patch.object(rrv_runtime, "discover_tools", return_value=tools), mock.patch.object(
                video_remix, "_render_module", return_value=render_module
            ), mock.patch.object(video_remix, "_qa_module", return_value=SimpleNamespace()), mock.patch.object(
                video_remix, "_render_qa", return_value={"passed": True, "outputs": []}
            ), mock.patch.object(
                video_remix,
                "sha256_file",
                side_effect=AssertionError("render provenance must not re-read input paths"),
            ):
                payload, status = video_remix.run_render(args)

            self.assertEqual(status, 0)
            hashes = payload["result"]["hashes"]
            self.assertEqual(hashes["template_sha256"], expected_template_sha256)
            self.assertEqual(hashes["manifest_sha256"], expected_manifest_sha256)
            self.assertNotEqual(hashes["template_sha256"], hashlib.sha256(template.read_bytes()).hexdigest())
            self.assertNotEqual(hashes["manifest_sha256"], hashlib.sha256(manifest.read_bytes()).hexdigest())

    def test_render_errors_are_publicly_redacted(self):
        class PrivateRenderError(RuntimeError):
            pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "template.json"
            manifest = root / "assets.json"
            template.write_text("{}", encoding="utf-8")
            manifest.write_text("{}", encoding="utf-8")
            args = self._render_args(root, template, manifest)
            tools = rrv_runtime.RuntimeTools(
                ffmpeg=rrv_runtime.ToolInfo("ffmpeg", "ffmpeg", "PATH", "ffmpeg 7"),
                ffprobe=rrv_runtime.ToolInfo("ffprobe", None, None, None),
            )
            render_module = SimpleNamespace(
                RenderError=PrivateRenderError,
                render_project=mock.Mock(
                    side_effect=PrivateRenderError("C:/PRIVATE/tools/ffmpeg.exe stderr secret")
                ),
            )
            with mock.patch.object(
                video_remix,
                "_require_render_inputs",
                return_value=(root, {}, {}, []),
            ), mock.patch.object(rrv_runtime, "discover_tools", return_value=tools), mock.patch.object(
                video_remix, "_render_module", return_value=render_module
            ), mock.patch.object(video_remix, "_qa_module", return_value=SimpleNamespace()):
                with self.assertRaises(rrv_runtime.RRVError) as raised:
                    video_remix.run_render(args)
            self.assertEqual(raised.exception.code, rrv_runtime.ERR_TOOL_EXECUTION)
            self.assertEqual(raised.exception.details, {})
            self.assertNotIn("PRIVATE", str(raised.exception))

            output = io.StringIO()
            with mock.patch.object(video_remix, "run_render", side_effect=raised.exception), contextlib.redirect_stdout(output):
                status = video_remix.main(
                    ["render", str(template), str(manifest), "--project-root", str(root), "--json"]
                )
            self.assertEqual(status, 2)
            rendered = output.getvalue()
            self.assertNotIn("PRIVATE", rendered)
            self.assertNotIn("stderr secret", rendered)
            self.assertNotIn('"reason"', rendered)

    def test_render_qa_failure_returns_one_and_keeps_complete_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template_path = root / "template.json"
            manifest_path = root / "assets.json"
            template_path.write_text("{}", encoding="utf-8")
            manifest_path.write_text('{"assets": []}', encoding="utf-8")
            template = {"source": {"source_sha256": "a" * 64}}
            manifest = {"assets": []}
            args = self._render_args(root, template_path, manifest_path, summary=Path("run-summary.json"))
            tools = rrv_runtime.RuntimeTools(
                ffmpeg=rrv_runtime.ToolInfo(
                    "ffmpeg",
                    "C:/Users/example/private-tools/ffmpeg.exe",
                    "explicit",
                    "ffmpeg version 7.1",
                ),
                ffprobe=rrv_runtime.ToolInfo(
                    "ffprobe",
                    "C:/Users/example/private-tools/ffprobe.exe",
                    "explicit",
                    "ffprobe version 7.1",
                ),
            )
            renderer_summary = {
                "status": "ok",
                "master": {"frame_count": 1, "fps": 30},
                "outputs": [
                    {
                        "id": "vertical-720",
                        "path": "output.mp4",
                        "width": 720,
                        "height": 1280,
                        "audio_muxed": True,
                    }
                ],
            }
            render_module = SimpleNamespace(RenderError=RuntimeError, render_project=mock.Mock(return_value=renderer_summary))
            qa_module = SimpleNamespace(
                verify_delivery=mock.Mock(return_value={"status": "fail", "passed": False, "checks": []})
            )
            with mock.patch.object(video_remix, "_require_render_inputs", return_value=(root, template, manifest, [])), mock.patch.object(
                rrv_runtime, "discover_tools", return_value=tools
            ) as discover_tools, mock.patch.object(video_remix, "_render_module", return_value=render_module), mock.patch.object(
                video_remix, "_qa_module", return_value=qa_module
            ):
                payload, status = video_remix.run_render(args)
            self.assertEqual(status, 1)
            self.assertEqual(payload["status"], "ok")
            result = payload["result"]
            self.assertEqual(result["status"], "fail")
            self.assertFalse(result["qa"]["passed"])
            qa_module.verify_delivery.assert_called_once()
            discover_tools.assert_called_once_with(
                ffmpeg=None,
                ffprobe=None,
                probe_versions=True,
            )
            written_summary = json.loads((root / "run-summary.json").read_text(encoding="utf-8"))
            runtime_provenance = written_summary["provenance"]["runtime"]
            self.assertEqual(runtime_provenance["ffmpeg"]["source"], "explicit")
            self.assertEqual(runtime_provenance["ffmpeg"]["version"], "ffmpeg version 7.1")
            self.assertEqual(runtime_provenance["ffprobe"]["version"], "ffprobe version 7.1")
            self.assertIsInstance(runtime_provenance["python_version"], str)
            self.assertIsInstance(runtime_provenance["pillow"]["version"], str)
            self.assertIsInstance(runtime_provenance["jsonschema"]["version"], str)
            serialized_summary = json.dumps(written_summary)
            self.assertNotIn("C:/Users/example/private-tools", serialized_summary)
            self.assertNotIn("path", runtime_provenance["ffmpeg"])
            self.assertNotIn("path", runtime_provenance["ffprobe"])

    def test_summary_path_stays_in_root_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(rrv_runtime.RRVError) as outside:
                video_remix._write_summary(rrv_runtime, root, Path("..") / "escape.json", {"status": "pass"})
            self.assertEqual(outside.exception.code, rrv_runtime.ERR_OUTPUT_PATH_OUTSIDE_ROOT)

            summary = root / "summary.json"
            summary.write_text("original", encoding="utf-8")
            with self.assertRaises(rrv_runtime.RRVError) as exists:
                video_remix._write_summary(rrv_runtime, root, Path("summary.json"), {"status": "pass"})
            self.assertEqual(exists.exception.code, rrv_runtime.ERR_OUTPUT_EXISTS)
            self.assertEqual(summary.read_text(encoding="utf-8"), "original")

    def test_render_preflights_summary_escape_before_renderer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "template.json"
            manifest = root / "assets.json"
            template.write_text("{}", encoding="utf-8")
            manifest.write_text("{}", encoding="utf-8")
            args = self._render_args(root, template, manifest, summary=Path("..") / "escape.json")
            with mock.patch.object(video_remix, "validate_template_data", return_value=[]), mock.patch.object(
                video_remix, "validate_assets_data", return_value=[]
            ):
                with self.assertRaises(rrv_runtime.RRVError) as raised:
                    video_remix._require_render_inputs(args, rrv_runtime)
            self.assertEqual(raised.exception.code, rrv_runtime.ERR_OUTPUT_PATH_OUTSIDE_ROOT)
            self.assertFalse((root.parent / "escape.json").exists())

    def test_doctor_uses_explicit_media_tool_paths_for_capabilities(self):
        tools = rrv_runtime.RuntimeTools(
            ffmpeg=rrv_runtime.ToolInfo("ffmpeg", "C:/portable/ffmpeg.exe", "explicit", "ffmpeg 7"),
            ffprobe=rrv_runtime.ToolInfo("ffprobe", "C:/portable/ffprobe.exe", "explicit", "ffprobe 7"),
        )
        runtime = SimpleNamespace(discover_tools=mock.Mock(return_value=tools))
        with mock.patch.object(video_remix, "_runtime_module", return_value=runtime):
            payload = video_remix.doctor_payload(ffmpeg=Path("C:/portable/ffmpeg.exe"), ffprobe=Path("C:/portable/ffprobe.exe"))
        runtime.discover_tools.assert_called_once()
        self.assertEqual(payload["stage"], "alpha")
        self.assertTrue(payload["capabilities"]["media_probe"])
        self.assertTrue(payload["capabilities"]["reference_survey"])
        self.assertTrue(payload["capabilities"]["reference_analysis"])
        self.assertFalse(payload["capabilities"]["semantic_slot_analysis"])
        self.assertTrue(payload["capabilities"]["template_compilation"])
        self.assertTrue(payload["capabilities"]["timeline_render"])
        self.assertTrue(payload["capabilities"]["video_qa"])
        self.assertEqual(payload["runtime"]["ffmpeg"]["source"], "explicit")

    def test_doctor_compiler_capability_requires_every_exact_prerequisite(self):
        tools = rrv_runtime.RuntimeTools(
            ffmpeg=rrv_runtime.ToolInfo("ffmpeg", "fake-ffmpeg", "explicit", "ffmpeg 7"),
            ffprobe=rrv_runtime.ToolInfo("ffprobe", None, None, None),
        )
        runtime = SimpleNamespace(discover_tools=mock.Mock(return_value=tools))
        compiler = SimpleNamespace(compile_reference=mock.Mock())
        with mock.patch.object(video_remix, "_runtime_module", return_value=runtime), mock.patch.object(
            video_remix, "_pillow_available", return_value=True
        ), mock.patch.object(video_remix, "_compile_module", return_value=compiler):
            payload = video_remix.doctor_payload()
        capabilities = payload["capabilities"]
        self.assertTrue(capabilities["compiler_plan_validation"])
        self.assertFalse(capabilities["reference_analysis"])
        self.assertFalse(capabilities["template_compilation"])
        self.assertFalse(capabilities["semantic_slot_analysis"])

    def test_doctor_does_not_claim_compilation_for_nonexecutable_explicit_tools(self):
        compiler = SimpleNamespace(compile_reference=mock.Mock())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_ffmpeg = root / "ffmpeg.exe"
            fake_ffprobe = root / "ffprobe.exe"
            fake_ffmpeg.write_text("not an executable", encoding="utf-8")
            fake_ffprobe.write_text("not an executable", encoding="utf-8")
            with mock.patch.object(video_remix, "_pillow_available", return_value=True), mock.patch.object(
                video_remix, "_compile_module", return_value=compiler
            ):
                payload = video_remix.doctor_payload(
                    ffmpeg=fake_ffmpeg,
                    ffprobe=fake_ffprobe,
                )
        self.assertIsNone(payload["runtime"]["ffmpeg"]["version"])
        self.assertIsNone(payload["runtime"]["ffprobe"]["version"])
        self.assertFalse(payload["capabilities"]["media_probe"])
        self.assertFalse(payload["capabilities"]["reference_survey"])
        self.assertFalse(payload["capabilities"]["reference_analysis"])
        self.assertFalse(payload["capabilities"]["template_compilation"])
        self.assertFalse(payload["capabilities"]["timeline_render"])
        self.assertFalse(payload["capabilities"]["video_qa"])

    def test_missing_compiler_schema_disables_compiler_capabilities(self):
        tools = rrv_runtime.RuntimeTools(
            ffmpeg=rrv_runtime.ToolInfo("ffmpeg", "fake-ffmpeg", "explicit", "ffmpeg 7"),
            ffprobe=rrv_runtime.ToolInfo("ffprobe", "fake-ffprobe", "explicit", "ffprobe 7"),
        )
        runtime = SimpleNamespace(discover_tools=mock.Mock(return_value=tools))
        compiler = SimpleNamespace(compile_reference=mock.Mock())
        with tempfile.TemporaryDirectory() as directory:
            missing_schema = Path(directory) / "missing.schema.json"
            with mock.patch.object(video_remix, "COMPILER_PLAN_SCHEMA_PATH", missing_schema), mock.patch.object(
                video_remix, "_runtime_module", return_value=runtime
            ), mock.patch.object(video_remix, "_pillow_available", return_value=True), mock.patch.object(
                video_remix, "_compile_module", return_value=compiler
            ):
                payload = video_remix.doctor_payload()
                errors = video_remix.validate_compiler_plan_data({})
        capabilities = payload["capabilities"]
        self.assertFalse(capabilities["compiler_plan_validation"])
        self.assertFalse(capabilities["reference_analysis"])
        self.assertFalse(capabilities["template_compilation"])
        self.assertTrue(any("Compiler Plan JSON Schema is unavailable" in error for error in errors))

    def test_invalid_compiler_plan_never_loads_compiler_or_creates_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "reference.mp4"
            plan = root / "compiler-plan.json"
            plan.write_text("{}", encoding="utf-8")
            args = self._compile_args(root, source, plan)
            with mock.patch.object(
                video_remix, "_compile_module", side_effect=AssertionError("compiler must not load")
            ), mock.patch.object(
                video_remix, "_runtime_module", side_effect=AssertionError("runtime must not load")
            ):
                payload, status = video_remix.run_compile(args)
            self.assertEqual(status, 2)
            self.assertEqual(payload["status"], "fail")
            self.assertFalse((root / "template-compile").exists())
            self.assertFalse(list(root.glob(".template-compile.*")))

    def test_compile_exit_statuses_use_compact_review_result(self):
        tools = rrv_runtime.RuntimeTools(
            ffmpeg=rrv_runtime.ToolInfo("ffmpeg", "fake-ffmpeg", "explicit", "ffmpeg 7"),
            ffprobe=rrv_runtime.ToolInfo("ffprobe", "fake-ffprobe", "explicit", "ffprobe 7"),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "reference.mp4"
            plan = root / "compiler-plan.json"
            plan.write_text(COMPILER_PLAN_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            common_result = {
                "schema_version": "0.3.0",
                "template_id": "authorized-gold-carousel",
                "output_dir": "template-compile",
                "switch_frames": [10],
                "artifacts": {
                    "template_ir": {
                        "path": "template-compile/template.ir.json",
                        "template": {"must_not": "be returned"},
                        "tool_path": "C:/private-tools/ffmpeg.exe",
                    },
                    "center_frames": [
                        {
                            "path": "template-compile/frames/private-frame.png",
                            "sha256": "a" * 64,
                        }
                    ],
                    "source": {"path": "C:/private-source/reference.mp4"},
                },
                "template": {"must_not": "be returned"},
                "scores": {"must_not": "be returned"},
            }
            compiler = SimpleNamespace(compile_reference=mock.Mock())
            argv = [
                "compile",
                str(source),
                str(plan),
                "--project-root",
                str(root),
                "--json",
            ]
            with mock.patch.object(rrv_runtime, "discover_tools", return_value=tools), mock.patch.object(
                video_remix, "_runtime_module", return_value=rrv_runtime
            ), mock.patch.object(video_remix, "_compile_module", return_value=compiler):
                for review_required, expected_status in ((False, 0), (True, 1)):
                    with self.subTest(review_required=review_required):
                        compiler.compile_reference.reset_mock()
                        compiler.compile_reference.return_value = {
                            **common_result,
                            "review_required": review_required,
                        }
                        output = io.StringIO()
                        with contextlib.redirect_stdout(output):
                            status = video_remix.main(argv)
                        self.assertEqual(status, expected_status)
                        payload = json.loads(output.getvalue())
                        self.assertEqual(payload["status"], "ok")
                        self.assertEqual(payload["result"]["review_required"], review_required)
                        self.assertNotIn("template", payload["result"])
                        self.assertNotIn("scores", payload["result"])
                        self.assertEqual(
                            payload["result"]["artifacts"]["center_frame_count"], 1
                        )
                        self.assertNotIn("center_frames", payload["result"]["artifacts"])
                        self.assertNotIn("C:/private-tools", output.getvalue())
                        self.assertNotIn("C:/private-source", output.getvalue())
                        compiler.compile_reference.assert_called_once()
                        self.assertIs(
                            compiler.compile_reference.call_args.kwargs["template_validator"],
                            video_remix.validate_template_data,
                        )

                compiler.compile_reference.side_effect = rrv_runtime.RRVError(
                    rrv_runtime.ERR_CAPABILITY_UNAVAILABLE,
                    "reference compilation requires local FFmpeg",
                )
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    status = video_remix.main(argv)
            self.assertEqual(status, 2)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "error")
            self.assertEqual(payload["error"]["code"], rrv_runtime.ERR_CAPABILITY_UNAVAILABLE)

    def test_compile_rrv_error_exposes_only_safe_allowlisted_details(self):
        tools = rrv_runtime.RuntimeTools(
            ffmpeg=rrv_runtime.ToolInfo("ffmpeg", "fake-ffmpeg", "explicit", "ffmpeg 7"),
            ffprobe=rrv_runtime.ToolInfo("ffprobe", "fake-ffprobe", "explicit", "ffprobe 7"),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "reference.mp4"
            plan = root / "compiler-plan.json"
            plan.write_text(COMPILER_PLAN_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            compiler = SimpleNamespace(
                compile_reference=mock.Mock(
                    side_effect=rrv_runtime.RRVError(
                        rrv_runtime.ERR_TOOL_EXECUTION,
                        "ffmpeg failed while reading C:/private/source.mp4",
                        {
                            "backend": "ffprobe",
                            "capability": "reference_compile",
                            "cause_code": "tool_execution_failed",
                            "missing_tool": "ffmpeg",
                            "returncode": 17,
                            "timeout_seconds": 12.5,
                            "tool": "C:/private/bin/ffmpeg.exe",
                            "output": "private stderr C:/private/source.mp4",
                            "reason": "private failure C:/private/source.mp4",
                            "source_path": "C:/private/source.mp4",
                        },
                    )
                )
            )
            argv = [
                "compile",
                str(source),
                str(plan),
                "--project-root",
                str(root),
                "--json",
            ]
            with mock.patch.object(rrv_runtime, "discover_tools", return_value=tools), mock.patch.object(
                video_remix, "_runtime_module", return_value=rrv_runtime
            ), mock.patch.object(video_remix, "_compile_module", return_value=compiler):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    status = video_remix.main(argv)
            self.assertEqual(status, 2)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "error")
            self.assertEqual(payload["error"]["message"], "reference compilation failed")
            self.assertEqual(
                payload["error"]["details"],
                {
                    "backend": "ffprobe",
                    "capability": "reference_compile",
                    "cause_code": "tool_execution_failed",
                    "missing_tool": "ffmpeg",
                    "returncode": 17,
                    "timeout_seconds": 12.5,
                    "tool": "ffmpeg.exe",
                },
            )
            serialized = output.getvalue()
            self.assertNotIn("private", serialized)
            self.assertNotIn("stderr", serialized)
            self.assertNotIn("reason", serialized)

    def test_validate_compiler_plan_command(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = Path(directory) / "compiler-plan.json"
            plan.write_text(COMPILER_PLAN_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = video_remix.main(["validate-compiler-plan", str(plan), "--json"])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue()), {"status": "pass", "errors": []})

            plan.write_text("{}", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = video_remix.main(["validate-compiler-plan", str(plan), "--json"])
            self.assertEqual(status, 2)
            self.assertEqual(json.loads(output.getvalue())["status"], "fail")

    def test_legacy_validate_commands_remain_compatible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "template.json"
            manifest = root / "assets.json"
            template.write_text(TEMPLATE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            manifest.write_text(ASSETS_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = video_remix.main(["validate-template", str(template), "--json"])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "pass")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = video_remix.main(
                    ["validate-assets", str(template), str(manifest), "--allow-missing-files", "--json"]
                )
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "pass")

    def test_template_and_manifest_validation_strictly_redacts_hostile_json(self):
        """The public Template/Manifest commands never echo parser input."""

        secret = "C:/PRIVATE/project/secret-source.png"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid_template = root / "valid-template.json"
            valid_template.write_text(TEMPLATE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            template_duplicate = root / "template-duplicate.json"
            template_duplicate.write_text(
                '{"support":{"review_required":false,"review_required":"' + secret + '"}}',
                encoding="utf-8",
            )
            template_nonfinite = root / "template-nonfinite.json"
            template_nonfinite.write_text(
                '{"support":{"review_required":NaN},"source_path":"' + secret + '"}',
                encoding="utf-8",
            )
            template_invalid = root / "template-invalid.json"
            template_invalid.write_text('{"source_path":"' + secret + '",', encoding="utf-8")
            manifest_duplicate = root / "manifest-duplicate.json"
            manifest_duplicate.write_text(
                '{"assets":[{"rights_confirmed":false,"rights_confirmed":"' + secret + '"}]}',
                encoding="utf-8",
            )
            manifest_nonfinite = root / "manifest-nonfinite.json"
            manifest_nonfinite.write_text(
                '{"assets":[{"rights_confirmed":Infinity,"path":"' + secret + '"}]}',
                encoding="utf-8",
            )
            cases = (
                (["validate-template", str(template_duplicate), "--json"], "$: json.duplicate_key"),
                (["validate-template", str(template_nonfinite), "--json"], "$: json.finite_number"),
                (["validate-template", str(template_invalid), "--json"], "$: json.invalid"),
                (
                    [
                        "validate-assets",
                        str(valid_template),
                        str(manifest_duplicate),
                        "--allow-missing-files",
                        "--json",
                    ],
                    "$: json.duplicate_key",
                ),
                (
                    [
                        "validate-assets",
                        str(valid_template),
                        str(manifest_nonfinite),
                        "--allow-missing-files",
                        "--json",
                    ],
                    "$: json.finite_number",
                ),
            )
            with mock.patch.object(
                video_remix,
                "validate_assets_data",
                side_effect=AssertionError("strict JSON failures must not inspect assets"),
            ) as validate_assets:
                for command, expected_error in cases:
                    with self.subTest(command=command[0], expected_error=expected_error):
                        output = io.StringIO()
                        with contextlib.redirect_stdout(output):
                            status = video_remix.main(command)
                        self.assertEqual(status, 2)
                        self.assertEqual(
                            json.loads(output.getvalue()),
                            {"status": "fail", "errors": [expected_error]},
                        )
                        self.assertNotIn("PRIVATE", output.getvalue())
                        self.assertNotIn("secret-source.png", output.getvalue())
                validate_assets.assert_not_called()
            self.assertFalse((root / "render").exists())

    def test_v04_public_validators_reject_unknown_nonfinite_paths_and_nested_plans(self):
        proposal = self._proposal_packet()
        self.assertEqual([], video_remix.validate_proposal_data(proposal))

        unknown = copy.deepcopy(proposal)
        unknown["unexpected"] = True
        self.assertTrue(video_remix.validate_proposal_data(unknown))

        nonfinite = copy.deepcopy(proposal)
        nonfinite["confidence"]["timing"] = float("nan")
        self.assertTrue(
            any("schema.finite_number" in error for error in video_remix.validate_proposal_data(nonfinite))
        )

        escaping_path = copy.deepcopy(proposal)
        escaping_path["evidence"]["artifacts"]["geometry_preview"]["path"] = "..\\private.png"
        self.assertTrue(
            any("path" in error for error in video_remix.validate_proposal_data(escaping_path))
        )

        nested = copy.deepcopy(proposal)
        nested["candidate_plan"]["geometry"]["unexpected"] = True
        self.assertTrue(
            any("$.candidate_plan" in error for error in video_remix.validate_proposal_data(nested))
        )

        fingerprint_mismatch = copy.deepcopy(proposal)
        fingerprint_mismatch["candidate_plan"]["geometry"]["source_rect"]["width"] = 577
        self.assertTrue(
            any(
                "$.candidate_plan" in error and "source_rect" in error
                for error in video_remix.validate_proposal_data(fingerprint_mismatch)
            )
        )

        review = self._review_packet(proposal)
        review["approved_plan"]["carousel"]["unexpected"] = True
        self.assertTrue(
            any("$.approved_plan" in error for error in video_remix.validate_review_data(review))
        )

    def test_v04_validation_errors_do_not_reflect_private_packet_values(self):
        secret_path = "C:/PRIVATE/secret-reference.mp4"
        stderr_value = f"ffmpeg stderr: failed to open {secret_path}"
        proposal = self._proposal_packet()
        proposal["evidence"]["artifacts"]["overview_contact_sheet"]["path"] = secret_path
        proposal["candidate_plan"]["geometry"]["PRIVATE_source_label"] = stderr_value
        proposal["PRIVATE_source_label"] = stderr_value
        proposal["limitations"] = [stderr_value * 64]
        review = self._review_packet(proposal)
        review["proposal_sha256"] = secret_path
        review["PRIVATE_source_label"] = stderr_value
        review["notes"] = stderr_value * 256

        for packet, validator in (
            (proposal, video_remix.validate_proposal_data),
            (review, video_remix.validate_review_data),
        ):
            with self.subTest(validator=validator.__name__):
                errors = validator(packet)
                self.assertTrue(errors)
                serialized = json.dumps({"errors": errors})
                for private_text in ("PRIVATE", "secret-reference.mp4", "ffmpeg stderr", "C:/"):
                    self.assertNotIn(private_text, serialized)
                self.assertTrue(all(error.startswith("$") and ": " in error for error in errors))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proposal_path = root / "proposal.json"
            review_path = root / "review.json"
            proposal_path.write_text(json.dumps(proposal), encoding="utf-8")
            review_path.write_text(json.dumps(review), encoding="utf-8")
            for command, path in (("validate-proposal", proposal_path), ("validate-review", review_path)):
                with self.subTest(command=command):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        status = video_remix.main([command, str(path), "--json"])
                    self.assertEqual(status, 2)
                    self.assertNotIn("PRIVATE", output.getvalue())
                    self.assertNotIn("secret-reference.mp4", output.getvalue())
                    self.assertNotIn("ffmpeg stderr", output.getvalue())

            args = SimpleNamespace(
                proposal=Path("proposal.json"),
                review=Path("review.json"),
                project_root=root,
                output_dir=Path("frozen-plan"),
            )
            with mock.patch.object(video_remix, "_propose_module", return_value=rrv_propose) as core_loader:
                payload, status = video_remix.run_freeze_plan(args)
            self.assertEqual(status, 2)
            self.assertEqual(payload["status"], "error")
            self.assertEqual(payload["error"]["code"], rrv_runtime.ERR_INVALID_ARGUMENT)
            core_loader.assert_called_once_with()
            serialized = json.dumps(payload)
            self.assertNotIn("PRIVATE", serialized)
            self.assertNotIn("secret-reference.mp4", serialized)
            self.assertNotIn("ffmpeg stderr", serialized)

    def test_v04_packet_loading_rejects_nested_duplicate_keys_without_reflection(self):
        secret_path = "C:/PRIVATE/secret-reference.mp4"
        proposal_text = json.dumps(self._proposal_packet())
        proposal_text = proposal_text.replace(
            '"path": "plan-proposal/overview.png"',
            f'"path": "plan-proposal/overview.png", "path": "{secret_path}"',
            1,
        )
        review_text = json.dumps(self._review_packet())
        review_text = review_text.replace(
            '"decision": "pending"',
            '"decision": "pending", "decision": "approved"',
            1,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proposal_path = root / "proposal.json"
            review_path = root / "review.json"
            proposal_path.write_text(proposal_text, encoding="utf-8")
            review_path.write_text(review_text, encoding="utf-8")
            for command, path in (("validate-proposal", proposal_path), ("validate-review", review_path)):
                with self.subTest(command=command):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        status = video_remix.main([command, str(path), "--json"])
                    self.assertEqual(status, 2)
                    self.assertEqual(
                        json.loads(output.getvalue()),
                        {"status": "fail", "errors": ["$: json.duplicate_key"]},
                    )
                    self.assertNotIn("PRIVATE", output.getvalue())
                    self.assertNotIn("secret-reference.mp4", output.getvalue())

            args = SimpleNamespace(
                proposal=Path("proposal.json"),
                review=Path("review.json"),
                project_root=root,
                output_dir=Path("frozen-plan"),
            )
            with mock.patch.object(video_remix, "_propose_module", return_value=rrv_propose) as core_loader:
                payload, status = video_remix.run_freeze_plan(args)
            self.assertEqual(status, 2)
            self.assertEqual(payload["status"], "error")
            self.assertEqual(payload["error"]["code"], rrv_runtime.ERR_INVALID_ARGUMENT)
            core_loader.assert_called_once_with()
            self.assertNotIn("PRIVATE", json.dumps(payload))

    def test_v04_review_approval_condition_is_enforced(self):
        review = self._review_packet()
        review["decision"] = "approved"
        review["reviewer_confirmed"] = True
        review["confirmations"] = {name: True for name in review["confirmations"]}
        self.assertEqual([], video_remix.validate_review_data(review))

        review["confirmations"]["authorization"] = False
        self.assertTrue(video_remix.validate_review_data(review))

    def test_v04_validate_commands_do_not_import_proposal_core(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proposal_path = root / "proposal.json"
            review_path = root / "review.json"
            proposal_path.write_text(json.dumps(self._proposal_packet()), encoding="utf-8")
            review_path.write_text(json.dumps(self._review_packet()), encoding="utf-8")
            with mock.patch.object(
                video_remix,
                "_propose_module",
                side_effect=AssertionError("proposal core must stay lazy"),
            ):
                for command, path in (("validate-proposal", proposal_path), ("validate-review", review_path)):
                    with self.subTest(command=command):
                        output = io.StringIO()
                        with contextlib.redirect_stdout(output):
                            status = video_remix.main([command, str(path), "--json"])
                        self.assertEqual(status, 0)
                        self.assertEqual(json.loads(output.getvalue()), {"status": "pass", "errors": []})

    def test_v04_parser_exposes_bounded_propose_options(self):
        parser = video_remix.build_parser()
        args = parser.parse_args(
            [
                "propose",
                "reference.mp4",
                "--project-root",
                "project",
                "--template-id",
                "authorised-template",
                "--reference-rights-confirmed",
                "--slot-count-hint",
                "12",
                "--output-profile",
                "720x1280",
                "--output-profile",
                "1080x1920",
                "--analysis-width",
                "32",
                "--max-evidence-frames",
                "64",
                "--json",
            ]
        )
        self.assertEqual(args.command, "propose")
        self.assertEqual(args.output_profiles, ["720x1280", "1080x1920"])
        self.assertEqual(args.analysis_width, 32)
        self.assertEqual(args.max_evidence_frames, 64)
        with self.assertRaises(video_remix.CliArgumentError):
            parser.parse_args(
                [
                    "propose",
                    "reference.mp4",
                    "--project-root",
                    "project",
                    "--template-id",
                    "authorised-template",
                    "--reference-rights-confirmed",
                    "--analysis-width",
                    "31",
                ]
            )

    def test_v04_doctor_has_exact_proposal_and_freeze_gates(self):
        tools = rrv_runtime.RuntimeTools(
            ffmpeg=rrv_runtime.ToolInfo("ffmpeg", "fake-ffmpeg", "explicit", "ffmpeg 7"),
            ffprobe=rrv_runtime.ToolInfo("ffprobe", "fake-ffprobe", "explicit", "ffprobe 7"),
        )
        runtime = SimpleNamespace(discover_tools=mock.Mock(return_value=tools))
        proposal_core = SimpleNamespace(propose_reference=mock.Mock(), freeze_plan=mock.Mock())
        with mock.patch.object(video_remix, "_runtime_module", return_value=runtime), mock.patch.object(
            video_remix, "_pillow_available", return_value=True
        ), mock.patch.object(video_remix, "_propose_module", return_value=proposal_core):
            payload = video_remix.doctor_payload()
        capabilities = payload["capabilities"]
        self.assertTrue(capabilities["proposal_validation"])
        self.assertTrue(capabilities["review_validation"])
        self.assertTrue(capabilities["compiler_plan_proposal"])
        self.assertTrue(capabilities["compiler_plan_freeze"])

        no_probe = rrv_runtime.RuntimeTools(
            ffmpeg=tools.ffmpeg,
            ffprobe=rrv_runtime.ToolInfo("ffprobe", None, None, None),
        )
        with mock.patch.object(
            video_remix, "_runtime_module", return_value=SimpleNamespace(discover_tools=mock.Mock(return_value=no_probe))
        ), mock.patch.object(video_remix, "_pillow_available", return_value=True), mock.patch.object(
            video_remix, "_propose_module", return_value=proposal_core
        ):
            payload = video_remix.doctor_payload()
        self.assertFalse(payload["capabilities"]["compiler_plan_proposal"])
        self.assertTrue(payload["capabilities"]["compiler_plan_freeze"])

    def test_v04_propose_delegates_and_emits_only_compact_sanitized_result(self):
        result = {
            "schema_version": "0.4.0",
            "template_id": "authorized-gold-carousel",
            "output_dir": "plan-proposal",
            "review_required": True,
            "candidate_summary": {
                "slot_count": 12,
                "carousel_boundary_count": 1,
                "switch_frame_count": 11,
                "raw_score": 0.99,
            },
            "artifacts": {
                "proposal": {"path": "plan-proposal/compiler-plan-proposal.json", "sha256": "a" * 64},
                "review_template": {"path": "plan-proposal/review-decision.template.json", "sha256": "b" * 64},
                "overview_contact_sheet": {"path": "plan-proposal/overview.png", "sha256": "c" * 64},
                "geometry_preview": {"path": "plan-proposal/geometry.png", "sha256": "d" * 64},
                "timing_profile": {"path": "plan-proposal/timing.json", "sha256": "e" * 64},
                "source": {"path": "C:/private/reference.mp4"},
            },
            "candidate_plan": {"private": "must not appear"},
            "source_name": "private-reference.mp4",
            "tool_stderr": "private stderr",
        }
        core = SimpleNamespace(propose_reference=mock.Mock(return_value=result))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "private-reference.mp4"
            args = self._propose_args(root, reference)
            with mock.patch.object(video_remix, "_propose_module", return_value=core), mock.patch.object(
                video_remix, "_runtime_module", return_value=rrv_runtime
            ):
                payload, status = video_remix.run_propose(args)
        self.assertEqual(status, 0)
        self.assertEqual(payload["status"], "ok")
        compact = payload["result"]
        self.assertEqual(compact["candidate_summary"], {
            "slot_count": 12,
            "carousel_boundary_count": 1,
            "switch_frame_count": 11,
        })
        self.assertEqual(
            set(compact["artifacts"]),
            {
                "proposal",
                "review_template",
                "overview_contact_sheet",
                "geometry_preview",
                "timing_profile",
            },
        )
        serialized = json.dumps(payload)
        self.assertNotIn("private", serialized)
        self.assertNotIn("raw_score", serialized)
        core.propose_reference.assert_called_once_with(
            reference,
            project_root=root,
            template_id="authorized-gold-carousel",
            output_dir=Path("plan-proposal"),
            slot_count_hint=None,
            audio_mode="preserve",
            reference_rights_confirmed=True,
            audio_rights_confirmed=True,
            output_profiles=("720x1280", "1080x1920"),
            analysis_width=96,
            max_evidence_frames=24,
            ffmpeg=None,
            ffprobe=None,
            timeout_seconds=120.0,
        )

    def test_v04_freeze_delegates_without_cli_packet_reads_and_compacts_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proposal_path = root / "proposal.json"
            review_path = root / "review.json"
            proposal = self._proposal_packet()
            review = self._review_packet(proposal)
            review["decision"] = "approved"
            review["reviewer_confirmed"] = True
            review["confirmations"] = {name: True for name in review["confirmations"]}
            proposal_path.write_text(json.dumps(proposal), encoding="utf-8")
            review_path.write_text(json.dumps(review), encoding="utf-8")
            core = SimpleNamespace(
                freeze_plan=mock.Mock(
                    return_value={
                        "schema_version": "0.4.0",
                        "template_id": "authorized-gold-carousel",
                        "output_dir": "frozen-plan",
                        "artifacts": {
                            "compiler_plan": {"path": "frozen-plan/compiler-plan.json", "sha256": "a" * 64},
                            "freeze_report": {"path": "frozen-plan/freeze-report.json", "sha256": "b" * 64},
                            "reviewer_override_paths": ["C:/private/never-return"],
                        },
                    }
                )
            )
            args = SimpleNamespace(
                proposal=Path("proposal.json"),
                review=Path("review.json"),
                project_root=root,
                output_dir=Path("frozen-plan"),
            )
            with mock.patch.object(video_remix, "_propose_module", return_value=core), mock.patch.object(
                video_remix, "_runtime_module", return_value=rrv_runtime
            ), mock.patch.object(
                video_remix,
                "_load_contract_json",
                side_effect=AssertionError("freeze CLI must not read untrusted packets"),
            ) as packet_loader, mock.patch.object(
                video_remix,
                "validate_proposal_data",
                side_effect=AssertionError("freeze CLI must not validate untrusted proposals"),
            ) as proposal_validator, mock.patch.object(
                video_remix,
                "validate_review_data",
                side_effect=AssertionError("freeze CLI must not validate untrusted reviews"),
            ) as review_validator:
                payload, status = video_remix.run_freeze_plan(args)
            self.assertEqual(status, 0)
            self.assertNotIn("private", json.dumps(payload))
            packet_loader.assert_not_called()
            proposal_validator.assert_not_called()
            review_validator.assert_not_called()
            core.freeze_plan.assert_called_once_with(
                Path("proposal.json"),
                Path("review.json"),
                project_root=root,
                output_dir=Path("frozen-plan"),
            )

    def test_v04_freeze_pending_or_rejected_review_exits_two_from_core(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, proposal_path, review_path = self._write_approved_freeze_packets(root)
            for decision in ("pending", "rejected"):
                with self.subTest(decision=decision):
                    review = json.loads(review_path.read_text(encoding="utf-8"))
                    review["decision"] = decision
                    review["reviewer_confirmed"] = False
                    review["confirmations"] = {name: False for name in review["confirmations"]}
                    review_path.write_text(
                        rrv_runtime.stable_json_dumps(review) + "\n", encoding="utf-8"
                    )
                    output_dir = Path(f"frozen-{decision}")
                    args = SimpleNamespace(
                        proposal=proposal_path.relative_to(root),
                        review=review_path.relative_to(root),
                        project_root=root,
                        output_dir=output_dir,
                    )
                    with mock.patch.object(video_remix, "_propose_module", return_value=rrv_propose):
                        payload, status = video_remix.run_freeze_plan(args)
                    self.assertEqual(status, 2)
                    self.assertEqual(payload["status"], "error")
                    self.assertEqual(payload["error"]["code"], rrv_runtime.ERR_INVALID_ARGUMENT)
                    self.assertFalse((root / output_dir).exists())

    def test_v04_freeze_core_rejects_reparse_packet_parent_after_cli_delegation(self):
        """A packet-directory junction is rejected by core, not pre-read by CLI."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packets, proposal_path, review_path = self._write_approved_freeze_packets(root)
            args = SimpleNamespace(
                proposal=proposal_path.relative_to(root),
                review=review_path.relative_to(root),
                project_root=root,
                output_dir=Path("frozen-plan"),
            )
            original_lstat = os.lstat
            packets_stat = original_lstat(packets)

            def reparse_packet_directory(path):
                stat_result = original_lstat(path)
                # ``require_project_root`` may yield a Windows 8.3 spelling,
                # whereas this fixture was created through the long spelling.
                # Match the actual directory identity so the test continues to
                # exercise the reparse guard under either lexical alias.
                if (
                    stat_result.st_dev == packets_stat.st_dev
                    and stat_result.st_ino == packets_stat.st_ino
                ):
                    return SimpleNamespace(
                        st_mode=stat_result.st_mode,
                        st_dev=stat_result.st_dev,
                        st_ino=stat_result.st_ino,
                        st_file_attributes=rrv_propose._FILE_ATTRIBUTE_REPARSE_POINT,
                    )
                return stat_result

            with mock.patch.object(
                rrv_propose, "freeze_plan", wraps=rrv_propose.freeze_plan
            ) as core_freeze, mock.patch.object(
                video_remix, "_propose_module", return_value=rrv_propose
            ), mock.patch.object(
                video_remix,
                "_load_contract_json",
                side_effect=AssertionError("freeze CLI must not read untrusted packets"),
            ) as packet_loader, mock.patch.object(
                video_remix,
                "validate_proposal_data",
                side_effect=AssertionError("freeze CLI must not validate untrusted proposals"),
            ) as proposal_validator, mock.patch.object(
                video_remix,
                "validate_review_data",
                side_effect=AssertionError("freeze CLI must not validate untrusted reviews"),
            ) as review_validator, mock.patch.object(
                rrv_propose.os, "lstat", side_effect=reparse_packet_directory
            ):
                payload, status = video_remix.run_freeze_plan(args)

            self.assertEqual(status, 2)
            self.assertEqual(payload["status"], "error")
            self.assertEqual(payload["error"]["code"], rrv_runtime.ERR_TOOL_EXECUTION)
            self.assertFalse((root / "frozen-plan").exists())
            packet_loader.assert_not_called()
            proposal_validator.assert_not_called()
            review_validator.assert_not_called()
            core_freeze.assert_called_once_with(
                proposal_path.relative_to(root),
                review_path.relative_to(root),
                project_root=root,
                output_dir=Path("frozen-plan"),
            )

    def test_v06_parser_exposes_asset_and_generation_commands_defaults_and_version(self):
        parser = video_remix.build_parser()
        proposed = parser.parse_args(
            [
                "propose-assets",
                "template.ir.json",
                "--project-root",
                "project",
                "--asset-pack",
                "asset-pack",
                "--asset-pack-rights-confirmed",
                "--json",
            ]
        )
        self.assertEqual(proposed.command, "propose-assets")
        self.assertEqual(proposed.output_dir, Path("asset-proposal"))
        self.assertEqual(proposed.ffprobe, Path("ffprobe"))
        self.assertEqual(proposed.timeout, 60.0)
        self.assertTrue(proposed.asset_pack_rights_confirmed)

        frozen = parser.parse_args(
            [
                "freeze-assets",
                "asset-proposal/proposal.json",
                "asset-proposal/review.json",
                "--project-root",
                "project",
            ]
        )
        self.assertEqual(frozen.output_dir, Path("frozen-assets"))
        self.assertEqual(frozen.ffprobe, Path("ffprobe"))
        self.assertEqual(frozen.timeout, 60.0)

        generation_plan = parser.parse_args(
            [
                "prepare-generation",
                "template.ir.json",
                "generation-request.json",
                "--project-root",
                "project",
                "--reference-pack",
                "reference-pack",
                "--generation-rights-confirmed",
                "--json",
            ]
        )
        self.assertEqual(generation_plan.command, "prepare-generation")
        self.assertEqual(generation_plan.output_dir, Path("generation-plan"))
        self.assertEqual(generation_plan.ffprobe, Path("ffprobe"))
        self.assertEqual(generation_plan.timeout, 60.0)
        self.assertTrue(generation_plan.generation_rights_confirmed)

        generation_results = parser.parse_args(
            [
                "propose-generation-results",
                "generation-plan/plan.json",
                "generation-plan/review.json",
                "--project-root",
                "project",
                "--result-pack",
                "generated-results",
                "--generation-results-rights-confirmed",
            ]
        )
        self.assertEqual(generation_results.output_dir, Path("generation-results-proposal"))
        self.assertEqual(generation_results.ffprobe, Path("ffprobe"))
        self.assertEqual(generation_results.timeout, 60.0)

        generation_assembly = parser.parse_args(
            [
                "assemble-generation-pack",
                "generation-plan/plan.json",
                "generation-plan/review.json",
                "generation-results/proposal.json",
                "generation-results/review.json",
                "--project-root",
                "project",
            ]
        )
        self.assertEqual(generation_assembly.output_dir, Path("generation-asset-pack"))
        self.assertEqual(generation_assembly.ffprobe, Path("ffprobe"))
        self.assertEqual(generation_assembly.timeout, 60.0)

        with self.assertRaises(video_remix.CliArgumentError):
            parser.parse_args(
                [
                    "propose-assets",
                    "template.ir.json",
                    "--project-root",
                    "project",
                    "--asset-pack",
                    "asset-pack",
                ]
            )
        with self.assertRaises(video_remix.CliArgumentError):
            parser.parse_args(
                [
                    "prepare-generation",
                    "template.ir.json",
                    "generation-request.json",
                    "--project-root",
                    "project",
                    "--reference-pack",
                    "reference-pack",
                ]
            )
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exited:
            parser.parse_args(["--version"])
        self.assertEqual(exited.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), "video-remix 0.6.0-alpha")

        with mock.patch.object(
            video_remix,
            "_assets_module",
            side_effect=AssertionError("rights failure must not delegate"),
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = video_remix.main(
                    [
                        "propose-assets",
                        "template.ir.json",
                        "--project-root",
                        "project",
                        "--asset-pack",
                        "asset-pack",
                        "--json",
                    ]
                )
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "invalid_argument")

    def test_v05_propose_assets_delegates_raw_paths_and_compacts_result(self):
        result = {
            "schema_version": "0.5.0",
            "review_required": True,
            "counts": {
                "inventory_entries": 2,
                "template_slots": 3,
                "suggested_slots": 1,
                "private_count": 99,
            },
            "artifacts": {
                "proposal": {
                    "path": "asset-proposal/asset-pack-proposal.json",
                    "sha256": "a" * 64,
                    "source_filename": "C:/PRIVATE/source.png",
                },
                "review_template": {
                    "path": "asset-proposal/asset-review-decision.template.json",
                    "sha256": "b" * 64,
                },
                "contact_sheet": {
                    "path": "asset-proposal/asset-contact-sheet.png",
                    "sha256": "c" * 64,
                    "tool_path": "C:/PRIVATE/ffprobe.exe",
                },
                "source": {"path": "C:/PRIVATE/source.png"},
            },
            "source_filename": "C:/PRIVATE/source.png",
            "tool_stderr": "ffprobe: C:/PRIVATE/source.png",
        }
        core = SimpleNamespace(propose_asset_pack=mock.Mock(return_value=result))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._propose_assets_args(root)
            with mock.patch.object(video_remix, "_assets_module", return_value=core), mock.patch.object(
                video_remix, "_runtime_module", return_value=rrv_runtime
            ):
                payload, status = video_remix.run_propose_assets(args)
        self.assertEqual(status, 0)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(
            payload["result"],
            {
                "schema_version": "0.5.0",
                "review_required": True,
                "counts": {
                    "inventory_entries": 2,
                    "template_slots": 3,
                    "suggested_slots": 1,
                },
                "artifacts": {
                    "proposal": {
                        "path": "asset-proposal/asset-pack-proposal.json",
                        "sha256": "a" * 64,
                    },
                    "review_template": {
                        "path": "asset-proposal/asset-review-decision.template.json",
                        "sha256": "b" * 64,
                    },
                    "contact_sheet": {
                        "path": "asset-proposal/asset-contact-sheet.png",
                        "sha256": "c" * 64,
                    },
                },
            },
        )
        self.assertNotIn("PRIVATE", json.dumps(payload))
        core.propose_asset_pack.assert_called_once_with(
            Path("template.ir.json"),
            project_root=root,
            asset_pack=Path("asset-pack"),
            asset_pack_rights_confirmed=True,
            output_dir=Path("asset-proposal"),
            ffprobe=Path("ffprobe"),
            timeout_seconds=60.0,
        )

    def test_v05_freeze_assets_does_not_preread_packets_and_compacts_result(self):
        result = {
            "schema_version": "0.5.0",
            "review_required": False,
            "counts": {
                "inventory_entries": 2,
                "mapped_slots": 1,
                "omitted_slots": 2,
                "copied_assets": 1,
                "private_count": 99,
            },
            "artifacts": {
                "assets_manifest": {
                    "path": "frozen-assets/assets.json",
                    "sha256": "a" * 64,
                    "source_filename": "C:/PRIVATE/source.png",
                },
                "freeze_report": {
                    "path": "frozen-assets/asset-freeze-report.json",
                    "sha256": "b" * 64,
                    "tool_path": "C:/PRIVATE/ffprobe.exe",
                },
            },
            "source_filename": "C:/PRIVATE/source.png",
        }
        core = SimpleNamespace(freeze_assets=mock.Mock(return_value=result))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._freeze_assets_args(root)
            with mock.patch.object(video_remix, "_assets_module", return_value=core), mock.patch.object(
                video_remix, "_runtime_module", return_value=rrv_runtime
            ), mock.patch.object(
                video_remix,
                "_load_contract_json",
                side_effect=AssertionError("freeze-assets CLI must not read packets"),
            ) as packet_loader, mock.patch.object(
                video_remix,
                "validate_asset_proposal_data",
                side_effect=AssertionError("freeze-assets CLI must not validate proposal"),
            ) as proposal_validator, mock.patch.object(
                video_remix,
                "validate_asset_review_data",
                side_effect=AssertionError("freeze-assets CLI must not validate review"),
            ) as review_validator:
                payload, status = video_remix.run_freeze_assets(args)
        self.assertEqual(status, 0)
        self.assertEqual(
            payload["result"],
            {
                "schema_version": "0.5.0",
                "review_required": False,
                "counts": {
                    "inventory_entries": 2,
                    "mapped_slots": 1,
                    "omitted_slots": 2,
                    "copied_assets": 1,
                },
                "artifacts": {
                    "assets_manifest": {
                        "path": "frozen-assets/assets.json",
                        "sha256": "a" * 64,
                    },
                    "freeze_report": {
                        "path": "frozen-assets/asset-freeze-report.json",
                        "sha256": "b" * 64,
                    },
                },
            },
        )
        self.assertNotIn("PRIVATE", json.dumps(payload))
        packet_loader.assert_not_called()
        proposal_validator.assert_not_called()
        review_validator.assert_not_called()
        core.freeze_assets.assert_called_once_with(
            Path("asset-proposal/asset-pack-proposal.json"),
            Path("asset-proposal/approved-review.json"),
            project_root=root,
            output_dir=Path("frozen-assets"),
            ffprobe=Path("ffprobe"),
            timeout_seconds=60.0,
        )

    def test_v05_freeze_assets_pending_or_rejected_core_failures_exit_two(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for decision in ("pending", "rejected"):
                with self.subTest(decision=decision):
                    core = SimpleNamespace(
                        freeze_assets=mock.Mock(
                            side_effect=rrv_runtime.RRVError(
                                rrv_runtime.ERR_INVALID_ARGUMENT,
                                f"review is {decision}: C:/PRIVATE/review.json",
                            )
                        )
                    )
                    with mock.patch.object(video_remix, "_assets_module", return_value=core), mock.patch.object(
                        video_remix, "_runtime_module", return_value=rrv_runtime
                    ):
                        payload, status = video_remix.run_freeze_assets(
                            self._freeze_assets_args(root)
                        )
                    self.assertEqual(status, 2)
                    self.assertEqual(payload["status"], "error")
                    self.assertEqual(payload["error"]["code"], rrv_runtime.ERR_INVALID_ARGUMENT)
                    self.assertNotIn("PRIVATE", json.dumps(payload))
                    self.assertFalse((root / "frozen-assets").exists())

    def test_v06_prepare_generation_delegates_raw_inputs_and_compacts_result(self):
        result = {
            "schema_version": "0.6.0",
            "review_required": True,
            "execution_profile": "controller-managed",
            "adapter_id": "PRIVATE-adapter",
            "counts": {
                "reference_inventory_entries": 2,
                "tasks": 3,
                "generation_tasks": 1,
                "passthrough_tasks": 1,
                "omitted_tasks": 1,
                "private_count": 99,
            },
            "artifacts": {
                "generation_plan": {
                    "path": "generation-plan/generation-plan.json",
                    "sha256": "a" * 64,
                    "prompt": "PRIVATE prompt",
                },
                "review_template": {
                    "path": "generation-plan/generation-plan-review.template.json",
                    "sha256": "b" * 64,
                },
                "input_contact_sheet": {
                    "path": "generation-plan/generation-input-contact-sheet.png",
                    "sha256": "c" * 64,
                    "provider": "PRIVATE provider",
                },
            },
        }
        core = SimpleNamespace(prepare_generation=mock.Mock(return_value=result))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._prepare_generation_args(root)
            with mock.patch.object(video_remix, "_generation_module", return_value=core), mock.patch.object(
                video_remix, "_runtime_module", return_value=rrv_runtime
            ):
                payload, status = video_remix.run_prepare_generation(args)
        self.assertEqual(status, 0)
        self.assertEqual(
            payload["result"],
            {
                "schema_version": "0.6.0",
                "review_required": True,
                "counts": {
                    "reference_inventory_entries": 2,
                    "tasks": 3,
                    "generation_tasks": 1,
                    "passthrough_tasks": 1,
                    "omitted_tasks": 1,
                },
                "artifacts": {
                    "generation_plan": {
                        "path": "generation-plan/generation-plan.json",
                        "sha256": "a" * 64,
                    },
                    "review_template": {
                        "path": "generation-plan/generation-plan-review.template.json",
                        "sha256": "b" * 64,
                    },
                    "input_contact_sheet": {
                        "path": "generation-plan/generation-input-contact-sheet.png",
                        "sha256": "c" * 64,
                    },
                },
            },
        )
        self.assertNotIn("PRIVATE", json.dumps(payload))
        core.prepare_generation.assert_called_once_with(
            Path("template.ir.json"),
            Path("generation-request.json"),
            project_root=root,
            reference_pack=Path("reference-pack"),
            generation_rights_confirmed=True,
            output_dir=Path("generation-plan"),
            ffprobe=Path("ffprobe"),
            timeout_seconds=60.0,
        )

    def test_v06_generation_rights_gates_before_lazy_core_load(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._prepare_generation_args(root, generation_rights_confirmed=False)
            with mock.patch.object(
                video_remix,
                "_generation_module",
                side_effect=AssertionError("rights failure must not delegate"),
            ):
                payload, status = video_remix.run_prepare_generation(args)
            self.assertEqual(status, 2)
            self.assertEqual(payload["error"]["code"], "invalid_argument")

            results_args = self._propose_generation_results_args(
                root, generation_results_rights_confirmed=False
            )
            with mock.patch.object(
                video_remix,
                "_generation_module",
                side_effect=AssertionError("rights failure must not delegate"),
            ):
                payload, status = video_remix.run_propose_generation_results(results_args)
            self.assertEqual(status, 2)
            self.assertEqual(payload["error"]["code"], "invalid_argument")

    def test_v06_main_dispatches_generation_workflows_and_keeps_core_lazy_elsewhere(self):
        ready = {"schema_version": "1.0", "status": "ok", "result": {}}
        with mock.patch.object(video_remix, "run_prepare_generation", return_value=(ready, 0)) as prepare, mock.patch.object(
            video_remix, "run_propose_generation_results", return_value=(ready, 0)
        ) as propose_results, mock.patch.object(
            video_remix, "run_assemble_generation_pack", return_value=(ready, 0)
        ) as assemble:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    video_remix.main(
                        [
                            "prepare-generation",
                            "template.ir.json",
                            "generation-request.json",
                            "--project-root",
                            "project",
                            "--reference-pack",
                            "reference-pack",
                            "--generation-rights-confirmed",
                            "--json",
                        ]
                    ),
                    0,
                )
                self.assertEqual(
                    video_remix.main(
                        [
                            "propose-generation-results",
                            "generation-plan/plan.json",
                            "generation-plan/review.json",
                            "--project-root",
                            "project",
                            "--result-pack",
                            "generated-results",
                            "--generation-results-rights-confirmed",
                            "--json",
                        ]
                    ),
                    0,
                )
                self.assertEqual(
                    video_remix.main(
                        [
                            "assemble-generation-pack",
                            "generation-plan/plan.json",
                            "generation-plan/review.json",
                            "generation-results/proposal.json",
                            "generation-results/review.json",
                            "--project-root",
                            "project",
                            "--json",
                        ]
                    ),
                    0,
                )
        self.assertEqual(prepare.call_count, 1)
        self.assertEqual(propose_results.call_count, 1)
        self.assertEqual(assemble.call_count, 1)

        with mock.patch.object(
            video_remix,
            "_generation_module",
            side_effect=AssertionError("unrelated validation must not import generation core"),
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = video_remix.main(["validate-template", str(TEMPLATE_PATH), "--json"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "pass")

    def test_v06_propose_results_and_assembly_do_not_preread_review_packets(self):
        proposal_result = {
            "schema_version": "0.6.0",
            "review_required": True,
            "counts": {
                "result_inventory_entries": 2,
                "tasks": 3,
                "generation_tasks": 1,
                "passthrough_tasks": 1,
                "omitted_tasks": 1,
            },
            "artifacts": {
                "proposal": {
                    "path": "generation-results-proposal/results-proposal.json",
                    "sha256": "a" * 64,
                },
                "review_template": {
                    "path": "generation-results-proposal/results-review.template.json",
                    "sha256": "b" * 64,
                },
                "comparison_contact_sheet": {
                    "path": "generation-results-proposal/generation-results-contact-sheet.png",
                    "sha256": "c" * 64,
                },
            },
        }
        assembly_result = {
            "schema_version": "0.6.0",
            "review_required": False,
            "output_dir": "generation-asset-pack",
            "counts": {
                "output_assets": 2,
                "generation_results": 1,
                "image_passthrough": 1,
                "audio_passthrough": 0,
                "omitted_tasks": 1,
            },
            "assets": [
                {
                    "slot_id": "outfit.01",
                    "path": "generation-asset-pack/outfit.01.png",
                    "sha256": "a" * 64,
                    "media_type": "image/png",
                },
                {
                    "slot_id": "product.01",
                    "path": "generation-asset-pack/product.01.png",
                    "sha256": "b" * 64,
                    "media_type": "image/png",
                },
            ],
        }
        core = SimpleNamespace(
            propose_generation_results=mock.Mock(return_value=proposal_result),
            assemble_generation_pack=mock.Mock(return_value=assembly_result),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(video_remix, "_generation_module", return_value=core), mock.patch.object(
                video_remix, "_runtime_module", return_value=rrv_runtime
            ), mock.patch.object(
                video_remix,
                "_load_contract_json",
                side_effect=AssertionError("generation CLI must not read review packets"),
            ) as packet_loader, mock.patch.object(
                video_remix,
                "validate_generation_plan_data",
                side_effect=AssertionError("generation CLI must not validate plan before core"),
            ) as plan_validator, mock.patch.object(
                video_remix,
                "validate_generation_plan_review_data",
                side_effect=AssertionError("generation CLI must not validate review before core"),
            ) as review_validator:
                results_payload, results_status = video_remix.run_propose_generation_results(
                    self._propose_generation_results_args(root)
                )
                assembly_payload, assembly_status = video_remix.run_assemble_generation_pack(
                    self._assemble_generation_pack_args(root)
                )
        self.assertEqual(results_status, 0)
        self.assertEqual(assembly_status, 0)
        self.assertEqual(
            results_payload["result"]["artifacts"]["proposal"],
            {
                "path": "generation-results-proposal/results-proposal.json",
                "sha256": "a" * 64,
            },
        )
        self.assertEqual(
            assembly_payload["result"],
            {
                "schema_version": "0.6.0",
                "review_required": False,
                "counts": {
                    "output_assets": 2,
                    "generation_results": 1,
                    "image_passthrough": 1,
                    "audio_passthrough": 0,
                    "omitted_tasks": 1,
                },
                "artifacts": {
                    "assets": [
                        {
                            "path": "generation-asset-pack/outfit.01.png",
                            "sha256": "a" * 64,
                        },
                        {
                            "path": "generation-asset-pack/product.01.png",
                            "sha256": "b" * 64,
                        },
                    ]
                },
            },
        )
        packet_loader.assert_not_called()
        plan_validator.assert_not_called()
        review_validator.assert_not_called()
        core.propose_generation_results.assert_called_once_with(
            Path("generation-plan/generation-plan.json"),
            Path("generation-plan/approved-review.json"),
            project_root=root,
            result_pack=Path("generated-results"),
            generation_results_rights_confirmed=True,
            output_dir=Path("generation-results-proposal"),
            ffprobe=Path("ffprobe"),
            timeout_seconds=60.0,
        )
        core.assemble_generation_pack.assert_called_once_with(
            Path("generation-plan/generation-plan.json"),
            Path("generation-plan/approved-review.json"),
            Path("generation-results-proposal/results-proposal.json"),
            Path("generation-results-proposal/approved-review.json"),
            project_root=root,
            output_dir=Path("generation-asset-pack"),
            ffprobe=Path("ffprobe"),
            timeout_seconds=60.0,
        )

    def test_v06_generation_workflow_errors_and_validators_are_nonreflective(self):
        secret = "C:/PRIVATE/generated-results/secret-look.png"
        core = SimpleNamespace(
            prepare_generation=mock.Mock(
                side_effect=rrv_runtime.RRVError(
                    rrv_runtime.ERR_INVALID_ARGUMENT,
                    f"provider stderr and prompt: {secret}",
                    {"provider": "PRIVATE", "stderr": secret},
                )
            ),
            validate_generation_request_data=mock.Mock(
                return_value=[f"$.tasks[0].prompt: {secret}"]
            ),
            validate_generation_plan_data=mock.Mock(
                return_value=[f"$.adapter_id: {secret}"]
            ),
            validate_generation_plan_review_data=mock.Mock(
                return_value=[f"$.controller_label: {secret}"]
            ),
            validate_generation_results_proposal_data=mock.Mock(
                return_value=[f"$.inventory[0].source_path: {secret}"]
            ),
            validate_generation_results_review_data=mock.Mock(
                return_value=[f"$.decisions[0].notes: {secret}"]
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packet = root / "packet.json"
            packet.write_text("{}", encoding="utf-8")
            duplicate = root / "duplicate.json"
            duplicate.write_text(
                '{"schema_version":"0.6.0","schema_version":"C:/PRIVATE/secret.json"}',
                encoding="utf-8",
            )
            nonfinite = root / "nonfinite.json"
            nonfinite.write_text('{"schema_version":NaN}', encoding="utf-8")
            with mock.patch.object(video_remix, "_generation_module", return_value=core), mock.patch.object(
                video_remix, "_runtime_module", return_value=rrv_runtime
            ):
                payload, status = video_remix.run_prepare_generation(
                    self._prepare_generation_args(root)
                )
                self.assertEqual(status, 2)
                self.assertEqual(payload["error"]["message"], "generation planning failed")
                self.assertNotIn("PRIVATE", json.dumps(payload))

                for command in (
                    "validate-generation-request",
                    "validate-generation-plan",
                    "validate-generation-plan-review",
                    "validate-generation-results-proposal",
                    "validate-generation-results-review",
                ):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        status = video_remix.main([command, str(packet), "--json"])
                    self.assertEqual(status, 2)
                    self.assertEqual(
                        json.loads(output.getvalue()),
                        {"status": "fail", "errors": ["$: validation.invalid"]},
                    )
                    self.assertNotIn("PRIVATE", output.getvalue())

                for command, path, expected in (
                    ("validate-generation-request", duplicate, "$: json.duplicate_key"),
                    ("validate-generation-results-review", nonfinite, "$: json.finite_number"),
                ):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        status = video_remix.main([command, str(path), "--json"])
                    self.assertEqual(status, 2)
                    self.assertEqual(
                        json.loads(output.getvalue()),
                        {"status": "fail", "errors": [expected]},
                    )
                    self.assertNotIn("PRIVATE", output.getvalue())

    def test_v05_asset_validate_commands_are_strict_pure_and_nonreflective(self):
        secret = "C:/PRIVATE/asset-pack/secret-source.png"
        stderr = f"ffprobe stderr: could not read {secret}"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proposal = self._asset_proposal_packet()
            proposal["template_path"] = secret
            proposal["PRIVATE_source"] = stderr
            proposal_path = root / "proposal.json"
            proposal_path.write_text(json.dumps(proposal), encoding="utf-8")
            review = self._asset_review_packet()
            review["proposal_sha256"] = secret
            review["PRIVATE_source"] = stderr
            review_path = root / "review.json"
            review_path.write_text(json.dumps(review), encoding="utf-8")
            duplicate_path = root / "duplicate.json"
            duplicate_path.write_text(
                '{"schema_version":"0.5.0","schema_version":"C:/PRIVATE/secret.json"}',
                encoding="utf-8",
            )
            nonfinite_path = root / "nonfinite.json"
            nonfinite_path.write_text('{"schema_version":NaN}', encoding="utf-8")

            with mock.patch.object(
                rrv_assets,
                "_run_ffprobe",
                side_effect=AssertionError("validation must not probe media"),
            ) as probe:
                for command, path in (
                    ("validate-asset-proposal", proposal_path),
                    ("validate-asset-review", review_path),
                    ("validate-asset-proposal", duplicate_path),
                    ("validate-asset-review", nonfinite_path),
                ):
                    with self.subTest(command=command, path=path.name):
                        output = io.StringIO()
                        with contextlib.redirect_stdout(output):
                            status = video_remix.main([command, str(path), "--json"])
                        self.assertEqual(status, 2)
                        rendered = output.getvalue()
                        self.assertNotIn("PRIVATE", rendered)
                        self.assertNotIn("secret-source.png", rendered)
                        self.assertNotIn("ffprobe stderr", rendered)
                probe.assert_not_called()
            duplicate_output = io.StringIO()
            with contextlib.redirect_stdout(duplicate_output):
                duplicate_status = video_remix.main(
                    ["validate-asset-proposal", str(duplicate_path), "--json"]
                )
            self.assertEqual(duplicate_status, 2)
            self.assertEqual(
                json.loads(duplicate_output.getvalue()),
                {"status": "fail", "errors": ["$: json.duplicate_key"]},
            )
            nonfinite_output = io.StringIO()
            with contextlib.redirect_stdout(nonfinite_output):
                nonfinite_status = video_remix.main(
                    ["validate-asset-review", str(nonfinite_path), "--json"]
                )
            self.assertEqual(nonfinite_status, 2)
            self.assertEqual(
                json.loads(nonfinite_output.getvalue()),
                {"status": "fail", "errors": ["$: json.finite_number"]},
            )
            self.assertFalse((root / "asset-proposal").exists())
            self.assertFalse((root / "frozen-assets").exists())

    def test_v05_doctor_gates_asset_capabilities_independently(self):
        executable_tools = rrv_runtime.RuntimeTools(
            ffmpeg=rrv_runtime.ToolInfo("ffmpeg", "fake-ffmpeg", "explicit", "ffmpeg 7"),
            ffprobe=rrv_runtime.ToolInfo("ffprobe", "fake-ffprobe", "explicit", "ffprobe 7"),
        )
        runtime = SimpleNamespace(discover_tools=mock.Mock(return_value=executable_tools))
        assets = SimpleNamespace(propose_asset_pack=mock.Mock(), freeze_assets=mock.Mock())
        snapshot_renderer = SimpleNamespace(
            render_project=mock.Mock(),
            resolve_local_assets=mock.Mock(),
            close_resolved_assets=mock.Mock(),
        )
        with mock.patch.object(video_remix, "_runtime_module", return_value=runtime), mock.patch.object(
            video_remix, "_pillow_available", return_value=True
        ), mock.patch.object(video_remix, "_assets_module", return_value=assets), mock.patch.object(
            video_remix, "_render_module", return_value=snapshot_renderer
        ):
            capabilities = video_remix.doctor_payload()["capabilities"]
        self.assertTrue(capabilities["asset_pack_proposal"])
        self.assertTrue(capabilities["asset_review_freeze"])
        self.assertTrue(capabilities["asset_bound_render"])
        self.assertFalse(capabilities["asset_media_probe_validation"])

        no_probe = rrv_runtime.RuntimeTools(
            ffmpeg=executable_tools.ffmpeg,
            ffprobe=rrv_runtime.ToolInfo("ffprobe", "fake-ffprobe", "explicit", None),
        )
        with mock.patch.object(
            video_remix,
            "_runtime_module",
            return_value=SimpleNamespace(discover_tools=mock.Mock(return_value=no_probe)),
        ), mock.patch.object(video_remix, "_pillow_available", return_value=True), mock.patch.object(
            video_remix, "_assets_module", return_value=assets
        ), mock.patch.object(video_remix, "_render_module", return_value=snapshot_renderer):
            capabilities = video_remix.doctor_payload()["capabilities"]
        self.assertFalse(capabilities["asset_pack_proposal"])
        self.assertFalse(capabilities["asset_review_freeze"])
        self.assertTrue(capabilities["asset_bound_render"])

        with tempfile.TemporaryDirectory() as directory:
            missing_schema = Path(directory) / "missing-asset-proposal.schema.json"
            with mock.patch.object(video_remix, "ASSET_PACK_PROPOSAL_SCHEMA_PATH", missing_schema), mock.patch.object(
                video_remix, "_runtime_module", return_value=runtime
            ), mock.patch.object(video_remix, "_pillow_available", return_value=True), mock.patch.object(
                video_remix, "_assets_module", return_value=assets
            ), mock.patch.object(video_remix, "_render_module", return_value=snapshot_renderer):
                capabilities = video_remix.doctor_payload()["capabilities"]
        self.assertFalse(capabilities["asset_pack_proposal"])
        self.assertFalse(capabilities["asset_review_freeze"])
        self.assertTrue(capabilities["asset_bound_render"])

        nonexecutable_tools = rrv_runtime.RuntimeTools(
            ffmpeg=rrv_runtime.ToolInfo("ffmpeg", "fake-ffmpeg", "explicit", None),
            ffprobe=rrv_runtime.ToolInfo("ffprobe", "fake-ffprobe", "explicit", None),
        )
        no_snapshot_renderer = SimpleNamespace(
            render_project=mock.Mock(), resolve_local_assets=mock.Mock()
        )
        with mock.patch.object(
            video_remix,
            "_runtime_module",
            return_value=SimpleNamespace(
                discover_tools=mock.Mock(return_value=nonexecutable_tools)
            ),
        ), mock.patch.object(video_remix, "_pillow_available", return_value=True), mock.patch.object(
            video_remix, "_assets_module", return_value=assets
        ), mock.patch.object(video_remix, "_render_module", return_value=no_snapshot_renderer):
            capabilities = video_remix.doctor_payload()["capabilities"]
        self.assertFalse(capabilities["asset_pack_proposal"])
        self.assertFalse(capabilities["asset_review_freeze"])
        self.assertFalse(capabilities["asset_bound_render"])

    def test_v06_doctor_gates_generation_packet_capabilities_without_claiming_generation(self):
        executable_tools = rrv_runtime.RuntimeTools(
            ffmpeg=rrv_runtime.ToolInfo("ffmpeg", "fake-ffmpeg", "explicit", "ffmpeg 7"),
            ffprobe=rrv_runtime.ToolInfo("ffprobe", "fake-ffprobe", "explicit", "ffprobe 7"),
        )
        runtime = SimpleNamespace(discover_tools=mock.Mock(return_value=executable_tools))
        generation = SimpleNamespace(
            prepare_generation=mock.Mock(),
            propose_generation_results=mock.Mock(),
            assemble_generation_pack=mock.Mock(),
        )
        with mock.patch.object(video_remix, "_runtime_module", return_value=runtime), mock.patch.object(
            video_remix, "_pillow_available", return_value=True
        ), mock.patch.object(video_remix, "_generation_module", return_value=generation):
            capabilities = video_remix.doctor_payload()["capabilities"]
        self.assertTrue(capabilities["generation_request_validation"])
        self.assertTrue(capabilities["generation_plan_validation"])
        self.assertTrue(capabilities["generation_plan_review_validation"])
        self.assertTrue(capabilities["generation_results_proposal_validation"])
        self.assertTrue(capabilities["generation_results_review_validation"])
        self.assertTrue(capabilities["generation_planning"])
        self.assertTrue(capabilities["generation_result_review"])
        self.assertTrue(capabilities["generation_pack_assembly"])
        self.assertFalse(capabilities["asset_generation"])
        self.assertFalse(capabilities["network_generation"])
        self.assertFalse(capabilities["cloud_generation"])

        incomplete_generation = SimpleNamespace(prepare_generation=mock.Mock())
        with mock.patch.object(video_remix, "_runtime_module", return_value=runtime), mock.patch.object(
            video_remix, "_pillow_available", return_value=True
        ), mock.patch.object(
            video_remix, "_generation_module", return_value=incomplete_generation
        ):
            capabilities = video_remix.doctor_payload()["capabilities"]
        self.assertTrue(capabilities["generation_planning"])
        self.assertFalse(capabilities["generation_result_review"])
        self.assertFalse(capabilities["generation_pack_assembly"])


if __name__ == "__main__":
    unittest.main()
