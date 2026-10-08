from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from storyteller.adapter import AutoRunner
from storyteller.cards import generate_task_card, prepare_task_inputs
from storyteller.glossary import build_glossary, glossary_id, related_glossary
from storyteller.tables import load_table
from storyteller.validation import load_and_validate_yaml, validate_output, input_source_ids
from storyteller.world_facts import (
    description_entries, fact_source_context, legacy_character_world_context,
    render_world_facts_markdown, specialize_fact_definition,
)
from tests.test_story_s3 import _run

ROOT = Path(__file__).parents[1]


def _definition(task_type="S4.fact"):
    return load_and_validate_yaml(ROOT / f"harness/story/tasks/{task_type}.yaml",
                                  ROOT / "schemas/task-definition.schema.json")


def _inputs(shape="number", bounds=None):
    return {"label": "標高", "shape": shape, "bounds": bounds or {"min": 1, "max": 300},
            "unit": "m" if shape == "number" else "", "current_year": 300,
            "calendar": "カナ暦", "name_sound": {"set_id": "sound-01", "sounds": ["カ", "ナ", "リ", "オ", "セ", "ト"]},
            "place": {"id": "place:t1", "text": "水路の町"},
            "era": {"id": "era:t1", "text": "開拓の後"}, "glossary": []}


@pytest.mark.parametrize("shape,raw", [("number", "多い"), ("number", "1〜2"), ("number", "NaN"),
                                      ("integer", "1.5"), ("year", "0"), ("year", "301"),
                                      ("text", "あ" * 41)])
def test_scalar_fact_rejects_ambiguous_multiple_or_out_of_range_values(shape, raw):
    result = validate_output(_definition(), raw, _inputs(shape), harness_root=ROOT)
    assert not result.passed


@pytest.mark.parametrize("shape,raw,expected", [("number", "２００ m。", "200"), ("integer", "200", "200"),
                                                ("year", "300", "300"), ("text", "水路の点検", "水路の点検")])
def test_fact_uses_shared_parser_and_output_contract(shape, raw, expected):
    result = validate_output(_definition(), raw, _inputs(shape), harness_root=ROOT)
    assert result.passed, result.errors
    assert result.value == expected


def test_name_fact_and_calendar_use_one_name_and_assigned_sounds():
    inputs = _inputs("name")
    for definition in (_definition(), _definition("S4.calendar_name")):
        result = validate_output(definition, '{"name":"カナ","reading":"カナ"}', inputs, harness_root=ROOT)
        assert result.passed, result.errors
        invalid = validate_output(definition, '{"name":"カナ","reading":"ホホ"}', inputs, harness_root=ROOT)
        assert any("uses_given" in error for error in invalid.errors)
        invalid = validate_output(definition, '{"name":"カナ","reading":"カナ","facts":[]}', inputs, harness_root=ROOT)
        assert any("schema" in error for error in invalid.errors)


def test_catalog_covers_all_sections_viewpoints_and_list_items():
    catalog = load_table("world_facts")
    fields = [*catalog["facts"], *catalog["item_facts"]]
    keys = [field["key"] for field in fields]
    assert len(set(keys)) == len(keys)
    counts = Counter((field["section_id"], field["viewpoint"]) for field in fields)
    for section in load_table("world_sections")["sections"]:
        assert "fact_schema" not in section
        for viewpoint in section["viewpoints"]:
            assert 2 <= counts[section["id"], viewpoint] <= 6
        pool = catalog["item_facts" if section["kind"] == "list" else "facts"]
        assert {field["viewpoint"] for field in pool if field["section_id"] == section["id"]} == set(section["viewpoints"])
    assert all(set(field.get("refers", [])) <= set(keys) for field in fields)
    assert [field["label"] for field in fields].count("最高地点の標高") == 1
    assert "place.highest_elevation" in next(field for field in fields if field["key"] == "social_structure.forest_area")["refers"]
    assert catalog["current_year_range"] == {"min": 300, "max": 3000}


