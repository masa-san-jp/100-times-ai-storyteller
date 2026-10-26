"""P4-07: a dev-scale batch of 10 finishes with executor crashes mixed in."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jsonschema import Draft202012Validator

from storyteller.new_run import create_story_orchestrator
from storyteller.report_layout import report_dir
from storyteller.world_facts import specialize_fact_definition

from tests.test_dev_scale import FREE_INPUT
from tests.test_phase2_e2e import _salt
from tests.test_report import REPORT_SCHEMA, _answer
from tests.test_story_cli_e2e import (
    _claimed_task,
    _cli,
    _fake_output,
    _manifest,
    _task_input,
)

BATCH_SIZE = 10
CRASH_EVERY = 7


def _batch(data_dir: Path, batch_id: str) -> dict:
    return json.loads((data_dir / "batches" / batch_id / "manifest.json").read_text(encoding="utf-8"))


def test_dev_batch_of_ten_completes_despite_executor_crashes(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    free_input = tmp_path / "free.md"
    free_input.write_text(FREE_INPUT, encoding="utf-8")
    created = _cli(
        data_dir, "new", "--free", str(free_input), "--scale", "dev",
        "--count", str(BATCH_SIZE), "--seed", "7",
    )
    assert created.returncode == 0, created.stderr
    batch_id = created.stdout.strip()
    assert len(_batch(data_dir, batch_id)["runs"]) == BATCH_SIZE

    now = [datetime.now(timezone.utc)]
    harness = create_story_orchestrator(data_dir)
    harness._clock = lambda: now[0]
    claims = crashes = 0
    driven: set[str] = set()
    # A duplicate run is replaced by a new run of the same batch (S9), so the
    # runs are read again from the batch manifest until none is left to drive.
    while True:
        pending = [
            entry["run_id"] for entry in _batch(data_dir, batch_id)["runs"]
            if entry["run_id"] not in driven
        ]
        if not pending:
            break
        for run_id in pending:
            driven.add(run_id)
            for _ in range(1000):
                claimed = harness.claim_next(run_id, executor_id="dev", isolation="none")
                if claimed is None:
                    break
                claims += 1
                if claims % CRASH_EVERY == 0:
                    # The executor dies holding the claim: it never submits, and
                    # the lease runs out before the next claim.
                    crashes += 1
                    now[0] += timedelta(hours=1)
                    continue
                task_id, task = _claimed_task(data_dir, run_id, claimed["ticket"])
                inputs = _task_input(data_dir, run_id, task_id)
                output = _fake_output(task_id, task, inputs, send_comparison_yes=False)
                if task["type"] == "S7.event" and task["index"][1] in {"what", "result"}:
                    # Runs tell different stories, as different executors would;
                    # the duplicate check compares the events' what/result.
                    output = output + _salt(run_id, task_id)
                definition = specialize_fact_definition(harness.task_definitions[task["type"]], inputs)
                if definition["output"] != "text":
                    output = json.dumps(output, ensure_ascii=False)
                submitted = harness.submit(claimed["ticket"], output)
                assert submitted.accepted, (task_id, submitted.errors)
            else:
                raise AssertionError(f"{run_id} did not finish")

    assert crashes >= BATCH_SIZE
    batch = _batch(data_dir, batch_id)
    statuses = {entry["run_id"]: _manifest(data_dir, entry["run_id"])["status"] for entry in batch["runs"]}
    assert set(statuses.values()) <= {"completed", "duplicate"}, statuses
    completed = sorted(run_id for run_id, status in statuses.items() if status == "completed")
    assert len(completed) == BATCH_SIZE == batch["completed"], statuses
    for run_id in statuses:
        manifest = _manifest(data_dir, run_id)
        assert not [t for t in manifest["tasks"].values() if t["state"] in {"failed", "skipped"}]

    status = _cli(data_dir, "status", "--batch", batch_id)
    assert status.returncode == 0, status.stderr

    # Completion condition 2: the trend report of this batch.
    started = _cli(data_dir, "report", "--batch", batch_id)
    assert started.returncode == 0, started.stderr
    report_run_id = started.stdout.strip()
    answer = tmp_path / "answer.txt"
    state: dict = {}
    for _ in range(1000):
        claimed = _cli(data_dir, "next", "--run", report_run_id, "--json")
        if claimed.returncode == 2:
            break
        assert claimed.returncode == 0, claimed.stderr
        claim = json.loads(claimed.stdout)
        answer.write_text(_answer(claim["card"], state), encoding="utf-8")
        submitted = _cli(data_dir, "submit", claim["ticket"], str(answer))
        assert submitted.returncode == 0, submitted.stderr
    else:
        raise AssertionError("the report did not finish")
    directory = report_dir(data_dir, batch_id)
    assert _manifest_at(directory)["status"] == "completed"
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(json.loads(REPORT_SCHEMA.read_text(encoding="utf-8")))
    assert list(validator.iter_errors(report)) == []
    assert report["run_ids"] == completed
    assert (directory / "report.md").is_file()


def _manifest_at(directory: Path) -> dict:
    return json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
