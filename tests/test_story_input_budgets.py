from __future__ import annotations

from tests.support.legacy_harness import legacy_story_harness, task_path  # noqa: F401

from pathlib import Path

import json

import pytest
import yaml

from storyteller.adapter import _without_string_constraints
from storyteller.cards import generate_task_card, input_char_count
from storyteller.character_facts import sheet_for_card
from tests.test_character_facts import _output as _facts, _glossary
from storyteller.orchestrator import _prepare_card_inputs
from storyteller.story_s6 import _select_world_sections, _world_excerpt
from tests.test_story_s5 import _character, _s5_inputs
from tests.test_story_s7 import _event, _slot


ROOT = Path(__file__).parents[1]


def _definition(task_type):
    return yaml.safe_load(
        (task_path(task_type)).read_text(encoding="utf-8")
    )


def _assert_fits(task_type, inputs):
    definition = _definition(task_type)
    inputs = {name: value for name, value in inputs.items() if name in definition["inputs"]}
    fitted = _prepare_card_inputs(definition, inputs)
    size = input_char_count(definition, fitted)
    assert size <= definition["max_input_chars"]
    tail = None
    if definition.get("continuation", definition["output"] == "text"):
        remaining = definition["max_input_chars"] - size
        assert remaining >= 1000
        tail = "末" * remaining
    card = generate_task_card(
        definition, "a" * 32, inputs=fitted,
        retry_reason="理" * 500, continuation_tail=tail, extend_to_min=tail is not None,
    )
    model = yaml.safe_load((ROOT / "config/models.yaml").read_text(encoding="utf-8"))["models"]["gpt-oss:20b"]
    assert 2 * len(card) + min(model["max_tokens"], definition.get("max_output_tokens", model["max_tokens"])) <= model["context_length"]
    if task_type == "S8.detail":
        schema = json.loads((ROOT / definition["validate"]["schema"]).read_text(encoding="utf-8"))
        # Include API JSON schema and conservative message-wrapper reservation.
        wire_schema = json.dumps(_without_string_constraints(schema), ensure_ascii=False)
        assert 2 * (len(card) + len(wire_schema) + 512) + min(
            model["max_tokens"], definition["max_output_tokens"]
        ) <= model["context_length"]
    return fitted


@pytest.mark.parametrize("task_type", ["S4.section", "S4.item"])
def test_world_prerequisites_with_twenty_long_facets_fit(task_type):
    facets = [{"body": "面" * 1199 + "。"} for _ in range(20)]
    fitted = _assert_fits(task_type, {
        "facts": [{"id": "customs", "text": "対面時の身体距離：2 m"}],
        "calendar": "開拓暦", "current_year": 300,
        "section": {"id": "customs", "name": "風習", "definition": "暮らしの風習。"},
        "viewpoint": "日々の暮らし。", "cut": {"id": "place:t1", "text": "水路"},
        "name": "カナ", "place": {"id": "place:t1", "text": "水路"},
        "era": {"id": "era:t1", "text": "停電"}, "plot_type": "旅",
        "prerequisite_sections": facets,
        "prerequisite_items": [{"name": "記録係", "summary": "要" * 120}] * 20,
    })
    assert fitted["prerequisite_sections"] == facets[:len(fitted["prerequisite_sections"])]
    assert fitted["prerequisite_sections"]


@pytest.mark.parametrize("task_type", [
    "S5.profile", "S5.intro", "S5.appearance", "S5.motive", "S5.catchphrase",
    "S5.personality", "S5.values", "S5.backstory", "S5.relationship",
    "S5.voice", "S5.inner_conflict",
])
def test_character_items_with_long_profile_fit(task_type):
    inputs = _s5_inputs("カナ", _character("adversary", "c2", "他者"))
    inputs["facts"] = sheet_for_card(_facts(), _glossary())
    inputs["profile"] = "人" * 1499 + "。"
    inputs["other_person"] = {"c1": {"id": "c1", "name": "主人公名", "role": "protagonist", "intro": "紹介。"}}
    inputs["names"] = {f"c{i}": {"name": "名" * 1500} for i in range(1, 6)}
    _assert_fits(task_type, inputs)