def test_local_adapter_receives_only_one_name_schema():
    inputs = _inputs("name")
    runner = SimpleNamespace(state=SimpleNamespace(current=lambda _model: {"json_mode": "schema"}), model=None)
    definition = specialize_fact_definition(_definition(), inputs)
    schema = AutoRunner._load_schema(runner, definition, SimpleNamespace(definition_root=ROOT), inputs=inputs)
    assert set(schema["properties"]) == {"name", "reading"}
    assert specialize_fact_definition(definition, inputs) == definition
    assert AutoRunner._load_schema(runner, specialize_fact_definition(_definition(), _inputs()),
                                  SimpleNamespace(definition_root=ROOT)) is None


def _planned(tmp_path, seed=25, sections=None):
    sections = sections or ["place", "customs", "people"]
    scale = {"preset": "short", "axes": load_table("scales")["presets"]["short"]["axes"],
             "derived": {"events": 3, "threads": 1, "cast": 1, "parts": None,
                         "world_sections": sections, "pool_need": {}}}
    orchestrator, run_id = _run(tmp_path, seed, run_number=1, scale=scale)
    assignment = json.loads((orchestrator.task_dir(run_id, "S3.assign") / "output.json").read_text(encoding="utf-8"))
    return orchestrator, run_id, assignment


def test_calendar_and_facts_are_seeded_and_shared_by_facets(tmp_path):
    a, run_id, assignment = _planned(tmp_path / "a")
    _, _, repeated = _planned(tmp_path / "b")
    assert assignment == repeated
    _, _, different = _planned(tmp_path / "c", seed=26)
    assert assignment["calendar"] != different["calendar"]
    tasks = a.load_run(run_id)["tasks"]
    assert 300 <= assignment["calendar"]["current_year"] <= 3000
    assert tasks["S4.calendar_epoch"]["deps"] == ["S3.assign", "S4.calendar_name"]
    assert not any(task["type"] == "S4.facts" for task in tasks.values())
    entries = assignment["world_fact_tasks"]
    assert len({entry["id"] for entry in entries}) == len(entries)
    assert sum(entry["key"] == "place.highest_elevation" for entry in entries) == 1
    for world in assignment["world_tasks"]:
        body_id = f"S4.{'section' if world['kind'] == 'single' else 'item'}-{world['id']}"
        assert {f"S4.fact-{field['id']}" for field in description_entries(world, entries)} <= set(tasks[body_id]["deps"])
    previous = {}
    for entry in entries:
        task = tasks[f"S4.fact-{entry['id']}"]
        assert "S4.calendar_name" in task["deps"]
        if entry["section_id"] in previous:
            assert previous[entry["section_id"]] in task["deps"]
        previous[entry["section_id"]] = f"S4.fact-{entry['id']}"
        if entry["item_id"]:
            assert f"S4.item_name-{entry['item_id']}" in task["deps"]
        if entry["element"] == "year":
            assert entry["range"] == {"min": 1, "max": assignment["calendar"]["current_year"]}


