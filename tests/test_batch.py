from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from storyteller.batch import (
    create_batch,
    is_duplicate_story,
    load_batch_manifest,
    max_created,
    settle_batch_run,
    story_fingerprint,
    trigram_jaccard,
)
from storyteller.batch_status import batch_status
from storyteller.cli import main as cli_main
from storyteller.manifest import load_manifest, write_manifest
from storyteller.orchestrator import CodeTaskResult, Orchestrator, TaskSpec
from storyteller.seed import derive_run_seed
from storyteller.story_s9 import story_s9_assemble
from tests.test_story_s9 import _base_outputs, _event

ROOT = Path(__file__).parents[1]


def _free_input(tmp_path: Path) -> Path:
    source = tmp_path / "free.md"
    source.write_text("一段目です。\n\n二段目です。", encoding="utf-8")
    return source


def _batch(tmp_path: Path, count: int, seed: int = 100) -> tuple[Path, str]:
    data_dir = tmp_path / "data"
    batch_id = create_batch(
        data_dir, _free_input(tmp_path), input_kind="free", count=count,
        preset="vignette", axis_overrides={}, seed=seed, parts=None,
        plot_type=None, repository_root=ROOT,
    )
    return data_dir, batch_id


def _story(what: str = "歩いた", name: str = "カナ") -> dict[str, object]:
    return {
        "cast": [{"name": name, "profile": "旅人。"}],
        "events": [{"id": "e001", "what": what, "result": "道が開いた", "when": "朝"}],
    }


def _write_story(data_dir: Path, run_id: str, story: dict[str, object]) -> None:
    path = data_dir / "runs" / run_id / "story" / "story.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(story, ensure_ascii=False), encoding="utf-8")


def _settle(data_dir: Path, batch_id: str, run_id: str, story: dict[str, object]) -> bool:
    _write_story(data_dir, run_id, story)
    return settle_batch_run(data_dir, batch_id, run_id, story, {"kind": "free", "paragraphs": [{"id": "p001", "text": "素材"}]}, repository_root=ROOT)


def test_max_created_is_larger_of_3n_and_n_plus_10() -> None:
    assert [max_created(n) for n in (1, 3, 5, 10)] == [11, 13, 15, 30]


def test_fingerprint_ignores_width_space_and_case_but_not_content() -> None:
    base = _story("Walked")
    variant = _story("ｗａｌｋ ｅｄ")
    assert story_fingerprint(base) == story_fingerprint(variant)
    assert story_fingerprint(base) != story_fingerprint(_story("Walked", name="リオ"))
    assert story_fingerprint(base) != story_fingerprint(_story("Ran"))


def test_trigram_jaccard_and_near_duplicate_threshold() -> None:
    assert trigram_jaccard("あいうえお", "あいうえお") == 1.0
    assert trigram_jaccard("あいうえお", "かきくけこ") == 0.0
    left = _story("あいうえおかきくけこ", name="A")
    right = _story("あいうえおかきくけさ", name="B")  # 異なる指紋、近似
    far = _story("さしすせそたちつてと", name="C")
    assert is_duplicate_story(right, [left], 0.5)
    assert not is_duplicate_story(far, [left], 0.5)
    assert not is_duplicate_story(right, [left], 0.95)


def test_create_batch_makes_n_runs_with_derived_seeds(tmp_path: Path) -> None:
    data_dir, batch_id = _batch(tmp_path, 3)
    batch = load_batch_manifest(data_dir, batch_id)
    assert (batch["requested"], batch["max_created"], batch["seed"]) == (3, 13, 100)
    assert [r["index"] for r in batch["runs"]] == [0, 1, 2]
    for entry in batch["runs"]:
        manifest = load_manifest(data_dir / "runs" / entry["run_id"] / "manifest.json")
        assert manifest["batch_id"] == batch_id
        assert manifest["seed_source"] == "batch"
        assert manifest["seed"] == derive_run_seed(100, entry["index"])
    assert batch_status(data_dir, batch_id)["requested"] == 3
    assert not (data_dir / "batches" / batch_id / "batch.lock").exists()


