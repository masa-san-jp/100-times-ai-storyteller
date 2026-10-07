from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from storyteller.new_run import create_free_run, create_story_orchestrator
from storyteller.task_outputs import task_sources
from storyteller.validation import load_and_validate_yaml


ROOT = Path(__file__).parents[1]


def _complete_s1(tmp_path: Path):
    source = tmp_path / "free.md"
    source.write_text("変化を望む静かな生活。", encoding="utf-8")
    data_dir = tmp_path / "data"
    run_id = create_free_run(data_dir, source, preset="vignette", axis_overrides={},
                             seed=123, parts=None, plot_type=None, repository_root=ROOT)
    harness = create_story_orchestrator(data_dir)
    for number in range(1, 6):
        claim = harness.claim_task(run_id, f"S1.extract-p001-{number}", executor_id="dummy")
        assert harness.submit(claim["ticket"], json.dumps({
            "text": f"変化を望む日常から浮かぶ心象{number}", "kind": "theme",
        }, ensure_ascii=False)).accepted
    harness.advance(run_id)
    return run_id, harness


def _element(axis: str, number: int) -> str:
    # NFKC and whitespace variants across groups must collapse to five rows.
    return f"{axis}の条件をひとつ変える観点{number}"


def _complete_s2(harness, run_id):
    for task_id, task in harness.load_run(run_id)["tasks"].items():
        if task["type"] not in {"S2.expand", "S2.counter"}:
            continue
        task = harness.load_run(run_id)["tasks"][task_id]
        if task["state"] in {"done", "skipped"}:
            continue
        claim = harness.claim_task(run_id, task_id, executor_id="dummy")
        inputs = json.loads((harness.task_dir(run_id, task_id) / "input.json").read_text(encoding="utf-8"))
        axis = inputs["axis"]["key"]
        output = (_element(axis, int(task["index"][1])) if task["type"] == "S2.expand"
                  else f"{axis}を反対の条件から再構築する要素")
        if inputs["material"]["id"] == "m002":
            output = "　" + output.replace("1", "１") + "　"
        assert harness.submit(claim["ticket"], output).accepted
    return harness.advance(run_id)


def _pools(harness, run_id):
    return json.loads((harness.task_dir(run_id, "S2.merge") / "output.json").read_text(encoding="utf-8"))["pools"]


def test_s2_task_definitions_are_valid() -> None:
    for name in ("S2.plan", "S2.expand", "S2.counter", "S2.merge"):
        definition = load_and_validate_yaml(ROOT / f"harness/story/tasks/{name}.yaml",
                                            ROOT / "schemas/task-definition.schema.json")
        assert definition["id"] == name
        if definition["kind"] == "llm":
            assert definition["element"] == "text"
            assert definition["output"] == "text"
            assert "schema" not in definition["validate"]


def test_s2_plan_sequential_expansion_counter_and_merge(tmp_path: Path) -> None:
    run_id, harness = _complete_s1(tmp_path)
    manifest = harness.load_run(run_id)
    need = manifest["scale"]["derived"]["pool_need"]
    groups = sum(math.ceil(value / 5) + 1 for value in need.values())
    assert len([task for task in manifest["tasks"].values() if task["type"] == "S2.expand"]) == groups * 5
    assert len([task for task in manifest["tasks"].values() if task["type"] == "S2.counter"]) == groups
    assert manifest["tasks"]["S2.merge"]["state"] == "blocked"
    for axis in need:
        for material in ("m001", "m002"):
            for number in range(1, 6):
                task = manifest["tasks"][f"S2.expand-{material}-{axis}-{number}"]
                assert task["deps"] == ["S2.plan", *[f"S2.expand-{material}-{axis}-{earlier}" for earlier in range(1, number)]]
                assert task["state"] == ("ready" if number == 1 else "blocked")
            counter = manifest["tasks"][f"S2.counter-{material}-{axis}"]
            assert counter["deps"] == ["S2.plan", *[f"S2.expand-{material}-{axis}-{number}" for number in range(1, 6)]]
            assert counter["state"] == "blocked"
    first = "S2.expand-m001-want-1"
    card = harness.claim_task(run_id, first, executor_id="dummy")
    assert "運命" in card["card"]
    assert "具体的な相手・場所・物・期限" in card["card"]
    assert "発動条件と制約" not in card["card"]
    assert "m001" in card["card"] and "m002" not in card["card"]
    assert harness.submit(card["ticket"], _element("want", 1)).accepted
    assert task_sources(harness.task_dir(run_id, first)) == ["m001"]
    manifest = _complete_s2(harness, run_id)
    assert manifest["tasks"]["S2.merge"]["state"] == "done"
    assert manifest["tasks"]["S3.assign"]["state"] == "done"
    pools = _pools(harness, run_id)
    for axis in need:
        assert pools[axis] == [{"id": f"{axis}:i{number:02d}", "text": _element(axis, number), "source": "m001"} for number in range(1, 6)]
        counter_dir = harness.task_dir(run_id, f"S2.counter-m001-{axis}")
        counter_input = json.loads((counter_dir / "input.json").read_text(encoding="utf-8"))
        assert counter_input["previous_elements"] == [_element(axis, number) for number in range(1, 6)]
        for number in range(1, 6):
            inputs = json.loads((harness.task_dir(run_id, f"S2.expand-m001-{axis}-{number}") / "input.json").read_text(encoding="utf-8"))
            assert inputs["previous_elements"] == [_element(axis, earlier) for earlier in range(1, number)]
    assert not (tmp_path / "data/tables").exists()


@pytest.mark.parametrize("failed_id", ["S2.expand-m001-want-2", "S2.counter-m001-want"])
def test_failed_element_keeps_siblings_and_s3_uses_fallback(tmp_path: Path, failed_id: str) -> None:
    run_id, harness = _complete_s1(tmp_path)
    last_number = 1 if "expand" in failed_id else 5
    for number in range(1, last_number + 1):
        claim = harness.claim_task(run_id, f"S2.expand-m001-want-{number}", executor_id="dummy")
        assert harness.submit(claim["ticket"], _element("want", number)).accepted
    first_dir = harness.task_dir(run_id, "S2.expand-m001-want-1")
    saved = (first_dir / "output.md").read_text(encoding="utf-8")
    for _ in range(5):
        claim = harness.claim_task(run_id, failed_id, executor_id="dummy")
        assert not harness.submit(claim["ticket"], "短い").accepted
    manifest = harness.load_run(run_id)
    assert manifest["tasks"][failed_id]["state"] == "skipped"
    assert manifest["tasks"]["S2.expand-m002-want-1"]["state"] == "ready"
    if "expand" in failed_id:
        assert manifest["tasks"]["S2.expand-m001-want-3"]["state"] == "ready"
    manifest = _complete_s2(harness, run_id)
    assert manifest["tasks"]["S3.assign"]["state"] == "done"
    assert manifest["status"] == "active"
    assert (first_dir / "output.md").read_text(encoding="utf-8") == saved
    assert len(_pools(harness, run_id)["want"]) == 5
    if "expand" in failed_id:
        inputs = json.loads((harness.task_dir(run_id, "S2.counter-m001-want") / "input.json").read_text(encoding="utf-8"))
        assert inputs["previous_elements"] == [_element("want", number) for number in (1, 3, 4, 5)]
        assert _pools(harness, run_id)["want"][-1]["source"] == "m002"