def _assembled():
    fields = [
        {"id": "place.location_name", "key": "place.location_name", "element": "name", "label": "地名", "viewpoint": "地形", "section_id": "place", "section": {"name": "場"}, "item_id": None},
        {"id": "place.height", "key": "place.height", "element": "number", "label": "標高", "unit": "m", "viewpoint": "地形", "section_id": "place", "item_id": None},
        {"id": "place.founding", "key": "place.founding", "element": "year", "label": "開拓年", "viewpoint": "歴史", "section_id": "place", "item_id": None},
        {"id": "past_events-001-past_events.year", "key": "past_events.year", "element": "year", "label": "出来事の年", "viewpoint": "歴史", "section_id": "past_events", "item_id": "past_events-001"},
    ]
    assignment = {"calendar": {"current_year": 300}, "world_fact_tasks": fields,
                  "world_sections": [{"id": "place", "name": "場", "prerequisites": []}, {"id": "past_events", "name": "過去", "prerequisites": ["place"]}],
                  "world_tasks": [{"id": "place-1-f1", "section_id": "place", "kind": "single", "viewpoint": "地形"},
                                  {"id": "past_events-001", "section_id": "past_events", "kind": "list"}], "cast": []}
    outputs = {"S3.assign": assignment, "S4.calendar_name": {"name": "カナ暦", "reading": "カナ"},
               "S4.calendar_epoch": "共同体が水路を開いた。", "S4.fact-place.location_name": {"name": "カナ", "reading": "カナ"},
               "S4.fact-place.height": "200", "S4.fact-place.founding": "200",
               "S4.fact-past_events-001-past_events.year": "12", "S4.item_name-past_events-001": {"name": "リオ", "reading": "リオ"}}
    return assignment, outputs


def test_export_has_one_calendar_measurement_year_and_sorted_timeline():
    assignment, outputs = _assembled()
    markdown = render_world_facts_markdown(assignment, outputs)
    assert "暦：カナ暦" in markdown and "現在の年：300年" in markdown
    assert "| 標高 | 200.0 | m | 300 | カナ暦 |" in markdown
    assert markdown.index("| カナ暦 | 12 | リオ") < markdown.index("| カナ暦 | 200 |")
    del outputs["S4.fact-place.height"]
    with pytest.raises(ValueError, match="世界の事実がありません"):
        render_world_facts_markdown(assignment, outputs)


def test_glossary_ids_and_fact_context_are_section_scoped_and_survive_budget():
    assignment, outputs = _assembled()
    glossary = build_glossary(outputs)
    calendar = next(term for term in glossary if term["kind"] == "暦")
    assert calendar["id"] == glossary_id("S4.calendar_name", 0)
    own = related_glossary(glossary, assignment, {}, ["place.height"])
    assert [term["name"] for term in own] == ["カナ"]
    context = fact_source_context(assignment, outputs, {"type": "S4.section", "index": ["place-1-f1"]})
    assert context["own"] == [{"id": glossary_id("S4.fact-place.location_name", 0), "text": "地名：カナ"},
                              {"id": "place", "text": "標高：200.0 m"}]
    definition = _definition("S4.section")
    inputs = {"facts": context["own"], "section": {"id": "place", "name": "場", "definition": "場"},
              "viewpoint": "地形", "cut": {"id": "place:t1", "text": "川沿い"},
              "place": {"id": "place:t1", "text": "町"}, "era": {"id": "era:t1", "text": "開拓"},
              "plot_type": "旅", "calendar": "カナ暦", "current_year": 300,
              "prerequisite_sections": [{"body": "前提の描写。" * 250}] * 20, "glossary": own}
    fitted = prepare_task_inputs(definition, inputs)
    assert fitted["facts"] == inputs["facts"]
    assert glossary_id("S4.fact-place.location_name", 0) in input_source_ids(fitted)
    card = generate_task_card(definition, "ticket", inputs=fitted)
    assert "標高：200.0 m" in card and "S4" not in card


def test_legacy_s5_input_is_assembled_from_new_facts():
    assignment, outputs = _assembled()
    context = legacy_character_world_context(assignment, outputs)
    assert context[0]["facts"][0]["calendar"] == "カナ暦"
    assert context[0]["facts"][0]["value"] == 300
    assert all(row["facts"][0]["year"] == 300 for row in context)
    assert any(row["facts"][0]["value"] == 200 for row in context)
    assert all("registered_task" not in term for row in context for term in row["glossary"])


