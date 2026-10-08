from __future__ import annotations

import json
import random
from copy import deepcopy
from pathlib import Path

import pytest

from storyteller.cards import generate_task_card, prepare_task_inputs
from storyteller.character_facts import (
    assign_character_fact_tasks, assemble_character_sheet, character_source_context,
    render_character_sheet, sheet_for_card, initial_character_specs,
)
from storyteller.glossary import glossary_id, build_glossary
from storyteller.new_run import create_story_orchestrator
from storyteller.orchestrator import TaskSpec
from storyteller.tables import load_table
from storyteller.validation import load_and_validate_yaml, validate_output, input_source_ids
from storyteller.world_facts import specialize_fact_definition
from tests.test_story_s3 import _run
from tests.test_story_s5 import _character, _s5_inputs

ROOT = Path(__file__).parents[1]


def _definition(task_type="S5.fact"):
    return load_and_validate_yaml(ROOT / f"harness/story/tasks/{task_type}.yaml",
                                  ROOT / "schemas/task-definition.schema.json")


def _world_output():
    return {"facts": [{"name": "カナ", "value": 200, "unit": "m", "year": 12,
                       "calendar": "開拓暦", "count": 1}],
            "glossary": [{"name": "カナ", "reading": "カナ", "kind": "地名",
                          "definition": "水路の端の集落。"}]}


def _glossary():
    return [{"id": glossary_id("S4.fact-place.location_name", 0), **_world_output()["glossary"][0]}]


def _output():
    place_id = _glossary()[0]["id"]
    return {"age": 30, "birth_year": 12, "calendar": "開拓暦", "height_cm": 170,
            "build": "細身", "birthplace": place_id, "residence": place_id,
            "occupation": "水路の点検係", "affiliation": None,
            "family": [], "timeline": [{"year": 12, "event": "集落で生まれた。"},
                                       {"year": 30, "event": "水路の点検を始めた。"}],
            "skills": ["流水の音から漏れを探す。"]}



def _inputs(shape="integer", **extra):
    return {"label": "年齢", "shape": shape, "bounds": {"min": 0, "max": 1000},
            "name": "リオ", "character": _character("protagonist", "c1", "本人"),
            "calendar": "開拓暦", "current_year": 300, **extra}


@pytest.mark.parametrize("raw,expected", [("約３０歳。", "30"), ("１,０００", "1000"), ("0", "0")])
def test_age_value_parsing(raw, expected):
    result = validate_output(_definition(), raw, _inputs(unit="歳"), harness_root=ROOT)
    assert result.passed, result.errors
    assert result.value == expected


@pytest.mark.parametrize("raw", ["-1", "1001", "30.5", "20〜30", "成人", ""])
def test_age_rejects_invalid_values(raw):
    assert not validate_output(_definition(), raw, _inputs(), harness_root=ROOT).passed


@pytest.mark.parametrize("label,limit", [("体格", 40), ("続柄", 10), ("年表の出来事", 60)])
def test_field_text_lengths(label, limit):
    inputs = _inputs("text", label=label, max_chars=limit)
    assert validate_output(_definition(), "あ" * limit, inputs, harness_root=ROOT).passed
    assert not validate_output(_definition(), "あ" * (limit + 1), inputs, harness_root=ROOT).passed


def test_choices_validate_ids_shown_on_card():
    inputs = _inputs("choice", choices=[{"id": "g123456", "name": "カナ"}, {"id": "none", "name": "なし"}])
    for value in ("g123456", "none"):
        assert validate_output(_definition(), value, inputs, harness_root=ROOT).passed
    for value in ("カナ", "g000000", "g123456 none"):
        assert not validate_output(_definition(), value, inputs, harness_root=ROOT).passed
    card = generate_task_card(_definition(), "ticket", inputs)
    assert "値だけを書く" in card and "S5" not in card


