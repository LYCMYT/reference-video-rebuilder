import copy
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIRECTORY = REPO_ROOT / "skills" / "reference-video-rebuilder" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIRECTORY))

import rrv_propose  # noqa: E402
import rrv_runtime  # noqa: E402


def fake_tools(*, ffmpeg=True, ffprobe=True):
    return rrv_runtime.RuntimeTools(
        ffmpeg=rrv_runtime.ToolInfo("ffmpeg", "fake-ffmpeg" if ffmpeg else None, "explicit"),
        ffprobe=rrv_runtime.ToolInfo("ffprobe", "fake-ffprobe" if ffprobe else None, "explicit"),
    )


def fake_media(*, rotation=None, audio=True):
    video = {
        "type": "video",
        "width": 576,
        "height": 1280,
        "frame_rate": 4.0,
        "average_frame_rate": 4.0,
        "frame_count": 8,
    }
    if rotation is not None:
        video["rotation_degrees"] = rotation
    streams = [video]
    if audio:
        streams.append({"type": "audio", "index": 1})
    return {"format": {"duration_seconds": 2.0}, "streams": streams}


def fake_exact(*, frame_count=8, fps=4.0, duration=2.0, confirmed=True):
    return {
        "frame_count": frame_count,
        "frame_count_source": "ffprobe-nb_read_frames",
        "fps": fps,
        "pts_step": 1,
        "time_base": 1 / fps,
        "duration_seconds": duration,
        "cfr_confirmed": confirmed,
    }