def test_future_year_ranges_and_cross_section_references(tmp_path):
    sections = [s["id"] for s in load_table("world_sections")["sections"]]
    orchestrator, run_id, assignment = _planned(tmp_path, sections=sections)
    tasks = orchestrator.load_run(run_id)["tasks"]
    year = assignment["calendar"]["current_year"]
    for field in assignment["world_fact_tasks"]:
        if field["section_id"] == "future" and field["element"] == "year":
            assert field["range"] == {"min": year + 1, "max": year + 1000}
    assert "S4.fact-place.highest_elevation" in tasks["S4.fact-social_structure.forest_area"]["deps"]


def _fact_range_harness(tmp_path, shape, limits):
    from storyteller.new_run import create_story_orchestrator
    from storyteller.orchestrator import CodeTaskResult, TaskSpec
    from storyteller.world_facts import build_world_fact_specs

    assignment, _ = _assembled()
    sound = {"set_id": "sound-01", "sounds": ["カ", "ナ", "リ", "オ", "セ", "ト"]}
    assignment["calendar"]["name_sound"] = sound
    assignment["world"] = {"place": {"id": "place:t1", "text": "水路の町"},
                           "era": {"id": "era:t1", "text": "開拓の後"}}
    assignment["world_fact_tasks"] = assignment["world_fact_tasks"][:2]
    for field in assignment["world_fact_tasks"]:
        field["section"] = {"id": "place", "name": "場", "definition": "水路の町"}
        field["name_sound"] = sound
    field = assignment["world_fact_tasks"][1]
    field.update(element=shape, range=limits)
    harness = create_story_orchestrator(tmp_path)

    def assign(context):
        return CodeTaskResult(assignment, add_tasks=build_world_fact_specs(context.task_id, assignment))

    harness.code_handlers["story_s3_assign"] = assign
    run_id = harness.create_run(seed=9, task_specs=[
        TaskSpec("S2.merge", "S2.merge"), TaskSpec("S3.assign", "S3.assign", deps=("S2.merge",)),
    ])
    harness._complete_task(run_id, "S2.merge", {"pools": {}})
    return harness, run_id


@pytest.mark.parametrize("shape,limits", [
    ("number", {"min": 10, "max": 5}),
    ("integer", {"min": 10, "max": 5}),
    ("year", {"min": 301, "max": 300}),
    ("integer", {"min": 1.5, "max": 1.5}),
])
def test_fact_bounds_are_checked_before_any_dynamic_task_is_created(tmp_path, shape, limits):
    harness, run_id = _fact_range_harness(tmp_path, shape, limits)
    manifest = harness.advance(run_id)
    assert manifest["tasks"]["S3.assign"]["state"] == "failed"
    assert "range:" in manifest["tasks"]["S3.assign"]["error"]
    assert not any(task_id.startswith("S4.") for task_id in manifest["tasks"])
    assert not any(path.name.startswith("S4.") for path in (harness.run_dir(run_id) / "tasks").iterdir())
    assert not (harness.task_dir(run_id, "S3.assign") / "output.json").exists()
    report = (harness.task_dir(run_id, "S3.assign") / "failure.md").read_text(encoding="utf-8")
    assert "range:" in report and "コードタスクのためカードはありません" in report


@pytest.mark.parametrize("shape,value", [("number", 42.5), ("integer", 42), ("year", 300)])
def test_fixed_fact_is_recorded_without_claim_and_keeps_sources(tmp_path, shape, value):
    from storyteller.task_outputs import read_task_output, task_sources

    harness, run_id = _fact_range_harness(tmp_path, shape, {"min": value, "max": value})
    harness.advance(run_id)
    for task_id in ("S4.calendar_name", "S4.fact-place.location_name"):
        claim = harness.claim_task(run_id, task_id, executor_id="test")
        assert harness.submit(claim["ticket"], '{"name":"カナ","reading":"カナ"}').accepted
    harness.advance(run_id)
    task = harness.load_run(run_id)["tasks"]["S4.fact-place.height"]
    assert task["state"] == "done" and task["tries"] == task["attempt"] == 0
    directory = harness.task_dir(run_id, "S4.fact-place.height")
    assert read_task_output(directory, "S4.fact") == str(value)
    assert glossary_id("S4.fact-place.location_name", 0) in task_sources(directory)
    assert not (directory / "claim.json").exists()
    assert not (directory / "attempts").exists()


