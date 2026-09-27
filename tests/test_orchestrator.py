from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from storyteller.orchestrator import (
    CodeTaskResult,
    DAGError,
    InvalidTransition,
    Orchestrator,
    TaskSpec,
    _update_run_status,
)
from storyteller.manifest import validate_manifest
from storyteller.seed import derive_task_seed
from storyteller.cards import TaskCardError


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

    claimed = orchestrator._transition_task(run_id, "D2.echo", "claimed")
    assert claimed["tasks"]["D2.echo"]["state"] == "claimed"
    assert claimed["tasks"]["D2.echo"]["claim"] is None

    completed = orchestrator._complete_task(
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
        orchestrator._transition_task(run_id, "D2.item-a", "ready")

    orchestrator._transition_task(run_id, "D2.item-a", "claimed")
    orchestrator._complete_task(run_id, "D2.item-a", {"value": "a"})
    orchestrator._transition_task(run_id, "D2.item-b", "claimed")
    manifest = orchestrator._complete_task(run_id, "D2.item-b", {"value": "b"})
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
                add_tasks=[TaskSpec("D2.item-a", "D2.item", index=("a",))]
            )
        },
        clock=FIXED_CLOCK,
    )
    run_id = orchestrator.create_run(seed=123, tasks=[TaskSpec("D1.expand", "D1.expand")])

    manifest = orchestrator.advance(run_id)
    assert manifest["status"] == "stalled"
    assert manifest["tasks"]["D1.expand"]["state"] == "failed"
    assert "追加元" in manifest["tasks"]["D1.expand"]["error"]
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


def test_graph_rejects_empty_dag(tmp_path: Path) -> None:
    definitions = {"D1.a": code_definition("D1.a", "a")}
    orchestrator = Orchestrator(tmp_path, definitions, clock=FIXED_CLOCK)

    with pytest.raises(DAGError, match="must not be empty"):
        orchestrator.create_run(seed=123, tasks=[])


@pytest.mark.parametrize(
    ("addition", "message"),
    [
        (
            [
                TaskSpec(
                    "D2.item-a", "D2.item", deps=("D1.expand",), index=("a",)
                ),
                TaskSpec(
                    "D2.item-a", "D2.item", deps=("D1.expand",), index=("a",)
                ),
            ],
            "重複",
        ),
        (
            [
                TaskSpec(
                    "D2.item-a",
                    "D2.item",
                    deps=("D1.expand", "D2.item-b"),
                    index=("a",),
                ),
                TaskSpec(
                    "D2.item-b",
                    "D2.item",
                    deps=("D1.expand", "D2.item-a"),
                    index=("b",),
                ),
            ],
            "cycle",
        ),
        (
            [TaskSpec("D9.unknown-a", "D9.unknown", index=("a",))],
            "定義",
        ),
        (
            [TaskSpec("D2.item-a", "D2.item", deps=("D1.expand",))],
            "task id",
        ),
    ],
)
def test_invalid_dynamic_result_fails_before_output_or_manifest_addition(
    tmp_path: Path,
    addition: list[TaskSpec],
    message: str,
) -> None:
    definitions = {
        "D1.expand": code_definition("D1.expand", "expand"),
        "D2.item": llm_definition("D2.item"),
    }
    orchestrator = Orchestrator(
        tmp_path,
        definitions,
        {"expand": lambda context: CodeTaskResult(output={"ok": True}, add_tasks=addition)},
        clock=FIXED_CLOCK,
    )
    run_id = orchestrator.create_run(
        seed=123, tasks=[TaskSpec("D1.expand", "D1.expand")]
    )

    manifest = orchestrator.advance(run_id)

    parent = manifest["tasks"]["D1.expand"]
    assert parent["state"] == "failed"
    assert message in parent["error"]
    assert not (tmp_path / "runs" / run_id / "tasks" / "D1.expand" / "output.json").exists()
    assert list(manifest["tasks"]) == ["D1.expand"]
    validate_manifest(manifest)


