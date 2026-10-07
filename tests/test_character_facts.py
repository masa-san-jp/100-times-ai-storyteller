from __future__ import annotations

import json
from pathlib import Path

import pytest

from storyteller.cards import generate_task_card, prepare_task_inputs, input_char_count
from storyteller.character_facts import (
    CHARACTER_WORLD_SECTIONS, render_character_sheet, sheet_for_card, world_context,
)
from storyteller.glossary import glossary_id
from storyteller.validation import load_and_validate_yaml, validate_output, input_source_ids
from tests.test_story_s3 import _run
from tests.test_story_s5 import _character, _s5_inputs

ROOT = Path(__file__).parents[1]


def _definition(task_type="S5.facts"):
    return load_and_validate_yaml(ROOT / f"harness/story/tasks/{task_type}.yaml",
                                  ROOT / "schemas/task-definition.schema.json")


def _world_output():
    return {"facts": [{"name": "カナ", "value": 200, "unit": "m", "year": 12,
                       "calendar": "開拓暦", "count": 1}],
            "glossary": [{"name": "カナ", "reading": "カナ", "kind": "地名",
                          "definition": "水路の端の集落。"}]}


def _glossary():
    return [{"id": glossary_id("S4.facts-place-1", 1), **_world_output()["glossary"][0]}]


def _output():
    place_id = _glossary()[0]["id"]
    return {"age": 30, "birth_year": 12, "calendar": "開拓暦", "height_cm": 170,
            "build": "細身", "birthplace": place_id, "residence": place_id,
            "occupation": "水路の点検係", "affiliation": None,
            "family": [], "timeline": [{"year": 12, "event": "集落で生まれた。"},
                                       {"year": 30, "event": "水路の点検を始めた。"}],
            "skills": ["流水の音から漏れを探す。"]}


def _inputs():
    character = _character("protagonist", "c1", "本人")
    return {**_s5_inputs("リオ", character), "glossary": _glossary(),
            "world_facts": world_context({"S4.facts-place-1": _world_output()})}


def _validate(output, inputs=None):
    return validate_output(_definition(), json.dumps(output, ensure_ascii=False),
                           _inputs() if inputs is None else inputs, harness_root=ROOT)


@pytest.mark.parametrize("field,value", [
    ("age", "成人"), ("age", -1), ("birth_year", "昔"), ("height_cm", "高い"),
    ("height_cm", 0), ("build", " "), ("occupation", ""), ("skills", []),
    ("timeline", []), ("family", ["母"]), ("calendar", ""),
])
def test_character_facts_require_explicit_typed_fields(field, value):
    output = _output()
    output[field] = value
    result = _validate(output)
    assert not result.passed and any("schema" in error for error in result.errors)


@pytest.mark.parametrize("field", list(_output()))
def test_character_facts_require_every_spec_field(field):
    output = _output()
    del output[field]
    assert not _validate(output).passed


@pytest.mark.parametrize("field,value", [
    ("birthplace", "g000000"), ("residence", "g000000"), ("affiliation", "g000000"),
    ("calendar", "別の暦"), ("family", [{"relationship": "母", "name": "未登録の家族"}]),
    ("timeline", [{"year": 11, "event": "出生前の職歴。"}]),
    ("timeline", [{"year": 11.0, "event": "出生前の職歴。"}]),
    ("birth_year", 13.0),
])
def test_character_facts_reject_unknown_glossary_calendar_and_prebirth_events(field, value):
    output = _output()
    output[field] = value
    result = _validate(output)
    assert not result.passed and any("人物の事実" in error for error in result.errors)


def test_character_facts_accept_registered_family_affiliation_and_embedded_terms():
    output = _output()
    output["family"] = [{"relationship": "母", "name": "リオ"}]
    output["affiliation"] = "g222222"
    inputs = _inputs()
    inputs["world_facts"][0]["glossary"].extend([
        {"id": "g111111", "name": "リオ", "reading": "リオ", "kind": "人物", "definition": "集落の水路の管理人。"},
        {"id": "g222222", "name": "セト", "reading": "セト", "kind": "組織", "definition": "水路を点検する組合。"},
    ])
    inputs["glossary"] = []
    result = _validate(output, inputs)
    assert result.passed, result.errors


@pytest.mark.parametrize("output", [None, [], "文字列", {"family": None, "timeline": None}])
def test_character_facts_invalid_shapes_report_errors_without_crashing(output):
    result = _validate(output)
    assert not result.passed


