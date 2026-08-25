import hashlib
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
SCRIPTS = REPO_ROOT / "skills" / "reference-video-rebuilder" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import rrv_nle  # noqa: E402
import rrv_runtime  # noqa: E402


def _tool_paths() -> tuple[str | None, str | None]:
    env_ffmpeg = os.environ.get("RRV_TEST_FFMPEG") or os.environ.get("RRV_FFMPEG")
    env_ffprobe = os.environ.get("RRV_TEST_FFPROBE") or os.environ.get("RRV_FFPROBE")
    if env_ffmpeg and env_ffprobe and Path(env_ffmpeg).is_file() and Path(env_ffprobe).is_file():
        return env_ffmpeg, env_ffprobe
    return shutil.which("ffmpeg"), shutil.which("ffprobe")


REAL_FFMPEG, REAL_FFPROBE = _tool_paths()


class NLECommandTests(unittest.TestCase):
    def test_transcode_command_is_argv_only_and_pins_the_frozen_profile(self):
        source = "C:/safe/input;not-a-command.mp4"
        output = "C:/safe/output;still-literal.mp4"
        command = rrv_nle.build_nle_transcode_command(
            source, output, "ffmpeg.exe", fps=30, has_audio=True
        )
        self.assertIsInstance(command, list)
        self.assertEqual(command[command.index("-i") + 1], source)
        self.assertEqual(command[-1], output)
        self.assertEqual(command[command.index("-c:v") + 1], "libx264")
        self.assertEqual(command[command.index("-profile:v") + 1], "high")
        self.assertEqual(command[command.index("-pix_fmt") + 1], "yuv420p")
        self.assertEqual(command[command.index("-crf") + 1], "18")
        self.assertEqual(command[command.index("-preset") + 1], "medium")
        self.assertEqual(command[command.index("-r") + 1], "30")
        self.assertEqual(command[command.index("-fps_mode") + 1], "cfr")
        self.assertEqual(command[command.index("-c:a") + 1], "aac")
        self.assertEqual(command[command.index("-profile:a") + 1], "aac_low")
        self.assertEqual(command[command.index("-ar") + 1], "48000")
        self.assertEqual(command[command.index("-ac") + 1], "2")
        self.assertEqual(command[command.index("-movflags") + 1], "+faststart")
        self.assertIn("-map_metadata", command)
        self.assertIn("-map_chapters", command)
        self.assertNotIn("shell", " ".join(command).lower())

    def test_full_decode_command_decodes_optional_audio_without_writing_a_file(self):
        command = rrv_nle.build_full_decode_command("delivery.mp4", "ffmpeg.exe")
        self.assertEqual(command[command.index("-i") + 1], "delivery.mp4")
        self.assertIn("0:a?", command)
        self.assertEqual(command[-2:], ["null", "-"])


class NLEExecutionSafetyTests(unittest.TestCase):
    def test_rights_gate_is_zero_touch(self):
        with mock.patch.object(rrv_nle.rrv_faithful, "_safe_project_root") as safe_root, mock.patch.object(
            rrv_nle.rrv_runtime, "discover_tools"
        ) as discover:
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_nle.export_nle_delivery(
                    "not-used.mp4",
                    project_root="not-used",
                    rights_confirmed=False,
                )
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_nle.verify_nle_delivery(
                    "not-used.mp4",
                    project_root="not-used",
                    rights_confirmed=False,
                )
        safe_root.assert_not_called()
        discover.assert_not_called()

    def test_custom_runner_is_rejected_before_root_or_tool_access(self):
        with mock.patch.object(rrv_nle.rrv_faithful, "_safe_project_root") as safe_root, mock.patch.object(
            rrv_nle.rrv_runtime, "discover_tools"
        ) as discover:
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_nle.export_nle_delivery(
                    "source.mp4",
                    project_root="not-used",
                    rights_confirmed=True,
                    runner=lambda *_args, **_kwargs: None,
                )
        safe_root.assert_not_called()
        discover.assert_not_called()

    def test_path_escape_hardlink_and_existing_target_leave_no_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory)
            root = outer / "project"
            root.mkdir()
            outside = outer / "outside.mp4"
            outside.write_bytes(b"not decoded")

            with self.assertRaises(rrv_runtime.RRVError):
                rrv_nle.export_nle_delivery(
                    "../outside.mp4", project_root=root, rights_confirmed=True
                )
            self.assertFalse((root / "jianying-delivery").exists())

            try:
                os.link(outside, root / "linked.mp4")
            except OSError as exc:
                self.skipTest(f"the test host cannot create a hardlink: {exc}")
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_nle.export_nle_delivery(
                    "linked.mp4", project_root=root, rights_confirmed=True
                )
            self.assertFalse((root / "jianying-delivery").exists())

            source = root / "source.mp4"
            source.write_bytes(b"not decoded")
            target = root / "occupied"
            target.mkdir()
            marker = target / "keep.txt"
            marker.write_text("must survive", encoding="utf-8")
            with self.assertRaises(rrv_runtime.RRVError) as raised:
                rrv_nle.export_nle_delivery(
                    source, project_root=root, rights_confirmed=True, output_dir="occupied"
                )
            self.assertEqual(raised.exception.code, rrv_runtime.ERR_OUTPUT_EXISTS)
            self.assertEqual(marker.read_text(encoding="utf-8"), "must survive")


