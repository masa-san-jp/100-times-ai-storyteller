"""P3-12: the story's skeleton is passed from F1.split v6 to F2 v5 (ADR-0011).

The canonical ``skeleton`` itself is recorded by P1-69; the story schema does
not know it yet, so these tests read the story without the schema's check of
that one key.
"""

from __future__ import annotations

import json
import socket
from copy import deepcopy
from pathlib import Path
from urllib.request import Request, urlopen

import pytest
import yaml

import storyteller.formatting as formatting
from storyteller.cache import lookup_cache
from storyteller.cards import F2_CONDITION_STEP, generate_task_card, prepare_task_inputs
from storyteller.new_run import create_story_orchestrator
from tests.test_format_novel import _event as _novel_event, _story as _novel_story, _thread
from tests.test_formatting import (
    PROFILE, ROOT, _completed_story_run, _drive, _good_responder, dummy_format,
)

SKELETON = {
    "analysis": {"desire": "認められたい。", "suppression": None, "conflict": "守りたいが逃げたい。"},
    "lack": "居場所を失ったこと。",
    "climax": "カナが鐘を鳴らし、港の行く末を決める。",
    "resolution": "港は残り、カナは旅人でなくなった。",
    "turns": [
        {"thread": "t002", "n": 1, "slot": "e004", "text": "ミオが地図を渡す。"},
        {"thread": "t001", "n": 2, "slot": "e003", "text": "カナが橋を渡る。"},
        {"thread": "t001", "n": 1, "slot": "e001", "text": "カナが呼びかけを聞く。"},
        {"thread": "t001", "n": 3, "slot": None, "text": None},
    ],
}
LEDGER = "\n".join([
    "欠落：居場所を失ったこと。",
    "決定的な瞬間：カナが鐘を鳴らし、港の行く末を決める。",
    "結末の後：港は残り、カナは旅人でなくなった。",
    "転換（t001・1・e001）：カナが呼びかけを聞く。",
    "転換（t001・2・e003）：カナが橋を渡る。",
    "転換（t001・3）：（未定）",
    "転換（t002・1・e004）：ミオが地図を渡す。",
])
SKELETON_LABEL = "### 物語の骨子"
EVENTS_LABEL = "### 書く対象の出来事"
SKELETON_STEP = "物語の骨子の中で、この部分が担う位置を踏まえて書く。"


@pytest.fixture(autouse=True)
def story_with_skeleton_is_readable(monkeypatch):
    """P1-69 adds ``skeleton`` to the schema; until then, validate the rest."""

    original = formatting.read_story

    def read(run_dir, repository_root=formatting.REPOSITORY_ROOT):
        path = Path(run_dir) / "story" / "story.json"
        story = json.loads(path.read_text(encoding="utf-8"))
        skeleton = story.pop("skeleton", None)
        formatting.validate_document(story, Path(repository_root) / formatting.STORY_SCHEMA)
        if skeleton is not None:
            story["skeleton"] = skeleton
        return story

    monkeypatch.setattr(formatting, "read_story", read)
    return original


def setup(tmp_path, dummy_format, *, skeleton=SKELETON, conditions=False, version=6, continuation=True):
    profile = deepcopy(PROFILE)
    profile["continuation"] = continuation
    (dummy_format / "dummy/profile.yaml").write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    data, run = _completed_story_run(tmp_path, events=5)
    story_path = data / "runs" / run / "story/story.json"
    story = json.loads(story_path.read_text(encoding="utf-8"))
    if skeleton is not None:
        story["skeleton"] = skeleton
    if conditions:
        story["events"][0]["conditions"] = ["鐘が鳴る時だけ門を開く。"]
    story_path.write_text(json.dumps(story, ensure_ascii=False), encoding="utf-8")
    h = create_story_orchestrator(data)
    h.task_definitions["F1.split"]["version"] = version
    formatting.start_formatting(h, run, "dummy", formats_root=dummy_format)
    h.advance(run)
    return h, run


def plan_of(h, run, name="dummy"):
    return json.loads((h.task_dir(run, f"F1.split-{name}") / "output.json").read_text(encoding="utf-8"))


def cards_by_task(h, run):
    log = _drive(h, run, _good_responder(h, run))
    assert h.load_run(run)["status"] == "completed"
    assert all(accepted for _, _, accepted in log)
    return {task: card for task, card, _ in log}


def input_of(h, run, task_id):
    return json.loads((h.task_dir(run, task_id) / "input.json").read_text(encoding="utf-8"))


# --- (1) a canonical skeleton is copied to the plan ---------------------------

def test_v6_split_writes_the_skeleton_ledger_at_the_top_of_the_plan(tmp_path, dummy_format):
    h, run = setup(tmp_path, dummy_format)
    plan = plan_of(h, run)
    assert plan["skeleton"] == LEDGER
    assert all("skeleton" not in part for part in plan["parts"])


