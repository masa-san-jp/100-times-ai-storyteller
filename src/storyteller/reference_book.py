"""Reference-book format (``reference-book``): world sections and characters.

format-pipeline.md section 4.4.  Unlike the event-based mappings, the units
of this format are the world and the cast of the canonical story, so F1 uses
:func:`build_reference_plan` instead of the event mapping.  The result has the
same shape as the common plan; the "events" of a part are the source entries
(one world item or one character field each) the F2 task must cover.  Every
choice here is made by code (P2); coverage is checked by code (F4).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

UNIT_MAPPING = "world_cast"

_OVERVIEW = (("place", "場所"), ("era", "時代"), ("object", "象徴的な物"))
_CHARACTER_FIELDS = (
    ("intro", "紹介"), ("profile", "プロフィール"), ("motive", "動機"),
    ("appearance", "外見"), ("personality", "性格"), ("values", "価値観"),
    ("voice", "話し方"), ("inner_conflict", "内面の葛藤"),
)


def _split(count: int, per_part: int) -> list[int]:
    parts = -(-count // per_part)
    base, extra = divmod(count, parts)
    return [base + 1 if position < extra else base for position in range(parts)]


def _world_units(story: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Source units of the world: an overview, then one per section."""

    world = story["world"]
    entries = [(f"world.{key}", f"{label}：{world[key]['text']}") for key, label in _OVERVIEW]
    if world.get("theme"):
        entries.append(("world.theme", f"主題：{world['theme']['text']}"))
    units: list[dict[str, Any]] = [
        {"key": "world", "title": "世界：概説", "entries": entries, "names": []}
    ]
    for section in world["sections"]:
        sid = section["id"]
        entries = []
        names: list[str] = []
        if section["body"]:
            entries.append((f"section.{sid}", f"{section['name']}：{section['body']}"))
        for number, item in enumerate(section["items"], start=1):
            entries.append((f"section.{sid}.{number}", f"{item['name']}：{item['body']}"))
            names.append(item["name"])
        if not entries:  # a section without body or items still gets its unit
            entries.append((f"section.{sid}", section["name"]))
        units.append({
            "key": f"section.{sid}", "title": f"世界：{section['name']}",
            "entries": entries, "names": names,
        })
    return units


def _cast_units(story: Mapping[str, Any], roles: Mapping[str, str]) -> list[dict[str, Any]]:
    units = []
    for person in story["cast"]:
        cid = person["id"]
        head = f"{person['name']}（{person['reading']}）／{roles.get(person['role'], person['role'])}"
        entries = [(f"{cid}.name", head)]
        entries += [(f"{cid}.{key}", f"{label}：{person[key]}") for key, label in _CHARACTER_FIELDS]
        entries += [(f"{cid}.backstory.{n}", f"過去：{text}") for n, text in enumerate(person["backstory"], 1)]
        entries += [(f"{cid}.relationship.{n}", f"関係：{text}") for n, text in enumerate(person["relationship"], 1)]
        entries.append((f"{cid}.catchphrase", f"口癖：{person['catchphrase']}"))
        units.append({
            "key": cid, "title": f"人物：{person['name']}",
            "entries": entries, "names": [person["name"]],
        })
    return units


def build_reference_plan(
    story: Mapping[str, Any], profile: Mapping[str, Any], roles: Mapping[str, str]
) -> dict[str, Any]:
    """F1 plan of the reference book: every world section and every person."""

    low, high = profile["chars_per_event"]
    per_part = max(1, profile["max_chars_per_task"] // high)
    sources = _world_units(story) + _cast_units(story, roles)
    unit_records: list[dict[str, Any]] = []
    parts: list[dict[str, Any]] = []
    width = max(2, len(str(len(sources))))
    for position, source in enumerate(sources, start=1):
        unit_id = f"u{position:0{width}d}"
        entries: Sequence[tuple[str, str]] = source["entries"]
        part_ids: list[str] = []
        start = 0
        for number, size in enumerate(_split(len(entries), per_part), start=1):
            chunk = entries[start:start + size]
            start += size
            part_id = f"{unit_id}p{number}"
            part_ids.append(part_id)
            chunk_text = " ".join(text for _, text in chunk)
            parts.append({
                "id": part_id,
                "unit": unit_id,
                "event_ids": [key for key, _ in chunk],
                "events": [{"id": key, "text": text} for key, text in chunk],
                "characters": [],
                "required_names": [name for name in source["names"] if name in chunk_text],
                "min_chars": low * len(chunk),
                "max_chars": high * len(chunk),
                "range": f"{low * len(chunk)}〜{high * len(chunk)}字",
            })
        unit_records.append({
            "id": unit_id, "title": source["title"], "part": None,
            "event_ids": [key for key, _ in entries], "part_ids": part_ids,
        })
    return {
        "format": profile["name"],
        "unit": profile["unit"],
        "continuation": profile["continuation"],
        "rules": list(profile["rules"]),
        "code_checks": list(profile["checks"]["code"]),
        "questions": list(profile["checks"].get("llm", [])),
        "units": unit_records,
        "parts": parts,
    }


def check_contains_names(body: str, part: Mapping[str, Any]) -> list[str]:
    """F4 code check ``contains_names``: item and person names must appear."""

    return [
        f"入力の名前「{name}」が本文にありません"
        for name in part.get("required_names", [])
        if name not in body
    ]
