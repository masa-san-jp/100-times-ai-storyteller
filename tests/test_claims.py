from __future__ import annotations

import multiprocessing
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import storyteller.orchestrator as orchestrator_module
from storyteller.orchestrator import (
    ClaimError,
    InvalidClaimError,
    Orchestrator,
    TaskSpec,
)
from storyteller.manifest import write_manifest


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


def test_default_executor_id_replaces_invalid_hostname_characters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        orchestrator_module.socket,
        "gethostname",
        lambda: "ci/runner:name with spaces",
    )

    executor_id = orchestrator_module._resolve_executor_id(None)

    assert executor_id == f"ci-runner-name-with-spaces-{orchestrator_module.os.getpid()}"


def test_default_executor_id_truncates_long_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hostname = "a" * 100
    monkeypatch.setattr(
        orchestrator_module.socket,
        "gethostname",
        lambda: hostname,
    )
    pid_suffix = f"-{orchestrator_module.os.getpid()}"

    executor_id = orchestrator_module._resolve_executor_id(None)

    assert executor_id == f"{hostname[:64 - len(pid_suffix)]}{pid_suffix}"
    assert len(executor_id) <= 64


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


def test_validate_claim_rejects_expired_ticket_before_reclaim(tmp_path: Path) -> None:
    clock = MutableClock()
    definition = llm_definition("D1.echo", lease_minutes=0.05)
    orchestrator = Orchestrator(tmp_path, {"D1.echo": definition}, clock=clock)
    run_id = orchestrator.create_run(seed=123)
    claimed = orchestrator.claim_next(run_id, executor_id="worker-old")
    assert claimed is not None

    clock.value += timedelta(seconds=3)
    with pytest.raises(InvalidClaimError):
        orchestrator.validate_claim(claimed["ticket"])