def test_ledger_order_is_lack_climax_resolution_then_turns_by_thread_and_n():
    threads = [{"id": "t001"}, {"id": "t002"}]
    assert formatting.skeleton_ledger(SKELETON, threads) == LEDGER
    # Missing values are （未定）, never dropped.
    empty = {"lack": None, "climax": "", "resolution": None, "turns": []}
    assert formatting.skeleton_ledger(empty, threads) == "欠落：（未定）\n決定的な瞬間：（未定）\n結末の後：（未定）"


def test_build_plan_copies_the_skeleton_only_for_formats_that_tell_the_events():
    events = [_novel_event(n, "t001", "setup" if n == 1 else "turn") for n in range(1, 4)]
    story = _novel_story([_thread(1, "main", "three-beat")], events)
    story["skeleton"] = {**SKELETON, "turns": [SKELETON["turns"][2]]}
    for name in ("novel", "screenplay", "slides"):
        plan = formatting.build_plan(story, formatting.load_profile(name), ROOT, skeleton=True)
        assert plan["skeleton"].startswith("欠落：居場所を失ったこと。\n決定的な瞬間：")
        assert plan["skeleton"].endswith("転換（t001・1・e001）：カナが呼びかけを聞く。")
    assert "skeleton" not in formatting.build_plan(story, formatting.load_profile("novel"), ROOT)


# --- (2) no skeleton: no key --------------------------------------------------

@pytest.mark.parametrize("skeleton", [None, {}])
def test_story_without_skeleton_has_no_skeleton_key_and_the_card_has_no_section(tmp_path, dummy_format, skeleton):
    h, run = setup(tmp_path, dummy_format, skeleton=skeleton)
    plan = plan_of(h, run)
    assert "skeleton" not in plan
    cards = cards_by_task(h, run)
    assert all(SKELETON_LABEL not in card for card in cards.values())
    assert "skeleton" not in input_of(h, run, "F2.write-u01p1-dummy")


# --- (3) card -------------------------------------------------------------------

@pytest.mark.parametrize("continuation", [True, False])
def test_f2_card_shows_the_skeleton_before_the_events(tmp_path, dummy_format, continuation):
    h, run = setup(tmp_path, dummy_format, continuation=continuation)
    write = "F2.write" if continuation else "F2.write_once"
    definition = h.task_definitions[write]
    assert definition["version"] == 5 and definition["max_input_chars"] == 16000
    assert list(definition["inputs"])[:2] == ["skeleton", "events"]
    slot = definition["inputs"]["skeleton"]
    assert slot == {
        "label": "物語の骨子", "from": "F1.split", "select": "skeleton",
        "required": False, "truncate": "none",
    }
    cards = cards_by_task(h, run)
    for part in ("u01p1", "u02p1"):
        card = cards[f"{write}-{part}-dummy"]
        assert f"{SKELETON_LABEL}\n{LEDGER}\n{EVENTS_LABEL}" in card
        assert card.index(SKELETON_LABEL) < card.index(EVENTS_LABEL)
        assert f"1. {SKELETON_STEP}" in card or f". {SKELETON_STEP}" in card
        assert input_of(h, run, f"{write}-{part}-dummy")["skeleton"] == LEDGER


def test_condition_step_is_shown_only_when_the_story_has_conditions(tmp_path, dummy_format):
    h, run = setup(tmp_path, dummy_format, conditions=True)
    assert all("conditions" in part for part in plan_of(h, run)["parts"])
    card = cards_by_task(h, run)["F2.write-u01p1-dummy"]
    assert F2_CONDITION_STEP in card and "確定した筋の条件" in card
    assert "内部 ID と「この場面は」などの構造の説明を書かない。" in card


def test_story_without_conditions_gives_parts_no_conditions_and_hides_the_condition_step(tmp_path, dummy_format):
    h, run = setup(tmp_path, dummy_format)
    assert all("conditions" not in part for part in plan_of(h, run)["parts"])
    cards = cards_by_task(h, run)
    f2 = {task: card for task, card in cards.items() if task.startswith("F2.")}
    assert f2
    for task, card in f2.items():
        assert F2_CONDITION_STEP not in card and "確定した筋の条件" not in card, task
        assert "内部 ID と「この場面は」などの構造の説明を書かない。" in card
        assert "conditions" not in input_of(h, run, task)
    # F4 makes no condition_conflict review for a part without conditions.
    assert all(
        "condition_conflict" not in json.dumps(review)
        for part in plan_of(h, run)["parts"] for review in part.get("reviews", [])
    )


# --- (4) old versions are unchanged ---------------------------------------------

def test_f1_v5_does_not_copy_the_skeleton_and_keeps_conditions(tmp_path, dummy_format):
    h, run = setup(tmp_path, dummy_format, version=5)
    plan = plan_of(h, run)
    assert "skeleton" not in plan
    assert all(part["conditions"] == [] for part in plan["parts"])
    cards = cards_by_task(h, run)
    assert all(SKELETON_LABEL not in card for card in cards.values())


