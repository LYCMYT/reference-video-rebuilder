import contextlib
import io
import json
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


if __name__ == "__main__":
    unittest.main()
