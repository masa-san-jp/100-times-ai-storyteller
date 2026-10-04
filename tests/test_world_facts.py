from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from storyteller.cards import generate_task_card, prepare_task_inputs
from storyteller.adapter import AutoRunner
from storyteller.glossary import build_glossary, related_glossary
from storyteller.selectors import resolve_inputs
from storyteller.tables import load_table
from storyteller.validation import load_and_validate_yaml, validate_output
from storyteller.world_facts import facts_for_card, render_world_facts_markdown
from storyteller.validation import input_source_ids
from tests.test_story_s3 import _run


ROOT = Path(__file__).parents[1]


def _definition():
    return load_and_validate_yaml(ROOT / "harness/story/tasks/S4.facts.yaml",
                                  ROOT / "schemas/task-definition.schema.json")


def _inputs():
    section = load_table("world_sections")["sections"][0]
    return {"fact_schema": next(iter(section["fact_schema"].values())),
            "name_sound": {"set_id": "sound-01", "sounds": ["カ", "ナ", "リ", "オ", "セ", "ト"]},
            "place": {"id": "place:t1", "text": "水路の町"},
            "era": {"id": "era:t1", "text": "開拓の後"}}


def _output():
    return {"facts": [{"name": "カナ", "value": 200, "unit": "m", "year": 12,
                       "calendar": "開拓暦", "count": 1}],
            "glossary": [{"name": "カナ", "reading": "カナ", "kind": "地名",
                          "definition": "水路の端にある丘。"}],
            "sources": ["place:t1"]}


@pytest.mark.parametrize("field,value", [("value", "多い"), ("year", "古い"),
                                         ("count", "少数"), ("unit", ""), ("name", " ")])
def test_world_facts_reject_ambiguous_numeric_and_missing_named_fields(field, value):
    output = _output()
    output["facts"][0][field] = value
    result = validate_output(_definition(), json.dumps(output, ensure_ascii=False),
                             _inputs(), harness_root=ROOT)
    assert not result.passed
    assert any("schema" in error for error in result.errors)


def test_world_facts_validate_catalog_schema_and_assigned_name_sounds():
    inputs = _inputs()
    valid = validate_output(_definition(), json.dumps(_output(), ensure_ascii=False), inputs, harness_root=ROOT)
    assert valid.passed
    stricter = deepcopy(inputs)
    stricter["fact_schema"]["properties"]["facts"]["items"]["properties"]["unit"] = {"const": "km²"}
    invalid = validate_output(_definition(), json.dumps(_output(), ensure_ascii=False), stricter, harness_root=ROOT)
    assert any("fact_schema" in error for error in invalid.errors)
    output = _output()
    output["glossary"][0]["reading"] = "ホホ"
    invalid = validate_output(_definition(), json.dumps(output, ensure_ascii=False), inputs, harness_root=ROOT)
    assert any("uses_given" in error for error in invalid.errors)


def test_world_catalog_covers_every_viewpoint_with_valid_fact_schemas():
    for section in load_table("world_sections")["sections"]:
        assert set(section["fact_schema"]) == set(section["viewpoints"])
        for schema in section["fact_schema"].values():
            Draft202012Validator.check_schema(schema)
            expected = schema["properties"]["facts"]["items"]["required"]
            assert {"name", "value", "unit", "year", "count"} <= set(expected)
            if section["kind"] == "list":
                assert {"composition", "affiliation"} <= set(expected)
            if section["id"] == "past_events":
                assert {"cause", "result"} <= set(expected)


def test_local_adapter_uses_viewpoint_schema_for_list_fact_generation():
    section = next(section for section in load_table("world_sections")["sections"] if section["id"] == "past_events")
    schema = next(iter(section["fact_schema"].values()))
    runner = SimpleNamespace(state=SimpleNamespace(current=lambda _model: {"json_mode": "schema"}), model=None)
    loaded = AutoRunner._load_schema(runner, _definition(), SimpleNamespace(definition_root=ROOT),
                                     inputs={"fact_schema": schema})
    assert loaded["properties"]["facts"]["items"]["required"] == schema["properties"]["facts"]["items"]["required"]
    assert "cause" in loaded["properties"]["facts"]["items"]["required"]


