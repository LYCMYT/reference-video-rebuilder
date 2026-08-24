import copy
import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skills" / "reference-video-rebuilder" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import rrv_faithful  # noqa: E402
import rrv_runtime  # noqa: E402


def _tools() -> rrv_runtime.RuntimeTools:
    return rrv_runtime.RuntimeTools(
        ffmpeg=rrv_runtime.ToolInfo("ffmpeg", "fake ffmpeg.exe", "explicit"),
        ffprobe=rrv_runtime.ToolInfo("ffprobe", "fake ffprobe.exe", "explicit"),
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _plan(data: bytes, *, audio_mode: str = "preserve-bitstream") -> dict:
    return {
        "schema_version": "0.9.0",
        "rights_confirmed": True,
        "operation": "faithful-reference-rebuild",
        "source": {
            "path": "source.mp4",
            "sha256": _sha(data),
            "width": 1280,
            "height": 720,
            "fps": 30.0,
            "frame_count": 30,
            "duration_seconds": 1.0,
            "has_audio": True,
        },
        "visible_text_policy": "preserve-exact",
        "text_inventory": [
            {
                "id": "text.brand",
                "start_frame": 0,
                "end_frame": 30,
                "lines": ["Example product"],
                "region": {"x": 20, "y": 20, "width": 300, "height": 50},
                "human_reviewed": True,
            }
        ],
        "video_mode": "preserve-bitstream",
        "audio_mode": audio_mode,
        "metadata": {"strip_all": True},
    }


def _probe(*, has_audio: bool = True, width: int = 1280, height: int = 720) -> dict:
    streams = [
        {
            "type": "video",
            "codec_name": "h264",
            "width": width,
            "height": height,
            "frame_rate": 30.0,
            "average_frame_rate": 30.0,
            "frame_count": 30,
            "rotation_degrees": 0,
        }
    ]
    if has_audio:
        streams.append({"type": "audio", "codec_name": "aac"})
    return {
        "probe": {"backend": "ffprobe", "capability_level": "full", "limitations": []},
        "media": {"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"}, "streams": streams},
    }


def _timing(*_args, **_kwargs) -> dict:
    return {
        "cfr_confirmed": True,
        "fps": 30.0,
        "frame_count": 30,
        "duration_seconds": 1.0,
    }


def _payload(_path, _ffprobe, selector, **_kwargs) -> rrv_faithful.PayloadHash:
    return rrv_faithful.PayloadHash(_sha(selector.encode("ascii")), 7 if selector == "v:0" else 4)


def _no_metadata(*_args, **_kwargs) -> dict:
    return {
        "format": {
            "tags": {
                "major_brand": "isom",
                "minor_version": "512",
                "compatible_brands": "isomiso2avc1mp41",
                "encoder": "Lavf63.1.101",
            }
        },
        "streams": [
            {"codec_type": "video", "tags": {"language": "und", "handler_name": "VideoHandler"}},
            {"codec_type": "audio", "tags": {"language": "und", "handler_name": "SoundHandler"}},
        ],
    }


class FaithfulPlanContractTests(unittest.TestCase):
    def test_schema_rejects_unknown_nonfinite_and_duplicate_json_keys(self):
        plan = _plan(b"source")
        plan["unexpected"] = True
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_faithful.validate_faithful_plan(plan)

        plan = _plan(b"source")
        plan["source"]["fps"] = float("nan")
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_faithful.validate_faithful_plan(plan)

        raw = json.dumps(_plan(b"source"))
        duplicate = raw[:-1] + ', "schema_version": "0.9.0"}'
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_faithful.load_plan_json(duplicate)

    def test_text_inventory_requires_bounded_half_open_ranges_coordinates_and_unique_ids(self):
        cases = []
        end_overflow = _plan(b"source")
        end_overflow["text_inventory"][0]["end_frame"] = 31
        cases.append(end_overflow)

        coordinate_escape = _plan(b"source")
        coordinate_escape["text_inventory"][0]["region"]["x"] = 1200
        cases.append(coordinate_escape)

        duplicate = _plan(b"source")
        duplicate["text_inventory"].append(copy.deepcopy(duplicate["text_inventory"][0]))
        cases.append(duplicate)

        partially_reviewed = _plan(b"source")
        second_item = copy.deepcopy(partially_reviewed["text_inventory"][0])
        second_item["id"] = "text.second"
        second_item["human_reviewed"] = False
        partially_reviewed["text_inventory"].append(second_item)
        cases.append(partially_reviewed)

        for plan in cases:
            with self.subTest(plan=plan["text_inventory"]):
                with self.assertRaises(rrv_runtime.RRVError):
                    rrv_faithful.validate_faithful_plan(plan)

    def test_packet_hash_normalizes_stream_index_but_binds_timing(self):
        packet = {
            "stream_index": 7,
            "pts": 100,
            "dts": 90,
            "duration": 10,
            "data_hash": "SHA256:" + ("ab" * 32),
        }
        baseline = rrv_faithful._payload_hash_from_ffprobe_json({"packets": [packet]})
        renumbered = dict(packet, stream_index=1)
        self.assertEqual(
            baseline,
            rrv_faithful._payload_hash_from_ffprobe_json({"packets": [renumbered]}),
        )
        retimed = dict(packet, pts=101)
        self.assertNotEqual(
            baseline,
            rrv_faithful._payload_hash_from_ffprobe_json({"packets": [retimed]}),
        )


