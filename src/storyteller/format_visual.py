"""Format ``visual-prompts``: one unit per cast member and one per event.

See docs/spec/format-pipeline.md section 4.3.  The mapping is a pure function
of the canonical story (P2); the LLM only writes one prompt per unit.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .format_units import UNIT_MAPPERS, Unit, unit_id

MAPPING_NAME = "person_and_event"
REFERENCE_EVENTS = 3  # events shown with a person's unit, for reference only


def is_visual(profile: Mapping[str, Any]) -> bool:
    return profile["unit_mapping"] == MAPPING_NAME


def map_person_and_event(
    story: Mapping[str, Any], profile: Mapping[str, Any], repository_root: Path
) -> list[Unit]:
    """People in cast order, then events in story order (people + events units)."""

    parts = {thread["id"]: thread.get("part") for thread in story["threads"]}
    total = len(story["cast"]) + len(story["events"])
    units = [
        Unit(
            id=unit_id(position, total),
            title=f"人物：{person['name']}",
            event_ids=(),
            person_id=person["id"],
        )
        for position, person in enumerate(story["cast"], start=1)
    ]
    offset = len(units)
    for position, event in enumerate(story["events"], start=offset + 1):
        units.append(Unit(
            id=unit_id(position, total),
            title=f"場面：{event['stage']['name']}",
            event_ids=(event["id"],),
            part=parts.get(event["thread"]),
        ))
    return units


def visual_character_text(person: Mapping[str, Any], roles: Mapping[str, str]) -> str:
    """Name, reading, role, profile and appearance (the picture needs the look)."""

    return (
        f"{person['name']}（{person['reading']}）／"
        f"{roles.get(person['role'], person['role'])}／{person['profile']}／"
        f"外見：{person['appearance']}"
    )


def person_part(
    unit: Unit,
    person: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    names: Mapping[str, str],
    roles: Mapping[str, str],
    chars: tuple[int, int],
) -> dict[str, Any]:
    """The single F2 part of a person unit (events are reference only)."""

    from .formatting import event_text

    low, high = chars
    reference = [event for event in events if person["id"] in event["who"]][:REFERENCE_EVENTS]
    return {
        "id": f"{unit.id}p1",
        "unit": unit.id,
        "event_ids": [],
        "events": [{"id": event["id"], "text": event_text(event, names)} for event in reference],
        "characters": [{"id": person["id"], "text": visual_character_text(person, roles)}],
        "min_chars": low,
        "max_chars": high,
        "range": f"{low}〜{high}字",
    }


UNIT_MAPPERS[MAPPING_NAME] = map_person_and_event
