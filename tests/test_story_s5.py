from __future__ import annotations

from pathlib import Path

from storyteller.cards import generate_task_card
from storyteller.validation import load_and_validate_yaml, validate_output


ROOT = Path(__file__).parents[1]
TASK_SCHEMA = ROOT / "schemas" / "task-definition.schema.json"
S5_TYPES = ("name", "profile", "intro", "appearance", "motive", "catchphrase")


def load_task(name: str) -> dict[str, object]:
    value = load_and_validate_yaml(
        ROOT / "harness" / "story" / "tasks" / f"S5.{name}.yaml",
        TASK_SCHEMA,
    )
    assert isinstance(value, dict)
    return value


def test_all_s5_task_definitions_and_schemas_are_valid() -> None:
    for name in S5_TYPES:
        definition = load_task(name)
        assert definition["id"] == f"S5.{name}"
        schema_ref = definition["validate"]["schema"]
        assert (ROOT / schema_ref).is_file()


def test_s5_name_rejects_a_reading_that_does_not_use_two_given_sounds() -> None:
    definition = load_task("name")
    sound_set = {
        "set_id": "sound-01",
        "description": "短く澄んだ響き",
        "sounds": ["カ", "ナ", "リ", "オ", "セ", "ト"],
    }

    accepted = validate_output(
        definition,
        '{"name":"カナ","reading":"カナ","sources":["sound-01"]}',
        inputs={"name_sound": sound_set},
        harness_root=ROOT,
    )
    rejected = validate_output(
        definition,
        '{"name":"ミナ","reading":"ミナ","sources":["sound-01"]}',
        inputs={"name_sound": sound_set},
        harness_root=ROOT,
    )

    assert accepted.passed
    assert not rejected.passed
    assert any("uses_given" in error for error in rejected.errors)


def test_s5_profile_card_contains_assignment_and_protagonist_context() -> None:
    definition = load_task("profile")
    protagonist = {
        "id": "c1",
        "role": "protagonist",
        "elements": {
            "want": {"id": "want:i01", "text": "具体的な相手を探す"},
            "ability": {"id": "ability:t1", "text": "条件付きの能力"},
            "duty": {"id": "duty:t1", "text": "記憶を記録する役割"},
            "age": {"id": "age:t1", "text": "成人"},
            "gender": {"id": "gender:t1", "text": "女性"},
            "species": {"id": "species:t1", "text": "人間"},
            "taboo": {"id": "taboo:t1", "text": "過去を語らない"},
        },
        "suppressed_self_image": {
            "id": "m001",
            "text": "誰にも見せない自己像",
            "kind": "suppression",
        },
        "role_definition": {
            "id": "protagonist",
            "name": "主人公",
            "definition": "物語の中心人物として変化する存在。",
        },
        "plot_context": {
            "id": "quest",
            "name": "旅",
            "character_requirements": "主人公と旅を支える人物を置く。",
        },
    }
    card = generate_task_card(
        definition,
        "a" * 32,
        inputs={
            "character": protagonist,
            "name": "カナ",
            "role_definition": protagonist["role_definition"],
            "plot_requirements": protagonist["plot_context"],
            "protagonist": protagonist,
        },
    )

    assert "役の定義" in card
    assert "character_requirements:" in card
    assert "suppressed_self_image:" in card
    assert "c1" in card
