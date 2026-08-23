import re
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
        ):
            self.assertIn(pattern, text)


if __name__ == "__main__":
    unittest.main()
