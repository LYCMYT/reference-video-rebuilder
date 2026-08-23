import copy
import json
import unittest
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ImportError:  # Contract tests still collect in a minimal development environment.
    Draft202012Validator = None


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "skills" / "reference-video-rebuilder"
SCHEMA_PATH = SKILL_ROOT / "assets" / "schemas" / "compiler-plan.schema.json"
EXAMPLE_PATH = SKILL_ROOT / "assets" / "project-template" / "compiler.plan.example.json"


@unittest.skipUnless(Draft202012Validator is not None, "jsonschema is required for contract validation")
class CompilerPlanContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.example = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)

    def assert_valid(self, plan):
        errors = sorted(self.validator.iter_errors(plan), key=lambda error: list(error.path))
        self.assertEqual([], errors, "\n".join(error.message for error in errors))

    def assert_invalid(self, plan):
        errors = list(self.validator.iter_errors(plan))
        self.assertTrue(errors, "plan unexpectedly passed schema validation")

    def test_schema_is_draft_2020_12_and_strict_for_objects(self):
        self.assertEqual("https://json-schema.org/draft/2020-12/schema", self.schema["$schema"])

        def object_schemas(value):
            if isinstance(value, dict):
                if value.get("type") == "object":
                    yield value
                for child in value.values():
                    yield from object_schemas(child)
            elif isinstance(value, list):
                for child in value:
                    yield from object_schemas(child)

        for object_schema in object_schemas(self.schema):
            self.assertIs(False, object_schema.get("additionalProperties"))

    def test_example_validates(self):
        self.assert_valid(self.example)

    def test_rejects_unknown_properties(self):
        top_level_unknown = copy.deepcopy(self.example)
        top_level_unknown["unexpected"] = True
        self.assert_invalid(top_level_unknown)

        nested_unknown = copy.deepcopy(self.example)
        nested_unknown["carousel"]["speed"] = 1
        self.assert_invalid(nested_unknown)

    def test_rejects_bad_timing_modes(self):
        plan = copy.deepcopy(self.example)
        plan["timing"]["mode"] = "automatic"
        self.assert_invalid(plan)

    def test_manual_requires_switch_frames(self):
        plan = copy.deepcopy(self.example)
        plan["timing"]["mode"] = "manual"
        self.assert_invalid(plan)

    def test_nonmanual_forbids_switch_frames(self):
        plan = copy.deepcopy(self.example)
        plan["timing"]["switch_frames"] = [0]
        self.assert_invalid(plan)

    def test_manual_switch_frames_must_be_unique_integers(self):
        duplicate_frames = copy.deepcopy(self.example)
        duplicate_frames["timing"].update(mode="manual", switch_frames=[0, 0])
        self.assert_invalid(duplicate_frames)

        non_integer_frames = copy.deepcopy(self.example)
        non_integer_frames["timing"].update(mode="manual", switch_frames=[0, 1.5])
        self.assert_invalid(non_integer_frames)

    def test_manual_frame_count_remains_a_semantic_check(self):
        plan = copy.deepcopy(self.example)
        plan["timing"].update(mode="manual", switch_frames=[0])
        self.assert_valid(plan)

    def test_preserved_audio_requires_confirmed_audio_rights(self):
        plan = copy.deepcopy(self.example)
        plan["authorization"]["audio_rights_confirmed"] = False
        self.assert_invalid(plan)

    def test_audio_required_flag_matches_preserve_and_mute_modes(self):
        preserve = copy.deepcopy(self.example)
        preserve["audio"]["required"] = False
        self.assert_invalid(preserve)

        muted = copy.deepcopy(self.example)
        muted["audio"] = {"mode": "mute", "required": True}
        self.assert_invalid(muted)

    def test_rejects_bad_values(self):
        cases = [
            ("template id", lambda plan: plan.update(template_id="Uppercase")),
            ("template id prefix", lambda plan: plan.update(template_id=".hidden")),
            ("source width", lambda plan: plan["geometry"]["source_rect"].update(width=0)),
            ("slot count", lambda plan: plan["timing"].update(slot_count=0)),
            ("minimum segment", lambda plan: plan["timing"].update(min_segment_frames=0)),
            ("carousel width", lambda plan: plan["carousel"].update(item_width=0)),
            ("carousel gap", lambda plan: plan["carousel"].update(gap=-1)),
            ("end offset", lambda plan: plan["carousel"].update(end_offset_x=1)),
            ("background color", lambda plan: plan["background"].update(color="#FFF")),
            ("output profile", lambda plan: plan.update(output_profiles=["1024x1820"])),
            ("duplicate output", lambda plan: plan.update(output_profiles=["720x1280", "720x1280"])),
            ("analysis width", lambda plan: plan["analysis"].update(width=31)),
            ("snap window", lambda plan: plan["analysis"].update(snap_window_frames=-1)),
            ("prominence", lambda plan: plan["analysis"].update(min_prominence=-0.1)),
            ("evidence frame cap", lambda plan: plan["analysis"].update(max_evidence_frames=0)),
        ]
        for label, mutate in cases:
            with self.subTest(label=label):
                plan = copy.deepcopy(self.example)
                mutate(plan)
                self.assert_invalid(plan)


if __name__ == "__main__":
    unittest.main()
