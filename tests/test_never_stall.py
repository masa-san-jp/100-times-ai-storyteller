"""ADR-0009: a run never stalls on undecided or rejected material.

Executors fail at random (tasks end ``skipped`` at their retry limit) and
checks report contradictions at random.  Whatever happens, the engine must
reach ``completed``: no code task may fail on skipped inputs or on an
invalidation of an already skipped task.
"""

from __future__ import annotations

import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from storyteller.new_run import create_story_orchestrator
from storyteller.orchestrator import Orchestrator
from tests.test_story_cli_e2e import _claimed_task, _cli, _fake_output, _task_input

_CHECKS = {"S8.detail", "S4.consistency", "S8.compare", "S8.condition_check", "F4.review"}
_FORMATS = ("novel", "screenplay", "slides")


@pytest.mark.parametrize("seed", range(1, 9))
def test_story_run_completes_under_random_failures_and_rejections(tmp_path: Path, seed: int) -> None:
    data_dir = tmp_path / "data"
    free_input = tmp_path / "free.md"
    free_input.write_text("静かな町で、失われた記録を探す。\n\n境界を守る仕組みが、暮らしの選択を変えていく。\n",
                          encoding="utf-8")
    created = _cli(data_dir, "new", "--free", str(free_input), "--scale", "dev", "--seed", str(seed))
    assert created.returncode == 0, created.stderr
    run_id = created.stdout.strip()
    base = create_story_orchestrator(data_dir)
    now = datetime.now(timezone.utc)
    harness = Orchestrator(data_dir, base.task_definitions, base.code_handlers, clock=lambda: now,
                           harness_root=base.definition_root, repository_root=base.repository_root,
                           harness_kind=base.harness_kind)
    rng = random.Random(seed)
    doomed: dict[str, bool] = {}
    _drive(harness, data_dir, run_id, rng, doomed)
    manifest = harness.load_run(run_id)
    assert not _failed(manifest)
    assert manifest["status"] == "completed"
    # The same holds for the formats built from that canonical story.
    for format_name in _FORMATS:
        started = _cli(data_dir, "format", "--run", run_id, "--as", format_name)
        assert started.returncode == 0, started.stderr
    _drive(harness, data_dir, run_id, rng, doomed)
    manifest = harness.load_run(run_id)
    assert not _failed(manifest)
    assert manifest["status"] == "completed"


def _failed(manifest: dict) -> dict:
    return {task_id: task["error"] for task_id, task in manifest["tasks"].items() if task["state"] == "failed"}


def _drive(harness: Orchestrator, data_dir: Path, run_id: str, rng: random.Random, doomed: dict[str, bool]) -> None:
    for _ in range(20000):
        claimed = harness.claim_next(run_id, executor_id="chaos", isolation="none")
        if claimed is None:
            break
        task_id, task = _claimed_task(data_dir, run_id, claimed["ticket"])
        if task_id not in doomed:
            doomed[task_id] = rng.random() < 0.15
        if doomed[task_id]:
            # This task never yields an output and ends skipped at its limit.
            harness.record_executor_failure(claimed["ticket"], "混乱テスト：出力なし")
            continue
        inputs = _task_input(data_dir, run_id, task_id)
        try:
            if task["type"] == "F4.review":
                output = {"answer": "no", "reason": "混乱テストで問題なし。", "source_quote": "", "body_quote": "", "subject": ""}
            elif task["type"].startswith("F2."):
                output = _format_body(task_id, inputs)
            else:
                output = _fake_output(task_id, task, inputs, send_comparison_yes=False)
        except (KeyError, IndexError, TypeError, ValueError, AssertionError):
            # Inputs built from undecided material the filler does not cover.
            harness.record_executor_failure(claimed["ticket"], "混乱テスト：出力を作れない")
            continue
        if task["type"] in _CHECKS and rng.random() < 0.3:
            output = {**output, "answer": "yes", "reason": "混乱テストの矛盾。"} if isinstance(output, dict) else output
        if not isinstance(output, str):
            output = json.dumps(output, ensure_ascii=False)
        harness.submit(claimed["ticket"], output)


def _format_body(task_id: str, inputs: dict) -> str:
    """A body of the part's events, padded with short sentences into its range."""
    events = inputs["events"]
    if isinstance(events, str):
        events = json.loads(events)
    minimum = int(re.match(r"(\d+)", inputs["range"]).group(1)) + 300
    fields = [dict(item.split("：", 1) for item in event["text"].split("／") if "：" in item) for event in events]
    actions = [f"{field.get('行動', '').rstrip('。')}。{field.get('結果', '').rstrip('。')}。" for field in fields]
    pad = [f"{word}が静かに揺れた。" for word in ("灯り", "風", "水面", "影", "旗", "草", "雲", "砂", "声", "扉")]
    if task_id.endswith("slides"):
        slides = [f"# 場面{index}\n- {action}\n<!-- {action} -->" for index, action in enumerate(actions, 1)]
        body = "\n\n".join(slides)
        while len(body) < minimum:
            body = body.replace(" -->", f"{pad[len(body) % len(pad)]} -->", 1)
        return body
    separator = "\n\n" if task_id.endswith("screenplay") else "\n"
    paragraphs = list(actions)
    index = 0
    while len(separator.join(paragraphs)) < minimum:
        paragraphs.append(pad[index % len(pad)] + "（" + str(index) + "）")
        index += 1
    return separator.join(paragraphs)
