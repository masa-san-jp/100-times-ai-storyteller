"""P1-68: condition-pair checks repair only the later condition (story-pipeline §9.5)."""

from __future__ import annotations

from tests.support.legacy_harness import legacy_story_harness, task_path  # noqa: F401

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from storyteller.cards import generate_task_card, input_char_count, prepare_task_inputs
from storyteller.new_run import create_story_orchestrator
from storyteller.orchestrator import Orchestrator, _refresh_blocked_tasks
from storyteller.plot_quality import CONDITION_LABELS
from storyteller.story_s6 import story_s6_expand
from storyteller.story_s8 import story_s8_condition_judge
from storyteller.world_facts import specialize_fact_definition

from tests.test_dev_scale import _new_dev_run
from tests.test_story_cli_e2e import _claimed_task, _fake_output, _manifest, _task_input
from tests.test_story_s6 import _assignment, _context

ROOT = Path(__file__).parents[1]
SLOT = "e001"
TRIGGER = f"S7.condition-{SLOT}-trigger"
PROHIBITION = f"S7.condition-{SLOT}-prohibition"
RESOLUTION = f"S7.condition-{SLOT}-resolution"


class Raw(str):
    """An output submitted as is, whose acceptance is not asserted."""


Override = Callable[[str, dict[str, Any], dict[str, Any]], Any]


def _drive(data_dir: Path, run_id: str, harness: Orchestrator,
           override: Override | None = None) -> list[tuple[str, str]]:
    cards: list[tuple[str, str]] = []
    for _ in range(800):
        claimed = harness.claim_next(run_id, executor_id="dev", isolation="none")
        if claimed is None:
            return cards
        task_id, task = _claimed_task(data_dir, run_id, claimed["ticket"])
        inputs = _task_input(data_dir, run_id, task_id)
        cards.append((task_id, claimed["card"]))
        output = override(task_id, task, inputs) if override else None
        if isinstance(output, Raw):
            harness.submit(claimed["ticket"], str(output))
            continue
        if output is None:
            output = _fake_output(task_id, task, inputs, send_comparison_yes=False)
        definition = specialize_fact_definition(harness.task_definitions[task["type"]], inputs)
        if definition["output"] != "text":
            output = json.dumps(output, ensure_ascii=False)
        submitted = harness.submit(claimed["ticket"], output)
        assert submitted.accepted, (task_id, submitted.errors)
    raise AssertionError("dev の run が上限回数で完走しませんでした")


def _output(harness: Orchestrator, run_id: str, task_id: str) -> Any:
    directory = harness.task_dir(run_id, task_id)
    if (directory / "output.json").is_file():
        return json.loads((directory / "output.json").read_text(encoding="utf-8"))
    return (directory / "output.md").read_text(encoding="utf-8")


def _run(tmp_path: Path, override: Override | None = None, seed: int = 68):
    data_dir, run_id = _new_dev_run(tmp_path, seed=seed)
    harness = create_story_orchestrator(data_dir)
    cards = _drive(data_dir, run_id, harness, override)
    return harness, run_id, _manifest(data_dir, run_id), cards


def _assert_no_failure_trace(harness: Orchestrator, run_id: str, manifest: dict[str, Any], task_id: str) -> None:
    task = manifest["tasks"][task_id]
    assert task["state"] == "skipped"
    assert task["error"] is None
    assert task["history"][-1]["reason"] == "比較する条件が未定"
    assert not any(warning.startswith(f"{task_id}:") for warning in manifest["warnings"])
    assert not (harness.task_dir(run_id, task_id) / "failure.md").exists()