@pytest.mark.parametrize("count", [8, 160])
def test_large_world_context_keeps_identical_fitted_inputs(count):
    from storyteller.orchestrator import _prepare_card_inputs
    from storyteller.world_facts import over_budget_fact_tail

    definition = {**_definition(), "max_input_chars": 1200}
    inputs = _inputs(world_facts=[{"id": f"g{number:04}", "text": "カ\u3099の水路の点検を行う。" * 2}
                                 for number in range(count)],
                     glossary=[{"id": "g0000", "name": "カナ", "definition": "水路の集落。"}],
                     previous_facts=[{"id": "c1", "text": "年齢：30歳"}])
    expected = prepare_task_inputs(definition, inputs=inputs)
    assert _prepare_card_inputs(definition, inputs) == expected
    tail = over_budget_fact_tail(inputs["world_facts"], definition["max_input_chars"])
    assert len(tail) <= count
    if count == 160:
        assert len(tail) < count
        assert tail[-1] == inputs["world_facts"][-1]


def test_world_context_without_tail_truncation_still_exceeds_budget():
    from storyteller.orchestrator import _prepare_card_inputs
    from storyteller.cards import InputBudgetError

    definition = deepcopy(_definition())
    definition["inputs"]["world_facts"]["truncate"] = "none"
    inputs = _inputs(world_facts=[{"id": "people", "text": "あ" * 40}] * 100)
    with pytest.raises(InputBudgetError):
        _prepare_card_inputs(definition, inputs)


def test_seeded_counts_order_and_independent_character_chains(tmp_path):
    harness, run_id = _run(tmp_path, 26, run_number=1)
    tasks = harness.load_run(run_id)["tasks"]
    assignment = json.loads((harness.task_dir(run_id, "S3.assign") / "output.json").read_text(encoding="utf-8"))
    entries = assignment["character_fact_tasks"]
    assert not any(task["type"] == "S5.facts" for task in tasks.values())
    for person in assignment["cast"]:
        fields = [entry for entry in entries if entry["person_id"] == person["id"]]
        assert fields[0]["key"] == "age"
        assert len([entry for entry in fields if entry["key"].startswith("skills.")]) == 3
        assert len([entry for entry in fields if entry["key"].endswith(".year")]) == 4
        assert 0 <= len([entry for entry in fields if entry["key"].endswith(".name")]) <= 3
        age = tasks[f"S5.fact-{person['id']}-age"]
        assert f"S5.name-{person['id']}" in age["deps"]
        assert "S4.calendar_name" in age["deps"]
        assert not any(dep.startswith("S5.fact-c2-") for dep in age["deps"]) if person["id"] == "c1" else True
        assert {f"S5.fact-{entry['id']}" for entry in fields} <= set(tasks[f"S5.profile-{person['id']}"]["deps"])
    names = load_table("name_sounds")["sets"]
    assert assign_character_fact_tasks(assignment["cast"], names, random.Random(1), ROOT) == assign_character_fact_tasks(assignment["cast"], names, random.Random(1), ROOT)


def _harness(tmp_path, *, age=30, with_place=True):
    harness = create_story_orchestrator(tmp_path)
    harness.task_definitions["S3.assign"]["inputs"] = {}
    harness.task_definitions["S4.fact"]["inputs"] = {}
    person = _character("protagonist", "c1", "本人")
    person["name_sound"] = {"set_id": "sound-01", "description": "短い響き", "sounds": ["カ", "ナ", "リ", "オ", "セ", "ト"]}
    assignment = {"cast": [person], "calendar": {"current_year": 300}, "world_tasks": [],
                  "world_sections": [{"id": "place", "name": "場", "prerequisites": []}],
                  "world_fact_tasks": []}
    if with_place:
        assignment["world_fact_tasks"] = [{"id": "place.location_name", "key": "place.location_name", "element": "name", "section_id": "place", "section": {"name": "場"}, "label": "主要地点の名前"}]
    assignment["character_fact_tasks"] = assign_character_fact_tasks([person], load_table("name_sounds")["sets"], random.Random(5), ROOT)
    specs = [TaskSpec("S3.assign", "S3.assign"), TaskSpec("S5.name-c1", "S5.name", deps=("S3.assign",), index=("c1",)), TaskSpec("S4.calendar_name", "S4.calendar_name", deps=("S3.assign",))]
    if with_place:
        specs.append(TaskSpec("S4.fact-place.location_name", "S4.fact", deps=("S3.assign",), index=("place.location_name",)))
    run_id = harness.create_run(seed=9, task_specs=specs)
    harness._complete_task(run_id, "S3.assign", assignment)
    harness._complete_task(run_id, "S4.calendar_name", {"name": "カナ暦", "reading": "カナ"})
    harness._complete_task(run_id, "S5.name-c1", {"name": "リオ", "reading": "リオ"})
    if with_place:
        harness._complete_task(run_id, "S4.fact-place.location_name", {"name": "カナ", "reading": "カナ"})
    harness._add_tasks(run_id, "S3.assign", initial_character_specs("S3.assign", "c1", assignment))
    claim = harness.claim_task(run_id, "S5.fact-c1-age", executor_id="test")
    assert harness.submit(claim["ticket"], str(age)).accepted
    harness.advance(run_id)
    return harness, run_id, assignment


