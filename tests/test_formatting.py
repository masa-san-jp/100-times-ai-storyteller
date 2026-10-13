"""P3-01: common parts of the formatting harness (F0-F4, ``st format``)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

import storyteller.format_units as format_units
import storyteller.formatting as formatting
from storyteller.cli import main as cli_main
from storyteller.format_units import Unit, map_per_event
from storyteller.manifest import write_manifest
from storyteller.new_run import create_story_orchestrator
from storyteller.orchestrator import InvalidTransition, Orchestrator, TaskSpec
from storyteller.story_s9 import story_s9_assemble
from storyteller.validation import SchemaValidationError, validate_document
from tests.test_story_s9 import _base_outputs, _context, _event


ROOT = Path(__file__).parents[1]
PROFILE = {
    "name": "dummy",
    "unit": "scene",
    "unit_mapping": "scene",
    "chars_per_event": [10, 40],
    "max_chars_per_task": 90,
    "template": "template.md",
    "continuation": True,
    "rules": ["入力の出来事の順序を変えない。"],
    "checks": {
        "code": ["min_chars", "max_chars", "ends_complete"],
        "llm": ["入力にない人物が登場していますか"],
    },
}
TEMPLATE = (
    "# {{title}}\n\n{{#units}}## {{unit.number}}. {{unit.title}}\n\n{{unit.body}}\n\n{{/units}}"
    "了 {{protagonist}} {{unit_count}}\n"
)


def _group_by_three(story: dict[str, Any], profile: dict[str, Any], root: Path) -> list[Unit]:
    ids = [event["id"] for event in story["events"]]
    return [
        Unit(f"u{number:02d}", f"場面{number}", tuple(ids[start:start + 3]))
        for number, start in enumerate(range(0, len(ids), 3), start=1)
    ]


@pytest.fixture
def dummy_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throw-away format whose ``scene`` mapping groups events by three."""

    formats_root = tmp_path / "formats"
    (formats_root / "dummy").mkdir(parents=True)
    (formats_root / "dummy" / "profile.yaml").write_text(
        yaml.safe_dump(PROFILE, allow_unicode=True), encoding="utf-8"
    )
    (formats_root / "dummy" / "template.md").write_text(TEMPLATE, encoding="utf-8")
    monkeypatch.setitem(format_units.UNIT_MAPPERS, "scene", _group_by_three)
    monkeypatch.setattr(formatting, "default_formats_root", lambda: formats_root)
    return formats_root


def _completed_story_run(tmp_path: Path, events: int = 5) -> tuple[Path, str]:
    """A story run whose canonical story was written by S9 and is completed."""

    data_dir = tmp_path / "data"
    outputs = _base_outputs({f"e{n:03d}": _event(what=f"歩いた{n}") for n in range(1, events + 1)})
    context = _context(data_dir, outputs, {"kind": "free", "paragraphs": [{"id": "p001", "text": "旅"}]})
    story_s9_assemble(context)
    manifest_path = context.run_dir / "manifest.json"
    from storyteller.manifest import load_manifest

    manifest = load_manifest(manifest_path)
    # The stub graph must stay valid under the real definitions: stand in for
    # the finished story with one completed task that has no dependencies.
    record = manifest["tasks"].pop("S9.assemble")
    record.update(type="S1.extract", state="done", index=["p001", "1"])
    manifest["tasks"]["S1.extract-p001-1"] = record
    manifest["status"] = "completed"
    write_manifest(manifest_path, manifest)
    return data_dir, context.run_id


def _claim_task(orchestrator: Orchestrator, run_id: str) -> tuple[str, str, str] | None:
    claimed = orchestrator.claim_next(run_id, executor_id="tester")
    if claimed is None:
        return None
    manifest = orchestrator.load_run(run_id)
    task_id = next(
        key for key, task in manifest["tasks"].items()
        if (task["claim"] or {}).get("ticket") == claimed["ticket"]
    )
    return task_id, claimed["ticket"], claimed["card"]