def test_incompatible_pair_invalidates_only_the_later_condition(tmp_path: Path) -> None:
    """(1) Only the later condition is redone; the earlier one is unchanged."""
    answered: list[str] = []

    def override(task_id: str, task: dict[str, Any], inputs: dict[str, Any]) -> Any:
        if task["type"] == "S8.condition_check" and task_id.endswith("-prohibition") and not answered:
            answered.append(inputs["decided_conditions"])
            return {"answer": "yes", "reason": "Bは灯を消すことを禁じ；Aは灯を消すことを必要とする。"}
        if task_id == PROHIBITION and task["attempt"]:
            # A regenerated condition differs, so the check is not a cache hit.
            return "扉を叩くことは禁じられ、叩くと道を失う。"
        return None

    harness, run_id, manifest, cards = _run(tmp_path, override)
    assert manifest["status"] == "completed"
    tasks = manifest["tasks"]
    assert tasks[PROHIBITION]["state"] == "done"
    assert tasks[PROHIBITION]["invalidations"] == 1
    assert tasks[TRIGGER]["invalidations"] == 0
    assert all(event["to"] != "ready" or event["from"] != "done" for event in tasks[TRIGGER]["history"])
    assert [task_id for task_id, _ in cards].count(TRIGGER) == 1
    assert answered == [f"{CONDITION_LABELS['trigger']}：{_output(harness, run_id, TRIGGER)}"]
    reclaimed = [card for task_id, card in cards if task_id == PROHIBITION][1]
    assert "先の条件と両立しない：Bは灯を消すことを禁じ;Aは灯を消すことを必要とする。" in reclaimed
    # The repaired condition was compared again before events were generated.
    assert [task_id for task_id, _ in cards].count(f"S8.condition_check-{SLOT}-prohibition") == 2
    assert _output(harness, run_id, f"S8.condition_judge-{SLOT}-prohibition") == {
        "slot": f"{SLOT}-prohibition", "invalidated": False}
    assert tasks[RESOLUTION]["invalidations"] == 0


def test_bound_skips_the_later_condition_and_events_proceed(tmp_path: Path) -> None:
    """(2) Past the bound the later condition is undecided and the run does not stall."""

    def override(task_id: str, task: dict[str, Any], inputs: dict[str, Any]) -> Any:
        if task["type"] == "S8.condition_check" and task_id.endswith("-resolution"):
            return {"answer": "yes", "reason": "Bの解決はAの禁止と両立しない。"}
        return None

    harness, run_id, manifest, cards = _run(tmp_path, override)
    assert manifest["status"] == "completed"
    tasks = manifest["tasks"]
    resolution = tasks[RESOLUTION]
    assert resolution["state"] == "skipped" and resolution["invalidations"] == 2
    assert [task_id for task_id, _ in cards].count(RESOLUTION) == 3
    assert resolution["error"].startswith("先の条件と両立しない：")
    assert any(warning.startswith(f"{RESOLUTION}: 無効化の上限に達したため省略")
               for warning in manifest["warnings"])
    assert (harness.task_dir(run_id, RESOLUTION) / "failure.md").is_file()
    # The check and judge keep their outputs; earlier conditions are unchanged.
    assert tasks[f"S8.condition_check-{SLOT}-resolution"]["state"] == "done"
    assert _output(harness, run_id, f"S8.condition_judge-{SLOT}-resolution") == {
        "slot": f"{SLOT}-resolution", "invalidated": True}
    assert tasks[TRIGGER]["invalidations"] == tasks[PROHIBITION]["invalidations"] == 0
    assembled = _output(harness, run_id, "S7.assemble-e001")
    assert "（未定）" in assembled["conditions"]
    assert all(task["state"] in {"done", "skipped"} for task in tasks.values())


def test_compatible_pairs_invalidate_nothing_and_card_matches_spec(tmp_path: Path) -> None:
    """(3) Compatible pairs keep every condition. (6) The card is the spec text."""
    harness, run_id, manifest, cards = _run(tmp_path)
    assert manifest["status"] == "completed"
    tasks = manifest["tasks"]
    for kind in ("trigger", "prohibition", "resolution"):
        assert tasks[f"S7.condition-{SLOT}-{kind}"]["invalidations"] == 0
    for kind in ("prohibition", "resolution"):
        assert tasks[f"S8.condition_check-{SLOT}-{kind}"]["state"] == "done"
        assert _output(harness, run_id, f"S8.condition_judge-{SLOT}-{kind}") == {
            "slot": f"{SLOT}-{kind}", "invalidated": False}
    assert f"S8.condition_check-{SLOT}-trigger" not in tasks

    card = next(card for task_id, card in cards if task_id == f"S8.condition_check-{SLOT}-resolution")
    spec = (ROOT / "docs/spec/story-pipeline.md").read_text(encoding="utf-8")
    definition = harness.task_definitions["S8.condition_check"]
    assert "筋の条件が、先に確定した条件と両立するかを検査する。" in card
    for number, step in enumerate(definition["card"]["steps"], start=1):
        assert f"      {number}. 「{step}」" in spec
        assert f"{number}. {step}" in card
    assert len(definition["card"]["steps"]) == 4
    assert "### 今回の条件（記述B）" in card and "### 先に確定した条件（記述A）" in card
    decided = (f"{CONDITION_LABELS['trigger']}：{_output(harness, run_id, TRIGGER)}\n"
               f"{CONDITION_LABELS['prohibition']}：{_output(harness, run_id, PROHIBITION)}")
    assert decided in card
    inputs = _task_input(harness.data_dir, run_id, f"S8.condition_check-{SLOT}-resolution")
    assert inputs == {"condition": _output(harness, run_id, RESOLUTION), "decided_conditions": decided}


