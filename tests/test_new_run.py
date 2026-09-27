from __future__ import annotations

import json
from pathlib import Path

from storyteller.new_run import create_free_run


ROOT = Path(__file__).parents[1]


def test_create_free_run_writes_input_scale_and_one_s1_task_per_paragraph(
    tmp_path: Path,
) -> None:
    source = tmp_path / "free.md"
    source.write_text("一段目です。\n\n二段目です。", encoding="utf-8")
    data_dir = tmp_path / "data"

    run_id = create_free_run(
        data_dir,
        source,
        preset="vignette",
        axis_overrides={},
        seed=123,
        parts=None,
        plot_type="quest",
        repository_root=ROOT,
    )
    run_dir = data_dir / "runs" / run_id
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    input_value = json.loads((run_dir / "input.json").read_text(encoding="utf-8"))

    assert input_value["kind"] == "free"
    assert [paragraph["id"] for paragraph in input_value["paragraphs"]] == [
        "p001", "p002"
    ]
    assert manifest["scale"]["plot_type"] == "quest"
    assert set(manifest["tasks"]) == {
        "S1.extract-p001", "S1.extract-p002"
    }
    assert all(task["state"] == "ready" for task in manifest["tasks"].values())
