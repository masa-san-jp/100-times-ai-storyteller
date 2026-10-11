"""P5-02: the ``reference-book`` format (world sections and characters)."""

from __future__ import annotations

import json
from pathlib import Path

import storyteller.formatting as formatting
from storyteller.formatting import build_plan, check_body, load_profile
from storyteller.new_run import create_story_orchestrator
from tests.test_formatting import (
    ROOT, _completed_story_run, _drive,
)


def _story_with_world(run_dir: Path) -> dict:
    path = run_dir / "story" / "story.json"
    story = json.loads(path.read_text(encoding="utf-8"))
    story["world"]["sections"] = [
        {"id": "place", "name": "場の描写", "kind": "single", "body": "水路の町。", "items": [], "sources": ["place:t1"]},
        {"id": "rules", "name": "掟", "kind": "list", "body": None, "sources": ["place:t1"],
         "items": [{"name": f"掟{n}", "body": f"内容{n}", "sources": ["place:t1"]} for n in range(1, 4)]},
    ]
    path.write_text(json.dumps(story, ensure_ascii=False), encoding="utf-8")
    return story


def test_profile_is_valid_and_loads() -> None:
    profile = load_profile("reference-book")
    assert profile["unit_mapping"] == "world_cast"


def test_plan_covers_every_section_and_person(tmp_path: Path) -> None:
    data_dir, run_id = _completed_story_run(tmp_path)
    run_dir = create_story_orchestrator(data_dir).run_dir(run_id)
    story = _story_with_world(run_dir)
    profile = load_profile("reference-book")
    plan = build_plan(story, profile, ROOT)
    titles = [unit["title"] for unit in plan["units"]]
    assert titles == ["世界：概説", "世界：場の描写", "世界：掟", "人物：カナ"]
    text = json.dumps(plan["parts"], ensure_ascii=False)
    for section in story["world"]["sections"]:
        assert f"section.{section['id']}" in text
        for item in section["items"]:
            assert item["name"] in text and item["body"] in text
    for person in story["cast"]:
        assert person["name"] in text and person["catchphrase"] in text
        assert all(entry in text for entry in person["backstory"])
    ids = [event_id for unit in plan["units"] for event_id in unit["event_ids"]]
    assert len(ids) == len(set(ids))


def test_long_unit_is_split_by_entries(tmp_path: Path) -> None:
    data_dir, run_id = _completed_story_run(tmp_path)
    run_dir = create_story_orchestrator(data_dir).run_dir(run_id)
    story = _story_with_world(run_dir)
    profile = {**load_profile("reference-book"), "max_chars_per_task": 400}  # 2 entries per part
    plan = build_plan(story, profile, ROOT)
    rules = next(unit for unit in plan["units"] if unit["title"] == "世界：掟")
    assert len(rules["part_ids"]) == 2
    flat = [e["id"] for part in plan["parts"] if part["unit"] == rules["id"] for e in part["events"]]
    assert flat == rules["event_ids"]


def test_contains_names_check() -> None:
    part = {"required_names": ["掟1"], "min_chars": 1, "max_chars": 100}
    assert check_body("掟1は大切だ。", part, ["contains_names"], ROOT) == []
    assert check_body("掟は大切だ。", part, ["contains_names"], ROOT)


def test_reference_book_passes_f0_to_f4_and_includes_everything(tmp_path: Path) -> None:
    data_dir, run_id = _completed_story_run(tmp_path)
    orchestrator = create_story_orchestrator(data_dir)
    story = _story_with_world(orchestrator.run_dir(run_id))
    formatting.start_formatting(orchestrator, run_id, "reference-book")

    def respond(task_id: str, card: str) -> str:
        if task_id.startswith("F4.judge"):
            return '{"answer": "no", "reason": "問題はない。"}'
        plan = json.loads(
            (orchestrator.run_dir(run_id) / "tasks" / "F1.split-reference-book" / "output.json").read_text(encoding="utf-8")
        )
        part = next(item for item in plan["parts"] if f"-{item['id']}-" in task_id)
        names = "、".join(part["required_names"])
        filler = "あ" * (part["min_chars"] + 5 - len(names))
        return f"{names}{filler}。"

    assert _drive(orchestrator, run_id, respond)
    manifest = orchestrator.load_run(run_id)
    assert manifest["tasks"]["F3.assemble-reference-book"]["state"] == "done"
    text = (orchestrator.run_dir(run_id) / "formats" / "reference-book" / "reference-book.md").read_text(encoding="utf-8")
    for section in story["world"]["sections"]:
        assert f"## 世界：{section['name']}" in text
        for item in section["items"]:
            assert item["name"] in text
    for person in story["cast"]:
        assert f"## 人物：{person['name']}" in text