@pytest.mark.parametrize("task_id", [
    "S4.calendar_name", "S4.calendar_epoch", "S4.fact-place.location_name", "S4.fact-place.height",
])
@pytest.mark.parametrize("before_claim", [False, True])
def test_calendar_and_specialized_fact_failures_have_redacted_cards(tmp_path, task_id, before_claim):
    harness, run_id = _fact_range_harness(tmp_path, "number", {"min": 1, "max": 300})
    if before_claim:
        task_type = task_id.split("-", 1)[0]
        harness.task_definitions[task_type]["max_input_chars"] = 1
    harness.advance(run_id)
    # Complete prerequisites directly so each failure path is tested in isolation.
    if task_id != "S4.calendar_name":
        harness._complete_task(run_id, "S4.calendar_name", {"name": "カナ暦", "reading": "カナ"})
    if task_id == "S4.fact-place.height":
        harness._complete_task(run_id, "S4.fact-place.location_name", {"name": "カナ", "reading": "カナ"})
    if before_claim:
        harness.advance(run_id)
    else:
        claim = harness.claim_task(run_id, task_id, executor_id="test-model")
        harness.record_executor_failure(claim["ticket"], "HTTP status 400: rejected", fatal=True)
    task = harness.load_run(run_id)["tasks"][task_id]
    assert task["state"] == "failed" and task["tries"] == int(not before_claim)
    report = (harness.task_dir(run_id, task_id) / "failure.md").read_text(encoding="utf-8")
    assert "[入力本文を伏せました:" in report
    assert "水路の町" not in report and "開拓の後" not in report and "カナ暦" not in report
    if task_id in {"S4.calendar_name", "S4.fact-place.location_name"}:
        assert '"reading"' in report and "次のJSONだけを出力" in report
    elif task_id == "S4.fact-place.height":
        assert "値だけを書く" in report