def test_graph_rejects_input_source_not_provided_by_a_dependency(
    tmp_path: Path,
) -> None:
    definitions = {
        "D1.source": code_definition("D1.source", "source"),
        "D2.use": code_definition(
            "D2.use",
            "use",
            inputs={
                "value": {
                    "label": "値",
                    "from": "D1.source",
                    "select": "value",
                }
            },
        ),
        "D3.other": code_definition("D3.other", "other"),
    }
    orchestrator = Orchestrator(tmp_path, definitions, clock=FIXED_CLOCK)

    with pytest.raises(DAGError, match="dependency of type 'D1.source'"):
        orchestrator.create_run(
            seed=123,
            tasks={
                "D2.use": {"type": "D2.use", "deps": ["D3.other"]},
                "D3.other": {"type": "D3.other"},
            },
        )


def test_inputs_ignore_unrelated_and_claimed_outputs(tmp_path: Path) -> None:
    definitions = {
        "D1.source": code_definition("D1.source", "source"),
        "D2.use": code_definition(
            "D2.use",
            "use",
            inputs={
                "value": {
                    "label": "値",
                    "from": "D1.source",
                    "select": "value",
                }
            },
        ),
    }
    orchestrator = Orchestrator(
        tmp_path,
        definitions,
        {"source": lambda context: {"value": "unrelated"}},
        clock=FIXED_CLOCK,
    )
    run_id = orchestrator.create_run(
        seed=123,
        tasks=[
            TaskSpec("D1.source", "D1.source"),
            TaskSpec("D1.source-claimed", "D1.source", index=("claimed",)),
            TaskSpec("D2.use", "D2.use", deps=("D1.source-claimed",)),
        ],
    )
    orchestrator._transition_task(run_id, "D1.source-claimed", "claimed")
    orchestrator.advance(run_id)
    orchestrator._write_task_output(run_id, "D1.source-claimed", {"value": "claimed"})

    with pytest.raises(TaskCardError, match="入力の生成に失敗しました"):
        orchestrator._build_context(
            orchestrator.load_run(run_id), run_id, "D2.use"
        )


def test_code_task_seed_randomness_and_task_record_copy_are_deterministic(
    tmp_path: Path,
) -> None:
    definitions = {"D1.random": code_definition("D1.random", "random")}
    observed: list[tuple[int, str]] = []

    def random_task(context):
        context.task["state"] = "tampered"
        value = context.random.random()
        observed.append((context.seed, context.task["state"]))
        return {"value": value}

    outputs = []
    for directory in (tmp_path / "one", tmp_path / "two"):
        orchestrator = Orchestrator(
            directory, definitions, {"random": random_task}, clock=FIXED_CLOCK
        )
        run_id = orchestrator.create_run(seed=123)
        manifest = orchestrator.advance(run_id)
        outputs.append(manifest["tasks"]["D1.random"]["state"])
        output_path = directory / "runs" / run_id / "tasks" / "D1.random" / "output.json"
        outputs.append(output_path.read_text(encoding="utf-8"))

    assert outputs[0] == outputs[2] == "done"
    assert outputs[1] == outputs[3]
    assert observed == [
        (derive_task_seed(123, "D1.random", 0), "tampered"),
        (derive_task_seed(123, "D1.random", 0), "tampered"),
    ]


