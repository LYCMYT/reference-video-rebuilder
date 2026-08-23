import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skills" / "reference-video-rebuilder" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import rrv_qa  # noqa: E402
import rrv_runtime  # noqa: E402


REAL_TOOLS = rrv_runtime.discover_tools()


def _source(directory: str, name: str = "delivery; not-a-command.mp4") -> Path:
    path = Path(directory) / name
    path.write_bytes(b"unchanged input")
    return path


def _probe(*, width=720, height=1280, fps=30.0, frames=10, audio=True, durations=True):
    video = {
        "index": 0,
        "type": "video",
        "codec_name": "h264",
        "width": width,
        "height": height,
        "average_frame_rate": fps,
        "frame_rate": fps,
        "frame_count": frames,
    }
    format_data = {"format_name": "mp4"}
    if durations:
        video["duration_seconds"] = frames / fps
        format_data["duration_seconds"] = frames / fps
    streams = [video]
    if audio:
        streams.append({"index": 1, "type": "audio", "codec_name": "aac"})
    return {
        "probe": {"backend": "ffprobe", "capability_level": "full", "limitations": []},
        "media": {"format": format_data, "streams": streams},
    }


def _tools() -> rrv_runtime.RuntimeTools:
    return rrv_runtime.RuntimeTools(
        ffmpeg=rrv_runtime.ToolInfo("ffmpeg", "fake ffmpeg.exe", "explicit"),
        ffprobe=rrv_runtime.ToolInfo("ffprobe", "fake ffprobe.exe", "explicit"),
    )


def _decode_result(frames: int = 10) -> rrv_runtime.CommandResult:
    progress = f"frame=1\nprogress=continue\nframe={frames}\nprogress=end\n"
    return rrv_runtime.CommandResult(("fake ffmpeg.exe",), 0, progress, "")


class QACommandTests(unittest.TestCase):
    def test_decode_command_is_argv_only_and_keeps_a_hostile_filename_literal(self):
        with tempfile.TemporaryDirectory() as directory:
            source = _source(directory)
            command = rrv_qa.build_full_decode_command(source, "ffmpeg.exe")
        self.assertIsInstance(command, list)
        self.assertEqual(command[0], "ffmpeg.exe")
        self.assertEqual(command[command.index("-i") + 1], str(source.resolve()))
        self.assertIn("-xerror", command)
        self.assertIn("pipe:1", command)
        self.assertEqual(command[-2:], ["null", "-"])
        self.assertNotIn("shell", " ".join(command).lower())

    def test_progress_parser_requires_terminal_event_and_uses_last_frame(self):
        self.assertEqual(
            rrv_qa.parse_decode_progress("frame=2\nprogress=continue\nframe=8\nprogress=end\n"),
            {"frame_count": 8, "completed": True},
        )
        self.assertEqual(
            rrv_qa.parse_decode_progress("frame=8\nprogress=continue\n"),
            {"frame_count": 8, "completed": False},
        )


