from pathlib import Path

import pytest

from storyteller.validation import validate_output, load_and_validate_yaml


ROOT = Path(__file__).parents[1]
FACTS = {"name": "ウネル", "age": 71, "birth_year": 811, "calendar": "灯暦"}
DEFINITION = {"id": "test.identity", "output": "text", "element": "text",
              "validate": {"checks": ["character_identity"]}}


@pytest.mark.parametrize("text,field", [
    ("ウネルは340歳の精霊だ。", "age"),
    ("ウネルの年齢は３４０歳。", "age"),
    ("ウネルは現在340歳。", "age"),
    ("ウネルは542年に生まれた。", "birth_year"),
    ("ウネルが灯暦５４２年に生まれた。", "birth_year"),
    ("ウネルの生年は灯暦542年。", "birth_year"),
    ("ウネルは灯暦542年生まれの精霊だ。", "birth_year"),
])
def test_explicit_wrong_target_facts_are_not_length_repairable(text, field):
    result = validate_output(DEFINITION, text, {"facts": FACTS})
    assert not result.passed and not result.repairable
    assert field in result.errors[0]


@pytest.mark.parametrize("text", [
    "ウネルは71歳で、灯守りをしている。ウネルは灯暦811年に生まれた。",
    "ウネルの年齢は７１歳。ウネルの生年は灯暦８１１年。",
    "獏賀暮は340歳である。ウネルは獏賀暮を訪ねる。",
    "獏賀暮は542年に生まれた。ウネルはその話を聞く。",
    "ウネルは10歳のとき、灯守りを見た。",
    "ウネルは10歳の頃、灯守りを見た。",
    "ウネルは10歳当時、灯守りを見た。",
    "ウネルは10歳だった。",
    "ウネルは340歳ではない。",
    "ウネルは340歳ではなく71歳である。",
    "542年のことだった。ウネルは灯守りの家系だ。",
    "『ウネルは340歳だ』と獏賀暮はからかった。",
    "「『ウネルは340歳だ』と聞いた」と獏賀暮が言った。",
    "ウネルは異暦542年に生まれたとの噂が流れた。",
    "小ウネルは340歳。",  # Substrings are not subject identity.
])
def test_other_people_history_quotes_and_ambiguous_claims_are_not_rejected(text):
    assert validate_output(DEFINITION, text, {"facts": FACTS}).passed


@pytest.mark.parametrize("facts", [{}, "未定", {"name": "ウネル"}])
def test_absent_or_undecided_fact_does_not_invent_a_value(facts):
    assert validate_output(DEFINITION, "ウネルは340歳だ。", {"facts": facts}).passed


def test_every_character_description_declares_identity_check():
    found = []
    for path in (ROOT / "harness/story/tasks").glob("S5.*.yaml"):
        definition = load_and_validate_yaml(path, ROOT / "schemas/task-definition.schema.json")
        if "facts" in definition.get("inputs", {}):
            found.append(path.stem)
            assert any(check == "character_identity" or isinstance(check, dict)
                       and "character_identity" in check
                       for check in definition["validate"]["checks"])
    assert set(found) == {"S5.profile", "S5.intro", "S5.motive", "S5.appearance",
                          "S5.personality", "S5.values", "S5.backstory", "S5.relationship",
                          "S5.voice", "S5.inner_conflict", "S5.catchphrase"}


@pytest.mark.parametrize("field", ["profile", "intro", "appearance", "personality",
                                  "values", "inner_conflict", "backstory"])
def test_actual_other_person_subject_is_rejected_for_subject_descriptions(field):
    definition = load_and_validate_yaml(ROOT / f"harness/story/tasks/S5.{field}.yaml",
                                       ROOT / "schemas/task-definition.schema.json")
    # Keep the production identity check and remove unrelated length checks.
    check = next(check for check in definition["validate"]["checks"]
                 if isinstance(check, dict) and "character_identity" in check)
    definition = {**definition, "validate": {"checks": [check]}}
    target = {"name": "獏賀暮", "age": 340, "birth_year": 542, "calendar": "灯暦"}
    result = validate_output(definition, "ウネルは灯守りの家系で、542年に生まれた。", {"facts": target})
    assert not result.passed and "獏賀暮" in result.errors[0]
    assert not result.repairable
    assert validate_output(definition, "ウネルは灯守りを見守っている。", {"facts": FACTS}).passed
    assert validate_output(definition, " 「ウネルは進む」", {"facts": FACTS}).passed


@pytest.mark.parametrize("field", ["voice", "catchphrase", "relationship", "motive"])
def test_context_and_first_person_items_do_not_require_a_target_subject(field):
    definition = load_and_validate_yaml(ROOT / f"harness/story/tasks/S5.{field}.yaml",
                                       ROOT / "schemas/task-definition.schema.json")
    assert "character_identity" in definition["validate"]["checks"]
    assert validate_output(DEFINITION, "獏賀暮を見て、私は歩き出す。", {"facts": FACTS}).passed