class NLESecurityRegressionTests(unittest.TestCase):
    @staticmethod
    def _input_facts() -> rrv_nle.MediaFacts:
        return rrv_nle.MediaFacts(
            format_name="mov,mp4,m4a,3gp,3g2,mj2",
            width=720,
            height=1280,
            fps=24.0,
            frame_count=24,
            duration_seconds=1.0,
            video_codec="h264",
            video_profile="Main",
            pixel_format="yuv420p",
            bit_depth=8,
            has_audio=False,
            audio_stream_count=0,
            audio_codec=None,
            audio_profile=None,
            audio_sample_rate=None,
            audio_channels=None,
            audio_channel_layout=None,
            rotation_degrees=(),
        )

    @staticmethod
    def _output_facts() -> rrv_nle.MediaFacts:
        return rrv_nle.MediaFacts(
            format_name="mov,mp4,m4a,3gp,3g2,mj2",
            width=720,
            height=1280,
            fps=24.0,
            frame_count=24,
            duration_seconds=1.0,
            video_codec="h264",
            video_profile="High",
            pixel_format="yuv420p",
            bit_depth=8,
            has_audio=False,
            audio_stream_count=0,
            audio_codec=None,
            audio_profile=None,
            audio_sample_rate=None,
            audio_channels=None,
            audio_channel_layout=None,
            rotation_degrees=(),
        )

    @staticmethod
    def _qa() -> dict:
        return {
            "full_decode": {
                "passed": True,
                "completed": True,
                "decoded_video_frames": 24,
                "decoded_audio": False,
                "audio_decode_applicable": False,
                "returncode": 0,
            },
            "checks": [],
        }

    def _export_with_fake_local_media(self, root: Path, source: Path, **extra):
        """Keep filesystem integrity boundaries real while replacing FFmpeg only."""

        def inspect(_root, identity, *, output_profile, **_kwargs):
            facts = self._output_facts() if output_profile else self._input_facts()
            return {}, {"cfr_confirmed": True}, facts

        def transcode(command, **_kwargs):
            input_path = Path(command[command.index("-i") + 1])
            Path(command[-1]).write_bytes(b"delivery:" + input_path.read_bytes())
            return rrv_runtime.CommandResult(tuple(command), 0, "", "")

        with mock.patch.object(
            rrv_nle,
            "_require_runtime_tools",
            return_value=(object(), "fake-ffmpeg", "fake-ffprobe"),
        ), mock.patch.object(rrv_nle, "_inspect_bound_media", side_effect=inspect), mock.patch.object(
            rrv_nle, "_run_local", side_effect=transcode
        ), mock.patch.object(
            rrv_nle, "_metadata_and_chapters_are_clear", return_value=True
        ), mock.patch.object(rrv_nle, "_faststart_verified", return_value=True), mock.patch.object(
            rrv_nle, "_full_decode_qa", side_effect=lambda *_args, **_kwargs: self._qa()
        ):
            return rrv_nle.export_nle_delivery(
                source,
                project_root=root,
                rights_confirmed=True,
                **extra,
            )

    def test_export_artifact_replacement_after_hash_prevents_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            source = root / "source.mp4"
            source.write_bytes(b"approved source")
            original_write = rrv_nle.rrv_propose._write_json_new
            tampered = False

            def write_report_then_replace_delivery(path, report, *, label, stage=None):
                nonlocal tampered
                original_write(path, report, label=label, stage=stage)
                if label == "NLE delivery report":
                    delivery = path.parent / rrv_nle.DELIVERY_FILENAME
                    attacker_copy = path.parent / "attacker-delivery.mp4"
                    attacker_copy.write_bytes(b"attacker replacement")
                    os.replace(attacker_copy, delivery)
                    tampered = True

            with mock.patch.object(
                rrv_nle.rrv_propose,
                "_write_json_new",
                side_effect=write_report_then_replace_delivery,
            ):
                with self.assertRaises(rrv_runtime.RRVError):
                    self._export_with_fake_local_media(root, source)

            self.assertTrue(tampered)
            self.assertFalse((root / "jianying-delivery").exists())

    def test_export_source_path_swap_uses_immutable_snapshot_and_rejects_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            source = root / "source.mp4"
            approved = b"approved source bytes"
            source.write_bytes(approved)
            observed_snapshot_inputs: list[tuple[str, bytes]] = []
            swapped = False

            def inspect(_root, _identity, *, output_profile, **_kwargs):
                return {}, {"cfr_confirmed": True}, (
                    self._output_facts() if output_profile else self._input_facts()
                )

            def transcode_and_swap_source(command, **_kwargs):
                nonlocal swapped
                input_path = Path(command[command.index("-i") + 1])
                observed_snapshot_inputs.append((input_path.name, input_path.read_bytes()))
                attacker = root / "attacker-source.mp4"
                attacker.write_bytes(b"attacker source bytes")
                os.replace(attacker, source)
                swapped = True
                Path(command[-1]).write_bytes(b"delivery:" + observed_snapshot_inputs[-1][1])
                return rrv_runtime.CommandResult(tuple(command), 0, "", "")

            with mock.patch.object(
                rrv_nle,
                "_require_runtime_tools",
                return_value=(object(), "fake-ffmpeg", "fake-ffprobe"),
            ), mock.patch.object(rrv_nle, "_inspect_bound_media", side_effect=inspect), mock.patch.object(
                rrv_nle, "_run_local", side_effect=transcode_and_swap_source
            ), mock.patch.object(
                rrv_nle, "_metadata_and_chapters_are_clear", return_value=True
            ), mock.patch.object(rrv_nle, "_faststart_verified", return_value=True), mock.patch.object(
                rrv_nle, "_full_decode_qa", side_effect=lambda *_args, **_kwargs: self._qa()
            ):
                with self.assertRaises(rrv_runtime.RRVError):
                    rrv_nle.export_nle_delivery(
                        source, project_root=root, rights_confirmed=True
                    )

            self.assertTrue(swapped)
            self.assertEqual(
                observed_snapshot_inputs, [("input-snapshot.mp4", approved)]
            )
            self.assertEqual(source.read_bytes(), b"attacker source bytes")
            self.assertFalse((root / "jianying-delivery").exists())

    def test_verify_path_replacement_is_blocked_or_detected_before_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            delivery = root / "delivery.mp4"
            original = b"approved delivery"
            delivery.write_bytes(original)
            swap = {"blocked": False, "replaced": False}

            def inspect(_root, _identity, *, output_profile, **_kwargs):
                self.assertTrue(output_profile)
                attacker = root / "attacker-delivery.mp4"
                attacker.write_bytes(b"attacker delivery")
                try:
                    os.replace(attacker, delivery)
                except OSError:
                    swap["blocked"] = True
                else:
                    swap["replaced"] = True
                return {}, {"cfr_confirmed": True}, self._output_facts()

            with mock.patch.object(
                rrv_nle,
                "_require_runtime_tools",
                return_value=(object(), "fake-ffmpeg", "fake-ffprobe"),
            ), mock.patch.object(rrv_nle, "_inspect_bound_media", side_effect=inspect), mock.patch.object(
                rrv_nle, "_metadata_and_chapters_are_clear", return_value=True
            ), mock.patch.object(rrv_nle, "_faststart_verified", return_value=True), mock.patch.object(
                rrv_nle, "_full_decode_qa", side_effect=lambda *_args, **_kwargs: self._qa()
            ):
                try:
                    result = rrv_nle.verify_nle_delivery(
                        delivery,
                        project_root=root,
                        rights_confirmed=True,
                    )
                except rrv_runtime.RRVError:
                    self.assertTrue(swap["replaced"])
                else:
                    self.assertTrue(swap["blocked"])
                    self.assertFalse(swap["replaced"])
                    self.assertEqual(result["output"]["sha256"], hashlib.sha256(original).hexdigest())