class ProposalHarness:
    """Fake bounded FFmpeg operations while retaining actual JSON/Pillow evidence."""

    def __init__(self, *, media=None, timing=None, fail_run=False):
        self.media = fake_media() if media is None else media
        self.timing = fake_exact() if timing is None else timing
        self.fail_run = fail_run
        self.commands = []

    def run(self, command, **_kwargs):
        self.commands.append(tuple(command))
        if self.fail_run:
            raise rrv_runtime.RRVError(rrv_runtime.ERR_TOOL_EXECUTION, "synthetic failure")
        if "-version" in command:
            return rrv_runtime.CommandResult(tuple(command), 0, "synthetic local tool 1.0\n", "")
        output = Path(command[-1])
        if "rawvideo" in command:
            filter_graph = command[command.index("-vf") + 1]
            match = re.search(r"scale=w=(\d+):h=(\d+)", filter_graph)
            assert match
            width, height = (int(item) for item in match.groups())
            frames = int(command[command.index("-frames:v") + 1])
            raw = bytearray()
            for frame in range(frames):
                for row in range(height):
                    # A moving top strip makes the temporal boundary testable;
                    # one subject-region change supplies a hard-cut candidate.
                    if row < max(2, height // 5):
                        value = (30 + frame * 27) % 256
                    else:
                        value = 250 if frame < frames // 2 else 220
                    raw.extend(bytes([value]) * width)
            output.write_bytes(bytes(raw))
        else:
            from PIL import Image, ImageDraw

            image = Image.new("RGB", (576, 1024), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((0, 830, 575, 1023), fill="black")
            draw.rectangle((0, 0, 575, 180), fill=(220, 220, 220))
            image.save(output, format="PNG")
            image.close()
        return rrv_runtime.CommandResult(tuple(command), 0, "", "")

    def run_pipe(self, command, output_handle, _timeout_seconds):
        self.commands.append(tuple(command))
        if self.fail_run:
            raise rrv_runtime.RRVError(rrv_runtime.ERR_TOOL_EXECUTION, "synthetic failure")
        if "rawvideo" in command:
            filter_graph = command[command.index("-vf") + 1]
            match = re.search(r"scale=w=(\d+):h=(\d+)", filter_graph)
            assert match
            width, height = (int(item) for item in match.groups())
            frames = int(command[command.index("-frames:v") + 1])
            raw = bytearray()
            for frame in range(frames):
                for row in range(height):
                    if row < max(2, height // 5):
                        value = (30 + frame * 27) % 256
                    else:
                        value = 250 if frame < frames // 2 else 220
                    raw.extend(bytes([value]) * width)
            output_handle.write(bytes(raw))
        else:
            from PIL import Image, ImageDraw

            image = Image.new("RGB", (576, 1024), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((0, 830, 575, 1023), fill="black")
            draw.rectangle((0, 0, 575, 180), fill=(220, 220, 220))
            encoded = io.BytesIO()
            image.save(encoded, format="PNG")
            image.close()
            output_handle.write(encoded.getvalue())
        return None

    def patches(self, *, tools=None):
        runtime_tools = fake_tools() if tools is None else tools
        return (
            mock.patch.object(rrv_propose.rrv_runtime, "discover_tools", return_value=runtime_tools),
            mock.patch.object(
                rrv_propose.rrv_runtime,
                "probe_media",
                return_value={"probe": {"backend": "fake"}, "media": copy.deepcopy(self.media)},
            ),
            mock.patch.object(
                rrv_propose.rrv_runtime,
                "probe_exact_video_timing",
                return_value=copy.deepcopy(self.timing),
            ),
            mock.patch.object(rrv_propose.rrv_runtime, "run_command", side_effect=self.run),
            mock.patch.object(rrv_propose, "_run_argv_to_open_file", side_effect=self.run_pipe),
        )


class ProposalTestCase(unittest.TestCase):
    def make_workspace(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        source = base / "private-person-reference.mp4"
        source.write_bytes(b"authorized immutable source bytes")
        root = base / "project"
        root.mkdir()
        return source, root

    def propose(self, source, root, harness=None, **kwargs):
        harness = harness or ProposalHarness()
        patches = harness.patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = rrv_propose.propose_reference(
                source,
                project_root=root,
                template_id="proposal-test",
                reference_rights_confirmed=True,
                audio_rights_confirmed=True,
                **kwargs,
            )
        return result, harness

    def proposal_data(self, root, result):
        return json.loads((root / result["artifacts"]["proposal"]["path"]).read_text(encoding="utf-8"))

    def review_data(self, root, result):
        return json.loads((root / result["artifacts"]["review_template"]["path"]).read_text(encoding="utf-8"))

    def test_pure_heuristics_use_centered_crop_and_deterministic_peaks(self):
        self.assertEqual(
            rrv_propose._centered_source_rect(576, 1280, ("720x1280",)),
            {"x": 0, "y": 128, "width": 576, "height": 1024},
        )
        peaks = rrv_propose._transition_peaks({1: 0.01, 2: 0.02, 3: 0.41, 4: 0.02, 5: 0.01}, 10)
        self.assertEqual([item["frame"] for item in peaks], [3])
        candidates, selected = rrv_propose._slot_count_candidates(
            peaks=peaks, frame_count=20, fps=10, hint=None
        )
        self.assertEqual(selected, 2)
        self.assertEqual(candidates[0]["value"], 2)
        mode, starts, confidence = rrv_propose._manual_timing(peaks, 2, 20)
        self.assertEqual(mode, "manual")
        self.assertEqual(starts, [0, 3])
        self.assertGreater(confidence, 0)

    def test_proposal_is_private_relative_schema_valid_and_writes_review_template(self):
        source, root = self.make_workspace()
        before = source.read_bytes()
        result, harness = self.propose(source, root)
        proposal = self.proposal_data(root, result)
        review = self.review_data(root, result)

        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(proposal["schema_version"], "0.4.0")
        self.assertTrue(proposal["review_required"])
        self.assertEqual(proposal["candidate_plan"]["geometry"]["source_rect"], {"x": 0, "y": 128, "width": 576, "height": 1024})
        self.assertEqual(proposal["candidate_plan"]["background"]["color"], "#FFFFFF")
        self.assertEqual(rrv_propose.validate_proposal_data(proposal), [])
        self.assertEqual(rrv_propose.validate_review_data(review), [])
        self.assertEqual(review["decision"], "pending")
        self.assertFalse(review["reviewer_confirmed"])
        self.assertTrue(all(value is False for value in review["confirmations"].values()))
        proposal_path = root / result["artifacts"]["proposal"]["path"]
        self.assertEqual(review["proposal_sha256"], rrv_propose.rrv_analyze.sha256_file(proposal_path))
        raw_json = proposal_path.read_text(encoding="utf-8")
        self.assertNotIn(source.name, raw_json)
        self.assertNotIn(str(source.resolve()), raw_json)
        for artifact in result["artifacts"].values():
            self.assertFalse(Path(artifact["path"]).is_absolute())
            self.assertTrue((root / artifact["path"]).is_file())
        self.assertTrue(all(isinstance(command, tuple) for command in harness.commands))
        self.assertFalse((root / result["output_dir"] / "frames").exists())

    def test_nested_plan_rejection_and_privacy_path_invariants(self):
        source, root = self.make_workspace()
        result, _ = self.propose(source, root)
        proposal = self.proposal_data(root, result)
        proposal["candidate_plan"]["unexpected"] = True
        errors = rrv_propose.validate_proposal_data(proposal)
        self.assertTrue(any("Additional properties" in error for error in errors), errors)
        proposal = self.proposal_data(root, result)
        proposal["evidence"]["artifacts"]["overview_contact_sheet"]["path"] = "../escape.jpg"
        errors = rrv_propose.validate_proposal_data(proposal)
        self.assertTrue(any("does not match" in error or "escape" in error for error in errors), errors)
        review = self.review_data(root, result)
        review["approved_plan"] = {"schema_version": "0.3.0"}
        errors = rrv_propose.validate_review_data(review)
        self.assertTrue(errors)

    def test_rights_gate_runs_before_probe_or_output(self):
        source, root = self.make_workspace()
        with mock.patch.object(rrv_propose.rrv_runtime, "probe_media") as probe:
            with self.assertRaises(rrv_runtime.RRVError) as raised:
                rrv_propose.propose_reference(
                    source,
                    project_root=root,
                    template_id="proposal-test",
                    reference_rights_confirmed=False,
                    audio_rights_confirmed=True,
                )
        self.assertEqual(raised.exception.code, rrv_runtime.ERR_INVALID_ARGUMENT)
        probe.assert_not_called()
        self.assertFalse((root / "plan-proposal").exists())

    def test_tool_vfr_rotation_and_duration_gates_leave_no_output(self):
        source, root = self.make_workspace()
        no_tools = ProposalHarness()
        patches = no_tools.patches(tools=fake_tools(ffmpeg=False))
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with self.assertRaises(rrv_runtime.RRVError) as raised:
                rrv_propose.propose_reference(
                    source,
                    project_root=root,
                    template_id="proposal-test",
                    reference_rights_confirmed=True,
                    audio_rights_confirmed=True,
                    output_dir="missing-tool",
                )
        self.assertEqual(raised.exception.code, rrv_runtime.ERR_CAPABILITY_UNAVAILABLE)
        self.assertFalse((root / "missing-tool").exists())

        cases = [
            ("vfr", fake_media(), fake_exact(confirmed=False)),
            ("rotation", fake_media(rotation=90), fake_exact()),
            ("duration", fake_media(), fake_exact(frame_count=610, fps=10, duration=61)),
        ]
        for name, media, timing in cases:
            with self.subTest(name=name):
                harness = ProposalHarness(media=media, timing=timing)
                patches = harness.patches()
                with patches[0], patches[1], patches[2], patches[3], patches[4]:
                    with self.assertRaises(rrv_runtime.RRVError):
                        rrv_propose.propose_reference(
                            source,
                            project_root=root,
                            template_id="proposal-test",
                            reference_rights_confirmed=True,
                            audio_rights_confirmed=True,
                            output_dir=name,
                        )
                self.assertFalse((root / name).exists())

    def test_failure_during_staging_is_atomic(self):
        source, root = self.make_workspace()
        harness = ProposalHarness(fail_run=True)
        with self.assertRaises(rrv_runtime.RRVError):
            self.propose(source, root, harness=harness, output_dir="atomic-failure")
        self.assertFalse((root / "atomic-failure").exists())
        self.assertEqual(list(root.glob(".rrv-proposal-*")), [])

    def test_freeze_requires_exact_hash_approval_and_all_confirmations(self):
        source, root = self.make_workspace()
        result, _ = self.propose(source, root)
        proposal_path = root / result["artifacts"]["proposal"]["path"]
        review_path = root / result["artifacts"]["review_template"]["path"]
        proposal_relative = Path(result["artifacts"]["proposal"]["path"])
        review_relative = Path(result["artifacts"]["review_template"]["path"])
        review = self.review_data(root, result)

        with self.assertRaises(rrv_runtime.RRVError):
            rrv_propose.freeze_plan(proposal_relative, review_relative, project_root=root, output_dir="pending")
        self.assertFalse((root / "pending").exists())

        review.update({"decision": "approved", "reviewer_confirmed": True})
        for key in review["confirmations"]:
            review["confirmations"][key] = True
        review["confirmations"]["audio"] = False
        review_path.write_text(rrv_runtime.stable_json_dumps(review) + "\n", encoding="utf-8")
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_propose.freeze_plan(proposal_relative, review_relative, project_root=root, output_dir="missing-confirmation")
        self.assertFalse((root / "missing-confirmation").exists())

        review["confirmations"]["audio"] = True
        review["proposal_sha256"] = "0" * 64
        review_path.write_text(rrv_runtime.stable_json_dumps(review) + "\n", encoding="utf-8")
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_propose.freeze_plan(proposal_relative, review_relative, project_root=root, output_dir="bad-hash")
        self.assertFalse((root / "bad-hash").exists())

    def test_freeze_accepts_reviewer_override_and_reports_json_pointer_diff(self):
        source, root = self.make_workspace()
        result, _ = self.propose(source, root)
        proposal_path = root / result["artifacts"]["proposal"]["path"]
        review_path = root / result["artifacts"]["review_template"]["path"]
        proposal_relative = Path(result["artifacts"]["proposal"]["path"])
        review_relative = Path(result["artifacts"]["review_template"]["path"])
        review = self.review_data(root, result)
        review["decision"] = "approved"
        review["reviewer_confirmed"] = True
        review["confirmations"] = {key: True for key in review["confirmations"]}
        review["approved_plan"]["background"]["color"] = "#FDFDFD"
        review_path.write_text(rrv_runtime.stable_json_dumps(review) + "\n", encoding="utf-8")

        frozen = rrv_propose.freeze_plan(proposal_relative, review_relative, project_root=root)
        self.assertIn("/background/color", frozen["reviewer_override_paths"])
        report = json.loads((root / frozen["artifacts"]["freeze_report"]["path"]).read_text(encoding="utf-8"))
        self.assertIn("/background/color", report["changed_json_pointer_paths"])
        self.assertRegex(report["candidate_plan_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(report["approved_plan_sha256"], r"^[0-9a-f]{64}$")
        self.assertFalse(Path(frozen["output_dir"]).is_absolute())

    def test_equivalent_runs_in_distinct_projects_are_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "same.mp4"
            source.write_bytes(b"same authorized source")
            roots = [base / "one", base / "two"]
            for root in roots:
                root.mkdir()
            outputs = []
            for root in roots:
                result, _ = self.propose(source, root)
                proposal_path = root / result["artifacts"]["proposal"]["path"]
                outputs.append((proposal_path.read_bytes(), result))
            self.assertEqual(outputs[0][0], outputs[1][0])
            for key in ("overview_contact_sheet", "geometry_preview", "timing_profile"):
                self.assertEqual(
                    outputs[0][1]["artifacts"][key]["sha256"],
                    outputs[1][1]["artifacts"][key]["sha256"],
                )

    def _reparse_result(self, value):
        return SimpleNamespace(
            st_mode=value.st_mode,
            st_dev=value.st_dev,
            st_ino=value.st_ino,
            st_file_attributes=rrv_propose._FILE_ATTRIBUTE_REPARSE_POINT,
        )

    def test_absolute_packet_paths_are_rejected_before_candidate_lstat(self):
        """Freeze packet paths are strictly project-root relative, never probed remotely."""

        _, root = self.make_workspace()
        candidates = (
            root / "packets" / "proposal.json",
            r"\\server\share\packets\review.json",
        )
        for candidate in candidates:
            with self.subTest(candidate=str(candidate)), mock.patch.object(
                rrv_propose.os,
                "lstat",
                side_effect=AssertionError("absolute packet path must not be lstat'd"),
            ) as lstat:
                with self.assertRaises(rrv_runtime.RRVError) as raised:
                    rrv_propose._project_file_path(root, candidate, "packet")
            self.assertEqual(raised.exception.code, rrv_runtime.ERR_INVALID_ARGUMENT)
            lstat.assert_not_called()

    def test_freeze_rejects_absolute_local_and_unc_packet_arguments(self):
        source, root = self.make_workspace()
        result, _ = self.propose(source, root)
        proposal_path = root / result["artifacts"]["proposal"]["path"]
        proposal_relative = Path(result["artifacts"]["proposal"]["path"])
        review_path = root / result["artifacts"]["review_template"]["path"]
        review_relative = Path(result["artifacts"]["review_template"]["path"])
        candidates = (
            (proposal_path, review_relative),
            (proposal_relative, review_path),
            (r"\\server\share\proposal.json", review_relative),
            (proposal_relative, r"\\server\share\review.json"),
        )
        for index, (proposal, review) in enumerate(candidates):
            output_dir = f"absolute-packet-{index}"
            with self.subTest(proposal=str(proposal), review=str(review)), self.assertRaises(
                rrv_runtime.RRVError
            ) as raised:
                rrv_propose.freeze_plan(
                    proposal,
                    review,
                    project_root=root,
                    output_dir=output_dir,
                )
            self.assertEqual(raised.exception.code, rrv_runtime.ERR_INVALID_ARGUMENT)
            self.assertFalse((root / output_dir).exists())

    def _approved_review(self, root, result):
        review = self.review_data(root, result)
        review["decision"] = "approved"
        review["reviewer_confirmed"] = True
        review["confirmations"] = {key: True for key in review["confirmations"]}
        review_path = root / result["artifacts"]["review_template"]["path"]
        review_path.write_text(rrv_runtime.stable_json_dumps(review) + "\n", encoding="utf-8")
        return review_path

    def test_stage_reparse_replacement_never_writes_or_cleans_victim(self):
        source, root = self.make_workspace()
        victim = root / "victim"
        victim.mkdir()
        sentinel = victim / "keep.txt"
        sentinel.write_text("do not touch", encoding="utf-8")
        stage = rrv_propose._new_staging_directory(root, "test")
        original_lstat = os.lstat

        def reparse_stage(path):
            result = original_lstat(path)
            if Path(path) == stage.path:
                return self._reparse_result(result)
            return result

        with mock.patch.object(rrv_propose.os, "lstat", side_effect=reparse_stage):
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_propose._stage_path(root, stage, "frames/frame.png")
            rrv_propose._cleanup_directory(root, stage)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "do not touch")
        self.assertFalse((victim / "frames" / "frame.png").exists())
        self.assertTrue(stage.path.exists())
        rrv_propose._cleanup_directory(root, stage)

    @unittest.skipUnless(os.name == "nt", "Windows directory-handle guard is platform-specific")
    def test_windows_stage_guard_blocks_output_time_rename_before_pipe_write(self):
        """Exercise the exact former subprocess-path race without touching a victim."""

        source, root = self.make_workspace()
        outside = root.parent / "outside-victim"
        outside.mkdir()
        moved = root / "attacker-moved-stage"
        harness = ProposalHarness()
        attempted: list[bool] = []
        blocked: list[bool] = []

        def rename_at_output_time(command, output_handle, timeout_seconds):
            stages = list(root.glob(".rrv-proposal-*"))
            self.assertEqual(len(stages), 1)
            attempted.append(True)
            try:
                stages[0].rename(moved)
            except OSError:
                blocked.append(True)
            else:  # A missing guard would make the old junction attack viable.
                self.fail("the active staging directory was renameable during output")
            self.assertFalse((outside / "analysis.gray").exists())
            return harness.run_pipe(command, output_handle, timeout_seconds)

        patches = harness.patches()
        with patches[0], patches[1], patches[2], patches[3], mock.patch.object(
            rrv_propose, "_run_argv_to_open_file", side_effect=rename_at_output_time
        ):
            result = rrv_propose.propose_reference(
                source,
                project_root=root,
                template_id="proposal-test",
                reference_rights_confirmed=True,
                audio_rights_confirmed=True,
            )
        self.assertTrue(attempted)
        self.assertTrue(blocked)
        self.assertFalse(moved.exists())
        self.assertFalse((outside / "analysis.gray").exists())
        self.assertTrue((root / result["output_dir"]).is_dir())
        self.assertEqual(list(root.glob(".rrv-proposal-*")), [])

    def test_pipe_output_writes_exact_bytes_to_the_bound_stage_handle(self):
        _, root = self.make_workspace()
        stage = rrv_propose._new_staging_directory(root, "pipe")
        payload = rrv_propose._stage_path(root, stage, "payload.bin")
        try:
            with rrv_propose._open_stage_output_file(stage, payload, "pipe test") as handle:
                rrv_propose._run_argv_to_open_file(
                    [
                        sys.executable,
                        "-c",
                        "import sys; sys.stdout.buffer.write(bytes((0, 255, 1)))",
                    ],
                    handle,
                    5,
                )
            self.assertEqual(payload.read_bytes(), b"\x00\xff\x01")
        finally:
            rrv_propose._cleanup_directory(root, stage)
        self.assertFalse(stage.path.exists())

    def test_direct_child_output_contract_rejects_nested_parent_before_media_or_writes(self):
        source, root = self.make_workspace()
        with mock.patch.object(rrv_propose.rrv_runtime, "discover_tools") as discover:
            with self.assertRaises(rrv_runtime.RRVError) as raised:
                rrv_propose.propose_reference(
                    source,
                    project_root=root,
                    template_id="proposal-test",
                    reference_rights_confirmed=True,
                    audio_rights_confirmed=True,
                    output_dir="nested/proposal",
                )
        self.assertEqual(raised.exception.code, rrv_runtime.ERR_INVALID_ARGUMENT)
        discover.assert_not_called()
        self.assertFalse((root / "nested").exists())

        result, _ = self.propose(source, root)
        review_path = self._approved_review(root, result)
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_propose.freeze_plan(
                Path(result["artifacts"]["proposal"]["path"]),
                review_path.relative_to(root),
                project_root=root,
                output_dir="nested/frozen",
            )
        self.assertFalse((root / "nested").exists())

    def test_publish_rechecks_project_root_identity_before_direct_child_rename(self):
        source, root = self.make_workspace()
        victim = root / "victim"
        victim.mkdir()
        stage = rrv_propose._new_staging_directory(root, "publish")
        payload = rrv_propose._stage_path(root, stage, "payload.json")
        rrv_propose._write_json_new(payload, {"ok": True}, label="payload", stage=stage)
        original_lstat = os.lstat

        def reparse_root(path):
            result = original_lstat(path)
            if Path(path) == root:
                return self._reparse_result(result)
            return result

        with mock.patch.object(rrv_propose.os, "lstat", side_effect=reparse_root):
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_propose._publish_stage(root, stage, root / "published", label="test")
        self.assertFalse((root / "published").exists())
        self.assertTrue(victim.is_dir())
        rrv_propose._cleanup_directory(root, stage)

    def test_freeze_uses_one_duplicate_free_proposal_snapshot_for_hash_and_plan(self):
        source, root = self.make_workspace()
        result, _ = self.propose(source, root)
        proposal_path = root / result["artifacts"]["proposal"]["path"]
        review_path = self._approved_review(root, result)
        original_proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
        replacement = copy.deepcopy(original_proposal)
        replacement["candidate_plan"]["background"]["color"] = "#000000"
        original_loader = rrv_propose._load_json_snapshot

        def swap_after_snapshot(root_arg, value, label):
            snapshot = original_loader(root_arg, value, label)
            if label == "proposal":
                proposal_path.write_text(rrv_runtime.stable_json_dumps(replacement) + "\n", encoding="utf-8")
            return snapshot

        with mock.patch.object(rrv_propose, "_load_json_snapshot", side_effect=swap_after_snapshot):
            frozen = rrv_propose.freeze_plan(
                proposal_path.relative_to(root), review_path.relative_to(root), project_root=root
            )
        frozen_plan = json.loads((root / frozen["artifacts"]["compiler_plan"]["path"]).read_text(encoding="utf-8"))
        self.assertEqual(frozen_plan["background"]["color"], original_proposal["candidate_plan"]["background"]["color"])
        self.assertEqual(
            json.loads(proposal_path.read_text(encoding="utf-8"))["candidate_plan"]["background"]["color"],
            "#000000",
        )

    def test_freeze_rejects_nested_duplicate_json_and_reparse_evidence(self):
        source, root = self.make_workspace()
        result, _ = self.propose(source, root)
        proposal_path = root / result["artifacts"]["proposal"]["path"]
        review_path = root / result["artifacts"]["review_template"]["path"]
        original_proposal = proposal_path.read_bytes()
        original_review = review_path.read_bytes()

        proposal_path.write_text('{"packet":{"value":1,"value":2}}', encoding="utf-8")
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_propose.freeze_plan(
                proposal_path.relative_to(root),
                review_path.relative_to(root),
                project_root=root,
                output_dir="duplicate-proposal",
            )
        self.assertFalse((root / "duplicate-proposal").exists())

        proposal_path.write_bytes(original_proposal)
        review_path.write_text('{"packet":{"value":1,"value":2}}', encoding="utf-8")
        with self.assertRaises(rrv_runtime.RRVError):
            rrv_propose.freeze_plan(
                proposal_path.relative_to(root),
                review_path.relative_to(root),
                project_root=root,
                output_dir="duplicate-review",
            )
        self.assertFalse((root / "duplicate-review").exists())

        review_path.write_bytes(original_review)
        approved_review = self._approved_review(root, result)
        proposal = self.proposal_data(root, result)
        evidence_path = root / proposal["evidence"]["artifacts"]["geometry_preview"]["path"]
        original_lstat = os.lstat
        evidence_stat = original_lstat(evidence_path)
        reparse_identities: set[tuple[int, int, int]] = set()

        def reparse_evidence(path):
            stat_result = original_lstat(path)
            # The bounded root may be returned in a Windows 8.3 spelling,
            # while this fixture used its long spelling.  Match the evidence
            # entry by filesystem identity so the mock always exercises the
            # intended no-reparse evidence gate.
            if (
                stat_result.st_dev == evidence_stat.st_dev
                and stat_result.st_ino == evidence_stat.st_ino
            ):
                replacement = self._reparse_result(stat_result)
                reparse_identities.add(
                    (replacement.st_dev, replacement.st_ino, replacement.st_file_attributes)
                )
                return replacement
            return stat_result

        with mock.patch.object(rrv_propose.os, "lstat", side_effect=reparse_evidence):
            with self.assertRaises(rrv_runtime.RRVError):
                rrv_propose.freeze_plan(
                    proposal_path.relative_to(root),
                    approved_review.relative_to(root),
                    project_root=root,
                    output_dir="reparse-evidence",
                )
        self.assertEqual(
            reparse_identities,
            {
                (
                    evidence_stat.st_dev,
                    evidence_stat.st_ino,
                    rrv_propose._FILE_ATTRIBUTE_REPARSE_POINT,
                )
            },
        )
        self.assertFalse((root / "reparse-evidence").exists())


class RealMediaProposalTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and FFprobe are unavailable")
    def test_small_cfr_media_can_propose_when_local_tools_are_available(self):
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        assert ffmpeg and ffprobe
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "fixture.mp4"
            project = base / "project"
            project.mkdir()
            subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=white:s=576x1280:r=5:d=1",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=500:sample_rate=8000:duration=1",
                    "-shortest",
                    "-c:v",
                    "mpeg4",
                    "-c:a",
                    "aac",
                    "-y",
                    str(source),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            result = rrv_propose.propose_reference(
                source,
                project_root=project,
                template_id="real-media",
                reference_rights_confirmed=True,
                audio_rights_confirmed=True,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                max_evidence_frames=4,
                timeout_seconds=30,
            )
            proposal = json.loads((project / result["artifacts"]["proposal"]["path"]).read_text(encoding="utf-8"))
            self.assertEqual(rrv_propose.validate_proposal_data(proposal), [])


if __name__ == "__main__":
    unittest.main()
