from __future__ import annotations

import json
from pathlib import Path

from storyteller.cards import generate_task_card, input_char_count
from storyteller.orchestrator import _source_outputs
from storyteller.selectors import resolve_inputs
from storyteller.validation import load_and_validate_yaml, validate_document, validate_output


ROOT = Path(__file__).parents[1]
TASK_SCHEMA = ROOT / "schemas" / "task-definition.schema.json"


def _load_definition(name: str) -> dict[str, object]:
    return load_and_validate_yaml(
        ROOT / "harness" / "story" / "tasks" / f"{name}.yaml",
        TASK_SCHEMA,
    )


def _assignment() -> dict[str, object]:
    return {
        "calendar": {"current_year": 300},
        "world_fact_tasks": [],
        "threads": [{"plot_type": "quest", "plot_type_name": "旅（クエスト）"}],
        "world": {
            "place": {"id": "place:t1", "text": "水路の町"},
            "era": {"id": "era:t1", "text": "長い停電の後"},
        },
        "world_sections": [
            {
                "id": "place",
                "name": "場の描写",
                "definition": "人物が行動する場所を描く。",
                "viewpoints": ["水路の流れ", "境界の規則"],
                "level": 0,
                "kind": "single",
                "max_chars": 400,
                "prerequisites": [],
            },
            {
                "id": "customs",
                "name": "生活風習",
                "definition": "共有される習慣を描く。",
                "viewpoints": ["固有の別観点"],
                "level": 1,
                "kind": "single",
                "max_chars": 400,
                "prerequisites": ["place"],
            },
            {
                "id": "future",
                "name": "未来のシナリオ",
                "definition": "未来を描く。",
                "viewpoints": ["時間の地平"],
                "level": 4,
                "kind": "list",
                "max_chars": 400,
                "prerequisites": ["people"],
            },
        ],
        "world_tasks": [
            {
                "id": "place-1-f1",
                "section_id": "place",
                "kind": "single",
                "section": {
                    "id": "place",
                    "name": "場の描写",
                    "definition": "人物が行動する場所を描く。",
                },
                "viewpoint": "水路の流れ",
                "facet_number": 1,
                "element_axis": "place",
                "element": {"id": "place:t1", "text": "水路の町"},
            },
            {
                "id": "future-001",
                "section_id": "future",
                "kind": "list",
                "section": {
                    "id": "future",
                    "name": "未来のシナリオ",
                    "definition": "未来を描く。",
                },
                "element_axis": "era",
                "element": {"id": "era:t1", "text": "長い停電の後"},
                "name_sound": {
                    "set_id": "sound-01",
                    "description": "短い響き",
                    "sounds": ["カ", "ナ", "リ", "オ", "セ", "ト"],
                },
            },
        ],
    }


def test_s4_task_definitions_and_output_schemas_are_valid() -> None:
    section = _load_definition("S4.section")
    item = _load_definition("S4.item")

    for definition in (section, item):
        assert "物語の place・era の要素や前提セクションの文を言い換えて書き始めない。観点と切り口から書き始める。" in definition["card"]["steps"]
        assert definition["output"] == "text"
        assert definition["extend_to_min"] is True
        assert "schema" not in definition["validate"]
        assert "output_example" not in definition["card"]
        assert "sources" not in str(definition["card"])
    name = _load_definition("S4.item_name")
    assert name["output"] == "json"
    validate_document({"name": "カナ", "reading": "カナ"}, ROOT / name["validate"]["schema"])


def test_s4_card_contains_only_the_current_section_viewpoints() -> None:
    definition = _load_definition("S4.section")
    card = generate_task_card(
        definition,
        "ticket",
        outputs={"S3.assign": _assignment(), "S4.fact": {"own": []}, "S4.calendar_name": {"name": "カナ暦"}},
        index=("place-1-f1",),
    )

    assert "水路の流れ" in card
    assert "境界の規則" not in card
    assert "固有の別観点" not in card


def test_s4_list_prerequisites_are_name_and_one_line_summaries() -> None:
    definition = _load_definition("S4.item")
    assignment = _assignment()
    manifest = {
        "tasks": {
            "S3.assign": {"type": "S3.assign", "state": "done"},
            "S4.calendar_name": {"type": "S4.calendar_name", "state": "done"},
            "S4.item-people-001": {"type": "S4.item", "state": "done"},
            "S4.item_name-future-001": {"type": "S4.item_name", "state": "done"},
            "S4.fact-future-001-future.year": {"type": "S4.fact", "state": "done"},
        }
    }
    task = {
        "type": "S4.item",
        "deps": ["S3.assign", "S4.calendar_name", "S4.item-people-001", "S4.item_name-future-001", "S4.fact-future-001-future.year"],
        "index": ["future-001"],
    }
    dependency_outputs = {
        "S3.assign": assignment,
        "S4.calendar_name": {"name": "カナ暦"},
        "S4.fact-future-001-future.year": "301",
        "S4.item_name-future-001": {"name": "カナ", "reading": "カナ"},
        "S4.item-people-001": {
            "name": "記録係",
            "body": "最初の行。\n二行目は渡さない。",
            "sources": ["place:t1"],
        },
    }
    source_outputs = _source_outputs(manifest, task, definition, dependency_outputs)
    inputs = resolve_inputs(definition, source_outputs, index=task["index"])
    card = generate_task_card(definition, "ticket", inputs=inputs)

    assert inputs["prerequisite_items"] == [{"name": "記録係", "summary": "最初の行。 二行目は渡さない。"}]
    assert input_char_count(definition, inputs) <= definition["max_input_chars"]
    assert "記録係" in card
    assert "二行目は渡さない。" in card
    assert "sources" not in card.split("### 前提一覧項目", 1)[1].split("## 手順", 1)[0]


def test_s4_item_name_validation_requires_two_given_sounds() -> None:
    definition = _load_definition("S4.item_name")
    assignment = _assignment()
    inputs = resolve_inputs(
        definition,
        {"S3.assign": assignment},
        index=("future-001",),
    )

    valid = validate_output(
        definition,
        json.dumps(
            {"name": "表記", "reading": "カナ"},
            ensure_ascii=False,
        ),
        inputs,
        harness_root=ROOT,
        index=("future-001",),
    )
    invalid = validate_output(
        definition,
        json.dumps(
            {"name": "表記", "reading": "カ"},
            ensure_ascii=False,
        ),
        inputs,
        harness_root=ROOT,
        index=("future-001",),
    )

    assert valid.passed
    assert not invalid.passed
    assert any("uses_given" in error for error in invalid.errors)
