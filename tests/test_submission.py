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
    assert json.loads((task_dir / "input.json").read_text(encoding="utf-8")) == {
        "given": "abcdefg"
    }

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
    assert json.loads(
        (task_dir / "attempts" / "1.json").read_text(encoding="utf-8")
    )["output"]

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


def test_code_result_invalidates_outermost_unique_target_only(tmp_path: Path) -> None:
    clock = Clock()
    definitions = {
        "D1.a": {"id": "D1.a", "version": 1, "kind": "code", "handler": "a"},
        "D2.b": {"id": "D2.b", "version": 1, "kind": "code", "handler": "b"},
        "D3.check": {
            "id": "D3.check",
            "version": 1,
            "kind": "code",
            "handler": "check",
        },
    }
    calls = 0

    def check(context):
        nonlocal calls
        calls += 1
        if calls == 1:
            context.invalidate_task("D1.a", "a が不合格")
            context.invalidate_task("D2.b", "b が不合格")
            context.invalidate_task("D2.b", "b が重複")
        return CodeTaskResult(output={})

    orchestrator = Orchestrator(
        tmp_path,
        definitions,
        {"a": lambda context: {}, "b": lambda context: {}, "check": check},
        clock=clock,
    )
    run_id = orchestrator.create_run(
        seed=1,
        tasks=[
            TaskSpec("D1.a", "D1.a"),
            TaskSpec("D2.b", "D2.b", deps=("D1.a",)),
            TaskSpec("D3.check", "D3.check", deps=("D2.b",)),
        ],
    )

    manifest = orchestrator.advance(run_id)

    assert manifest["tasks"]["D1.a"]["state"] == "done"
    assert manifest["tasks"]["D1.a"]["invalidations"] == 0
    assert manifest["tasks"]["D2.b"]["invalidations"] == 1
    assert manifest["tasks"]["D2.b"]["state"] == "done"
    assert any(
        event["from"] == "done" and event["to"] == "blocked"
        for event in manifest["tasks"]["D3.check"]["history"]
    )
    assert sum(
        event.get("reason") == "b が不合格"
        for event in manifest["tasks"]["D2.b"]["history"]
    ) == 1


