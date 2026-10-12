"""P2-09: every plot type reaches ``completed`` at the dev scale (``--plot-type``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from storyteller.new_run import create_story_orchestrator
from storyteller.tables import load_table
from storyteller.world_facts import specialize_fact_definition

from tests.test_story_cli_e2e import (
    _claimed_task,
    _cli,
    _fake_output,
    _manifest,
    _task_input,
)

ROOT = Path(__file__).parents[1]
FREE_INPUT = "静かな町で、失われた記録を探す。\n\n境界を守る仕組みが、暮らしの選択を変えていく。\n"
PLOT_TYPES = [plot["id"] for plot in load_table("plot_types", repository_root=ROOT)["types"]]


def test_the_sweep_covers_all_21_plot_types() -> None:
    assert len(PLOT_TYPES) == 21 == len(set(PLOT_TYPES))


@pytest.mark.parametrize("plot_type", PLOT_TYPES)
def test_dev_run_completes_for_every_plot_type(tmp_path: Path, plot_type: str) -> None:
    data_dir = tmp_path / "data"
    free_input = tmp_path / "free.md"
    free_input.write_text(FREE_INPUT, encoding="utf-8")
    created = _cli(
        data_dir, "new", "--free", str(free_input), "--scale", "dev", "--seed", "209",
        "--plot-type", plot_type,
    )
    assert created.returncode == 0, created.stderr
    run_id = created.stdout.strip()

    harness = create_story_orchestrator(data_dir)
    for _ in range(500):
        claimed = harness.claim_next(run_id, executor_id="sweep", isolation="none")
        if claimed is None:
            break
        task_id, task = _claimed_task(data_dir, run_id, claimed["ticket"])
        inputs = _task_input(data_dir, run_id, task_id)
        output = _fake_output(task_id, task, inputs, send_comparison_yes=False)
        definition = specialize_fact_definition(harness.task_definitions[task["type"]], inputs)
        if definition["output"] != "text":
            output = json.dumps(output, ensure_ascii=False)
        submitted = harness.submit(claimed["ticket"], output)
        assert submitted.accepted, (plot_type, task_id, submitted.errors)
    else:
        raise AssertionError(f"{plot_type}: run did not finish within the step limit")

    manifest = _manifest(data_dir, run_id)
    assert manifest["status"] == "completed", (plot_type, {
        n: (t["state"], t.get("error")) for n, t in manifest["tasks"].items() if t["state"] != "done"
    })
    story_dir = data_dir / "runs" / run_id / "story"
    for name in ("story.json", "story.md", "characters.md", "world.md"):
        assert (story_dir / name).is_file(), (plot_type, name)
    story = json.loads((story_dir / "story.json").read_text(encoding="utf-8"))
    assert [t["id"] for t in story["meta"]["plot_types"]] == [plot_type]