class FaithfulExecutionSafetyTests(unittest.TestCase):
    def test_rights_gate_is_zero_touch(self):
        with mock.patch.object(rrv_faithful, "_safe_project_root") as safe_root, mock.patch.object(
            rrv_faithful.rrv_propose, "_new_staging_directory"
        ) as stage:
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_faithful.execute_faithful_rebuild({"rights_confirmed": False}, "not-used")
        safe_root.assert_not_called()
        stage.assert_not_called()

    def test_path_escape_and_reparse_source_are_rejected_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b"source bytes"
            (root / "source.mp4").write_bytes(payload)
            escaped = _plan(payload)
            escaped["source"]["path"] = "../source.mp4"
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_faithful.execute_faithful_rebuild(escaped, root)
            self.assertFalse((root / "faithful-rebuild").exists())

            # Exercise the same reparse-point branch deterministically even
            # when the host prohibits creating symlinks.
            with mock.patch.object(
                rrv_faithful,
                "_is_link_or_reparse",
                side_effect=lambda entry: stat.S_ISREG(entry.st_mode),
            ):
                with self.assertRaises(rrv_runtime.RRVError):
                    rrv_faithful.execute_faithful_rebuild(_plan(payload), root)
            self.assertFalse((root / "faithful-rebuild").exists())

    def test_hardlinked_source_is_rejected_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory)
            root = outer / "project"
            root.mkdir()
            outside = outer / "outside.mp4"
            payload = b"source bytes"
            outside.write_bytes(payload)
            try:
                os.link(outside, root / "source.mp4")
            except OSError as exc:
                self.skipTest(f"the test host cannot create a hardlink: {exc}")
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_faithful.execute_faithful_rebuild(_plan(payload), root)
            self.assertFalse((root / "faithful-rebuild").exists())

    def test_existing_final_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b"source bytes"
            (root / "source.mp4").write_bytes(payload)
            target = root / "occupied"
            target.mkdir()
            marker = target / "marker.txt"
            marker.write_text("keep", encoding="utf-8")
            with self.assertRaises(rrv_runtime.RRVError) as caught:
                rrv_faithful.execute_faithful_rebuild(_plan(payload), root, "occupied")
            self.assertEqual(caught.exception.code, rrv_runtime.ERR_OUTPUT_EXISTS)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_source_hash_drift_leaves_no_visible_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = b"source bytes"
            source = root / "source.mp4"
            source.write_bytes(original)
            calls = 0

            def drifting_probe(*_args, **_kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    source.write_bytes(b"changed bytes")
                return _probe()

            with self.assertRaises(rrv_runtime.RRVError):
                rrv_faithful.execute_faithful_rebuild(
                    _plan(original),
                    root,
                    tools=_tools(),
                    probe_media_fn=drifting_probe,
                    exact_timing_fn=_timing,
                )
            self.assertFalse((root / "faithful-rebuild").exists())

    def test_media_mismatch_leaves_no_visible_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b"source bytes"
            (root / "source.mp4").write_bytes(payload)
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_faithful.execute_faithful_rebuild(
                    _plan(payload),
                    root,
                    tools=_tools(),
                    probe_media_fn=lambda *_args, **_kwargs: _probe(width=720, height=1280),
                    exact_timing_fn=_timing,
                )
            self.assertFalse((root / "faithful-rebuild").exists())

    def test_commands_are_argv_only_and_keep_hostile_paths_literal(self):
        source = "C:/safe/semicolon;not-a-command.mp4"
        output = "C:/safe/output;still-literal.mp4"
        command = rrv_faithful.build_faithful_remux_command(
            source, output, "ffmpeg.exe", audio_mode="preserve-bitstream"
        )
        self.assertIsInstance(command, list)
        self.assertEqual(command[command.index("-i") + 1], source)
        self.assertEqual(command[-1], output)
        self.assertIn("-c:v", command)
        self.assertEqual(command[command.index("-c:v") + 1], "copy")
        self.assertIn("-c:a", command)
        self.assertEqual(command[command.index("-c:a") + 1], "copy")
        self.assertIn("-map_metadata", command)
        self.assertIn("-map_chapters", command)
        self.assertNotIn("shell", " ".join(command).lower())


class FaithfulEndToEndTests(unittest.TestCase):
    def _run(self, plan, root, *, captured_command=None):
        def probe(path, **_kwargs):
            muted_replica = plan["audio_mode"] == "mute" and Path(path).name == "replica.mp4"
            return _probe(has_audio=not muted_replica)

        def runner(command, **_kwargs):
            if captured_command is not None:
                captured_command.extend(command)
            source = Path(command[command.index("-i") + 1])
            Path(command[-1]).write_bytes(source.read_bytes())
            return rrv_runtime.CommandResult(tuple(command), 0, "", "")

        return rrv_faithful.execute_faithful_rebuild(
            plan,
            root,
            "delivery",
            tools=_tools(),
            runner=runner,
            probe_media_fn=probe,
            exact_timing_fn=_timing,
            payload_hash_fn=_payload,
            metadata_probe_fn=_no_metadata,
        )

    def test_bitstream_payload_hashes_match_and_metadata_comment_is_rejected(self):
        self.assertFalse(
            rrv_faithful.metadata_is_stripped(
                {"format": {"tags": {"comment": "private source note"}}, "streams": []}
            )
        )
        self.assertFalse(rrv_faithful.metadata_is_stripped({}))
        self.assertFalse(rrv_faithful.metadata_is_stripped({"format": {"tags": {}}, "streams": []}))
        self.assertFalse(
            rrv_faithful.metadata_is_stripped(
                {
                    "format": {"tags": {"encoder": "private source note"}},
                    "streams": [
                        {"codec_type": "video", "tags": {"handler_name": "private title"}}
                    ],
                }
            )
        )
        self.assertTrue(rrv_faithful.metadata_is_stripped(_no_metadata()))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b"source bytes"
            (root / "source.mp4").write_bytes(payload)
            command: list[str] = []
            summary = self._run(_plan(payload), root, captured_command=command)
            self.assertEqual(summary["completion"], "faithful_source_preservation")
            self.assertEqual(
                summary["payload_hashes"]["video"]["source"],
                summary["payload_hashes"]["video"]["replica"],
            )
            self.assertEqual(
                summary["payload_hashes"]["audio"]["source"],
                summary["payload_hashes"]["audio"]["replica"],
            )
            self.assertTrue((root / "delivery" / "replica.mp4").is_file())
            self.assertTrue((root / "delivery" / "rebuild-summary.json").is_file())
            self.assertEqual((root / "delivery" / "replica.mp4").read_bytes(), payload)
            self.assertIn("-map_metadata", command)
            self.assertIn("-map_chapters", command)

    def test_metadata_comment_prevents_atomic_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b"source bytes"
            (root / "source.mp4").write_bytes(payload)
            plan = _plan(payload)

            def probe(path, **_kwargs):
                return _probe(has_audio=True)

            def runner(command, **_kwargs):
                Path(command[-1]).write_bytes(Path(command[command.index("-i") + 1]).read_bytes())
                return rrv_runtime.CommandResult(tuple(command), 0, "", "")

            with self.assertRaises(rrv_runtime.RRVError):
                rrv_faithful.execute_faithful_rebuild(
                    plan,
                    root,
                    "bad-metadata",
                    tools=_tools(),
                    runner=runner,
                    probe_media_fn=probe,
                    exact_timing_fn=_timing,
                    payload_hash_fn=_payload,
                    metadata_probe_fn=lambda *_args, **_kwargs: {
                        "format": {"tags": {"comment": "still present"}},
                        "streams": [],
                    },
                )
            self.assertFalse((root / "bad-metadata").exists())

    def test_mute_removes_output_audio_but_preserves_video(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b"source bytes"
            (root / "source.mp4").write_bytes(payload)
            command: list[str] = []
            summary = self._run(_plan(payload, audio_mode="mute"), root, captured_command=command)
            self.assertEqual(summary["payload_hashes"]["audio"]["mode"], "mute")
            self.assertIsNone(summary["payload_hashes"]["audio"]["replica"])
            self.assertIn("-an", command)
            self.assertEqual(summary["text_inventory_count"], 1)


if __name__ == "__main__":
    unittest.main()