def test_forbidden_transitions_and_failed_reason(tmp_path: Path) -> None:
    definitions = {
        "D1.code": code_definition("D1.code", "code"),
        "D2.llm": llm_definition("D2.llm"),
    }
    orchestrator = Orchestrator(
        tmp_path, definitions, {"code": lambda context: {"ok": True}}, clock=FIXED_CLOCK
    )
    run_id = orchestrator.create_run(
        seed=123,
        tasks={
            "D1.code": {"type": "D1.code"},
            "D2.llm": {"type": "D2.llm", "deps": ["D1.code"]},
        },
    )
    with pytest.raises(InvalidTransition):
        orchestrator._transition_task(run_id, "D2.llm", "ready")
    with pytest.raises(InvalidTransition):
        orchestrator._transition_task(run_id, "D1.code", "failed")
    orchestrator.advance(run_id)
    orchestrator._transition_task(run_id, "D2.llm", "claimed")
    with pytest.raises(InvalidTransition):
        orchestrator._transition_task(run_id, "D2.llm", "skipped")
    orchestrator._complete_task(run_id, "D2.llm", {"ok": True})
    for state in ("ready", "claimed", "failed", "skipped"):
        with pytest.raises(InvalidTransition):
            orchestrator._transition_task(run_id, "D2.llm", state)

    failed_run = orchestrator.create_run(
        seed=0x100000, tasks=[TaskSpec("D1.code", "D1.code")]
    )
    with pytest.raises(InvalidTransition):
        orchestrator._transition_task(failed_run, "D1.code", "failed")
    failed = orchestrator._transition_task(
        failed_run, "D1.code", "failed", reason="テストで失敗"
    )
    assert failed["tasks"]["D1.code"]["error"] == "テストで失敗"


def test_ready_to_done_is_not_restricted_by_kind_and_public_seams_are_internal(
    tmp_path: Path,
) -> None:
    definitions = {"D1.llm": llm_definition("D1.llm")}
    orchestrator = Orchestrator(tmp_path, definitions, clock=FIXED_CLOCK)
    run_id = orchestrator.create_run(seed=123)

    assert not hasattr(orchestrator, "transition_task")
    assert not hasattr(orchestrator, "complete_task")
    assert not hasattr(orchestrator, "add_tasks")
    manifest = orchestrator._complete_task(run_id, "D1.llm", {"value": "cached"})
    assert manifest["status"] == "completed"


def test_run_status_priority_and_run_id_format(tmp_path: Path) -> None:
    definitions = {"D1.code": code_definition("D1.code", "code")}
    orchestrator = Orchestrator(tmp_path, definitions, clock=FIXED_CLOCK)
    run_id = orchestrator.create_run(seed=123)
    assert re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", run_id)
    manifest = orchestrator.load_run(run_id)
    manifest["tasks"]["D1.code"]["state"] = "failed"
    manifest["status"] = "active"
    _update_run_status(manifest)
    assert manifest["status"] == "stalled"
    manifest["status"] = "halted"
    _update_run_status(manifest)
    assert manifest["status"] == "halted"


def test_create_run_failure_removes_run_directory(tmp_path: Path) -> None:
    definitions = {"D1.code": code_definition("D1.code", "code")}
    orchestrator = Orchestrator(tmp_path, definitions, clock=FIXED_CLOCK)

    with pytest.raises(TypeError):
        orchestrator.create_run(seed=123, input_data=object())

    assert not list((tmp_path / "runs").iterdir())


def test_dynamic_manifest_and_new_orchestrator_can_continue(tmp_path: Path) -> None:
    definitions = {
        "D1.expand": code_definition("D1.expand", "expand"),
        "D2.item": llm_definition("D2.item"),
    }

    def expand(context):
        return CodeTaskResult(
            output={"items": ["a"]},
            add_tasks=[
                TaskSpec(
                    "D2.item-a",
                    "D2.item",
                    deps=(context.task_id,),
                    index=("a",),
                )
            ],
        )

    first = Orchestrator(
        tmp_path, definitions, {"expand": expand}, clock=FIXED_CLOCK
    )
    run_id = first.create_run(seed=123, tasks=[TaskSpec("D1.expand", "D1.expand")])
    first_manifest = first.advance(run_id)
    validate_manifest(first_manifest)
    assert first_manifest["tasks"]["D2.item-a"]["state"] == "ready"

    second = Orchestrator(tmp_path, definitions, clock=FIXED_CLOCK)
    continued = second.advance(run_id)
    assert continued["tasks"]["D1.expand"]["state"] == "done"
    completed = second._complete_task(run_id, "D2.item-a", {"value": "a"})
    assert completed["status"] == "completed"
