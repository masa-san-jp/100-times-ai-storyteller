"""P4-01: st auto --workers N recovers from workers that die."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from storyteller.adapter import AdapterError, AdapterResponse, AutoRunner, ModelConfig
from storyteller.auto_workers import WorkersFailed, run_supervised
from storyteller.orchestrator import Orchestrator


def _definition(task_id: str) -> dict:
    return {
        "id": task_id, "version": 1, "kind": "llm", "element": "text",
        "output": "text", "lease_minutes": 1,
        "card": {"role": "書く。", "steps": ["1語だけ書く。"]},
    }


DEFINITIONS = {name: _definition(name) for name in ("D1.a", "D1.b", "D1.c")}


def _model() -> ModelConfig:
    return ModelConfig("test:model", "ollama", "http://127.0.0.1:11434", 0.2, 50, 1000, "off")


class _Adapter:
    def __init__(self, model: ModelConfig) -> None:
        pass

    def complete(self, card: str, **kwargs):
        return AdapterResponse("ok", "stop")


def test_supervisor_replaces_a_dead_worker(capsys) -> None:
    calls = {"n": 0}
    guard = threading.Lock()

    def worker() -> None:
        with guard:
            calls["n"] += 1
            first = calls["n"] == 1
        if first:
            raise RuntimeError("boom")

    run_supervised(1, worker)
    assert calls["n"] == 2
    assert "異常終了" in capsys.readouterr().err


def test_supervisor_fails_when_every_worker_keeps_dying() -> None:
    def worker() -> None:
        raise RuntimeError("always")

    with pytest.raises(WorkersFailed):
        run_supervised(2, worker, max_restarts=1)


def test_surviving_worker_keeps_going_when_another_gives_up() -> None:
    done = threading.Event()
    guard = threading.Lock()
    calls = {"n": 0}

    def worker() -> None:
        with guard:
            calls["n"] += 1
            first = calls["n"] == 1
        if first:
            raise RuntimeError("dies")  # restarted, then gives up (max_restarts=0)
        done.set()

    run_supervised(2, worker, max_restarts=0)
    assert done.is_set()


def test_workers_finish_all_tasks_although_one_worker_crashes(tmp_path: Path, capsys) -> None:
    offset = {"seconds": 0}
    harness = Orchestrator(
        tmp_path, DEFINITIONS,
        clock=lambda: datetime.now(timezone.utc) + timedelta(seconds=offset["seconds"]),
    )
    run_id = harness.create_run(seed=1)
    crashed = threading.Event()

    class CrashingOnce(_Adapter):
        def complete(self, card: str, **kwargs):
            if not crashed.is_set():
                crashed.set()
                offset["seconds"] = 120  # its claim's lease lapses
                raise KeyError("unexpected bug")
            return super().complete(card, **kwargs)

    AutoRunner(
        tmp_path, _model(), harness, adapter_factory=CrashingOnce
    ).run(run_id=run_id, workers=2, until_empty=True)
    states = {t["state"] for t in harness.load_run(run_id)["tasks"].values()}
    assert states == {"done"}
    assert crashed.is_set()


def test_all_workers_dead_is_an_error(tmp_path: Path) -> None:
    harness = Orchestrator(tmp_path, DEFINITIONS)
    run_id = harness.create_run(seed=1)

    class Broken:
        def __init__(self, model: ModelConfig) -> None:
            raise RuntimeError("cannot start")

    with pytest.raises(AdapterError):
        AutoRunner(tmp_path, _model(), harness, adapter_factory=Broken).run(
            run_id=run_id, workers=2, until_empty=True
        )


_KILLED_WORKER = """
import os, signal, sys
from pathlib import Path
from storyteller.orchestrator import Orchestrator
defs = __import__("json").loads(sys.argv[2])
o = Orchestrator(Path(sys.argv[1]), defs)
claim = o.claim_next(sys.argv[3], executor_id="doomed", isolation="adapter")
assert claim is not None
print("claimed", flush=True)
os.kill(os.getpid(), signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
"""


def test_killed_worker_process_is_taken_over_after_lease_expiry(tmp_path: Path) -> None:
    harness = Orchestrator(tmp_path, DEFINITIONS)
    run_id = harness.create_run(seed=1)
    proc = subprocess.run(
        [sys.executable, "-c", _KILLED_WORKER, str(tmp_path), json.dumps(DEFINITIONS), run_id],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode != 0 and "claimed" in proc.stdout
    claimed = [
        t for t in harness.load_run(run_id)["tasks"].values() if t["state"] == "claimed"
    ]
    assert len(claimed) == 1

    killed_id = next(
        task_id for task_id, t in harness.load_run(run_id)["tasks"].items() if t["state"] == "claimed"
    )

    # Before the lease ends the claim is respected; afterwards another worker takes over.
    offset = {"seconds": 0}
    live = Orchestrator(
        tmp_path, DEFINITIONS,
        clock=lambda: datetime.now(timezone.utc) + timedelta(seconds=offset["seconds"]),
    )
    assert live.claim_next(run_id) is not None  # a different task
    assert live.load_run(run_id)["tasks"][killed_id]["state"] == "claimed"
    offset["seconds"] = 120

    AutoRunner(tmp_path, _model(), live, adapter_factory=_Adapter).run(
        run_id=run_id, workers=2, until_empty=True
    )
    tasks = live.load_run(run_id)["tasks"]
    assert {t["state"] for t in tasks.values()} == {"done"}
    assert list(live.task_dir(run_id, killed_id).glob("claim.expired.*.json"))


def test_until_empty_does_not_wait_for_live_claim_of_stalled_run(tmp_path: Path, monkeypatch) -> None:
    from storyteller.manifest import load_manifest, write_manifest

    harness = Orchestrator(tmp_path, DEFINITIONS)
    run_id = harness.create_run(seed=1)
    assert harness.claim_next(run_id, executor_id="other") is not None
    path = tmp_path / "runs" / run_id / "manifest.json"
    manifest = load_manifest(path)
    manifest["status"] = "stalled"
    write_manifest(path, manifest)
    monkeypatch.setattr("storyteller.adapter.time.sleep", lambda _d: pytest.fail("waited"))

    AutoRunner(tmp_path, _model(), harness, adapter_factory=_Adapter).run(
        run_id=run_id, workers=1, until_empty=True
    )
    claimed = [t for t in harness.load_run(run_id)["tasks"].values() if t["state"] == "claimed"]
    assert len(claimed) == 1  # the live claim was left alone


def test_single_worker_is_also_restarted(tmp_path: Path) -> None:
    harness = Orchestrator(tmp_path, DEFINITIONS)
    run_id = harness.create_run(seed=1)
    made = {"n": 0}

    class FailsOnFirstStart(_Adapter):
        def __init__(self, model: ModelConfig) -> None:
            made["n"] += 1
            if made["n"] == 1:
                raise RuntimeError("cannot start")

    AutoRunner(tmp_path, _model(), harness, adapter_factory=FailsOnFirstStart).run(
        run_id=run_id, workers=1, until_empty=True
    )
    assert {t["state"] for t in harness.load_run(run_id)["tasks"].values()} == {"done"}


def test_base_exception_in_every_thread_worker_is_a_failure() -> None:
    def worker() -> None:
        raise SystemExit(3)

    with pytest.raises(WorkersFailed):
        run_supervised(2, worker)


def test_base_exception_propagates_for_single_worker() -> None:
    def worker() -> None:
        raise SystemExit(3)

    with pytest.raises(SystemExit):
        run_supervised(1, worker)
