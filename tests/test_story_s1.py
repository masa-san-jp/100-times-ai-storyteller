from __future__ import annotations

import json
from pathlib import Path

from storyteller.new_run import create_free_run, create_story_orchestrator
from storyteller.validation import load_and_validate_yaml


ROOT = Path(__file__).parents[1]


def _materials_output(paragraph_id: str) -> dict[str, object]:
    return {
        "materials": [
            {"text": "変化を望みながら守り続ける日常", "kind": "conflict"},
            {"text": "遠い場所へ向かうことへの静かな憧れ", "kind": "desire"},
            {"text": "言葉にならない不安が残る夕暮れ", "kind": "mood"},
            {"text": "記憶の隙間に浮かぶ光景の断片", "kind": "image"},
            {"text": "誰かと分かち合いたい小さな価値", "kind": "value"},
        ],
    }


def test_s1_task_definition_and_output_schema_are_valid() -> None:
    definition = load_and_validate_yaml(
        ROOT / "harness" / "story" / "tasks" / "S1.extract.yaml",
        ROOT / "schemas" / "task-definition.schema.json",
    )
    assert definition["id"] == "S1.extract"
    assert definition["share_across_runs"] is True
    assert definition["validate"]["schema"] == "schemas/tasks/S1.extract.schema.json"


def test_dummy_executor_submission_assigns_material_ids(tmp_path: Path) -> None:
    source = tmp_path / "free.md"
    source.write_text(
        "変化を望みながら、見慣れた日常を守っている。\n\n"
        "遠い場所への憧れを、まだ誰にも話せずにいる。",
        encoding="utf-8",
    )
    data_dir = tmp_path / "data"
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

    for paragraph_id in ("p001", "p002"):
        claim = orchestrator.claim_next(run_id, executor_id="dummy")
        assert claim is not None
        result = orchestrator.submit(
            claim["ticket"],
            json.dumps(_materials_output(paragraph_id), ensure_ascii=False),
        )
        assert result.accepted

    outputs = []
    for task_id in ("S1.extract-p001", "S1.extract-p002"):
        output_path = data_dir / "runs" / run_id / "tasks" / task_id / "output.json"
        outputs.extend(json.loads(output_path.read_text(encoding="utf-8"))["materials"])

    assert [material["id"] for material in outputs] == [
        "m001",
        "m002",
        "m003",
        "m004",
        "m005",
        "m006",
        "m007",
        "m008",
        "m009",
        "m010",
    ]
