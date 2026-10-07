from __future__ import annotations

from pathlib import Path

import pytest
import yaml

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
        (ROOT / "harness/story/tasks" / f"{task_type}.yaml").read_text(encoding="utf-8")
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
    assert 2 * len(card) + model["max_tokens"] <= model["context_length"]
    return fitted


@pytest.mark.parametrize("task_type", ["S4.section", "S4.item"])
def test_world_prerequisites_with_twenty_long_facets_fit(task_type):
    facets = [{"body": "面" * 1199 + "。"} for _ in range(20)]
    fitted = _assert_fits(task_type, {
        "facts": {"customs-1": {"facts": [{"name": "水路", "value": 200, "unit": "m",
                                            "year": 12, "calendar": "開拓暦", "count": 1}]}},
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
    inputs = {"event_a": {"id": "e001", "event": event}, "event_b": {"id": "e002", "event": event}}
    assert _assert_fits("S8.compare", inputs) == inputs


def test_world_excerpt_keeps_whole_leading_facets_and_paragraphs():
    facets = [{"body": str(i) + "面" * 1198 + "。"} for i in range(10)]
    selected = _select_world_sections(
        {"world_sections": ["place"]}, {"place": {"name": "場"}}, {"place": facets},
    )
    assert selected[0]["body"] == facets[0]["body"]
    assert _world_excerpt("あ" * 700 + "。\n\n" + "い" * 600 + "。", 1200) == "あ" * 700 + "。"
    assert _world_excerpt("あ" * 700 + "。" + "い" * 600 + "。", 1200) == "あ" * 700 + "。"
