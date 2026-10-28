"""P3-11: the novel F2 rejects a repeated long narration sentence at submission.

format-pipeline §4.5 (submit_checks, F1.split v5, F2.write / F2.write_once v4),
task-model §6.2 (no_repeated_sentence: exclude_quoted, excerpt) and §8.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

import storyteller.formatting as formatting
from storyteller.cache import cache_path
from storyteller.new_run import create_story_orchestrator
from storyteller.repair import REPAIR_ROLE
from storyteller.validation import (
    SchemaValidationError, ValidationConfigurationError, validate_document, validate_output,
)
from tests.test_formatting import (
    PROFILE, ROOT, _claim_task, _completed_story_run, _drive, _good_responder, dummy_format,
)

CHECK = "no_repeated_sentence"
NOVEL_CHECKS = [{CHECK: {"exclude_quoted": True, "excerpt": True}}]
OLD_REASON = "no_repeated_sentence: 同じ20文字以上の文を本文内で繰り返さないでください"
LINE = "港の灯りが静かに揺れて夜がゆっくり更けていった。"  # 23 characters + 。
REPEATED = LINE + LINE
REFRAIN = f"「{LINE}」と彼は言った。「{LINE}」"
GOOD = "港の灯りが揺れていた。舟は朝を待って静かに岸を離れた。"
CONDITION = "同じ長い文を繰り返さず、担当部分を重複のない文で書く。"


def _check(text: str, **arguments: bool):
    check = {CHECK: arguments} if arguments else CHECK
    return validate_output({"output": "text", "validate": {"checks": [check]}}, text)


# --- no_repeated_sentence: exclude_quoted and excerpt --------------------------

def test_without_arguments_the_reason_is_unchanged():
    result = _check(REPEATED)
    assert not result.passed and result.repairable
    assert list(result.errors) == [OLD_REASON]
    # Quoted refrains still count without exclude_quoted (S7.detail is unchanged).
    assert not _check(REFRAIN).passed


def test_exclude_quoted_ignores_dialogue_refrains_but_not_narration():
    assert _check(REFRAIN, exclude_quoted=True).passed
    assert _check(f"『{LINE}』{LINE}", exclude_quoted=True).passed
    assert not _check(f"「おはよう」{LINE}{LINE}", exclude_quoted=True).passed


def test_exclude_quoted_removes_nested_pairs_and_keeps_unmatched_brackets():
    nested = f"「外『{LINE}』外」{LINE}"
    assert _check(nested, exclude_quoted=True).passed
    # A closing bracket of another kind does not close 「, so nothing is removed.
    assert not _check(f"{LINE}\n「\n{LINE}\n』", exclude_quoted=True).passed
    assert _check(f"{LINE}\n「\n{LINE}\n」", exclude_quoted=True).passed
    # An unmatched opening bracket stays as a character.
    assert not _check(f"{LINE}\n「\n{LINE}", exclude_quoted=True).passed
    # Inside a matched pair, a closing bracket of another kind is removed with it.
    assert _check(f"「』{LINE}」{LINE}", exclude_quoted=True).passed
    # A stray closing bracket stays as a character, too.
    assert not _check(f"{LINE}\n」\n{LINE}", exclude_quoted=True).passed
    assert _check(f"」{LINE}\n{LINE}", exclude_quoted=True).passed  # kept, so the sentences differ


def test_excerpt_is_the_second_sentence_nfc_trimmed_without_nfkc_or_space_removal():
    first = "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴ"
    second = "  A B C D E F G H I J K L M N O P Q R S T  "
    result = _check(first + "。" + second + "\n", excerpt=True)
    assert list(result.errors) == [
        OLD_REASON + "。重複した文：A B C D E F G H I J K L M N O P Q R S T"
    ]
    decomposed = "が" + "あ" * 20  # NFD が -> NFC が
    result = _check(decomposed + "。" + decomposed + "。", excerpt=True)
    assert result.errors[0].endswith("重複した文：が" + "あ" * 20)


def test_excerpt_is_cut_at_120_codepoints_and_semicolons_are_replaced():
    long = "；" + "長" * 130
    result = _check(long + "。" + long + "。", excerpt=True)
    excerpt = result.errors[0].split("重複した文：", 1)[1]
    assert excerpt == ";" + "長" * 119 + "…"
    exact = "長" * 120
    assert _check(exact + "。" + exact, excerpt=True).errors[0].endswith("：" + exact)


def test_excerpt_with_exclude_quoted_has_no_brackets():
    text = f"{LINE}「台詞」{LINE.rstrip('。')}「別」。"
    result = _check(text, exclude_quoted=True, excerpt=True)
    assert result.errors[0].endswith("重複した文：" + LINE.rstrip("。"))


def test_wrong_arguments_are_configuration_errors():
    for arguments in ({"n": 20}, {"excerpt": "yes"}, {"exclude_quoted": 1}):
        with pytest.raises(ValidationConfigurationError):
            validate_output({"output": "text", "validate": {"checks": [{CHECK: arguments}]}}, LINE)


# --- schemas and profiles ----------------------------------------------------

def test_profile_schema_allows_only_no_repeated_sentence_in_submit_checks():
    schema = ROOT / "schemas/format-profile.schema.json"
    for good in ([], [CHECK], NOVEL_CHECKS, [{CHECK: {}}]):
        validate_document({**PROFILE, "submit_checks": good}, schema)
    for bad in (
        ["max_chars"], ["no_story_metadata"], [{"no_story_metadata": {}}],
        [{CHECK: {"n": 20}}], [{CHECK: {"excerpt": "yes"}}], [{CHECK: {}, "max_chars": {"n": 1}}],
        CHECK,
    ):
        with pytest.raises(SchemaValidationError):
            validate_document({**PROFILE, "submit_checks": bad}, schema)


def test_task_definition_schema_accepts_the_new_arguments():
    definition = yaml.safe_load((ROOT / "harness/story/tasks/S7.detail.yaml").read_text(encoding="utf-8"))
    schema = ROOT / "schemas/task-definition.schema.json"
    definition["validate"]["checks"] = [{CHECK: {"exclude_quoted": True, "excerpt": False}}]
    validate_document(definition, schema)
    definition["validate"]["checks"] = [{CHECK: {"excerpt": "yes"}}]
    with pytest.raises(SchemaValidationError):
        validate_document(definition, schema)


def test_only_the_novel_profile_has_submit_checks_and_f4_code_checks_are_unchanged():
    novel = formatting.load_profile("novel")
    assert novel["submit_checks"] == NOVEL_CHECKS
    assert novel["checks"]["code"] == ["min_chars", "max_chars", "ends_complete"]
    for name in ("screenplay", "slides", "visual-prompts", "reference-book"):
        assert "submit_checks" not in formatting.load_profile(name)


# --- integration with the dummy format -------------------------------------

def _setup(tmp_path, dummy_format, *, checks=NOVEL_CHECKS, continuation=True, f1=None, f2=None):
    profile = deepcopy(PROFILE)
    profile["continuation"] = continuation
    if checks is not None:
        profile["submit_checks"] = checks
    (dummy_format / "dummy/profile.yaml").write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    data, run = _completed_story_run(tmp_path, events=5)
    h = create_story_orchestrator(data)
    write = "F2.write" if continuation else "F2.write_once"
    if f1 is not None:
        h.task_definitions["F1.split"]["version"] = f1
    if f2 is not None:
        h.task_definitions[write]["version"] = f2
    formatting.start_formatting(h, run, "dummy", formats_root=dummy_format)
    h.advance(run)
    return h, run, f"{write}-u01p1-dummy"


def _plan(h, run):
    return json.loads((h.task_dir(run, "F1.split-dummy") / "output.json").read_text(encoding="utf-8"))


def _attempts(h, run, task_id):
    directory = h.task_dir(run, task_id) / "attempts"
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


def test_definition_versions():
    from storyteller.formatting import load_format_task_definitions
    definitions = load_format_task_definitions(ROOT)
    assert definitions["F1.split"]["version"] == 7
    assert definitions["F2.write"]["version"] == 5
    assert definitions["F2.write_once"]["version"] == 5
    for name in ("F2.write", "F2.write_once"):
        assert definitions[name]["validate"]["checks"] == ["no_story_metadata"]


def test_f1_split_v5_copies_submit_checks_and_an_empty_array_without_them(tmp_path, dummy_format):
    h, run, _ = _setup(tmp_path, dummy_format)
    assert _plan(h, run)["submit_checks"] == NOVEL_CHECKS
    h, run, _ = _setup(tmp_path / "none", dummy_format, checks=None)
    assert _plan(h, run)["submit_checks"] == []
    h, run, _ = _setup(tmp_path / "v4", dummy_format, f1=4)
    assert "submit_checks" not in _plan(h, run)


@pytest.mark.parametrize("continuation", [True, False])
def test_narration_repeat_is_rejected_then_repaired_and_dialogue_refrain_passes(
    tmp_path, dummy_format, continuation
):
    h, run, target = _setup(tmp_path, dummy_format, continuation=continuation)
    good = _good_responder(h, run)
    cards = {}

    def respond(task_id, card):
        if task_id == target:
            cards.setdefault(task_id, []).append(card)
            if len(cards[task_id]) == 1:
                assert CHECK not in card and "重複した文" not in card  # not on the normal card
                return REPEATED
            assert REPAIR_ROLE in card
            return GOOD
        if task_id.startswith("F2.") and "-u02p1-" in task_id:
            return REFRAIN
        return good(task_id, card)

    log = _drive(h, run, respond)
    manifest = h.load_run(run)
    assert manifest["status"] == "completed"
    first, repair = cards[target][:2]
    record = json.loads(_attempts(h, run, target)[0].read_text(encoding="utf-8"))
    assert record["output"] == REPEATED and record["repairable"] is True
    assert record["errors"] == [OLD_REASON + "。重複した文：" + LINE.rstrip("。")]
    # Conditions: the plan's min/max/ends_complete first, then submit_checks.
    conditions = repair.split("### 満たすべき条件\n", 1)[1].split("\n\n", 1)[0].split("\n")
    conditions = [line for line in conditions if line.startswith("- ")]
    assert [line[2:] for line in conditions][-1] == CONDITION
    assert "字以上にする" in conditions[0] and "字以内にする" in conditions[1]
    assert "文末" in conditions[2] and len(conditions) == 4
    assert REPEATED in repair and "重複した文：" in repair
    # The dialogue refrain was accepted on its first submission.
    refrain_task = target.replace("u01p1", "u02p1")
    assert [accepted for task_id, _, accepted in log if task_id == refrain_task][0]
    assert (h.task_dir(run, refrain_task) / "output.md").read_text(encoding="utf-8") == REFRAIN


def test_failure_after_continuation_discards_partial_and_repairs_the_whole_body(tmp_path, dummy_format):
    h, run, target = _setup(tmp_path, dummy_format)
    task_id, ticket, _ = _claim_task(h, run)
    assert task_id == target
    assert h.submit(ticket, LINE, truncated=True).accepted
    task_dir = h.task_dir(run, target)
    assert (task_dir / "partial.md").read_text(encoding="utf-8") == LINE
    assert h.load_run(run)["tasks"][target]["continuation_step"] == 1
    _, ticket, card = _claim_task(h, run)
    final = "朝になると舟は岸を離れていった。" + LINE
    result = h.submit(ticket, final)
    assert not result.accepted
    task = h.load_run(run)["tasks"][target]
    assert task["continuation_step"] == 0 and not (task_dir / "partial.md").exists()
    record = json.loads(_attempts(h, run, target)[-1].read_text(encoding="utf-8"))
    assert record["output"] == LINE + final and record["repairable"] is True
    claimed, ticket, card = _claim_task(h, run)
    assert claimed == target and REPAIR_ROLE in card
    assert "### 元の出力\n" + LINE + final + "\n" in card
    assert "これまでの出力の末尾" not in card
    assert h.submit(ticket, GOOD).accepted
    _drive(h, run, _good_responder(h, run))
    assert h.load_run(run)["status"] == "completed"


def test_old_f2_version_keeps_the_partial_and_does_not_add_submit_checks(tmp_path, dummy_format):
    h, run, target = _setup(tmp_path, dummy_format, f2=3)
    _, ticket, _ = _claim_task(h, run)
    assert h.submit(ticket, LINE, truncated=True).accepted
    _, ticket, _ = _claim_task(h, run)
    # The same repeated narration is accepted by the old version.
    assert h.submit(ticket, "朝になると舟は岸を離れていった。" + LINE).accepted


@pytest.mark.parametrize("f2,kept", [(3, True), (4, False)])
def test_other_failure_after_continuation_discards_partial_only_from_v4(tmp_path, dummy_format, f2, kept):
    h, run, target = _setup(tmp_path, dummy_format, f2=f2)
    _, ticket, _ = _claim_task(h, run)
    assert h.submit(ticket, LINE, truncated=True).accepted
    _, ticket, _ = _claim_task(h, run)
    result = h.submit(ticket, "e001 が岸を離れた。")
    assert not result.accepted and result.errors[0].startswith("no_story_metadata:")
    task = h.load_run(run)["tasks"][target]
    assert (h.task_dir(run, target) / "partial.md").exists() is kept
    assert task["continuation_step"] == (1 if kept else 0)
    record = json.loads(_attempts(h, run, target)[-1].read_text(encoding="utf-8"))
    assert record["output"] == LINE + "e001 が岸を離れた。"


@pytest.mark.parametrize("continuation", [True, False])
def test_old_f1_output_without_submit_checks_accepts_the_repeat(tmp_path, dummy_format, continuation):
    h, run, target = _setup(tmp_path, dummy_format, continuation=continuation, f1=4)
    task_id, ticket, _ = _claim_task(h, run)
    assert task_id == target and h.submit(ticket, REPEATED).accepted


@pytest.mark.parametrize("continuation", [True, False])
def test_cache_hit_failing_submit_checks_is_a_miss_without_using_an_attempt(
    tmp_path, dummy_format, continuation
):
    h, run, target = _setup(tmp_path, dummy_format, continuation=continuation)
    task = h.load_run(run)["tasks"][target]
    assert task["state"] == "ready" and isinstance(task["cache_key"], str)
    path = cache_path(h.data_dir, task["cache_key"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(REPEATED, ensure_ascii=False), encoding="utf-8")
    h.advance(run)
    task = h.load_run(run)["tasks"][target]
    assert task["state"] == "ready"
    assert task["tries"] == 0 and task["attempt"] == 0 and task["error"] is None
    assert not _attempts(h, run, target)
    claimed, ticket, card = _claim_task(h, run)
    assert claimed == target and REPAIR_ROLE not in card
    # A cached body that passes is reused as before.
    h2, run2, target2 = _setup(tmp_path / "pass", dummy_format, continuation=continuation)
    key = h2.load_run(run2)["tasks"][target2]["cache_key"]
    path = cache_path(h2.data_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(REFRAIN, ensure_ascii=False), encoding="utf-8")
    h2.advance(run2)
    task = h2.load_run(run2)["tasks"][target2]
    assert task["state"] == "done" and task["tries"] == 0


def test_screenplay_and_slides_plans_carry_an_empty_submit_checks(tmp_path):
    data, run = _completed_story_run(tmp_path)
    h = create_story_orchestrator(data)
    for name in ("screenplay", "slides"):
        assert "submit_checks" not in formatting.load_profile(name)
        formatting.start_formatting(h, run, name)
        h.advance(run)
        assert h.load_run(run)["tasks"][f"F1.split-{name}"]["state"] == "done"
        plan = json.loads((h.task_dir(run, f"F1.split-{name}") / "output.json").read_text(encoding="utf-8"))
        assert plan["submit_checks"] == []
