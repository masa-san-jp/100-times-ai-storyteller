"""P1-69: staged expansion and cumulative context (ADR-0011, story-pipeline S6.plot)."""

from __future__ import annotations

import json
import random
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from storyteller.cards import generate_task_card, prepare_task_inputs
from storyteller.event_fields import EVENT_FIELDS, EVENT_LABELS
from storyteller.selectors import resolve_inputs
from storyteller.story_ledger import (
    UNDECIDED, augment_sources, skeleton_row, whole_input_text,
)
from storyteller.story_s6 import story_s6_expand
from storyteller.tables import load_table

from tests.test_story_s6 import _assignment

ROOT = Path(__file__).parents[1]
TASKS = ROOT / "harness" / "story" / "tasks"


def _definition(task_type: str) -> dict[str, Any]:
    return yaml.safe_load((TASKS / f"{task_type}.yaml").read_text(encoding="utf-8"))


def _cast_assignment(threads: list[dict[str, Any]]) -> dict[str, Any]:
    assignment = _assignment()
    assignment["threads"] = threads
    roles = [("c1", "protagonist"), ("c2", "messenger"), ("c3", "adversary"), ("c4", "supporter")]
    assignment["cast"] = [
        {**deepcopy(assignment["cast"][0]), "id": person_id, "role": role}
        for person_id, role in roles
    ]
    assignment["absent_roles"] = []
    return assignment


def _thread(number: int, *, kind: str = "main", part: int | None = None,
            plot: str = "quest", events: int = 6, structure: str = "kishotenketsu") -> dict[str, Any]:
    return {
        "id": f"t{number:03d}", "kind": kind, "part": part, "plot_type": plot,
        "plot_type_name": plot, "events": events, "structure": structure,
    }


def _context(assignment: dict[str, Any], *, version: int = 6) -> SimpleNamespace:
    outputs: dict[str, Any] = {
        "S3.assign": assignment,
        "S4.section-place": {"body": "水路の音が境界を知らせる場所。", "sources": ["place:t1"]},
    }
    for person in assignment["cast"]:
        outputs[f"S5.name-{person['id']}"] = {"name": f"人物{person['id']}", "reading": "ナ"}
        outputs[f"S5.motive-{person['id']}"] = {"motive": "道を確かめたい。"}
    return SimpleNamespace(
        inputs={"assignment": assignment}, dependency_outputs=outputs,
        random=random.Random(3), task_id="S6.expand",
        definition={"version": version}, task={"deps": ["S3.assign"]},
    )


def _expand(threads: list[dict[str, Any]], *, version: int = 6):
    return story_s6_expand(_context(_cast_assignment(threads), version=version))


# (1) S1.analysis: the whole input, one string, fitted by the specified formula.

def _paragraphs(count: int, length: int) -> dict[str, Any]:
    return {"paragraphs": [{"id": f"p{n:03d}", "text": "あ" * length} for n in range(1, count + 1)]}


def test_whole_input_is_one_newline_joined_string_when_it_fits() -> None:
    text, warned = whole_input_text(_paragraphs(3, 100))
    assert text == "\n".join(["あ" * 100] * 3)
    assert not warned


