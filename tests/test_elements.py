from __future__ import annotations

import json
from pathlib import Path

import pytest

from storyteller.cards import generate_task_card
from storyteller.elements import element_contract_errors, parse_element_value, resolve_range
from storyteller.orchestrator import Orchestrator
from storyteller.task_outputs import read_task_output, task_sources
from storyteller.validation import SchemaValidationError, validate_document, validate_output
from tools.check_harness import MIGRATION_EXCEPTIONS, check_harness

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(("element", "raw", "unit", "expected"), [
    ("integer", "　１，２４８年。\n", "年", 1248),
    ("integer", "-20", "", -20),
    ("number", " ４５０．５ km²。 ", "km²", 450.5),
    ("number", "-1.25e2", "", -125.0),
])
def test_value_normalization(element, raw, unit, expected):
    assert parse_element_value(raw, element, unit=unit) == expected


@pytest.mark.parametrize(("element", "raw"), [
    ("integer", "12.0"), ("integer", "年は12"), ("integer", "12 13"),
    ("number", "NaN"), ("number", "Infinity"), ("number", "1e999"),
    ("number", "450m"),
])
def test_values_reject_explanations_and_nonfinite_numbers(element, raw):
    with pytest.raises(ValueError):
        parse_element_value(raw, element)


def test_ranges_resolve_run_values_and_check_inclusive_bounds():
    limits = resolve_range({"min": 1, "max": "{calendar.current_year}"}, {"calendar": {"current_year": 1248}})
    for value in (1, 1248):
        assert parse_element_value(str(value), "integer", value_range=limits) == value
    for value in (0, 1249):
        with pytest.raises(ValueError, match="range"):
            parse_element_value(str(value), "integer", value_range=limits)
    for limits, values in [({"max": "{missing}"}, {}), ({"min": 2, "max": 1}, {}), ({"max": True}, {})]:
        with pytest.raises(ValueError):
            resolve_range(limits, values)


def test_choice_set_membership_and_maximum():
    choices = ["g123abc", "g456def"]
    assert parse_element_value("g123abc。", "choice", choices=choices) == "g123abc"
    assert parse_element_value("g123abc\ng456def", "choice", choices=choices, choice_max=2) == choices
    for raw in ("unknown", "g123abc g456def", "", "g123abc g123abc"):
        with pytest.raises(ValueError):
            parse_element_value(raw, "choice", choices=choices)


def test_value_validation_and_cards_share_the_element_contract():
    definition = {"id": "D1.value", "version": 1, "kind": "llm", "element": "integer",
                  "output": "text", "range": {"min": 1, "max": "{current_year}"},
                  "card": {"role": "年を作る。", "steps": ["値を書く。"]}}
    inputs = {"unit": "年", "current_year": 1248}
    result = validate_output(definition, "１２４８年。", inputs)
    assert result.passed and result.value == "1248"
    assert not validate_output(definition, "1249", inputs).passed
    assert "値だけを書く。単位・説明を書かない。" in generate_task_card(definition, "a" * 32, inputs={})


@pytest.mark.parametrize("extra", [
    {"element": "integer", "continuation": True},
    {"element": "choice", "choice_max": 0},
    {"element": "name", "range": {"min": 0}},
    {"element": "integer", "choice_max": 2},
    {"default_sources": ["c1"]},
    {"validate": {"checks": ["sources_exist"]}},
])
def test_task_schema_rejects_invalid_element_settings_and_retired_fields(extra):
    definition = {"id": "D1.value", "version": 1, "kind": "llm", "element": "text", "output": "text",
                  "card": {"role": "書く。", "steps": ["書く。"]}, **extra}
    with pytest.raises(SchemaValidationError):
        validate_document(definition, ROOT / "schemas/task-definition.schema.json")


def test_task_schema_requires_element_and_forbids_llm_fields_on_code_tasks():
    for definition in [
        {"id": "D1.value", "version": 1, "kind": "llm", "output": "text", "card": {"role": "書く。", "steps": ["書く。"]}},
        {"id": "D1.value", "version": 1, "kind": "code", "handler": "value", "element": "text"},
    ]:
        with pytest.raises(SchemaValidationError):
            validate_document(definition, ROOT / "schemas/task-definition.schema.json")


def test_harness_check_passes_with_only_explicit_followup_exceptions():
    assert check_harness() == []
    assert MIGRATION_EXCEPTIONS == {"S4.facts": "P1-32", "S5.facts": "P1-33", "S7.event": "P1-34"}


@pytest.mark.parametrize("element,fields", [("name", {"name", "reading"}), ("labeled", {"text", "kind"}), ("judgement", {"answer", "reason"})])
def test_element_checker_rejects_extra_fields_missing_fields_and_missing_enums(element, fields):
    properties = {field: {"type": "string"} for field in fields}
    for field in fields & {"kind", "answer"}:
        properties[field]["enum"] = ["yes", "no"]
    definition = {"element": element, "output": "json"}
    schema = {"type": "object", "properties": properties, "required": sorted(fields), "additionalProperties": False}
    assert not element_contract_errors(definition, schema)
    assert element_contract_errors(definition, {**schema, "properties": {**properties, "sources": {"type": "array"}}})
    assert element_contract_errors(definition, {**schema, "required": []})
    assert element_contract_errors({**definition, "output": "text"}, schema)


