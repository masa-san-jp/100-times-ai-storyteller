from __future__ import annotations

import json
from pathlib import Path

from storyteller.new_run import create_free_run, create_story_orchestrator
from storyteller.validation import load_and_validate_yaml


ROOT = Path(__file__).parents[1]


def _materials_output() -> dict[str, object]:
    return {
        "materials": [
            {"text": "変化を望みながら守り続ける日常", "kind": "conflict"},
            {"text": "遠い場所へ向かうことへの静かな憧れ", "kind": "desire"},
            {"text": "言葉にならない不安が残る夕暮れ", "kind": "mood"},
            {"text": "記憶の隙間に浮かぶ光景の断片", "kind": "image"},
            {"text": "誰かと分かち合いたい小さな価値", "kind": "value"},
        ],
        "sources": ["p001"],
    }


def _expand_output(axis: str, source: str) -> dict[str, object]:
    return {
        "items": [
            f"{axis}の具体的な観点をひとつ選ぶ",
            f"{axis}に異なる条件を組み込む",
            f"{axis}の表層と深層をずらす",
            f"{axis}が関係を変える仕組み",
            f"{axis}を別の角度から見直す",
        ],
        "counterpart": f"{axis}を反対の条件から再構築する要素",
        "sources": [source],
    }


def _complete_s1(data_dir: Path, source: Path) -> tuple[str, object]:
    run_id = create_free_run(
        data_dir,
        source,
        preset="vignette",
        axis_overrides={},
        seed=123,
        parts=None,
        plot_type=None,
        repository_root=ROOT,
    )
    orchestrator = create_story_orchestrator(data_dir)
    claim = orchestrator.claim_next(run_id, executor_id="dummy")
    assert claim is not None
    assert orchestrator.submit(
        claim["ticket"], json.dumps(_materials_output(), ensure_ascii=False)
    ).accepted
    return run_id, orchestrator


def test_s2_task_definitions_and_schema_are_valid() -> None:
    task_schema = ROOT / "schemas" / "task-definition.schema.json"
    for name in ("S2.plan", "S2.expand", "S2.merge"):
        definition = load_and_validate_yaml(
            ROOT / "harness" / "story" / "tasks" / f"{name}.yaml",
            task_schema,
        )
        assert definition["id"] == name
    assert (
        ROOT / "schemas" / "tasks" / "S2.expand.schema.json"
    ).is_file()


def test_s2_plan_expand_merge_deduplicate_and_skip_after_need(
    tmp_path: Path,
) -> None:
    source = tmp_path / "free.md"
    source.write_text("変化を望む静かな生活。", encoding="utf-8")
    run_id, orchestrator = _complete_s1(tmp_path / "data", source)

    manifest = orchestrator.advance(run_id)
    expand_ids = [
        task_id for task_id in manifest["tasks"] if task_id.startswith("S2.expand-")
    ]
    assert len(expand_ids) == 20
    assert manifest["tasks"]["S2.plan"]["state"] == "done"
    assert manifest["tasks"]["S2.merge"]["state"] == "blocked"

    plan_path = (
        tmp_path / "data" / "runs" / run_id / "tasks" / "S2.plan" / "output.json"
    )
    plans = json.loads(plan_path.read_text(encoding="utf-8"))["plans"]
    first_want = "S2.expand-m001-want"
    card = orchestrator.claim_task(run_id, first_want, executor_id="dummy")
    assert "運命" in card["card"]
    assert "m001" in card["card"]
    assert "m002" not in card["card"]
    assert orchestrator.submit(
        card["ticket"],
        json.dumps(_expand_output("want", "m001"), ensure_ascii=False),
    ).accepted

    manifest = orchestrator.load_run(run_id)
    assert manifest["tasks"]["S2.expand-m002-want"]["state"] == "skipped"

    for task_id in expand_ids:
        task = orchestrator.load_run(run_id)["tasks"][task_id]
        if task["state"] != "ready":
            continue
        plan = plans[task["index"][0]]
        claim = orchestrator.claim_task(run_id, task_id, executor_id="dummy")
        axis = plan["axis"]["key"]
        source_id = plan["material"]["id"]
        assert orchestrator.submit(
            claim["ticket"],
            json.dumps(_expand_output(axis, source_id), ensure_ascii=False),
        ).accepted

    manifest = orchestrator.advance(run_id)
    assert manifest["tasks"]["S2.merge"]["state"] == "done"
    merge_path = (
        tmp_path / "data" / "runs" / run_id / "tasks" / "S2.merge" / "output.json"
    )
    pools = json.loads(merge_path.read_text(encoding="utf-8"))["pools"]
    assert set(pools) == {
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
    }
    assert [item["id"] for item in pools["want"]] == [
        "want:i01",
        "want:i02",
        "want:i03",
        "want:i04",
        "want:i05",
    ]
    assert all(item["source"].startswith("m") for item in pools["want"])
