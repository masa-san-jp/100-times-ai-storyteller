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


UNIT_MAPPERS: dict[str, UnitMapper] = {"per_event": map_per_event}
