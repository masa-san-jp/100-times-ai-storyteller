from __future__ import annotations

import json
from pathlib import Path

from storyteller.cards import generate_task_card
from storyteller.validation import load_and_validate_yaml, validate_output


ROOT = Path(__file__).parents[1]
TASK_SCHEMA = ROOT / "schemas" / "task-definition.schema.json"
S5_TYPES = ("name", "profile", "intro", "appearance", "motive", "catchphrase")
S5_NEW_SINGLE_TYPES = ("personality", "values", "voice", "inner_conflict", "backstory")
_VOLUME_RANGES = {
    "personality": (500, 1000),
    "values": (400, 800),
    "voice": (400, 800),
    "inner_conflict": (500, 1000),
    "backstory": (1000, 2000),
    "relationship": (300, 600),
}


def load_task(name: str) -> dict[str, object]:
    value = load_and_validate_yaml(
        ROOT / "harness" / "story" / "tasks" / f"S5.{name}.yaml",
        TASK_SCHEMA,
    )
    assert isinstance(value, dict)
    return value


def test_all_s5_task_definitions_and_schemas_are_valid() -> None:
    for name in (*S5_TYPES, *S5_NEW_SINGLE_TYPES, "relationship"):
        definition = load_task(name)
        assert definition["id"] == f"S5.{name}"
        if name in {"name", "intro", "motive", "catchphrase"}:
            assert definition["output"] == "json"
            assert (ROOT / definition["validate"]["schema"]).is_file()
        else:
            assert definition["output"] == "text"
            assert definition["extend_to_min"] is True
            assert "schema" not in definition["validate"]
            assert "output_example" not in definition["card"]
            assert "sources" not in str(definition["card"])


def test_relationship_context_is_a_code_task_keyed_by_the_counterpart() -> None:
    definition = load_task("relationship_context")
    assert definition["kind"] == "code"
    assert definition["handler"] == "story_s5_relationship_context"
    assert set(definition["inputs"]) == {"name", "role", "intro"}
    for slot in definition["inputs"].values():
        assert slot["select"].endswith("[{slot}]") or "[{slot}]" in slot["select"]


def test_s5_new_items_enforce_the_volume_table_char_range() -> None:
    for name, (minimum, maximum) in _VOLUME_RANGES.items():
        definition = load_task(name)
        checks = definition["validate"]["checks"]
        assert {"min_chars": {"n": minimum}} in checks
        assert {"max_chars": {"n": maximum, "fix": "trim"}} in checks
        assert "sources_exist" not in checks
        assert "default_sources" not in definition


def test_s5_new_single_item_cards_carry_the_same_context_as_appearance() -> None:
    protagonist = _character("protagonist", "c1", "主人公固有")
    other = _character("adversary", "c2", "他者固有")
    for name in S5_NEW_SINGLE_TYPES:
        definition = load_task(name)
        protagonist_card = generate_task_card(
            definition, "a" * 32, inputs=_s5_inputs("本人名", protagonist)
        )
        other_card = generate_task_card(
            definition, "b" * 32, inputs=_s5_inputs("他者名", other)
        )
        assert "### 主人公の名前" not in protagonist_card
        assert "主人公固有の願望" in protagonist_card
        assert "### 主人公の名前\n主人公名" in other_card
        assert "### 主人公の役\nprotagonist" in other_card
        assert "主人公の紹介文" not in other_card


def test_s5_relationship_card_only_shows_the_counterpart_s_name_role_and_intro() -> None:
    definition = load_task("relationship")
    subject = _character("protagonist", "c1", "主人公固有")
    inputs = _s5_inputs("本人名", subject)
    inputs["other_person"] = {
        "c2": {"id": "c2", "name": "相手名", "role": "adversary", "intro": "相手の短い紹介。"}
    }
    card = generate_task_card(definition, "c" * 32, inputs=inputs)

    assert "相手名" in card
    assert "相手の短い紹介。" in card
    assert "adversary" in card
    # The counterpart's own elements/taboo must never reach this card
    # (story-pipeline.md S5: "相手の人物の名前・役・紹介だけを渡す"). The
    # subject's own elements legitimately appear elsewhere on the card (the
    # same "character" slot every other S5 item carries), so only the
    # "相手の人物" section itself is checked here.
    counterpart_section = card.split("### 相手の人物", 1)[1].split("###", 1)[0]
    for forbidden in ("want:", "ability:", "taboo:", "elements"):
        assert forbidden not in counterpart_section


def test_s5_default_sources_recovers_from_the_protagonist_s_id_instead_of_the_character_s_own_id() -> None:
    # Observed with gpt-oss:20b on S5.intro-c2: the executor cites the
    # protagonist's ID (c1) instead of its own subject (c2), which is never
    # shown as an ID on the card, and the task would otherwise fail forever.
    protagonist = _character("protagonist", "c1", "主人公固有")
    other = _character("adversary", "c2", "他者固有")
    for task_name, field in (
        ("intro", "intro"),
        ("motive", "motive"),
        ("catchphrase", "catchphrase"),
    ):
        definition = load_task(task_name)
        assert definition["default_sources"] == ["{slot}"]
        recovered = validate_output(
            definition,
            json.dumps(
                {field: "他者の短い説明文。", "sources": ["c1"]}, ensure_ascii=False
            ),
            inputs=_s5_inputs(
                "他者名",
                other,
                protagonist_name=protagonist["name_sound"]["description"],
            ),
            harness_root=ROOT,
            index=["c2"],
        )
        assert recovered.passed, recovered.errors
        assert recovered.value["sources"] == ["c2"]
        assert "出典を補完: ['c2']" in recovered.warnings


