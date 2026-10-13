"""P3-02: novel format (``formats/novel``) and the ``structure`` unit mapping."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

import storyteller.formatting as formatting
from storyteller.format_units import chapters_of, map_structure
from storyteller.tables import load_table


ROOT = Path(__file__).parents[1]
STRUCTURES = load_table("structures", repository_root=ROOT)["templates"]
HJ_STAGES = [stage["id"] for stage in STRUCTURES["heros-journey-12"]["stages"]]


def _event(number: int, thread: str, stage: str) -> dict[str, Any]:
    return {
        "id": f"e{number:03d}", "stage": {"id": stage, "name": stage}, "thread": thread,
        "when": "朝", "where": "水路", "who": ["c1"], "why": "確かめる", "intent": "進む",
        "what": f"{number}番目の区画を歩いた", "result": f"{number}番目の入口が開いた", "emotion": "安心", "foreshadowing": "",
        "sources": [],
    }


def _thread(number: int, kind: str, structure: str, part: int | None = None) -> dict[str, Any]:
    return {
        "id": f"t{number:03d}", "kind": kind, "part": part, "plot_type": "x",
        "plot_type_name": "型", "events": 3, "structure": structure,
    }


def _story(threads: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "meta": {"run_id": "20261012-000000-abcdef", "plot_types": [{"name": "試練"}]},
        "cast": [{"id": "c1", "name": "カナ", "reading": "カナ", "role": "protagonist", "profile": "旅人"}],
        "threads": threads, "events": events,
    }


def _hero_story(per_stage: int = 1, with_side: bool = False) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for stage in HJ_STAGES:
        for _ in range(per_stage):
            events.append(_event(len(events) + 1, "t001", stage))
        if with_side and stage in {"mentor", "trials"}:
            events.append(_event(len(events) + 1, "t002", "setup"))
    return _story(
        [_thread(1, "main", "heros-journey-12"), _thread(2, "subthread", "kishotenketsu")], events
    )


def test_heros_journey_table_maps_twelve_stages_to_ten_chapters() -> None:
    chapters = chapters_of("heros-journey-12", STRUCTURES["heros-journey-12"])
    assert len(chapters) == 10
    assert [stage for chapter in chapters for stage in chapter["stages"]] == HJ_STAGES
    # Structures without a chapter table make each stage one chapter.
    assert len(chapters_of("three-beat", STRUCTURES["three-beat"])) == len(STRUCTURES["three-beat"]["stages"])


def test_chapters_must_cover_every_stage_once_in_order() -> None:
    structure = {**STRUCTURES["three-beat"], "chapters": [{"id": "a", "name": "あ", "stages": ["turn", "setup"]}]}
    with pytest.raises(ValueError, match="ちょうど1回"):
        chapters_of("three-beat", structure)


def test_main_stages_become_chapters_and_side_events_follow_the_previous_main_slot() -> None:
    story = _hero_story(with_side=True)
    units = map_structure(story, {}, ROOT)
    assert len(units) == 10
    assert [unit.id for unit in units] == [f"u{n:02d}" for n in range(1, 11)]
    assert [unit.title for unit in units][:3] == ["日常世界", "冒険への呼びかけと拒否", "師との出会い"]
    by_id = {unit.id: unit.event_ids for unit in units}
    # call+refusal share one chapter; the side event after "mentor" joins the mentor chapter.
    assert len(by_id["u02"]) == 2
    mentor_events = [e["id"] for e in story["events"] if e["stage"]["id"] == "mentor"]
    side = [e["id"] for e in story["events"] if e["thread"] == "t002"]
    assert by_id["u03"] == (mentor_events[0], side[0])
    assert side[1] in by_id["u05"]
    assert sum(len(ids) for ids in by_id.values()) == len(story["events"])


def test_side_event_before_the_first_main_event_joins_the_first_chapter() -> None:
    story = _hero_story()
    story["events"].insert(0, _event(99, "t002", "setup"))
    units = map_structure(story, {}, ROOT)
    assert units[0].event_ids[0] == "e099"
    assert len(units) == 10


def test_unknown_stage_or_structure_is_rejected() -> None:
    story = _hero_story()
    story["events"][0]["stage"]["id"] = "nowhere"
    with pytest.raises(ValueError, match="段階"):
        map_structure(story, {}, ROOT)
    story = _hero_story()
    story["threads"][0]["structure"] = "no-such"
    with pytest.raises(ValueError, match="構造テンプレート"):
        map_structure(story, {}, ROOT)


def test_saga_counts_chapters_anew_per_part() -> None:
    events = [_event(1, "t001", "setup"), _event(2, "t001", "turn"),
              _event(3, "t002", "setup"), _event(4, "t002", "turn")]
    story = _story(
        [_thread(1, "main", "three-beat", 1), _thread(2, "main", "three-beat", 2)], events
    )
    units = map_structure(story, {}, ROOT)
    assert [(unit.part, unit.event_ids) for unit in units] == [
        (1, ("e001",)), (1, ("e002",)), (2, ("e003",)), (2, ("e004",)),
    ]


def test_novel_profile_loads_and_chapter_count_and_range_are_within_bounds() -> None:
    profile = formatting.load_profile("novel")
    assert profile["unit"] == "chapter" and profile["chars_per_event"] == [1500, 3000]
    story = _hero_story(per_stage=2, with_side=True)
    plan = formatting.build_plan(story, profile, ROOT)
    assert len(plan["units"]) == 10
    low, high = profile["chars_per_event"]
    assert all(part["min_chars"] == low * len(part["event_ids"]) for part in plan["parts"])
    assert all(part["max_chars"] == high * len(part["event_ids"]) for part in plan["parts"])
    # Every F2 task stays within max_chars_per_task; every event appears once.
    assert all(part["max_chars"] <= profile["max_chars_per_task"] for part in plan["parts"])
    ids = [event_id for part in plan["parts"] for event_id in part["event_ids"]]
    assert sorted(ids) == sorted(event["id"] for event in story["events"])
    total = sum(part["max_chars"] for part in plan["parts"])
    assert total == high * len(story["events"])
    # Chapter body checks: a body inside the range passes, a short one fails.
    part = plan["parts"][0]
    inside = "あ" * (part["min_chars"] + 10) + "。"
    assert formatting.check_body(inside, part, plan["code_checks"], ROOT) == []
    assert formatting.check_body("短い。", part, plan["code_checks"], ROOT)


def test_novel_template_adds_chapter_headings_and_part_headings() -> None:
    template = (ROOT / "formats" / "novel" / "template.md").read_text(encoding="utf-8")
    story = _hero_story()
    units = [
        {"id": "u01", "title": "日常世界", "part": None, "body": "一つ目。"},
        {"id": "u02", "title": "師との出会い", "part": None, "body": "二つ目。"},
    ]
    text = formatting.render_template(template, story, units, "novel")
    assert text.startswith("# 試練：カナ\n")
    assert "## 第1章 日常世界\n\n一つ目。" in text and "## 第2章 師との出会い" in text
    assert "部" not in text.replace("日常世界", "")

    saga = [
        {"id": "u01", "title": "導入", "part": 1, "body": "あ。"},
        {"id": "u02", "title": "転換", "part": 1, "body": "い。"},
        {"id": "u03", "title": "導入", "part": 2, "body": "う。"},
    ]
    text = formatting.render_template(template, story, saga, "novel")
    assert text.count("# 第1部") == 1 and text.count("# 第2部") == 1
    assert text.index("# 第2部") < text.index("う。")
    # Chapters are counted anew in part 2.
    assert text.count("## 第1章") == 2 and text.count("## 第2章") == 1


def test_structures_table_schema_accepts_chapters_only_in_the_expected_shape() -> None:
    from storyteller.validation import SchemaValidationError, validate_document

    schema = ROOT / "schemas" / "tables" / "structures.schema.json"
    table = yaml.safe_load((ROOT / "tables" / "structures.yaml").read_text(encoding="utf-8"))
    validate_document(table, schema)
    table["templates"]["heros-journey-12"]["chapters"][0]["extra"] = 1
    with pytest.raises(SchemaValidationError):
        validate_document(table, schema)
