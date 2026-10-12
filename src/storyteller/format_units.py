"""F1 unit mapping: how a canonical story's events are grouped into units.

``unit_mapping`` in a format profile names an entry of :data:`UNIT_MAPPERS`
(format-pipeline.md section 4.1).  A mapper is a pure, deterministic function
of the canonical story, so the choice never depends on an LLM (P2).  Later
work items register the mappings they introduce (``structure``, ``scene``)
by adding entries to the registry.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .tables import load_table


@dataclass(frozen=True)
class Unit:
    """One unit (chapter, scene or slide) and the events assigned to it."""

    id: str
    title: str
    event_ids: tuple[str, ...]
    part: int | None = None


UnitMapper = Callable[[Mapping[str, Any], Mapping[str, Any], Path], list[Unit]]


def unit_id(position: int, total: int) -> str:
    """Return ``u01``-style IDs, widened when there are more than 99 units."""

    return f"u{position:0{max(2, len(str(total)))}d}"


def _thread_parts(story: Mapping[str, Any]) -> dict[str, int | None]:
    return {thread["id"]: thread.get("part") for thread in story["threads"]}


def map_per_event(
    story: Mapping[str, Any], profile: Mapping[str, Any], repository_root: Path
) -> list[Unit]:
    """One event, one unit (``unit_mapping: per_event``)."""

    parts = _thread_parts(story)
    events: Sequence[Mapping[str, Any]] = story["events"]
    return [
        Unit(
            id=unit_id(position, len(events)),
            title=event["stage"]["name"],
            event_ids=(event["id"],),
            part=parts.get(event["thread"]),
        )
        for position, event in enumerate(events, start=1)
    ]


def chapters_of(structure_id: str, structure: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the chapters of a structure template.

    The table's ``chapters`` is used when present; otherwise each stage is one
    chapter.  A table mapping must list every stage exactly once, in order.
    """

    stages = [stage["id"] for stage in structure["stages"]]
    chapters = structure.get("chapters")
    if chapters is None:
        return [
            {"id": stage["id"], "name": stage["name"], "stages": [stage["id"]]}
            for stage in structure["stages"]
        ]
    listed = [stage for chapter in chapters for stage in chapter["stages"]]
    if listed != stages:
        raise ValueError(
            f"構造テンプレート {structure_id} の chapters が、段階を順にちょうど1回ずつ割り付けていません"
        )
    return [dict(chapter) for chapter in chapters]


def map_structure(
    story: Mapping[str, Any], profile: Mapping[str, Any], repository_root: Path
) -> list[Unit]:
    """Stages of the main thread's structure become chapters (``structure``).

    A side thread's event joins the chapter of the main-thread event just
    before it in time (the first chapter when none precedes it).  In a saga
    every part is a main thread, so chapters are counted anew per part (the
    ``Unit.part`` field lets the template add the part heading).
    """

    structures = load_table("structures", repository_root=repository_root)["templates"]
    threads = {thread["id"]: thread for thread in story["threads"]}
    chapter_of: dict[str, dict[str, str]] = {}
    chapter_names: dict[tuple[str, str], str] = {}
    for thread in threads.values():
        if thread["kind"] != "main":
            continue
        structure_id = thread["structure"]
        if structure_id not in structures:
            raise ValueError(f"構造テンプレートがありません: {structure_id}")
        mapping: dict[str, str] = {}
        for chapter in chapters_of(structure_id, structures[structure_id]):
            chapter_names[(thread["id"], chapter["id"])] = chapter["name"]
            for stage in chapter["stages"]:
                mapping[stage] = chapter["id"]
        chapter_of[thread["id"]] = mapping

    keys: list[tuple[str, str]] = []
    assigned: dict[str, tuple[str, str]] = {}
    previous: tuple[str, str] | None = None
    for event in story["events"]:
        thread_id = event["thread"]
        if thread_id not in chapter_of:
            continue
        stage_id = event["stage"]["id"]
        if stage_id not in chapter_of[thread_id]:
            raise ValueError(f"出来事 {event['id']} の段階 {stage_id} が構造テンプレートにありません")
        previous = (thread_id, chapter_of[thread_id][stage_id])
        assigned[event["id"]] = previous
        if previous not in keys:
            keys.append(previous)
    if not keys:
        raise ValueError("主筋の出来事がありません")

    previous = None
    for event in story["events"]:
        if event["id"] in assigned:
            previous = assigned[event["id"]]
        else:
            assigned[event["id"]] = previous if previous is not None else keys[0]

    members: dict[tuple[str, str], list[str]] = {key: [] for key in keys}
    for event in story["events"]:
        members[assigned[event["id"]]].append(event["id"])
    return [
        Unit(
            id=unit_id(position, len(keys)),
            title=chapter_names[key],
            event_ids=tuple(members[key]),
            part=threads[key[0]].get("part"),
        )
        for position, key in enumerate(keys, start=1)
    ]


UNIT_MAPPERS: dict[str, UnitMapper] = {"per_event": map_per_event, "structure": map_structure}
