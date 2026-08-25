import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "skills" / "reference-video-rebuilder"


class SkillMetadataTests(unittest.TestCase):
    def test_skill_frontmatter_and_references(self):
        self.assertEqual(SKILL_ROOT.name, "reference-video-rebuilder")
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("TODO", text)
        match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        self.assertIsNotNone(match)
        frontmatter = match.group(1)
        self.assertRegex(frontmatter, r"(?m)^name: reference-video-rebuilder$")
        self.assertRegex(frontmatter, r"(?m)^description: .{80,}$")
        for reference in re.findall(r"\]\(references/([^)]+)\)", text):
            self.assertTrue((SKILL_ROOT / "references" / reference).is_file(), reference)

    def test_openai_metadata_matches_skill(self):
        text = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn('display_name: "reference-video-rebuilder"', text)
        self.assertIn('$reference-video-rebuilder', text)

    def test_private_generation_staging_directories_are_ignored(self):
        text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        for pattern in (
            ".rrv-generation-plan-*/",
            ".rrv-generation-results-proposal-*/",
            ".rrv-generation-asset-pack-*/",
            ".rrv-openai-generation-*/",
        ):
            self.assertIn(pattern, text)

    def test_faithful_evidence_and_nle_outputs_are_ignored(self):
        text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        for pattern in (
            "faithful-rebuild*/",
            "faithful-evidence*/",
            "jianying-delivery*/",
            ".rrv-faithful-*/",
            ".rrv-faithful-evidence-*/",
            ".rrv-nle-*/",
        ):
            self.assertIn(pattern, text)

    def test_documented_skill_root_cli_invocation_works_from_an_arbitrary_cwd(self):
        for document in (
            SKILL_ROOT / "SKILL.md",
            SKILL_ROOT / "references" / "faithful-rebuild-contract.md",
            SKILL_ROOT / "references" / "nle-delivery-contract.md",
        ):
            self.assertIn("<skill-root>/scripts/video_remix.py", document.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(SKILL_ROOT / "scripts" / "video_remix.py"), "--version"],
                cwd=directory,
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0.9.1-alpha", result.stdout)

    def test_codex_builtin_imagegen_example_is_cloud_managed_and_keyless(self):
        example = SKILL_ROOT / "assets" / "project-template" / "generation.request.codex-builtin.example.json"
        data = json.loads(example.read_text(encoding="utf-8"))
        self.assertEqual(data["privacy_profile"], "controller-cloud")
        self.assertEqual(data["execution_profile"], "controller-managed")
        self.assertEqual(data["adapter_id"], "codex-builtin-imagegen")
        self.assertEqual(data["adapter_version"], "2026-08-24")
        self.assertIs(data["cloud_upload_confirmed"], True)
        expected_slots = {"audio", "model.identity"}
        expected_slots.update(f"outfit.{index:02d}" for index in range(1, 13))
        expected_slots.update(f"product.{index:02d}" for index in range(1, 13))
        self.assertEqual({task["target_slot_id"] for task in data["tasks"]}, expected_slots)
        self.assertEqual(sum(not task["passthrough"] for task in data["tasks"]), 12)
        serialized = json.dumps(data, sort_keys=True).lower()
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("openai_api_key", serialized)


if __name__ == "__main__":
    unittest.main()