def test_card_fits_the_budget_with_maximum_conditions() -> None:
    """(6) Three 160-character conditions fit 2800 characters untruncated, retry included."""
    definition = yaml.safe_load((task_path("S8.condition_check")).read_text(encoding="utf-8"))
    assert definition["version"] == 1 and definition["element"] == "judgement"
    assert definition["max_input_chars"] == 2800
    assert all("truncate" not in slot for slot in definition["inputs"].values())
    decided = "\n".join(f"{CONDITION_LABELS[kind]}：" + "条" * 160 for kind in ("trigger", "prohibition"))
    source = {"condition": "件" * 160, "decided_conditions": decided}
    fitted = prepare_task_inputs(definition, inputs=source)
    assert fitted == source and input_char_count(definition, fitted) <= 2800
    card = generate_task_card(definition, "a" * 32, inputs=fitted, retry_reason="理" * 500)
    model = yaml.safe_load((ROOT / "config/models.yaml").read_text(encoding="utf-8"))["models"]["gpt-oss:20b"]
    assert 2 * len(card) + model["max_tokens"] <= model["context_length"]


def _exhaust(target: str) -> Override:
    def override(task_id: str, task: dict[str, Any], inputs: dict[str, Any]) -> Any:
        if task_id == target:
            return Raw("長" * 300 + "。")  # max_chars fails until the attempts run out
        return None
    return override


def test_check_is_skipped_when_all_earlier_conditions_are_undecided(tmp_path: Path) -> None:
    """(4)(7) No earlier condition: skipped check, compatible judge, no failure trace."""
    harness, run_id, manifest, cards = _run(tmp_path, _exhaust(TRIGGER))
    assert manifest["status"] == "completed"
    tasks = manifest["tasks"]
    assert tasks[TRIGGER]["state"] == "skipped"
    check = f"S8.condition_check-{SLOT}-prohibition"
    assert check not in [task_id for task_id, _ in cards]
    _assert_no_failure_trace(harness, run_id, manifest, check)
    assert _output(harness, run_id, f"S8.condition_judge-{SLOT}-prohibition") == {
        "slot": f"{SLOT}-prohibition", "invalidated": False}
    # The resolution is still compared with the decided prohibition only.
    inputs = _task_input(harness.data_dir, run_id, f"S8.condition_check-{SLOT}-resolution")
    assert inputs["decided_conditions"] == f"{CONDITION_LABELS['prohibition']}：{_output(harness, run_id, PROHIBITION)}"


def test_check_is_skipped_when_the_current_condition_is_undecided(tmp_path: Path) -> None:
    """(4)(7) The current condition was skipped by its attempts."""
    harness, run_id, manifest, cards = _run(tmp_path, _exhaust(RESOLUTION))
    assert manifest["status"] == "completed"
    check = f"S8.condition_check-{SLOT}-resolution"
    assert check not in [task_id for task_id, _ in cards]
    _assert_no_failure_trace(harness, run_id, manifest, check)
    assert _output(harness, run_id, f"S8.condition_judge-{SLOT}-resolution")["invalidated"] is False
    # The skipped condition itself keeps its ordinary failure record.
    assert (harness.task_dir(run_id, RESOLUTION) / "failure.md").is_file()


@pytest.mark.parametrize("state", ["blocked", "ready"])
def test_automatic_skip_clears_an_earlier_error(state: str) -> None:
    """(7) The transition clears a stale error and records only the reason."""
    manifest = {"warnings": [], "tasks": {
        TRIGGER: {"type": "S7.condition", "state": "skipped", "deps": [], "index": [SLOT, "trigger"],
                  "history": [], "error": "試行の上限"},
        PROHIBITION: {"type": "S7.condition", "state": "done", "deps": [], "index": [SLOT, "prohibition"],
                      "history": [], "error": None},
        "check": {"type": "S8.condition_check", "state": state, "deps": [TRIGGER, PROHIBITION],
                  "index": [SLOT, "prohibition"], "history": [], "error": "以前の試行の理由",
                  "cache_key": None},
    }}
    _refresh_blocked_tasks(manifest, "2026-10-18T00:00:00Z")
    check = manifest["tasks"]["check"]
    assert check["state"] == "skipped" and check["error"] is None
    assert check["history"] == [{"at": "2026-10-18T00:00:00Z", "from": state, "to": "skipped",
                                 "reason": "比較する条件が未定"}]
    assert manifest["warnings"] == []


