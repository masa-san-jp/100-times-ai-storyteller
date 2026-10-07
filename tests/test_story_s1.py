from __future__ import annotations

import json
from pathlib import Path

from storyteller.new_run import create_free_run, create_story_orchestrator
from storyteller.task_outputs import task_sources
from storyteller.validation import load_and_validate_yaml


ROOT = Path(__file__).parents[1]
MATERIALS = [
    {"text": "変化を望みながら守り続ける日常", "kind": "conflict"},
    {"text": "遠い場所へ向かうことへの静かな憧れ", "kind": "desire"},
    {"text": "言葉にならない不安が残る夕暮れ", "kind": "mood"},
    {"text": "記憶の隙間に浮かぶ光景の断片", "kind": "image"},
    {"text": "誰かと分かち合いたい小さな価値", "kind": "value"},
]


def _new_run(data_dir: Path, source: Path, seed: int = 123) -> str:
    return create_free_run(data_dir, source, preset="vignette", axis_overrides={},
                           seed=seed, parts=None, plot_type=None, repository_root=ROOT)


def _submit(harness, run_id, paragraph, number):
    task_id = f"S1.extract-{paragraph}-{number}"
    claim = harness.claim_task(run_id, task_id, executor_id="dummy")
    result = harness.submit(claim["ticket"], json.dumps(MATERIALS[number - 1], ensure_ascii=False))
    assert result.accepted
    return harness.task_dir(run_id, task_id)


def test_s1_task_definition_and_output_schema_are_valid() -> None:
    definition = load_and_validate_yaml(ROOT / "harness/story/tasks/S1.extract.yaml",
                                        ROOT / "schemas/task-definition.schema.json")
    assert definition["element"] == "labeled"
    assert definition["share_across_runs"] is True
    assert definition["on_exhausted"] == "skip"


def test_dummy_executor_submission_assigns_stable_material_ids_and_card_history(tmp_path: Path) -> None:
    source = tmp_path / "free.md"
    source.write_text("見慣れた日常を守る。\n\n遠い場所に憧れる。", encoding="utf-8")
    data_dir = tmp_path / "data"
    run_id = _new_run(data_dir, source)
    harness = create_story_orchestrator(data_dir)
    for paragraph in ("p001", "p002"):
        for number in range(1, 6):
            task_dir = _submit(harness, run_id, paragraph, number)
            output = json.loads((task_dir / "output.json").read_text(encoding="utf-8"))
            expected_id = f"m{(int(paragraph[1:]) - 1) * 5 + number:03d}"
            assert output == {**MATERIALS[number - 1], "id": expected_id}
            inputs = json.loads((task_dir / "input.json").read_text(encoding="utf-8"))
            assert [item["text"] for item in inputs["previous_materials"]] == [item["text"] for item in MATERIALS[:number - 1]]
            assert inputs["paragraph"]["id"] == paragraph
            assert paragraph in task_sources(task_dir)
    assert harness.advance(run_id)["tasks"]["S2.plan"]["state"] == "done"


def test_one_failed_material_preserves_others_and_continues_the_group(tmp_path: Path) -> None:
    source = tmp_path / "free.md"
    source.write_text("見慣れた日常を守る。\n\n遠い場所に憧れる。", encoding="utf-8")
    harness = create_story_orchestrator(tmp_path / "data")
    run_id = _new_run(tmp_path / "data", source)
    first = _submit(harness, run_id, "p001", 1)
    saved = (first / "output.json").read_text(encoding="utf-8")
    for _ in range(5):
        claim = harness.claim_task(run_id, "S1.extract-p001-2", executor_id="dummy")
        assert not harness.submit(claim["ticket"], '{"text":"短い", "kind":"theme"}').accepted
    manifest = harness.load_run(run_id)
    assert manifest["tasks"]["S1.extract-p001-2"]["state"] == "skipped"
    assert manifest["tasks"]["S1.extract-p001-3"]["state"] == "ready"
    assert manifest["tasks"]["S1.extract-p002-1"]["state"] == "ready"
    for number in range(3, 6):
        task_dir = _submit(harness, run_id, "p001", number)
        history = json.loads((task_dir / "input.json").read_text(encoding="utf-8"))["previous_materials"]
        assert "m002" not in [item["id"] for item in history]
    for number in range(1, 6):
        _submit(harness, run_id, "p002", number)
    assert (first / "output.json").read_text(encoding="utf-8") == saved
    manifest = harness.advance(run_id)
    assert manifest["tasks"]["S2.plan"]["state"] == "done"
    plans = json.loads((harness.task_dir(run_id, "S2.plan") / "output.json").read_text(encoding="utf-8"))["plans"]
    assert all(plan["material"]["id"] != "m002" for plan in plans.values())


def test_s1_cached_elements_keep_ids_and_history(tmp_path: Path) -> None:
    source = tmp_path / "free.md"
    source.write_text("静かな日常の中で変化を望む。", encoding="utf-8")
    data_dir = tmp_path / "data"
    harness = create_story_orchestrator(data_dir)
    run_id = _new_run(data_dir, source)
    for number in range(1, 6):
        _submit(harness, run_id, "p001", number)
    cached_id = _new_run(data_dir, source, seed=456)
    for _ in range(5):
        manifest = harness.advance(cached_id)
    for number in range(1, 6):
        task_id = f"S1.extract-p001-{number}"
        assert manifest["tasks"][task_id]["state"] == "done"
        output = json.loads((harness.task_dir(cached_id, task_id) / "output.json").read_text(encoding="utf-8"))
        assert output["id"] == f"m{number:03d}"
