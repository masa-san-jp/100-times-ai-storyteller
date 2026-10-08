"""Character fact sheets, world context and deterministic Markdown export."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# These sections supply locations, institutions, calendars and dated history.
CHARACTER_WORLD_SECTIONS = (
    "place", "customs", "organizations", "social_groups", "social_structure",
    "people", "past_events",
)


def sheet_for_card(output: Mapping[str, Any], glossary: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    """Attach referenced definitions to the required, untruncated sheet."""
    refs = {output.get(field) for field in ("birthplace", "residence", "affiliation")}
    names = {member["name"] for member in output.get("family", [])}
    return {**output, "glossary": [
        {key: value for key, value in entry.items() if key != "registered_task"}
        for entry in glossary if entry["id"] in refs or entry["name"] in names
    ]}


def validate_character_facts(output: Any, inputs: Mapping[str, Any]) -> list[str]:
    """Check references and chronology against the context actually shown."""
    if not isinstance(output, Mapping):
        return []  # The JSON Schema reports malformed shapes.
    errors = []
    entries = list(inputs.get("glossary", []))
    calendars = {inputs["calendar"]} if "calendar" in inputs else set()
    for sheet in inputs.get("world_facts", []):
        entries.extend(sheet.get("glossary", []))
        calendars.update(row["calendar"] for row in sheet.get("facts", []))
    identifiers = {entry["id"] for entry in entries}
    for field in ("birthplace", "residence", "affiliation"):
        value = output.get(field)
        if value is not None and isinstance(value, str) and value not in identifiers:
            errors.append(f"人物の事実: {field} は入力の用語集 ID ではありません: {value}")
    if isinstance(output.get("calendar"), str) and output["calendar"] not in calendars:
        errors.append("人物の事実: calendar は世界の事実の暦ではありません")
    names = {entry["name"] for entry in entries}
    if isinstance(output.get("family"), list):
        for member in output["family"]:
            if isinstance(member, Mapping) and isinstance(member.get("name"), str) and member["name"] not in names:
                errors.append(f"人物の事実: 家族の名前が用語集にありません: {member['name']}")
    birth_year = output.get("birth_year")
    if isinstance(birth_year, (int, float)) and isinstance(output.get("timeline"), list):
        for row in output["timeline"]:
            if isinstance(row, Mapping) and isinstance(row.get("year"), (int, float)) and row["year"] < birth_year:
                errors.append("人物の事実: 年表の年が生年より前です")
    return errors


def render_character_sheet(output: Mapping[str, Any], glossary: Sequence[Mapping[str, str]]) -> str:
    """Render scalar facts, named family members, a timeline and skills."""
    names = {entry["id"]: entry["name"] for entry in glossary}

    def cell(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")

    labels = {"age": "年齢（歳）", "birth_year": "生年", "calendar": "暦",
              "height_cm": "身長（cm）", "build": "体格", "birthplace": "出身地",
              "residence": "居住地", "occupation": "職業", "affiliation": "所属"}
    lines = ["### 事実のシート", "", "| 項目 | 事実 |", "|---|---|"]
    for field, label in labels.items():
        value = output[field]
        if field in {"birthplace", "residence", "affiliation"}:
            value = "なし" if value is None else names[value]
        lines.append(f"| {label} | {cell(value)} |")
    lines.extend(["", "#### 家族構成", "", "| 続柄 | 名前 |", "|---|---|"])
    lines.extend(f"| {cell(member['relationship'])} | {cell(member['name'])} |" for member in output["family"])
    if not output["family"]:
        lines.append("| — | なし |")
    lines.extend(["", "#### 年表", "", "| 暦 | 年 | 出来事 |", "|---|---|---|"])
    lines.extend(f"| {cell(output['calendar'])} | {row['year']} | {cell(row['event'])} |"
                 for row in sorted(output["timeline"], key=lambda row: row["year"]))
    lines.extend(["", "#### 技能", ""])
    lines.extend(f"- {cell(skill)}" for skill in output["skills"])
    return "\n".join(lines) + "\n"