@pytest.mark.parametrize("age", [0, 30, 1000])
def test_year_bounds_precomputed_after_age_and_sheet_is_assembled(tmp_path, age):
    from storyteller.task_outputs import read_task_output
    harness, run_id, assignment = _harness(tmp_path, age=age)
    plan = read_task_output(harness.task_dir(run_id, "S5.fact_plan-c1"), "S5.fact_plan")
    assert plan["birth_year"] == 300 - age
    for entry in plan["facts"]:
        if entry["key"].endswith(".year"):
            assert entry["range"] == {"min": 300 - age, "max": 300}
    tasks = harness.load_run(run_id)["tasks"]
    previous = "S5.fact-c1-age"
    for entry in plan["facts"][1:]:
        task_id = f"S5.fact-{entry['id']}"
        assert previous in tasks[task_id]["deps"]
        previous = task_id
    _finish(harness, run_id)
    outputs = _accepted_outputs(harness, run_id)
    sheet = assemble_character_sheet(outputs, "c1")
    assert sheet["age"] == age and sheet["birth_year"] == 300 - age
    assert len(sheet["timeline"]) == 4 and len(sheet["skills"]) == 3
    assert sheet["affiliation"] is None
    assert sheet["timeline"] == sorted(sheet["timeline"], key=lambda row: row["year"])
    if age == 0:
        tasks = harness.load_run(run_id)["tasks"]
        for entry in plan["facts"]:
            if entry["key"].endswith(".year"):
                task_id = f"S5.fact-{entry['id']}"
                assert tasks[task_id]["attempt"] == tasks[task_id]["tries"] == 0
                assert not (harness.task_dir(run_id, task_id) / "claim.json").exists()


def _finish(harness, run_id):
    while True:
        manifest = harness.advance(run_id)
        ready = [(task_id, task) for task_id, task in manifest["tasks"].items() if task["kind"] == "llm" and task["state"] == "ready"]
        if not ready:
            assert all(task["state"] == "done" for task in manifest["tasks"].values()), manifest
            return
        task_id, task = ready[0]
        claim = harness.claim_task(run_id, task_id, executor_id="test")
        inputs = json.loads((harness.task_dir(run_id, task_id) / "input.json").read_text(encoding="utf-8"))
        if inputs["shape"] == "name":
            reading = "".join(inputs["name_sound"]["sounds"][:2])
            value = json.dumps({"name": reading, "reading": reading}, ensure_ascii=False)
        elif inputs["shape"] == "choice":
            value = inputs["choices"][-1]["id"]
        elif inputs["shape"] in {"integer", "number"}:
            value = str(inputs.get("bounds", {}).get("max", 170))
        else:
            value = "母" if inputs["label"] == "続柄" else "水路を点検する"
        result = harness.submit(claim["ticket"], value)
        assert result.accepted, result.errors


def _accepted_outputs(harness, run_id):
    from storyteller.task_outputs import read_task_output
    return {task_id: read_task_output(harness.task_dir(run_id, task_id), task["type"])
            for task_id, task in harness.load_run(run_id)["tasks"].items() if task["state"] == "done"}


