import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "skills" / "rebuild-reference-video"


class SkillMetadataTests(unittest.TestCase):
    def test_skill_frontmatter_and_references(self):
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("TODO", text)
        match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        self.assertIsNotNone(match)
        frontmatter = match.group(1)
        self.assertRegex(frontmatter, r"(?m)^name: rebuild-reference-video$")
        self.assertRegex(frontmatter, r"(?m)^description: .{80,}$")
        for reference in re.findall(r"\]\(references/([^)]+)\)", text):
            self.assertTrue((SKILL_ROOT / "references" / reference).is_file(), reference)

    def test_openai_metadata_matches_skill(self):
        text = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn('display_name: "参考视频重建器"', text)
        self.assertIn('$rebuild-reference-video', text)


if __name__ == "__main__":
    unittest.main()