def test_cli_count_prints_batch_id_and_rejects_zero(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("STORYTELLER_HOME", str(tmp_path / "data"))
    source = _free_input(tmp_path)
    assert cli_main(["new", "--free", str(source), "--scale", "vignette", "--count", "2", "--seed", "5"]) == 0
    batch_id = capsys.readouterr().out.strip()
    assert len(load_batch_manifest(tmp_path / "data", batch_id)["runs"]) == 2
    assert cli_main(["new", "--free", str(source), "--scale", "vignette", "--count", "0"]) != 0


def test_duplicate_run_is_marked_and_replenished_with_next_index(tmp_path: Path) -> None:
    data_dir, batch_id = _batch(tmp_path, 2)
    first, second = [r["run_id"] for r in load_batch_manifest(data_dir, batch_id)["runs"]]
    assert _settle(data_dir, batch_id, first, _story()) is False
    assert _settle(data_dir, batch_id, second, _story()) is True
    batch = load_batch_manifest(data_dir, batch_id)
    assert [r["outcome"] for r in batch["runs"]] == ["accepted", "duplicate", "pending"]
    assert (batch["completed"], batch["duplicates"]) == (1, 1)
    new = load_manifest(data_dir / "runs" / batch["runs"][2]["run_id"] / "manifest.json")
    assert new["seed"] == derive_run_seed(100, 2)
    assert new["batch_id"] == batch_id
    assert new["input"]["kind"] == "free"


def test_distinct_story_is_accepted_without_replenishment(tmp_path: Path) -> None:
    data_dir, batch_id = _batch(tmp_path, 2)
    first, second = [r["run_id"] for r in load_batch_manifest(data_dir, batch_id)["runs"]]
    _settle(data_dir, batch_id, first, _story("あいうえおかきくけこ"))
    assert _settle(data_dir, batch_id, second, _story("さしすせそたちつてと", name="リオ")) is False
    batch = load_batch_manifest(data_dir, batch_id)
    assert len(batch["runs"]) == 2 and batch["completed"] == 2


def test_replenishment_stops_at_the_creation_limit(tmp_path: Path) -> None:
    data_dir, batch_id = _batch(tmp_path, 2)
    story = _story()
    for _ in range(20):
        batch = load_batch_manifest(data_dir, batch_id)
        pending = [r for r in batch["runs"] if r["outcome"] == "pending"]
        if not pending:
            break
        _settle(data_dir, batch_id, pending[0]["run_id"], copy.deepcopy(story))
    batch = load_batch_manifest(data_dir, batch_id)
    assert len(batch["runs"]) == max_created(2) == 12
    assert (batch["completed"], batch["duplicates"]) == (1, 11)
    assert not [r for r in batch["runs"] if r["outcome"] == "pending"]


def test_run_without_a_batch_manifest_is_left_alone(tmp_path: Path) -> None:
    assert settle_batch_run(tmp_path, "20260101-000000-abcdef", "20260101-000000-abcdef", _story(), {}, repository_root=ROOT) is False


def test_orchestrator_accepts_duplicate_status_from_code_task(tmp_path: Path) -> None:
    definitions = {"D1.plan": {"id": "D1.plan", "kind": "code", "version": 1, "handler": "plan"}}
    harness = Orchestrator(
        tmp_path, definitions,
        code_handlers={"plan": lambda context: CodeTaskResult({}, manifest_updates={"status": "duplicate"})},
    )
    run = harness.create_run(seed=1, task_specs=[TaskSpec("D1.plan", "D1.plan")])
    assert harness.advance(run)["status"] == "duplicate"


def test_s9_marks_duplicate_and_replenishes(tmp_path: Path) -> None:
    data_dir, batch_id = _batch(tmp_path, 2)
    document = {"kind": "free", "paragraphs": [{"id": "p001", "text": "素材"}]}
    outputs = _base_outputs({f"e{n:03d}": _event() for n in range(1, 4)})
    updates = []
    for entry in load_batch_manifest(data_dir, batch_id)["runs"]:
        run_dir = data_dir / "runs" / entry["run_id"]
        manifest = load_manifest(run_dir / "manifest.json")
        manifest["input_ratio"] = 0.7
        write_manifest(run_dir / "manifest.json", manifest)
        context = SimpleNamespace(
            run_id=entry["run_id"], task_id="S9.assemble", task={}, definition={}, inputs={},
            run_input=document, outputs=outputs, dependency_outputs=outputs,
            data_dir=data_dir, run_dir=run_dir,
        )
        updates.append(story_s9_assemble(context).manifest_updates)
    assert "status" not in updates[0]
    assert updates[1]["status"] == "duplicate"
    batch = load_batch_manifest(data_dir, batch_id)
    assert [r["outcome"] for r in batch["runs"]] == ["accepted", "duplicate", "pending"]


def test_settling_the_same_run_twice_does_not_replenish_again(tmp_path: Path) -> None:
    data_dir, batch_id = _batch(tmp_path, 2)
    first, second = [r["run_id"] for r in load_batch_manifest(data_dir, batch_id)["runs"]]
    _settle(data_dir, batch_id, first, _story())
    assert _settle(data_dir, batch_id, second, _story()) is True
    # S9 executed again for the same run (e.g. st retry after a later failure).
    document = {"kind": "free", "paragraphs": [{"id": "p001", "text": "素材"}]}
    assert settle_batch_run(data_dir, batch_id, second, _story(), document, repository_root=ROOT) is True
    assert settle_batch_run(data_dir, batch_id, first, _story(), document, repository_root=ROOT) is False
    batch = load_batch_manifest(data_dir, batch_id)
    assert len(batch["runs"]) == 3
    assert (batch["completed"], batch["duplicates"]) == (1, 1)


def test_replenishment_does_not_reuse_indices_reserved_for_initial_runs(tmp_path: Path) -> None:
    data_dir, batch_id = _batch(tmp_path, 3)
    path = data_dir / "batches" / batch_id / "manifest.json"
    batch = load_batch_manifest(data_dir, batch_id)
    batch["runs"] = batch["runs"][:2]  # index 2 is still being created
    path.write_text(json.dumps(batch), encoding="utf-8")
    first, second = [r["run_id"] for r in batch["runs"]]
    _settle(data_dir, batch_id, first, _story())
    assert _settle(data_dir, batch_id, second, _story()) is True
    assert [r["index"] for r in load_batch_manifest(data_dir, batch_id)["runs"]] == [0, 1, 3]


def test_near_duplicate_threshold_is_inclusive() -> None:
    left = {"cast": [{"name": "A"}], "events": [{"what": "abcde", "result": ""}]}
    right = {"cast": [{"name": "B"}], "events": [{"what": "abcdf", "result": ""}]}
    assert trigram_jaccard("abcde", "abcdf") == 0.5
    assert is_duplicate_story(right, [left], 0.5)
    assert not is_duplicate_story(right, [left], 0.51)
