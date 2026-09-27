from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import storyteller.orchestrator as orchestrator_module
from storyteller.orchestrator import (
    CodeTaskResult,
    HaltedRunError,
    InvalidClaimError,
    InvalidTransition,
    Orchestrator,
    TaskSpec,
)


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 27, 3, 15, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value


def llm_definition(task_id: str = "D1.echo", **extra: object) -> dict:
    definition = {
        "id": task_id,
        "version": 1,
        "kind": "llm",
        "output": "json",
        "inputs": {
            "given": {
                "label": "入力",
                "from": "input",
                "select": "input.given",
                "required": True,
                "truncate": "head",
            }
        },
        "card": {
            "role": "入力を確認する。",
            "steps": ["JSONを出力する。"],
            "output_example": '{"text": "...", "sources": ["a"]}',
        },
        "validate": {"checks": ["sources_exist", {"max_chars": {"field": "text", "n": 20}}]},
        "max_input_chars": 14,
    }
    definition.update(extra)
    return definition


def test_submit_uses_the_truncated_card_input_and_records_rejection(tmp_path: Path) -> None:
    clock = Clock()
    orchestrator = Orchestrator(tmp_path, {"D1.echo": llm_definition()}, clock=clock)
    run_id = orchestrator.create_run(seed=1, input_data={"given": "abcdefghijk"})
    claimed = orchestrator.claim_next(run_id, executor_id="worker")
    assert claimed is not None
    task_dir = tmp_path / "runs" / run_id / "tasks" / "D1.echo"
    assert json.loads((task_dir / "input.json").read_text()) == {"given": "abcdefg"}

    rejected = orchestrator.submit(
        claimed["ticket"],
        json.dumps({"text": "ok", "sources": ["missing"]}),
    )
    assert not rejected.accepted
    manifest = orchestrator.load_run(run_id)
    task = manifest["tasks"]["D1.echo"]
    assert task["state"] == "ready"
    assert task["tries"] == 1
    assert task["attempt"] == 1
    assert "sources_exist" in task["error"]
    assert json.loads((task_dir / "attempts" / "1.json").read_text())["output"]

    replacement = orchestrator.claim_next(run_id, executor_id="worker-2")
    assert replacement is not None
    accepted = orchestrator.submit(
        replacement["ticket"],
        json.dumps({"text": "ok", "sources": []}),
    )
    assert accepted.accepted
    assert orchestrator.load_run(run_id)["status"] == "completed"


def test_submit_reaches_failed_at_max_attempts_and_retry_resets_counters(
    tmp_path: Path,
) -> None:
    clock = Clock()
    definition = llm_definition(max_attempts=2)
    orchestrator = Orchestrator(tmp_path, {"D1.echo": definition}, clock=clock)
    run_id = orchestrator.create_run(seed=1, input_data={"given": "a"})
    for _ in range(2):
        claimed = orchestrator.claim_next(run_id, executor_id="worker")
        assert claimed is not None
        result = orchestrator.submit(claimed["ticket"], '{"text": "bad"}')
        assert not result.accepted
    task = orchestrator.load_run(run_id)["tasks"]["D1.echo"]
    assert task["state"] == "failed"
    assert orchestrator.load_run(run_id)["status"] == "stalled"

    retried = orchestrator.retry_failed(run_id, "D1.echo")
    task = retried["tasks"]["D1.echo"]
    assert task["state"] == "ready"
    assert task["tries"] == 0
    assert task["invalidations"] == 0
    assert task["attempt"] == 2
    assert task["error"] is None


def test_submit_rechecks_lease_under_manifest_lock(tmp_path: Path, monkeypatch) -> None:
    clock = Clock()
    orchestrator = Orchestrator(
        tmp_path,
        {"D1.echo": llm_definition(validate={}, lease_minutes=0.05)},
        clock=clock,
    )
    run_id = orchestrator.create_run(seed=1, input_data={"given": "a"})
    claimed = orchestrator.claim_next(run_id, executor_id="worker")
    assert claimed is not None
    original = orchestrator_module.validate_output

    def expire_before_commit(*args, **kwargs):
        result = original(*args, **kwargs)
        clock.value += timedelta(seconds=31)
        return result

    monkeypatch.setattr(orchestrator_module, "validate_output", expire_before_commit)
    with pytest.raises(InvalidClaimError):
        orchestrator.submit(claimed["ticket"], '{"text":"ok"}')
    assert orchestrator.load_run(run_id)["tasks"]["D1.echo"]["state"] == "claimed"


