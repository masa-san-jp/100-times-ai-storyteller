"""Reconstruct the run's glossary from accepted registration outputs."""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .tables import load_table
from .task_outputs import read_task_output


@lru_cache(maxsize=1)
def _role_names() -> dict[str, str]:
    return {role["id"]: role["name"] for role in load_table("roles")["roles"]}


def glossary_id(task_id: str, ordinal: int) -> str:
    """Derive stable IDs from the registration task and element ordinal."""
    return "g" + hashlib.sha256(f"{task_id}:{ordinal}".encode("utf-8")).hexdigest()[:6]


def build_glossary(outputs: Mapping[str, Any]) -> list[dict[str, str]]:
    """Build in task-ID order; adding tasks never renumbers existing entries."""
    assignment = outputs.get("S3.assign", {})
    roles = _role_names()
    people = {person["id"]: person for person in assignment.get("cast", [])}
    world_tasks = {task["id"]: task for task in assignment.get("world_tasks", [])}
    sections = {section["id"]: section for section in assignment.get("world_sections", [])}
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for task_id, output in sorted(outputs.items()):
        if not isinstance(output, Mapping):
            continue
        entries: list[Mapping[str, Any]] = []
        if task_id.startswith("S5.name-"):
            person_id = task_id.removeprefix("S5.name-")
            person = people[person_id]
            entries = [{**output, "kind": "人物", "definition": roles[person["role"]]}]
        elif task_id.startswith("S4.item_name-"):
            item_id = task_id.removeprefix("S4.item_name-")
            section = sections[world_tasks[item_id]["section_id"]]
            entries = [{**output, "kind": section["name"], "definition": f"{section['name']}の項目"}]
        elif task_id.startswith("S4.facts-"):
            entries = output.get("glossary", [])
            if not isinstance(entries, list):
                raise ValueError(f"用語集の出力が不正です: {task_id}")
        start = 1 if task_id.startswith("S4.facts-") else 0
        for ordinal, entry in enumerate(entries, start=start):
            identifier = glossary_id(task_id, ordinal)
            if identifier in seen:
                raise ValueError(f"用語集の ID が衝突しています: {identifier}")
            if not isinstance(entry, Mapping) or any(
                not isinstance(entry.get(field), str) or not entry[field]
                for field in ("name", "reading", "kind", "definition")
            ):
                raise ValueError(f"用語集の項目が不正です: {task_id}:{ordinal}")
            seen.add(identifier)
            result.append({
                "id": identifier,
                **{field: entry[field] for field in ("name", "reading", "kind", "definition")},
                "registered_task": task_id,
            })
    return result


def registration_outputs(run_dir: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Ignore stale files belonging to invalidated, blocked or skipped tasks."""
    return {
        task_id: read_task_output(run_dir / "tasks" / task_id, task["type"])
        for task_id, task in manifest["tasks"].items()
        if task["state"] == "done"
        and task["type"] in {"S3.assign", "S5.name", "S4.item_name", "S4.facts"}
    }


def related_glossary(
    glossary: Sequence[Mapping[str, str]],
    assignment: Mapping[str, Any],
    inputs: Mapping[str, Any],
    index: Sequence[str],
) -> list[dict[str, str]]:
    """Expose only referenced entries and entries of the same section.

    Registration task IDs remain internal: a card must not reveal stage IDs.
    """
    references: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, str):
            references.add(value)
        elif isinstance(value, Mapping):
            references.update(key for key in value if re.fullmatch(r"c\d+|g[0-9a-f]{6}", key))
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(inputs)
    world_tasks = {task["id"]: task for task in assignment.get("world_tasks", [])}
    fact_tasks = {task["id"]: task for task in assignment.get("world_fact_tasks", [])}
    own_section = {**world_tasks, **fact_tasks}.get(index[0], {}).get("section_id") if index else None
    sections = [section["id"] for section in assignment.get("world_sections", [])]
    result: list[dict[str, str]] = []
    for entry in glossary:
        task_id = entry["registered_task"]
        person_id = task_id.removeprefix("S5.name-") if task_id.startswith("S5.name-") else None
        section_id = None
        if task_id.startswith("S4.item_name-"):
            section_id = world_tasks[task_id.removeprefix("S4.item_name-")]["section_id"]
        elif task_id.startswith("S4.facts-"):
            suffix = task_id.removeprefix("S4.facts-")
            section_id = next((section for section in sorted(sections, key=len, reverse=True)
                               if suffix == section or suffix.startswith(section + "-")), None)
        if entry["id"] in references or person_id in references or (
            section_id is not None and (section_id == own_section or section_id in references)
        ):
            result.append({key: value for key, value in entry.items() if key != "registered_task"})
    return result


def registered_names(glossary: Sequence[Mapping[str, str]]) -> list[str]:
    return [entry[field] for entry in glossary for field in ("name", "reading")]


def render_glossary_markdown(glossary: Sequence[Mapping[str, str]]) -> str:
    """Render the export, including provenance, with safe table cells."""
    def cell(value: str) -> str:
        return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")

    fields = ("id", "name", "reading", "kind", "definition", "registered_task")
    lines = ["# 用語集", "", "| ID | 名前 | 読み | 種類 | 定義 | 登録したタスク |",
             "|---|---|---|---|---|---|"]
    lines.extend("| " + " | ".join(cell(entry[field]) for field in fields) + " |" for entry in glossary)
    return "\n".join(lines) + "\n"
