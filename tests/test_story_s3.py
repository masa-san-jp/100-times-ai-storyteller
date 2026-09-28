from __future__ import annotations

import json
from pathlib import Path

from storyteller.orchestrator import CodeTaskResult, Orchestrator, TaskSpec
from storyteller.story_s3 import story_s3_assign
from storyteller.validation import validate_document


ROOT = Path(__file__).parents[1]
ALL_S5_TYPES = (
    "S5.name",
    "S5.profile",
    "S5.intro",
    "S5.appearance",
    "S5.motive",
    "S5.catchphrase",
)


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


def _definitions(material_kind: str = "suppression") -> dict[str, dict[str, object]]:
    definitions: dict[str, dict[str, object]] = {
        "S1.extract": _code_definition("S1.extract", "extract"),
        "S2.merge": _code_definition("S2.merge", "merge"),
        "S3.assign": {
            "id": "S3.assign",
            "version": 1,
            "kind": "code",
            "handler": "assign",
            "inputs": {
                "pools": {
                    "label": "入力由来プール",
                    "from": "S2.merge",
                    "select": "pools",
                    "required": True,
                }
            },
        },
        "S4.section": _llm_definition("S4.section"),
        "S4.item": _llm_definition("S4.item"),
        "S6.expand": _code_definition("S6.expand", "s6"),
    }
    definitions.update({task_id: _llm_definition(task_id) for task_id in ALL_S5_TYPES})
    return definitions


def _pools() -> dict[str, list[dict[str, str]]]:
    return {
        "want": [],
        "ability": [],
        "duty": [],
        "taboo": [],
        "place": [],
        "era": [],
        "object": [],
        "age": [],
        "gender": [],
        "species": [],
    }


def _make_orchestrator(data_dir: Path, *, material_kind: str = "suppression") -> Orchestrator:
    definitions = _definitions(material_kind)

    def extract(_context):
        return {
            "materials": [
                {"id": "m001", "text": "言葉にされない自己像の抑圧", "kind": material_kind},
            ]
        }

    def merge(context):
        return CodeTaskResult(
            output={"pools": _pools()},
            add_tasks=[TaskSpec("S3.assign", "S3.assign", deps=(context.task_id,))],
        )

    return Orchestrator(
        data_dir,
        definitions,
        {
            "extract": extract,
            "merge": merge,
            "assign": story_s3_assign,
            "s6": lambda _context: {"ok": True},
        },
        harness_root=ROOT,
        repository_root=ROOT,
    )


def _run(
    data_dir: Path,
    seed: int,
    *,
    run_number: int,
    material_kind: str = "suppression",
    plot_type: str | None = None,
) -> tuple[Orchestrator, str]:
    orchestrator = _make_orchestrator(data_dir, material_kind=material_kind)
    scale = {
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
    }
    run_id = orchestrator.create_run(
        seed=seed,
        run_id=f"20260927-0315{run_number:02d}-{seed:06x}",
        input_data={"kind": "free", "source_sha256": "a" * 64, "paragraphs": []},
        input_type="free",
        plot_type=plot_type,
        scale=scale,
        table_snapshot={axis: 0 for axis in _pools()},
        task_specs=[
            TaskSpec("S1.extract-p001", "S1.extract", index=("p001",)),
            TaskSpec("S2.merge", "S2.merge", deps=("S1.extract-p001",)),
        ],
    )
    manifest = orchestrator.advance(run_id)
    assert manifest["tasks"]["S3.assign"]["state"] == "done"
    return orchestrator, run_id


def _assignment(orchestrator: Orchestrator, run_id: str) -> dict[str, object]:
    path = orchestrator.run_dir(run_id) / "tasks" / "S3.assign" / "output.json"
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def test_s3_assignment_is_schema_valid_and_adds_the_phase1_dag(tmp_path: Path) -> None:
    orchestrator, run_id = _run(tmp_path / "data", 123, run_number=1)
    assignment = _assignment(orchestrator, run_id)
    validate_document(assignment, ROOT / "schemas/story/assignment.schema.json")
    manifest = orchestrator.load_run(run_id)

    assert manifest["table_snapshot"] == {axis: 0 for axis in _pools()}
    assert assignment["cast"][0]["role"] == "protagonist"
    assert assignment["cast"][0]["suppressed_self_image"]["kind"] == "suppression"
    assert {"messenger", "supporter", "adversary"}.issubset(
        set(assignment["absent_roles"])
    )
    assert manifest["tasks"]["S4.section-place"]["deps"] == ["S3.assign"]
    assert manifest["tasks"]["S6.expand"]["deps"][0] == "S3.assign"
    assert len(manifest["tasks"]["S6.expand"]["deps"]) > 1


def test_s3_same_seed_repeats_and_ten_seeds_do_not_all_match(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    first_orchestrator, first_id = _run(data_dir, 456, run_number=1)
    second_orchestrator, second_id = _run(data_dir, 456, run_number=2)
    assert _assignment(first_orchestrator, first_id) == _assignment(second_orchestrator, second_id)

    assignments = []
    for number, seed in enumerate(range(100, 110), start=3):
        orchestrator, run_id = _run(data_dir, seed, run_number=number)
        assignments.append(json.dumps(_assignment(orchestrator, run_id), ensure_ascii=False, sort_keys=True))
    assert len(set(assignments)) > 1


def test_s3_plot_type_override_is_read_from_manifest_input(tmp_path: Path) -> None:
    orchestrator, run_id = _run(
        tmp_path / "data",
        789,
        run_number=1,
        plot_type="quest",
    )
    assert _assignment(orchestrator, run_id)["threads"][0]["plot_type"] == "quest"