def _s6_task_ids(s6_version: int | None) -> dict[str, tuple[str, ...]]:
    context = _context(_assignment(multi_thread=True))
    if s6_version is not None:
        context.definition = {"id": "S6.expand", "version": s6_version}
    return {spec.task_id: spec.deps for spec in story_s6_expand(context).add_tasks}


def test_checks_follow_only_the_s6_expand_version() -> None:
    """(5) v4 or older adds nothing; v5 adds the static checks and dependencies."""
    for version in (None, 3, 4):
        tasks = _s6_task_ids(version)
        assert not [task_id for task_id in tasks if task_id.startswith("S8.condition_")]
        assert all(not dep.startswith("S8.condition_") for deps in tasks.values() for dep in deps)

    tasks = _s6_task_ids(5)
    slots = sorted({task_id.split("-")[1] for task_id in tasks if task_id.startswith("S7.condition-")})
    assert len(slots) >= 2  # one first slot per thread
    for slot in slots:
        trigger, prohibition, resolution = (f"S7.condition-{slot}-{kind}"
                                            for kind in ("trigger", "prohibition", "resolution"))
        assert tasks[f"S8.condition_check-{slot}-prohibition"] == ("S6.expand", prohibition, trigger)
        assert tasks[f"S8.condition_check-{slot}-resolution"] == ("S6.expand", resolution, trigger, prohibition)
        assert tasks[f"S8.condition_judge-{slot}-prohibition"] == ("S6.expand", f"S8.condition_check-{slot}-prohibition")
        assert f"S8.condition_check-{slot}-trigger" not in tasks
        assert tasks[trigger] == ("S6.expand",)
        assert tasks[prohibition] == ("S6.expand", trigger)
        assert tasks[resolution] == ("S6.expand", trigger, prohibition, f"S8.condition_judge-{slot}-prohibition")
    judges = {f"S8.condition_judge-{slot}-resolution" for slot in slots}
    for task_id, deps in tasks.items():
        kind = task_id.split("-", 1)[0]
        if kind in {"S7.event", "S7.select", "S7.assemble", "S8.plan"}:
            assert len(judges & set(deps)) == 1, task_id
        if kind in {"S7.detail", "S8.detail", "S9.assemble"}:
            assert not judges & set(deps), task_id


def _run_with_versions(tmp_path: Path, s6_version: int, condition_version: int) -> dict[str, Any]:
    data_dir, run_id = _new_dev_run(tmp_path)
    base = create_story_orchestrator(data_dir)
    definitions = deepcopy(base.task_definitions)
    definitions["S6.expand"]["version"] = s6_version
    definitions["S7.condition"]["version"] = condition_version
    harness = Orchestrator(data_dir, definitions, base.code_handlers, harness_root=base.definition_root,
                           repository_root=base.repository_root, harness_kind=base.harness_kind)
    _drive(data_dir, run_id, harness)
    manifest = _manifest(data_dir, run_id)
    assert manifest["status"] == "completed"
    return manifest


def test_older_s6_expand_run_adds_no_checks_despite_condition_v3(tmp_path: Path) -> None:
    """(5) An S6.expand v4 graph has no checks even with S7.condition v3."""
    manifest = _run_with_versions(tmp_path, 4, 3)
    assert not [task_id for task_id in manifest["tasks"] if task_id.startswith("S8.condition_")]
    assert all(not dep.startswith("S8.condition_")
               for task in manifest["tasks"].values() for dep in task["deps"])


def test_s6_v5_adds_checks_even_with_an_older_condition_definition(tmp_path: Path) -> None:
    """(5) The decision does not read S7.condition's version."""
    manifest = _run_with_versions(tmp_path, 5, 2)
    checks = sorted(task_id for task_id in manifest["tasks"] if task_id.startswith("S8.condition_"))
    assert checks == [f"S8.condition_{kind}-{SLOT}-{cond}" for kind in ("check", "judge")
                      for cond in ("prohibition", "resolution")]
