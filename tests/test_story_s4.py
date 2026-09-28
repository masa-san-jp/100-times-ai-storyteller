from __future__ import annotations

from pathlib import Path

from storyteller.cards import generate_task_card
from storyteller.orchestrator import _source_outputs
from storyteller.selectors import resolve_inputs
from storyteller.validation import load_and_validate_yaml, validate_document


ROOT = Path(__file__).parents[1]
TASK_SCHEMA = ROOT / "schemas" / "task-definition.schema.json"


def _load_definition(name: str) -> dict[str, object]:
    return load_and_validate_yaml(
        ROOT / "harness" / "story" / "tasks" / f"{name}.yaml",
        TASK_SCHEMA,
    )


def _assignment() -> dict[str, object]:
    return {
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
    }


def test_s4_task_definitions_and_output_schemas_are_valid() -> None:
    section = _load_definition("S4.section")
    item = _load_definition("S4.item")

    assert section["validate"]["schema"] == "schemas/tasks/S4.section.schema.json"
    assert item["validate"]["schema"] == "schemas/tasks/S4.item.schema.json"
    assert section["card"]["output_example"] == (
        '{"body": "...", "sources": ["place:t1"]}'
    )
    assert item["card"]["output_example"] == (
        '{"name": "...", "body": "...", "sources": ["place:t1"]}'
    )

    validate_document(
        {"body": "水路と境界の規則。", "sources": ["place:t1"]},
        ROOT / "schemas" / "tasks" / "S4.section.schema.json",
    )
    validate_document(
        {"name": "水門守", "body": "水門を見張る集団。", "sources": ["place:t1"]},
        ROOT / "schemas" / "tasks" / "S4.item.schema.json",
    )


def test_s4_card_contains_only_the_current_section_viewpoints() -> None:
    definition = _load_definition("S4.section")
    card = generate_task_card(
        definition,
        "ticket",
        outputs={"S3.assign": _assignment()},
        index=("place",),
    )

    assert "水路の流れ" in card
    assert "境界の規則" in card
    assert "固有の別観点" not in card


def test_s4_list_prerequisites_are_name_and_one_line_summaries() -> None:
    definition = _load_definition("S4.item")
    assignment = _assignment()
    manifest = {
        "tasks": {
            "S3.assign": {"type": "S3.assign", "state": "done"},
            "S4.item-people-001": {"type": "S4.item", "state": "done"},
        }
    }
    task = {
        "deps": ["S3.assign", "S4.item-people-001"],
        "index": ["future", "001"],
    }
    dependency_outputs = {
        "S3.assign": assignment,
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
    assert "記録係" in card
    assert "二行目は渡さない。" in card
    assert "sources" not in card.split("### 前提一覧項目", 1)[1].split("## 手順", 1)[0]