def test_finished_v5_split_is_not_reexpanded_by_the_v6_definition(tmp_path, dummy_format):
    h, run = setup(tmp_path, dummy_format, version=5)
    before = (plan_of(h, run), {name: task["deps"] for name, task in h.load_run(run)["tasks"].items()})
    h.task_definitions["F1.split"]["version"] = 6
    h.advance(run)
    after = (plan_of(h, run), {name: task["deps"] for name, task in h.load_run(run)["tasks"].items()})
    assert before == after


def test_visual_prompts_and_reference_book_plans_have_no_skeleton_key():
    events = [_novel_event(n, "t001", "setup" if n == 1 else "turn") for n in range(1, 4)]
    story = _novel_story([_thread(1, "main", "three-beat")], events)
    story["skeleton"] = SKELETON
    story["cast"][0].update(appearance="黒髪", motive="x")
    profile = formatting.load_profile("visual-prompts")
    assert "skeleton" not in formatting.build_plan(story, profile, ROOT, skeleton=True)


@pytest.mark.parametrize("continuation", [True, False])
def test_f2_v5_cache_key_is_separate_from_v4(tmp_path, dummy_format, continuation):
    h, run = setup(tmp_path, dummy_format, continuation=continuation)
    cards_by_task(h, run)
    write = "F2.write" if continuation else "F2.write_once"
    task_id = f"{write}-u01p1-dummy"
    manifest = h.load_run(run)
    record = manifest["tasks"][task_id]
    inputs = input_of(h, run, task_id)
    assert h._cache_key_for_task(manifest, task_id, inputs) == record["cache_key"]
    assert lookup_cache(h.data_dir, record["cache_key"])[0]
    h.task_definitions[write] = dict(h.task_definitions[write], version=4)
    assert h._cache_key_for_task(manifest, task_id, inputs) != record["cache_key"]


# --- budget (saga-sized, largest F2 card) --------------------------------------

def _largest_saga_f2_inputs() -> dict:
    """The largest F2 card of a saga: every turn of 6 parts and the sub threads,
    each at its 200-character limit; a part of three events (screenplay) whose
    event fields are at their 120-character limits."""

    turns = [
        {"thread": f"t{(n % 8) + 1:03d}", "n": n // 8 + 1, "slot": f"e{n + 1:03d}", "text": "転" * 200}
        for n in range(40)
    ]
    skeleton = {"lack": "欠" * 150, "climax": "極" * 200, "resolution": "結" * 200, "turns": turns}
    threads = [{"id": f"t{n:03d}"} for n in range(1, 9)]
    field = "あ" * 120
    event = (
        f"いつ：{field}／どこ：{field}／誰が：カナ、ミオ／なぜ：{field}／意思：{field}／"
        f"行動：{field}／結果：{field}／感情：{field}"
    )
    return {
        "skeleton": formatting.skeleton_ledger(skeleton, threads),
        "events": [{"id": f"e{n:03d}", "text": event} for n in range(1, 4)],
        "characters": [{"id": f"c{n}", "text": f"人物{n}（よみ）／役／" + "い" * 200} for n in range(1, 9)],
        "motives": [{"name": f"人物{n}", "motive": "う" * 120} for n in range(1, 9)],
        "rules": list(formatting.load_profile("screenplay")["rules"]),
        "range": "900〜2400字",
        "previous_unit": "え" * 600,
        "conditions": ["お" * 100] * 6,
    }


def test_largest_saga_f2_input_fits_the_budget_without_dropping_the_skeleton():
    definition = formatting.load_format_task_definitions(ROOT)["F2.write"]
    inputs = _largest_saga_f2_inputs()
    fitted = prepare_task_inputs(definition, inputs=deepcopy(inputs))
    assert fitted["skeleton"] == inputs["skeleton"] and fitted["motives"] == inputs["motives"]
    assert fitted["events"] == inputs["events"] and fitted["conditions"] == inputs["conditions"]


def _ollama_up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=1):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _ollama_up(), reason="Ollama (gpt-oss:20b) が使えない")
def test_largest_saga_f2_card_with_one_inference_fits_the_context_of_gpt_oss_20b():
    context_length, max_tokens = 32768, 8192  # story-pipeline S6.plot: 20B models use 32768
    definition = formatting.load_format_task_definitions(ROOT)["F2.write"]
    card = generate_task_card(
        definition, "T-0001", inputs=_largest_saga_f2_inputs(), continuation_tail=None
    )
    payload = {
        "model": "gpt-oss:20b", "stream": False, "think": "low",
        "messages": [{"role": "user", "content": card}],
        # The input token count does not depend on how much is generated.
        "options": {"num_ctx": context_length, "num_predict": 32, "temperature": 0.7},
    }
    request = Request(
        "http://127.0.0.1:11434/api/chat", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urlopen(request, timeout=600) as response:
        document = json.loads(response.read().decode("utf-8"))
    used = document["prompt_eval_count"]
    print(f"card_chars={len(card)} prompt_eval_count={used} max_tokens={max_tokens} context={context_length}")
    assert used + max_tokens <= context_length
