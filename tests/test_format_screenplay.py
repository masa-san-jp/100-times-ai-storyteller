"""P3-03: screenplay format (``formats/screenplay``), ``scene`` mapping, Fountain check."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

import storyteller.formatting as formatting
from storyteller.format_scene import fountain_errors, has_time_jump, map_scene
from storyteller.format_units import UNIT_MAPPERS
from storyteller.tables import load_table
from storyteller.validation import SchemaValidationError, validate_document


ROOT = Path(__file__).parents[1]


def _event(number: int, where: str, when: str, thread: str = "t001") -> dict[str, Any]:
    return {
        "id": f"e{number:03d}", "stage": {"id": "setup", "name": "導入"}, "thread": thread,
        "when": when, "where": where, "who": ["c1"], "why": "確かめる", "intent": "進む",
        "what": "歩いた", "result": "開いた", "emotion": "安心", "foreshadowing": "",
        "sources": [],
    }


def _story(events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "meta": {"run_id": "20261012-000000-abcdef", "plot_types": [{"name": "試練"}]},
        "cast": [{"id": "c1", "name": "カナ", "reading": "カナ", "role": "protagonist", "profile": "旅人"}],
        "threads": [{
            "id": "t001", "kind": "main", "part": None, "plot_type": "x",
            "plot_type_name": "型", "events": len(events), "structure": "three-beat",
        }],
        "events": events,
    }


def _scenes(events: list[dict[str, Any]]) -> list[tuple[str, ...]]:
    return [unit.event_ids for unit in map_scene(_story(events), {}, ROOT)]


def test_scene_mapping_is_registered_and_profile_loads() -> None:
    assert UNIT_MAPPERS["scene"] is map_scene
    profile = formatting.load_profile("screenplay")
    assert profile["unit"] == "scene" and profile["unit_mapping"] == "scene"
    assert "fountain_syntax" in profile["checks"]["code"]


def test_same_place_with_continuous_time_is_one_scene() -> None:
    events = [_event(1, "水路", "朝"), _event(2, "水路", "朝のうち"), _event(3, "水路", "昼")]
    assert _scenes(events) == [("e001", "e002", "e003")]


def test_place_change_starts_a_new_scene() -> None:
    events = [_event(1, "水路", "朝"), _event(2, "水路", "朝"), _event(3, "市場", "朝"), _event(4, "水路", "朝")]
    assert _scenes(events) == [("e001", "e002"), ("e003",), ("e004",)]


@pytest.mark.parametrize("when", ["翌日", "数年後", "三日後の夜", "翌朝の水路"])
def test_time_jump_starts_a_new_scene_even_in_the_same_place(when: str) -> None:
    events = [_event(1, "水路", "朝"), _event(2, "水路", when), _event(3, "水路", "昼")]
    assert _scenes(events) == [("e001",), ("e002", "e003")]


def test_scene_units_have_headings_ids_and_cover_every_event_once() -> None:
    events = [_event(n, "水路" if n < 3 else "市場", "朝") for n in range(1, 6)]
    units = map_scene(_story(events), {}, ROOT)
    assert [unit.id for unit in units] == ["u01", "u02"]
    assert units[0].title == "水路 - 朝"
    assert [e for unit in units for e in unit.event_ids] == [e["id"] for e in events]


def test_time_jump_phrases_come_from_the_table() -> None:
    phrases = load_table("time_jumps", repository_root=ROOT)["phrases"]
    assert has_time_jump("数年後", phrases) and not has_time_jump("朝", phrases)
    schema = ROOT / "schemas" / "tables" / "time_jumps.schema.json"
    table = yaml.safe_load((ROOT / "tables" / "time_jumps.yaml").read_text(encoding="utf-8"))
    validate_document(table, schema)
    table["phrases"] = []
    with pytest.raises(SchemaValidationError):
        validate_document(table, schema)


def test_plan_splits_scenes_into_tasks_within_limits() -> None:
    profile = formatting.load_profile("screenplay")
    events = [_event(n, "水路", "朝") for n in range(1, 8)] + [_event(8, "市場", "翌日")]
    plan = formatting.build_plan(_story(events), profile, ROOT)
    assert len(plan["units"]) == 2
    low, high = profile["chars_per_event"]
    assert all(part["max_chars"] <= profile["max_chars_per_task"] for part in plan["parts"])
    assert all(part["min_chars"] == low * len(part["event_ids"]) for part in plan["parts"])
    ids = [event_id for part in plan["parts"] for event_id in part["event_ids"]]
    assert ids == [event["id"] for event in events]


GOOD_BODY = (
    "カナは水路の縁に立つ。\n\n@カナ\n(小声で)\nここなら見つからない。\n\n"
    "足音が近づく。カナは身を縮める。"
)


def test_valid_scene_body_passes_fountain_check() -> None:
    assert fountain_errors(GOOD_BODY) == []
    part = {"min_chars": 1, "max_chars": 10_000}
    assert formatting.check_body(GOOD_BODY, part, ["fountain_syntax"], ROOT) == []


@pytest.mark.parametrize(
    "body",
    [
        ".水路 - 朝\n\nカナが立つ。",  # scene heading belongs to the template
        "# 第一幕\n\nカナが立つ。",
        "= 要約\n\nカナが立つ。",
        "カナが立つ。\n\n@カナ\n\n行く。",  # cue without dialogue
        "カナが立つ。\n@カナ\n行く。",  # cue without a blank line before
        "カナが立つ。\n\n(小声で)\n\n行く。",  # parenthetical outside dialogue
        "@カナ\n(小声で\n行く。",  # unclosed parenthetical
        "カナが立つ。\n\n@カナ",  # cue at the end
        "カナが立つ。[[メモ",
        "/* 消した部分\nカナが立つ。",
    ],
)
def test_invalid_scene_bodies_are_rejected(body: str) -> None:
    assert fountain_errors(body)
    part = {"min_chars": 1, "max_chars": 10_000}
    assert formatting.check_body(body, part, ["fountain_syntax"], ROOT)


def test_assembled_screenplay_passes_document_check() -> None:
    template = (ROOT / "formats" / "screenplay" / "template.fountain").read_text(encoding="utf-8")
    units = [
        {"id": "u01", "title": "水路 - 朝", "part": None, "body": GOOD_BODY},
        {"id": "u02", "title": "市場 - 翌日", "part": None, "body": "市場は賑わう。"},
    ]
    text = formatting.render_template(template, _story([_event(1, "水路", "朝")]), units, "screenplay")
    assert text.startswith("Title: 試練：カナ\n")
    assert ".水路 - 朝\n\nカナは水路の縁に立つ。" in text and ".市場 - 翌日\n" in text
    assert fountain_errors(text, document=True) == []
    # A script without any scene heading, or a title page without a blank line, is invalid.
    assert fountain_errors("Title: x\n\n市場は賑わう。", document=True)
    assert fountain_errors("Title: x\n.水路\n\nあ。", document=True)