def test_no_location_falls_back_to_one_registered_name_then_choice(tmp_path):
    harness, run_id, _ = _harness(tmp_path, with_place=False)
    _finish(harness, run_id)
    outputs = _accepted_outputs(harness, run_id)
    sheet = assemble_character_sheet(outputs, "c1")
    assert sheet["birthplace"] == sheet["residence"] == glossary_id("S5.fact-c1-birthplace", 0)
    glossary = build_glossary(outputs)
    assert any(entry["id"] == sheet["birthplace"] and entry["kind"] == "地名" for entry in glossary)
    for member in sheet["family"]:
        assert any(entry["name"] == member["name"] and entry["kind"] == "人物" for entry in glossary)
    card = json.loads((harness.task_dir(run_id, "S5.fact-c1-residence") / "input.json").read_text(encoding="utf-8"))
    assert card["shape"] == "choice" and card["choices"][0]["name"]


def test_sheet_and_referenced_terms_survive_description_budget():
    sheet = sheet_for_card(_output(), _glossary())
    character = _character("protagonist", "c1", "本人")
    for task_type in ("profile", "intro", "appearance", "personality", "values", "backstory", "relationship", "voice", "inner_conflict", "motive", "catchphrase"):
        definition = _definition(f"S5.{task_type}")
        inputs = {**_s5_inputs("リオ", character), "facts": sheet,
                  "profile": "長いプロフィール。" * 200,
                  "names": [{"name": "相手名" * 500}],
                  "other_person": {"c2": {"id": "c2", "name": "相手", "role": "supporter", "intro": "紹介。"}}}
        fitted = prepare_task_inputs(definition, inputs)
        assert fitted["facts"] == sheet
        assert _glossary()[0]["id"] in input_source_ids(fitted)
        card = generate_task_card(definition, "ticket", fitted)
        assert "水路の端の集落。" in card and "事実のシート" in card and "矛盾させない" in card


def test_character_sheet_renders_units_sorted_dates_and_safe_cells():
    output = _output()
    output["family"] = [{"relationship": "母", "name": "カナ"}]
    output["timeline"].reverse()
    output["occupation"] = "点検|係\n水路"
    markdown = render_character_sheet(output, _glossary())
    assert "| 身長（cm） | 170 |" in markdown and "| 出身地 | カナ |" in markdown
    assert "| 母 | カナ |" in markdown and "点検\\|係 水路" in markdown
    assert markdown.index("| 開拓暦 | 12 |") < markdown.index("| 開拓暦 | 30 |")


def test_retry_survives_restart_and_preserves_accepted_age(tmp_path):
    from storyteller.task_outputs import read_task_output
    harness, run_id, _ = _harness(tmp_path)
    age_path = harness.task_dir(run_id, "S5.fact-c1-age") / "output.md"
    age_before = age_path.read_text(encoding="utf-8")
    task_id = "S5.fact-c1-height_cm"
    claim = harness.claim_task(run_id, task_id, executor_id="test")
    assert "年齢：30 歳" in claim["card"] and "生年：270 年" in claim["card"]
    result = harness.submit(claim["ticket"], "170〜180")
    assert not result.accepted
    assert harness.load_run(run_id)["status"] == "active"
    harness = create_story_orchestrator(tmp_path)
    claim = harness.claim_task(run_id, task_id, executor_id="test")
    assert "前回の不合格理由" in claim["card"] and "値を1つだけ書く" in claim["card"]
    assert harness.submit(claim["ticket"], "約１７０cm。").accepted
    assert read_task_output(harness.task_dir(run_id, task_id), "S5.fact") == "170"
    assert age_path.read_text(encoding="utf-8") == age_before


