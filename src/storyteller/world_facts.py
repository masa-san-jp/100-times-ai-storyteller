"""Plan single world facts and assemble calendar, card context and exports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any
import unicodedata

from .elements import parse_element_value
from .tables import load_table


def assign_world_fact_tasks(
    sections: Sequence[Mapping[str, Any]],
    world_tasks: Sequence[Mapping[str, Any]],
    name_sets: Sequence[Mapping[str, Any]],
    random_source: Any,
    *, current_year: int, repository_root: Path,
) -> list[dict[str, Any]]:
    """Expand catalog fields once, independently of the number of facets."""
    catalog = load_table("world_facts", repository_root=repository_root)
    result = []
    for section in sections:
        fields = catalog["facts" if section["kind"] == "single" else "item_facts"]
        items = [None] if section["kind"] == "single" else [
            task["id"] for task in world_tasks if task["section_id"] == section["id"]
        ]
        for item_id in items:
            for field in fields:
                if field["section_id"] != section["id"]:
                    continue
                entry = {**deepcopy(field), "id": f"{item_id}-{field['key']}" if item_id else field["key"],
                         "item_id": item_id, "section": {
                             key: section[key] for key in ("id", "name", "definition")}}
                if field["element"] == "year":
                    entry["range"] = ({"min": current_year + 1, "max": current_year + 1000}
                                      if section["id"] == "future" else {"min": 1, "max": current_year})
                if field["element"] == "name":
                    sound = random_source.choice(name_sets)
                    entry["name_sound"] = {"set_id": sound["id"], "description": sound["description"],
                                           "sounds": random_source.sample(sound["sounds"], 6)}
                result.append(entry)
    return result


def assign_calendar(random_source: Any, name_sets: Sequence[Mapping[str, Any]],
                    repository_root: Path) -> dict[str, Any]:
    limits = load_table("world_facts", repository_root=repository_root)["current_year_range"]
    sound = random_source.choice(name_sets)
    return {"current_year": random_source.randint(limits["min"], limits["max"]),
            "name_sound": {"set_id": sound["id"], "description": sound["description"],
                           "sounds": random_source.sample(sound["sounds"], 6)}}


def fact_ids_by_section(assignment: Mapping[str, Any]) -> dict[str, list[str]]:
    return {section["id"]: [f"S4.fact-{entry['id']}" for entry in assignment["world_fact_tasks"]
                            if entry["section_id"] == section["id"]]
            for section in assignment["world_sections"]}


def referred_entries(entry: Mapping[str, Any], entries: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Resolve item-local references to that item, and world references once."""
    return [other for other in entries if other["key"] in entry.get("refers", [])
            and (other.get("item_id") is None or other.get("item_id") == entry.get("item_id"))]