def test_event_with_long_world_and_character_items_fits():
    slot = _slot()
    characters = [dict(slot["characters"][0], id=f"c{i}", voice="声" * 1500) for i in range(1, 4)]
    world = [dict(section, body="\n".join(["面" * 1199 + "。"] * 10)) for section in slot["world_sections"]]
    _assert_fits("S7.event", {
        "event_field": "後の出来事への伏線",
        "decided_fields": {field: "記" * 120 for field in ("what", "where", "when", "why", "intent", "result", "emotion")},
        "stage_definition": slot["stage"]["definition"], "stage_guidance": slot["stage"]["guidance"],
        "required_events": slot["required_events"], "absent_role_note": None,
        "object": slot["object"], "characters": characters, "world_sections": world,
        "previous_result": "結" * 120, "conflict": slot["plot"]["conflict"],
        "climax": slot["plot"]["climax"], "theme": slot["theme"],
    })
    _assert_fits("S7.detail", {
        **_event(), "characters": characters, "world_sections": world,
        "beat": {"id": "opening", "name": "発端", "definition": "発端を示す。"},
        "previous_scene": "前" * 600,
    })


def test_comparison_preserves_both_max_length_valid_events():
    event = _event()
    event.update({name: "記" * 120 for name, value in event.items() if isinstance(value, str)})
    event["who"] = [f"c{i}" for i in range(1, 6)]
    inputs = {"comparison_kind": "events", "event_a": {"comparison_kind": "events", "id": "e001", "event": event}, "event_b": {"id": "e002", "event": event}}
    assert _assert_fits("S8.compare", inputs) == inputs


def test_world_excerpt_keeps_whole_leading_facets_and_paragraphs():
    facets = [{"body": str(i) + "面" * 1198 + "。"} for i in range(10)]
    selected = _select_world_sections(
        {"world_sections": ["place"]}, {"place": {"name": "場"}}, {"place": facets},
    )
    assert selected[0]["body"] == facets[0]["body"]
    assert _world_excerpt("あ" * 700 + "。\n\n" + "い" * 600 + "。", 1200) == "あ" * 700 + "。"
    assert _world_excerpt("あ" * 700 + "。" + "い" * 600 + "。", 1200) == "あ" * 700 + "。"


def test_relationship_keeps_counterpart_and_glossary_at_dev_scale_sizes():
    """Regression: the counterpart (required) and glossary were emptied.

    Indexed ``S5.relationship_context`` output is {"c2": {...}}; with a
    realistic profile the whole input exceeds 2800 chars, and head truncation
    in reverse slot order used to empty ``other_person`` before the profile
    (and the optional glossary is dropped first, by design).
    """
    inputs = _s5_inputs("カナ", _character("protagonist", "c1", "本人"))
    inputs["facts"] = sheet_for_card(_facts(), _glossary())
    inputs["profile"] = "人" * 1019
    inputs["other_person"] = {"c2": {
        "id": "c2", "name": "カタエ", "role": "supporter",
        "intro": "ハリソの朗読者カタエ。忘れられた名を読み上げ、朱亜理の傍らで試練と気づきをもたらす支援者。",
    }}
    inputs["glossary"] = [
        {"id": f"g{number:06x}", "name": f"用語{number}", "reading": "ヨウゴ", "kind": "人物",
         "definition": "この世界の用語の定義です"} for number in range(5)
    ]
    fitted = _assert_fits("S5.relationship", inputs)
    assert [entry["name"] for entry in fitted["other_person"]] == ["カタエ"]
    assert fitted["other_person"][0]["intro"]
    # The optional glossary is the first thing dropped under budget (task-model
    # §3.4); the fact sheet carries the glossary entries its own text needs.
    assert fitted["facts"]["glossary"]


def test_detail_judge_preserves_inputs_at_maximum_budget_with_retry():
    definition = _definition("S8.detail")
    inputs = {"event": _event(), "body": "", "previous": "直前の場面。"}
    remaining = definition["max_input_chars"] - input_char_count(definition, inputs)
    inputs["body"] = "本" * remaining
    assert input_char_count(definition, inputs) == 3500
    assert _assert_fits("S8.detail", inputs) == inputs


def test_detail_judge_valid_body_conditions_and_previous_fill_budget():
    definition = _definition("S8.detail")
    event = _event()
    event["conditions"] = "扉を押すと灯りが点く。扉を開く前に鍵を回す。"
    inputs = {"event": event, "body": "本" * 2499 + "。", "previous": ""}
    remaining = definition["max_input_chars"] - input_char_count(definition, inputs)
    assert remaining > 0
    inputs["previous"] = "前" * (remaining - 1) + "。"
    assert input_char_count(definition, inputs) == 3500
    assert _assert_fits("S8.detail", inputs) == inputs
    assert len(inputs["body"]) == 2500
