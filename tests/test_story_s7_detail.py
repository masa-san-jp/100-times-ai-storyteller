from __future__ import annotations

from pathlib import Path

from storyteller.cards import generate_task_card
from storyteller.orchestrator import _source_outputs
from storyteller.selectors import resolve_selector
from storyteller.validation import load_and_validate_yaml, validate_output


ROOT = Path(__file__).parents[1]
TASK_SCHEMA = ROOT / "schemas" / "task-definition.schema.json"


def _definition() -> dict[str, object]:
    value = load_and_validate_yaml(
        ROOT / "harness" / "story" / "tasks" / "S7.detail.yaml", TASK_SCHEMA
    )
    assert isinstance(value, dict)
    return value


def test_s7_detail_is_text_with_the_volume_range_and_continuation() -> None:
    definition = _definition()

    assert definition["output"] == "text"
    assert definition["continuation"] is True
    assert definition["validate"]["checks"] == [
        {"min_chars": {"n": 1500}},
        {"max_chars": {"n": 2500, "fix": "trim"}},
    ]
    output = "説明的な場面の記述。" * 200
    result = validate_output(definition, output, inputs={})
    assert result.passed, result.errors


def test_s7_detail_card_contains_only_the_current_event_and_local_scene_context() -> None:
    definition = _definition()
    inputs = {
        "when": "停電が始まった直後。",
        "where": "水路の分岐点で。",
        "who": ["c1"],
        "why": "失われた道を確かめるため。",
        "intent": "羅針盤の示す方角を選ぶ。",
        "what": "カナは灯りを一つ消して水門を開く。",
        "result": "隠れていた通路が現れる。",
        "emotion": "ためらいの後に決意を感じる。",
        "beat": {"id": "opening", "name": "発端", "definition": "発端を示す。"},
        "characters": [
            {
                "id": "c1",
                "name": "カナ",
                "role": "protagonist",
                "intro": "道を探す旅人。",
                "voice": "短く確かめるように話す。",
            }
        ],
        "world_sections": [
            {"id": "place", "name": "場", "body": "水路の音が境界を知らせる。"}
        ],
        "previous_scene": "直前の場面の末尾。",
    }

    card = generate_task_card(definition, "ticket", inputs=inputs)

    assert "停電が始まった直後。" in card
    assert "発端" in card
    assert "カナ" in card
    assert "短く確かめるように話す。" in card
    assert "直前の場面の末尾。" in card
    assert "別の出来事" not in card


def test_s7_detail_selects_the_second_index_as_a_one_based_beat_number() -> None:
    source = {
        "slots": [
            {
                "id": "e001",
                "beats": [{"id": "opening"}, {"id": "turn"}],
            }
        ]
    }
    assert (
        resolve_selector("slots[{slot}].beats[{beat}]", source, index=("e001", "b1"))
        == {"id": "opening"}
    )


def test_s7_detail_previous_scene_is_limited_to_600_characters() -> None:
    definition = _definition()
    manifest = {
        "tasks": {
            "S7.detail-e001-b1": {
                "type": "S7.detail",
                "state": "done",
                "index": ["e001", "b1"],
            }
        }
    }
    resolved = _source_outputs(
        manifest,
        {
            "type": "S7.detail",
            "deps": ["S7.detail-e001-b1"],
            "index": ["e001", "b2"],
        },
        definition,
        {"S7.detail-e001-b1": "前" * 700},
    )

    assert resolved["S7.detail"] == "前" * 600
