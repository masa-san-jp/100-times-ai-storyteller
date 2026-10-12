"""P5-01: format ``visual-prompts`` (one prompt per person and per event)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import storyteller.formatting as formatting
from storyteller.format_units import UNIT_MAPPERS
from storyteller.format_visual import REFERENCE_EVENTS
from storyteller.new_run import create_story_orchestrator
from tests.test_formatting import _completed_story_run, _drive

ROOT = Path(__file__).parents[1]
NAME = "visual-prompts"



def test_profile_loads_and_mapping_is_registered() -> None:
    profile = formatting.load_profile(NAME)
    assert profile["unit"] == "prompt"
    assert profile["unit_mapping"] == "person_and_event" in UNIT_MAPPERS
    assert profile["continuation"] is False


def test_plan_has_one_unit_per_person_and_event(tmp_path: Path) -> None:
    data_dir, run_id = _completed_story_run(tmp_path, 4)
    orchestrator = create_story_orchestrator(data_dir)
    story = formatting.read_story(orchestrator.run_dir(run_id))
    plan = formatting.build_plan(story, formatting.load_profile(NAME), ROOT)

    people, events = len(story["cast"]), len(story["events"])
    assert len(plan["units"]) == len(plan["parts"]) == people + events
    assert [unit["title"] for unit in plan["units"][:people]] == [
        f"人物：{person['name']}" for person in story["cast"]
    ]
    assert all(unit["title"].startswith("場面：") for unit in plan["units"][people:])
    # every event is assigned exactly once, by the event units
    assert [i for unit in plan["units"] for i in unit["event_ids"]] == [e["id"] for e in story["events"]]
    person_part = plan["parts"][0]
    assert person_part["event_ids"] == [] and len(person_part["events"]) <= REFERENCE_EVENTS
    assert "外見：" in person_part["characters"][0]["text"]
    assert (person_part["min_chars"], person_part["max_chars"]) == (150, 400)
    scene_part = plan["parts"][people]
    assert len(scene_part["event_ids"]) == 1 and "外見：" in scene_part["characters"][0]["text"]


def test_end_to_end_outputs_a_prompt_for_every_person_and_event(tmp_path: Path) -> None:
    data_dir, run_id = _completed_story_run(tmp_path, 3)
    orchestrator = create_story_orchestrator(data_dir)
    formatting.start_formatting(orchestrator, run_id, NAME)
    orchestrator.advance(run_id)
    run_dir = orchestrator.run_dir(run_id)
    plan = json.loads((run_dir / "tasks" / f"F1.split-{NAME}" / "output.json").read_text(encoding="utf-8"))

    def respond(task_id: str, card: str) -> str:
        if task_id.startswith("F4.judge"):
            return '{"answer": "no", "reason": "問題はない。"}'
        return "あ" * 200 + "。"

    log = _drive(orchestrator, run_id, respond)
    assert log and all(accepted for _, _, accepted in log)
    orchestrator.advance(run_id)
    story = formatting.read_story(run_dir)
    text = (run_dir / "formats" / NAME / f"{NAME}.md").read_text(encoding="utf-8")
    assert text.count("\n## ") == len(story["cast"]) + len(story["events"]) == len(plan["units"])
    assert text.count("## 1. 人物：") == 1 and text.count("場面：") == len(story["events"])
    assert orchestrator.load_run(run_id)["tasks"][f"F3.assemble-{NAME}"]["state"] == "done"
