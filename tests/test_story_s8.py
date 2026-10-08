from __future__ import annotations

import random
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from storyteller.orchestrator import (
    CodeTaskResult,
    Orchestrator,
    TaskSpec,
    _source_outputs,
)
from storyteller.story_s7 import story_s7_assemble
from storyteller.story_s8 import story_s8_judge, story_s8_plan
from storyteller.story_s3 import story_s3_assign
from storyteller.story_s5 import story_s5_relationship_context
from storyteller.story_s6 import story_s6_expand
from storyteller.cards import generate_task_card
from storyteller.selectors import resolve_inputs
from storyteller.validation import load_and_validate_yaml


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
            f"S7.assemble-{slot_id}": {"who": who, "result": slot_id}
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


def test_s8_compare_inputs_are_only_the_two_event_descriptions() -> None:
    definition = load_and_validate_yaml(
        ROOT / "harness/story/tasks/S8.compare.yaml",
        ROOT / "schemas/task-definition.schema.json",
    )
    plan_output = {
        "slot": "e003",
        "comparison_targets": ["e001"],
        "current": {
            "id": "e003",
            "event": {"result": "Bの結果", "who": ["c1"]},
        },
        "targets": [
            {
                "id": "e001",
                "event": {"result": "Aの結果", "who": ["c1"]},
            }
        ],
    }
    inputs = resolve_inputs(
        definition,
        {
            "S8.plan": {
                "e003": {
                    "targets": {"e003": plan_output["targets"][0]},
                    "current": plan_output["current"],
                }
            }
        },
        index=("e003", "k1"),
    )

    card = generate_task_card(definition, "ticket", inputs=inputs)

    assert set(inputs) == {"event_a", "event_b"}
    assert inputs["event_a"]["id"] == "e001"
    assert inputs["event_b"]["id"] == "e003"
    assert "Aの結果" in card
    assert "Bの結果" in card
    assert "事実として両立しないことだけを指す" in card
    assert "人物の心情・関係・状況の変化や展開は矛盾に含めない" in card
    assert "比較計画" not in card
    input_section = card.split("## 手順", 1)[0]
    assert "e002" not in input_section


def test_s8_compare_input_assembly_selects_the_numbered_target() -> None:
    definition = load_and_validate_yaml(
        ROOT / "harness/story/tasks/S8.compare.yaml",
        ROOT / "schemas/task-definition.schema.json",
    )
    plan_output = {
        "current": {"result": "現在"},
        "targets": [
            {"id": "e001", "event": {"result": "一つ目"}},
            {"id": "e002", "event": {"result": "二つ目"}},
        ],
    }
    task = {
        "type": "S8.compare",
        "index": ["e003", "k2"],
        "deps": ["S8.plan-e003"],
    }
    source_outputs = _source_outputs(
        {"tasks": {"S8.plan-e003": {"type": "S8.plan", "state": "done"}}},
        task,
        definition,
        {"S8.plan-e003": plan_output},
    )

    inputs = resolve_inputs(definition, source_outputs, index=task["index"])

    assert inputs["event_a"]["id"] == "e002"
    assert inputs["event_a"]["event"]["result"] == "二つ目"
    assert inputs["event_b"]["id"] == "e003"


def test_indexed_source_is_an_index_to_output_object_of_done_dependencies() -> None:
    definition = load_and_validate_yaml(
        ROOT / "harness/story/tasks/S5.profile.yaml",
        ROOT / "schemas/task-definition.schema.json",
    )
    manifest = {
        "tasks": {
            "S5.name-c1": {
                "type": "S5.name",
                "state": "done",
                "index": ["c1"],
            },
            "S5.name-c2": {
                "type": "S5.name",
                "state": "done",
                "index": ["c2"],
            },
            "S5.name-c3": {
                "type": "S5.name",
                "state": "ready",
                "index": ["c3"],
            },
        }
    }
    task = {
        "type": "S5.profile",
        "deps": ["S5.name-c1", "S5.name-c2", "S5.name-c3"],
        "index": ["c2"],
    }
    source_outputs = _source_outputs(
        manifest,
        task,
        definition,
        {
            "S5.name-c1": {"name": "カナ"},
            "S5.name-c2": {"name": "ミナ"},
        },
    )

    assert source_outputs["S5.name"] == {
        "c1": {"name": "カナ"},
        "c2": {"name": "ミナ"},
    }
    assert resolve_inputs(
        {"inputs": {"name": {"from": "S5.name", "select": "[{slot}].name"}}},
        source_outputs,
        index=("c2",),
    ) == {"name": "ミナ"}


def test_s8_judge_invalidates_s7_when_a_comparison_is_yes() -> None:
    context = SimpleNamespace(
        task_id="S8.judge-e003",
        task={"index": ["e003"]},
        inputs={
            "comparisons": [
                {
                    "answer": "no",
                    "reason": "整合している。",
                },
                {
                    "answer": "yes",
                    "reason": "結果が食い違う。",
                },
            ]
        },
    )

    result = story_s8_judge(context)

    assert result.output == {"slot": "e003", "invalidated": True}
    assert result.invalidations == [
        ("S7.event-e003-what", "比較結果に矛盾あり：結果が食い違う。")
    ]