@unittest.skipUnless(REAL_FFMPEG and REAL_FFPROBE, "portable FFmpeg and FFprobe are unavailable")
class RealNLEIntegrationTests(unittest.TestCase):
    def _source(self, root: Path, *, audio: bool) -> Path:
        source = root / ("audio-source.mp4" if audio else "silent-source.mp4")
        command = [
            str(REAL_FFMPEG),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=720x1280:rate=24:duration=1",
        ]
        if audio:
            command.extend(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:sample_rate=44100:duration=1",
                    "-shortest",
                ]
            )
        else:
            command.append("-an")
        command.extend(["-c:v", "mpeg4"])
        if audio:
            command.extend(["-c:a", "aac"])
        command.extend(["-y", str(source)])
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
        return source

    def test_real_portable_ffmpeg_export_and_read_only_verification(self):
        self.assertIsNotNone(REAL_FFMPEG)
        self.assertIsNotNone(REAL_FFPROBE)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            source = self._source(root, audio=True)
            result = rrv_nle.export_nle_delivery(
                source,
                project_root=root,
                rights_confirmed=True,
                ffmpeg=REAL_FFMPEG,
                ffprobe=REAL_FFPROBE,
                timeout_seconds=30,
            )
            delivery = root / result["output"]["path"]
            report_path = root / result["report_path"]
            self.assertTrue(delivery.is_file())
            self.assertTrue(report_path.is_file())
            self.assertEqual(json.loads(report_path.read_text(encoding="utf-8")), result)
            self.assertEqual(result["completion"], "nle_compatible_derivative")
            self.assertFalse(result["bitstream_faithful"])
            self.assertEqual(result["profile"], "jianying-compatible-v1")
            self.assertEqual(result["input"]["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(result["output"]["sha256"], hashlib.sha256(delivery.read_bytes()).hexdigest())
            self.assertEqual(result["input_sha256"], result["input"]["sha256"])
            self.assertEqual(result["output_sha256"], result["output"]["sha256"])
            output = result["media_facts"]["output"]
            self.assertEqual(output["video_codec"], "h264")
            self.assertEqual(output["video_profile"], "High")
            self.assertEqual(output["pixel_format"], "yuv420p")
            self.assertEqual(output["bit_depth"], 8)
            self.assertEqual(output["fps"], 24)
            self.assertEqual(output["audio_codec"], "aac")
            self.assertEqual(output["audio_profile"], "LC")
            self.assertEqual(output["audio_sample_rate"], 48000)
            self.assertEqual(output["audio_channels"], 2)
            self.assertEqual(output["audio_channel_layout"], "stereo")
            self.assertTrue(result["qa"]["full_decode"]["passed"])
            self.assertEqual(result["qa"]["full_decode"]["decoded_video_frames"], 24)
            self.assertTrue(result["qa"]["profile_checks"]["faststart"])
            self.assertTrue(result["qa"]["profile_checks"]["metadata_cleared"])
            self.assertNotIn(str(root), json.dumps(result))

            verified = rrv_nle.verify_nle_delivery(
                result["output"]["path"],
                project_root=root,
                rights_confirmed=True,
                ffmpeg=REAL_FFMPEG,
                ffprobe=REAL_FFPROBE,
                timeout_seconds=30,
            )
            self.assertTrue(verified["verified"])
            self.assertEqual(verified["output"]["sha256"], result["output"]["sha256"])
            self.assertNotIn(str(root), json.dumps(verified))

    def test_real_silent_source_stays_silent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            source = self._source(root, audio=False)
            result = rrv_nle.export_nle_delivery(
                "silent-source.mp4",
                project_root=root,
                rights_confirmed=True,
                ffmpeg=REAL_FFMPEG,
                ffprobe=REAL_FFPROBE,
                timeout_seconds=30,
            )
            output = result["media_facts"]["output"]
            self.assertFalse(output["has_audio"])
            self.assertEqual(output["audio_stream_count"], 0)
            self.assertFalse(result["qa"]["full_decode"]["audio_decode_applicable"])


if __name__ == "__main__":
    unittest.main()
