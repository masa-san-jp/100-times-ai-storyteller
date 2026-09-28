from __future__ import annotations

import random
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from storyteller.orchestrator import CodeTaskResult, Orchestrator, TaskSpec
from storyteller.story_s8 import story_s8_plan
from storyteller.story_s3 import story_s3_assign
from storyteller.story_s6 import story_s6_expand


ROOT = Path(__file__).parents[1]


def _slots() -> list[dict[str, object]]:
    return [
        {"id": "e001", "thread": "t001", "characters": [{"id": "c9"}]},
        {"id": "e002", "thread": "t002", "characters": [{"id": "c8"}]},
        {"id": "e003", "thread": "t001", "characters": [{"id": "c7"}]},
        {"id": "e004", "thread": "t002", "characters": [{"id": "c6"}]},
    ]


def _plan_context(
    event_id: str,
    slots: list[dict[str, object]],
    event_who: dict[str, list[str]],
) -> SimpleNamespace:
    dependency_outputs: dict[str, object] = {"S6.expand": {"slots": slots}}
    dependency_outputs.update(
        {
            f"S7.event-{slot_id}": {"who": who, "result": slot_id}
            for slot_id, who in event_who.items()
        }
    )
    return SimpleNamespace(
        task_id=f"S8.plan-{event_id}",
        task={"index": [event_id]},
        inputs={},
        dependency_outputs=dependency_outputs,
    )


def test_s8_plan_uses_s7_who_and_limits_targets_to_three() -> None:
    slots = [
        {"id": f"e{number:03d}", "thread": f"t{number:03d}", "characters": []}
        for number in range(1, 6)
    ]
    context = _plan_context(
        "e005",
        slots,
        {
            "e001": ["c1"],
            "e002": ["c2"],
            "e003": ["c3"],
            "e004": ["c4"],
            # These people differ from S6's characters on purpose.
            "e005": ["c1", "c2", "c3", "c4"],
        },
    )

    result = story_s8_plan(context)

    assert result.output["comparison_targets"] == ["e004", "e003", "e002"]
    assert len(result.output["comparison_targets"]) == 3
    assert [task.task_id for task in result.add_tasks] == [
        "S8.compare-e005-k1",
        "S8.compare-e005-k2",
        "S8.compare-e005-k3",
        "S8.judge-e005",
    ]
    assert all(task.deps == ("S8.plan-e005",) for task in result.add_tasks[:3])
    assert result.add_tasks[-1].deps == (
        "S8.plan-e005",
        "S8.compare-e005-k1",
        "S8.compare-e005-k2",
        "S8.compare-e005-k3",
    )


def test_s8_plan_keeps_same_thread_predecessor_and_then_nearest_who_match() -> None:
    context = _plan_context(
        "e004",
        _slots(),
        {"e001": ["c1"], "e002": ["c8"], "e003": ["c1"], "e004": ["c1"]},
    )

    result = story_s8_plan(context)

    # e002 is the same-thread predecessor even though it does not contain c1;
    # e003/e001 are selected only because c1 appears in their S7 `who`.
    assert result.output["comparison_targets"] == ["e002", "e003", "e001"]


def _code_definition(task_id: str, handler: str) -> dict[str, object]:
    return {"id": task_id, "version": 1, "kind": "code", "handler": handler}


def _llm_definition(task_id: str) -> dict[str, object]:
    return {
        "id": task_id,
        "version": 1,
        "kind": "llm",
        "output": "json",
        "card": {
            "role": "入力を確認する。",
            "steps": ["JSONを出力する。"],
            "output_example": '{"value": "..."}',
        },
    }


def _integration_definitions() -> dict[str, dict[str, object]]:
    definitions = {
        "S1.extract": _code_definition("S1.extract", "extract"),
        "S2.merge": _code_definition("S2.merge", "merge"),
        "S3.assign": _code_definition("S3.assign", "assign"),
        "S4.section": _llm_definition("S4.section"),
        "S4.item": _llm_definition("S4.item"),
        "S6.expand": _code_definition("S6.expand", "expand"),
        "S7.event": _llm_definition("S7.event"),
        "S8.plan": _code_definition("S8.plan", "plan"),
        "S8.compare": _llm_definition("S8.compare"),
        "S8.judge": _code_definition("S8.judge", "judge"),
        "S9.assemble": _code_definition("S9.assemble", "assemble"),
    }
    for task_type in (
        "S5.name",
        "S5.profile",
        "S5.intro",
        "S5.appearance",
        "S5.motive",
        "S5.catchphrase",
    ):
        definitions[task_type] = _llm_definition(task_type)
    return definitions


