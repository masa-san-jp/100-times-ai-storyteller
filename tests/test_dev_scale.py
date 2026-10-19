"""P1-38: the development scale ``dev`` (scale.md, ADR-0009)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
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


def test_dev_run_reaches_completed_with_every_llm_task_kind(tmp_path: Path) -> None:
    data_dir, run_id = _new_dev_run(tmp_path)
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
        from storyteller.volume import dev_task_definition
        _, bounds = dev_task_definition(harness.task_definitions[task["type"]], {"preset": "dev"}, ROOT)
        if bounds is not None:
            assert inputs["output_chars"] == bounds
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
    # The condition tasks belong to the S6.expand v5 and older graphs only (ADR-0011).
    llm_types -= {"S7.condition", "S8.condition_check"}
    assert llm_types <= used_types, sorted(llm_types - used_types)
    s2_plan = json.loads((harness.task_dir(run_id, "S2.plan") / "output.json").read_text(encoding="utf-8"))
    assert {entry["axis"]["key"] for entry in s2_plan["plans"].values()} == {"want"}
    pools = json.loads((harness.task_dir(run_id, "S2.merge") / "output.json").read_text(encoding="utf-8"))["pools"]
    assert pools["want"] and all(not pool for axis, pool in pools.items() if axis != "want")
    assignment = json.loads((harness.task_dir(run_id, "S3.assign") / "output.json").read_text(encoding="utf-8"))
    def element_ids(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "id" and isinstance(item, str):
                    yield item
                else:
                    yield from element_ids(item)
        elif isinstance(value, list):
            for item in value:
                yield from element_ids(item)
    assert any(identifier.startswith("ability:t") for identifier in element_ids(assignment))
    assert not any(":i" in identifier and not identifier.startswith("want:") for identifier in element_ids(assignment))

    assert len(tasks) < 240  # a few minutes with a real model (ADR-0009)
    for event in ('e001', 'e002', 'e003'):
        assert {name for name in tasks if name.startswith(f'S7.event-{event}-what-c')} == {
            f'S7.event-{event}-what-c1', f'S7.event-{event}-what-c2',
        }
        choice = json.loads((harness.task_dir(run_id, f'S7.select-{event}') / 'output.json').read_text(encoding='utf-8'))
        assert choice['candidate'] in {'c1', 'c2'}
        picked = (harness.task_dir(run_id, f"S7.event-{event}-what-{choice['candidate']}") / 'output.md').read_text(encoding='utf-8')
        assert choice['text'] == picked
        assembled = json.loads((harness.task_dir(run_id, f'S7.assemble-{event}') / 'output.json').read_text(encoding='utf-8'))
        assert assembled['what'] == picked



def test_dev_prose_bounds_do_not_change_normal_definitions(tmp_path: Path) -> None:
    from copy import deepcopy
    from storyteller.volume import dev_task_definition

    harness = create_story_orchestrator(tmp_path / "data")
    original = harness.task_definitions["S5.profile"]
    snapshot = deepcopy(original)
    dev, bounds = dev_task_definition(original, {"preset": "dev"}, ROOT)
    normal, normal_bounds = dev_task_definition(original, {"preset": "short"}, ROOT)
    assert bounds == [40, 80]
    assert normal_bounds is None
    assert normal is original and original == snapshot
    checks = dev["validate"]["checks"]
    assert next(c["min_chars"]["n"] for c in checks if "min_chars" in c) == 40
    assert next(c["max_chars"]["n"] for c in checks if "max_chars" in c) == 80
    assert dev["inputs"]["output_chars"]["required"] is True
    # Identity and factual validation still apply to the smaller prose.
    assert [c for c in checks if "character_identity" in c] == [
        c for c in original["validate"]["checks"] if "character_identity" in c
    ]


def test_dev_source_allocation_has_one_task_per_paragraph(tmp_path: Path) -> None:
    data_dir, run_id = _new_dev_run(tmp_path)
    tasks = _manifest(data_dir, run_id)["tasks"]
    sources = [task for task in tasks.values() if task["type"] == "S1.extract"]
    assert len(sources) == 2
    assert {tuple(task["index"]) for task in sources} == {("p001", "1"), ("p002", "1")}


@pytest.mark.parametrize("length,accepted", [(14, False), (15, True), (60, True), (61, False)])
def test_dev_catchphrase_validates_both_bounds(tmp_path: Path, length: int, accepted: bool) -> None:
    from storyteller.validation import validate_output
    from storyteller.volume import dev_task_definition

    harness = create_story_orchestrator(tmp_path / "data")
    original = harness.task_definitions["S5.catchphrase"]
    dev, _ = dev_task_definition(original, {"preset": "dev"}, ROOT)
    # Isolate the length checks; the full run exercises the retained semantic checks.
    definition = dict(dev)
    definition["validate"] = {"checks": [
        check for check in dev["validate"]["checks"]
        if isinstance(check, dict) and ("min_chars" in check or "max_chars" in check)
    ]}
    result = validate_output(definition, "あ" * (length - 1) + "。")
    assert result.passed is accepted
    assert not any(isinstance(check, dict) and "max_chars" in check
                   for check in original["validate"]["checks"])


def _test_bash() -> str | None:
    """Use Git Bash on Windows; System32/bash.exe is a WSL launcher."""
    if sys.platform == "win32":
        git = shutil.which("git")
        if git:
            candidate = Path(git).resolve().parent.parent / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
        return None
    return shutil.which("bash")


@pytest.mark.skipif(_test_bash() is None, reason="a native Bash is required")
def test_dev_run_script_creates_a_dev_run_and_reports(tmp_path: Path) -> None:
    script = ROOT / "tools" / "dev_run.sh"
    data_dir = tmp_path / "data"
    result = subprocess.run(
        [_test_bash(), str(script), "--data-dir", str(data_dir), "--seed", "3", "--command", "true"],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )

    # The driver does nothing, so the run is not completed: exit 1 with a report.
    assert result.returncode == 1, result.stderr
    assert "elapsed:" in result.stdout and "skipped: 0" in result.stdout
    assert "warnings: 0" in result.stdout and "status: active" in result.stdout
    run_id = next(line.split(": ", 1)[1] for line in result.stdout.splitlines() if line.startswith("run: "))
    assert _manifest(data_dir, run_id)["scale"]["preset"] == "dev"

    usage = subprocess.run(
        [_test_bash(), str(script)], capture_output=True, text=True, encoding="utf-8", check=False
    )
    assert usage.returncode == 2


@pytest.mark.parametrize("seed", [5, 7])
def test_dev_run_relationship_cards_keep_the_counterpart(tmp_path: Path, seed: int) -> None:
    data_dir, run_id = _new_dev_run(tmp_path, seed=seed)
    harness = create_story_orchestrator(data_dir)
    seen = 0
    for _ in range(500):
        claimed = harness.claim_next(run_id, executor_id="dev", isolation="none")
        if claimed is None:
            break
        task_id, task = _claimed_task(data_dir, run_id, claimed["ticket"])
        inputs = _task_input(data_dir, run_id, task_id)
        if task["type"] == "S5.relationship":
            seen += 1
            assert inputs["other_person"], task_id
            assert inputs["other_person"][0]["name"] in claimed["card"], task_id
        output = _fake_output(task_id, task, inputs, send_comparison_yes=False)
        definition = specialize_fact_definition(harness.task_definitions[task["type"]], inputs)
        if definition["output"] != "text":
            output = json.dumps(output, ensure_ascii=False)
        assert harness.submit(claimed["ticket"], output).accepted, task_id
    assert seen
    assert _manifest(data_dir, run_id)["status"] == "completed"


def test_windows_script_launcher_uses_git_bash_instead_of_wsl(tmp_path, monkeypatch):
    git_root = tmp_path / "Git"
    git = git_root / "cmd" / "git.exe"
    bash = git_root / "bin" / "bash.exe"
    git.parent.mkdir(parents=True)
    bash.parent.mkdir(parents=True)
    git.touch()
    bash.touch()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(shutil, "which", lambda name: str(git) if name == "git" else "C:/Windows/System32/bash.exe")
    assert _test_bash() == str(bash)
    bash.unlink()
    assert _test_bash() is None


@pytest.mark.parametrize('task_type', ['S4.section', 'S4.item', 'S5.profile', 'S5.motive', 'S7.detail'])
def test_dev_short_bounds_match_card_and_validation(tmp_path, task_type):
    from storyteller.volume import dev_task_definition
    from storyteller.validation import validate_output
    harness = create_story_orchestrator(tmp_path / 'data')
    definition, bounds = dev_task_definition(harness.task_definitions[task_type], {'preset': 'dev'}, ROOT)
    assert bounds == [40, 80]
    assert any('40〜80字' in step for step in definition['card']['steps'])
    checks = [check for check in definition['validate']['checks'] if 'min_chars' in check or 'max_chars' in check]
    length_only = {**definition, 'validate': {'checks': checks}}
    for length, accepted in ((39, False), (40, True), (69, True), (80, True), (81, False)):
        assert validate_output(length_only, 'あ' * (length - 1) + '。').passed is accepted
