"""Character fact sheets, world context and deterministic Markdown export."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .tables import load_table
from .elements import parse_element_value
from .glossary import glossary_id

if TYPE_CHECKING:
    from .orchestrator import CodeTaskContext, CodeTaskResult, TaskSpec

# These sections supply locations, institutions, calendars and dated history.
CHARACTER_WORLD_SECTIONS = (
    "place", "customs", "organizations", "social_groups", "social_structure",
    "people", "past_events",
)


def character_card_view(person: Mapping[str, Any], *, description: bool = False) -> dict[str, Any]:
    """Keep assigned attributes, with role/plot context in its dedicated slots."""
    omitted = {"name_sound"}
    if description:
        omitted.update({"role_definition", "plot_context"})
    return {key: deepcopy(value) for key, value in person.items() if key not in omitted}


def sheet_for_card(output: Mapping[str, Any], glossary: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    """Attach referenced definitions to the required, untruncated sheet."""
    refs = {output.get(field) for field in ("birthplace", "residence", "affiliation")}
    names = {member["name"] for member in output.get("family", [])}
    return {**output, "glossary": [
        {key: entry[key] for key in ("id", "name", "definition")}
        for entry in glossary if entry["id"] in refs or entry["name"] in names
    ]}


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


def assign_character_fact_tasks(
    cast: Sequence[Mapping[str, Any]], name_sets: Sequence[Mapping[str, Any]],
    random_source: Any, repository_root: Path,
) -> list[dict[str, Any]]:
    """Expand repeated fields in catalog order; randomness belongs to S3."""
    catalog = load_table("character_facts", repository_root=repository_root)
    if len({field["key"] for field in catalog["facts"]}) != len(catalog["facts"]):
        raise ValueError("人物の事実のキーが重複しています")
    result = []
    for person in cast:
        family_count = random_source.randint(0, 3)
        for group in ("scalar", "family", "timeline", "skills"):
            fields = [field for field in catalog["facts"] if (
                "family" if field["key"].startswith("family.") else
                "timeline" if field["key"].startswith("timeline.") else
                "skills" if field["key"] == "skills" else "scalar") == group]
            count = {"scalar": 1, "family": family_count,
                     "timeline": catalog["timeline_count"], "skills": 3}[group]
            for ordinal in range(1, count + 1):
                for field in fields:
                    key = field["key"] if group == "scalar" else (
                        f"{group}.{ordinal}.{field['key'].split('.')[-1]}" if group != "skills"
                        else f"skills.{ordinal}")
                    entry = {**deepcopy(field), "key": key, "id": f"{person['id']}-{key}",
                             "person_id": person["id"]}
                    if field["element"] in {"name", "choice"}:
                        sound = random_source.choice(name_sets)
                        entry["name_sound"] = {"set_id": sound["id"], "description": sound["description"],
                                               "sounds": random_source.sample(sound["sounds"], 6)}
                    result.append(entry)
    return result


def character_entries(outputs: Mapping[str, Any], person_id: str) -> list[Mapping[str, Any]]:
    entries = outputs["S3.assign"]["character_fact_tasks"]
    planned = {entry["id"]: entry for entry in outputs.get(f"S5.fact_plan-{person_id}", {}).get("facts", [])}
    return [planned.get(entry["id"], entry) for entry in entries if entry["person_id"] == person_id]


def initial_character_specs(
    parent_id: str, person_id: str, assignment: Mapping[str, Any],
) -> list[TaskSpec]:
    from .orchestrator import TaskSpec
    from .world_facts import fact_ids_by_section

    age_id = f"S5.fact-{person_id}-age"
    world_ids = [task_id for section, ids in fact_ids_by_section(assignment).items()
                 if section in CHARACTER_WORLD_SECTIONS for task_id in ids[-1:]]
    protagonist = ("S5.name-c1",) if person_id != "c1" else ()
    deps = (parent_id, f"S5.name-{person_id}", *protagonist, "S4.calendar_name", *world_ids)
    return [TaskSpec(age_id, "S5.fact", deps=deps, index=(f"{person_id}-age",)),
            TaskSpec(f"S5.fact_plan-{person_id}", "S5.fact_plan", deps=(*deps, age_id), index=(person_id,))]


def _world_options(
    outputs: Mapping[str, Any],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Select typed glossary entries, including list-item organizations."""
    from .glossary import build_glossary

    places, organizations = [], []
    for term in build_glossary(outputs):
        option = {"id": term["id"], "name": term["name"]}
        if term["kind"] == "地名" or term["kind"].endswith("地点の名前"):
            places.append(option)
        elif term["kind"] in {"組織", "組織の名前", "所属団体の名前", "社会集団"}:
            organizations.append(option)
    return places, organizations


