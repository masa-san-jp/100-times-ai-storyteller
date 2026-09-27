from __future__ import annotations

import json
from pathlib import Path

from tests.support.dummy_executor import (
    DummyExecutor,
    Response,
    item_id_from_card,
    sleep_for_dummy_lease,
)


class E2EPlan:
    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self.story_calls = 0

    def __call__(self, card: str) -> Response:
        if "次のJSONだけを出力すること" in card:
            item_id = item_id_from_card(card)
            assert item_id is not None
            self.calls[item_id] = self.calls.get(item_id, 0) + 1
            if item_id == "d1" and self.calls[item_id] == 1:
                return Response({"text": "INVALID", "sources": [item_id]})
            if item_id == "d2" and self.calls[item_id] == 1:
                return Response("これは JSON ではありません")
            return Response(
                {
                    "text": {"d1": "alpha", "d2": "beta", "d3": "gamma"}[item_id],
                    "sources": [item_id],
                }
            )

        self.story_calls += 1
        if self.story_calls == 1:
            return Response("alpha beta", truncated=True)
        return Response(" gamma。")


def _manifest(data_dir: Path, run_id: str) -> dict:
    path = data_dir / "runs" / run_id / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_dummy_harness_runs_end_to_end_with_invalidation_retry_continuation_and_restart(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    executor = DummyExecutor(data_dir)
    run_id = executor.run_new_dummy(seed=101)
    plan = E2EPlan()

    executor.run(plan, stop_after_successes=1)
    executor.run(plan)

    manifest = _manifest(data_dir, run_id)
    assert manifest["status"] == "completed"
    assert all(
        manifest["tasks"][task_id]["state"] == "done"
        for task_id in (
            "D1.items",
            "D2.echo-d1",
            "D2.echo-d2",
            "D2.echo-d3",
            "D3.check",
            "D4.story",
            "D5.assemble",
        )
    )
    assert manifest["tasks"]["D2.echo-d1"]["invalidations"] == 1
    assert manifest["tasks"]["D2.echo-d1"]["attempt"] == 1
    assert manifest["tasks"]["D2.echo-d2"]["tries"] == 1
    assert manifest["tasks"]["D4.story"]["continuation_step"] == 1

    d2_attempts = data_dir / "runs" / run_id / "tasks" / "D2.echo-d2" / "attempts"
    assert (d2_attempts / "1.json").is_file()
    assembled = data_dir / "runs" / run_id / "tasks" / "D5.assemble" / "output.json"
    assert json.loads(assembled.read_text(encoding="utf-8")) == {
        "text": "alpha beta gamma。"
    }


def test_dummy_harness_failed_task_can_be_retried_from_cli(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    executor = DummyExecutor(data_dir)
    run_id = executor.run_new_dummy(seed=102)
    failed_task = "D2.echo-d1"

    executor.run(lambda card: Response("not json"))

    manifest = _manifest(data_dir, run_id)
    assert manifest["status"] == "stalled"
    assert manifest["tasks"][failed_task]["state"] == "failed"
    retried = executor._run("retry", failed_task)
    assert retried.returncode == 0, retried.stderr

    executor.run(
        lambda card: Response(
            {
                "text": "alpha" if item_id_from_card(card) == "d1" else "beta",
                "sources": [item_id_from_card(card)],
            }
        )
        if "次のJSONだけを出力すること" in card
        else Response("alpha beta gamma。")
    )
    assert _manifest(data_dir, run_id)["status"] == "completed"


def test_two_subprocess_executors_do_not_claim_the_same_task(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    executor = DummyExecutor(data_dir)
    run_id = executor.run_new_dummy(seed=103)
    results = executor.concurrent_next(tmp_path)

    successful = [result for result in results if result["returncode"] == 0]
    assert len(successful) == 2
    tickets = {json.loads(result["stdout"])["ticket"] for result in successful}
    manifest = _manifest(data_dir, run_id)
    claimed_task_ids = [
        task_id
        for task_id, task in manifest["tasks"].items()
        if (task.get("claim") or {}).get("ticket") in tickets
    ]
    assert len(claimed_task_ids) == len(set(claimed_task_ids)) == len(tickets)


def test_expired_dummy_lease_can_be_reclaimed_by_another_executor(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    first_executor = DummyExecutor(data_dir)
    run_id = first_executor.run_new_dummy(seed=104)
    first = first_executor.next()
    assert first is not None

    sleep_for_dummy_lease()

    second_executor = DummyExecutor(data_dir, run_id)
    second = second_executor.next()
    assert second is not None
    assert second.ticket != first.ticket
    task_dir = data_dir / "runs" / run_id / "tasks" / "D2.echo-d1"
    assert (task_dir / "claim.expired.1.json").is_file()
