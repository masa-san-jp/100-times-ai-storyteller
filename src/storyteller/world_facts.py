"""World fact task planning, schema validation and deterministic export."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any


def facts_for_card(task_id: str, output: Mapping[str, Any]) -> dict[str, Any]:
    """Keep each sheet's registered terms beside its required numeric facts."""
    from .glossary import glossary_id

    return {
        **output,
        "glossary": [
            {**entry, "id": glossary_id(task_id, ordinal)}
            for ordinal, entry in enumerate(output.get("glossary", []), start=1)
        ],
    }


def validate_world_facts(output: Mapping[str, Any], inputs: Mapping[str, Any]) -> list[str]:
    """Apply the viewpoint schema and the existing sound check to each name."""
    from .validation import YamlValidationError, validate_document, validate_output

    errors: list[str] = []
    try:
        validate_document(output, inputs["fact_schema"])
    except YamlValidationError as error:
        errors.append(f"fact_schema: {error}")
    if isinstance(output, Mapping) and isinstance(output.get("glossary"), list):
        sound_check = {"output": "json", "validate": {"checks": [
            {"uses_given": {"field": "reading", "slot": "name_sound", "n": 2}},
        ]}}
        for entry in output["glossary"]:
            result = validate_output(sound_check, json.dumps(entry, ensure_ascii=False), inputs)
            errors.extend(result.errors)
    return errors


def assign_world_fact_tasks(
    sections: Sequence[Mapping[str, Any]],
    world_tasks: Sequence[Mapping[str, Any]],
    name_sets: Sequence[Mapping[str, Any]],
    random_source: Any,
) -> list[dict[str, Any]]:
    """One fact sheet per single viewpoint or per list item, not per facet."""
    result: list[dict[str, Any]] = []
    for section in sections:
        if section["kind"] == "single":
            entries = [(f"{section['id']}-{number}", viewpoint)
                       for number, viewpoint in enumerate(section["viewpoints"], 1)]
        else:
            entries = [(task["id"], "／".join(section["viewpoints"]))
                       for task in world_tasks if task["section_id"] == section["id"]]
        for key, viewpoint in entries:
            sound = random_source.choice(name_sets)
            schema_key = viewpoint if section["kind"] == "single" else section["viewpoints"][0]
            result.append({
                "id": key,
                "section_id": section["id"],
                "section": {field: section[field] for field in ("id", "name", "definition")},
                "viewpoint": viewpoint,
                "fact_schema": deepcopy(section["fact_schema"][schema_key]),
                "name_sound": {
                    "set_id": sound["id"], "description": sound["description"],
                    "sounds": random_source.sample(sound["sounds"], 6),
                },
            })
    return result


def fact_key(world_task: Mapping[str, Any]) -> str:
    return world_task["id"].rsplit("-f", 1)[0] if world_task["kind"] == "single" else world_task["id"]


def render_world_facts_markdown(
    assignment: Mapping[str, Any], outputs: Mapping[str, Any],
) -> str:
    """Render each fact sheet once, with numeric tables and a dated timeline."""
    def cell(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")

    lines = ["# 世界の事実", ""]
    for task in assignment.get("world_fact_tasks", []):
        output = outputs.get(f"S4.facts-{task['id']}")
        if not isinstance(output, Mapping):
            raise ValueError(f"世界の事実がありません: {task['id']}")
        rows = output["facts"]
        lines.extend([f"## {task['section']['name']}：{task['viewpoint']}", ""])
        name = outputs.get(f"S4.item_name-{task['id']}", {}).get("name")
        if name:
            lines.extend([f"### {name}", ""])
        fields = list(task["fact_schema"]["properties"]["facts"]["items"]["properties"])
        labels = {"name": "名前・指標", "value": "数値", "unit": "単位", "year": "年",
                  "calendar": "暦", "count": "件数", "composition": "構成",
                  "affiliation": "所属", "cause": "原因", "result": "結果"}
        lines.extend(["| " + " | ".join(labels[field] for field in fields) + " |",
                      "|" + "---|" * len(fields)])
        lines.extend("| " + " | ".join(cell(row[field]) for field in fields) + " |" for row in rows)
        lines.extend(["", "### 年表", "", "| 暦 | 年 | 名前・指標 |", "|---|---|---|"])
        lines.extend("| " + " | ".join(cell(row[field]) for field in ("calendar", "year", "name")) + " |"
                     for row in sorted(rows, key=lambda row: (row["calendar"], row["year"], row["name"])))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
