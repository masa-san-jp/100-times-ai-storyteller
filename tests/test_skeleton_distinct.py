"""P1-70: each skeleton element asks its own question at its own point in time
(story-pipeline S6.plot §3, heros-journey §13)."""

from __future__ import annotations

import re
from typing import Any

from storyteller.cards import generate_task_card, prepare_task_inputs
from storyteller.selectors import resolve_inputs
from storyteller.story_ledger import augment_sources

from tests.test_staged_plot import ROOT, _definition, _expand, _thread

FOLLOWING_LABEL = "この転換の直後に起きること"


def _card(result: Any, task_id: str) -> tuple[str, dict[str, Any]]:
    spec = next(task for task in result.add_tasks if task.task_id == task_id)
    output = {key: result.output[key] for key in ("slots", "skeleton", "ledgers")}
    # Only the outputs of the task's own dependencies are available, as in a run.
    outputs = {
        element["task"]: f"{element['task']}の本文。"
        for element in output["skeleton"]["elements"] if element["task"] in spec.deps
    }
    task = {"type": spec.type, "index": list(spec.index), "deps": list(spec.deps)}
    definition = _definition(spec.type)
    sources = augment_sources(None, None, task_id, task, {"S6.expand": output}, outputs)
    inputs = resolve_inputs(definition, sources, None, index=list(spec.index) or None)
    fitted = prepare_task_inputs(definition, inputs=inputs)
    return generate_task_card(definition, "tk", inputs=fitted), inputs


def _following(result: Any, task_id: str) -> list[str]:
    _card_text, inputs = _card(result, task_id)
    following = inputs["following_skeleton"]
    # The line is the same as its row in the skeleton ledger, and its text is confirmed.
    assert following[0] in inputs["skeleton_ledger"]
    assert not following[0].endswith("（未定）")
    return following


def _turn_tasks(result: Any, thread: str) -> list[str]:
    turns = [e for e in result.output["skeleton"]["elements"] if e["kind"] == "turn" and e["thread"] == thread]
    return [e["task"] for e in sorted(turns, key=lambda e: e["n"])]


def _climax_head(result: Any) -> str:
    climax = next(e for e in result.output["skeleton"]["elements"] if e["kind"] == "climax")
    return f"決定的な瞬間（{climax['slot']}）："


# (1) the turn card carries the element right after it.

def test_turn_n_is_followed_by_turn_n_plus_1_and_the_last_turn_by_the_climax() -> None:
    result = _expand([_thread(1), _thread(2, kind="subthread", plot="rebirth", events=4)])
    for thread in ("t001", "t002"):
        tasks = _turn_tasks(result, thread)
        assert len(tasks) >= 2
        for current, after in zip(tasks, tasks[1:]):
            following = _following(result, current)
            assert len(following) == 1
            assert following[0].startswith(f"転換（{thread}・{after.rsplit('-', 1)[1]}・e")
            assert following[0].endswith(f"{after}の本文。")
        # Each thread's last turn (main or sub) heads for the main thread's 決定的な瞬間.
        last = _following(result, tasks[-1])
        assert last[0].startswith(_climax_head(result))
    card, _ = _card(result, _turn_tasks(result, "t001")[0])
    assert FOLLOWING_LABEL in card


def test_saga_part_hands_over_to_the_next_parts_first_turn() -> None:
    result = _expand([_thread(n, part=n, events=6) for n in (1, 2, 3)])
    for thread, next_thread in (("t001", "t002"), ("t002", "t003")):
        last = _turn_tasks(result, thread)[-1]
        first_of_next = _turn_tasks(result, next_thread)[0]
        following = _following(result, last)
        assert following[0].startswith(f"転換（{next_thread}・1・")
        assert following[0].endswith(f"{first_of_next}の本文。")
    assert _following(result, _turn_tasks(result, "t003")[-1])[0].startswith(_climax_head(result))


def test_saga_hand_over_follows_the_part_numbers_not_the_input_order() -> None:
    # Parts given in reverse order: part 1 still hands over to part 2, and depends on it.
    result = _expand([_thread(1, part=2, events=6), _thread(2, part=1, events=6)])
    last_of_part1 = _turn_tasks(result, "t002")[-1]
    first_of_part2 = _turn_tasks(result, "t001")[0]
    spec = next(task for task in result.add_tasks if task.task_id == last_of_part1)
    assert first_of_part2 in spec.deps
    assert _following(result, last_of_part1)[0].startswith("転換（t001・1・")


def test_following_skeleton_is_a_required_untruncated_input_of_the_turn() -> None:
    definition = _definition("S6.skeleton_turn")
    slot = definition["inputs"]["following_skeleton"]
    assert (slot["label"], slot["required"], slot["truncate"]) == (FOLLOWING_LABEL, True, "none")
    names = list(definition["inputs"])
    assert names.index("following_skeleton") == names.index("skeleton_ledger") + 1


# (2)(3) each element's card has the spec's steps, word for word.

def _spec_steps() -> dict[str, list[str]]:
    text = (ROOT / "docs" / "spec" / "story-pipeline.md").read_text(encoding="utf-8")
    steps: dict[str, list[str]] = {}
    for number, kind in (("1", "lack"), ("2", "climax"), ("3", "resolution"), ("4", "turn")):
        line = next(line for line in text.splitlines() if line.startswith(f"  - {number}（"))
        steps[kind] = re.findall(r"「([^」]+)」", line)
    return steps


def test_each_element_card_has_the_specified_steps() -> None:
    result = _expand([_thread(1)])
    tasks = {
        "lack": "S6.skeleton_lack", "climax": "S6.skeleton_climax",
        "resolution": "S6.skeleton_resolution", "turn": _turn_tasks(result, "t001")[0],
    }
    for kind, steps in _spec_steps().items():
        assert steps, kind
        card, _ = _card(result, tasks[kind])
        definition_steps = _definition(f"S6.skeleton_{kind}")["card"]["steps"]
        for step in steps:
            assert step in definition_steps, (kind, step)
            assert step in card, (kind, step)
        assert _definition(f"S6.skeleton_{kind}")["version"] == 2


def test_resolution_is_written_as_a_state_not_as_an_action() -> None:
    result = _expand([_thread(1)])
    card, _ = _card(result, "S6.skeleton_resolution")
    assert "割り当てた人物が行う行動として書く" not in card
    assert "決定的な瞬間の行動を繰り返さず、その後の状態を書く。" in card
    assert "決定的な瞬間の後の状態として" in card


# (4) S6.expand v5 and older graphs have no skeleton tasks.

def test_v5_graph_is_unchanged() -> None:
    result = _expand([_thread(1)], version=5)
    assert not any(task.type.startswith("S6.skeleton") for task in result.add_tasks)
    assert "skeleton" not in result.output