def _drive(
    orchestrator: Orchestrator,
    run_id: str,
    respond: Callable[[str, str], str],
    *,
    limit: int = 200,
) -> list[tuple[str, str, bool]]:
    """Claim and submit until no LLM task is ready.  Returns (task, card, accepted)."""

    log = []
    for _ in range(limit):
        claim = _claim_task(orchestrator, run_id)
        if claim is None:
            return log
        task_id, ticket, card = claim
        result = orchestrator.submit(ticket, respond(task_id, card))
        log.append((task_id, card, result.accepted))
    raise AssertionError("did not settle")


def _body(task_id: str, orchestrator: Orchestrator, run_id: str, *, scale: int = 25) -> str:
    """A body inside the part's range of characters (10..40 per event)."""

    plan = json.loads(
        (orchestrator.run_dir(run_id) / "tasks" / f"F1.split-{task_id.rsplit('-', 1)[1]}" / "output.json").read_text(encoding="utf-8")
    )
    part = next(item for item in plan["parts"] if f"-{item['id']}-" in task_id)
    return "あ" * (scale * len(part["event_ids"]) - 1) + "。"


def _good_responder(orchestrator: Orchestrator, run_id: str) -> Callable[[str, str], str]:
    def respond(task_id: str, card: str) -> str:
        if task_id.startswith("F4.judge"):
            return '{"answer": "no", "reason": "問題はない。"}'
        return _body(task_id, orchestrator, run_id)
    return respond


def _start(tmp_path: Path, dummy_format: Path, events: int = 5):
    data_dir, run_id = _completed_story_run(tmp_path, events)
    orchestrator = create_story_orchestrator(data_dir)
    formatting.start_formatting(orchestrator, run_id, "dummy", formats_root=dummy_format)
    return data_dir, run_id, orchestrator


# --- schema and profile ------------------------------------------------------

def test_profile_schema_accepts_the_spec_example_and_rejects_deviations() -> None:
    schema = ROOT / "schemas" / "format-profile.schema.json"
    validate_document(PROFILE, schema)
    for bad in (
        {**PROFILE, "extra": 1},
        {**PROFILE, "name": "Novel"},
        {**PROFILE, "unit": "page"},
        {**PROFILE, "unit_mapping": "magic"},
        {**PROFILE, "chars_per_event": [10]},
        {**PROFILE, "rules": []},
        {k: v for k, v in PROFILE.items() if k != "template"},
        {**PROFILE, "checks": {"llm": []}},
        {**PROFILE, "template": "../template.md"},
    ):
        with pytest.raises(SchemaValidationError):
            validate_document(bad, schema)


