import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIRECTORY = REPO_ROOT / "skills" / "reference-video-rebuilder" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIRECTORY))

import rrv_analyze  # noqa: E402
import rrv_compile  # noqa: E402
import rrv_runtime  # noqa: E402
import video_remix  # noqa: E402


def media(*, audio=True, duration=10, fps=10.0):
    streams = [
        {
            "type": "video",
            "width": 100,
            "height": 200,
            "frame_rate": fps,
            "average_frame_rate": fps,
            "frame_count": duration,
        }
    ]
    if audio:
        streams.append({"type": "audio", "index": 1})
    return {"format": {"duration_seconds": duration / fps}, "streams": streams}


def exact_timing(*, frame_count=10, fps=10.0):
    return {
        "frame_count": frame_count,
        "frame_count_source": "ffprobe-nb_read_frames",
        "fps": fps,
        "pts_step": 1,
        "time_base": 1 / fps,
        "duration_seconds": frame_count / fps,
        "cfr_confirmed": True,
    }


def plan(*, timing_mode="uniform", audio_mode="mute", audio_rights=True, source_width=100):
    timing = {"slot_count": 2, "mode": timing_mode, "min_segment_frames": 1}
    if timing_mode == "manual":
        timing["switch_frames"] = [0, 4]
    return {
        "schema_version": "0.3.0",
        "template_id": "compiler-test",
        "family": "fixed-subject-carousel",
        "authorization": {
            "reference_rights_confirmed": True,
            "audio_rights_confirmed": audio_rights,
        },
        "privacy": "local-only",
        "geometry": {
            "source_rect": {"x": 0, "y": 0, "width": source_width, "height": 200},
            "carousel_rect": {"x": 0, "y": 0, "width": 100, "height": 40},
            "subject_rect": {"x": 0, "y": 40, "width": 100, "height": 160},
        },
        "timing": timing,
        "carousel": {"origin": {"x": 0, "y": 0}, "item_width": 50, "item_height": 40, "gap": 0},
        "background": {"color": "#ffffff", "replaceable": True},
        "audio": {"mode": audio_mode, "required": audio_mode != "mute"},
        "output_profiles": ["720x1280"],
        "analysis": {
            "width": 32,
            "snap_window_frames": 2,
            "min_prominence": 2,
            "max_evidence_frames": 2,
        },
    }


def tools(*, ffmpeg=True, ffprobe=True):
    return rrv_runtime.RuntimeTools(
        ffmpeg=rrv_runtime.ToolInfo("ffmpeg", "fake-ffmpeg" if ffmpeg else None, "explicit"),
        ffprobe=rrv_runtime.ToolInfo("ffprobe", "fake-ffprobe" if ffprobe else None, "explicit"),
    )


class CompilerCoreTests(unittest.TestCase):
    def test_balanced_ranges_assigns_remainder_to_earliest_segments(self):
        ranges = rrv_compile.balanced_ranges(347, 12)
        self.assertEqual(
            [ranges[0][0], *(end for _, end in ranges)],
            [0, 29, 58, 87, 116, 145, 174, 203, 232, 261, 290, 319, 347],
        )

    def test_manual_ranges_are_exact_and_template_is_schema_valid(self):
        source_media = media()
        document = rrv_compile.build_template(
            plan(timing_mode="manual"),
            source_media,
            "0" * 64,
            [0, 4],
            True,
        )
        self.assertEqual([event["frame"] for event in document["events"]], [0, 4])
        self.assertEqual(video_remix.validate_template_data(document), [])

    def test_direct_core_rejects_inconsistent_audio_and_long_template_id(self):
        preserve_optional = plan(audio_mode="preserve")
        preserve_optional["audio"]["required"] = False
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_compile.build_template(
                preserve_optional, media(), "0" * 64, [5], True
            )

        muted_required = plan(audio_mode="mute")
        muted_required["audio"]["required"] = True
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_compile.build_template(
                muted_required, media(), "0" * 64, [5], True
            )

        long_id = plan()
        long_id["template_id"] = "a" * 65
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_compile.build_template(long_id, media(), "0" * 64, [5], True)

    def test_direct_builder_executes_compiler_plan_schema_for_integer_geometry(self):
        invalid = plan()
        invalid["carousel"]["origin"]["x"] = 0.5
        with self.assertRaises(rrv_runtime.RRVError) as raised:
            rrv_compile.build_template(invalid, media(), "0" * 64, [5], True)
        self.assertEqual(raised.exception.code, rrv_runtime.ERR_INVALID_ARGUMENT)
        self.assertIn("JSON Schema", raised.exception.message)

    def test_media_metadata_without_an_exact_count_is_not_estimated(self):
        inexact = media()
        del inexact["streams"][0]["frame_count"]
        with self.assertRaises(rrv_runtime.RRVError) as raised:
            rrv_compile._media_info(inexact)
        self.assertEqual(raised.exception.code, rrv_runtime.ERR_CAPABILITY_UNAVAILABLE)
        self.assertEqual(raised.exception.details["capability"], "exact_cfr_frame_timing")

    def test_hybrid_uses_strong_nearby_mad_and_falls_back_when_threshold_is_not_met(self):
        snapped = rrv_compile._hybrid_timing(
            20,
            2,
            3,
            2,
            2,
            {8: 1.0, 9: 2.0, 10: 3.0, 11: 22.0, 12: 4.0},
        )
        self.assertEqual(snapped.switch_frames, (11,))
        self.assertFalse(snapped.review_required)

        fallback = rrv_compile._hybrid_timing(
            20,
            2,
            3,
            2,
            100,
            {10: 3.0, 11: 22.0},
        )
        self.assertEqual(fallback.switch_frames, (10,))
        self.assertEqual(fallback.fallback_frames, (10,))
        self.assertTrue(fallback.review_required)