def description_entries(world_task: Mapping[str, Any], entries: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    own = [entry for entry in entries if entry["section_id"] == world_task["section_id"]
           and (entry.get("item_id") == world_task["id"] if world_task["kind"] == "list"
                else entry["viewpoint"] == world_task["viewpoint"])]
    ids = {entry["id"] for entry in own}
    for entry in list(own):
        for other in referred_entries(entry, entries):
            if other["id"] not in ids:
                own.append(other)
                ids.add(other["id"])
    return own


def build_world_fact_specs(parent_task_id: str, assignment: Mapping[str, Any]) -> list[Any]:
    from .orchestrator import TaskSpec

    by_section = fact_ids_by_section(assignment)
    sections = {section["id"]: section for section in assignment["world_sections"]}
    entries = assignment["world_fact_tasks"]
    specs = [TaskSpec("S4.calendar_name", "S4.calendar_name", deps=(parent_task_id,)),
             TaskSpec("S4.calendar_epoch", "S4.calendar_epoch", deps=(parent_task_id, "S4.calendar_name"))]
    previous = {}
    for entry in entries:
        section_id = entry["section_id"]
        dependencies = [parent_task_id, "S4.calendar_name"]
        dependencies.extend(task_id for prerequisite in sections[section_id]["prerequisites"]
                            for task_id in by_section.get(prerequisite, [])[-1:])
        if section_id in previous:
            dependencies.append(previous[section_id])
        dependencies.extend(f"S4.fact-{other['id']}" for other in referred_entries(entry, entries))
        if entry.get("item_id"):
            dependencies.append(f"S4.item_name-{entry['item_id']}")
        task_id = f"S4.fact-{entry['id']}"
        specs.append(TaskSpec(task_id, "S4.fact", deps=tuple(dict.fromkeys(dependencies)), index=(entry["id"],)))
        previous[section_id] = task_id
    return specs


def supplement_fact_outputs(run_dir: Path, manifest: Mapping[str, Any],
                            task: Mapping[str, Any], outputs: Mapping[str, Any],
                            *, definition: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Read accepted fact ancestors while keeping sequential DAG edges compact."""
    from .task_outputs import read_task_output

    result = dict(outputs)
    if task["type"] not in {"S4.fact", "S5.fact", "S5.fact_plan"}:
        return result
    pending = list(task["deps"])
    seen = set()
    while pending:
        task_id = pending.pop()
        if task_id in seen:
            continue
        seen.add(task_id)
        record = manifest["tasks"][task_id]
        if record["type"] in {"S5.fact", "S5.fact_plan"} and record["state"] == "done":
            pending.extend(record["deps"])
            continue
        if record["type"] == "S4.item_name":
            continue
        if record["type"] != "S4.fact" or record["state"] != "done":
            continue
        pending.extend(record["deps"])
    entries = result.get("S3.assign", {}).get("world_fact_tasks", [])
    if task["type"] == "S4.fact" and definition is not None and definition.get("inputs", {}).get("previous_facts", {}).get("truncate") == "tail":
        current = next(entry for entry in entries if entry["id"] == task["index"][0])
        section = next(section for section in result["S3.assign"]["world_sections"] if section["id"] == current["section_id"])
        prior = [entry for entry in entries if f"S4.fact-{entry['id']}" in seen
                 and entry["section_id"] in [current["section_id"], *section["prerequisites"]]]
        entries = _bounded_prior_entries(prior, definition.get("max_input_chars", 3000))
        wanted = {f"S4.fact-{entry['id']}" for entry in entries}
    else:
        wanted = {task_id for task_id in seen if manifest["tasks"][task_id]["type"] == "S4.fact"}
    for task_id in wanted:
        if task_id not in result and manifest["tasks"][task_id]["state"] == "done":
            result[task_id] = read_task_output(run_dir / "tasks" / task_id, "S4.fact")
    for entry in entries:
        if entry.get("item_id") and f"S4.fact-{entry['id']}" in result:
            name_id = f"S4.item_name-{entry['item_id']}"
            if name_id not in result and manifest["tasks"].get(name_id, {}).get("state") == "done":
                result[name_id] = read_task_output(run_dir / "tasks" / name_id, "S4.item_name")
    return result


def _fact_line_id(entry: Mapping[str, Any]) -> str:
    from .glossary import glossary_id

    if entry["element"] == "name":
        return glossary_id(f"S4.fact-{entry['id']}", 0)
    if entry.get("item_id"):
        return glossary_id(f"S4.item_name-{entry['item_id']}", 0)
    return entry["section_id"]


def _bounded_prior_entries(entries: Sequence[Mapping[str, Any]], budget: int) -> Sequence[Mapping[str, Any]]:
    """Exclude only rows that cannot survive the existing tail truncation.

    Even with empty values, units and item names, the retained tail exceeds
    the entire input budget. Earlier rows therefore cannot appear on a fitted
    card. Keep the overflowing row too, preserving the normal fitting order
    (including glossary truncation) and the exact fitted attribution/cache key.
    """
    minimum = 0
    for offset, entry in enumerate(reversed(entries), 1):
        minimum += len(unicodedata.normalize("NFC", f"[{_fact_line_id(entry)}] {entry['label']}：")) + 1
        if minimum > budget:
            return entries[-offset:]
    return entries


def over_budget_fact_tail(rows: list[Mapping[str, Any]], budget: int) -> list[Mapping[str, Any]]:
    """Keep an overflowing suffix so fitting and glossary removal stay identical."""
    length = 0
    for offset, row in enumerate(reversed(rows), 1):
        length += len(f"[{row['id']}] {unicodedata.normalize('NFC', str(row['text']))}")
        if offset > 1:
            length += 1
        if length > budget:
            return rows[-offset:]
    return rows


def specialize_fact_definition(definition: Mapping[str, Any], inputs: Mapping[str, Any]) -> Mapping[str, Any]:
    """Use one catalog field's shape with the P1-30 element contracts."""
    if definition.get("id") not in {"S4.fact", "S5.fact"} or "shape" not in inputs:
        return definition
    result = deepcopy(definition)
    common_checks = ["no_copy_from_inputs", {"avoid_listed": {"table": "tables/meta_terms.yaml"}}]
    shape = inputs["shape"]
    result["element"] = "integer" if shape == "year" else shape
    result["continuation"] = False
    if shape == "name":
        result["output"] = "json"
        result["card"]["output_example"] = '{"name": "...", "reading": "..."}'
        step = "与えられた音の2個以上を使う。名前とカタカナの読みだけを出力する。"
        if step not in result["card"]["steps"]:
            result["card"]["steps"].append(step)
        result["validate"] = {"schema": "schemas/tasks/S4.calendar_name.schema.json", "checks": [*common_checks, {"min_chars": {"field": "name", "n": 1}},
            {"uses_given": {"field": "reading", "slot": "name_sound", "n": 2}}]}
    elif shape == "choice":
        result["choice_max"] = 1
        result["validate"] = {"checks": common_checks}
    elif shape in {"number", "integer", "year"}:
        result["range"] = inputs.get("bounds", {})
        result["validate"] = {"checks": common_checks}
    else:
        result["validate"] = {"checks": [*common_checks, {"min_chars": {"n": 1}}, {"max_chars": {"n": inputs.get("max_chars", 40)}},
                                          {"no_new_proper_nouns": {"mode": "fail"}}]}
        step = f"{inputs.get('max_chars', 40)}字以内の1句で、具体的な種類・行為・条文・手続だけを書く。曖昧な程度語で埋めない。"
        if step not in result["card"]["steps"]:
            result["card"]["steps"].append(step)
    return result


def fact_value(entry: Mapping[str, Any], output: Any) -> Any:
    if entry["element"] == "name":
        return output["name"]
    if entry["element"] in {"number", "integer", "year"}:
        return parse_element_value(str(output), "integer" if entry["element"] == "year" else entry["element"],
                                   unit=entry.get("unit", ""), value_range=entry.get("range"))
    return output


def fact_lines(assignment: Mapping[str, Any], outputs: Mapping[str, Any],
               entries: Sequence[Mapping[str, Any]], *, include_items: bool = False) -> list[dict[str, str]]:
    """Keep whole heading/value/unit lines, with glossary IDs for names."""
    lines = []
    for entry in entries:
        task_id = f"S4.fact-{entry['id']}"
        if task_id not in outputs:
            continue
        value = fact_value(entry, outputs[task_id])
        unit = entry.get("unit", "")
        if entry["element"] == "year":
            unit = f"年（{outputs['S4.calendar_name']['name']}）"
        item_id = entry.get("item_id")
        identifier = _fact_line_id(entry)
        heading = entry["label"]
        if include_items and item_id:
            item_name = outputs.get(f"S4.item_name-{item_id}", {}).get("name")
            if item_name:
                heading = f"{item_name}／{heading}"
        lines.append({"id": identifier, "text": f"{heading}：{value} {unit}".rstrip()})
    return lines


def fact_source_context(assignment: Mapping[str, Any], outputs: Mapping[str, Any],
                        task: Mapping[str, Any]) -> dict[str, Any]:
    """Build selectors' views without exposing task IDs or full assignment."""
    entries = assignment.get("world_fact_tasks", [])
    index = task.get("index", [])
    if task["type"] == "S4.fact":
        entry = next(entry for entry in entries if entry["id"] == index[0])
        sections = {s["id"]: s for s in assignment["world_sections"]}
        prerequisites = sections[entry["section_id"]]["prerequisites"]
        previous = [other for other in entries if other["section_id"] == entry["section_id"]
                    or other["section_id"] in prerequisites]
        return {"previous": fact_lines(assignment, outputs, previous, include_items=True),
                "referred": fact_lines(assignment, outputs, referred_entries(entry, entries), include_items=True)}
    if task["type"] in {"S4.section", "S4.item"}:
        world_task = next(entry for entry in assignment["world_tasks"] if entry["id"] == index[0])
        return {"own": fact_lines(assignment, outputs, description_entries(world_task, entries))}
    if task["type"] == "S5.fact":
        from .character_facts import CHARACTER_WORLD_SECTIONS
        return {"world": fact_lines(assignment, outputs, [entry for entry in entries
                        if entry["section_id"] in CHARACTER_WORLD_SECTIONS], include_items=True)}
    return {}


def render_world_facts_markdown(assignment: Mapping[str, Any], outputs: Mapping[str, Any]) -> str:
    """Assemble a single calendar, measurement dates and a sorted timeline."""
    def cell(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")

    # Lightweight legacy assembly fixtures with no world facts need no calendar.
    if not assignment.get("world_fact_tasks"):
        return "# 世界の事実\n"
    calendar = outputs["S4.calendar_name"]["name"]
    year = assignment["calendar"]["current_year"]
    lines = ["# 世界の事実", "", "## 暦", "", f"暦：{cell(calendar)}", f"現在の年：{year}年",
             f"紀元（元年）：{cell(outputs['S4.calendar_epoch'])}", ""]
    timeline = []
    for section in assignment["world_sections"]:
        lines.extend([f"## {cell(section['name'])}", "",
                      "| 項目 | 観点 | 見出し | 値 | 単位 | 測定年 | 暦 |", "|---|---|---|---|---|---|---|"])
        for entry in assignment["world_fact_tasks"]:
            if entry["section_id"] != section["id"]:
                continue
            task_id = f"S4.fact-{entry['id']}"
            if task_id not in outputs:
                raise ValueError(f"世界の事実がありません: {entry['id']}")
            value = fact_value(entry, outputs[task_id])
            item = outputs.get(f"S4.item_name-{entry.get('item_id')}", {}).get("name", "—")
            lines.append("| " + " | ".join(cell(v) for v in (item, entry["viewpoint"], entry["label"],
                         value, entry.get("unit", "年" if entry["element"] == "year" else "—"), year, calendar)) + " |")
            if entry["element"] == "year":
                timeline.append((value, item, entry["label"]))
        lines.append("")
    lines.extend(["## 年表", "", "| 暦 | 年 | 項目 | 出来事・見出し |", "|---|---|---|---|"])
    lines.extend(f"| {cell(calendar)} | {date} | {cell(item)} | {cell(label)} |"
                 for date, item, label in sorted(timeline))
    return "\n".join(lines) + "\n"
