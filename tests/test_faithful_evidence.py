import copy
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

import rrv_faithful_evidence  # noqa: E402
import rrv_runtime  # noqa: E402


def _tool_paths() -> tuple[str | None, str | None]:
    ffmpeg = os.environ.get("RRV_TEST_FFMPEG") or os.environ.get("RRV_FFMPEG")
    ffprobe = os.environ.get("RRV_TEST_FFPROBE") or os.environ.get("RRV_FFPROBE")
    if ffmpeg and ffprobe and Path(ffmpeg).is_file() and Path(ffprobe).is_file():
        return ffmpeg, ffprobe
    return shutil.which("ffmpeg"), shutil.which("ffprobe")


REAL_FFMPEG, REAL_FFPROBE = _tool_paths()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plan(source: Path, *, fps: int = 24, frames: int = 24) -> dict:
    return {
        "schema_version": "0.9.0",
        "rights_confirmed": True,
        "operation": "faithful-reference-rebuild",
        "source": {
            "path": source.name,
            "sha256": _sha(source),
            "width": 720,
            "height": 1280,
            "fps": fps,
            "frame_count": frames,
            "duration_seconds": frames / fps,
            "has_audio": False,
        },
        "visible_text_policy": "preserve-exact",
        "text_inventory": [
            {
                "id": "text.example",
                "start_frame": 0,
                "end_frame": frames,
                "lines": ["Example"],
                "region": {"x": 20, "y": 30, "width": 240, "height": 80},
                "human_reviewed": True,
            }
        ],
        "video_mode": "preserve-bitstream",
        "audio_mode": "preserve-bitstream",
        "metadata": {"strip_all": True},
    }


class EvidenceSelectionTests(unittest.TestCase):
    def test_selection_never_exceeds_one_or_an_exact_panel_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"source")
            one = _plan(source, fps=30, frames=300)
            self.assertEqual(
                len(rrv_faithful_evidence.select_evidence_frames(one, max_panels=1)["selected_frames"]),
                1,
            )

            exact = _plan(source, fps=30, frames=300)
            exact["text_inventory"] = []
            for index in range(24):
                exact["text_inventory"].append(
                    {
                        "id": f"text.{index:02d}",
                        "start_frame": index * 10,
                        "end_frame": index * 10 + 2,
                        "lines": [f"item {index}"],
                        "region": {"x": 0, "y": 0, "width": 10, "height": 10},
                        "human_reviewed": True,
                    }
                )
            self.assertEqual(
                len(rrv_faithful_evidence.select_evidence_frames(exact, max_panels=24)["selected_frames"]),
                24,
            )

    def test_selection_is_bounded_deterministic_and_reports_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"source")
            plan = _plan(source, fps=30, frames=300)
            plan["source"]["duration_seconds"] = 10
            plan["text_inventory"] = []
            for index in range(30):
                plan["text_inventory"].append(
                    {
                        "id": f"text.{index:02d}",
                        "start_frame": index * 10,
                        "end_frame": index * 10 + 2,
                        "lines": [f"item {index}"],
                        "region": {"x": 0, "y": 0, "width": 10, "height": 10},
                        "human_reviewed": True,
                    }
                )
            first = rrv_faithful_evidence.select_evidence_frames(plan)
            second = rrv_faithful_evidence.select_evidence_frames(copy.deepcopy(plan))
            self.assertEqual(first, second)
            self.assertEqual(len(first["selected_frames"]), 24)
            self.assertTrue(first["truncated"])
            self.assertEqual(first["inventory_without_midpoint_panel"], 6)

    def test_rights_gate_precedes_root_tools_and_pillow(self):
        with mock.patch.object(
            rrv_faithful_evidence.rrv_faithful, "_safe_project_root"
        ) as root, mock.patch.object(
            rrv_faithful_evidence.rrv_faithful, "_require_runtime_tools"
        ) as tools, mock.patch.object(rrv_faithful_evidence, "_pillow") as pillow:
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_faithful_evidence.build_faithful_evidence(
                    {"rights_confirmed": False}, "not-used"
                )
        root.assert_not_called()
        tools.assert_not_called()
        pillow.assert_not_called()