@pytest.mark.parametrize("before_claim", [False, True])
def test_failure_report_redacts_inputs_and_uses_specialized_value_card(tmp_path, before_claim):
    harness, run_id, _ = _harness(tmp_path)
    task_id = "S5.fact-c1-height_cm"
    if before_claim:
        harness.task_definitions["S5.fact"]["max_input_chars"] = 1
        harness.advance(run_id)
    else:
        claim = harness.claim_task(run_id, task_id, executor_id="test-model")
        harness.record_executor_failure(claim["ticket"], "HTTP status 400: rejected", fatal=True)
    task = harness.load_run(run_id)["tasks"][task_id]
    assert task["state"] == "failed"
    from storyteller.cli import _run_status
    status = _run_status(tmp_path, run_id)
    assert next(record for record in status["tasks"] if record["task_id"] == task_id)["failure_report"].endswith("failure.md")
    report = (harness.task_dir(run_id, task_id) / "failure.md").read_text(encoding="utf-8")
    assert "[入力本文を伏せました:" in report and "値だけを書く" in report
    assert "カナ暦" not in report and "年齢：30" not in report and "本人" not in report
    assert harness.load_run(run_id)["tasks"]["S5.fact-c1-age"]["state"] == "done"


def test_invalid_bounds_fail_planner_before_creating_any_post_age_task(tmp_path, monkeypatch):
    import storyteller.character_facts as facts
    original = facts.assign_character_fact_tasks

    def defective(*args, **kwargs):
        entries = original(*args, **kwargs)
        next(entry for entry in entries if entry["key"] == "height_cm")["range"] = {"min": 200, "max": 100}
        return entries

    monkeypatch.setattr("tests.test_character_facts.assign_character_fact_tasks", defective)
    harness, run_id, _ = _harness(tmp_path)
    tasks = harness.load_run(run_id)["tasks"]
    assert tasks["S5.fact_plan-c1"]["state"] == "failed"
    assert "range" in tasks["S5.fact_plan-c1"]["error"]
    assert "S5.fact-c1-height_cm" not in tasks
    assert not harness.task_dir(run_id, "S5.fact-c1-height_cm").exists()


@pytest.mark.parametrize("raw,passed", [("269", False), ("270", True), ("300", True), ("301", False)])
def test_timeline_year_uses_birth_and_current_year_bounds(raw, passed):
    result = validate_output(_definition(), raw,
                             _inputs(bounds={"min": 270, "max": 300}, unit="年"), harness_root=ROOT)
    assert result.passed is passed


def test_family_counts_cover_zero_to_three_and_characters_have_separate_chains():
    cast = [_character("protagonist", "c1", "本人"), _character("supporter", "c2", "相手")]
    name_sets = load_table("name_sounds")["sets"]
    counts = set()
    for seed in range(20):
        entries = assign_character_fact_tasks(cast, name_sets, random.Random(seed), ROOT)
        for person in cast:
            counts.add(sum(entry["person_id"] == person["id"] and entry["key"].endswith(".name") for entry in entries))
    assert counts == {0, 1, 2, 3}
    assignment = {"cast": cast, "character_fact_tasks": entries, "world_sections": [], "world_fact_tasks": []}
    for person in cast:
        specs = initial_character_specs("S3.assign", person["id"], assignment)
        assert all(not any(dep.startswith(f"S5.fact-{other['id']}-")
                           for dep in spec.deps) for spec in specs for other in cast if other != person)
        if person["id"] != "c1":
            assert all("S5.name-c1" in spec.deps for spec in specs)


def test_real_description_card_recovers_calendar_and_all_referenced_names(tmp_path):
    harness, run_id, assignment = _harness(tmp_path)
    _finish(harness, run_id)
    facts = [f"S5.fact-{entry['id']}" for entry in assignment["character_fact_tasks"]]
    harness._add_tasks(run_id, "S3.assign", [
        TaskSpec("S5.profile-c1", "S5.profile", deps=("S3.assign", "S5.name-c1", *facts), index=("c1",))
    ])
    claim = harness.claim_task(run_id, "S5.profile-c1", executor_id="test")
    inputs = json.loads((harness.task_dir(run_id, "S5.profile-c1") / "input.json").read_text(encoding="utf-8"))
    assert inputs["role_definition"] == assignment["cast"][0]["role_definition"]
    assert inputs["plot_requirements"] == assignment["cast"][0]["plot_context"]
    assert all(key not in inputs["character"] for key in ("name_sound", "role_definition", "plot_context"))
    from storyteller.cards import input_char_count
    definition = harness.task_definitions["S5.profile"]
    assert definition["max_input_chars"] - input_char_count(definition, inputs) >= 1000
    sheet = inputs["facts"]
    assert sheet["calendar"] == "カナ暦" and sheet["birth_year"] == 270
    assert _glossary()[0]["id"] in {entry["id"] for entry in sheet["glossary"]}
    assert all(any(entry["name"] == member["name"] for entry in sheet["glossary"]) for member in sheet["family"])
    assert "主要地点の名前" in claim["card"] and "S5" not in claim["card"]


