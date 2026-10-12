"""P1-38: the development scale ``dev`` (scale.md, ADR-0009)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from storyteller.new_run import create_story_orchestrator
from storyteller.world_facts import specialize_fact_definition
from storyteller.scale import ScaleError, derive_scale

from tests.test_story_cli_e2e import (
    _claimed_task,
    _cli,
    _fake_output,
    _manifest,
    _task_input,
)

ROOT = Path(__file__).parents[1]
FREE_INPUT = "静かな町で、失われた記録を探す。\n\n境界を守る仕組みが、暮らしの選択を変えていく。\n"


def _new_dev_run(tmp_path: Path, seed: int = 38) -> tuple[Path, str]:
    data_dir = tmp_path / "data"
    free_input = tmp_path / "free.md"
    free_input.write_text(FREE_INPUT, encoding="utf-8")
    created = _cli(data_dir, "new", "--free", str(free_input), "--scale", "dev", "--seed", str(seed))
    assert created.returncode == 0, created.stderr
    return data_dir, created.stdout.strip()


def test_dev_scale_values_are_fixed_and_not_a_user_preset() -> None:
    result = derive_scale("dev", seed=1, repository_root=ROOT)
    derived = result.value["derived"]

    assert result.warnings == ()
    assert result.value["preset"] == "dev"
    assert (derived["events"], derived["cast"], derived["threads"]) == (3, 2, 1)
    assert derived["world_sections"] == ["place", "people"]
    assert derive_scale("dev", seed=2, repository_root=ROOT).value == result.value
    with pytest.raises(ScaleError):
        derive_scale("dev", overrides={"time": "weeks"}, seed=1, repository_root=ROOT)
    with pytest.raises(ScaleError):
        derive_scale("dev", seed=1, parts=3, repository_root=ROOT)


def test_dev_run_has_minimal_allocation(tmp_path: Path) -> None:
    data_dir, run_id = _new_dev_run(tmp_path)
    volume = _manifest(data_dir, run_id)["scale"]["derived"]["volume"]

    assert volume["multiplier"] == 0
    place, people = volume["world"]["sections"]
    assert [v["facet_count"] for v in place["viewpoints"]] == [1]
    assert people["item_count"] == 1
    assert volume["story"]["scene_counts"] == [1, 1, 1]


@pytest.mark.parametrize("seed", [38, 5])
def test_dev_run_reaches_completed_with_every_llm_task_kind(tmp_path: Path, seed: int) -> None:
    data_dir, run_id = _new_dev_run(tmp_path, seed)
    harness = create_story_orchestrator(data_dir)
    for _ in range(500):
        claimed = harness.claim_next(run_id, executor_id="dev", isolation="none")
        if claimed is None:
            break
        task_id, task = _claimed_task(data_dir, run_id, claimed["ticket"])
        output = _fake_output(
            task_id, task, _task_input(data_dir, run_id, task_id), send_comparison_yes=False
        )
        inputs = _task_input(data_dir, run_id, task_id)
        if task["type"] == "S5.relationship":
            other = inputs["other_person"]
            assert other and other[0]["name"] and other[0]["intro"], inputs
        definition = specialize_fact_definition(harness.task_definitions[task["type"]], inputs)
        if definition["output"] != "text":
            output = json.dumps(output, ensure_ascii=False)
        submitted = harness.submit(claimed["ticket"], output)
        assert submitted.accepted, (task_id, submitted.errors)
    else:
        raise AssertionError("dev の run が上限回数で完走しませんでした")

    manifest = _manifest(data_dir, run_id)
    assert manifest["status"] == "completed", {
        n: (t["state"], t.get("error")) for n, t in manifest["tasks"].items() if t["state"] not in {"done"}
    }
    tasks = manifest["tasks"]
    assert not [name for name, task in tasks.items() if task["state"] in {"failed", "skipped"}]
    llm_types = {
        task_type
        for task_type, definition in harness.task_definitions.items()
        if definition["kind"] == "llm" and not task_type.startswith(("D", "F"))
    }
    used_types = {task["type"] for task in tasks.values()}
    # F* (formatting) tasks are created by `st format`, not by a story run.
    assert llm_types <= used_types, sorted(llm_types - used_types)
    assert len(tasks) < 400  # a few minutes with a real model (ADR-0009)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")
def test_dev_run_script_creates_a_dev_run_and_reports(tmp_path: Path) -> None:
    script = ROOT / "tools" / "dev_run.sh"
    data_dir = tmp_path / "data"
    result = subprocess.run(
        ["bash", str(script), "--data-dir", str(data_dir), "--seed", "3", "--command", "true"],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )

    # The driver does nothing, so the run is not completed: exit 1 with a report.
    assert result.returncode == 1, result.stderr
    assert "elapsed:" in result.stdout and "skipped: 0" in result.stdout
    assert "warnings: 0" in result.stdout and "status: active" in result.stdout
    run_id = next(line.split(": ", 1)[1] for line in result.stdout.splitlines() if line.startswith("run: "))
    assert _manifest(data_dir, run_id)["scale"]["preset"] == "dev"

    usage = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, encoding="utf-8", check=False
    )
    assert usage.returncode == 2