class CompilerFilesystemTests(unittest.TestCase):
    def _source_and_root(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        source = base / "private source.mp4"
        source.write_bytes(b"reference bytes")
        root = base / "project"
        root.mkdir()
        return source, root

    def _compile(self, source, root, compiler_plan, **kwargs):
        with mock.patch.object(rrv_compile, "_probe_with_runner", return_value={"media": media()}), mock.patch.object(
            rrv_compile, "_exact_timing_with_runner", return_value=exact_timing()
        ), mock.patch.object(
            rrv_compile, "_pillow_available", return_value=False
        ):
            return rrv_compile.compile_reference(
                source,
                compiler_plan,
                root,
                tools(),
                template_validator=video_remix.validate_template_data,
                **kwargs,
            )

    def test_result_paths_are_relative_and_existing_target_is_never_overwritten(self):
        source, root = self._source_and_root()
        result = self._compile(source, root, plan(), output_dir="compiled")
        self.assertEqual(result["artifacts"]["template_ir"]["path"], "compiled/template.ir.json")
        self.assertTrue((root / "compiled" / "template.ir.json").is_file())
        self.assertNotIn(source.name, (root / "compiled" / "compile-report.json").read_text(encoding="utf-8"))

        with self.assertRaises(rrv_runtime.RRVError) as raised:
            self._compile(source, root, plan(), output_dir="compiled")
        self.assertEqual(raised.exception.code, rrv_runtime.ERR_OUTPUT_EXISTS)

    def test_bad_geometry_or_unauthorized_preserve_audio_creates_no_target(self):
        source, root = self._source_and_root()
        with mock.patch.object(rrv_compile, "_probe_with_runner", return_value={"media": media()}):
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_compile.compile_reference(
                    source,
                    plan(source_width=101),
                    root,
                    tools(),
                    output_dir="bad-geometry",
                    template_validator=lambda _template: [],
                )
        self.assertFalse((root / "bad-geometry").exists())

        with mock.patch.object(rrv_compile, "_probe_with_runner", return_value={"media": media(audio=False)}):
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_compile.compile_reference(
                    source,
                    plan(audio_mode="preserve", audio_rights=False),
                    root,
                    tools(),
                    output_dir="bad-audio",
                    template_validator=lambda _template: [],
                )
        self.assertFalse((root / "bad-audio").exists())

    def test_invalid_schema_fails_before_media_probe_or_output_creation(self):
        source, root = self._source_and_root()
        invalid = plan()
        invalid["geometry"]["source_rect"]["x"] = 0.5
        with mock.patch.object(rrv_compile, "_probe_with_runner") as probe:
            with self.assertRaises(rrv_runtime.RRVError) as raised:
                rrv_compile.compile_reference(
                    source,
                    invalid,
                    root,
                    tools(),
                    output_dir="invalid-schema",
                    template_validator=lambda _template: [],
                )
        self.assertEqual(raised.exception.code, rrv_runtime.ERR_INVALID_ARGUMENT)
        probe.assert_not_called()
        self.assertFalse((root / "invalid-schema").exists())

    def test_compile_publishes_ffprobe_counted_frames_not_duration_estimate(self):
        source, root = self._source_and_root()
        stale_media = media(duration=10, fps=10.0)
        stale_media["format"]["duration_seconds"] = 1.0
        with mock.patch.object(
            rrv_compile, "_probe_with_runner", return_value={"media": stale_media}
        ), mock.patch.object(
            rrv_compile, "_exact_timing_with_runner", return_value=exact_timing(frame_count=7, fps=10.0)
        ), mock.patch.object(rrv_compile, "_pillow_available", return_value=False):
            result = rrv_compile.compile_reference(
                source,
                plan(),
                root,
                tools(),
                output_dir="counted",
                template_validator=video_remix.validate_template_data,
            )
        document = json.loads(
            (root / result["artifacts"]["template_ir"]["path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(document["source"]["duration_frames"], 7)

    def test_hybrid_review_flag_is_persisted_in_template_ir(self):
        source, root = self._source_and_root()
        hybrid_plan = plan(timing_mode="hybrid")

        def write_gray(_command, output, _timeout, _runner, _label):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(bytes(32 * 64 * 10))

        with mock.patch.object(
            rrv_compile, "_probe_with_runner", return_value={"media": media()}
        ), mock.patch.object(
            rrv_compile, "_exact_timing_with_runner", return_value=exact_timing()
        ), mock.patch.object(
            rrv_compile, "_run_artifact", side_effect=write_gray
        ), mock.patch.object(rrv_compile, "_pillow_available", return_value=False):
            result = rrv_compile.compile_reference(
                source,
                hybrid_plan,
                root,
                tools(),
                output_dir="hybrid-review",
                template_validator=video_remix.validate_template_data,
            )

        document = json.loads(
            (root / result["artifacts"]["template_ir"]["path"]).read_text(encoding="utf-8")
        )
        self.assertTrue(result["review_required"])
        self.assertTrue(document["support"]["review_required"])
        self.assertTrue(document["support"]["warnings"])

    def test_missing_ffprobe_fails_before_target_or_staging_is_created(self):
        source, root = self._source_and_root()
        with self.assertRaises(rrv_runtime.RRVError) as raised:
            rrv_compile.compile_reference(
                source,
                plan(),
                root,
                tools(ffprobe=False),
                output_dir="no-ffprobe",
                template_validator=lambda _template: [],
            )
        self.assertEqual(raised.exception.code, rrv_runtime.ERR_CAPABILITY_UNAVAILABLE)
        self.assertFalse((root / "no-ffprobe").exists())
        self.assertFalse(list(root.glob(".no-ffprobe.*-*")))

    def test_failure_after_stage_creation_is_cleaned_up_atomically(self):
        source, root = self._source_and_root()

        def failing_runner(_argv):
            raise rrv_runtime.RRVError(rrv_runtime.ERR_TOOL_EXECUTION, "deliberate failure")

        with mock.patch.object(rrv_compile, "_probe_with_runner", return_value={"media": media()}), mock.patch.object(
            rrv_compile, "_exact_timing_with_runner", return_value=exact_timing()
        ), mock.patch.object(
            rrv_compile, "_pillow_available", return_value=False
        ):
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_compile.compile_reference(
                    source,
                    plan(audio_mode="preserve"),
                    root,
                    tools(),
                    output_dir="failed",
                    template_validator=lambda _template: [],
                    runner=failing_runner,
                )
        self.assertFalse((root / "failed").exists())
        self.assertFalse(list(root.glob(".failed.staging-*")))


class CompilerCommandTests(unittest.TestCase):
    def test_ffmpeg_commands_are_argv_safe_and_strip_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source ; no shell.mp4"
            source.write_bytes(b"source")
            raw = Path(directory) / "raw output.gray"
            command = rrv_compile.build_grayscale_extraction_command(
                source,
                "ffmpeg; still-an-argv",
                {"x": 0, "y": 0, "width": 32, "height": 64},
                32,
                64,
                2,
                raw,
            )
            self.assertIsInstance(command, list)
            self.assertEqual(command[0], "ffmpeg; still-an-argv")
            self.assertEqual(command[command.index("-i") + 1], str(source.resolve()))
            self.assertIn("-n", command)
            self.assertEqual(command[command.index("-map_metadata") + 1], "-1")
            self.assertEqual(command[command.index("-map_chapters") + 1], "-1")

            evidence = rrv_compile.build_evidence_frame_extraction_command(
                source,
                "ffmpeg",
                {"x": 3, "y": 5, "width": 20, "height": 30},
                7,
                Path(directory) / "evidence.png",
            )
            evidence_filter = evidence[evidence.index("-vf") + 1]
            self.assertIn("select=eq(n\\,7)", evidence_filter)
            self.assertIn("crop=w=20:h=30:x=3:y=5", evidence_filter)
            self.assertEqual(evidence[evidence.index("-map_metadata") + 1], "-1")
            self.assertEqual(evidence[evidence.index("-map_chapters") + 1], "-1")

            audio = rrv_analyze.build_audio_extraction_command(source, "ffmpeg", raw)
            self.assertEqual(audio[audio.index("-fflags") + 1], "+bitexact")
            self.assertEqual(audio[audio.index("-map_metadata") + 1], "-1")
            self.assertEqual(audio[audio.index("-map_chapters") + 1], "-1")


if __name__ == "__main__":
    unittest.main()
