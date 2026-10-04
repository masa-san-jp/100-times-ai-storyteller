from __future__ import annotations

import json
from pathlib import Path

from storyteller.cards import generate_task_card
from storyteller.selectors import resolve_inputs
from storyteller.validation import load_and_validate_yaml, validate_output


ROOT = Path(__file__).parents[1]
TASK_SCHEMA = ROOT / "schemas" / "task-definition.schema.json"
S7_SCHEMA = ROOT / "schemas" / "tasks" / "S7.event.schema.json"


def _load_definition() -> dict[str, object]:
    return load_and_validate_yaml(
        ROOT / "harness" / "story" / "tasks" / "S7.event.yaml",
        TASK_SCHEMA,
    )


def _slot(event_id: str = "e001") -> dict[str, object]:
    return {
        "id": event_id,
        "stage": {
            "definition": "変化のきっかけを描く。",
            "guidance": "小さな選択が状況を動かす。",
        },
        "required_events": ["主人公が境界を越える。"],
        "absent_role_note": None,
        "object": {"id": "object:t1", "text": "ひびの入った羅針盤"},
        "characters": [
            {
                "id": "c1",
                "name": "カナ",
                "role": "protagonist",
                "motive": "失われた道を確かめたい。",
            }
        ],
        "world_sections": [
            {"id": "place", "name": "場", "body": "水路の音が境界を知らせる。"},
            {"id": "customs", "name": "風習", "body": "灯りを消して道を譲る。"},
        ],
        "plot": {
            "conflict": "記憶を守るか真実を明かすかの葛藤。",
            "climax": "選択の結果を引き受ける。",
        },
        "theme": {"id": "m001", "text": "沈黙と理解の両立", "kind": "theme"},
    }


def _event() -> dict[str, object]:
    return {
        "when": "停電が始まった直後。",
        "where": "水路の分岐点で。",
        "who": ["c1"],
        "why": "失われた道を確かめるため。",
        "intent": "羅針盤の示す方角を選ぶ。",
        "what": "カナは灯りを一つ消して水門を開く。",
        "result": "隠れていた通路が現れる。",
        "emotion": "ためらいの後に決意を感じる。",
        "foreshadowing": "通路の奥で別の音がする。",
        "sources": ["c1", "place"],
    }


def test_s7_definition_and_schema_are_valid() -> None:
    definition = _load_definition()

    assert definition["id"] == "S7.event"
    assert definition["candidates"] == 1
    assert definition["validate"]["schema"] == "schemas/tasks/S7.event.schema.json"
    assert any(
        "why・where・when は、この出来事に固有の内容にし" in step
        and "人物の動機をそのまま書き写さない" in step
        for step in definition["card"]["steps"]
    )
    assert set(definition["inputs"]) == {
        "stage_definition",
        "stage_guidance",
        "required_events",
        "absent_role_note",
        "object",
        "characters",
        "world_sections",
        "previous_result",
        "conflict",
        "climax",
        "theme",
        "glossary",
    }

    result = validate_output(
        definition,
        json.dumps(_event(), ensure_ascii=False),
        inputs={"characters": _slot()["characters"], "world_sections": _slot()["world_sections"]},
        harness_root=ROOT,
    )
    assert result.passed, result.errors

    invalid = dict(_event(), who=["c2"])
    rejected = validate_output(
        definition,
        json.dumps(invalid, ensure_ascii=False),
        inputs={"characters": _slot()["characters"]},
        harness_root=ROOT,
    )
    assert not rejected.passed
    assert any("ids_subset" in error for error in rejected.errors)


def test_s7_card_contains_only_local_slot_context() -> None:
    definition = _load_definition()
    current = _slot()
    other_character = {
        "id": "c2",
        "name": "ミナ",
        "role": "adversary",
        "motive": "道を閉ざしたい。",
    }
    inputs = {
        "stage_definition": current["stage"]["definition"],
        "stage_guidance": current["stage"]["guidance"],
        "required_events": current["required_events"],
        "absent_role_note": current["absent_role_note"],
        "object": current["object"],
        "characters": current["characters"],
        "world_sections": current["world_sections"],
        "previous_result": "直前の結果だけが残っている。",
        "conflict": current["plot"]["conflict"],
        "climax": current["plot"]["climax"],
        "theme": current["theme"],
    }
    card = generate_task_card(definition, "ticket", inputs=inputs)

    assert "変化のきっかけを描く。" in card
    assert "小さな選択が状況を動かす。" in card
    assert "直前の結果だけが残っている。" in card
    assert "水路の音が境界を知らせる。" in card
    assert "灯りを消して道を譲る。" in card
    assert "c2" not in card
    assert "ミナ" not in card
    assert "過去の出来事" not in card


def test_s7_card_includes_climax_only_for_a_climax_stage() -> None:
    definition = _load_definition()
    current = _slot()
    inputs = {
        "stage_definition": current["stage"]["definition"],
        "stage_guidance": current["stage"]["guidance"],
        "required_events": current["required_events"],
        "absent_role_note": current["absent_role_note"],
        "object": current["object"],
        "characters": current["characters"],
        "world_sections": current["world_sections"],
        "conflict": current["plot"]["conflict"],
        "theme": current["theme"],
    }

    non_climax_card = generate_task_card(definition, "non-climax", inputs=inputs)
    assert "選択の結果を引き受ける。" not in non_climax_card
    assert "### 筋のクライマックス" not in non_climax_card
    assert "最後のスロット" not in non_climax_card

    climax_inputs = dict(inputs, climax=current["plot"]["climax"])
    climax_card = generate_task_card(definition, "climax", inputs=climax_inputs)
    assert "選択の結果を引き受ける。" in climax_card
    assert "### 筋のクライマックス" in climax_card
    assert "クライマックスの条件が入力にある場合" in climax_card
    assert "最後のスロット" not in climax_card


def test_s7_input_selectors_use_s6_slot_and_previous_judge_result() -> None:
    definition = _load_definition()
    inputs = definition["inputs"]

    assert inputs["characters"]["from"] == "S6.expand"
    assert inputs["characters"]["select"] == "slots[{slot}].characters"
    assert inputs["world_sections"]["select"] == "slots[{slot}].world_sections"
    assert inputs["previous_result"]["from"] == "S8.judge"
    assert inputs["previous_result"]["select"] == "output"
    assert inputs["climax"]["required"] is False

    slot = _slot()
    slot["plot"] = {
        key: value for key, value in slot["plot"].items() if key != "climax"
    }
    resolved = resolve_inputs(
        definition,
        {"S6.expand": {"slots": [slot]}},
        index="e001",
    )
    assert "climax" not in resolved
