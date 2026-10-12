"""P4-07: a dev-scale batch of 10 completes without human help even when
executors die mid-task (an abandoned claim is taken over after its lease)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from storyteller.batch import create_batch, load_batch_manifest
from storyteller.new_run import create_story_orchestrator
from storyteller.orchestrator import Orchestrator
from storyteller.world_facts import specialize_fact_definition

from tests.test_story_cli_e2e import _claimed_task, _fake_output, _manifest, _task_input

ROOT = Path(__file__).parents[1]
EXECUTORS = 4
CRASH_EVERY = 11  # every 11th claim is abandoned: the executor "died"


# 約2,700タスク（1 claim あたり約0.15秒、全体で10分前後）のため、通常の pytest では回さない。
# 実行: STORYTELLER_SLOW_TESTS=1 uv run pytest tests/test_unattended_batch.py
@pytest.mark.skipif(not os.environ.get("STORYTELLER_SLOW_TESTS"), reason="長時間のテスト（STORYTELLER_SLOW_TESTS=1 で実行）")
def test_dev_batch_of_ten_completes_with_executor_crashes(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    source = tmp_path / "free.md"
    source.write_text("静かな町で、失われた記録を探す。\n\n境界を守る仕組みが、暮らしの選択を変えていく。\n", encoding="utf-8")
    batch_id = create_batch(
        data_dir, source, input_kind="free", count=10, preset="dev",
        axis_overrides={}, seed=7, parts=None, plot_type=None, repository_root=ROOT,
    )

    now = [datetime.now(timezone.utc)]
    base = create_story_orchestrator(data_dir)
    harness = Orchestrator(
        data_dir, base.task_definitions, base.code_handlers, clock=lambda: now[0],
        harness_root=base.definition_root, repository_root=base.repository_root,
        harness_kind=base.harness_kind,
    )

    claims = crashes = 0
    for step in range(20000):
        progressed = False
        for executor in range(EXECUTORS):
            run_ids = [r["run_id"] for r in load_batch_manifest(data_dir, batch_id)["runs"]]
            run_id = claimed = None
            for candidate in run_ids[executor % len(run_ids):] + run_ids[:executor % len(run_ids)]:
                claimed = harness.claim_next(candidate, executor_id=f"exec-{executor}", isolation="none")
                if claimed is not None:
                    run_id = candidate
                    break
            if claimed is None:
                continue
            progressed = True
            claims += 1
            if claims % CRASH_EVERY == 0:
                crashes += 1
                now[0] += timedelta(hours=3)  # the lease runs out
                continue
            task_id, task = _claimed_task(data_dir, run_id, claimed["ticket"])
            inputs = _task_input(data_dir, run_id, task_id)
            output = _fake_output(task_id, task, inputs, send_comparison_yes=False)
            definition = specialize_fact_definition(harness.task_definitions[task["type"]], inputs)
            if definition["output"] != "text":
                output = json.dumps(output, ensure_ascii=False)
            submitted = harness.submit(claimed["ticket"], output)
            assert submitted.accepted, (task_id, submitted.errors)
        if not progressed:
            break
    else:
        raise AssertionError("バッチが上限回数で終わりませんでした")

    assert crashes >= 5
    batch = load_batch_manifest(data_dir, batch_id)
    finished = [r for r in batch["runs"] if r.get("status") != "duplicate"]
    states = {r["run_id"]: _manifest(data_dir, r["run_id"])["status"] for r in batch["runs"]}
    assert len(finished) >= 10, batch["runs"]
    for entry in finished:
        assert states[entry["run_id"]] == "completed", (entry, states)
