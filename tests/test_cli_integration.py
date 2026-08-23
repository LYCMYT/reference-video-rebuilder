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
        self.assertTrue(payload["capabilities"]["timeline_render"])
        self.assertTrue(payload["capabilities"]["video_qa"])
        self.assertEqual(payload["runtime"]["ffmpeg"]["source"], "explicit")

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
