from __future__ import annotations

import json
from pathlib import Path

from storyteller.new_run import create_free_run


ROOT = Path(__file__).parents[1]


def test_create_free_run_writes_input_and_five_sequential_s1_tasks_per_paragraph(
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
    assert manifest["input"] == {
        "kind": "free",
        "source_sha256": input_value["source_sha256"],
        "plot_type": "quest",
    }
    assert manifest["harness_kind"] == "story"
    assert "plot_type" not in manifest["scale"]
    s1_ids = {f"S1.extract-{paragraph}-{number}" for paragraph in ("p001", "p002") for number in range(1, 6)}
    assert set(manifest["tasks"]) == s1_ids | {"S2.plan"}
    for paragraph in ("p001", "p002"):
        for number in range(1, 6):
            task = manifest["tasks"][f"S1.extract-{paragraph}-{number}"]
            assert task["index"] == [paragraph, str(number)]
            assert task["deps"] == [f"S1.extract-{paragraph}-{earlier}" for earlier in range(1, number)]
            assert task["state"] == ("ready" if number == 1 else "blocked")
    assert set(manifest["tasks"]["S2.plan"]["deps"]) == s1_ids
    assert manifest["tasks"]["S2.plan"]["state"] == "blocked"