def test_whole_input_keeps_spaces_and_newlines_and_counts_them() -> None:
    # S6.plot §2: the text is NFC-normalized, but spaces and newlines are kept and counted.
    paragraphs = {"paragraphs": [
        {"id": "p001", "text": "甲  乙\n丙。", "question": "問い "},
        {"id": "p002", "text": "\u30ab\u3099 x"},
    ]}
    text, warned = whole_input_text(paragraphs)
    assert text == "問い ：甲  乙\n丙。\nガ x" and not warned
    long = {"paragraphs": [{"id": "p001", "text": "あ " * 2600}, {"id": "p002", "text": "い"}]}
    text, _ = whole_input_text(long)
    # 5201 characters with the spaces, so the line allowance applies; without them it would fit.
    assert len(text) <= 5000 and text.split("\n")[0] == ("あ " * 2600)[: (5000 - 2) // 2]


def test_whole_input_over_5000_cuts_each_line_to_the_allowance() -> None:
    text, warned = whole_input_text(_paragraphs(10, 1000))
    lines = text.split("\n")
    assert len(lines) == 10 and not warned
    assert all(len(line) == (5000 - 10) // 10 for line in lines)
    assert len(text) <= 5000


def test_whole_input_warns_only_when_the_allowance_is_under_20() -> None:
    # 238 lines: (5000 - 238) // 238 = 20, no warning.
    text, warned = whole_input_text(_paragraphs(238, 30))
    assert not warned and len(text) <= 5000
    # 239 lines: the allowance is 19, so each line is cut to 20 and the tail is dropped.
    text, warned = whole_input_text(_paragraphs(239, 30))
    lines = text.split("\n")
    assert warned
    assert all(len(line) == 20 for line in lines)
    assert len(text) <= 5000 < len("\n".join(["あ" * 20] * 239))


def test_analysis_task_definition_follows_the_specification() -> None:
    definition = _definition("S1.analysis")
    assert definition["version"] == 1 and definition["share_across_runs"] is True
    assert definition["max_input_chars"] == 7000 and definition["on_exhausted"] == "skip"
    checks = definition["validate"]["checks"]
    assert {"min_chars": {"n": 300}} in checks and {"max_chars": {"n": 400}} in checks
    assert "ends_complete" in checks
    inputs = {"whole_input": "あ" * 10, "analysis_kind": "抑圧"}
    card = generate_task_card(definition, "tk", inputs=inputs)
    assert "抑圧している根源的な感情" in card
    assert "切望していること" not in card and "自己矛盾と葛藤" not in card


def test_analysis_is_handed_to_the_five_tasks_at_the_end_of_their_inputs() -> None:
    for task_type, version in (("S1.extract", 6), ("S4.section", 13), ("S5.profile", 15),
                               ("S5.motive", 13), ("S5.inner_conflict", 14)):
        definition = _definition(task_type)
        assert definition["version"] == version
        assert list(definition["inputs"])[-1] == "analysis"
        slot = definition["inputs"]["analysis"]
        assert (slot["label"], slot["required"], slot["truncate"], slot["from"]) == (
            "入力の全体の分析", False, "head", "S1.analysis")
    role = _definition("S1.extract")["card"]["role"]
    assert role == "与えられた段落を、入力の全体の分析が示す願望・抑圧・葛藤と照らし合わせて読み、物語に使える素材を抽出する。"


# (2) skeleton plan: reverse dependencies and protagonist assignment.

def test_turns_depend_on_later_turns_and_the_protagonist_is_in_climax_and_resolution() -> None:
    result = _expand([_thread(1)])
    by_id = {task.task_id: task for task in result.add_tasks}
    turns = [f"S6.skeleton_turn-t001-{n}" for n in (1, 2, 3)]
    assert all(turn in by_id for turn in turns)
    assert by_id["S6.skeleton_lack"].deps == ("S6.expand",)
    assert "S6.skeleton_lack" in by_id["S6.skeleton_climax"].deps
    assert {"S6.skeleton_lack", "S6.skeleton_climax"} <= set(by_id["S6.skeleton_resolution"].deps)
    last = by_id[turns[2]]
    assert {"S6.skeleton_lack", "S6.skeleton_climax", "S6.skeleton_resolution"} <= set(last.deps)
    assert not set(turns) & set(last.deps)
    assert turns[2] in by_id[turns[1]].deps and turns[1] not in by_id[turns[2]].deps
    assert {turns[1], turns[2]} <= set(by_id[turns[0]].deps)

    plan = result.output["skeleton"]
    slots = {slot["id"]: slot for slot in result.output["slots"]}
    by_kind = {element["kind"]: element for element in plan["elements"] if element["kind"] != "turn"}
    for kind in ("climax", "resolution"):
        assert "c1" in [person["id"] for person in slots[by_kind[kind]["slot"]]["characters"]]
    main = [slot for slot in result.output["slots"] if slot["thread"] == plan["main_thread"]]
    assert by_kind["climax"]["slot"] == next(s["id"] for s in main if s["stage"]["climax"])
    assert by_kind["resolution"]["slot"] == main[-1]["id"]
    assert not any(t.type in {"S7.condition", "S8.condition_check", "S8.condition_judge"}
                   for t in result.add_tasks)


def test_saga_continues_the_back_calculation_across_parts() -> None:
    threads = [_thread(n, part=n, plot=plot) for n, plot in enumerate(("quest", "rebirth", "rags-to-riches"), 1)]
    result = _expand(threads)
    plan = result.output["skeleton"]
    assert plan["main_thread"] == "t003" and plan["saga"]
    by_id = {task.task_id: task for task in result.add_tasks}
    first_of_next = "S6.skeleton_turn-t002-1"
    # The previous part's last turn depends on the next part's first turn, and on every later part's turns.
    deps = set(by_id["S6.skeleton_turn-t001-3"].deps)
    assert first_of_next in deps and "S6.skeleton_turn-t003-3" in deps
    assert not any(dep.startswith("S6.skeleton_turn-t001") for dep in by_id["S6.skeleton_turn-t002-3"].deps)
    # Every part's main thread has its own turns, assigned to its own slots.
    for element in plan["elements"]:
        if element["kind"] == "turn":
            slot = next(s for s in result.output["slots"] if s["id"] == element["slot"])
            assert slot["thread"] == element["thread"]


def test_each_required_event_is_assigned_to_exactly_one_slot() -> None:
    result = _expand([_thread(1), _thread(2, kind="subthread", plot="rebirth", events=4)])
    plan = result.output["skeleton"]
    turns = [e for e in plan["elements"] if e["kind"] == "turn"]
    assert len(turns) == 6 and len({e["task"] for e in turns}) == 6
    assigned = [task for slot in result.output["slots"] for task in slot["skeleton"]]
    assert sorted(assigned) == sorted(e["task"] for e in plan["elements"] if e["slot"])
    # A sub-thread's turn depends on the sub-thread's later turns, not on the main thread's.
    by_id = {task.task_id: task for task in result.add_tasks}
    assert "S6.skeleton_turn-t002-2" in by_id["S6.skeleton_turn-t002-1"].deps
    assert not any(dep.startswith("S6.skeleton_turn-t001") for dep in by_id["S6.skeleton_turn-t002-1"].deps)


# (7) the old graphs are unchanged.

@pytest.mark.parametrize("version", [4, 5])
def test_s6_expand_v5_and_older_have_no_skeleton(version: int) -> None:
    result = _expand([_thread(1)], version=version)
    assert "skeleton" not in result.output and "ledgers" not in result.output
    assert not any(task.type.startswith("S6.skeleton") for task in result.add_tasks)
    assert any(task.type == "S7.condition" for task in result.add_tasks)
    assert all("skeleton" not in slot for slot in result.output["slots"])
    assert all("time_design" not in slot["plot"] for slot in result.output["slots"])


# Shared helpers for the card tests.

def _sources(result, slot_id: str, field: str = "what", with_events: int | None = None):
    slots = result.output["slots"]
    output = {key: result.output[key] for key in ("slots", "skeleton", "ledgers")}
    outputs: dict[str, Any] = {}
    for index, slot in enumerate(slots):
        if with_events is None or index < with_events:
            outputs[f"S7.assemble-{slot['id']}"] = {
                "who": [person["id"] for person in slot["characters"]],
                **{key: f"{key}の記述" for key in (
                    "when", "where", "why", "intent", "what", "result", "emotion", "foreshadowing")},
            }
    for element in output["skeleton"]["elements"]:
        outputs[element["task"]] = f"{element['name']}の本文"
    return output, outputs


def _s7_card(result, slot_id: str, field: str = "what", *, upto: int | None = None) -> tuple[str, dict[str, Any]]:
    output, outputs = _sources(result, slot_id, field, upto)
    task = {"type": "S7.event", "index": [slot_id, field], "deps": []}
    definition = _definition("S7.event")
    labels = {"what": "何が起きたか", "where": "どこで起きたか"}
    expand = {**output, "event_field": labels[field]}
    sources = augment_sources(None, None, f"S7.event-{slot_id}-{field}", task, {"S6.expand": expand}, outputs)
    inputs = resolve_inputs(definition, sources, None, index=[slot_id, field])
    fitted = prepare_task_inputs(definition, inputs=inputs)
    return generate_task_card(definition, "tk", inputs=fitted), fitted


# (3) S7.event card: skeleton ledger, event ledger, the skeleton to realize or to head for.

def test_s7_event_card_carries_ledgers_and_the_realizing_skeleton() -> None:
    result = _expand([_thread(1)])
    slots = result.output["slots"]
    plan = result.output["skeleton"]
    target = next(slot for slot in slots if slot["skeleton"])
    index = slots.index(target)
    card, inputs = _s7_card(result, target["id"], upto=index)
    assert "#### 骨子の台帳" in card and "#### この出来事で実現する骨子" in card
    assert "#### 次に向かう骨子" not in card
    ledger = inputs["skeleton_ledger"]
    assert len(ledger) == len(plan["elements"])
    slot_of = {e["kind"]: e["slot"] for e in plan["elements"] if e["kind"] != "turn"}
    assert slot_of["climax"] and slot_of["resolution"]
    assert ledger[0].startswith("欠落：")
    assert ledger[1].startswith(f"決定的な瞬間（{slot_of['climax']}）：")
    assert ledger[2].startswith(f"結末の後（{slot_of['resolution']}）：")
    assert [row.split("：")[0] for row in ledger[3:]] == [
        f"転換（t001・{n}・{e['slot']}）"
        for n, e in enumerate(
            sorted((e for e in plan["elements"] if e["kind"] == "turn"), key=lambda e: e["n"]), 1)
    ]
    names = [row.split("：", 1)[0] for row in inputs["realizing_skeleton"]]
    expected = [e["name"] for e in plan["elements"] if e["slot"] == target["id"]]
    assert sorted(n.split("（", 1)[0] for n in names) == sorted(n.split("（", 1)[0] for n in expected)
    turn_names = [n for n in names if n.startswith("転換")]
    assert names[: len(turn_names)] == turn_names  # turns first, then 決定的な瞬間, 結末の後
    assert len(inputs["event_ledger"]) == index
    assert all(row.startswith(slots[i]["id"]) for i, row in enumerate(inputs["event_ledger"]))


def test_s7_event_card_heads_for_the_next_skeleton_when_nothing_is_assigned() -> None:
    result = _expand([_thread(1, events=9)])
    slots = result.output["slots"]
    index = next(i for i, slot in enumerate(slots) if not slot["skeleton"]
                 and any(later["skeleton"] for later in slots[i + 1:]))
    card, inputs = _s7_card(result, slots[index]["id"])
    assert "realizing_skeleton" not in inputs and "#### 次に向かう骨子" in card
    later = next(s for s in slots[index + 1:] if s["skeleton"])
    assert later["id"] in inputs["next_skeleton"][0].split("：")[0] or inputs["next_skeleton"][0].startswith(("決定的", "結末"))
    assert len(inputs["next_skeleton"]) == len(later["skeleton"])


def test_s7_event_definition_follows_v22() -> None:
    definition = _definition("S7.event")
    assert definition["version"] == 22 and definition["max_input_chars"] == 16000
    slots = definition["inputs"]
    required = [name for name, slot in slots.items() if slot.get("required")]
    assert {"skeleton_ledger", "stage_definition", "stage_guidance", "time_design", "pacing",
            "characters", "event_field"} <= set(required)
    assert all(slots[name].get("truncate", "none") == "none" for name in required)
    assert slots["event_ledger"]["truncate"] == "tail" and not slots["event_ledger"]["required"]
    for name in ("cast_ledger", "world_sections", "world_ledger"):
        assert slots[name]["truncate"] == "head" and not slots[name]["required"]
    assert "conditions" not in slots and "previous_result" not in slots
    steps = " ".join(definition["card"]["steps"])
    assert "骨子と、これまでの出来事に続く、この出来事を書く。確定した人物・世界・出来事と矛盾させない。" in steps
    assert definition["card"]["material_rule"] == "骨子・これまでの出来事・人物・世界を参照し、この出来事を具体的に書く。"


def test_event_tasks_wait_for_the_skeleton_and_every_earlier_judge() -> None:
    result = _expand([_thread(1)])
    slots = result.output["slots"]
    by_id = {task.task_id: task for task in result.add_tasks}
    skeleton = {e["task"] for e in result.output["skeleton"]["elements"]}
    for index, slot in enumerate(slots):
        deps = set(by_id[f"S7.event-{slot['id']}-what-c1"].deps)
        assert skeleton <= deps
        assert {f"S8.judge-{earlier['id']}" for earlier in slots[:index]} <= deps
        detail = by_id[f"S7.detail-{slot['id']}-b1"]
        assert {f"S8.judge-{earlier['id']}" for earlier in slots[:index]} <= set(detail.deps)
    assert skeleton <= set(by_id["S9.assemble"].deps)


# (4) the budget: cut in the specified order, required slots stay.

def test_over_budget_cards_cut_the_ledgers_in_the_specified_order() -> None:
    result = _expand([_thread(1)])
    slots = result.output["slots"]
    output, outputs = _sources(result, slots[3]["id"])
    big = ["あ" * 3000] * 6
    output["ledgers"] = {"input_ledger": big, "cast_ledger": big, "world_ledger": big}
    task = {"type": "S7.event", "index": [slots[3]["id"], "what"], "deps": []}
    definition = _definition("S7.event")
    expand = {**output, "event_field": "何が起きたか"}
    sources = augment_sources(None, None, "x", task, {"S6.expand": expand}, outputs)
    sources["S6.expand"]["event_ledger"] = ["い" * 3000] * 6
    inputs = resolve_inputs(definition, sources, None, index=[slots[3]["id"], "what"])
    fitted = prepare_task_inputs(definition, inputs=inputs)
    # Processed in reverse definition order: world ledger, world excerpt, cast ledger, event ledger.
    size = lambda name: len(fitted.get(name, []))  # noqa: E731
    if size("cast_ledger") < 6:
        assert size("world_ledger") == 0
    if size("event_ledger") < 6:
        assert size("cast_ledger") == 0 and size("world_ledger") == 0
    assert size("world_ledger") < 6 or size("cast_ledger") == 6
    for name, slot in definition["inputs"].items():
        if slot.get("required") and name in inputs:
            assert fitted[name] == inputs[name], name
    assert fitted["skeleton_ledger"] == inputs["skeleton_ledger"]


# (5) the largest saga cards: the required slots alone fit.

def _longest(values: list[str]) -> str:
    return max(values, key=len)


def test_saga_largest_cards_fit_with_required_slots_only() -> None:
    plots = [t["id"] for t in load_table("plot_types", repository_root=ROOT)["types"]][:6]
    threads = [_thread(n, part=n, plot=plot, events=8) for n, plot in enumerate(plots, 1)]
    result = _expand(threads)
    output = {key: result.output[key] for key in ("slots", "skeleton", "ledgers")}
    long_text = "あ" * 200
    outputs = {e["task"]: long_text for e in output["skeleton"]["elements"]}
    slots = output["slots"]
    structures = load_table("structures", repository_root=ROOT)["templates"]
    stage_definition = _longest([s["definition"] for t in structures.values() for s in t["stages"]])
    stage_guidance = _longest([s["guidance"] for t in structures.values() for s in t["stages"]])
    for slot in slots:
        slot["stage"]["definition"], slot["stage"]["guidance"] = stage_definition, stage_guidance
        slot["characters"] = [
            {"id": f"c{n}", "name": "名" * 8, "role": "supporter", "motive": "動" * 120,
             "intro": "紹" * 50, "voice": "声" * 800}
            for n in range(1, 5)
        ]
    # S7.event for the last field (foreshadowing), so all seven other fields are decided at
    # their longest (120 chars each); earlier event rows are optional and not counted.
    slot = slots[-1]
    task = {"type": "S7.event", "index": [slot["id"], "foreshadowing"], "deps": []}
    definition = _definition("S7.event")
    expand = {**output, "event_field": EVENT_LABELS["foreshadowing"]}
    sources = augment_sources(None, None, "x", task, {"S6.expand": expand}, outputs)
    inputs = resolve_inputs(definition, sources, None, index=[slot["id"], "foreshadowing"])
    inputs["decided_fields"] = {
        EVENT_LABELS[field]: "文" * 120 for field in EVENT_FIELDS if field != "foreshadowing"}
    assert len(inputs["decided_fields"]) == 7
    only_required = {
        name: value for name, value in inputs.items()
        if definition["inputs"].get(name, {}).get("required")
        or name in {"realizing_skeleton", "next_skeleton", "decided_fields"}
    }
    from storyteller.cards import input_char_count
    assert input_char_count(definition, only_required) <= definition["max_input_chars"]
    # The skeleton turn with the most later turns (the first part's first turn) in a saga.
    task = {"type": "S6.skeleton_turn", "index": ["t001", "1"], "deps": [
        e["task"] for e in output["skeleton"]["elements"] if e["task"] != "S6.skeleton_turn-t001-1"]}
    definition = _definition("S6.skeleton_turn")
    sources = augment_sources(None, None, "S6.skeleton_turn-t001-1", task, {"S6.expand": output}, outputs)
    inputs = resolve_inputs(definition, sources, None, index=["t001", "1"])
    inputs["absent_role_note"] = "不" * 80
    only_required = {
        name: value for name, value in inputs.items()
        if definition["inputs"][name].get("required") or name in {"absent_role_note", "theme"}
    }
    assert len(inputs["skeleton_ledger"]) == len(output["skeleton"]["elements"]) - 1
    assert input_char_count(definition, only_required) <= definition["max_input_chars"]
    # S7.detail keeps its required slots small as well.
    detail = _definition("S7.detail")
    required = {n for n, s in detail["inputs"].items() if s.get("required")}
    assert "skeleton_ledger" in required and "conditions" not in detail["inputs"]
    assert detail["version"] == 15 and detail["max_input_chars"] == 16000


# (6) no conditions; S8 asks for contradictions of facts only.

def test_v6_graph_has_no_condition_tasks_and_s8_asks_only_for_fact_contradictions() -> None:
    result = _expand([_thread(1), _thread(2, kind="subthread", plot="rebirth", events=4)])
    types = {task.type for task in result.add_tasks}
    assert not types & {"S7.condition", "S8.condition_check", "S8.condition_judge"}
    plan_deps = [task.deps for task in result.add_tasks if task.type == "S8.plan"]
    assert plan_deps and all(not any("condition" in dep for dep in deps) for deps in plan_deps)
    compare, detail = _definition("S8.compare"), _definition("S8.detail")
    assert (compare["version"], detail["version"]) == (15, 7)
    assert "comparison_kind" not in compare["inputs"] and "conditions" not in detail["card"]["role"]
    for definition in (compare, detail):
        text = " ".join(definition["card"]["steps"])
        for word in ("再演", "条件", "禁止"):
            assert word not in text, (definition["id"], word)
        assert "事実として両立しない" in text


# (8) a skipped element proceeds as （未定）.

def test_a_skipped_element_is_shown_as_undecided() -> None:
    element = {"name": "欠落", "slot": None, "kind": "lack"}
    assert skeleton_row(element, None) == f"欠落：{UNDECIDED}"
    turn = {"name": "転換", "thread": "t001", "n": 1, "slot": "e002", "kind": "turn"}
    assert skeleton_row(turn, "") == f"転換（t001・1・e002）：{UNDECIDED}"
    assert skeleton_row({**turn, "slot": None}, "本文") == "転換（t001・1）：本文"
    for name in ("S6.skeleton_lack", "S6.skeleton_climax", "S6.skeleton_resolution", "S6.skeleton_turn"):
        assert _definition(name)["on_exhausted"] == "skip"


def test_climax_and_resolution_rows_carry_their_event_ids() -> None:
    climax = {"name": "決定的な瞬間", "slot": "e007", "kind": "climax"}
    assert skeleton_row(climax, "決着。") == "決定的な瞬間（e007）：決着。"
    resolution = {"name": "結末の後", "slot": "e008", "kind": "resolution"}
    assert skeleton_row(resolution, "前文。\n  後文。") == "結末の後（e008）：前文。 後文。"


def test_story_skeleton_records_the_climax_and_resolution_slots() -> None:
    from storyteller.story_s9 import _assemble_skeleton, _skeleton_lines

    result = _expand([_thread(1)])
    plan = result.output["skeleton"]
    outputs = {element["task"]: "本文。" for element in plan["elements"]}
    skeleton = _assemble_skeleton(plan, outputs)
    slot_of = {e["kind"]: e["slot"] for e in plan["elements"] if e["kind"] != "turn"}
    assert skeleton["climax_slot"] == slot_of["climax"]
    assert skeleton["resolution_slot"] == slot_of["resolution"]
    schema = json.loads((ROOT / "schemas" / "story.schema.json").read_text(encoding="utf-8"))
    jsonschema_errors = list(Draft202012Validator(schema["$defs"]["skeleton"]).iter_errors(skeleton))
    assert not jsonschema_errors
    section = "\n".join(_skeleton_lines(skeleton))
    assert f"- 決定的な瞬間（{slot_of['climax']}）：本文。" in section


def test_skeleton_card_budget_and_checks_follow_the_specification() -> None:
    for name, limit in (("lack", 150), ("climax", 200), ("resolution", 200), ("turn", 200)):
        definition = _definition(f"S6.skeleton_{name}")
        assert definition["max_input_chars"] == 16000 and definition["version"] == 1
        assert {"max_chars": {"n": limit}} in definition["validate"]["checks"]
        assert "ends_complete" in definition["validate"]["checks"]
        assert {"no_new_proper_nouns": {"mode": "warn"}} in definition["validate"]["checks"]
        slots = definition["inputs"]
        for ledger in ("input_ledger", "cast_ledger", "world_ledger"):
            assert slots[ledger]["truncate"] == "head" and not slots[ledger]["required"]
        assert _definition("S6.expand")["version"] == 6
    assert _definition("S9.assemble")["version"] == 4
    models = yaml.safe_load((ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))["models"]
    assert models["gpt-oss:20b"]["context_length"] == 32768
    assert models["openai/gpt-oss-20b"]["context_length"] == 32768
