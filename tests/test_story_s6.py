from __future__ import annotations

import random
from collections import Counter
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from storyteller.story_s6 import allocate_stage_counts, story_s6_expand
from storyteller.scale import derive_scale
from storyteller.tables import load_table
from storyteller.validation import validate_document
from storyteller.volume import compute_initial_volume


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
        "S7.assemble-e001",
        "S8.plan-e001",
        "S8.plan-e002",
        "S8.plan-e003",
        "S9.assemble",
    }
    assert not any(task.type in {"S8.compare", "S8.judge"} for task in result.add_tasks)
    assert "comparison_targets" not in slots[0]


def test_s6_carries_optional_character_intro_and_voice_to_slots() -> None:
    context = _context(_assignment())
    context.dependency_outputs["S5.intro-c1"] = {"intro": "道を探す旅人。"}
    context.dependency_outputs["S5.voice-c1"] = {"voice": "短く話す。"}

    slots = story_s6_expand(context).output["slots"]

    assert slots[0]["characters"][0]["intro"] == "道を探す旅人。"
    assert slots[0]["characters"][0]["voice"] == "短く話す。"


def test_s6_passes_climax_condition_only_to_climax_stages() -> None:
    output = story_s6_expand(_context(_assignment())).output
    expected_climax = load_table("plot_types", repository_root=ROOT)["types"][0][
        "climax"
    ]

    for slot in output["slots"]:
        if slot["stage"]["climax"]:
            assert slot["plot"]["climax"] == expected_climax
        else:
            assert "climax" not in slot["plot"]


def test_s6_adds_chronological_s7_s8_s9_dependencies() -> None:
    result = story_s6_expand(_context(_assignment(multi_thread=True)))
    slots = result.output["slots"]
    additions = result.add_tasks
    by_id = {task.task_id: task for task in additions}

    for index, slot in enumerate(slots):
        event_id = slot["id"]
        s7 = by_id[f"S7.event-{event_id}-what"]
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
            assert f"S7.assemble-{same_thread_previous}" in s7.deps

        plan = by_id[f"S8.plan-{event_id}"]
        prior_s7 = {f"S7.assemble-{prior['id']}" for prior in slots[:index]}
        assert {f"S7.assemble-{event_id}", *prior_s7} <= set(plan.deps)

    judge_ids = [f"S8.judge-{slot['id']}" for slot in slots]
    assemble = by_id["S9.assemble"]
    assert assemble.deps[0] == "S6.expand"
    assert set(judge_ids) <= set(assemble.deps)
    detail_ids = [
        task.task_id for task in additions if task.type == "S7.detail"
    ]
    assert detail_ids
    assert set(detail_ids) <= set(assemble.deps)
    for task_id in detail_ids:
        detail = by_id[task_id]
        event_id, beat_id = task_id.removeprefix("S7.detail-").split("-b")
        assert f"S7.assemble-{event_id}" in detail.deps
        assert f"S8.judge-{event_id}" in detail.deps
        if beat_id != "1":
            assert f"S7.detail-{event_id}-b{int(beat_id) - 1}" in detail.deps


@pytest.mark.parametrize("with_scale", [False, True])
def test_s6_creates_the_volume_allocated_number_of_detail_tasks(with_scale: bool) -> None:
    assignment = _assignment()
    context = _context(assignment)
    scale = derive_scale("short", seed=7, repository_root=ROOT).value
    scale["derived"]["events"] = sum(
        thread["events"] for thread in assignment["threads"]
    )
    if with_scale:
        context.scale = scale

    result = story_s6_expand(context)
    allocation = compute_initial_volume(scale, repository_root=ROOT)["story"]
    detail_tasks = [task for task in result.add_tasks if task.type == "S7.detail"]

    assert len(detail_tasks) == sum(allocation["scene_counts"])
    assert len(detail_tasks) == 67
    assert sum(allocation["scene_counts"]) * allocation["scene_chars"] >= 100_000
    assert len(detail_tasks) * 1500 >= 100_000
    assert all(len(slot["beats"]) >= 1 for slot in result.output["slots"])
    assert result.output["slots"][0]["beats"][0]["id"] == "opening"


def test_s6_allocation_when_event_count_equals_stage_count() -> None:
    stages = load_table("structures", repository_root=ROOT)["templates"][
        "three-beat"
    ]["stages"]

    counts = allocate_stage_counts(len(stages), stages, random.Random(4))

    assert counts == [1] * len(stages)