def test_fact_card_uses_own_character_and_only_protagonist_name_and_role():
    from storyteller.orchestrator import _source_outputs

    cast = [_character("protagonist", "c1", "本人"), _character("supporter", "c2", "相手")]
    assignment = {"cast": cast, "calendar": {"current_year": 300},
                  "character_fact_tasks": assign_character_fact_tasks(
                      cast, load_table("name_sounds")["sets"], random.Random(5), ROOT)}
    outputs = {"S3.assign": assignment, "S4.calendar_name": {"name": "開拓暦", "reading": "カナ"},
               "S5.name-c1": {"name": "リオ", "reading": "リオ"},
               "S5.name-c2": {"name": "セト", "reading": "セト"}}
    manifest = {"tasks": {key: {"type": key.split("-", 1)[0], "state": "done",
                                "index": [key.split("-", 1)[1]] if "-" in key else []}
                           for key in outputs}}
    task = {"type": "S5.fact", "index": ["c2-age"], "deps": list(outputs)}
    definition = _definition()
    sources = _source_outputs(manifest, task, definition, outputs)
    inputs = prepare_task_inputs(definition, outputs=sources, run_input={}, index=task["index"])
    assert inputs["name"] == "セト" and inputs["protagonist_name"] == "リオ"
    assert inputs["protagonist_role"] == "protagonist"
    assert inputs["character"] == {key: value for key, value in cast[1].items() if key != "name_sound"}
    card = generate_task_card(definition, "ticket", inputs)
    assert "supporterの定義" in card and "主人公と旅を支える人物" in card
    assert "本人の禁忌" not in card and "本人の抑圧された自己像" not in card


@pytest.mark.parametrize("item", ["profile", "appearance", "personality", "values", "backstory",
                                 "relationship", "voice", "inner_conflict", "intro", "motive", "catchphrase"])
def test_complete_sheet_fits_description_inputs_without_truncating_facts(item):
    from storyteller.cards import input_char_count
    from storyteller.orchestrator import _prepare_card_inputs
    from storyteller.character_facts import character_card_view

    character = _character("protagonist", "c1", "本人")
    for element in character["elements"].values():
        element["text"] = "あ" * 40
    sheet = _output()
    sheet["build"] = sheet["occupation"] = "あ" * 40
    sheet["timeline"] = [{"year": year, "event": "あ" * 60} for year in (12, 20, 30, 42)]
    sheet["skills"] = ["あ" * 40] * 3
    sheet["family"] = [{"relationship": "あ" * 10, "name": f"カナ{number}"} for number in range(3)]
    glossary = [*_glossary(), *[
        {"id": glossary_id(f"S5.fact-c1-family.{number}.name", 0), "name": member["name"],
         "reading": "カナ", "kind": "人物", "definition": "c1の家族の名前"}
        for number, member in enumerate(sheet["family"], 1)
    ]]
    sheet = sheet_for_card(sheet, glossary)
    inputs = {**_s5_inputs("リオ", character), "facts": sheet, "glossary": glossary,
              "profile": "あ" * 1500, "other_person": {"c2": {"name": "カナ", "role": "supporter", "intro": "あ" * 50}}}
    inputs["character"] = character_card_view(character, description=True)
    definition = _definition(f"S5.{item}")
    prepared = _prepare_card_inputs(definition, inputs)
    assert prepared["facts"] == sheet
    assert prepared["role_definition"] == character["role_definition"]
    assert prepared["plot_requirements"] == character["plot_context"]
    assert all(key not in prepared["character"] for key in ("name_sound", "role_definition", "plot_context"))
    used = input_char_count(definition, prepared)
    assert used <= definition["max_input_chars"]
