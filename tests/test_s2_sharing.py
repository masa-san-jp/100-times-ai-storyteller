from __future__ import annotations

import json
from pathlib import Path

from storyteller.new_run import create_free_run, create_story_orchestrator
from storyteller.story_s3 import _table_pools
from storyteller.table_edit import add_row, axis_keys
from .test_story_s2 import ROOT, _complete_s1, _complete_s2


def _table(data_dir: Path, axis: str) -> list[dict[str, str]]:
    path = data_dir / "tables" / f"{axis}.json"
    return json.loads(path.read_text(encoding="utf-8"))["items"] if path.exists() else []


def _second_run(tmp_path: Path, seed: int = 1) -> str:
    return create_free_run(tmp_path / "data", tmp_path / "free.md", preset="vignette",
                           axis_overrides={}, seed=seed, parts=None, plot_type=None,
                           repository_root=ROOT)


def test_counter_elements_are_appended_once_per_axis_under_the_lock(tmp_path: Path) -> None:
    run_id, harness = _complete_s1(tmp_path)
    _complete_s2(harness, run_id)
    data_dir = tmp_path / "data"
    manifest = harness.load_run(run_id)
    for axis in axis_keys():
        rows = _table(data_dir, axis)
        assert rows == [{"text": f"{axis}を反対の条件から再構築する要素",
                         "source": f"{run_id}/S2.counter-m001-{axis}"}]
    # m002 groups produce the same normalized text; they are not duplicated.
    assert manifest["table_snapshot"] == {axis: 1 for axis in axis_keys()}
    assert not (data_dir / "tables.lock").exists()


def test_second_run_gets_s1_and_s2_from_cache_and_does_not_append(tmp_path: Path) -> None:
    run_id, harness = _complete_s1(tmp_path)
    _complete_s2(harness, run_id)
    data_dir = tmp_path / "data"
    before = {axis: _table(data_dir, axis) for axis in axis_keys()}

    second = _second_run(tmp_path)  # different seed: only sharing can explain cache hits
    harness.advance(second)
    manifest = harness.load_run(second)
    assert manifest["seed"] != harness.load_run(run_id)["seed"]
    assert (manifest["scale"]["derived"]["pool_need"]
            == harness.load_run(run_id)["scale"]["derived"]["pool_need"])
    shared = [task for task in manifest["tasks"].values()
              if task["type"].startswith(("S1.extract", "S2.expand", "S2.counter"))]
    assert shared
    assert all(task["state"] == "done" for task in shared)
    assert all(task["executor_id"] is None for task in shared if "executor_id" in task)
    for task_id, task in manifest["tasks"].items():
        if task["type"] in {"S1.extract", "S2.expand", "S2.counter"}:
            assert not (harness.task_dir(second, task_id) / "claim.json").exists()
    assert manifest["tasks"]["S2.merge"]["state"] == "done"
    # A cache hit never appends to the supplemental tables.
    assert {axis: _table(data_dir, axis) for axis in axis_keys()} == before
    assert manifest["table_snapshot"] == {axis: 1 for axis in axis_keys()}


def test_existing_run_assignment_is_reproduced_after_more_rows_are_appended(tmp_path: Path) -> None:
    run_id, harness = _complete_s1(tmp_path)
    _complete_s2(harness, run_id)
    data_dir = tmp_path / "data"
    snapshot = harness.load_run(run_id)["table_snapshot"]
    axes = axis_keys()
    before = _table_pools(snapshot, axes, data_dir, ROOT)
    assert any(row["id"].startswith(f"{axis}:x") for axis in axes for row in before[axis])

    for axis in axes:
        add_row(data_dir, axis, f"{axis}の後から追記された別の要素", None)

    # The run's manifest still limits the tables to the rows it saw.
    assert harness.load_run(run_id)["table_snapshot"] == snapshot
    assert _table_pools(snapshot, axes, data_dir, ROOT) == before

    # A run created after the append records the larger snapshot.
    later = _second_run(tmp_path)
    harness.advance(later)
    assert harness.load_run(later)["table_snapshot"] == {axis: 2 for axis in axes}