class DeliveryVerificationTests(unittest.TestCase):
    def _verify(self, source, *, probe=None, decode=None, **kwargs):
        if probe is None:
            probe = _probe()
        if decode is None:
            decode = _decode_result()
        with mock.patch.object(rrv_runtime, "probe_media", return_value=probe), mock.patch.object(
            rrv_runtime, "run_command", return_value=decode
        ) as run:
            result = rrv_qa.verify_delivery(source, tools=_tools(), **kwargs)
        return result, run

    def test_matching_delivery_returns_sha_backend_decode_count_and_pass_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            source = _source(directory)
            original = source.read_bytes()
            result, run = self._verify(
                source,
                expected_width=720,
                expected_height=1280,
                expected_fps=30,
                expected_frames=10,
                expect_audio=True,
            )
            self.assertEqual(source.read_bytes(), original)
        self.assertTrue(result["passed"])
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["probe_backend"], "ffprobe")
        self.assertEqual(result["decode_frame_count"], 10)
        self.assertEqual(len(result["source"]["sha256"]), 64)
        command = run.call_args.args[0]
        self.assertIsInstance(command, list)
        self.assertEqual(command[command.index("-i") + 1], str(source.resolve()))
        self.assertEqual(run.call_args.kwargs["check"], False)

    def test_frame_and_audio_mismatch_are_structured_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            source = _source(directory)
            result, _ = self._verify(
                source,
                probe=_probe(audio=False),
                decode=_decode_result(9),
                expected_frames=10,
                expect_audio=True,
            )
        checks = {item["id"]: item for item in result["checks"]}
        self.assertFalse(result["passed"])
        self.assertEqual(result["status"], "fail")
        self.assertFalse(checks["frame_count"]["passed"])
        self.assertFalse(checks["audio_presence"]["passed"])

    def test_failed_decode_is_structured_and_not_a_generic_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            source = _source(directory)
            failed = rrv_runtime.CommandResult(
                ("fake ffmpeg.exe",), 69, "frame=3\nprogress=continue\n", "Invalid data found"
            )
            result, _ = self._verify(source, decode=failed, expected_frames=10)
        checks = {item["id"]: item for item in result["checks"]}
        self.assertFalse(result["passed"])
        self.assertFalse(checks["full_video_decode"]["passed"])
        self.assertEqual(result["decode"]["error"]["code"], "ffmpeg_decode_failed")
        self.assertLessEqual(
            len(result["decode"]["error"]["details"]["output"]), rrv_runtime.MAX_ERROR_TEXT_LENGTH
        )

    def test_incomplete_progress_is_a_structured_decode_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            source = _source(directory)
            incomplete = rrv_runtime.CommandResult(
                ("fake ffmpeg.exe",), 0, "frame=10\nprogress=continue\n", ""
            )
            result, _ = self._verify(source, decode=incomplete, expected_frames=10)
        self.assertFalse(result["passed"])
        self.assertEqual(result["decode"]["error"]["code"], "decode_progress_incomplete")

    def test_probe_failure_is_a_structured_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            source = _source(directory)
            error = rrv_runtime.RRVError(rrv_runtime.ERR_PROBE_FAILED, "bad container")
            with mock.patch.object(rrv_runtime, "probe_media", side_effect=error):
                result = rrv_qa.verify_delivery(source, tools=_tools())
        self.assertFalse(result["passed"])
        self.assertEqual(result["probe"]["error"]["code"], rrv_runtime.ERR_PROBE_FAILED)
        self.assertIsNone(result["decode_frame_count"])

    def test_timeout_is_bounded_and_distinct_from_a_delivery_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            source = _source(directory)
            timeout = rrv_runtime.RRVError(
                rrv_runtime.ERR_TOOL_TIMEOUT, "ffmpeg exceeded the timeout", {"timeout_seconds": 0.01}
            )
            with mock.patch.object(rrv_runtime, "probe_media", return_value=_probe()), mock.patch.object(
                rrv_runtime, "run_command", side_effect=timeout
            ):
                with self.assertRaises(rrv_runtime.RRVError) as raised:
                    rrv_qa.verify_delivery(source, tools=_tools(), timeout_seconds=0.01)
        self.assertEqual(raised.exception.code, rrv_qa.ERR_DECODE_TIMEOUT)
        self.assertEqual(raised.exception.details["cause_code"], rrv_runtime.ERR_TOOL_TIMEOUT)

    def test_public_timeout_alias_is_forwarded_to_probe_and_decode(self):
        with tempfile.TemporaryDirectory() as directory:
            source = _source(directory)
            with mock.patch.object(rrv_runtime, "probe_media", return_value=_probe()) as probe, mock.patch.object(
                rrv_runtime, "run_command", return_value=_decode_result()
            ) as run:
                result = rrv_qa.verify_delivery(source, tools=_tools(), timeout=7.5)
        self.assertTrue(result["passed"])
        self.assertEqual(probe.call_args.kwargs["timeout_seconds"], 7.5)
        self.assertEqual(run.call_args.kwargs["timeout_seconds"], 7.5)

    def test_duration_delta_over_one_frame_fails_when_metadata_is_available(self):
        with tempfile.TemporaryDirectory() as directory:
            source = _source(directory)
            probe = _probe(durations=True)
            probe["media"]["format"]["duration_seconds"] = 2.0
            result, _ = self._verify(source, probe=probe)
        checks = {item["id"]: item for item in result["checks"]}
        self.assertFalse(result["passed"])
        self.assertFalse(checks["container_video_duration"]["passed"])

    def test_invalid_expectation_is_a_bounded_error(self):
        with tempfile.TemporaryDirectory() as directory:
            source = _source(directory)
            with self.assertRaises(rrv_runtime.RRVError) as raised:
                rrv_qa.verify_delivery(source, expected_frames=0, tools=_tools())
        self.assertEqual(raised.exception.code, rrv_qa.ERR_EXPECTATION_INVALID)


class DeliveryQACliTests(unittest.TestCase):
    def test_cli_wraps_a_structured_failure_and_uses_exit_code_one(self):
        failure = {
            "schema_version": rrv_qa.QA_SCHEMA_VERSION,
            "passed": False,
            "status": "fail",
            "checks": [],
        }
        with mock.patch.object(rrv_qa, "verify_delivery", return_value=failure), mock.patch(
            "builtins.print"
        ) as printed:
            status = rrv_qa.main(["verify", "delivery.mp4"])
        self.assertEqual(status, 1)
        payload = json.loads(printed.call_args.args[0])
        self.assertEqual(payload["status"], "ok")
        self.assertFalse(payload["result"]["passed"])


class RealMediaDeliveryQATests(unittest.TestCase):
    @unittest.skipUnless(REAL_TOOLS.ffmpeg.path, "a local ffmpeg is not discoverable")
    def test_real_lightweight_media_decodes_and_verifies(self):
        ffmpeg = REAL_TOOLS.ffmpeg.path
        ffprobe = REAL_TOOLS.ffprobe.path
        self.assertIsNotNone(ffmpeg)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "real delivery.mp4"
            command = [
                str(ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=32x32:r=10:d=1",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=8000:cl=mono:d=1",
                "-shortest",
                "-c:v",
                "mpeg4",
                "-c:a",
                "aac",
                "-y",
                str(source),
            ]
            completed = __import__("subprocess").run(command, check=False, capture_output=True, text=True)
            if completed.returncode != 0:
                self.skipTest("available ffmpeg cannot make the lightweight test media")
            result = rrv_qa.verify_delivery(
                source,
                expected_width=32,
                expected_height=32,
                expected_fps=10,
                expected_frames=10,
                expect_audio=True,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                timeout_seconds=20,
            )
        self.assertTrue(result["passed"], result)


if __name__ == "__main__":
    unittest.main()
