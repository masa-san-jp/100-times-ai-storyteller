"""Deterministic assembly of the canonical story for story-pipeline S9."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from .manifest import load_manifest
from .orchestrator import CodeTaskContext, CodeTaskResult
from .storage import atomic_write_json, atomic_write_text
from .validation import validate_document


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_STORY_SCHEMA = _REPOSITORY_ROOT / "schemas" / "story.schema.json"


def story_s9_assemble(context: CodeTaskContext) -> CodeTaskResult:
    """Assemble, validate, and persist ``story.json`` and ``story.md``."""

    outputs = _all_outputs(context)
    assignment = _mapping_output(outputs, "S3.assign")
    slots_output = _mapping_output(outputs, "S6.expand")
    slots = slots_output.get("slots")
    if not isinstance(slots, list) or not all(isinstance(slot, Mapping) for slot in slots):
        raise ValueError("S6.expand の slots がありません")

    manifest = load_manifest(context.run_dir / "manifest.json")
    story = _assemble_story(context, manifest, assignment, slots, outputs)
    validate_document(story, _STORY_SCHEMA)
    _validate_references(story, context.run_input, outputs)

    story_dir = context.run_dir / "story"
    atomic_write_json(story_dir / "story.json", story)
    atomic_write_text(story_dir / "story.md", render_story_markdown(story), encoding="utf-8")
    return CodeTaskResult(output=story)


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
        path = context.run_dir / "tasks" / task_id / "output.json"
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8") as stream:
                outputs[task_id] = json.load(stream)
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


def _assemble_cast(person: Any, outputs: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(person, Mapping) or not isinstance(person.get("id"), str):
        raise ValueError("assignment の人物が不正です")
    person_id = person["id"]
    fields: dict[str, Any] = {}
    source_ids: list[str] = []
    for field in ("name", "profile", "intro", "appearance", "motive", "catchphrase"):
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
            and _world_task_section_id(task_id) == section_id
            and isinstance(value, Mapping)
        ]
        raw_outputs.sort(key=lambda item: item[0])
        source_ids: list[str] = []
        if section.get("kind") == "single":
            if len(raw_outputs) != 1:
                raise ValueError(f"S4 の単一セクション出力が不正です: {section_id}")
            output = raw_outputs[0][1]
            body = output.get("body")
            if not isinstance(body, str) or not body:
                raise ValueError(f"S4 の本文が不正です: {section_id}")
            _extend_ids(source_ids, output.get("sources"))
            sections.append({"id": section_id, "name": section["name"], "kind": "single", "body": body, "items": [], "sources": source_ids})
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


def _world_task_section_id(task_id: str) -> str | None:
    if task_id.startswith("S4.section-"):
        return task_id.removeprefix("S4.section-")
    if task_id.startswith("S4.item-"):
        return task_id.removeprefix("S4.item-").rsplit("-", 1)[0]
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


def _extend_ids(target: list[str], value: Any) -> None:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise ValueError("sources が不正です")
    for item in value:
        if item not in target:
            target.append(item)


def _validate_references(story: Mapping[str, Any], run_input: Any, outputs: Mapping[str, Any]) -> None:
    cast_ids = {person["id"] for person in story["cast"]}
    thread_ids = {thread["id"] for thread in story["threads"]}
    event_ids = {event["id"] for event in story["events"]}
    known_ids = cast_ids | thread_ids | event_ids
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
    if isinstance(run_input, Mapping):
        for paragraph in run_input.get("paragraphs", []):
            if isinstance(paragraph, Mapping) and isinstance(paragraph.get("id"), str):
                known_ids.add(paragraph["id"])
    for task_id, output in outputs.items():
        if isinstance(task_id, str) and task_id.startswith("S1.extract-") and isinstance(output, Mapping):
            for material in output.get("materials", []):
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


def render_story_markdown(story: Mapping[str, Any]) -> str:
    """Render the human-readable canonical-story template."""

    protagonist = next(person for person in story["cast"] if person["role"] == "protagonist")
    title_plot = story["meta"]["plot_types"][0]["name"]
    lines = [f"# {title_plot}：{protagonist['name']}", "", "## 登場人物", ""]
    for person in story["cast"]:
        lines.extend(
            [
                f"### {person['name']}",
                f"- 役割：{person['role']}",
                f"- 紹介：{person['intro']}",
                f"- 外見：{person['appearance']}",
                f"- 決め台詞：{person['catchphrase']}",
                "",
            ]
        )
    lines.extend(["## 世界", ""])
    for section in story["world"]["sections"]:
        lines.extend([f"### {section['name']}"])
        if section["kind"] == "single":
            lines.append(section["body"])
        else:
            lines.extend(f"- {item['name']}：{item['body']}" for item in section["items"])
        lines.append("")
    lines.extend(["## 出来事", ""])
    cast_names = {person["id"]: person["name"] for person in story["cast"]}
    for index, event in enumerate(story["events"], start=1):
        names = "、".join(cast_names[person_id] for person_id in event["who"])
        lines.append(
            f"{index}. {event['when']}、{event['where']}で、{names}が、{event['why']}ために、{event['what']}。その結果、{event['result']}。"
        )
    lines.append("")
    return "\n".join(lines)


__all__ = ["render_story_markdown", "story_s9_assemble"]