def story_s5_fact_plan(context: CodeTaskContext) -> CodeTaskResult:
    """Create post-age tasks with concrete year bounds and choice IDs."""
    from .orchestrator import CodeTaskResult, TaskSpec

    assignment = context.outputs["S3.assign"]
    person_id = context.task["index"][0]
    age = parse_element_value(context.outputs[f"S5.fact-{person_id}-age"], "integer")
    current_year = assignment["calendar"]["current_year"]
    birth_year = current_year - age
    places, organizations = _world_options(context.outputs)
    entries = deepcopy(character_entries(context.outputs, person_id))
    specs = []
    previous = f"S5.fact-{person_id}-age"
    for entry in entries:
        key = entry["key"]
        if key == "age":
            continue
        if key in {"birthplace", "residence"}:
            if not places:
                entry["element"] = "name"
                # The next residence task chooses the newly registered birthplace.
                places = [{"id": glossary_id(f"S5.fact-{entry['id']}", 0)}]
            else:
                entry["choices"] = deepcopy(places)
                entry.pop("name_sound", None)
        elif key == "affiliation":
            entry.pop("name_sound", None)
            entry["choices"] = [*organizations, {"id": "none", "name": "なし"}]
        elif key.startswith("timeline.") and key.endswith(".year"):
            entry["range"] = {"min": birth_year, "max": current_year}
        task_id = f"S5.fact-{entry['id']}"
        specs.append(TaskSpec(task_id, "S5.fact", deps=(context.task_id, "S3.assign",
            f"S5.name-{person_id}", *(("S5.name-c1",) if person_id != "c1" else ()),
            "S4.calendar_name", previous,
            *(task_id for task_id in context.task["deps"] if task_id.startswith("S4.fact-"))),
            index=(entry["id"],)))
        previous = task_id
    return CodeTaskResult(output={"facts": entries, "birth_year": birth_year}, add_tasks=specs)


def character_fact_value(entry: Mapping[str, Any], output: Any) -> Any:
    if entry["element"] == "name":
        return output["name"]
    if entry["element"] in {"number", "integer"}:
        return parse_element_value(output, entry["element"], unit=entry.get("unit", ""), value_range=entry.get("range"))
    return output


def character_source_context(
    outputs: Mapping[str, Any], task: Mapping[str, Any],
) -> dict[str, Any]:
    assignment = outputs.get("S3.assign", {})
    if "character_fact_tasks" not in assignment:
        return {}
    person_id = task["index"][0].split("-", 1)[0]
    entries = character_entries(outputs, person_id)
    if task["type"] != "S5.fact":
        from .glossary import build_glossary
        return {person_id: sheet_for_card(assemble_character_sheet(outputs, person_id), build_glossary(outputs))}
    current = deepcopy(next(entry for entry in entries if entry["id"] == task["index"][0]))
    # Fill display names from accepted dependencies, retaining the planned IDs.
    from .glossary import build_glossary
    names = {entry["id"]: entry["name"] for entry in build_glossary(outputs)}
    for option in current.get("choices", []):
        if option["id"] in names:
            option["name"] = names[option["id"]]
    previous = []
    for entry in entries:
        task_id = f"S5.fact-{entry['id']}"
        if task_id not in outputs:
            continue
        value = character_fact_value(entry, outputs[task_id])
        if entry["element"] == "choice":
            value = names.get(value, "なし" if value == "none" else value)
        identifier = glossary_id(task_id, 0) if entry["element"] == "name" else person_id
        previous.append({"id": identifier, "text": f"{entry['label']}：{value} {entry.get('unit', '')}".rstrip()})
        if entry["key"] == "age":
            previous.append({"id": person_id, "text": f"生年：{assignment['calendar']['current_year'] - value} 年"})
    from .world_facts import fact_lines
    world_entries = [entry for entry in assignment.get("world_fact_tasks", []) if entry["section_id"] in CHARACTER_WORLD_SECTIONS]
    return {"current": current, "previous": previous,
            "character": next(person for person in assignment["cast"] if person["id"] == person_id),
            "name": outputs.get(f"S5.name-{person_id}", {}).get("name", ""),
            "world": fact_lines(assignment, outputs, world_entries, include_items=True)}


