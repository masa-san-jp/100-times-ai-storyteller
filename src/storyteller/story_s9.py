"""Deterministic assembly of the canonical story for story-pipeline S9."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from .manifest import load_manifest
from .orchestrator import CodeTaskContext, CodeTaskResult
from .storage import atomic_write_json, atomic_write_text
from .tables import load_table
from .validation import validate_document
from .task_outputs import read_task_output
from .volume import multiplier_for_preset
from .glossary import build_glossary, render_glossary_markdown
from .world_facts import render_world_facts_markdown
from .character_facts import render_character_sheet


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_STORY_SCHEMA = _REPOSITORY_ROOT / "schemas" / "story.schema.json"

# S7.detail のタスクID（docs/plan/phase-1.md: S7.detail-e<3桁>-b<番号>）。
_DETAIL_TASK_ID = re.compile(r"^S7\.detail-(e[0-9]+)-b([0-9]+)$")

# characters.md に並べる S5 の項目（存在するものだけを表示する。story-pipeline.md §8 の
# tables/volume.yaml task_chars.character のキーに対応する。personality 以降は、まだ S5 が
# 生成しない項目で、P1-17 で追加される）。
_CHARACTER_FIELDS: tuple[tuple[str, str], ...] = (
    ("intro", "紹介"),
    ("profile", "プロフィール"),
    ("motive", "動機"),
    ("appearance", "外見"),
    ("personality", "性格"),
    ("values", "価値観"),
    ("voice", "口調"),
    ("inner_conflict", "内的葛藤"),
    ("backstory", "来歴"),
    ("relationship", "人物関係"),
    ("catchphrase", "決め台詞"),
)

# manifest.warnings に書く資料名（story-pipeline.md §8 の「資料」列、ADR-0007）。
_VOLUME_LABELS: dict[str, str] = {"characters": "人物", "world": "世界", "story": "物語"}


def story_s9_assemble(context: CodeTaskContext) -> CodeTaskResult:
    """Assemble, validate, and persist the canonical story and its references.

    Writes ``story/story.json``, ``story.md`` (events), ``characters.md``
    (cast), ``world.md`` (world sections), ``world_facts.md`` and ``glossary.md``,
    and records any volume
    shortfall against story-pipeline.md §8 as manifest warnings.
    """

    outputs = _all_outputs(context)
    assignment = _mapping_output(outputs, "S3.assign")
    slots_output = _mapping_output(outputs, "S6.expand")
    slots = slots_output.get("slots")
    if not isinstance(slots, list) or not all(isinstance(slot, Mapping) for slot in slots):
        raise ValueError("S6.expand の slots がありません")

    manifest = load_manifest(context.run_dir / "manifest.json")
    story = _assemble_story(context, manifest, assignment, slots, outputs)
    # Check referential integrity (who/sources exist) before rendering, so an
    # invalid story fails with that diagnosis instead of a KeyError while the
    # markdown templates look up a cast member that does not exist.
    _validate_references(story, context.run_input, outputs, assignment)

    details = _collect_event_details(outputs)
    story_markdown = render_story_markdown(story, details)
    glossary = build_glossary(outputs)
    characters_markdown = render_characters_markdown(story, outputs, glossary)
    world_markdown = render_world_markdown(story)
    glossary_markdown = render_glossary_markdown(glossary)
    world_facts_markdown = render_world_facts_markdown(assignment, outputs)

    volume, warnings = _compute_volume(
        manifest,
        {
            "characters": characters_markdown,
            "world": world_markdown + world_facts_markdown,
            "story": story_markdown,
        },
    )
    story["meta"]["volume"] = volume

    validate_document(story, _STORY_SCHEMA)

    story_dir = context.run_dir / "story"
    atomic_write_json(story_dir / "story.json", story)
    atomic_write_text(story_dir / "story.md", story_markdown, encoding="utf-8")
    atomic_write_text(story_dir / "characters.md", characters_markdown, encoding="utf-8")
    atomic_write_text(story_dir / "world.md", world_markdown, encoding="utf-8")
    atomic_write_text(story_dir / "glossary.md", glossary_markdown, encoding="utf-8")
    atomic_write_text(story_dir / "world_facts.md", world_facts_markdown, encoding="utf-8")
    return CodeTaskResult(
        output=story,
        manifest_updates={"warnings": warnings} if warnings else {},
    )


def _all_outputs(context: CodeTaskContext) -> dict[str, Any]:
    """Use direct dependency outputs, with a durable-run fallback.

    S9 declares all upstream outputs as dependencies.  Reading the completed
    task files as a fallback also keeps this code task resumable when a caller
    supplies a context assembled by an older orchestrator instance.
    """

    outputs = dict(context.dependency_outputs)
    manifest_path = context.run_dir / "manifest.json"
    if not manifest_path.is_file():
        return outputs
    try:
        manifest = load_manifest(manifest_path)
    except ValueError:
        return outputs
    for task_id, task in manifest.get("tasks", {}).items():
        if task_id in outputs or not isinstance(task, Mapping) or task.get("state") != "done":
            continue
        json_path = context.run_dir / "tasks" / task_id / "output.json"
        md_path = context.run_dir / "tasks" / task_id / "output.md"
        if json_path.is_file() or md_path.is_file():
            try:
                outputs[task_id] = read_task_output(context.run_dir / "tasks" / task_id, task["type"])
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise ValueError(f"タスク出力を読み込めません: {task_id}") from error
    return outputs


def _mapping_output(outputs: Mapping[str, Any], task_id: str) -> Mapping[str, Any]:
    value = outputs.get(task_id)
    if not isinstance(value, Mapping):
        raise ValueError(f"タスク出力がありません: {task_id}")
    return value


def _assemble_story(
    context: CodeTaskContext,
    manifest: Mapping[str, Any],
    assignment: Mapping[str, Any],
    slots: Sequence[Mapping[str, Any]],
    outputs: Mapping[str, Any],
) -> dict[str, Any]:
    threads = _copy_threads(assignment.get("threads"))
    cast_assignment = assignment.get("cast")
    if not isinstance(cast_assignment, list):
        raise ValueError("assignment の cast がありません")
    cast = [_assemble_cast(person, outputs) for person in cast_assignment]
    world = _assemble_world(assignment, outputs)
    events = _assemble_events(slots, outputs)
    theme = assignment.get("world", {}).get("theme") if isinstance(assignment.get("world"), Mapping) else None
    plot_types = [
        {"thread": thread["id"], "id": thread["plot_type"], "name": thread["plot_type_name"]}
        for thread in threads
    ]
    structures = [{"thread": thread["id"], "id": thread["structure"]} for thread in threads]
    story = {
        "meta": {
            "run_id": manifest["run_id"],
            "seed": manifest["seed"],
            "scale": deepcopy(manifest["scale"]),
            "input_ratio": manifest.get("input_ratio"),
            "input_type": manifest.get("input", {}).get("kind", "free"),
            "plot_types": plot_types,
            "structures": structures,
            "theme": deepcopy(theme),
        },
        "world": world,
        "cast": cast,
        "threads": threads,
        "events": events,
    }
    if story["meta"]["input_ratio"] is None:
        raise ValueError("manifest の input_ratio が未確定です")
    return story


def _copy_threads(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or not all(isinstance(item, Mapping) for item in value):
        raise ValueError("assignment の threads が不正です")
    return [deepcopy(dict(item)) for item in value]


_SINGLE_CHARACTER_FIELDS: tuple[str, ...] = (
    "name",
    "profile",
    "intro",
    "appearance",
    "motive",
    "personality",
    "values",
    "voice",
    "inner_conflict",
    "catchphrase",
)
_BACKSTORY_TASK = re.compile(r"^S5\.backstory-(c[0-9]+)-p([0-9]+)$")
_RELATIONSHIP_TASK = re.compile(r"^S5\.relationship-(c[0-9]+)-(c[0-9]+)$")


def _assemble_cast(person: Any, outputs: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(person, Mapping) or not isinstance(person.get("id"), str):
        raise ValueError("assignment の人物が不正です")
    person_id = person["id"]
    fields: dict[str, Any] = {}
    source_ids: list[str] = []
    for field in _SINGLE_CHARACTER_FIELDS:
        output = _mapping_output(outputs, f"S5.{field}-{person_id}")
        value = output.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"S5 の {field} 出力が不正です: {person_id}")
        fields[field] = value
        _extend_ids(source_ids, output.get("sources"))
    name_output = _mapping_output(outputs, f"S5.name-{person_id}")
    reading = name_output.get("reading")
    if not isinstance(reading, str) or not reading:
        raise ValueError(f"S5.name の reading が不正です: {person_id}")
    _extend_ids(source_ids, name_output.get("sources"))
    backstory = _collect_indexed_field(
        outputs, _BACKSTORY_TASK, person_id, "backstory", source_ids, require_at_least_one=True
    )
    relationship = _collect_indexed_field(
        outputs, _RELATIONSHIP_TASK, person_id, "relationship", source_ids, require_at_least_one=False
    )
    elements = person.get("elements")
    if not isinstance(elements, Mapping):
        raise ValueError(f"人物の elements が不正です: {person_id}")
    result: dict[str, Any] = {
        "id": person_id,
        "role": person.get("role"),
        "name": fields["name"],
        "reading": reading,
        "intro": fields["intro"],
        "profile": fields["profile"],
        "motive": fields["motive"],
        "appearance": fields["appearance"],
        "personality": fields["personality"],
        "values": fields["values"],
        "voice": fields["voice"],
        "inner_conflict": fields["inner_conflict"],
        "backstory": backstory,
        "relationship": relationship,
        "catchphrase": fields["catchphrase"],
        "elements": deepcopy(dict(elements)),
        "name_sound": deepcopy(person.get("name_sound")),
        "sources": source_ids,
    }
    if "suppressed_self_image" in person:
        result["suppressed_self_image"] = deepcopy(person["suppressed_self_image"])
    return result


def _assemble_world(assignment: Mapping[str, Any], outputs: Mapping[str, Any]) -> dict[str, Any]:
    world = assignment.get("world")
    catalog = assignment.get("world_sections")
    if not isinstance(world, Mapping) or not isinstance(catalog, list):
        raise ValueError("assignment の world が不正です")
    world_task_contexts = {
        f"S4.{entry['kind'] == 'single' and 'section' or 'item'}-{entry['id']}": entry
        for entry in assignment.get("world_tasks", [])
        if isinstance(entry, Mapping)
        and isinstance(entry.get("id"), str)
        and entry.get("kind") in {"single", "list"}
    }
    section_ids = [
        section.get("id")
        for section in catalog
        if isinstance(section, Mapping) and isinstance(section.get("id"), str)
    ]
    sections: list[dict[str, Any]] = []
    for section in catalog:
        if not isinstance(section, Mapping):
            raise ValueError("world_sections の項目が不正です")
        section_id = section.get("id")
        if not isinstance(section_id, str):
            raise ValueError("world section の ID が不正です")
        raw_outputs = [
            (task_id, value)
            for task_id, value in outputs.items()
            if isinstance(task_id, str)
            and (
                (
                    task_id in world_task_contexts
                    and world_task_contexts[task_id].get("section_id") == section_id
                )
                or _world_task_section_id(task_id, section_ids) == section_id
            )
            and isinstance(value, Mapping)
        ]
        raw_outputs.sort(
            key=lambda item: (
                0 if item[0] in world_task_contexts else 1,
                next(
                    (
                        index
                        for index, entry in enumerate(assignment.get("world_tasks", []))
                        if isinstance(entry, Mapping)
                        and f"S4.{entry.get('kind') == 'single' and 'section' or 'item'}-{entry.get('id')}" == item[0]
                    ),
                    0,
                ),
                item[0],
            )
        )
        source_ids: list[str] = []
        if section.get("kind") == "single":
            if not raw_outputs:
                raise ValueError(f"S4 の単一セクション出力が不正です: {section_id}")
            facet_bodies: list[str] = []
            for task_id, output in raw_outputs:
                body = output.get("body")
                if not isinstance(body, str) or not body:
                    raise ValueError(f"S4 の本文が不正です: {section_id}")
                _extend_ids(source_ids, output.get("sources"))
                task_context = world_task_contexts.get(task_id)
                if isinstance(task_context, Mapping):
                    viewpoint = task_context.get("viewpoint")
                    facet_number = task_context.get("facet_number")
                    if isinstance(viewpoint, str) and isinstance(facet_number, int):
                        facet_bodies.append(
                            f"### {viewpoint}\n#### 面 {facet_number}\n{body}"
                        )
                        continue
                facet_bodies.append(body)
            sections.append({"id": section_id, "name": section["name"], "kind": "single", "body": "\n\n".join(facet_bodies), "items": [], "sources": source_ids})
        else:
            items: list[dict[str, Any]] = []
            for _, output in raw_outputs:
                name, body = output.get("name"), output.get("body")
                if not isinstance(name, str) or not name or not isinstance(body, str) or not body:
                    raise ValueError(f"S4 の一覧項目が不正です: {section_id}")
                item_sources: list[str] = []
                _extend_ids(item_sources, output.get("sources"))
                _extend_ids(source_ids, item_sources)
                items.append({"name": name, "body": body, "sources": item_sources})
            if not items:
                raise ValueError(f"S4 の一覧出力がありません: {section_id}")
            sections.append({"id": section_id, "name": section["name"], "kind": "list", "body": None, "items": items, "sources": source_ids})
    required_world = {key: deepcopy(world.get(key)) for key in ("place", "era", "object", "theme")}
    required_world["sections"] = sections
    return required_world


def _world_task_section_id(task_id: str, section_ids: Sequence[Any] | None = None) -> str | None:
    if task_id.startswith("S4.section-"):
        suffix = task_id.removeprefix("S4.section-")
        if section_ids is None:
            return suffix
        for section_id in sorted(
            (value for value in section_ids if isinstance(value, str)),
            key=len,
            reverse=True,
        ):
            if suffix == section_id or suffix.startswith(f"{section_id}-"):
                return section_id
    if task_id.startswith("S4.item-"):
        suffix = task_id.removeprefix("S4.item-")
        if section_ids is None:
            return suffix.rsplit("-", 1)[0]
        for section_id in sorted(
            (value for value in section_ids if isinstance(value, str)),
            key=len,
            reverse=True,
        ):
            if suffix.startswith(f"{section_id}-"):
                return section_id
    return None


def _assemble_events(slots: Sequence[Mapping[str, Any]], outputs: Mapping[str, Any]) -> list[dict[str, Any]]:
    last_by_thread: dict[str, str] = {}
    for slot in slots:
        thread = slot.get("thread")
        event_id = slot.get("id")
        if isinstance(thread, str) and isinstance(event_id, str):
            last_by_thread[thread] = event_id
    events: list[dict[str, Any]] = []
    for slot in slots:
        event_id, thread_id = slot.get("id"), slot.get("thread")
        if not isinstance(event_id, str) or not isinstance(thread_id, str):
            raise ValueError("slot の ID 参照が不正です")
        output = _mapping_output(outputs, f"S7.event-{event_id}")
        event = {key: deepcopy(output.get(key)) for key in ("when", "where", "who", "why", "intent", "what", "result", "emotion", "foreshadowing", "sources")}
        event.update({"id": event_id, "stage": {"id": slot["stage"]["id"], "name": slot["stage"]["name"]}, "thread": thread_id})
        if event_id == last_by_thread[thread_id]:
            event["foreshadowing"] = ""
        events.append(event)
    return events


def _collect_indexed_field(
    outputs: Mapping[str, Any],
    pattern: re.Pattern[str],
    person_id: str,
    field: str,
    source_ids: list[str],
    *,
    require_at_least_one: bool,
) -> list[str]:
    """Gather one S5 item that has several per-character task instances.

    ``S5.backstory-<person>-p<n>`` and ``S5.relationship-<person>-<other>``
    each produce one entry per task rather than one task per character
    (story-pipeline.md S5: backstory is per time period, relationship is per
    other character).  This orders backstory by period number and
    relationship by the counterpart's ID, both taken from ``pattern``'s
    second capture group, which sorts numerically rather than lexically so
    ``c2`` precedes ``c10``.
    """

    entries: list[tuple[int, str]] = []
    for task_id, output in outputs.items():
        if not isinstance(task_id, str) or not isinstance(output, Mapping):
            continue
        match = pattern.match(task_id)
        if match is None or match.group(1) != person_id:
            continue
        value = output.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{pattern.pattern} の出力が不正です: {task_id}")
        ordinal = int(re.sub(r"[^0-9]", "", match.group(2)))
        entries.append((ordinal, value))
        _extend_ids(source_ids, output.get("sources"))
    if require_at_least_one and not entries:
        raise ValueError(f"{field} の出力がありません: {person_id}")
    entries.sort(key=lambda entry: entry[0])
    return [value for _, value in entries]


def _extend_ids(target: list[str], value: Any) -> None:
    # A name task can contain only naming instructions, with no material IDs.
    # The assembled element's sources are still checked by the story schema.
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError("sources が不正です")
    for item in value:
        if item not in target:
            target.append(item)


def _validate_references(
    story: Mapping[str, Any],
    run_input: Any,
    outputs: Mapping[str, Any],
    assignment: Mapping[str, Any] | None = None,
) -> None:
    if assignment is None and isinstance(outputs.get("S3.assign"), Mapping):
        assignment = outputs["S3.assign"]
    cast_ids = {person["id"] for person in story["cast"]}
    thread_ids = {thread["id"] for thread in story["threads"]}
    event_ids = {event["id"] for event in story["events"]}
    known_ids = cast_ids | thread_ids | event_ids
    known_ids.update(entry["id"] for entry in build_glossary(outputs))
    for person in story["cast"]:
        known_ids.add(person["name_sound"]["set_id"])
        known_ids.update(element["id"] for element in person["elements"].values())
        if person.get("suppressed_self_image") is not None:
            known_ids.add(person["suppressed_self_image"]["id"])
    for key in ("place", "era", "object"):
        known_ids.add(story["world"][key]["id"])
    if story["world"].get("theme") is not None:
        known_ids.add(story["world"]["theme"]["id"])
    for section in story["world"]["sections"]:
        known_ids.add(section["id"])
    world_tasks = (
        [*assignment.get("world_tasks", []), *assignment.get("world_fact_tasks", [])]
        if isinstance(assignment, Mapping) else None
    )
    if isinstance(world_tasks, list):
        for world_task in world_tasks:
            if not isinstance(world_task, Mapping):
                continue
            element = world_task.get("element")
            if isinstance(element, Mapping) and isinstance(element.get("id"), str):
                known_ids.add(element["id"])
            name_sound = world_task.get("name_sound")
            if isinstance(name_sound, Mapping) and isinstance(name_sound.get("set_id"), str):
                known_ids.add(name_sound["set_id"])
    if isinstance(run_input, Mapping):
        for paragraph in run_input.get("paragraphs", []):
            if isinstance(paragraph, Mapping) and isinstance(paragraph.get("id"), str):
                known_ids.add(paragraph["id"])
    for task_id, output in outputs.items():
        if isinstance(task_id, str) and task_id.startswith("S1.extract-") and isinstance(output, Mapping):
            from .story_materials import materials_from_outputs
            for material in materials_from_outputs({task_id: output}):
                if isinstance(material, Mapping) and isinstance(material.get("id"), str):
                    known_ids.add(material["id"])
    for event in story["events"]:
        if event["thread"] not in thread_ids:
            raise ValueError(f"存在しない thread を参照しています: {event['thread']}")
        if not set(event["who"]).issubset(cast_ids):
            raise ValueError(f"存在しない who を参照しています: {event['id']}")
        if not set(event["sources"]).issubset(known_ids):
            raise ValueError(f"存在しない sources を参照しています: {event['id']}")
    for person in story["cast"]:
        if not set(person["sources"]).issubset(known_ids):
            raise ValueError(f"存在しない人物 sources を参照しています: {person['id']}")
    for section in story["world"]["sections"]:
        if not set(section["sources"]).issubset(known_ids):
            raise ValueError(f"存在しない世界 sources を参照しています: {section['id']}")
        for item in section["items"]:
            if not set(item["sources"]).issubset(known_ids):
                raise ValueError(f"存在しない世界項目 sources を参照しています: {section['id']}")


def render_story_markdown(story: Mapping[str, Any], details: Mapping[str, str] | None = None) -> str:
    """Render story.md: event summaries, each followed by its scene detail.

    ``details`` maps an event ID to its S7.detail text (already joined across
    beats). An empty mapping renders the summary fields alone, which keeps
    the renderer useful for partial assembly contexts.
    """

    details = details or {}
    protagonist = next(person for person in story["cast"] if person["role"] == "protagonist")
    title_plot = story["meta"]["plot_types"][0]["name"]
    cast_names = {person["id"]: person["name"] for person in story["cast"]}

    lines = [f"# {title_plot}：{protagonist['name']}", ""]
    for index, event in enumerate(story["events"], start=1):
        names = "、".join(cast_names[person_id] for person_id in event["who"])
        lines.extend(
            [
                f"## {index}. {event['stage']['name']}",
                "",
                f"- いつ：{event['when']}",
                f"- どこで：{event['where']}",
                f"- 誰が：{names}",
                f"- 目的：{event['why']}",
                f"- 行動：{event['what']}",
                f"- 結果：{event['result']}",
            ]
        )
        detail = details.get(event["id"])
        if detail:
            lines.extend(["", detail])
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def render_characters_markdown(
    story: Mapping[str, Any], outputs: Mapping[str, Any] | None = None,
    glossary: Sequence[Mapping[str, str]] = (),
) -> str:
    """Render characters.md: every present S5 item, per character."""

    roles = _role_names()
    lines = ["# 登場人物", ""]
    for person in story["cast"]:
        lines.extend(
            [
                f"## {person['name']}",
                f"- 役割：{roles.get(person['role'], person['role'])}",
                f"- よみ：{person['reading']}",
            ]
        )
        if outputs is not None:
            sheet = _mapping_output(outputs, f"S5.facts-{person['id']}")
            lines.extend(["", render_character_sheet(sheet, glossary), "### 描写", ""])
        for field, label in _CHARACTER_FIELDS:
            value = person.get(field)
            if isinstance(value, str) and value:
                lines.append(f"- {label}：{value}")
            elif isinstance(value, list) and value:
                lines.append(f"- {label}：")
                lines.extend(f"  - {item}" for item in value)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def render_world_markdown(story: Mapping[str, Any]) -> str:
    """Render world.md in section, viewpoint, facet, and item order."""

    lines = ["# 世界", ""]
    for section in story["world"]["sections"]:
        lines.append(f"## {section['name']}")
        if section["kind"] == "single":
            lines.append(section["body"])
        else:
            lines.extend(f"- {item['name']}：{item['body']}" for item in section["items"])
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _role_names(*, repository_root: Path = _REPOSITORY_ROOT) -> dict[str, str]:
    """Return ``tables/roles.yaml`` role IDs mapped to their Japanese name."""

    return {
        role["id"]: role["name"]
        for role in load_table("roles", repository_root=repository_root)["roles"]
    }


def _collect_event_details(outputs: Mapping[str, Any]) -> dict[str, str]:
    """Group S7.detail scene text by event ID, in beat order.

    Task IDs follow ``S7.detail-<event>-b<beat>`` (docs/plan/phase-1.md).
    Missing detail tasks are simply omitted from the mapping.
    """

    beats_by_event: dict[str, list[tuple[int, str]]] = {}
    for task_id, value in outputs.items():
        if not isinstance(task_id, str) or not isinstance(value, str) or not value:
            continue
        match = _DETAIL_TASK_ID.match(task_id)
        if match is None:
            continue
        event_id, beat_number = match.group(1), int(match.group(2))
        beats_by_event.setdefault(event_id, []).append((beat_number, value))
    return {
        event_id: "\n\n".join(text for _, text in sorted(beats))
        for event_id, beats in beats_by_event.items()
    }


def _char_count(text: str) -> int:
    """Count characters per task-model.md §3.3: NFC, whitespace excluded."""

    normalized = unicodedata.normalize("NFC", text)
    return sum(1 for character in normalized if not character.isspace())


def _compute_volume(
    manifest: Mapping[str, Any],
    texts: Mapping[str, str],
) -> tuple[dict[str, Any], list[str]]:
    """Aggregate character counts against the floor×multiplier (story-pipeline §8)."""

    scale = manifest.get("scale")
    if not isinstance(scale, Mapping) or not isinstance(scale.get("preset"), str):
        raise ValueError("manifest の scale.preset が不正です")
    multiplier = multiplier_for_preset(scale["preset"], repository_root=_REPOSITORY_ROOT)
    floor_chars = load_table("volume", repository_root=_REPOSITORY_ROOT)["floor_chars"]

    volume: dict[str, Any] = {}
    warnings: list[str] = []
    for key in ("characters", "world", "story"):
        actual = _char_count(texts[key])
        floor = int(floor_chars[key]) * multiplier
        volume[key] = {"chars": actual, "floor_chars": floor}
        if actual < floor:
            warnings.append(f"分量が最低ラインに満たない：{_VOLUME_LABELS[key]} {actual}/{floor}")
    return volume, warnings


__all__ = [
    "render_characters_markdown",
    "render_story_markdown",
    "render_world_markdown",
    "story_s9_assemble",
]
