import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIRECTORY = REPO_ROOT / "skills" / "rebuild-reference-video" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIRECTORY))

import rrv_analyze  # noqa: E402
import rrv_runtime  # noqa: E402


class RuntimePathSafetyTests(unittest.TestCase):
    def test_output_paths_stay_inside_the_given_project_root(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "project"
            root.mkdir()
            inside = rrv_runtime.resolve_output_path(root, "outputs/frame.png", create_parent=True)
            self.assertEqual(inside, (root / "outputs" / "frame.png").resolve())
            self.assertTrue(inside.parent.is_dir())

            for unsafe in ("../escape.png", base / "outside.png"):
                with self.assertRaises(rrv_runtime.RRVError) as raised:
                    rrv_runtime.resolve_output_path(root, unsafe)
                self.assertEqual(raised.exception.code, rrv_runtime.ERR_OUTPUT_PATH_OUTSIDE_ROOT)

    def test_source_input_may_be_outside_project_root_and_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "external.mp4"
            source.write_bytes(b"source bytes")
            project = base / "project"
            project.mkdir()
            resolved = rrv_runtime.require_source_file(source)
            self.assertEqual(resolved, source.resolve())
            self.assertEqual(source.read_bytes(), b"source bytes")
            self.assertEqual(
                rrv_analyze.sha256_file(source),
                "4d4823794cbed3c4ee0bbc684c8f66e1dfd5afa6f078d494ce254ec5a4671753",
            )


class RuntimeDiscoveryTests(unittest.TestCase):
    def test_explicit_paths_win_over_environment_and_path(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            explicit_ffmpeg = base / "explicit-ffmpeg.exe"
            environment_ffmpeg = base / "environment-ffmpeg.exe"
            environment_ffprobe = base / "environment-ffprobe.exe"
            for path in (explicit_ffmpeg, environment_ffmpeg, environment_ffprobe):
                path.write_bytes(b"not executed")
            tools = rrv_runtime.discover_tools(
                ffmpeg=explicit_ffmpeg,
                environment={
                    "RRV_FFMPEG": str(environment_ffmpeg),
                    "RRV_FFPROBE": str(environment_ffprobe),
                },
            )
            self.assertEqual(tools.ffmpeg.path, str(explicit_ffmpeg.resolve()))
            self.assertEqual(tools.ffmpeg.source, "explicit")
            self.assertEqual(tools.ffprobe.path, str(environment_ffprobe.resolve()))
            self.assertEqual(tools.ffprobe.source, "env:RRV_FFPROBE")

    def test_path_discovery_uses_shutil_which_without_hardcoded_locations(self):
        with mock.patch.object(rrv_runtime.shutil, "which", return_value="C:/tools/ffmpeg.exe") as which:
            tools = rrv_runtime.discover_tools(environment={"PATH": "C:/tools"})
        self.assertEqual(tools.ffmpeg.path, str(Path("C:/tools/ffmpeg.exe").resolve()))
        self.assertEqual(tools.ffmpeg.source, "PATH")
        self.assertIn(mock.call("ffmpeg", path="C:/tools"), which.call_args_list)


class CommandAndProbeTests(unittest.TestCase):
    def _source_file(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        directory = tempfile.TemporaryDirectory()
        source = Path(directory.name) / "input media.mp4"
        source.write_bytes(b"fake media")
        return directory, source

    def test_commands_are_argv_lists_and_keep_input_and_output_separate(self):
        temporary, source = self._source_file()
        self.addCleanup(temporary.cleanup)
        output = Path(temporary.name) / "out frame.png"
        frame_command = rrv_analyze.build_frame_extraction_command(
            source, "ffmpeg.exe", 42, output
        )
        self.assertIsInstance(frame_command, list)
        self.assertEqual(frame_command[0], "ffmpeg.exe")
        self.assertEqual(frame_command[frame_command.index("-i") + 1], str(source.resolve()))
        self.assertIn("select=eq(n\\,42)", frame_command)
        self.assertIn("-n", frame_command)
        self.assertEqual(frame_command[-1], str(output))

        audio_command = rrv_analyze.build_audio_extraction_command(source, "ffmpeg.exe", output)
        self.assertEqual(audio_command[audio_command.index("-map") + 1], "0:a:0")
        self.assertEqual(audio_command[audio_command.index("-c:a") + 1], "copy")
        self.assertEqual(audio_command[audio_command.index("-f") + 1], "matroska")
        self.assertEqual(audio_command[audio_command.index("-map_metadata") + 1], "-1")
        self.assertEqual(audio_command[audio_command.index("-map_chapters") + 1], "-1")

    def test_real_stream_copy_strips_source_identity_metadata_when_ffmpeg_is_available(self):
        ffmpeg = os.environ.get("RRV_FFMPEG") or shutil.which("ffmpeg")
        ffprobe = os.environ.get("RRV_FFPROBE") or shutil.which("ffprobe")
        if not ffmpeg:
            self.skipTest("ffmpeg is not available for the integration assertion")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "identified-source.mka"
            extracted = root / "audio-original.mka"
            source_command = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=660:sample_rate=8000:duration=0.1",
                "-metadata",
                "title=private-title-marker",
                "-metadata",
                "artist=private-artist-marker",
                "-metadata",
                "comment=private-account-marker",
                "-c:a",
                "pcm_s16le",
                "-f",
                "matroska",
                "-y",
                str(source),
            ]
            subprocess.run(source_command, check=True, capture_output=True, text=True, timeout=20)
            subprocess.run(
                rrv_analyze.build_audio_extraction_command(source, ffmpeg, extracted),
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
            if ffprobe:
                probe = subprocess.run(
                    [ffprobe, "-v", "error", "-show_entries", "format_tags", "-of", "json", str(extracted)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                tags = json.loads(probe.stdout).get("format", {}).get("tags", {})
                self.assertFalse({"title", "artist", "comment"} & {str(key).lower() for key in tags})
                probe_text = json.dumps(tags).lower()
            else:
                probe = subprocess.run(
                    [ffmpeg, "-hide_banner", "-nostdin", "-i", str(extracted), "-f", "null", "-"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                probe_text = (probe.stdout + probe.stderr).lower()
            self.assertNotIn("private-title-marker", probe_text)
            self.assertNotIn("private-artist-marker", probe_text)
            self.assertNotIn("private-account-marker", probe_text)

    def test_ffprobe_json_is_normalized_and_preferred(self):
        temporary, source = self._source_file()
        self.addCleanup(temporary.cleanup)
        raw = {
            "format": {
                "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
                "format_long_name": "QuickTime / MOV",
                "duration": "2.500000",
                "size": "99",
                "bit_rate": "12345",
            },
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "r_frame_rate": "30000/1001",
                    "avg_frame_rate": "30000/1001",
                    "nb_frames": "75",
                },
                {
                    "index": 1,
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "sample_rate": "48000",
                    "channels": 2,
                    "channel_layout": "stereo",
                },
            ],
        }
        tools = rrv_runtime.RuntimeTools(
            ffmpeg=rrv_runtime.ToolInfo("ffmpeg", "fake-ffmpeg", "explicit"),
            ffprobe=rrv_runtime.ToolInfo("ffprobe", "fake-ffprobe", "explicit"),
        )
        observed: list[tuple[str, ...]] = []

        def fake_run(command, **_kwargs):
            observed.append(tuple(command))
            return rrv_runtime.CommandResult(tuple(command), 0, json.dumps(raw), "")

        with mock.patch.object(rrv_runtime, "run_command", side_effect=fake_run):
            result = rrv_runtime.probe_media(source, tools=tools)
        self.assertEqual(result["probe"]["backend"], "ffprobe")
        self.assertEqual(result["probe"]["capability_level"], "full")
        self.assertEqual(result["media"]["format"]["duration_seconds"], 2.5)
        self.assertEqual(result["media"]["streams"][0]["frame_count"], 75)
        self.assertEqual(result["media"]["streams"][1]["sample_rate"], 48000)
        self.assertIn("-of", observed[0])
        self.assertEqual(observed[0][-1], str(source.resolve()))

    def test_ffmpeg_fallback_declares_its_metadata_limitations(self):
        temporary, source = self._source_file()
        self.addCleanup(temporary.cleanup)
        stderr = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'input.mp4':
  Duration: 00:00:02.50, start: 0.000000, bitrate: 123 kb/s
    Stream #0:0[0x1](und): Video: h264, yuv420p, 1920x1080, 30 fps
    Stream #0:1[0x2](und): Audio: aac, 48000 Hz, stereo, fltp
"""
        tools = rrv_runtime.RuntimeTools(
            ffmpeg=rrv_runtime.ToolInfo("ffmpeg", "fake-ffmpeg", "explicit"),
            ffprobe=rrv_runtime.ToolInfo("ffprobe", None, None),
        )
        with mock.patch.object(
            rrv_runtime,
            "run_command",
            return_value=rrv_runtime.CommandResult(("fake-ffmpeg",), 1, "", stderr),
        ):
            result = rrv_runtime.probe_media(source, tools=tools)
        self.assertEqual(result["probe"]["backend"], "ffmpeg-fallback")
        self.assertEqual(result["probe"]["capability_level"], "minimal")
        self.assertTrue(result["probe"]["limitations"])
        self.assertEqual(result["media"]["streams"][0]["width"], 1920)
        self.assertEqual(result["media"]["streams"][1]["type"], "audio")

    def test_subprocess_timeout_and_error_are_bounded(self):
        with self.assertRaises(rrv_runtime.RRVError) as timed_out:
            rrv_runtime.run_command(
                [sys.executable, "-c", "import time; time.sleep(2)"], timeout_seconds=0.01
            )
        self.assertEqual(timed_out.exception.code, rrv_runtime.ERR_TOOL_TIMEOUT)

        script = "import sys; sys.stderr.write('x' * 3000); sys.exit(7)"
        with self.assertRaises(rrv_runtime.RRVError) as failed:
            rrv_runtime.run_command([sys.executable, "-c", script], timeout_seconds=5)
        self.assertEqual(failed.exception.code, rrv_runtime.ERR_TOOL_EXECUTION)
        self.assertLessEqual(len(failed.exception.details["output"]), rrv_runtime.MAX_ERROR_TEXT_LENGTH)


class SurveyPlanningTests(unittest.TestCase):
    def test_uniform_frame_selection_is_bounded_and_deterministic(self):
        media = {
            "format": {"duration_seconds": 10.0},
            "streams": [
                {"type": "video", "average_frame_rate": 10.0, "frame_count": 100}
            ],
        }
        self.assertEqual(
            rrv_analyze.choose_frame_numbers(media, sample_count=5), [0, 25, 50, 74, 99]
        )
        self.assertEqual(
            rrv_analyze.choose_frame_numbers(media, frame_numbers=[4, 1, 4]), [4, 1]
        )

    def test_missing_pillow_has_an_explicit_capability_error(self):
        with mock.patch.dict(sys.modules, {"PIL": None}):
            with self.assertRaises(rrv_runtime.RRVError) as raised:
                rrv_analyze._load_pillow()
        self.assertEqual(raised.exception.code, rrv_runtime.ERR_CAPABILITY_UNAVAILABLE)
        self.assertEqual(raised.exception.details["dependency"], "Pillow")

    def test_new_survey_directory_cannot_escape_project_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            with self.assertRaises(rrv_runtime.RRVError) as raised:
                rrv_analyze._new_survey_directory(root, "../escape")
        self.assertEqual(raised.exception.code, rrv_runtime.ERR_OUTPUT_PATH_OUTSIDE_ROOT)

    def test_survey_writes_only_root_relative_artifacts_with_mocked_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "outside-project.mp4"
            source.write_bytes(b"unchanged source media")
            root = base / "project"
            root.mkdir()
            raw_probe = {
                "format": {"format_name": "mp4", "duration": "1.0"},
                "streams": [
                    {
                        "index": 0,
                        "codec_type": "video",
                        "codec_name": "h264",
                        "r_frame_rate": "10/1",
                        "avg_frame_rate": "10/1",
                        "nb_frames": "10",
                    },
                    {"index": 1, "codec_type": "audio", "codec_name": "aac"},
                ],
            }
            tools = rrv_runtime.RuntimeTools(
                ffmpeg=rrv_runtime.ToolInfo("ffmpeg", "fake-ffmpeg", "explicit"),
                ffprobe=rrv_runtime.ToolInfo("ffprobe", "fake-ffprobe", "explicit"),
            )

            def fake_run(command, **_kwargs):
                if "-show_format" in command:
                    return rrv_runtime.CommandResult(tuple(command), 0, json.dumps(raw_probe), "")
                output = Path(command[-1])
                output.write_bytes(b"derived artifact")
                return rrv_runtime.CommandResult(tuple(command), 0, "", "")

            with mock.patch.object(rrv_runtime, "run_command", side_effect=fake_run):
                result = rrv_analyze.survey_reference(
                    source,
                    root,
                    output_dir="surveys/run-001",
                    frame_numbers=[0, 9],
                    include_contact_sheet=False,
                    tools=tools,
                )

            self.assertEqual(source.read_bytes(), b"unchanged source media")
            self.assertEqual([item["frame_number"] for item in result["frames"]], [0, 9])
            self.assertEqual(result["audio"]["mode"], "stream-copy")
            self.assertEqual(result["audio"]["media_type"], "audio/x-matroska")
            self.assertEqual(result["audio"]["container"], "matroska")
            self.assertTrue(result["audio"]["metadata_stripped"])
            for relative_path in (
                *(item["path"] for item in result["frames"]),
                result["audio"]["path"],
                result["artifacts"]["media_json"],
                result["artifacts"]["survey_json"],
            ):
                self.assertFalse(Path(relative_path).is_absolute())
                self.assertTrue((root / relative_path).is_file(), relative_path)
            survey_data = json.loads((root / result["artifacts"]["survey_json"]).read_text(encoding="utf-8"))
            self.assertEqual(survey_data["source"]["sha256"], result["source"]["sha256"])


if __name__ == "__main__":
    unittest.main()