def test_fact_tasks_are_shared_by_facets_and_precede_list_bodies(tmp_path: Path):
    scale = {"preset": "short", "axes": load_table("scales")["presets"]["short"]["axes"], "derived": {"events": 3, "threads": 1,
             "cast": 1, "parts": None, "world_sections": ["place", "customs", "people"], "pool_need": {}}}
    orchestrator, run_id = _run(tmp_path, 25, run_number=1, scale=scale)
    manifest = orchestrator.load_run(run_id)
    assignment = json.loads((orchestrator.task_dir(run_id, "S3.assign") / "output.json").read_text(encoding="utf-8"))
    tasks = manifest["tasks"]
    facts = assignment["world_fact_tasks"]
    sections = {section["id"]: section for section in assignment["world_sections"]}
    assert len(facts) == sum(len(section["viewpoints"]) if section["kind"] == "single" else
                             sum(task["section_id"] == section["id"] for task in assignment["world_tasks"])
                             for section in sections.values())
    for task in assignment["world_tasks"]:
        key = task["id"].rsplit("-f", 1)[0] if task["kind"] == "single" else task["id"]
        fact_id = f"S4.facts-{key}"
        body_id = f"S4.{'section' if task['kind'] == 'single' else 'item'}-{task['id']}"
        assert fact_id in tasks[body_id]["deps"]
        assert tasks[body_id]["state"] == "blocked"
        if task["kind"] == "list":
            assert f"S4.item_name-{key}" in tasks[fact_id]["deps"]
    for entry in facts:
        fact_id = f"S4.facts-{entry['id']}"
        prerequisites = sections[entry["section_id"]]["prerequisites"]
        expected = {f"S4.facts-{other['id']}" for other in facts if other["section_id"] in prerequisites}
        assert expected <= set(tasks[fact_id]["deps"])
        inputs = resolve_inputs(_definition(), {"S3.assign": assignment}, index=(entry["id"],))
        # List item names are optional here; real task dependencies supply them.
        fitted = prepare_task_inputs(_definition(), inputs)
        assert fitted["fact_schema"] == entry["fact_schema"]
        card = generate_task_card(_definition(), "ticket", inputs=fitted)
        assert "S4" not in card and "S3" not in card


def test_fact_export_and_related_glossary_use_only_own_section():
    inputs = _inputs()
    fact_tasks = [{"id": "place-1", "section_id": "place", "section": {"name": "場"},
                   "viewpoint": "標高", "fact_schema": inputs["fact_schema"]}]
    assignment = {"world_sections": [{"id": "place"}, {"id": "customs"}],
                  "world_fact_tasks": fact_tasks, "world_tasks": [], "cast": []}
    outputs = {"S3.assign": assignment, "S4.facts-place-1": _output(),
               "S4.facts-customs-1": _output()}
    glossary = build_glossary(outputs)
    related = related_glossary(glossary, assignment, {}, ["place-1"])
    assert len(related) == 1
    assert related[0]["name"] == "カナ"
    markdown = render_world_facts_markdown(assignment, outputs)
    assert "200 | m | 12 | 開拓暦 | 1" in markdown
    assert "### 年表" in markdown and "| 開拓暦 | 12 | カナ |" in markdown


def test_own_registered_terms_survive_prerequisite_budget_truncation():
    definition = load_and_validate_yaml(ROOT / "harness/story/tasks/S4.section.yaml",
                                        ROOT / "schemas/task-definition.schema.json")
    output = _output()
    sheet = facts_for_card("S4.facts-customs-1", output)
    term = sheet["glossary"][0]
    registered = build_glossary({"S4.facts-customs-1": output})[0]
    assert term["id"] == registered["id"]
    assert "id" not in output["glossary"][0]
    inputs = {
        "facts": {"customs-1": sheet},
        "section": {"id": "customs", "name": "風習", "definition": "暮らしの風習。"},
        "viewpoint": "公共空間の規則", "cut": {"id": "object:t1", "text": "門の石"},
        "place": {"id": "place:t1", "text": "水路の町"},
        "era": {"id": "era:t1", "text": "開拓の後"}, "plot_type": "旅",
        "prerequisite_sections": [{"body": "前提の描写。" * 250}] * 20,
        "glossary": [registered],
    }
    fitted = prepare_task_inputs(definition, inputs)
    assert fitted["glossary"] == []
    assert fitted["facts"] == inputs["facts"]
    assert term["id"] in input_source_ids(fitted)
    card = generate_task_card(definition, "ticket", inputs=fitted)
    assert term["id"] in card and term["definition"] in card
    assert "S4.facts" not in card
