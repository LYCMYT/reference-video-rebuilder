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
            self.assertEqual(payload["errors"], ["template invalid", "asset invalid"])
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

            def reparse_packet_directory(path):
                stat_result = original_lstat(path)
                if Path(path) == packets:
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


if __name__ == "__main__":
    unittest.main()