def test_load_profile_rejects_semantic_errors(
    tmp_path: Path, dummy_format: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert formatting.load_profile("dummy", formats_root=dummy_format)["name"] == "dummy"

    def write(profile: dict[str, Any]) -> None:
        (dummy_format / "dummy" / "profile.yaml").write_text(
            yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8"
        )

    for bad, message in (
        ({**PROFILE, "name": "other"}, "ディレクトリ名"),
        ({**PROFILE, "chars_per_event": [50, 10]}, "下限"),
        ({**PROFILE, "checks": {"code": ["fountain"]}}, "未対応"),
        ({**PROFILE, "template": "missing.md"}, "テンプレート"),
    ):
        write(bad)
        with pytest.raises(ValueError, match=message):
            formatting.load_profile("dummy", formats_root=dummy_format)
    write(PROFILE)
    monkeypatch.delitem(format_units.UNIT_MAPPERS, "scene")
    with pytest.raises(ValueError, match="未対応"):
        formatting.load_profile("dummy", formats_root=dummy_format)
    with pytest.raises(ValueError, match="様式名"):
        formatting.load_profile("../x", formats_root=dummy_format)
    with pytest.raises(ValueError, match="プロファイル"):
        formatting.load_profile("absent", formats_root=dummy_format)


def test_split_counts_is_even_and_bounded() -> None:
    assert formatting.split_counts(5, 2) == [2, 2, 1]
    assert formatting.split_counts(4, 4) == [4]
    assert formatting.split_counts(7, 3) == [3, 2, 2]
    assert all(sum(formatting.split_counts(n, k)) == n for n in range(1, 20) for k in range(1, 6))


# --- st format, F0, F1 -------------------------------------------------------

def test_start_formatting_returns_the_run_to_active(tmp_path: Path, dummy_format: Path) -> None:
    data_dir, run_id, orchestrator = _start(tmp_path, dummy_format)
    manifest = orchestrator.load_run(run_id)
    assert manifest["status"] == "active"
    assert manifest["tasks"]["F0.select-dummy"]["state"] == "ready"
    assert manifest["tasks"]["S1.extract-p001-1"]["state"] == "done"
    with pytest.raises(ValueError, match="作成済み"):
        formatting.start_formatting(orchestrator, run_id, "dummy", formats_root=dummy_format)


def test_start_formatting_rejects_a_second_run_of_the_same_format(tmp_path: Path, dummy_format: Path) -> None:
    data_dir, run_id, orchestrator = _start(tmp_path, dummy_format)
    manifest = orchestrator.load_run(run_id)
    manifest["status"] = "completed"
    for task in manifest["tasks"].values():
        task["state"] = "done"
    write_manifest(orchestrator.run_dir(run_id) / "manifest.json", manifest)
    with pytest.raises(ValueError, match="作成済み"):
        formatting.start_formatting(orchestrator, run_id, "dummy", formats_root=dummy_format)


def test_f0_and_f1_compute_ranges_units_and_parts(tmp_path: Path, dummy_format: Path) -> None:
    data_dir, run_id, orchestrator = _start(tmp_path, dummy_format)
    orchestrator.advance(run_id)
    run_dir = orchestrator.run_dir(run_id)
    f0 = json.loads((run_dir / "tasks" / "F0.select-dummy" / "output.json").read_text(encoding="utf-8"))
    assert f0["event_count"] == 5
    assert f0["total_chars"] == {"min": 50, "max": 200}
    assert f0["template_text"] == TEMPLATE
    plan = json.loads((run_dir / "tasks" / "F1.split-dummy" / "output.json").read_text(encoding="utf-8"))
    # 5 events by three -> units of 3 and 2 events.  90 // 40 = 2 events per task.
    assert [unit["event_ids"] for unit in plan["units"]] == [["e001", "e002", "e003"], ["e004", "e005"]]
    assert [(part["id"], part["event_ids"]) for part in plan["parts"]] == [
        ("u01p1", ["e001", "e002"]), ("u01p2", ["e003"]), ("u02p1", ["e004", "e005"]),
    ]
    first, second, _ = plan["parts"]
    assert (first["min_chars"], first["max_chars"], first["range"]) == (20, 80, "20〜80字")
    assert (second["min_chars"], second["max_chars"]) == (10, 40)
    assert plan["questions"] == PROFILE["checks"]["llm"]
    assert plan["units"][0]["part_ids"] == ["u01p1", "u01p2"]
    assert [event["id"] for event in first["events"]] == ["e001", "e002"]
    assert first["characters"][0]["id"] == "c1" and "主人公" in first["characters"][0]["text"]

    manifest = orchestrator.load_run(run_id)
    tasks = manifest["tasks"]
    assert tasks["F1.split-dummy"]["state"] == "done"
    assert tasks["F2.write-u01p1-dummy"]["state"] == "ready"
    assert tasks["F2.write-u01p2-dummy"]["deps"] == ["F1.split-dummy", "F2.write-u01p1-dummy"]
    assert tasks["F4.judge-u01p1-b1-dummy"]["deps"] == ["F1.split-dummy", "F2.write-u01p1-dummy"]
    assert tasks["F4.verify-u01p1-dummy"]["deps"] == [
        "F1.split-dummy", "F2.write-u01p1-dummy", "F4.judge-u01p1-b1-dummy"
    ]
    assert set(tasks["F3.assemble-dummy"]["deps"]) >= {
        "F0.select-dummy", "F1.split-dummy", "F2.write-u02p1-dummy", "F4.verify-u02p1-dummy"
    }
    assert tasks["F3.assemble-dummy"]["state"] == "blocked"


def test_mapping_must_assign_every_event_once(tmp_path: Path, dummy_format: Path, monkeypatch) -> None:
    monkeypatch.setitem(
        format_units.UNIT_MAPPERS, "scene",
        lambda story, profile, root: [Unit("u01", "一", ("e001",))],
    )
    data_dir, run_id, orchestrator = _start(tmp_path, dummy_format)
    manifest = orchestrator.advance(run_id)
    assert manifest["tasks"]["F1.split-dummy"]["state"] == "failed"
    assert "ちょうど1回" in manifest["tasks"]["F1.split-dummy"]["error"]


def test_per_event_mapping_gives_one_unit_per_event(tmp_path: Path) -> None:
    data_dir, run_id = _completed_story_run(tmp_path, 4)
    story = formatting.read_story(data_dir / "runs" / run_id)
    units = map_per_event(story, PROFILE, ROOT)
    assert [unit.event_ids for unit in units] == [("e001",), ("e002",), ("e003",), ("e004",)]
    assert [unit.id for unit in units] == ["u01", "u02", "u03", "u04"]


# --- F0..F4 end to end -------------------------------------------------------

CARD_FORBIDDEN = re.compile(r"\bF[0-4]\b|F[0-4]\.[a-z]|20\d{6}-\d{6}-[0-9a-f]{6}")


def test_dummy_format_passes_f0_to_f4_and_assembles(tmp_path: Path, dummy_format: Path) -> None:
    data_dir, run_id, orchestrator = _start(tmp_path, dummy_format)
    log = _drive(orchestrator, run_id, _good_responder(orchestrator, run_id))
    manifest = orchestrator.load_run(run_id)
    assert manifest["status"] == "completed"
    assert all(task["state"] == "done" for task in manifest["tasks"].values())
    assert all(accepted for _, _, accepted in log)
    assert sorted(task for task, _, _ in log if task.startswith("F2")) == [
        "F2.write-u01p1-dummy", "F2.write-u01p2-dummy", "F2.write-u02p1-dummy",
    ]
    assert len([task for task, _, _ in log if task.startswith("F4.judge")]) == 3
    for _, card, _ in log:
        assert not CARD_FORBIDDEN.search(card)

    result = json.loads((orchestrator.run_dir(run_id) / "tasks" / "F3.assemble-dummy" / "output.json").read_text(encoding="utf-8"))
    assert result["path"] == "formats/dummy/dummy.md"
    text = (orchestrator.run_dir(run_id) / result["path"]).read_text(encoding="utf-8")
    story = formatting.read_story(orchestrator.run_dir(run_id))
    assert text.startswith(f"# {story['meta']['plot_types'][0]['name']}：カナ\n")
    assert "## 1. 場面1" in text and "## 2. 場面2" in text
    assert text.rstrip().endswith("了 カナ 2")
    # Unit 1 holds the bodies of its two parts, in order, separated by a blank line.
    first = (orchestrator.run_dir(run_id) / "tasks" / "F2.write-u01p1-dummy" / "output.md").read_text(encoding="utf-8")
    second = (orchestrator.run_dir(run_id) / "tasks" / "F2.write-u01p2-dummy" / "output.md").read_text(encoding="utf-8")
    assert f"{first.strip()}\n\n{second.strip()}\n" in text


def test_f2_card_carries_only_its_own_inputs_and_the_previous_tail(tmp_path: Path, dummy_format: Path) -> None:
    data_dir, run_id, orchestrator = _start(tmp_path, dummy_format)
    orchestrator.advance(run_id)
    first = _claim_task(orchestrator, run_id)
    assert first is not None and first[0] == "F2.write-u01p1-dummy"
    assert "直前の本文の末尾（最大600字）" not in first[2]
    assert "[e001]" in first[2] and "[e002]" in first[2] and "[e003]" not in first[2]
    assert "入力の出来事の順序を変えない。" in first[2]
    assert "20〜80字" in first[2]
    body = "ア" * 100 + "い" * 500 + "う" * 79 + "。"
    orchestrator.submit(first[1], body)
    second = _claim_task(orchestrator, run_id)
    assert second is not None and second[0] == "F2.write-u01p2-dummy"
    tail = body[-600:]
    assert tail in second[2] and "ア" * 50 not in second[2]
    assert "[e003]" in second[2] and "[e001]" not in second[2]


def test_f4_code_check_failure_invalidates_f2_with_the_reason(tmp_path: Path, dummy_format: Path) -> None:
    data_dir, run_id, orchestrator = _start(tmp_path, dummy_format)
    good = _good_responder(orchestrator, run_id)
    calls = {"count": 0}

    def respond(task_id: str, card: str) -> str:
        if task_id == "F2.write-u01p1-dummy" and calls["count"] == 0:
            calls["count"] += 1
            return "短い。"
        return good(task_id, card)

    orchestrator.advance(run_id)
    log = _drive(orchestrator, run_id, respond)
    manifest = orchestrator.load_run(run_id)
    f2 = manifest["tasks"]["F2.write-u01p1-dummy"]
    assert f2["invalidations"] == 1 and f2["attempt"] == 1
    assert manifest["status"] == "completed"
    written = [task for task, _, _ in log if task == "F2.write-u01p1-dummy"]
    assert len(written) == 2
    retry_card = [card for task, card, _ in log if task == "F2.write-u01p1-dummy"][1]
    assert "### 不合格の理由" in retry_card and "min_chars" in retry_card
    # Later tasks were reset and ran again after the rewrite.
    assert len([task for task, _, _ in log if task == "F2.write-u02p1-dummy"]) == 1


def test_f4_yes_answer_invalidates_f2(tmp_path: Path, dummy_format: Path) -> None:
    data_dir, run_id, orchestrator = _start(tmp_path, dummy_format)
    good = _good_responder(orchestrator, run_id)
    answered = {"count": 0}

    def respond(task_id: str, card: str) -> str:
        if task_id == "F4.judge-u02p1-b1-dummy" and answered["count"] == 0:
            answered["count"] += 1
            return '{"answer": "yes", "reason": "見知らぬ人物が出る。"}'
        if task_id == "F2.write-u02p1-dummy" and "前回の不合格理由" in card:
            # A rewrite that differs, so the earlier answer is not reused from the cache.
            return "い" * 49 + "。"
        return good(task_id, card)

    log = _drive(orchestrator, run_id, respond)
    manifest = orchestrator.load_run(run_id)
    assert manifest["tasks"]["F2.write-u02p1-dummy"]["invalidations"] == 1
    assert manifest["tasks"]["F2.write-u01p1-dummy"]["invalidations"] == 0
    assert manifest["status"] == "completed"
    retry_card = [card for task, card, _ in log if task == "F2.write-u02p1-dummy"][1]
    assert "見知らぬ人物が出る。" in retry_card and "入力にない人物が登場していますか" in retry_card
    judge_cards = [card for task, card, _ in log if task.startswith("F4.judge")]
    assert all("入力にない人物が登場していますか" in card for card in judge_cards)
    assert all("### 本文" in card and "### 対象の出来事" in card for card in judge_cards)


def test_f2_is_skipped_after_max_invalidations_and_run_continues(tmp_path: Path, dummy_format: Path) -> None:
    # task-model §6.7 / format-pipeline.md「F4 検証」: exceeding max_invalidations
    # follows F2's on_exhausted (skip by default), so the run does not stall.
    data_dir, run_id, orchestrator = _start(tmp_path, dummy_format)
    good = _good_responder(orchestrator, run_id)

    def respond(task_id: str, card: str) -> str:
        if task_id == "F2.write-u01p2-dummy":
            return "短い。"
        return good(task_id, card)

    _drive(orchestrator, run_id, respond)
    manifest = orchestrator.load_run(run_id)
    f2 = manifest["tasks"]["F2.write-u01p2-dummy"]
    assert f2["state"] == "skipped" and f2["invalidations"] == 2
    assert manifest["status"] == "completed"
    assert any("F2.write-u01p2-dummy" in warning and "省略" in warning for warning in manifest["warnings"])
    assemble = manifest["tasks"]["F3.assemble-dummy"]
    assert assemble["state"] == "done"
    text = (orchestrator.run_dir(run_id) / "formats" / "dummy" / "dummy.md").read_text(encoding="utf-8")
    assert "（未定）" in text


def test_two_formats_run_independently_in_one_run(tmp_path: Path, dummy_format: Path) -> None:
    other = {**PROFILE, "name": "other", "chars_per_event": [5, 40], "continuation": False}
    (dummy_format / "other").mkdir()
    (dummy_format / "other" / "profile.yaml").write_text(yaml.safe_dump(other, allow_unicode=True), encoding="utf-8")
    (dummy_format / "other" / "template.md").write_text(TEMPLATE, encoding="utf-8")
    data_dir, run_id, orchestrator = _start(tmp_path, dummy_format)
    _drive(orchestrator, run_id, _good_responder(orchestrator, run_id))
    assert orchestrator.load_run(run_id)["status"] == "completed"
    formatting.start_formatting(orchestrator, run_id, "other", formats_root=dummy_format)
    assert orchestrator.load_run(run_id)["status"] == "active"

    def respond(task_id: str, card: str) -> str:
        if task_id.startswith("F4.judge"):
            return '{"answer": "no", "reason": "問題はない。"}'
        return _body(task_id, orchestrator, run_id)

    log = _drive(orchestrator, run_id, respond)
    manifest = orchestrator.load_run(run_id)
    assert manifest["status"] == "completed"
    # continuation: false in the profile selects the single-shot task type.
    assert manifest["tasks"]["F2.write_once-u01p1-other"]["type"] == "F2.write_once"
    assert manifest["tasks"]["F2.write-u01p1-dummy"]["type"] == "F2.write"
    assert (orchestrator.run_dir(run_id) / "formats" / "other" / "other.md").is_file()
    assert (orchestrator.run_dir(run_id) / "formats" / "dummy" / "dummy.md").is_file()
    assert any(task.startswith("F4.judge") for task, _, _ in log)


# --- template ----------------------------------------------------------------

def test_render_template_requires_one_block_and_known_variables(tmp_path: Path) -> None:
    data_dir, run_id = _completed_story_run(tmp_path, 3)
    story = formatting.read_story(data_dir / "runs" / run_id)
    units = [{"id": "u01", "title": "始まり", "part": None, "body": "本文"}]
    text = formatting.render_template("[{{protagonist}}]{{#units}}<{{unit.part}}|{{unit.body}}>{{/units}}", story, units, "x")
    assert text == "[カナ]<|本文>"
    for bad in ("なし", "{{#units}}{{unknown}}{{/units}}", "{{#units}}a{{/units}}{{#units}}b{{/units}}", "{{#units}}{{x}}{{/units}}"):
        with pytest.raises(ValueError):
            formatting.render_template(bad, story, units, "x")


def test_syntax_checks_are_registered_per_format(tmp_path: Path, dummy_format: Path, monkeypatch) -> None:
    monkeypatch.setitem(
        formatting.SYNTAX_CHECKS, "no_bang",
        lambda body, part: ["感嘆符を含む"] if "!" in body else [],
    )
    part = {"min_chars": 1, "max_chars": 10}
    assert formatting.check_body("あ。", part, ["no_bang"], ROOT) == []
    assert formatting.check_body("あ!。", part, ["no_bang", "max_chars"], ROOT) == ["感嘆符を含む"]
    failures = formatting.check_body("あ" * 11 + "。", part, ["min_chars", "max_chars", "ends_complete"], ROOT)
    assert len(failures) == 1 and "max_chars" in failures[0]


# --- CLI ---------------------------------------------------------------------

def test_st_format_cli(tmp_path: Path, dummy_format: Path, monkeypatch, capsys) -> None:
    data_dir, run_id = _completed_story_run(tmp_path)
    monkeypatch.setenv("STORYTELLER_HOME", str(data_dir))
    assert cli_main(["format", "--run", run_id, "--as", "dummy"]) == 0
    status = json.loads(_status(capsys, run_id))
    assert status["status"] == "active"
    assert any(task["task_id"] == "F0.select-dummy" for task in status["tasks"])
    # Not completed any more, an unknown run, an unknown format and a missing option.
    assert cli_main(["format", "--run", run_id, "--as", "dummy"]) == 1  # already started
    assert cli_main(["format", "--run", "20260101-000000-abcdef", "--as", "dummy"]) == 1
    assert cli_main(["format", "--run", run_id, "--as", "absent"]) == 1
    assert cli_main(["format", "--run", run_id]) == 1


def test_st_format_rejects_unknown_format_without_changing_the_run(tmp_path: Path, dummy_format: Path, monkeypatch) -> None:
    data_dir, run_id = _completed_story_run(tmp_path)
    monkeypatch.setenv("STORYTELLER_HOME", str(data_dir))
    assert cli_main(["format", "--run", run_id, "--as", "absent"]) == 1
    manifest = create_story_orchestrator(data_dir).load_run(run_id)
    assert manifest["status"] == "completed" and "F0.select-absent" not in manifest["tasks"]


def test_add_tasks_to_completed_run_rejects_other_statuses(tmp_path: Path, dummy_format: Path) -> None:
    data_dir, run_id, orchestrator = _start(tmp_path, dummy_format)
    manifest = orchestrator.load_run(run_id)
    manifest["status"] = "stalled"
    write_manifest(orchestrator.run_dir(run_id) / "manifest.json", manifest)
    with pytest.raises(InvalidTransition):
        orchestrator.add_tasks_to_completed_run(run_id, [TaskSpec("F0.select-x", "F0.select", index=("x",))])


def _status(capsys, run_id: str) -> str:
    capsys.readouterr()
    assert cli_main(["status", "--run", run_id, "--json"]) == 0
    return capsys.readouterr().out


def test_format_definitions_are_checked_by_the_harness_tool() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_harness", ROOT / "tools" / "check_harness.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.check_harness() == []
    definitions = formatting.load_format_task_definitions(ROOT)
    assert set(definitions) == {
        "F0.select", "F1.split", "F2.write", "F2.write_once", "F3.assemble", "F4.judge", "F4.verify"
    }
    once = dict(definitions["F2.write_once"])
    regular = dict(definitions["F2.write"])
    assert (regular.pop("continuation"), once.pop("continuation")) == (True, False)
    for definition in (regular, once):
        definition["id"] = "x"
        definition["inputs"] = {
            name: {**slot, "from": slot["from"].replace("_once", "")}
            for name, slot in definition["inputs"].items()
        }
    assert regular == once


def test_second_format_can_start_while_the_first_is_running(tmp_path: Path, dummy_format: Path) -> None:
    other = {**PROFILE, "name": "other", "chars_per_event": [5, 40]}
    (dummy_format / "other").mkdir()
    (dummy_format / "other" / "profile.yaml").write_text(yaml.safe_dump(other, allow_unicode=True), encoding="utf-8")
    (dummy_format / "other" / "template.md").write_text(TEMPLATE, encoding="utf-8")
    data_dir, run_id, orchestrator = _start(tmp_path, dummy_format)
    assert orchestrator.load_run(run_id)["status"] == "active"
    formatting.start_formatting(orchestrator, run_id, "other", formats_root=dummy_format)
    _drive(orchestrator, run_id, _good_responder(orchestrator, run_id))
    manifest = orchestrator.load_run(run_id)
    assert manifest["status"] == "completed"
    for name in ("dummy", "other"):
        assert (orchestrator.run_dir(run_id) / "formats" / name / f"{name}.md").is_file()


def test_unfinished_story_stalled_and_empty_units_are_rejected(tmp_path: Path, dummy_format: Path, monkeypatch) -> None:
    data_dir, run_id, orchestrator = _start(tmp_path, dummy_format)
    manifest = orchestrator.load_run(run_id)
    manifest["tasks"]["S1.extract-p001-1"]["state"] = "ready"
    write_manifest(orchestrator.run_dir(run_id) / "manifest.json", manifest)
    with pytest.raises(ValueError, match="終わっていない"):
        formatting.start_formatting(orchestrator, run_id, "dummy", formats_root=dummy_format)
    manifest["status"] = "stalled"
    write_manifest(orchestrator.run_dir(run_id) / "manifest.json", manifest)
    with pytest.raises(ValueError, match="completed または active"):
        formatting.start_formatting(orchestrator, run_id, "dummy", formats_root=dummy_format)
    story = formatting.read_story(orchestrator.run_dir(run_id))
    monkeypatch.setitem(format_units.UNIT_MAPPERS, "scene", lambda s, p, r: [Unit("u01", "x", ())])
    with pytest.raises(ValueError, match="出来事のない"):
        formatting.build_plan(story, PROFILE, ROOT)


@pytest.mark.parametrize("continuation", [True, False])
@pytest.mark.parametrize("bad", ["短い。", "あ" * 150 + "。"])
def test_f2_length_failure_is_repaired_or_skipped(tmp_path, dummy_format, continuation, bad):
    from storyteller.repair import REPAIR_ROLE
    profile = {**PROFILE, "continuation": continuation}
    (dummy_format / "dummy" / "profile.yaml").write_text(
        yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    _, run_id, harness = _start(tmp_path, dummy_format)
    good = _good_responder(harness, run_id)
    seen = set()
    repaired = []

    def respond(task_id, card):
        if task_id.startswith("F2.") and "-u01p1-" in task_id:
            if task_id not in seen:
                seen.add(task_id)
                return bad
            assert REPAIR_ROLE in card
            assert "書き足す" in card and "短くする" in card
            repaired.append(task_id)
        return good(task_id, card)

    _drive(harness, run_id, respond)
    assert repaired and harness.load_run(run_id)["status"] == "completed"

    # Exhausting attempts also completes both continuation variants.
    _, run_id, harness = _start(tmp_path / "exhaust", dummy_format)
    good = _good_responder(harness, run_id)
    _drive(harness, run_id, lambda task_id, card: bad if task_id.startswith("F2.") else good(task_id, card))
    manifest = harness.load_run(run_id)
    assert manifest["status"] == "completed"
    skipped = [key for key, task in manifest["tasks"].items() if task["state"] == "skipped"]
    assert skipped and all(key.startswith("F2.") for key in skipped)
    assert all((harness.task_dir(run_id, key) / "failure.md").exists() for key in skipped)


@pytest.mark.parametrize("truncated", [False, True])
def test_f2_repairs_real_1029_character_shortfall(tmp_path, dummy_format, truncated):
    profile = {**PROFILE, "chars_per_event": [1500, 3000], "max_chars_per_task": 6000}
    (dummy_format / "dummy" / "profile.yaml").write_text(
        yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    _, run_id, harness = _start(tmp_path, dummy_format, events=1)
    claim = _claim_task(harness, run_id)
    task_id, ticket, _ = claim
    assert task_id.startswith("F2.write-")
    if truncated:
        assert harness.submit(ticket, "い" * 500, truncated=True).accepted
        _, ticket, _ = _claim_task(harness, run_id)
    result = harness.submit(ticket, "あ" * (528 if truncated else 1028) + "。")
    assert result.accepted
    # F4 code validation invalidates the accepted body before the next claim.
    claim = _claim_task(harness, run_id)
    if claim[0].startswith("F4.judge"):
        harness.submit(claim[1], '{"answer":"no","reason":"問題なし。"}')
        claim = _claim_task(harness, run_id)
    assert "min_chars: 文字数 1029 が 1500以上ではありません" in claim[2]
    assert "1500字以上" in claim[2] and "書き足す" in claim[2]
    assert harness.submit(claim[1], "あ" * 1499 + "。").accepted
    _drive(harness, run_id, _good_responder(harness, run_id))
    assert harness.load_run(run_id)["status"] == "completed"


@pytest.mark.parametrize("continuation", [True, False])
def test_f2_submission_exhaustion_completes_format(tmp_path, dummy_format, continuation):
    profile = {**PROFILE, "continuation": continuation}
    (dummy_format / "dummy" / "profile.yaml").write_text(
        yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    _, run_id, harness = _start(tmp_path, dummy_format)
    good = _good_responder(harness, run_id)
    _drive(harness, run_id, lambda task_id, card: {} if task_id.startswith("F2.") else good(task_id, card))
    manifest = harness.load_run(run_id)
    assert manifest["status"] == "completed"
    assert all(task["state"] == "skipped" and task["tries"] == 5
               for task_id, task in manifest["tasks"].items() if task_id.startswith("F2."))
    body = (harness.run_dir(run_id) / "formats/dummy/dummy.md").read_text(encoding="utf-8")
    assert "（未定）" in body