def test_claim_then_complete_removes_claim_and_allows_next_claim(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    definitions = {
        "D1.first": llm_definition("D1.first"),
        "D1.second": llm_definition("D1.second"),
    }
    orchestrator = Orchestrator(tmp_path, definitions, clock=clock)
    run_id = orchestrator.create_run(
        seed=123,
        tasks=[
            TaskSpec("D1.first", "D1.first"),
            TaskSpec("D1.second", "D1.second"),
        ],
    )

    first = orchestrator.claim_next(run_id, executor_id="worker-1")
    assert first is not None
    completed = orchestrator._complete_task(run_id, "D1.first", {"value": "done"})
    assert completed["tasks"]["D1.first"]["claim"] is None
    assert not (
        tmp_path / "runs" / run_id / "tasks" / "D1.first" / "claim.json"
    ).exists()

    second = orchestrator.claim_next(run_id, executor_id="worker-2")
    assert second is not None
    assert orchestrator.load_run(run_id)["tasks"]["D1.second"]["state"] == "claimed"


@pytest.mark.parametrize(
    ("state", "reason"),
    [("ready", "leaseが切れた"), ("failed", "処理に失敗した")],
)
def test_claim_cleanup_is_shared_by_ready_and_failed_transitions(
    tmp_path: Path, state: str, reason: str
) -> None:
    clock = MutableClock()
    definition = llm_definition("D1.echo")
    orchestrator = Orchestrator(tmp_path, {"D1.echo": definition}, clock=clock)
    run_id = orchestrator.create_run(seed=123)
    claimed = orchestrator.claim_next(run_id, executor_id="worker")
    assert claimed is not None

    orchestrator._transition_task(run_id, "D1.echo", state, reason=reason)
    task_dir = tmp_path / "runs" / run_id / "tasks" / "D1.echo"
    manifest = orchestrator.load_run(run_id)
    assert manifest["tasks"]["D1.echo"]["claim"] is None
    assert not (task_dir / "claim.json").exists()


def test_subsecond_lease_is_rounded_up_and_expires_normally(tmp_path: Path) -> None:
    clock = MutableClock()
    definition = llm_definition("D1.echo", lease_minutes=0.001)
    orchestrator = Orchestrator(tmp_path, {"D1.echo": definition}, clock=clock)
    run_id = orchestrator.create_run(seed=123)

    old = orchestrator.claim_next(run_id, executor_id="worker-old")
    assert old is not None
    assert old["lease_expires_at"] == "2026-09-27T03:15:01Z"

    clock.value += timedelta(seconds=1)
    new = orchestrator.claim_next(run_id, executor_id="worker-new")
    assert new is not None
    assert new["ticket"] != old["ticket"]


def test_claim_with_expiry_before_claimed_at_is_quarantined(tmp_path: Path) -> None:
    clock = MutableClock()
    definition = llm_definition("D1.echo")
    orchestrator = Orchestrator(tmp_path, {"D1.echo": definition}, clock=clock)
    run_id = orchestrator.create_run(seed=123)
    claimed = orchestrator.claim_next(run_id, executor_id="worker-old")
    assert claimed is not None
    task_dir = tmp_path / "runs" / run_id / "tasks" / "D1.echo"
    claim = json.loads((task_dir / "claim.json").read_text(encoding="utf-8"))
    claim["lease_expires_at"] = claim["claimed_at"]
    (task_dir / "claim.json").write_text(json.dumps(claim), encoding="utf-8")

    replacement = orchestrator.claim_next(run_id, executor_id="worker-new")
    assert replacement is not None
    assert (task_dir / "claim.expired.1.json").exists()


def test_missing_claim_on_claimed_task_is_recovered(tmp_path: Path) -> None:
    clock = MutableClock()
    definition = llm_definition("D1.echo")
    orchestrator = Orchestrator(tmp_path, {"D1.echo": definition}, clock=clock)
    run_id = orchestrator.create_run(seed=123)
    old = orchestrator.claim_next(run_id, executor_id="worker-old")
    assert old is not None
    task_dir = tmp_path / "runs" / run_id / "tasks" / "D1.echo"
    (task_dir / "claim.json").unlink()

    new = orchestrator.claim_next(run_id, executor_id="worker-new")
    assert new is not None
    assert new["ticket"] != old["ticket"]


def test_invalid_claim_is_quarantined_without_blocking_another_run(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    definition = llm_definition("D1.echo")
    orchestrator = Orchestrator(tmp_path, {"D1.echo": definition}, clock=clock)
    broken_run = orchestrator.create_run(seed=1)
    claimed = orchestrator.claim_next(broken_run, executor_id="worker-broken")
    assert claimed is not None
    broken_claim = (
        tmp_path / "runs" / broken_run / "tasks" / "D1.echo" / "claim.json"
    )
    broken_claim.write_text("{not-json", encoding="utf-8")

    clock.value += timedelta(seconds=1)
    healthy_run = orchestrator.create_run(seed=2)
    replacement = orchestrator.claim_next(executor_id="worker-recovery")
    assert replacement is not None
    healthy_claim = orchestrator.claim_next(healthy_run, executor_id="worker-healthy")
    assert healthy_claim is not None
    assert orchestrator.load_run(healthy_run)["tasks"]["D1.echo"]["state"] == "claimed"
    assert (
        tmp_path
        / "runs"
        / broken_run
        / "tasks"
        / "D1.echo"
        / "claim.expired.1.json"
    ).exists()


def test_orphan_claim_on_done_task_is_quarantined(tmp_path: Path) -> None:
    clock = MutableClock()
    definitions = {
        "D1.done": code_definition("D1.done"),
        "D1.echo": llm_definition("D1.echo"),
    }
    orchestrator = Orchestrator(
        tmp_path,
        definitions,
        {"done": lambda context: {"ok": True}},
        clock=clock,
    )
    run_id = orchestrator.create_run(
        seed=123,
        tasks=[TaskSpec("D1.done", "D1.done"), TaskSpec("D1.echo", "D1.echo")],
    )
    orchestrator.advance(run_id)
    done_task_dir = tmp_path / "runs" / run_id / "tasks" / "D1.done"
    (done_task_dir / "claim.json").write_text("orphan", encoding="utf-8")

    result = orchestrator.claim_next(run_id, executor_id="worker")
    assert result is not None
    assert (done_task_dir / "claim.expired.1.json").read_text(encoding="utf-8") == "orphan"


def test_revoke_claim_succeeds_when_claim_file_is_missing(tmp_path: Path) -> None:
    clock = MutableClock()
    definition = llm_definition("D1.echo")
    orchestrator = Orchestrator(tmp_path, {"D1.echo": definition}, clock=clock)
    run_id = orchestrator.create_run(seed=123)
    claimed = orchestrator.claim_next(run_id, executor_id="worker")
    assert claimed is not None
    task_dir = tmp_path / "runs" / run_id / "tasks" / "D1.echo"
    (task_dir / "claim.json").unlink()

    manifest = orchestrator.revoke_claim(run_id, "D1.echo")
    assert manifest["tasks"]["D1.echo"]["state"] == "ready"
    assert manifest["tasks"]["D1.echo"]["claim"] is None


def test_revoke_claim_rejects_a_mismatched_claim_file(tmp_path: Path) -> None:
    clock = MutableClock()
    definition = llm_definition("D1.echo")
    orchestrator = Orchestrator(tmp_path, {"D1.echo": definition}, clock=clock)
    run_id = orchestrator.create_run(seed=123)
    claimed = orchestrator.claim_next(run_id, executor_id="worker")
    assert claimed is not None
    task_dir = tmp_path / "runs" / run_id / "tasks" / "D1.echo"
    claim = json.loads((task_dir / "claim.json").read_text(encoding="utf-8"))
    claim["ticket"] = "0" * 32
    (task_dir / "claim.json").write_text(json.dumps(claim), encoding="utf-8")

    with pytest.raises(ClaimError):
        orchestrator.revoke_claim(run_id, "D1.echo")


def test_code_tasks_are_not_claimed_when_they_are_the_only_ready_tasks(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    definition = code_definition("D1.done")
    orchestrator = Orchestrator(
        tmp_path,
        {"D1.done": definition},
        {"done": lambda context: {"ok": True}},
        clock=clock,
    )
    run_id = orchestrator.create_run(seed=123)

    assert orchestrator.claim_next(run_id, executor_id="worker") is None
    assert orchestrator.load_run(run_id)["status"] == "completed"


def test_invalid_ticket_format_is_rejected(tmp_path: Path) -> None:
    orchestrator = Orchestrator(
        tmp_path,
        {"D1.echo": llm_definition("D1.echo")},
        clock=MutableClock(),
    )
    with pytest.raises(InvalidClaimError):
        orchestrator.validate_claim("not-a-ticket")


def test_halted_and_completed_runs_are_excluded_from_claim_selection(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    definition = llm_definition("D1.echo")
    orchestrator = Orchestrator(tmp_path, {"D1.echo": definition}, clock=clock)
    halted_run = orchestrator.create_run(seed=1)
    clock.value += timedelta(seconds=1)
    completed_run = orchestrator.create_run(seed=2)
    clock.value += timedelta(seconds=1)
    active_run = orchestrator.create_run(seed=3)
    for run_id, status in ((halted_run, "halted"), (completed_run, "completed")):
        manifest = orchestrator.load_run(run_id)
        manifest["status"] = status
        write_manifest(tmp_path / "runs" / run_id / "manifest.json", manifest)

    result = orchestrator.claim_next(executor_id="worker")
    assert result is not None
    assert orchestrator.load_run(active_run)["tasks"]["D1.echo"]["state"] == "claimed"
    assert orchestrator.load_run(halted_run)["tasks"]["D1.echo"]["state"] == "ready"
    assert orchestrator.load_run(completed_run)["tasks"]["D1.echo"]["state"] == "ready"


def test_broken_manifest_is_skipped_with_warning_in_selection_and_ticket_search(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = MutableClock()
    definition = llm_definition("D1.echo")
    orchestrator = Orchestrator(tmp_path, {"D1.echo": definition}, clock=clock)
    healthy_run = orchestrator.create_run(seed=1)
    broken_run = "20260927-031500-deadbe"
    broken_dir = tmp_path / "runs" / broken_run
    broken_dir.mkdir(parents=True)
    (broken_dir / "manifest.json").write_text("{broken", encoding="utf-8")

    result = orchestrator.claim_next(executor_id="worker")
    assert result is not None
    assert orchestrator.load_run(healthy_run)["tasks"]["D1.echo"]["state"] == "claimed"
    assert broken_run in capsys.readouterr().err

    with pytest.raises(InvalidClaimError):
        orchestrator.validate_claim("a" * 32)
    assert broken_run in capsys.readouterr().err


def test_card_failure_tries_next_task_in_the_same_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = MutableClock()
    definitions = {
        "D1.bad": llm_definition("D1.bad"),
        "D1.good": llm_definition("D1.good"),
    }
    orchestrator = Orchestrator(tmp_path, definitions, clock=clock)
    run_id = orchestrator.create_run(
        seed=123,
        tasks=[TaskSpec("D1.bad", "D1.bad"), TaskSpec("D1.good", "D1.good")],
    )
    original = orchestrator._build_task_card

    def fail_first(manifest, candidate_run_id, task_id, ticket):
        if task_id == "D1.bad":
            raise RuntimeError("card failed")
        return original(manifest, candidate_run_id, task_id, ticket)

    monkeypatch.setattr(orchestrator, "_build_task_card", fail_first)
    result = orchestrator.claim_next(run_id, executor_id="worker")
    assert result is not None
    manifest = orchestrator.load_run(run_id)
    assert manifest["tasks"]["D1.bad"]["state"] == "failed"
    assert manifest["tasks"]["D1.good"]["state"] == "claimed"


def test_o_excl_race_retries_in_the_same_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import storyteller.orchestrator as orchestrator_module

    clock = MutableClock()
    definition = llm_definition("D1.echo")
    orchestrator = Orchestrator(tmp_path, {"D1.echo": definition}, clock=clock)
    run_id = orchestrator.create_run(seed=123)
    original = orchestrator_module._create_claim_file
    calls = 0

    def collide_once(path, payload):
        nonlocal calls
        if calls == 0:
            calls += 1
            raise FileExistsError(path)
        return original(path, payload)

    monkeypatch.setattr(orchestrator_module, "_create_claim_file", collide_once)
    result = orchestrator.claim_next(run_id, executor_id="worker")
    assert result is not None
    assert calls == 1


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