def test_s5_name_has_no_default_sources_since_its_subject_is_a_name_sound_set() -> None:
    definition = load_task("name")
    assert "default_sources" not in definition


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


def test_s5_one_sentence_items_complete_the_sentence_instead_of_failing() -> None:
    character = _character("protagonist", "c1", "主人公固有")
    for task_name, field in (("intro", "intro"), ("catchphrase", "catchphrase")):
        definition = load_task(task_name)
        assert {
            "ends_complete": {"field": field, "fix": "append"}
        } in definition["validate"]["checks"]
        fixed = validate_output(
            definition,
            json.dumps({field: "文の途中", "sources": ["c1"]}, ensure_ascii=False),
            inputs=_s5_inputs("本人名", character),
            harness_root=ROOT,
            index=["c1"],
        )
        assert fixed.passed, fixed.errors
        assert fixed.value[field] == "文の途中。"
        assert "文末を補完" in fixed.warnings


def _character(role: str, character_id: str, marker: str) -> dict[str, object]:
    character: dict[str, object] = {
        "id": character_id,
        "role": role,
        "elements": {
            "want": {"id": f"want:{marker}", "text": f"{marker}の願望"},
            "ability": {"id": f"ability:{marker}", "text": f"{marker}の能力"},
            "duty": {"id": f"duty:{marker}", "text": f"{marker}の課題"},
            "age": {"id": f"age:{marker}", "text": f"{marker}の年齢"},
            "gender": {"id": f"gender:{marker}", "text": f"{marker}の性別"},
            "species": {"id": f"species:{marker}", "text": f"{marker}の種族"},
        },
        "name_sound": {
            "set_id": "sound-01",
            "description": "短く澄んだ響き",
            "sounds": ["カ", "ナ", "リ", "オ", "セ", "ト"],
        },
        "role_definition": {
            "id": role,
            "name": role,
            "definition": f"{role}の定義。",
        },
        "plot_context": {
            "id": "quest",
            "name": "旅",
            "character_requirements": "主人公と旅を支える人物を置く。",
        },
    }
    if role == "protagonist":
        character["elements"]["taboo"] = {
            "id": f"taboo:{marker}",
            "text": f"{marker}の禁忌",
        }
        character["suppressed_self_image"] = {
            "id": "m001",
            "text": f"{marker}の抑圧された自己像",
            "kind": "suppression",
        }
    return character


def _s5_inputs(
    name: str,
    character: dict[str, object],
    *,
    protagonist_name: str = "主人公名",
) -> dict[str, object]:
    return {
        "character": character,
        "facts": {},
        "name": name,
        "profile": "人物のプロフィール",
        "motive": "人物の動機",
        "other_characters": [character],
        "names": [{"name": name, "reading": name, "sources": ["sound-01"]}],
        "role_definition": character["role_definition"],
        "plot_requirements": character["plot_context"],
        "protagonist_name": protagonist_name,
        "protagonist_role": "protagonist",
        "protagonist_intro": "主人公の紹介文",
    }


def test_s5_cards_limit_protagonist_context_to_non_protagonists() -> None:
    protagonist = _character("protagonist", "c1", "主人公固有")
    other = _character("adversary", "c2", "他者固有")

    for task_name in ("profile", "intro", "appearance"):
        definition = load_task(task_name)
        protagonist_card = generate_task_card(
            definition,
            "a" * 32,
            inputs=_s5_inputs("本人名", protagonist),
        )
        other_card = generate_task_card(
            definition,
            "b" * 32,
            inputs=_s5_inputs("他者名", other),
        )

        assert "### 主人公の名前" not in protagonist_card
        assert "### 主人公の役" not in protagonist_card
        assert "主人公名" not in protagonist_card
        assert "主人公固有の願望" in protagonist_card
        assert "主人公固有の禁忌" in protagonist_card
        assert "主人公固有の抑圧された自己像" in protagonist_card
        assert "### 主人公の名前\n主人公名" in other_card
        assert "### 主人公の役\nprotagonist" in other_card
        assert "主人公の紹介文" not in other_card
        for forbidden in (
            "主人公固有の願望",
            "主人公固有の能力",
            "主人公固有の禁忌",
            "主人公固有の抑圧された自己像",
        ):
            assert forbidden not in other_card

    for task_name in ("motive", "catchphrase"):
        definition = load_task(task_name)
        protagonist_card = generate_task_card(
            definition,
            "c" * 32,
            inputs=_s5_inputs("本人名", protagonist),
        )
        other_card = generate_task_card(
            definition,
            "d" * 32,
            inputs=_s5_inputs("他者名", other),
        )

        assert "### 主人公の名前" not in protagonist_card
        assert "### 主人公の役" not in protagonist_card
        assert "### 主人公の紹介" not in protagonist_card
        assert "主人公名" not in protagonist_card
        assert "主人公の紹介文" not in protagonist_card
        assert "### 主人公の名前\n主人公名" in other_card
        assert "### 主人公の役\nprotagonist" in other_card
        assert "### 主人公の紹介\n主人公の紹介文" in other_card
        for forbidden in (
            "主人公固有の願望",
            "主人公固有の能力",
            "主人公固有の禁忌",
            "主人公固有の抑圧された自己像",
        ):
            assert forbidden not in other_card


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
            "facts": {},
            "name": "カナ",
            "role_definition": protagonist["role_definition"],
            "plot_requirements": protagonist["plot_context"],
            "protagonist_name": "主人公名",
            "protagonist_role": "protagonist",
        },
    )

    assert "役の定義" in card
    assert "character_requirements:" in card
    assert "suppressed_self_image:" in card
    assert "c1" in card
    assert "### 主人公の名前" not in card