def test_real_harness_retry_preserves_prior_fact_and_other_work_after_restart(tmp_path):
    from storyteller.new_run import create_story_orchestrator
    from storyteller.orchestrator import TaskSpec
    from storyteller.task_outputs import read_task_output, task_sources
    from storyteller.world_facts import build_world_fact_specs

    assignment, _ = _assembled()
    sound = {"set_id": "sound-01", "description": "短い響き", "sounds": ["カ", "ナ", "リ", "オ", "セ", "ト"]}
    assignment["calendar"]["name_sound"] = sound
    assignment["world_fact_tasks"] = assignment["world_fact_tasks"][:3]
    for entry in assignment["world_fact_tasks"]:
        entry["section"] = {"id": "place", "name": "場", "definition": "水路の町"}
        if entry["element"] == "name":
            entry["name_sound"] = sound
        else:
            entry["range"] = {"min": 1, "max": 300}
    assignment["world"] = {"place": {"id": "place:t1", "text": "水路の町"},
                           "era": {"id": "era:t1", "text": "開拓の後"}}
    assignment["cast"] = [{"id": "c1", "role": "protagonist", "name_sound": sound}]
    harness = create_story_orchestrator(tmp_path)
    run_id = harness.create_run(seed=9, task_specs=[
        TaskSpec("S2.merge", "S2.merge"), TaskSpec("S3.assign", "S3.assign", deps=("S2.merge",)),
        TaskSpec("S5.name-c1", "S5.name", deps=("S3.assign",), index=("c1",)),
    ])
    harness._complete_task(run_id, "S2.merge", {"pools": {}})
    harness._complete_task(run_id, "S3.assign", assignment)
    harness._add_tasks(run_id, "S3.assign", build_world_fact_specs("S3.assign", assignment))
    claim = harness.claim_task(run_id, "S4.calendar_name", executor_id="test")
    assert harness.submit(claim["ticket"], '{"name":"カナ暦","reading":"カナ"}').accepted
    name_id = "S4.fact-place.location_name"
    claim = harness.claim_task(run_id, name_id, executor_id="test")
    invalid = harness.submit(claim["ticket"], '{"name":"カナ","reading":"ホホ"}')
    assert not invalid.accepted and any("uses_given" in error for error in invalid.errors)
    assert harness.load_run(run_id)["status"] == "active"
    claim = harness.claim_task(run_id, name_id, executor_id="test")
    assert "uses_given" in claim["card"]
    assert harness.submit(claim["ticket"], '{"name":"カナ","reading":"カナ"}').accepted
    name_path = harness.task_dir(run_id, name_id) / "output.json"
    accepted_name = name_path.read_text(encoding="utf-8")
    scalar_id = "S4.fact-place.height"
    claim = harness.claim_task(run_id, scalar_id, executor_id="test")
    assert "地名：カナ" in claim["card"] and "値だけを書く" in claim["card"]
    invalid = harness.submit(claim["ticket"], "450〜500")
    assert not invalid.accepted and harness.load_run(run_id)["status"] == "active"
    assert name_path.read_text(encoding="utf-8") == accepted_name
    harness = create_story_orchestrator(tmp_path)
    claim = harness.claim_task(run_id, "S5.name-c1", executor_id="other")
    assert harness.submit(claim["ticket"], '{"name":"リオ","reading":"リオ"}').accepted
    claim = harness.claim_task(run_id, scalar_id, executor_id="test")
    assert "前回の不合格理由" in claim["card"]
    assert harness.submit(claim["ticket"], "２００ m。").accepted
    assert read_task_output(harness.task_dir(run_id, scalar_id), "S4.fact") == "200"
    assert glossary_id(name_id, 0) in task_sources(harness.task_dir(run_id, scalar_id))
    assert name_path.read_text(encoding="utf-8") == accepted_name
    record = harness.load_run(run_id)["tasks"]
    assert record[scalar_id]["tries"] == 1 and record[name_id]["tries"] == 1
    assert record["S5.name-c1"]["state"] == "done"
    claim = harness.claim_task(run_id, "S4.fact-place.founding", executor_id="test")
    assert "地名：カナ" in claim["card"] and "標高：200.0 m" in claim["card"]


def test_every_list_viewpoint_can_be_recorded_and_item_card_fits_with_whole_lines():
    from storyteller.cards import input_char_count
    from storyteller.world_facts import fact_lines

    fields = [field for field in load_table("world_facts")["item_facts"] if field["section_id"] == "people"]
    entries = [{**field, "id": f"people-001-{field['key']}", "item_id": "people-001"} for field in fields]
    outputs = {"S4.item_name-people-001": {"name": "カナ", "reading": "カナ"},
               "S4.calendar_name": {"name": "リオ暦", "reading": "リオ"}}
    for entry in entries:
        outputs[f"S4.fact-{entry['id']}"] = ({"name": "カナ", "reading": "カナ"} if entry["element"] == "name"
                                           else "あ" * 40 if entry["element"] == "text" else "20")
    lines = fact_lines({}, outputs, entries)
    assert len(lines) == len(fields) == 74
    assert all(row["id"].startswith("g") for row in lines)
    prior = fact_lines({}, outputs, entries[:1], include_items=True)
    assert prior[0]["text"].startswith("カナ／")
    definition = _definition("S4.item")
    inputs = {"facts": lines, "name": "カナ", "section": {"id": "people", "name": "人々", "definition": "日々の暮らし"},
              "cut": {"id": "place:t1", "text": "水路"}, "place": {"id": "place:t1", "text": "町"},
              "era": {"id": "era:t1", "text": "開拓後"}, "plot_type": "旅", "calendar": "リオ暦", "current_year": 300}
    fitted = prepare_task_inputs(definition, inputs)
    assert fitted["facts"] and fitted["facts"] == lines[:len(fitted["facts"])]
    assert input_char_count(definition, fitted) <= definition["max_input_chars"]
    assert glossary_id("S4.item_name-people-001", 0) in input_source_ids(fitted)
    assert len(lines) == 74 and len(outputs) == 76
    card = generate_task_card(definition, "ticket", inputs=fitted)
    assert "S4" not in card and "事実と矛盾させない" in card