def test_world_context_preserves_complete_rows_terms_and_interleaves_sections():
    world = _world_output()
    world["facts"] *= 6
    context = world_context({"S4.facts-place-1": world,
                             "S4.facts-customs-1": _world_output(),
                             "S4.facts-events-1": _world_output()})
    assert [row["id"] for row in context[:2]] == ["place", "customs"]
    assert all(len(row["facts"]) == 1 for row in context)
    assert context[0]["glossary"][0]["id"] == _glossary()[0]["id"]
    assert "events" not in {row["id"] for row in context}
    inputs = _inputs()
    inputs["world_facts"] = context * 20
    fitted = prepare_task_inputs(_definition(), inputs)
    assert fitted["world_facts"] and len(fitted["world_facts"]) < len(inputs["world_facts"])
    assert fitted["world_facts"] == inputs["world_facts"][:len(fitted["world_facts"])]
    card = generate_task_card(_definition(), "ticket", fitted)
    assert "S4.facts" not in card and "S5.facts" not in card
    assert input_char_count(_definition(), fitted) <= _definition()["max_input_chars"]
    assert _validate(_output(), fitted).passed


def test_required_sheet_and_referenced_terms_survive_description_budget():
    inputs = _inputs()
    sheet = sheet_for_card(_output(), _glossary())
    for task_type in ("profile", "intro", "appearance", "personality", "values", "backstory",
                      "relationship", "voice", "inner_conflict", "motive", "catchphrase"):
        definition = _definition(f"S5.{task_type}")
        inputs.update({"facts": sheet, "profile": "長いプロフィール。" * 200,
                       "names": [{"name": "相手名" * 500}],
                       "other_person": {"c2": {"id": "c2", "name": "相手", "role": "supporter", "intro": "紹介。"}}})
        fitted = prepare_task_inputs(definition, inputs)
        assert fitted["facts"] == sheet
        assert _glossary()[0]["id"] in input_source_ids(fitted)
        card = generate_task_card(definition, "ticket", fitted)
        assert "水路の端の集落。" in card
        assert "事実のシート" in card and "矛盾させない" in card


def test_fact_tasks_precede_every_description_and_depend_only_on_relevant_world(tmp_path):
    orchestrator, run_id = _run(tmp_path, 26, run_number=1)
    manifest = orchestrator.load_run(run_id)
    tasks = manifest["tasks"]
    assert tasks["S5.name-c1"]["deps"] == ["S3.assign"]
    facts_id = "S5.facts-c1"
    assert "S5.name-c1" in tasks[facts_id]["deps"]
    assert any(task_id.startswith("S4.facts-place-") for task_id in tasks[facts_id]["deps"])
    for task_id in tasks[facts_id]["deps"]:
        if task_id.startswith("S4."):
            assert task_id.startswith("S4.facts-")
            assert any(task_id.startswith(f"S4.facts-{section}-") for section in CHARACTER_WORLD_SECTIONS)
    for task in tasks.values():
        if task["type"].startswith("S5.") and task["type"] not in {"S5.name", "S5.facts", "S5.relationship_context"}:
            assert facts_id in task["deps"]
            assert task["state"] == "blocked"


def test_character_sheet_renders_names_units_sorted_timeline_and_safe_cells():
    output = _output()
    output["family"] = [{"relationship": "母", "name": "カナ"}]
    output["timeline"].reverse()
    output["occupation"] = "点検|係\n水路"
    markdown = render_character_sheet(output, _glossary())
    assert "| 身長（cm） | 170 |" in markdown
    assert "| 出身地 | カナ |" in markdown
    assert "| 母 | カナ |" in markdown
    assert "点検\\|係 水路" in markdown
    assert markdown.index("| 開拓暦 | 12 |") < markdown.index("| 開拓暦 | 30 |")


def test_character_facts_reject_terms_removed_from_the_card_by_budget():
    inputs = _inputs()
    for ordinal in range(1, 20):
        inputs["world_facts"].extend(world_context({f"S4.facts-customs-{ordinal}": _world_output()}))
    fitted = prepare_task_inputs(_definition(), inputs)
    assert len(fitted["world_facts"]) < len(inputs["world_facts"])
    output = _output()
    output["residence"] = inputs["world_facts"][-1]["glossary"][0]["id"]
    result = _validate(output, fitted)
    assert not result.passed
    assert any("residence" in error for error in result.errors)


def test_fact_card_fits_model_context_with_full_retry_reason():
    inputs = _inputs()
    inputs["world_facts"] *= 40
    definition = _definition()
    fitted = prepare_task_inputs(definition, inputs)
    card = generate_task_card(definition, "a" * 32, fitted, retry_reason="理" * 500)
    import yaml
    model = yaml.safe_load((ROOT / "config/models.yaml").read_text(encoding="utf-8"))["models"]["gpt-oss:20b"]
    assert 2 * len(card) + model["max_tokens"] <= model["context_length"]
    example = json.loads(definition["card"]["output_example"])
    assert set(example["family"][0]) == {"relationship", "name"}