def test_s6_expand_distributes_all_events_and_required_events() -> None:
    assignment = _assignment()
    assignment["threads"] = [dict(assignment["threads"][0], events=6)]

    slots = story_s6_expand(_context(assignment)).output["slots"]
    stage_counts = Counter(slot["stage"]["id"] for slot in slots)
    expected_stages = load_table("structures", repository_root=ROOT)["templates"][
        "three-beat"
    ]["stages"]
    plot = load_table("plot_types", repository_root=ROOT)["types"][0]
    required = {event["description"] for event in plot["required_events"]}

    assert len(slots) == 6
    assert all(stage_counts[stage["id"]] >= 1 for stage in expected_stages)
    assert sum(stage_counts.values()) == 6
    assert required <= {
        event
        for slot in slots
        for event in slot["required_events"]
    }


def test_s6_absent_role_note_matches_missing_stage_role() -> None:
    assignment = _assignment()
    assignment["absent_roles"] = []
    for role, person_id in (
        ("messenger", "c2"),
        ("supporter", "c3"),
        ("adversary", "c4"),
    ):
        person = deepcopy(assignment["cast"][0])
        person["id"] = person_id
        person["role"] = role
        assignment["cast"].append(person)

    context = _context(assignment)
    for index in range(2, 5):
        context.dependency_outputs[f"S5.name-c{index}"] = {
            "name": f"人物{index}",
            "reading": f"ジンブツ{index}",
            "sources": ["sound-01"],
        }
        context.dependency_outputs[f"S5.motive-c{index}"] = {
            "motive": f"動機{index}",
            "sources": [f"c{index}"],
        }

    slots = story_s6_expand(context).output["slots"]

    assert all(slot["absent_role_note"] is None for slot in slots)

    assignment["absent_roles"] = ["adversary"]
    assignment["cast"] = assignment["cast"][:3]
    context = _context(assignment)
    for index in (2, 3):
        context.dependency_outputs[f"S5.name-c{index}"] = {
            "name": f"人物{index}",
            "reading": f"ジンブツ{index}",
            "sources": ["sound-01"],
        }
        context.dependency_outputs[f"S5.motive-c{index}"] = {
            "motive": f"動機{index}",
            "sources": [f"c{index}"],
        }
    slots = story_s6_expand(context).output["slots"]
    for slot in slots:
        has_missing_role = "adversary" in slot["stage"]["roles"]
        assert (slot["absent_role_note"] is not None) == has_missing_role


def test_s6_parts_are_concatenated_without_interleaving() -> None:
    assignment = _assignment()
    base = assignment["threads"][0]
    assignment["threads"] = [
        dict(base, id="t002", part=2, events=3),
        dict(base, id="t001", part=1, events=3),
        dict(base, id="t003", part=3, events=3),
    ]

    slots = story_s6_expand(_context(assignment)).output["slots"]

    assert [slot["thread"] for slot in slots] == [
        "t001",
        "t001",
        "t001",
        "t002",
        "t002",
        "t002",
        "t003",
        "t003",
        "t003",
    ]


def test_s6_side_thread_order_is_preserved_around_main_stages() -> None:
    assignment = _assignment(multi_thread=True)
    slots = story_s6_expand(_context(assignment)).output["slots"]

    main_slots = [slot for slot in slots if slot["thread"] == "t001"]
    side_slots = [slot for slot in slots if slot["thread"] == "t002"]
    assert [slot["stage"]["id"] for slot in main_slots] == [
        "setup",
        "turn",
        "resolution",
    ]
    assert [slot["stage"]["id"] for slot in side_slots] == [
        "ki",
        "sho",
        "ten",
        "ketsu",
    ]
    positions = [slots.index(slot) for slot in side_slots]
    assert positions == sorted(positions)
    assert all(0 < position < len(slots) - 1 for position in positions)


def test_s6_same_seed_produces_the_same_output() -> None:
    first = story_s6_expand(_context(_assignment()))
    second = story_s6_expand(_context(_assignment()))

    assert first.output == second.output
    assert first.add_tasks == second.add_tasks


def test_s6_chooses_between_people_with_the_same_required_role_by_seed() -> None:
    assignment = _assignment()
    for person_id in ("c2", "c3"):
        person = deepcopy(assignment["cast"][0])
        person["id"] = person_id
        person["role"] = "adversary"
        assignment["cast"].append(person)
    assignment["absent_roles"] = ["messenger", "supporter"]

    selected: set[str] = set()
    for seed in range(20):
        context = _context(assignment)
        context.random = random.Random(seed)
        for person_id in ("c2", "c3"):
            context.dependency_outputs[f"S5.name-{person_id}"] = {
                "name": person_id,
                "reading": person_id,
                "sources": ["sound-01"],
            }
            context.dependency_outputs[f"S5.motive-{person_id}"] = {
                "motive": person_id,
                "sources": [person_id],
            }
        slots = story_s6_expand(context).output["slots"]
        selected.update(
            character["id"]
            for slot in slots
            for character in slot["characters"]
            if character["role"] == "adversary"
        )

    assert selected == {"c2", "c3"}
