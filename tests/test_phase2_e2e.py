"""P2-09: Phase 2 completion conditions, checked end to end with fake output.

No real executor is used: every task is answered by the deterministic fake
output of ``tests.test_story_cli_e2e``.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path

import pytest
import yaml

from storyteller.batch import load_batch_manifest
from storyteller.narrative import NARRATIVE_ITEMS
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
PRESETS = ["dev", *yaml.safe_load((ROOT / "tables" / "scales.yaml").read_text(encoding="utf-8"))["presets"]]
_PLOTS = load_table("plot_types", repository_root=ROOT)["types"]
PLOT_TYPES = [p["id"] for p in _PLOTS]
# Heavy scales are run with one plot type; every plot type runs at dev scale.
FIXED_PLOT = PLOT_TYPES[0]
# One plot type with the standard structure is completed by default (the dev
# run of FIXED_PLOT, which is also the dev preset's run). The seven plot types
# with a dedicated template are completed in tests/test_dedicated_plots.py, and
# the batch test completes dev runs with code-chosen plot types; the others are
# created and checked here, and STORYTELLER_E2E_HEAVY=1 completes all 21
# (about 1 minute each at dev scale).
COMPLETED_PLOTS = {FIXED_PLOT}
# Completing the larger scales takes minutes (short) to hours (saga) even with
# fake output, and implementation.md runs the volume minimum once per phase.
# So by default only the small presets are completed (dev by the plot type
# test above); the rest are created and checked, and STORYTELLER_E2E_HEAVY=1
# completes every preset.
HEAVY = os.environ.get("STORYTELLER_E2E_HEAVY") == "1"
COMPLETED_PRESETS = {"vignette"}
# Condition 1 asks for ten stories. A dev run takes about 40 seconds with fake
# output, so by default the same checks run on a batch of three, and
# STORYTELLER_E2E_HEAVY=1 runs the batch of ten.
BATCH_COUNT = 10 if HEAVY else 3
CANONICAL_FILES = ("story.json", "story.md", "characters.md", "world.md")


def _write_narrative(path: Path, filled: int = 4) -> Path:
    value = {key: ("傾向の記述" if index < filled else "") for index, key in enumerate(NARRATIVE_ITEMS)}
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def _salt(run_id: str, task_id: str) -> str:
    """Run-specific CJK text, so that fake stories of different runs differ."""

    digest = hashlib.shake_256(f"{run_id}/{task_id}".encode("utf-8")).digest(40)
    return "".join(chr(0x4E00 + byte * 40 + index) for index, byte in enumerate(digest))


def _drive(
    harness, data_dir: Path, run_id: str, submitted: Counter | None = None, *, vary: bool = False
) -> None:
    """Claim and answer every task of ``run_id`` until none is left."""

    sent_yes = False
    for _ in range(100000):
        claimed = harness.claim_next(run_id, executor_id="e2e", isolation="none")
        if claimed is None:
            return
        task_id, task = _claimed_task(data_dir, run_id, claimed["ticket"])
        inputs = _task_input(data_dir, run_id, task_id)
        is_compare = task["type"] == "S8.compare"
        if task["type"] == "S1.extract":
            # Narrative items are keyed by name (not p1, p2, ...).
            kinds = ("theme", "suppression", "conflict", "desire", "value")
            number = int(task["index"][1])
            output = {"text": f"{task['index'][0]}から見える素材{number}", "kind": kinds[number - 1]}
        else:
            output = _fake_output(task_id, task, inputs, send_comparison_yes=is_compare and not sent_yes)
            if vary and task["type"] == "S7.event" and task["index"][1] in {"what", "result"}:
                # The duplicate check compares the events' what/result.
                output = output + _salt(run_id, task_id)
        sent_yes = sent_yes or (is_compare and output["answer"] == "yes")
        definition = specialize_fact_definition(harness.task_definitions[task["type"]], inputs)
        text = output if definition["output"] == "text" else json.dumps(output, ensure_ascii=False)
        result = harness.submit(claimed["ticket"], text)
        assert result.accepted, (task_id, result.errors)
        if submitted is not None:
            submitted[task_id] += 1
    raise AssertionError(f"{run_id} did not finish")


def _new_narrative_run(tmp_path: Path, *arguments: str) -> tuple[Path, str]:
    data_dir = tmp_path / "data"
    narrative = _write_narrative(tmp_path / "narrative.json")
    created = _cli(data_dir, "new", "--narrative", str(narrative), *arguments)
    assert created.returncode == 0, created.stderr
    return data_dir, created.stdout.strip()


def _assert_canonical(data_dir: Path, run_id: str) -> None:
    manifest = _manifest(data_dir, run_id)
    assert manifest["status"] == "completed", {
        n: (t["state"], t.get("error")) for n, t in manifest["tasks"].items() if t["state"] != "done"
    }
    story_dir = data_dir / "runs" / run_id / "story"
    for name in CANONICAL_FILES:
        assert (story_dir / name).is_file(), name


@pytest.mark.parametrize("preset", PRESETS)
def test_every_scale_preset_produces_the_canonical_files(tmp_path: Path, preset: str) -> None:
    data_dir, run_id = _new_narrative_run(
        tmp_path, "--scale", preset, "--seed", "209", "--plot-type", FIXED_PLOT
    )
    manifest = _manifest(data_dir, run_id)
    assert manifest["scale"]["preset"] == preset
    assert manifest["status"] == "active" and manifest["tasks"]
    if preset == "dev" and not HEAVY:
        pytest.skip("the same dev run is completed by test_every_plot_type_produces_the_canonical_files")
    if not HEAVY and preset not in COMPLETED_PRESETS:
        pytest.skip(f"completing {preset} is opt-in (STORYTELLER_E2E_HEAVY=1)")
    _drive(create_story_orchestrator(data_dir), data_dir, run_id)
    _assert_canonical(data_dir, run_id)


@pytest.mark.parametrize("plot_id", PLOT_TYPES)
def test_every_plot_type_produces_the_canonical_files(tmp_path: Path, plot_id: str) -> None:
    data_dir, run_id = _new_narrative_run(
        tmp_path, "--scale", "dev", "--seed", "209", "--plot-type", plot_id
    )
    manifest = _manifest(data_dir, run_id)
    assert manifest["status"] == "active" and manifest["tasks"]
    assert manifest["input"]["plot_type"] == plot_id  # S3 reads it from here
    if not HEAVY and plot_id not in COMPLETED_PLOTS:
        pytest.skip(f"{plot_id}: completion is opt-in (STORYTELLER_E2E_HEAVY=1) or covered by test_dedicated_plots")
    _drive(create_story_orchestrator(data_dir), data_dir, run_id)
    _assert_canonical(data_dir, run_id)
    story = json.loads((data_dir / "runs" / run_id / "story" / "story.json").read_text(encoding="utf-8"))
    assert story["threads"][0]["plot_type"] == plot_id


def test_stories_from_one_narrative_are_distinct_and_share_s1_s2(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    narrative = _write_narrative(tmp_path / "narrative.json")
    created = _cli(data_dir, "new", "--narrative", str(narrative), "--scale", "dev", "--count", str(BATCH_COUNT), "--seed", "209")
    assert created.returncode == 0, created.stderr
    batch_id = created.stdout.strip()
    harness = create_story_orchestrator(data_dir)
    submitted: Counter = Counter()
    handled: set[str] = set()
    for _ in range(100):
        batch = load_batch_manifest(data_dir, batch_id)
        pending = [r["run_id"] for r in batch["runs"] if r["run_id"] not in handled]
        if not pending:
            break
        for run_id in pending:
            _drive(harness, data_dir, run_id, submitted, vary=True)
            handled.add(run_id)
    else:
        raise AssertionError("batch did not settle")

    batch = load_batch_manifest(data_dir, batch_id)
    assert not [r for r in batch["runs"] if r["outcome"] == "pending"]
    # Condition 1: no story is judged a duplicate or near duplicate.
    assert batch["duplicates"] == 0 and len(batch["runs"]) == BATCH_COUNT, batch["duplicates"]
    accepted = [r for r in batch["runs"] if r["outcome"] == "accepted"]
    assert batch["completed"] == len(accepted) == BATCH_COUNT
    for entry in accepted:
        _assert_canonical(data_dir, entry["run_id"])
    # Condition 2: S1 and S2 are executed once; the other runs reuse the result.
    s1_s2 = {task_id: n for task_id, n in submitted.items() if task_id.startswith(("S1.", "S2."))}
    # Every S1/S2 task answered by an executor in any run (the code tasks such
    # as S2.plan are not submitted) is answered exactly once in the batch.
    llm_s1_s2 = {
        task_id
        for entry in batch["runs"]
        for task_id, task in _manifest(data_dir, entry["run_id"])["tasks"].items()
        if task_id.startswith(("S1.", "S2.")) and task["kind"] == "llm"
    }
    assert any(t.startswith("S1.") for t in llm_s1_s2)
    assert set(s1_s2) == llm_s1_s2
    assert set(s1_s2.values()) == {1}, {t: n for t, n in s1_s2.items() if n != 1}


def test_narrative_with_personal_information_is_rejected(tmp_path: Path) -> None:
    # Detection itself is covered by tests/test_pii_check.py; here: `st new`.
    data_dir = tmp_path / "data"
    value = {key: ("傾向の記述" if index < 4 else "") for index, key in enumerate(NARRATIVE_ITEMS)}
    value["desire"] = "連絡先は a@b.example です。2024年3月5日に会う。"
    path = tmp_path / "pii.json"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    result = _cli(data_dir, "new", "--narrative", str(path), "--scale", "dev")

    assert result.returncode != 0
    assert "a@b.example" not in result.stdout + result.stderr
    assert not (data_dir / "runs").exists() or not list((data_dir / "runs").iterdir())