@pytest.mark.parametrize("budget", [600, 1200, 3000])
def test_bounded_ancestor_reads_preserve_fitted_card_and_attribution(tmp_path, monkeypatch, budget):
    from storyteller.world_facts import supplement_fact_outputs

    fields = [field for field in load_table("world_facts")["item_facts"] if field["section_id"] == "people"]
    entries = [{**field, "id": f"people-{item:03d}-{field['key']}", "item_id": f"people-{item:03d}"}
               for item in range(1, 11) for field in fields]
    current = {**fields[0], "id": "people-011-people.daily_action", "item_id": "people-011"}
    assignment = {"world_fact_tasks": [*entries, current],
                  "world_sections": [{"id": "people", "prerequisites": []}]}
    base = {"S3.assign": assignment, "S4.calendar_name": {"name": "カナ暦", "reading": "カナ"},
            "S4.item_name-people-011": {"name": "カナ", "reading": "カナ"}}
    stored = dict(base)
    manifest = {"tasks": {key: {"type": key.split("-", 1)[0], "state": "done", "deps": []} for key in base}}
    previous = "S3.assign"
    for entry in entries:
        task_id = f"S4.fact-{entry['id']}"
        name_id = f"S4.item_name-{entry['item_id']}"
        stored[name_id] = {"name": "カナ", "reading": "カナ"}
        manifest["tasks"][name_id] = {"type": "S4.item_name", "state": "done", "deps": []}
        stored[task_id] = ({"name": "カナ", "reading": "カナ"} if entry["element"] == "name"
                           else "具体的な作業を担当する" if entry["element"] == "text" else "20")
        manifest["tasks"][task_id] = {"type": "S4.fact", "state": "done", "deps": [previous, name_id]}
        previous = task_id
    task = {"type": "S4.fact", "index": [current["id"]], "deps": [*base, previous]}
    definition = {**_definition(), "max_input_chars": budget}
    reads = []

    def read(directory, _task_type):
        reads.append(directory.name)
        return stored[directory.name]

    monkeypatch.setattr("storyteller.task_outputs.read_task_output", read)
    limited = supplement_fact_outputs(tmp_path, manifest, task, base, definition=definition)
    assert len([task_id for task_id in reads if task_id.startswith("S4.fact-")]) < len(entries) / 2
    full_rows = fact_source_context(assignment, stored, task)["previous"]
    limited_rows = fact_source_context(assignment, limited, task)["previous"]
    inputs = {**_inputs("text"), "section": {"id": "people", "name": "人々", "definition": "日々の生活"},
              "viewpoint": "日々の担当作業", "name": {"name": "カナ", "reading": "カナ"},
              "referred_facts": [{"id": "place", "text": "距離：20 km"}],
              "glossary": [{"id": "g123456", "name": "カナ", "reading": "カナ", "kind": "人物", "definition": "住民"}] * 50}
    full = prepare_task_inputs(definition, {**inputs, "previous_facts": full_rows})
    bounded = prepare_task_inputs(definition, {**inputs, "previous_facts": limited_rows})
    assert full == bounded
    assert input_source_ids(full) == input_source_ids(bounded)
    assert generate_task_card(definition, "ticket", inputs=full) == generate_task_card(definition, "ticket", inputs=bounded)
    assert len(stored) > 740
