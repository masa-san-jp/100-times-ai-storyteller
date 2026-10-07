from __future__ import annotations

import json
from pathlib import Path

from storyteller.new_run import create_story_orchestrator
from storyteller.orchestrator import CodeTaskResult, Orchestrator, TaskSpec
from storyteller.story_s7 import EVENT_FIELDS, EVENT_LABELS, event_tasks
from storyteller.task_outputs import task_sources
from tests.test_story_s7 import ROOT, _event, _slot


def _harness(tmp_path: Path, count: int = 1):
    story = create_story_orchestrator(tmp_path / "unused")
    definitions = {
        name: story.task_definitions[name]
        for name in ("S7.event", "S7.assemble", "S8.plan", "S8.compare", "S8.judge")
    }
    definitions["S6.expand"] = {
        "id": "S6.expand", "version": 1, "kind": "code", "handler": "slots"
    }
    slots = [dict(_slot(f"e{number:03d}"), thread="t1") for number in range(1, count + 1)]

    def expand(context):
        additions = []
        prior = None
        assembled = []
        for slot in slots:
            event_id = slot["id"]
            deps = [context.task_id]
            if prior:
                deps.append(f"S8.judge-{prior}")
            additions.extend(event_tasks(
                context.task_id, event_id, deps,
                f"S7.assemble-{prior}" if prior else None,
            ))
            assembled.append(f"S7.assemble-{event_id}")
            additions.append(TaskSpec(
                f"S8.plan-{event_id}", "S8.plan",
                deps=(context.task_id, *assembled), index=(event_id,),
            ))
            prior = event_id
        return CodeTaskResult(output={"slots": slots}, add_tasks=additions)

    harness = Orchestrator(
        tmp_path / "data", definitions, {**story.code_handlers, "slots": expand},
        harness_root=ROOT, repository_root=ROOT,
    )
    run_id = harness.create_run(seed=34, task_specs=[TaskSpec("S6.expand", "S6.expand")])
    return harness, run_id


def _submit_event(harness, run_id, event_id, *, what=None):
    values = _event()
    if what is not None:
        values["what"] = what
    for index, field in enumerate(EVENT_FIELDS):
        claimed = harness.claim_next(run_id, executor_id="test", isolation="none")
        assert claimed is not None
        task_dir = harness.task_dir(run_id, f"S7.event-{event_id}-{field}")
        claim = json.loads((task_dir / "claim.json").read_text(encoding="utf-8"))
        assert claim["ticket"] == claimed["ticket"]
        inputs = json.loads((task_dir / "input.json").read_text(encoding="utf-8"))
        assert inputs["event_field"] == EVENT_LABELS[field]
        instructions = claimed["card"].split("### 作り方の指示", 1)[1]
        assert EVENT_LABELS[field] in instructions
        assert inputs.get("decided_fields", {}) == {
            EVENT_LABELS[prior]: values[prior] for prior in EVENT_FIELDS[:index]
        }
        assert len(inputs["world_sections"]) <= 2
        assert "S7." not in claimed["card"]
        if event_id == "e001":
            assert "previous_result" not in inputs
        else:
            assert inputs["previous_result"] == _event()["result"]
        result = harness.submit(claimed["ticket"], values[field])
        assert result.accepted, result.errors


def test_field_failure_preserves_completed_elements_and_sources(tmp_path):
    harness, run_id = _harness(tmp_path)
    for field in EVENT_FIELDS[:2]:
        claimed = harness.claim_next(run_id, executor_id="test", isolation="none")
        assert harness.submit(claimed["ticket"], _event()[field]).accepted
    prior_outputs = {
        field: (harness.task_dir(run_id, f"S7.event-e001-{field}") / "output.md").read_text(encoding="utf-8")
        for field in EVENT_FIELDS[:2]
    }
    claimed = harness.claim_next(run_id, executor_id="test", isolation="none")
    rejected = harness.submit(claimed["ticket"], "時" * 121)
    assert not rejected.accepted and any("max_chars" in error for error in rejected.errors)
    tasks = harness.load_run(run_id)["tasks"]
    assert tasks["S7.event-e001-when"]["state"] == "ready"
    assert tasks["S7.event-e001-when"]["attempt"] == 1
    for field, text in prior_outputs.items():
        task_id = f"S7.event-e001-{field}"
        assert tasks[task_id]["state"] == "done" and tasks[task_id]["attempt"] == 0
        assert (harness.task_dir(run_id, task_id) / "output.md").read_text(encoding="utf-8") == text
    for field in EVENT_FIELDS[2:]:
        claimed = harness.claim_next(run_id, executor_id="test", isolation="none")
        assert harness.submit(claimed["ticket"], _event()[field]).accepted
    harness.advance(run_id)
    output = json.loads((harness.task_dir(run_id, "S7.assemble-e001") / "output.json").read_text(encoding="utf-8"))
    assert {key: value for key, value in output.items() if key != "sources"} == _event()
    expected_sources = list(dict.fromkeys(
        source for field in EVENT_FIELDS
        for source in task_sources(harness.task_dir(run_id, f"S7.event-e001-{field}"))
    ))
    assert output["sources"] == expected_sources
    assert "c1" in output["sources"] and "m001" in output["sources"]


def test_s8_invalidates_what_and_rebuilds_all_event_fields(tmp_path):
    harness, run_id = _harness(tmp_path, count=2)
    _submit_event(harness, run_id, "e001")
    _submit_event(harness, run_id, "e002")
    claimed = harness.claim_next(run_id, executor_id="test", isolation="none")
    assert claimed is not None
    result = harness.submit(claimed["ticket"], json.dumps({"answer": "yes", "reason": "結果が両立しない。"}, ensure_ascii=False))
    assert result.accepted
    tasks = harness.advance(run_id)["tasks"]
    assert tasks["S7.event-e002-what"]["invalidations"] == 1
    assert tasks["S7.event-e002-what"]["state"] == "ready"
    for field in EVENT_FIELDS[1:]:
        task_id = f"S7.event-e002-{field}"
        assert tasks[task_id]["state"] == "blocked"
        assert not (harness.task_dir(run_id, task_id) / "output.md").exists()
    assert tasks["S7.assemble-e002"]["state"] == "blocked"
    assert tasks["S8.plan-e002"]["state"] == "blocked"
    assert all(tasks[f"S7.event-e001-{field}"]["state"] == "done" for field in EVENT_FIELDS)
    _submit_event(harness, run_id, "e002", what="人物は水門の仕組みを変える。")
    claimed = harness.claim_next(run_id, executor_id="test", isolation="none")
    assert harness.submit(claimed["ticket"], json.dumps({"answer": "no", "reason": "事実は両立する。"}, ensure_ascii=False)).accepted
    assert harness.advance(run_id)["status"] == "completed"


def test_assembly_uses_the_shared_character_count_rule(tmp_path):
    harness, run_id = _harness(tmp_path)
    what = " ".join(["扉が開く。"] * 24)
    assert len(what) > 120
    _submit_event(harness, run_id, "e001", what=what)
    assert harness.advance(run_id)["status"] == "completed"
    output = json.loads((harness.task_dir(run_id, "S7.assemble-e001") / "output.json").read_text(encoding="utf-8"))
    assert output["what"] == what