def test_invalidation_rejects_non_done_and_resets_downstream(tmp_path: Path) -> None:
    clock = Clock()
    definitions = {
        "D1.answer": llm_definition("D1.answer", validate={}),
        "D2.after": llm_definition("D2.after", validate={}),
    }
    orchestrator = Orchestrator(tmp_path, definitions, clock=clock)
    run_id = orchestrator.create_run(
        seed=1,
        input_data={"given": "a"},
        tasks=[
            TaskSpec("D1.answer", "D1.answer"),
            TaskSpec("D2.after", "D2.after", deps=("D1.answer",)),
        ],
    )
    first = orchestrator.claim_next(run_id, executor_id="worker")
    assert first is not None
    orchestrator.submit(first["ticket"], '{"text":"ok","sources":["a"]}')
    second = orchestrator.claim_next(run_id, executor_id="worker-2")
    assert second is not None
    manifest = orchestrator.invalidate_task(
        run_id, "D1.answer", reason="検査で不合格"
    )
    assert manifest["tasks"]["D1.answer"]["state"] == "ready"
    assert manifest["tasks"]["D1.answer"]["invalidations"] == 1
    assert manifest["tasks"]["D2.after"]["state"] == "blocked"
    with pytest.raises(InvalidClaimError):
        orchestrator.validate_claim(second["ticket"])

    with pytest.raises(InvalidTransition):
        orchestrator.invalidate_task(run_id, "D1.answer", reason="まだ未完了")


def test_code_task_can_stage_invalidation_of_its_dependency(tmp_path: Path) -> None:
    clock = Clock()
    definitions = {
        "D1.answer": llm_definition("D1.answer", validate={}),
        "D2.check": {
            "id": "D2.check",
            "version": 1,
            "kind": "code",
            "handler": "check",
        },
    }

    def check(context):
        context.invalidate_task("D1.answer", "検査タスクが差し戻した")
        return CodeTaskResult(output={"checked": True})

    orchestrator = Orchestrator(
        tmp_path, definitions, {"check": check}, clock=clock
    )
    run_id = orchestrator.create_run(
        seed=1,
        input_data={"given": "a"},
        tasks=[
            TaskSpec("D1.answer", "D1.answer"),
            TaskSpec("D2.check", "D2.check", deps=("D1.answer",)),
        ],
    )
    claim = orchestrator.claim_next(run_id, executor_id="worker")
    assert claim is not None
    orchestrator.submit(claim["ticket"], '{"text":"ok"}')
    manifest = orchestrator.advance(run_id)
    assert manifest["tasks"]["D1.answer"]["state"] == "ready"
    assert manifest["tasks"]["D2.check"]["state"] == "blocked"


def test_invalidation_limit_stalls_without_an_extra_invalidation(tmp_path: Path) -> None:
    clock = Clock()
    definition = llm_definition(validate={}, max_invalidations=1)
    orchestrator = Orchestrator(tmp_path, {"D1.echo": definition}, clock=clock)
    run_id = orchestrator.create_run(seed=1, input_data={"given": "a"})
    orchestrator._complete_task(run_id, "D1.echo", {"text": "one"})
    first = orchestrator.invalidate_task(run_id, "D1.echo", reason="first")
    assert first["tasks"]["D1.echo"]["invalidations"] == 1
    orchestrator._complete_task(run_id, "D1.echo", {"text": "two"})
    second = orchestrator.invalidate_task(run_id, "D1.echo", reason="second")
    task = second["tasks"]["D1.echo"]
    assert task["state"] == "failed"
    assert task["invalidations"] == 1


def test_harness_change_halts_and_resume_accepts_snapshot(tmp_path: Path) -> None:
    clock = Clock()
    harness_root = tmp_path / "repo"
    schema_dir = harness_root / "schemas"
    schema_dir.mkdir(parents=True)
    tracked = schema_dir / "task.schema.json"
    tracked.write_text("one", encoding="utf-8")
    orchestrator = Orchestrator(
        tmp_path / "data",
        {"D1.echo": llm_definition()},
        clock=clock,
        harness_root=harness_root,
    )
    run_id = orchestrator.create_run(seed=1, input_data={"given": "a"})
    tracked.write_text("two", encoding="utf-8")
    halted = orchestrator.advance(run_id)
    assert halted["status"] == "halted"
    with pytest.raises(HaltedRunError):
        orchestrator.claim_next(run_id, executor_id="worker")
    resumed = orchestrator.resume_harness_change(run_id)
    assert resumed["status"] == "active"
    assert orchestrator.claim_next(run_id, executor_id="worker") is not None