class EvidenceSecurityRegressionTests(unittest.TestCase):
    @staticmethod
    def _facts() -> object:
        return rrv_faithful_evidence.rrv_faithful.MediaFacts(
            width=720,
            height=1280,
            fps=24.0,
            frame_count=24,
            duration_seconds=1.0,
            has_audio=False,
            audio_stream_count=0,
            video_codec="h264",
            container="mp4",
        )

    def _run_with_fake_local_media(self, root: Path, source: Path, plan: dict, **extra):
        """Exercise real snapshot, identity, and publication code without FFmpeg."""

        def extract_frame(stage, command, output, *_args, **_kwargs):
            with rrv_faithful_evidence.rrv_propose._open_stage_output_file(
                stage, output, "fake faithful evidence frame"
            ) as handle:
                handle.write(b"frame")

        def write_sheet(stage, destination, *_args, **_kwargs):
            with rrv_faithful_evidence.rrv_propose._open_stage_output_file(
                stage, destination, "fake faithful review contact sheet"
            ) as handle:
                handle.write(b"contact sheet")

        patches = [
            mock.patch.object(
                rrv_faithful_evidence.rrv_faithful,
                "_require_runtime_tools",
                return_value=(object(), "fake-ffmpeg", "fake-ffprobe"),
            ),
            mock.patch.object(
                rrv_faithful_evidence.rrv_faithful,
                "_probe_facts",
                return_value=self._facts(),
            ),
            mock.patch.object(
                rrv_faithful_evidence.rrv_propose,
                "_run_output",
                side_effect=extract_frame,
            ),
            mock.patch.object(
                rrv_faithful_evidence,
                "_write_contact_sheet",
                side_effect=write_sheet,
            ),
            mock.patch.object(rrv_faithful_evidence, "_validate_report"),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            return rrv_faithful_evidence.build_faithful_evidence(plan, root, **extra)

    def _assert_contact_tamper_is_rejected(self, tamper) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            source = root / "source.mp4"
            source.write_bytes(b"approved source")
            plan = _plan(source)
            original_write = rrv_faithful_evidence.rrv_propose._write_json_new
            tampered = False

            def write_report_then_tamper(path, report, *, label, stage=None):
                nonlocal tampered
                original_write(path, report, label=label, stage=stage)
                if label == "faithful evidence report":
                    tamper(path.parent / "contact-sheet.png")
                    tampered = True

            with mock.patch.object(
                rrv_faithful_evidence.rrv_propose,
                "_write_json_new",
                side_effect=write_report_then_tamper,
            ):
                with self.assertRaises(rrv_runtime.RRVError):
                    self._run_with_fake_local_media(root, source, plan)

            self.assertTrue(tampered)
            self.assertFalse((root / "faithful-evidence").exists())

    def test_contact_sheet_overwrite_after_hash_prevents_publication(self):
        self._assert_contact_tamper_is_rejected(
            lambda contact: contact.write_bytes(b"attacker overwrite")
        )

    def test_contact_sheet_hardlink_after_hash_prevents_publication(self):
        def create_attacker_hardlink(contact: Path) -> None:
            try:
                os.link(contact, contact.with_name("attacker-contact-link.png"))
            except OSError as exc:
                self.skipTest(f"the test host cannot create a hardlink: {exc}")

        self._assert_contact_tamper_is_rejected(create_attacker_hardlink)

    def test_source_path_swap_after_snapshot_cannot_change_extracted_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            source = root / "source.mp4"
            approved = b"approved source bytes"
            source.write_bytes(approved)
            plan = _plan(source)
            observed_snapshot_inputs: list[tuple[str, bytes]] = []
            swapped = False

            def swap_source_during_extraction(stage, command, output, *args, **kwargs):
                nonlocal swapped
                input_path = Path(command[command.index("-i") + 1])
                observed_snapshot_inputs.append((input_path.name, input_path.read_bytes()))
                if not swapped:
                    attacker = root / "attacker-source.mp4"
                    attacker.write_bytes(b"attacker source bytes")
                    os.replace(attacker, source)
                    swapped = True
                with rrv_faithful_evidence.rrv_propose._open_stage_output_file(
                    stage, output, "fake faithful evidence frame"
                ) as handle:
                    handle.write(b"frame")

            with mock.patch.object(
                rrv_faithful_evidence.rrv_propose,
                "_run_output",
                side_effect=swap_source_during_extraction,
            ), mock.patch.object(
                rrv_faithful_evidence.rrv_faithful,
                "_require_runtime_tools",
                return_value=(object(), "fake-ffmpeg", "fake-ffprobe"),
            ), mock.patch.object(
                rrv_faithful_evidence.rrv_faithful,
                "_probe_facts",
                return_value=self._facts(),
            ), mock.patch.object(
                rrv_faithful_evidence,
                "_write_contact_sheet",
            ) as write_sheet, mock.patch.object(
                rrv_faithful_evidence,
                "_validate_report",
            ):
                with self.assertRaises(rrv_runtime.RRVError):
                    rrv_faithful_evidence.build_faithful_evidence(plan, root)

            self.assertTrue(swapped)
            self.assertGreater(len(observed_snapshot_inputs), 0)
            self.assertEqual(
                set(observed_snapshot_inputs), {("source-snapshot.mp4", approved)}
            )
            self.assertEqual(source.read_bytes(), b"attacker source bytes")
            self.assertFalse((root / "faithful-evidence").exists())


@unittest.skipUnless(REAL_FFMPEG and REAL_FFPROBE, "portable FFmpeg and FFprobe are unavailable")
class RealEvidenceIntegrationTests(unittest.TestCase):
    def _source(self, root: Path) -> Path:
        source = root / "source.mp4"
        subprocess.run(
            [
                str(REAL_FFMPEG),
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=720x1280:rate=24:duration=1",
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-y",
                str(source),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return source

    def test_real_evidence_is_metadata_free_hash_bound_and_explicitly_non_ocr(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            source = self._source(root)
            plan = _plan(source)
            result = rrv_faithful_evidence.build_faithful_evidence(
                plan,
                root,
                ffmpeg=REAL_FFMPEG,
                ffprobe=REAL_FFPROBE,
                timeout_seconds=30,
            )
            output = root / "faithful-evidence"
            sheet = output / "contact-sheet.png"
            report = output / "faithful-evidence.json"
            self.assertTrue(sheet.is_file())
            self.assertTrue(report.is_file())
            self.assertEqual(json.loads(report.read_text(encoding="utf-8")), result)
            self.assertEqual(result["claim"], "human_review_support_only")
            self.assertFalse(result["ocr_used"])
            self.assertEqual(result["source"]["sha256"], _sha(source))
            self.assertEqual(result["artifacts"]["contact_sheet"]["sha256"], _sha(sheet))
            self.assertIn("cannot prove", " ".join(result["limitations"]))
            self.assertGreater(result["artifacts"]["contact_sheet"]["panel_count"], 1)
            with Image.open(sheet) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.mode, "RGB")
                self.assertEqual(image.getexif(), {})
                self.assertFalse(any(key.lower() in {"comment", "description", "xml"} for key in image.info))
            self.assertFalse(any(root.glob(".rrv-faithful-evidence-*")))

    def test_wrong_source_hash_and_existing_output_publish_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            source = self._source(root)
            plan = _plan(source)
            plan["source"]["sha256"] = "0" * 64
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_faithful_evidence.build_faithful_evidence(
                    plan,
                    root,
                    ffmpeg=REAL_FFMPEG,
                    ffprobe=REAL_FFPROBE,
                )
            self.assertFalse((root / "faithful-evidence").exists())

            occupied = root / "occupied"
            occupied.mkdir()
            marker = occupied / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_faithful_evidence.build_faithful_evidence(
                    _plan(source),
                    root,
                    output_dir="occupied",
                    ffmpeg=REAL_FFMPEG,
                    ffprobe=REAL_FFPROBE,
                )
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