def test_s8_judge_flattens_indexed_comparison_outputs() -> None:
    context = SimpleNamespace(
        task_id="S8.judge-e003",
        task={"index": ["e003"]},
        inputs={
            "comparisons": {
                "e003-k1": {
                    "answer": "no",
                    "reason": "整合している。",
                },
                "e003-k2": {
                    "answer": "yes",
                    "reason": "結果が食い違う。",
                },
            }
        },
    )

    result = story_s8_judge(context)

    assert result.output["invalidated"] is True
    assert result.invalidations == [
        ("S7.event-e003-what", "比較結果に矛盾あり：結果が食い違う。")
    ]


def test_s8_judge_adopts_last_s7_output_at_invalidation_limit(
    tmp_path: Path, monkeypatch
) -> None:
    manifest = {
        "tasks": {"S7.event-e003-what": {"invalidations": 2}},
        "warnings": [],
    }
    monkeypatch.setattr(
        "storyteller.story_s8.load_manifest",
        lambda _path: manifest,
    )
    context = SimpleNamespace(
        task_id="S8.judge-e003",
        task={"index": ["e003"]},
        run_dir=tmp_path,
        inputs={
            "comparisons": [
                {
                    "answer": "yes",
                    "reason": "結果が食い違う。",
                }
            ]
        },
    )

    result = story_s8_judge(context)

    assert result.output == {"slot": "e003", "invalidated": False}
    assert result.invalidations == []
    assert result.manifest_updates == {
        "warnings": [
            "S7.event-e003-what: 矛盾あり判定が無効化上限（2回）に達したため、最後の出力を採用"
        ]
    }


def test_s8_judge_does_not_invalidate_without_yes() -> None:
    context = SimpleNamespace(
        task_id="S8.judge-e003",
        task={"index": ["e003"]},
        inputs={
            "comparisons": [
                {
                    "answer": "no",
                    "reason": "整合している。",
                }
            ]
        },
    )

    result = story_s8_judge(context)

    assert result.output == {"slot": "e003", "invalidated": False}
    assert result.invalidations == []


def _code_definition(task_id: str, handler: str) -> dict[str, object]:
    return {"id": task_id, "version": 1, "kind": "code", "handler": handler}


def _llm_definition(task_id: str) -> dict[str, object]:
    return {
        "id": task_id,
        "version": 1,
        "kind": "llm", "element": "text",
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
        "S4.fact": _llm_definition("S4.fact"),
        "S4.calendar_name": _llm_definition("S4.calendar_name"),
        "S4.calendar_epoch": _llm_definition("S4.calendar_epoch"),
        "S4.diversity": _code_definition("S4.diversity", "diversity"),
        "S6.expand": _code_definition("S6.expand", "expand"),
        "S7.assemble": {**_code_definition("S7.assemble", "event_assemble"), "inputs": {"slot": {"from": "S6.expand", "select": "slots[{slot}]", "label": "スロット"}}},
        "S7.event": {**_llm_definition("S7.event"), "output": "text", "continuation": False},
        "S7.detail": _llm_definition("S7.detail"),
        "S8.plan": _code_definition("S8.plan", "plan"),
        "S8.compare": _llm_definition("S8.compare"),
        "S8.judge": _code_definition("S8.judge", "judge"),
        "S9.assemble": _code_definition("S9.assemble", "assemble"),
        "S5.relationship_context": _code_definition(
            "S5.relationship_context", "relationship_context"
        ),
    }
    for task_type in (
        "S5.name",
        "S5.facts",
        "S5.profile",
        "S5.intro",
        "S5.appearance",
        "S5.motive",
        "S5.personality",
        "S5.values",
        "S5.voice",
        "S5.inner_conflict",
        "S5.backstory",
        "S5.relationship",
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
            "diversity": lambda _context: {"invalidated": []},
            "relationship_context": story_s5_relationship_context,
            "expand": story_s6_expand,
            "event_assemble": story_s7_assemble,
            "plan": story_s8_plan,
            "judge": lambda _context: {"invalid": False},
            "assemble": lambda _context: {"ok": True},
        },
        clock=lambda: datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc),
        harness_root=ROOT,
        repository_root=ROOT,
    )
    # The S4/S5 task outputs are injected through the internal test seam.
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
        if task_id.startswith("S4.fact-"):
            output = {"facts": [], "glossary": []}
        elif task_id.startswith("S4."):
            output = {"body": "水路の音が境界を知らせる場所。"}
        elif task_id.startswith("S5.name-"):
            output = {"name": "カナ"}
        elif task_id.startswith("S5.motive-"):
            output = {"motive": "道を確かめたい。"}
        else:
            output = {"value": "ok"}
        orchestrator._complete_task(run_id, task_id, output)

    # Complete the synthetic S7/compare outputs as they become ready.  Each
    # advance call executes the real S6 or S8.plan code tasks in between.
    for _ in range(100):
        manifest = orchestrator.advance(run_id)
        pending = [
            (task_id, task)
            for task_id, task in manifest["tasks"].items()
            if task["state"] == "ready" and task["kind"] == "llm"
        ]
        if not pending:
            break
        for task_id, task in pending:
            if task_id.startswith("S4."):
                output = {"body": "水路の音が境界を知らせる場所。"}
            elif task_id.startswith("S5.name-"):
                output = {"name": "カナ"}
            elif task_id.startswith("S5.motive-"):
                output = {"motive": "道を確かめたい。"}
            elif task_id.startswith("S7.event-"):
                output = "出来事の項目。"
            elif task_id.startswith("S7.detail-"):
                output = "場面の説明。"
            else:
                output = {"answer": "no", "reason": "整合している"}
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