def test_invalidation_limit_keeps_output_and_dependents_unchanged(
    tmp_path: Path,
) -> None:
    clock = Clock()
    definitions = {
        "D1.answer": llm_definition(
            "D1.answer", validate={}, max_invalidations=0
        ),
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
    orchestrator._complete_task(run_id, "D1.answer", {"text": "one"})
    orchestrator._complete_task(run_id, "D2.after", {"text": "two"})

    result = orchestrator.invalidate_task(run_id, "D1.answer", reason="上限超過")
    target_dir = tmp_path / "runs" / run_id / "tasks" / "D1.answer"
    task = result["tasks"]["D1.answer"]

    assert task["state"] == "failed"
    assert task["error"] == "上限超過"
    assert json.loads(
        (target_dir / "output.json").read_text(encoding="utf-8")
    ) == {"text": "one"}
    assert result["tasks"]["D2.after"]["state"] == "done"
    assert json.loads(
        (tmp_path / "runs" / run_id / "tasks" / "D2.after" / "output.json").read_text(
            encoding="utf-8"
        )
    ) == {"text": "two"}


def test_retry_failed_blocks_until_dependencies_are_done(tmp_path: Path) -> None:
    clock = Clock()
    definitions = {
        "D1.source": {
            "id": "D1.source",
            "version": 1,
            "kind": "code",
            "handler": "source",
        },
        "D2.answer": llm_definition("D2.answer", max_attempts=1),
    }
    orchestrator = Orchestrator(
        tmp_path,
        definitions,
        {"source": lambda context: {"ok": True}},
        clock=clock,
    )
    run_id = orchestrator.create_run(
        seed=1,
        input_data={"given": "a"},
        tasks=[
            TaskSpec("D1.source", "D1.source"),
            TaskSpec("D2.answer", "D2.answer", deps=("D1.source",)),
        ],
    )
    orchestrator.advance(run_id)
    claim = orchestrator.claim_next(run_id, executor_id="worker")
    assert claim is not None
    assert not orchestrator.submit(claim["ticket"], '{"text": 3}').accepted
    orchestrator.invalidate_task(run_id, "D1.source", reason="依存先を差し戻す")

    retried = orchestrator.retry_failed(run_id, "D2.answer")

    assert retried["tasks"]["D2.answer"]["state"] == "blocked"


def test_rejected_submission_uses_unused_attempt_number_and_removes_claim(
    tmp_path: Path,
) -> None:
    clock = Clock()
    definition = llm_definition(max_attempts=1)
    orchestrator = Orchestrator(tmp_path, {"D1.echo": definition}, clock=clock)
    run_id = orchestrator.create_run(seed=1, input_data={"given": "a"})
    claimed = orchestrator.claim_next(run_id, executor_id="worker")
    assert claimed is not None
    task_dir = tmp_path / "runs" / run_id / "tasks" / "D1.echo"
    (task_dir / "attempts").mkdir(exist_ok=True)
    (task_dir / "attempts" / "1.json").write_text("old", encoding="utf-8")
    (task_dir / "output.json").write_text('{"stale": true}', encoding="utf-8")

    result = orchestrator.submit(claimed["ticket"], '{"text": 3}')

    assert not result.accepted
    manifest = orchestrator.load_run(run_id)
    assert manifest["tasks"]["D1.echo"]["tries"] == 1
    assert manifest["tasks"]["D1.echo"]["state"] == "failed"
    assert not (task_dir / "claim.json").exists()
    assert not (task_dir / "output.json").exists()
    attempt = json.loads(
        (task_dir / "attempts" / "2.json").read_text(encoding="utf-8")
    )
    assert attempt["tries"] == manifest["tasks"]["D1.echo"]["tries"]
    assert (task_dir / "attempts" / "1.json").read_text(encoding="utf-8") == "old"


def test_proper_noun_warning_is_task_prefixed_only_after_pass(tmp_path: Path) -> None:
    clock = Clock()
    definition = llm_definition(
        validate={
            "checks": [
                {"no_new_proper_nouns": {"mode": "warn"}},
                {"max_chars": {"field": "text", "n": 2}},
            ]
        }
    )
    orchestrator = Orchestrator(tmp_path, {"D1.echo": definition}, clock=clock)
    first_run = orchestrator.create_run(seed=1, input_data={"given": "a"})
    first_claim = orchestrator.claim_next(first_run, executor_id="worker")
    assert first_claim is not None
    rejected = orchestrator.submit(first_claim["ticket"], '{"text": "新名称"}')
    assert not rejected.accepted
    assert orchestrator.load_run(first_run)["warnings"] == []

    clock.value += timedelta(seconds=1)
    second_run = orchestrator.create_run(seed=2, input_data={"given": "a"})
    second_claim = orchestrator.claim_next(second_run, executor_id="worker")
    assert second_claim is not None
    accepted = orchestrator.submit(second_claim["ticket"], '{"text": "AB"}')
    assert accepted.accepted
    warnings = orchestrator.load_run(second_run)["warnings"]
    assert len(warnings) == 1
    assert warnings[0].startswith("D1.echo: no_new_proper_nouns:")


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
    tracked.unlink()
    assert orchestrator.advance(run_id)["status"] == "halted"


def test_harness_change_is_checked_for_stalled_claim_submit_and_validation(
    tmp_path: Path,
) -> None:
    clock = Clock()
    harness_root = tmp_path / "repo"
    schema_dir = harness_root / "schemas"
    schema_dir.mkdir(parents=True)
    tracked = schema_dir / "task.schema.json"
    tracked.write_text("one", encoding="utf-8")
    definition = llm_definition(max_attempts=1)
    orchestrator = Orchestrator(
        tmp_path / "data",
        {
            "D1.first": dict(definition, id="D1.first"),
            "D2.second": dict(definition, id="D2.second"),
        },
        clock=clock,
        harness_root=harness_root,
    )
    run_id = orchestrator.create_run(
        seed=1,
        input_data={"given": "a"},
        tasks=[
            TaskSpec("D1.first", "D1.first"),
            TaskSpec("D2.second", "D2.second"),
        ],
    )
    first = orchestrator.claim_next(run_id, executor_id="worker-1")
    second = orchestrator.claim_next(run_id, executor_id="worker-2")
    assert first is not None and second is not None
    assert not orchestrator.submit(first["ticket"], '{"text": 3}').accepted
    assert orchestrator.load_run(run_id)["status"] == "stalled"

    tracked.write_text("two", encoding="utf-8")
    with pytest.raises(HaltedRunError):
        orchestrator.claim_task(run_id, "D2.second", executor_id="worker-3")
    assert orchestrator.load_run(run_id)["status"] == "halted"

    orchestrator.resume_harness_change(run_id)
    tracked.write_text("three", encoding="utf-8")
    with pytest.raises(HaltedRunError):
        orchestrator.validate_claim(second["ticket"])
    assert orchestrator.load_run(run_id)["status"] == "halted"

    orchestrator.resume_harness_change(run_id)
    tracked.write_text("four", encoding="utf-8")
    with pytest.raises(HaltedRunError):
        orchestrator.submit(second["ticket"], '{"text": "ok"}')
    assert orchestrator.load_run(run_id)["status"] == "halted"


def test_empty_harness_snapshot_detects_added_file(tmp_path: Path) -> None:
    clock = Clock()
    harness_root = tmp_path / "repo"
    harness_root.mkdir()
    orchestrator = Orchestrator(
        tmp_path / "data",
        {"D1.echo": llm_definition()},
        clock=clock,
        harness_root=harness_root,
    )
    run_id = orchestrator.create_run(seed=1, input_data={"given": "a"})
    (harness_root / "harness").mkdir()
    (harness_root / "harness" / "new.yaml").write_text("new", encoding="utf-8")

    assert orchestrator.advance(run_id)["status"] == "halted"


def test_definition_and_repository_roots_are_separate_for_dummy_harness(
    tmp_path: Path,
) -> None:
    clock = Clock()
    repository_root = tmp_path / "repo"
    definition_root = repository_root / "src" / "storyteller" / "dev" / "dummy"
    (definition_root / "schemas").mkdir(parents=True)
    (definition_root / "schemas" / "output.schema.json").write_text(
        json.dumps({"type": "object", "properties": {"text": {"type": "string"}}}),
        encoding="utf-8",
    )
    (repository_root / "tables").mkdir(parents=True)
    (repository_root / "tables" / "common_words.yaml").write_text(
        "- Tokyo\n", encoding="utf-8"
    )
    definition = llm_definition(
        validate={
            "schema": "schemas/output.schema.json",
            "checks": [
                {"no_new_proper_nouns": {"mode": "fail"}}
            ],
        }
    )
    orchestrator = Orchestrator(
        tmp_path / "data",
        {"D1.echo": definition},
        clock=clock,
        harness_root=definition_root,
        repository_root=repository_root,
    )
    run_id = orchestrator.create_run(seed=1, input_data={"given": "a"})
    (repository_root / "schemas").mkdir()
    (repository_root / "schemas" / "outside.json").write_text(
        "outside", encoding="utf-8"
    )
    claim = orchestrator.claim_next(run_id, executor_id="worker")
    assert claim is not None
    assert orchestrator.submit(claim["ticket"], '{"text": "Tokyo"}').accepted
    manifest = orchestrator.load_run(run_id)
    assert (
        "src/storyteller/dev/dummy/schemas/output.schema.json"
        in manifest["harness"]
    )
    assert "schemas/outside.json" not in manifest["harness"]
