from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from storyteller.orchestrator import (
    CodeTaskResult,
    DAGError,
    InvalidTransition,
    Orchestrator,
    TaskSpec,
)


FIXED_CLOCK = lambda: datetime(2026, 9, 27, 3, 15, tzinfo=timezone.utc)


def code_definition(task_id: str, handler: str, **extra: object) -> dict:
    definition = {
        "id": task_id,
        "version": 1,
        "kind": "code",
        "handler": handler,
    }
    definition.update(extra)
    return definition


def llm_definition(task_id: str) -> dict:
    return {
        "id": task_id,
        "version": 1,
        "kind": "llm",
        "output": "json",
        "card": {
            "role": "入力をそのまま確認する。",
            "steps": ["JSONを出力する。"],
            "output_example": '{"value": "..."}',
        },
    }


def test_dag_moves_blocked_ready_claimed_done_and_completes(
    tmp_path: Path,
) -> None:
    definitions = {
        "D1.make": code_definition("D1.make", "make"),
        "D2.echo": llm_definition("D2.echo"),
    }
    orchestrator = Orchestrator(
        tmp_path,
        definitions,
        {"make": lambda context: {"value": "alpha"}},
        clock=FIXED_CLOCK,
    )
    run_id = orchestrator.create_run(
        seed=123,
        tasks=[
            TaskSpec("D2.echo", "D2.echo", deps=("D1.make",)),
            TaskSpec("D1.make", "D1.make"),
        ],
    )

    before = orchestrator.load_run(run_id)
    assert before["tasks"]["D1.make"]["state"] == "ready"
    assert before["tasks"]["D2.echo"]["state"] == "blocked"

    after_code = orchestrator.advance(run_id)
    assert after_code["tasks"]["D1.make"]["state"] == "done"
    assert after_code["tasks"]["D2.echo"]["state"] == "ready"
    assert after_code["status"] == "active"

    claimed = orchestrator.transition_task(run_id, "D2.echo", "claimed")
    assert claimed["tasks"]["D2.echo"]["state"] == "claimed"
    assert claimed["tasks"]["D2.echo"]["claim"] is None

    completed = orchestrator.complete_task(
        run_id, "D2.echo", {"value": "echoed"}
    )
    assert completed["tasks"]["D2.echo"]["state"] == "done"
    assert completed["status"] == "completed"
    output_path = (
        tmp_path / "runs" / run_id / "tasks" / "D2.echo" / "output.json"
    )
    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "value": "echoed"
    }


def test_code_task_receives_resolved_inputs_and_writes_output(tmp_path: Path) -> None:
    definitions = {
        "D1.make": code_definition("D1.make", "make"),
        "D2.double": code_definition(
            "D2.double",
            "double",
            inputs={
                "value": {
                    "label": "値",
                    "from": "D1.make",
                    "select": "value",
                }
            },
        ),
    }
    seen: list[int] = []

    def double(context):
        seen.append(context.inputs["value"])
        return {"value": context.inputs["value"] * 2}

    orchestrator = Orchestrator(
        tmp_path,
        definitions,
        {"make": lambda context: {"value": 4}, "double": double},
        clock=FIXED_CLOCK,
    )
    run_id = orchestrator.create_run(
        seed=123,
        tasks={
            "D1.make": {"type": "D1.make"},
            "D2.double": {"type": "D2.double", "deps": ["D1.make"]},
        },
    )

    manifest = orchestrator.advance(run_id)
    assert seen == [4]
    assert manifest["status"] == "completed"
    assert manifest["tasks"]["D2.double"]["state"] == "done"