def test_json_sources_are_reconstructed_from_fitted_input_including_cache_reuse(tmp_path: Path):
    definition = {"id": "D1.name", "version": 1, "kind": "llm", "element": "name", "output": "json",
                  "card": {"role": "名前を作る。", "steps": ["書く。"], "output_example": '{"name":"...","reading":"..."}'},
                  "inputs": {"given": {"label": "素材", "from": "input", "select": "input.given", "required": True},
                             "unused": {"label": "補足", "from": "input", "select": "input.unused", "truncate": "drop"}},
                  "max_input_chars": 150}
    harness = Orchestrator(tmp_path, {"D1.name": definition})
    inputs = {"given": {"id": "m001", "text": "水路", "sources": ["hidden-parent"]}, "unused": {"id": "hidden", "text": "長" * 1000}}
    run_id = harness.create_run(seed=1, input_data=inputs)
    claim = harness.claim_next(run_id, executor_id="worker")
    assert "sources" not in claim["card"] and "hidden" not in claim["card"]
    output = {"name": "カナ", "reading": "カナ"}
    assert harness.submit(claim["ticket"], json.dumps(output, ensure_ascii=False)).accepted
    directory = harness.task_dir(run_id, "D1.name")
    assert json.loads((directory / "output.json").read_text(encoding="utf-8")) == output
    assert read_task_output(directory, "D1.name") == {**output, "sources": ["m001"]}
    assert task_sources(directory) == ["m001"]
    cached = harness.create_run(seed=1, input_data=inputs, run_id="20261007-000000-000001")
    assert harness.claim_next(cached, executor_id="worker") is None
    assert read_task_output(harness.task_dir(cached, "D1.name"), "D1.name") == {**output, "sources": ["m001"]}


def test_harness_checker_reports_source_and_shape_errors_from_files(tmp_path: Path):
    import shutil
    import yaml

    (tmp_path / "schemas/tasks").mkdir(parents=True)
    (tmp_path / "harness/story/tasks").mkdir(parents=True)
    shutil.copyfile(ROOT / "schemas/task-definition.schema.json", tmp_path / "schemas/task-definition.schema.json")
    definition = {"id": "S5.name", "version": 1, "kind": "llm", "element": "name", "output": "json",
                  "validate": {"schema": "schemas/tasks/S5.name.schema.json"},
                  "card": {"role": "名前を書く。", "steps": ["書く。"], "output_example": '{"name":"...","reading":"..."}'}}
    path = tmp_path / "harness/story/tasks/S5.name.yaml"
    path.write_text(yaml.safe_dump(definition, allow_unicode=True), encoding="utf-8")
    schema_path = tmp_path / "schemas/tasks/S5.name.schema.json"
    schema = {"type": "object", "required": ["name", "reading", "sources"], "additionalProperties": False,
              "properties": {"name": {"type": "string"}, "reading": {"type": "string"}, "sources": {"type": "array"}}}
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    errors = check_harness(tmp_path)
    assert any("sources" in error for error in errors)
    assert any("element:" in error for error in errors)
    definition["card"]["steps"].append("sources を書く。")
    path.write_text(yaml.safe_dump(definition, allow_unicode=True), encoding="utf-8")
    assert any("カードに sources" in error for error in check_harness(tmp_path))


def test_choice_validation_accepts_only_ids_from_the_choice_slot():
    definition = {"element": "choice", "output": "text", "choice_max": 2}
    inputs = {"choices": [{"id": "g123abc"}, {"id": "g456def"}], "character": {"id": "c1"}}
    assert validate_output(definition, "g123abc g456def", inputs).passed
    assert not validate_output(definition, "c1", inputs).passed
    assert validate_output(definition, "none", {"choices": ["none", "g123abc"]}).passed


def test_integer_tasks_retry_one_value_without_entering_continuation(tmp_path: Path):
    definition = {"id": "D1.value", "version": 1, "kind": "llm", "element": "integer", "output": "text",
                  "range": {"min": 1, "max": "{current_year}"},
                  "inputs": {"current_year": {"label": "現在の年", "from": "input", "select": "input.current_year", "required": True}},
                  "card": {"role": "年を書く。", "steps": ["値を書く。"]}}
    harness = Orchestrator(tmp_path, {"D1.value": definition})
    run_id = harness.create_run(seed=1, input_data={"current_year": 1248})
    for raw in ("あ" * 400, "1249"):
        claim = harness.claim_next(run_id, executor_id="worker")
        assert not harness.submit(claim["ticket"], raw).accepted
        task = harness.load_run(run_id)["tasks"]["D1.value"]
        assert task["continuation_step"] == 0 and task["state"] == "ready"
    claim = harness.claim_next(run_id, executor_id="worker")
    assert harness.submit(claim["ticket"], "１，２４８。").accepted
    directory = harness.task_dir(run_id, "D1.value")
    assert (directory / "output.md").read_text(encoding="utf-8") == "1248"
    assert not (directory / "partial.md").exists()


def test_attribution_uses_material_ids_and_excludes_instruction_ids():
    from storyteller.validation import input_source_ids
    assert input_source_ids({
        "names": {"c1": {"name": "カナ"}, "c2": {"name": "ナリ"}},
        "event_a": {"id": "e001", "event": {"who": ["c1"]}},
        "event_b": {"id": "e002"},
        "section": {"id": "place"}, "name_sound": {"set_id": "sound-01"},
        "role_definition": {"id": "protagonist"},
    }) == ["c1", "c2", "e001", "e002"]
