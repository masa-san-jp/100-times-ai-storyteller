from __future__ import annotations

import random
from pathlib import Path
from types import SimpleNamespace

from storyteller.story_s6 import allocate_stage_counts, story_s6_expand
from storyteller.tables import load_table
from storyteller.validation import validate_document


ROOT = Path(__file__).parents[1]


def _assignment(*, multi_thread: bool = False) -> dict[str, object]:
    threads = [
        {
            "id": "t001",
            "kind": "main",
            "part": None,
            "plot_type": "quest",
            "plot_type_name": "旅（クエスト）",
            "events": 3,
            "structure": "three-beat",
        }
    ]
    cast = [
        {
            "id": "c1",
            "role": "protagonist",
            "elements": {},
            "name_sound": {
                "set_id": "sound-01",
                "description": "短い響き",
                "sounds": ["カ", "ナ", "リ", "オ", "セ", "ト"],
            },
            "role_definition": {
                "id": "protagonist",
                "name": "主人公",
                "definition": "物語の中心人物。",
            },
            "plot_context": {
                "id": "quest",
                "name": "旅",
                "character_requirements": "旅の目的を置く。",
            },
        }
    ]
    if multi_thread:
        threads.append(
            {
                "id": "t002",
                "kind": "subthread",
                "part": None,
                "plot_type": "rebirth",
                "plot_type_name": "再生",
                "events": 4,
                "structure": "kishotenketsu",
            }
        )
    sections = load_table("world_sections", repository_root=ROOT)["sections"]
    return {
        "r": 0.7,
        "threads": threads,
        "cast": cast,
        "absent_roles": ["messenger", "supporter", "adversary"],
        "world": {
            "place": {"id": "place:t1", "text": "水路の町"},
            "era": {"id": "era:t1", "text": "長い停電の後"},
            "object": {"id": "object:t1", "text": "ひびの入った羅針盤"},
            "theme": None,
        },
        "world_sections": [sections[0]],
    }


def _context(assignment: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        inputs={"assignment": assignment},
        dependency_outputs={
            "S3.assign": assignment,
            "S4.section-place": {
                "body": "水路の音が境界を知らせる場所。",
                "sources": ["place:t1"],
            },
            "S5.name-c1": {
                "name": "カナ",
                "reading": "カナ",
                "sources": ["sound-01"],
            },
            "S5.motive-c1": {
                "motive": "失われた道を確かめたい。",
                "sources": ["c1"],
            },
        },
        random=random.Random(12),
        task_id="S6.expand",
    )


def test_s6_allocates_at_least_one_event_to_every_stage() -> None:
    stages = load_table("structures", repository_root=ROOT)["templates"][
        "heros-journey-12"
    ]["stages"]
    counts = allocate_stage_counts(24, stages, random.Random(4))

    assert len(counts) == 12
    assert all(count >= 1 for count in counts)
    assert sum(counts) == 24


def test_s6_assigns_absent_role_note_object_and_world_excerpt() -> None:
    result = story_s6_expand(_context(_assignment()))
    output = result.output
    validate_document(output, ROOT / "schemas/story/slots.schema.json")

    slots = output["slots"]
    assert len(slots) == 3
    assert [slot["id"] for slot in slots] == ["e001", "e002", "e003"]
    assert all(slot["world_sections"] == [
        {"id": "place", "name": "場の描写", "body": "水路の音が境界を知らせる場所。"}
    ] for slot in slots)
    assert slots[-1]["object"] == {
        "id": "object:t1",
        "text": "ひびの入った羅針盤",
    }
    assert slots[1]["absent_role_note"]
    assert slots[2]["absent_role_note"]
    assert slots[0]["characters"] == [
        {
            "id": "c1",
            "name": "カナ",
            "role": "protagonist",
            "motive": "失われた道を確かめたい。",
        }
    ]
    assert {task.task_id for task in result.add_tasks} >= {
        "S7.event-e001",
        "S8.judge-e001",
        "S8.judge-e002",
        "S8.judge-e003",
        "S9.assemble",
    }


def test_s6_adds_chronological_s7_s8_s9_dependencies() -> None:
    result = story_s6_expand(_context(_assignment(multi_thread=True)))
    slots = result.output["slots"]
    additions = result.add_tasks
    by_id = {task.task_id: task for task in additions}

    for index, slot in enumerate(slots):
        event_id = slot["id"]
        s7 = by_id[f"S7.event-{event_id}"]
        assert s7.deps[0] == "S6.expand"
        same_thread_previous = next(
            (
                candidate["id"]
                for candidate in reversed(slots[:index])
                if candidate["thread"] == slot["thread"]
            ),
            None,
        )
        if same_thread_previous is None:
            assert len(s7.deps) == 1
        else:
            assert f"S8.judge-{same_thread_previous}" in s7.deps

        for task in additions:
            if task.type == "S8.compare" and task.index[0] == event_id:
                prior_s7 = {
                    f"S7.event-{prior['id']}" for prior in slots[:index]
                }
                assert prior_s7 <= set(task.deps)

    judge_ids = [f"S8.judge-{slot['id']}" for slot in slots]
    assemble = by_id["S9.assemble"]
    assert assemble.deps[0] == "S6.expand"
    assert set(judge_ids) <= set(assemble.deps)
