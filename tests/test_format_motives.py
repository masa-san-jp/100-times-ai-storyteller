"""P3-10: the canonical motives are passed from F1.split v4 to F2 v3."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

import storyteller.formatting as formatting
from storyteller.cache import lookup_cache
from storyteller.cards import InputBudgetError, input_char_count, prepare_task_inputs
from storyteller.new_run import create_story_orchestrator
from tests.test_format_novel import _event as _novel_event, _story as _novel_story, _thread
from tests.test_formatting import (
    PROFILE, ROOT, _completed_story_run, _drive, _good_responder, dummy_format,
)
from tests.test_reference_book import _story_with_world

MOTIVE_RULE = "登場人物の動機は、その人物の行動・台詞・内面で示す。動機を示すために、入力にない出来事を加えない。"
KANA = "「約束」を果たし、  道を確かめたい。\n港へは戻らない。"
MIO = "港を守りたい。"
SORA = "（未定）の地図を見つけたい。"
LABEL = "### 登場人物の動機"


def _add_cast(story: dict) -> None:
    """c1 カナ（動機あり）, c2 ハル（（未定））, c3 ミオ, c4 ソラ（（未定）を含むだけ）."""

    base = story["cast"][0]
    base["motive"] = KANA
    for person_id, role, name, motive in (
        ("c2", "supporter", "ハル", "（未定）"),
        ("c3", "adversary", "ミオ", MIO),
        ("c4", "bystander", "ソラ", SORA),
    ):
        person = deepcopy(base)
        person.update(id=person_id, role=role, name=name, reading=name, motive=motive)
        person["elements"].pop("taboo", None)
        story["cast"].append(person)


# e001: ミオ・カナ / e002: ハル -> part u01p1 = c1, c2, c3
# e003: ソラ・カナ -> part u01p2 = c1, c4
# e004, e005: ハル -> part u02p1 = c2 only (empty motives)
WHO = {"e001": ["c3", "c1"], "e002": ["c2"], "e003": ["c4", "c1"], "e004": ["c2"], "e005": ["c2"]}


def setup(tmp_path, dummy_format, version=4, continuation=True):
    profile = deepcopy(PROFILE)
    profile["continuation"] = continuation
    (dummy_format / "dummy/profile.yaml").write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    data, run = _completed_story_run(tmp_path, events=5)
    story_path = data / "runs" / run / "story/story.json"
    story = json.loads(story_path.read_text(encoding="utf-8"))
    _add_cast(story)
    for event in story["events"]:
        event["who"] = WHO[event["id"]]
    story_path.write_text(json.dumps(story, ensure_ascii=False), encoding="utf-8")
    h = create_story_orchestrator(data)
    h.task_definitions["F1.split"]["version"] = version
    formatting.start_formatting(h, run, "dummy", formats_root=dummy_format)
    h.advance(run)
    formatting.read_story(h.run_dir(run))  # still a valid canonical story
    return h, run


def plan_of(h, run, name="dummy"):
    return json.loads((h.task_dir(run, f"F1.split-{name}") / "output.json").read_text(encoding="utf-8"))


def cards_by_task(h, run):
    log = _drive(h, run, _good_responder(h, run))
    assert h.load_run(run)["status"] == "completed"
    assert all(accepted for _, _, accepted in log)
    return {task: card for task, card, _ in log}


# --- (1) (2) F1.split v4 -----------------------------------------------------

def test_v4_split_copies_only_the_parts_characters_motives_verbatim(tmp_path, dummy_format):
    h, run = setup(tmp_path, dummy_format)
    parts = {part["id"]: part for part in plan_of(h, run)["parts"]}
    first = parts["u01p1"]
    assert [c["id"] for c in first["characters"]] == ["c1", "c2", "c3"]
    # Same order as characters, （未定） left out, the text unchanged.
    assert first["motives"] == [{"name": "カナ", "motive": KANA}, {"name": "ミオ", "motive": MIO}]
    # Only this part's people: ソラ is in u01p2, not u01p1.
    assert parts["u01p2"]["motives"] == [{"name": "カナ", "motive": KANA}, {"name": "ソラ", "motive": SORA}]


def test_undecided_motive_is_excluded_and_a_part_without_motives_has_an_empty_array(tmp_path, dummy_format):
    h, run = setup(tmp_path, dummy_format)
    parts = {part["id"]: part for part in plan_of(h, run)["parts"]}
    assert [c["id"] for c in parts["u02p1"]["characters"]] == ["c2"]
    assert parts["u02p1"]["motives"] == []
    assert all(entry["motive"] != "（未定）" for part in parts.values() for entry in part["motives"])
    # A motive that merely contains （未定） is not an exact match and is kept.
    assert {"name": "ソラ", "motive": SORA} in parts["u01p2"]["motives"]


# --- F2 v3 card -------------------------------------------------------------

@pytest.mark.parametrize("continuation", [True, False])
def test_f2_card_shows_one_name_motive_line_per_person_and_hides_an_empty_array(tmp_path, dummy_format, continuation):
    h, run = setup(tmp_path, dummy_format, continuation=continuation)
    write = "F2.write" if continuation else "F2.write_once"
    assert h.task_definitions[write]["version"] == 3
    assert list(h.task_definitions[write]["inputs"])[:3] == ["events", "characters", "motives"]
    cards = cards_by_task(h, run)
    first = cards[f"{write}-u01p1-dummy"]
    block = f"{LABEL}\nカナ：{KANA}\nミオ：{MIO}\n### 書き方の規則"
    assert block in first
    assert first.index("### 登場人物\n") < first.index(LABEL) < first.index("### 書き方の規則")
    assert '"motive"' not in first and "{" not in first.split(LABEL)[1].split("###")[0]
    assert f"{LABEL}\nカナ：{KANA}\nソラ：{SORA}\n###" in cards[f"{write}-u01p2-dummy"]
    empty = cards[f"{write}-u02p1-dummy"]
    assert "登場人物の動機" not in empty and "ハル" in empty
    # The steps of the card are not changed.
    steps = h.task_definitions[write]["card"]["steps"]
    for card in cards.values():
        if card.startswith("# タスク") and "## 手順" in card and "書き起こす" in card:
            assert all(step in card for step in steps)
    inputs = json.loads((h.task_dir(run, f"{write}-u01p1-dummy") / "input.json").read_text(encoding="utf-8"))
    assert inputs["motives"] == [{"name": "カナ", "motive": KANA}, {"name": "ミオ", "motive": MIO}]
    empty_inputs = json.loads((h.task_dir(run, f"{write}-u02p1-dummy") / "input.json").read_text(encoding="utf-8"))
    assert "motives" not in empty_inputs


# --- (3) budget ---------------------------------------------------------------

def _budget_inputs():
    return {
        "events": [{"id": "e001", "text": "いつ：朝／行動：歩いた"}],
        "characters": [{"id": f"c{n}", "text": f"人物{n}" + "あ" * 200} for n in range(1, 6)],
        "motives": [{"name": "カナ", "motive": "う" * 120}, {"name": "ミオ", "motive": "え" * 120}],
        "rules": ["入力の出来事の順序を変えない。", MOTIVE_RULE],
        "range": "10〜40字",
        "previous_unit": "い" * 600,
        "conditions": ["鐘が鳴る時だけ門を開く。"],
    }


@pytest.mark.parametrize("write", ["F2.write", "F2.write_once"])
def test_over_budget_shrinks_previous_unit_then_characters_and_keeps_motives(write):
    definition = formatting.load_format_task_definitions(ROOT)[write]
    inputs = _budget_inputs()
    full = input_char_count(definition, inputs)
    # Only the previous tail has to give way.
    fitted = prepare_task_inputs(dict(definition, max_input_chars=full - 100), inputs=deepcopy(inputs))
    assert 0 < len(fitted["previous_unit"]) < 600
    assert fitted["characters"] == inputs["characters"]
    assert fitted["motives"] == inputs["motives"] and fitted["conditions"] == inputs["conditions"]
    # Then the characters are cut from the end; motives and conditions stay whole.
    without_previous = input_char_count(definition, {**inputs, "previous_unit": ""})
    fitted = prepare_task_inputs(dict(definition, max_input_chars=without_previous - 300), inputs=deepcopy(inputs))
    assert fitted["previous_unit"] == ""
    assert 0 < len(fitted["characters"]) < len(inputs["characters"])
    assert fitted["characters"] == inputs["characters"][:len(fitted["characters"])]
    assert fitted["motives"] == inputs["motives"] and fitted["conditions"] == inputs["conditions"]
    # Motives are never truncated: if nothing else is left to cut, it fails.
    minimal = input_char_count(definition, {**inputs, "previous_unit": "", "characters": []})
    with pytest.raises(InputBudgetError, match="入力が予算を超える"):
        prepare_task_inputs(dict(definition, max_input_chars=minimal - 1), inputs=deepcopy(inputs))


# --- (4) old F1 output and the formats without motives -----------------------

def test_legacy_f1_output_has_no_motives_and_the_f2_card_is_unchanged(tmp_path, dummy_format):
    h, run = setup(tmp_path, dummy_format, version=3)
    assert all("motives" not in part for part in plan_of(h, run)["parts"])
    cards = cards_by_task(h, run)
    f2 = [card for task, card in cards.items() if task.startswith("F2.")]
    assert len(f2) == 3 and all("登場人物の動機" not in card for card in f2)
    inputs = json.loads((h.task_dir(run, "F2.write-u01p1-dummy") / "input.json").read_text(encoding="utf-8"))
    assert "motives" not in inputs


def test_finished_v3_split_is_not_reexpanded_by_the_v4_definition(tmp_path, dummy_format):
    h, run = setup(tmp_path, dummy_format, version=3)
    before = (plan_of(h, run), {name: task["deps"] for name, task in h.load_run(run)["tasks"].items()})
    h.task_definitions["F1.split"]["version"] = 4
    h.advance(run)
    after = (plan_of(h, run), {name: task["deps"] for name, task in h.load_run(run)["tasks"].items()})
    assert before == after


def test_visual_prompts_and_reference_book_parts_have_no_motives_key(tmp_path):
    data, run = _completed_story_run(tmp_path)
    h = create_story_orchestrator(data)
    assert h.task_definitions["F1.split"]["version"] == 4
    _story_with_world(h.run_dir(run))
    for name in ("visual-prompts", "reference-book"):
        formatting.start_formatting(h, run, name)
        h.advance(run)
        assert h.load_run(run)["tasks"][f"F1.split-{name}"]["state"] == "done"
        parts = plan_of(h, run, name)["parts"]
        assert parts and all("motives" not in part for part in parts)


# --- novel, screenplay and slides ------------------------------------------

@pytest.mark.parametrize("name", ["novel", "screenplay", "slides"])
def test_event_telling_profiles_end_rules_with_the_motive_rule_and_carry_motives(name):
    profile = formatting.load_profile(name)
    assert profile["rules"][-1] == MOTIVE_RULE
    assert profile["rules"].count(MOTIVE_RULE) == 1
    events = [_novel_event(n, "t001", "setup" if n == 1 else "turn") for n in range(1, 4)]
    story = _novel_story([_thread(1, "main", "three-beat")], events)
    story["cast"][0]["motive"] = KANA
    plan = formatting.build_plan(story, profile, ROOT, motives=True)
    assert plan["rules"][-1] == MOTIVE_RULE
    assert all(part["motives"] == [{"name": "カナ", "motive": KANA}] for part in plan["parts"])
    assert all("motives" not in part for part in formatting.build_plan(story, profile, ROOT)["parts"])


@pytest.mark.parametrize("name", ["visual-prompts", "reference-book"])
def test_other_profiles_keep_their_rules(name):
    assert MOTIVE_RULE not in formatting.load_profile(name)["rules"]


# --- (5) cache --------------------------------------------------------------

@pytest.mark.parametrize("continuation", [True, False])
def test_f2_v3_cache_key_is_separate_from_the_old_version(tmp_path, dummy_format, continuation):
    h, run = setup(tmp_path, dummy_format, version=3, continuation=continuation)
    cards_by_task(h, run)
    write = "F2.write" if continuation else "F2.write_once"
    task_id = f"{write}-u01p1-dummy"
    manifest = h.load_run(run)
    record = manifest["tasks"][task_id]
    # Same card inputs as an old run (an old F1 output has no motives).
    inputs = json.loads((h.task_dir(run, task_id) / "input.json").read_text(encoding="utf-8"))
    assert h._cache_key_for_task(manifest, task_id, inputs) == record["cache_key"]
    hit, _ = lookup_cache(h.data_dir, record["cache_key"])
    assert hit
    h.task_definitions[write] = dict(h.task_definitions[write], version=2)
    old_key = h._cache_key_for_task(manifest, task_id, inputs)
    assert old_key != record["cache_key"]
    assert lookup_cache(h.data_dir, old_key) == (False, None)
