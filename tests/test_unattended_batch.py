"""P4-07: a dev-scale batch of 10 finishes with executor crashes mixed in."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from storyteller.new_run import create_story_orchestrator
from storyteller.world_facts import specialize_fact_definition

from tests.test_dev_scale import FREE_INPUT
from tests.test_story_cli_e2e import (
    _claimed_task,
    _cli,
    _fake_output,
    _manifest,
    _task_input,
)

BATCH_SIZE = 10
CRASH_EVERY = 7


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
    batch = json.loads((data_dir / "batches" / batch_id / "manifest.json").read_text(encoding="utf-8"))
    run_ids = [entry["run_id"] for entry in batch["runs"]]
    assert len(run_ids) == BATCH_SIZE

    now = [datetime.now(timezone.utc)]
    harness = create_story_orchestrator(data_dir)
    harness._clock = lambda: now[0]
    claims = crashes = 0
    for run_id in run_ids:
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
            definition = specialize_fact_definition(harness.task_definitions[task["type"]], inputs)
            if definition["output"] != "text":
                output = json.dumps(output, ensure_ascii=False)
            submitted = harness.submit(claimed["ticket"], output)
            assert submitted.accepted, (task_id, submitted.errors)
        else:
            raise AssertionError(f"{run_id} did not finish")

    assert crashes >= BATCH_SIZE
    for run_id in run_ids:
        manifest = _manifest(data_dir, run_id)
        assert manifest["status"] in {"completed", "duplicate"}, run_id
        assert not [t for t in manifest["tasks"].values() if t["state"] in {"failed", "skipped"}]

    status = _cli(data_dir, "status", "--batch", batch_id)
    assert status.returncode == 0, status.stderr