def test_code_task_can_add_only_dependent_tasks_and_they_join_the_dag(
    tmp_path: Path,
) -> None:
    definitions = {
        "D1.expand": code_definition("D1.expand", "expand"),
        "D2.item": llm_definition("D2.item"),
    }

    def expand(context):
        return CodeTaskResult(
            output={"items": ["a", "b"]},
            add_tasks=[
                TaskSpec("D2.item-a", "D2.item", deps=(context.task_id,), index=("a",)),
                TaskSpec("D2.item-b", "D2.item", deps=(context.task_id,), index=("b",)),
            ],
        )

    orchestrator = Orchestrator(
        tmp_path, definitions, {"expand": expand}, clock=FIXED_CLOCK
    )
    run_id = orchestrator.create_run(seed=123, tasks=[TaskSpec("D1.expand", "D1.expand")])

    manifest = orchestrator.advance(run_id)
    assert manifest["tasks"]["D2.item-a"]["state"] == "ready"
    assert manifest["tasks"]["D2.item-b"]["state"] == "ready"
    assert manifest["status"] == "active"

    with pytest.raises(InvalidTransition):
        orchestrator.transition_task(run_id, "D2.item-a", "ready")

    orchestrator.transition_task(run_id, "D2.item-a", "claimed")
    orchestrator.complete_task(run_id, "D2.item-a", {"value": "a"})
    orchestrator.transition_task(run_id, "D2.item-b", "claimed")
    manifest = orchestrator.complete_task(run_id, "D2.item-b", {"value": "b"})
    assert manifest["status"] == "completed"


def test_code_task_can_skip_unneeded_task_and_run_completes(tmp_path: Path) -> None:
    definitions = {
        "D1.choose": code_definition("D1.choose", "choose"),
        "D2.unused": code_definition("D2.unused", "unused"),
    }
    orchestrator = Orchestrator(
        tmp_path,
        definitions,
        {
            "choose": lambda context: CodeTaskResult(
                output={"chosen": True}, skip_tasks=["D2.unused"]
            ),
            "unused": lambda context: {"ran": True},
        },
        clock=FIXED_CLOCK,
    )
    run_id = orchestrator.create_run(
        seed=123,
        tasks={
            "D1.choose": {"type": "D1.choose"},
            "D2.unused": {"type": "D2.unused", "deps": ["D1.choose"]},
        },
    )

    manifest = orchestrator.advance(run_id)
    assert manifest["tasks"]["D1.choose"]["state"] == "done"
    assert manifest["tasks"]["D2.unused"]["state"] == "skipped"
    assert manifest["status"] == "completed"


def test_code_exception_fails_task_and_stalls_run(tmp_path: Path) -> None:
    definitions = {"D1.broken": code_definition("D1.broken", "broken")}
    orchestrator = Orchestrator(
        tmp_path,
        definitions,
        {"broken": lambda context: (_ for _ in ()).throw(RuntimeError("boom"))},
        clock=FIXED_CLOCK,
    )
    run_id = orchestrator.create_run(seed=123)

    manifest = orchestrator.advance(run_id)
    assert manifest["status"] == "stalled"
    assert manifest["tasks"]["D1.broken"]["state"] == "failed"
    assert "boom" in manifest["tasks"]["D1.broken"]["error"]


def test_dynamic_task_without_parent_dependency_is_rejected(tmp_path: Path) -> None:
    definitions = {
        "D1.expand": code_definition("D1.expand", "expand"),
        "D2.item": llm_definition("D2.item"),
    }
    orchestrator = Orchestrator(
        tmp_path,
        definitions,
        {
            "expand": lambda context: CodeTaskResult(
                add_tasks=[TaskSpec("D2.item-a", "D2.item")]
            )
        },
        clock=FIXED_CLOCK,
    )
    run_id = orchestrator.create_run(seed=123, tasks=[TaskSpec("D1.expand", "D1.expand")])

    manifest = orchestrator.advance(run_id)
    assert manifest["status"] == "stalled"
    assert manifest["tasks"]["D1.expand"]["state"] == "failed"
    assert "depend" in manifest["tasks"]["D1.expand"]["error"]
    assert "D2.item-a" not in manifest["tasks"]


def test_graph_rejects_missing_dependency_and_cycle(tmp_path: Path) -> None:
    definitions = {
        "D1.a": code_definition("D1.a", "a"),
        "D2.b": code_definition("D2.b", "b"),
    }
    orchestrator = Orchestrator(tmp_path, definitions, clock=FIXED_CLOCK)
    with pytest.raises(DAGError):
        orchestrator.create_run(
            seed=123,
            tasks={"D1.a": {"type": "D1.a", "deps": ["missing"]}},
        )
    with pytest.raises(DAGError):
        orchestrator.create_run(
            seed=124,
            tasks={
                "D1.a": {"type": "D1.a", "deps": ["D2.b"]},
                "D2.b": {"type": "D2.b", "deps": ["D1.a"]},
            },
        )