def supplement_character_outputs(
    run_dir: Path, manifest: Mapping[str, Any], task: Mapping[str, Any],
    outputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Recover prior accepted fields and the planner through compact sequential edges."""
    from .task_outputs import read_task_output
    if not task["type"].startswith("S5.") or task["type"] == "S5.name":
        return dict(outputs)
    result = dict(outputs)
    pending = list(task["deps"])
    seen = set()
    while pending:
        task_id = pending.pop()
        if task_id in seen:
            continue
        seen.add(task_id)
        record = manifest["tasks"].get(task_id, {})
        if record.get("state") != "done" or record.get("type") not in {"S5.fact", "S5.fact_plan"}:
            continue
        if task_id not in result:
            result[task_id] = read_task_output(run_dir / "tasks" / task_id, record["type"])
        pending.extend(record["deps"])
    # Descriptions have compact edges to the facts, not the calendar or each
    # world registration. Recover their names so the required sheet includes
    # the definitions of every location, affiliation and family reference.
    assignment = result.get("S3.assign", {})
    name_ids = {"S4.calendar_name"}
    name_ids.update(f"S4.fact-{entry['id']}" for entry in assignment.get("world_fact_tasks", [])
                    if entry["element"] == "name" and entry["section_id"] in CHARACTER_WORLD_SECTIONS)
    name_ids.update(f"S4.item_name-{entry['id']}" for entry in assignment.get("world_tasks", [])
                    if entry["section_id"] in CHARACTER_WORLD_SECTIONS)
    for task_id in name_ids:
        record = manifest["tasks"].get(task_id, {})
        if task_id not in result and record.get("state") == "done":
            result[task_id] = read_task_output(run_dir / "tasks" / task_id, record["type"])
    return result


def assemble_character_sheet(outputs: Mapping[str, Any], person_id: str) -> dict[str, Any]:
    entries = character_entries(outputs, person_id)
    sheet = {"calendar": outputs["S4.calendar_name"]["name"], "family": [], "timeline": [], "skills": []}
    families, timeline = {}, {}
    for entry in entries:
        task_id = f"S5.fact-{entry['id']}"
        if task_id not in outputs:
            raise ValueError(f"人物の事実がありません: {entry['id']}")
        value = character_fact_value(entry, outputs[task_id])
        key = entry["key"]
        if key.startswith("family.") or key.startswith("timeline."):
            group, number, field = key.split(".")
            target = families if group == "family" else timeline
            target.setdefault(int(number), {})[field] = value
        elif key.startswith("skills."):
            sheet["skills"].append(value)
        else:
            if key in {"birthplace", "residence"} and entry["element"] == "name":
                value = glossary_id(task_id, 0)
            sheet[key] = None if key == "affiliation" and value == "none" else value
    sheet["birth_year"] = outputs["S3.assign"]["calendar"]["current_year"] - sheet["age"]
    sheet["family"] = [families[number] for number in sorted(families)]
    sheet["timeline"] = sorted(timeline.values(), key=lambda row: row["year"])
    return sheet