def _integration_pools() -> dict[str, list[dict[str, str]]]:
    axes = (
        "want",
        "ability",
        "duty",
        "taboo",
        "place",
        "era",
        "object",
        "age",
        "gender",
        "species",
    )
    return {
        axis: [
            {"id": f"{axis}:i{index:02d}", "text": f"入力由来の{axis}{index}"}
            for index in range(1, 12)
        ]
        for axis in axes
    }


def test_orchestrator_runs_s3_s6_and_s8_plan_with_test_only_definitions(
    tmp_path: Path,
) -> None:
    pools = _integration_pools()

    def merge(context):
        return CodeTaskResult(
            output={"pools": pools},
            add_tasks=[TaskSpec("S3.assign", "S3.assign", deps=(context.task_id,))],
        )

    orchestrator = Orchestrator(
        tmp_path / "data",
        _integration_definitions(),
        {
            "extract": lambda _context: {"materials": []},
            "merge": merge,
            "assign": story_s3_assign,
            "expand": story_s6_expand,
            "plan": story_s8_plan,
            "judge": lambda _context: {"invalid": False},
            "assemble": lambda _context: {"ok": True},
        },
        clock=lambda: datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc),
        harness_root=ROOT,
        repository_root=ROOT,
    )
    # The S4/S5 task outputs are injected through the internal test seam; the
    # production harness intentionally has no S7-S9 definition files yet.
    run_id = orchestrator.create_run(
        seed=12,
        input_data={"kind": "free", "source_sha256": "a" * 64, "paragraphs": []},
        input_type="free",
        scale={
            "preset": "vignette",
            "axes": {
                "time": "hours",
                "space": "spot",
                "cast": "1-2",
                "threads": "single",
                "change": "inner",
            },
            "overrides": {},
            "derived": {
                "events": 3,
                "threads": 1,
                "cast": 1,
                "parts": None,
                "world_sections": ["place"],
                "pool_need": {},
            },
        },
        table_snapshot={axis: 0 for axis in pools},
        task_specs=[
            TaskSpec("S1.extract-p001", "S1.extract", index=("p001",)),
            TaskSpec("S2.merge", "S2.merge", deps=("S1.extract-p001",)),
        ],
    )
    orchestrator.advance(run_id)
    manifest = orchestrator.load_run(run_id)
    assert manifest["tasks"]["S3.assign"]["state"] == "done"

    for task_id, task in list(manifest["tasks"].items()):
        if task["state"] != "ready" or task_id == "S6.expand":
            continue
        if task_id.startswith("S4."):
            output = {"body": "水路の音が境界を知らせる場所。", "sources": ["place:t1"]}
        elif task_id.startswith("S5.name-"):
            output = {"name": "カナ"}
        elif task_id.startswith("S5.motive-"):
            output = {"motive": "道を確かめたい。"}
        else:
            output = {"value": "ok"}
        orchestrator._complete_task(run_id, task_id, output)

    # Complete the synthetic S7/compare outputs as they become ready.  Each
    # advance call executes the real S6 or S8.plan code tasks in between.
    for _ in range(30):
        manifest = orchestrator.advance(run_id)
        pending = [
            (task_id, task)
            for task_id, task in manifest["tasks"].items()
            if task["state"] == "ready" and task["kind"] == "llm"
        ]
        if not pending:
            break
        for task_id, task in pending:
            if task_id.startswith("S5.name-"):
                output = {"name": "カナ"}
            elif task_id.startswith("S5.motive-"):
                output = {"motive": "道を確かめたい。"}
            elif task_id.startswith("S7.event-"):
                output = {"who": ["c1"], "result": task_id}
            else:
                output = {"answer": "no", "reason": "整合している", "sources": []}
            orchestrator._complete_task(run_id, task_id, output)

    manifest = orchestrator.load_run(run_id)
    assert manifest["tasks"]["S6.expand"]["state"] == "done", manifest["tasks"]["S6.expand"]["error"]
    assert all(
        task["state"] == "done"
        for task_id, task in manifest["tasks"].items()
        if task_id.startswith("S8.plan-")
    )
    assert "S7.event" not in {
        path.name for path in (ROOT / "harness" / "story" / "tasks").iterdir()
    }
    assert any(task_id.startswith("S8.compare-") for task_id in manifest["tasks"])
