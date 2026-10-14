"""P2-08: dedicated structure templates and the seven remaining plot types."""

from __future__ import annotations

import json
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from storyteller.new_run import create_story_orchestrator
from storyteller.orchestrator import Orchestrator
from storyteller.story_s6 import story_s6_expand
from storyteller.tables import load_table
from tests.test_story_cli_e2e import (
    _claimed_task,
    _cli,
    _fake_output,
    _manifest,
    _task_input,
)
from tests.test_story_s6 import _assignment, _context

ROOT = Path(__file__).parents[1]
STANDARD_TEMPLATES = {"three-beat", "kishotenketsu", "heros-journey-12"}
DEDICATED_PLOTS = [
    plot["id"]
    for plot in load_table("plot_types", repository_root=ROOT)["types"]
    if plot["structure"] != "standard"
]


def test_seven_dedicated_plot_types_each_have_their_own_template() -> None:
    plots = {p["id"]: p for p in load_table("plot_types", repository_root=ROOT)["types"]}
    templates = load_table("structures", repository_root=ROOT)["templates"]

    assert len(plots) == 21
    assert len(DEDICATED_PLOTS) == 7
    assert len({plots[p]["structure"] for p in DEDICATED_PLOTS}) == 7
    for plot_id in DEDICATED_PLOTS:
        template = templates[plots[plot_id]["structure"]]
        stage_ids = [stage["id"] for stage in template["stages"]]
        assert len(stage_ids) == len(set(stage_ids))
        # Every required event maps to a stage of the dedicated template too.
        for event in plots[plot_id]["required_events"]:
            assert event["stages"][plots[plot_id]["structure"]] in stage_ids


@pytest.mark.parametrize("plot_id", DEDICATED_PLOTS)
@pytest.mark.parametrize("events", [3, 7, 20])
def test_s6_expands_dedicated_templates_for_any_event_count(plot_id: str, events: int) -> None:
    plots = {p["id"]: p for p in load_table("plot_types", repository_root=ROOT)["types"]}
    templates = load_table("structures", repository_root=ROOT)["templates"]
    plot = plots[plot_id]
    assignment = _assignment()
    assignment["threads"] = [
        dict(
            assignment["threads"][0],
            plot_type=plot_id,
            plot_type_name=plot["name"],
            events=events,
            structure=plot["structure"],
        )
    ]

    slots = story_s6_expand(_context(assignment)).output["slots"]
    counts = Counter(slot["stage"]["id"] for slot in slots)

    assert len(slots) == events
    assert set(counts) == {s["id"] for s in templates[plot["structure"]]["stages"]}
    assigned = {e for slot in slots for e in slot["required_events"]}
    assert {e["description"] for e in plot["required_events"]} <= assigned
    climax = [slot for slot in slots if slot["stage"]["climax"]]
    assert climax and all(slot["plot"]["climax"] == plot["climax"] for slot in climax)


@pytest.mark.parametrize("plot_id", DEDICATED_PLOTS)
def test_dedicated_plot_type_produces_the_canonical_files(tmp_path: Path, plot_id: str) -> None:
    data_dir = tmp_path / "data"
    free_input = tmp_path / "free.md"
    free_input.write_text(
        "静かな町で、失われた記録を探す。\n\n"
        "境界を守る仕組みが、暮らしの選択を変えていく。\n\n"
        "誰かの沈黙を手がかりに、明日の道を考える。\n",
        encoding="utf-8",
    )
    created = _cli(
        # All seven templates run end to end here; short-scale coverage stays
        # in test_story_cli_e2e and the phase-2 heavy scale tests.
        data_dir, "new", "--free", str(free_input), "--scale", "dev",
        "--seed", "808", "--plot-type", plot_id,
    )
    assert created.returncode == 0, created.stderr
    run_id = created.stdout.strip()
    harness = create_story_orchestrator(data_dir)
    now = datetime.now(timezone.utc)
    harness = Orchestrator(
        data_dir, harness.task_definitions, harness.code_handlers,
        clock=lambda: now, harness_root=harness.definition_root,
        repository_root=harness.repository_root, harness_kind=harness.harness_kind,
    )

    comparison_yes_slot: str | None = None
    submitted_counts: Counter[str] = Counter()
    regenerated_what: set[str] = set()
    for _ in range(2000):
        claimed = harness.claim_next(run_id, executor_id="e2e", isolation="none")
        if claimed is None:
            break
        task_id, task = _claimed_task(data_dir, run_id, claimed["ticket"])
        inputs = _task_input(data_dir, run_id, task_id)
        is_compare = task["type"] == "S8.compare"
        output = _fake_output(
            task_id, task, inputs,
            send_comparison_yes=is_compare and comparison_yes_slot is None,
        )
        text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        submitted = harness.submit(claimed["ticket"], text)
        assert submitted.accepted, (task_id, submitted.errors)
        submitted_counts[task_id] += 1
        if is_compare and output["answer"] == "yes":
            assert comparison_yes_slot is None
            comparison_yes_slot = inputs["event_b"]["id"]
        if (task["type"] == "S7.event"
                and task["index"][:2] == [comparison_yes_slot, "what"]):
            assert task["invalidations"] >= 1
            regenerated_what.add(task_id)
    else:
        raise AssertionError("完走しませんでした")

    manifest = _manifest(data_dir, run_id)
    assert manifest["status"] == "completed"
    assert comparison_yes_slot is not None
    what_tasks = {
        name: task for name, task in manifest["tasks"].items()
        if task["type"] == "S7.event"
        and task["index"][:2] == [comparison_yes_slot, "what"]
    }
    assert what_tasks and regenerated_what == set(what_tasks)
    for name, task in what_tasks.items():
        assert task["invalidations"] >= 1 and task["state"] == "done", name
        assert submitted_counts[name] == task["invalidations"] + 1, name
    story_dir = data_dir / "runs" / run_id / "story"
    for name in ("story.json", "story.md", "characters.md", "world.md"):
        assert (story_dir / name).is_file(), name
    story = json.loads((story_dir / "story.json").read_text(encoding="utf-8"))
    plots = {p["id"]: p for p in load_table("plot_types", repository_root=ROOT)["types"]}
    main = story["threads"][0]
    assert main["plot_type"] == plot_id
    assert main["structure"] == plots[plot_id]["structure"]
    stage_ids = {s["id"] for s in load_table("structures", repository_root=ROOT)["templates"][main["structure"]]["stages"]}
    assert {event["stage"]["id"] for event in story["events"]} <= stage_ids
