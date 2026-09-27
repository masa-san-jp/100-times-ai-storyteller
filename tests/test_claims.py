from __future__ import annotations

import multiprocessing
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from storyteller.orchestrator import (
    InvalidClaimError,
    Orchestrator,
    TaskSpec,
)


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 27, 3, 15, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value


def llm_definition(task_id: str, *, lease_minutes: float = 30) -> dict:
    return {
        "id": task_id,
        "version": 1,
        "kind": "llm",
        "output": "json",
        "card": {
            "role": "与えられた入力を確認する。",
            "steps": ["JSONを出力する。"],
            "output_example": '{"value": "..."}',
        },
        "lease_minutes": lease_minutes,
    }


def code_definition(task_id: str) -> dict:
    return {
        "id": task_id,
        "version": 1,
        "kind": "code",
        "handler": "done",
    }


def test_claim_writes_manifest_claim_file_and_card(tmp_path: Path) -> None:
    clock = MutableClock()
    orchestrator = Orchestrator(
        tmp_path,
        {"D1.echo": llm_definition("D1.echo", lease_minutes=0.05)},
        clock=clock,
    )
    run_id = orchestrator.create_run(seed=123)

    result = orchestrator.claim_next(
        run_id,
        executor_id="codex.worker-1",
        isolation="permission",
    )

    assert result is not None
    assert set(result) == {"ticket", "card", "lease_expires_at"}
    assert result["lease_expires_at"] == "2026-09-27T03:15:03Z"
    assert result["card"].startswith(f"# タスク {result['ticket']}\n")

    task_dir = tmp_path / "runs" / run_id / "tasks" / "D1.echo"
    claim_file = json.loads((task_dir / "claim.json").read_text(encoding="utf-8"))
    manifest = orchestrator.load_run(run_id)
    task = manifest["tasks"]["D1.echo"]
    assert task["state"] == "claimed"
    assert task["claim"] == {
        "ticket": result["ticket"],
        "executor_id": "codex.worker-1",
        "isolation": "permission",
        "lease_expires_at": result["lease_expires_at"],
    }
    assert claim_file["task_id"] == "D1.echo"
    assert claim_file["claimed_at"] == "2026-09-27T03:15:00Z"
    assert (task_dir / "card.md").read_text(encoding="utf-8") == result["card"]
    assert orchestrator.validate_claim(result["ticket"])["task_id"] == "D1.echo"


def test_claim_selection_uses_run_order_then_depth_then_task_id(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    definitions = {
        "D1.done": code_definition("D1.done"),
        "D1.a": llm_definition("D1.a"),
        "D9.z": llm_definition("D9.z"),
    }
    orchestrator = Orchestrator(
        tmp_path,
        definitions,
        {"done": lambda context: {"ok": True}},
        clock=clock,
    )
    first_run = orchestrator.create_run(
        seed=1,
        tasks=[
            TaskSpec("D1.done", "D1.done"),
            TaskSpec("D1.a", "D1.a", deps=("D1.done",)),
            TaskSpec("D9.z", "D9.z"),
        ],
    )
    clock.value += timedelta(seconds=1)
    second_run = orchestrator.create_run(seed=2)

    # D9.z has depth 0, so it precedes D1.a even though its ID is later.
    first = orchestrator.claim_next(executor_id="worker-1")
    assert first is not None
    first_manifest = orchestrator.load_run(first_run)
    assert first_manifest["tasks"]["D9.z"]["claim"]["ticket"] == first["ticket"]

    second = orchestrator.claim_next(executor_id="worker-2")
    assert second is not None
    first_manifest = orchestrator.load_run(first_run)
    assert first_manifest["tasks"]["D1.a"]["claim"]["ticket"] == second["ticket"]

    third = orchestrator.claim_next(executor_id="worker-3")
    assert third is not None
    second_manifest = orchestrator.load_run(second_run)
    assert second_manifest["tasks"]["D1.a"]["claim"]["ticket"] == third["ticket"]


def test_expired_claim_is_renamed_and_reclaimed(tmp_path: Path) -> None:
    clock = MutableClock()
    definition = llm_definition("D1.echo", lease_minutes=0.05)
    first = Orchestrator(tmp_path, {"D1.echo": definition}, clock=clock)
    run_id = first.create_run(seed=123)
    old = first.claim_next(run_id, executor_id="worker-old")
    assert old is not None

    task_dir = tmp_path / "runs" / run_id / "tasks" / "D1.echo"
    (task_dir / "claim.expired.1.json").write_text("previous\n", encoding="utf-8")
    clock.value += timedelta(seconds=3)
    second = Orchestrator(tmp_path, {"D1.echo": definition}, clock=clock)
    new = second.claim_next(run_id, executor_id="worker-new")
    assert new is not None
    assert new["ticket"] != old["ticket"]

    current_claim = json.loads((task_dir / "claim.json").read_text(encoding="utf-8"))
    assert current_claim["ticket"] == new["ticket"]
    expired = task_dir / "claim.expired.2.json"
    assert expired.exists()
    assert json.loads(expired.read_text(encoding="utf-8"))["ticket"] == old["ticket"]
    assert (task_dir / "claim.expired.1.json").read_text(encoding="utf-8") == "previous\n"
    with pytest.raises(InvalidClaimError):
        second.validate_claim(old["ticket"])


def test_revoke_claim_returns_task_to_ready_and_invalidates_ticket(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    definition = llm_definition("D1.echo")
    orchestrator = Orchestrator(tmp_path, {"D1.echo": definition}, clock=clock)
    run_id = orchestrator.create_run(seed=123)
    claimed = orchestrator.claim_next(run_id, executor_id="worker")
    assert claimed is not None

    manifest = orchestrator.revoke_claim(run_id, "D1.echo", reason="依存先が無効化された")
    task_dir = tmp_path / "runs" / run_id / "tasks" / "D1.echo"
    assert manifest["tasks"]["D1.echo"]["state"] == "ready"
    assert manifest["tasks"]["D1.echo"]["claim"] is None
    assert (task_dir / "claim.revoked.1.json").exists()
    with pytest.raises(InvalidClaimError):
        orchestrator.validate_claim(claimed["ticket"])

    replacement = orchestrator.claim_next(run_id, executor_id="worker-2")
    assert replacement is not None
    assert replacement["ticket"] != claimed["ticket"]


def _claim_in_process(data_dir: str, run_id: str, queue, start_event) -> None:
    definition = llm_definition("D1.echo")
    orchestrator = Orchestrator(
        data_dir,
        {"D1.echo": definition},
        clock=lambda: datetime(2026, 9, 27, 3, 15, tzinfo=timezone.utc),
    )
    start_event.wait(10)
    try:
        queue.put(orchestrator.claim_next(run_id, executor_id=f"worker-{orchestrator.data_dir.name}"))
    except Exception as error:  # pragma: no cover - surfaced by the parent
        queue.put({"error": repr(error)})


def test_two_processes_cannot_claim_the_same_task(tmp_path: Path) -> None:
    definition = llm_definition("D1.echo")
    orchestrator = Orchestrator(tmp_path, {"D1.echo": definition})
    run_id = orchestrator.create_run(seed=123)
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    start_event = context.Event()
    processes = [
        context.Process(
            target=_claim_in_process,
            args=(str(tmp_path), run_id, queue, start_event),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start_event.set()
    results = [queue.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)
    assert all(result is None or "error" not in result for result in results)
    assert sum(result is not None for result in results) == 1
    manifest = orchestrator.load_run(run_id)
    assert manifest["tasks"]["D1.echo"]["state"] == "claimed"
