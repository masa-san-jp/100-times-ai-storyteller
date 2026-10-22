"""Deterministic structure expansion for story-pipeline S6."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from .manifest import load_manifest
from .orchestrator import CodeTaskContext, CodeTaskResult, TaskSpec
from .story_ledger import (
    SKELETON_CLIMAX, SKELETON_LACK, SKELETON_RESOLUTION, SKELETON_TURN,
    assigned_elements, static_ledgers,
)
from .story_s7 import event_tasks
from .tables import load_table
from .validation import validate_document
from .volume import compute_initial_volume


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_REQUIRED_ROLES = frozenset(
    {"protagonist", "messenger", "supporter", "adversary", "bystander"}
)
_CONDITION_CHECK_VERSION = 5
# ADR-0011: S6.expand v6 and later build the staged graph (skeleton, cumulative
# context, no plot conditions).
_STAGED_VERSION = 6


def _is_staged(context: CodeTaskContext) -> bool:
    """Only S6.expand's own definition version decides the graph (story-pipeline S6.plot)."""

    definition = getattr(context, "definition", None)
    version = definition.get("version", 0) if isinstance(definition, Mapping) else 0
    return isinstance(version, int) and version >= _STAGED_VERSION


def story_s6_expand(context: CodeTaskContext) -> CodeTaskResult:
    """Expand the S3 assignment into chronological slots and the S7-S9 DAG."""

    staged = _is_staged(context)
    assignment = _assignment_from_context(context)
    structures = load_table("structures", repository_root=_REPOSITORY_ROOT)["templates"]
    plots = load_table("plot_types", repository_root=_REPOSITORY_ROOT)["types"]
    plot_by_id = {plot["id"]: plot for plot in plots}
    world_catalog = load_table("world_sections", repository_root=_REPOSITORY_ROOT)[
        "sections"
    ]
    world_by_id = {section["id"]: section for section in world_catalog}

    threads = _require_list(assignment, "threads")
    cast = _require_list(assignment, "cast")
    absent_roles = _require_string_list(assignment, "absent_roles")
    world = _require_mapping(assignment, "world")
    generated_world = _world_outputs(context.dependency_outputs, assignment)
    character_outputs = _character_outputs(context.dependency_outputs, cast)

    per_thread: list[list[dict[str, Any]]] = []
    for thread in threads:
        if not isinstance(thread, Mapping):
            raise ValueError("assignment の thread が不正です")
        per_thread.append(
            _build_thread_slots(
                context,
                thread,
                structures,
                plot_by_id,
                cast,
                character_outputs,
                absent_roles,
                world,
                world_by_id,
                generated_world,
                staged=staged,
            )
        )

    chronological = _interleave_threads(context, threads, per_thread)
    plan: dict[str, Any] | None = None
    if staged:
        plan = _plan_skeleton(threads, chronological, plot_by_id)
        _assign_protagonist(chronological, plan, cast, character_outputs)
    scene_counts = _scene_counts(context, len(chronological))
    slots = _finalise_slots(chronological, scene_counts)
    output: dict[str, Any] = {"slots": slots}
    if plan is not None:
        for slot in slots:
            slot["skeleton"] = [
                element["task"] for element in assigned_elements(plan, slot["id"])
            ]
        output["skeleton"] = plan
        output["ledgers"] = _static_ledgers(context, assignment)
    validate_document(output, _REPOSITORY_ROOT / "schemas" / "story" / "slots.schema.json")

    additions = _build_downstream_tasks(context, slots, plan)
    return CodeTaskResult(output=output, add_tasks=additions)


def _static_ledgers(
    context: CodeTaskContext, assignment: Mapping[str, Any]
) -> dict[str, list[str]]:
    """The input, cast and world ledgers (story-pipeline S6.plot §1), built once."""

    run_dir = getattr(context, "run_dir", None)
    manifest = None
    if isinstance(run_dir, Path) and (run_dir / "manifest.json").is_file():
        manifest = load_manifest(run_dir / "manifest.json")
    else:
        run_dir = None
    return static_ledgers(assignment, context.dependency_outputs, run_dir, manifest)


def _assignment_from_context(context: CodeTaskContext) -> Mapping[str, Any]:
    assignment = context.inputs.get("assignment")
    if assignment is None:
        assignment = context.dependency_outputs.get("S3.assign")
    if not isinstance(assignment, Mapping):
        raise ValueError("S3.assign の assignment がありません")
    return assignment


def _require_list(value: Mapping[str, Any], key: str) -> list[Any]:
    result = value.get(key)
    if not isinstance(result, list):
        raise ValueError(f"assignment の {key} が不正です")
    return result


def _require_string_list(value: Mapping[str, Any], key: str) -> list[str]:
    result = _require_list(value, key)
    if not all(isinstance(item, str) for item in result):
        raise ValueError(f"assignment の {key} が不正です")
    return result


def _require_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    result = value.get(key)
    if not isinstance(result, Mapping):
        raise ValueError(f"assignment の {key} が不正です")
    return result


def allocate_stage_counts(
    event_count: int,
    stages: Sequence[Mapping[str, Any]],
    random_source: Any,
) -> list[int]:
    """Allocate events with one per stage and seeded largest-remainder rounding."""

    if isinstance(event_count, bool) or not isinstance(event_count, int):
        raise ValueError("筋の出来事数が不正です")
    if event_count < len(stages) or not stages:
        raise ValueError("各段階に最低1件を割り当てられません")

    weights: list[float] = []
    for stage in stages:
        weight = stage.get("weight")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise ValueError("段階の weight が不正です")
        if weight < 0:
            raise ValueError("段階の weight は0以上でなければなりません")
        weights.append(float(weight))
    total_weight = sum(weights)
    if total_weight <= 0:
        raise ValueError("段階の weight の合計が0です")

    remaining = event_count - len(stages)
    quotas = [remaining * weight / total_weight for weight in weights]
    counts = [1 + int(quota) for quota in quotas]
    extras = remaining - sum(count - 1 for count in counts)
    tie_order = list(range(len(stages)))
    random_source.shuffle(tie_order)
    tie_rank = {stage_index: rank for rank, stage_index in enumerate(tie_order)}
    ranked = sorted(
        range(len(stages)),
        key=lambda stage_index: (
            -(quotas[stage_index] - int(quotas[stage_index])),
            tie_rank[stage_index],
        ),
    )
    for stage_index in ranked[:extras]:
        counts[stage_index] += 1
    return counts


def _build_thread_slots(
    context: CodeTaskContext,
    thread: Mapping[str, Any],
    structures: Mapping[str, Any],
    plot_by_id: Mapping[str, Mapping[str, Any]],
    cast: Sequence[Any],
    character_outputs: Mapping[str, Mapping[str, Any]],
    absent_roles: Sequence[str],
    world: Mapping[str, Any],
    world_by_id: Mapping[str, Mapping[str, Any]],
    generated_world: Mapping[str, list[Mapping[str, Any]]],
    *,
    staged: bool = False,
) -> list[dict[str, Any]]:
    thread_id = thread.get("id")
    structure_id = thread.get("structure")
    plot_id = thread.get("plot_type")
    events = thread.get("events")
    if not isinstance(thread_id, str) or not isinstance(structure_id, str):
        raise ValueError("thread の ID または structure が不正です")
    if not isinstance(plot_id, str) or not isinstance(events, int) or isinstance(events, bool):
        raise ValueError(f"thread の plot_type または events が不正です: {thread_id}")
    structure = structures.get(structure_id)
    plot = plot_by_id.get(plot_id)
    if not isinstance(structure, Mapping) or not isinstance(structure.get("stages"), list):
        raise ValueError(f"構造テンプレートがありません: {structure_id}")
    if not isinstance(plot, Mapping):
        raise ValueError(f"プロット型がありません: {plot_id}")
    stages = structure["stages"]
    if not all(isinstance(stage, Mapping) for stage in stages):
        raise ValueError(f"構造テンプレートの段階が不正です: {structure_id}")
    stage_counts = allocate_stage_counts(events, stages, context.random)
    required_by_stage = _required_events_by_stage(plot, structure_id)
    cast_by_role = _cast_by_role(cast)
    plot_context = {
        "id": plot["id"],
        "name": plot["name"],
        "conflict": plot["conflict"],
    }
    if staged:
        # S7.event v22 shows the plot type's time design and pacing.
        plot_context["time_design"] = plot["time_design"]
        plot_context["pacing"] = plot["pacing"]
    thread_context = {
        "id": thread_id,
        "kind": thread.get("kind"),
        "part": thread.get("part"),
        "plot_type": plot_id,
        "plot_type_name": thread.get("plot_type_name", plot["name"]),
        "events": events,
        "structure": structure_id,
    }

    slots: list[dict[str, Any]] = []
    thread_stage_slots: dict[str, list[dict[str, Any]]] = {}
    for stage, count in zip(stages, stage_counts):
        stage_id = stage.get("id")
        if not isinstance(stage_id, str):
            raise ValueError("段階の ID が不正です")
        stage_plot_context = deepcopy(plot_context)
        if stage.get("climax") is True:
            stage_plot_context["climax"] = plot["climax"]
        stage_slots: list[dict[str, Any]] = []
        for _ in range(count):
            characters, absent_note = _assign_characters(
                context,
                stage,
                cast_by_role,
                cast,
                character_outputs,
                absent_roles,
            )
            selected_world = _select_world_sections(
                stage,
                world_by_id,
                generated_world,
            )
            slot = {
                "thread": thread_id,
                "thread_context": deepcopy(thread_context),
                "plot": deepcopy(stage_plot_context),
                "stage": deepcopy(dict(stage)),
                "required_events": [],
                "characters": characters,
                "absent_role_note": absent_note,
                "object": deepcopy(world.get("object")) if stage.get("object") else None,
                "theme": deepcopy(world.get("theme")),
                "world_sections": selected_world,
                "_stage_id": stage_id,
                "_turns": [],
            }
            stage_slots.append(slot)
            slots.append(slot)
        thread_stage_slots[stage_id] = stage_slots

    numbers = _required_event_numbers(plot, structure_id)
    for stage_id, required_events in required_by_stage.items():
        stage_slots = thread_stage_slots.get(stage_id)
        if not stage_slots:
            raise ValueError(
                f"必須の出来事の割当先段階にスロットがありません: {thread_id}/{stage_id}"
            )
        for index, required_event in enumerate(required_events):
            target = stage_slots[index % len(stage_slots)]
            target["required_events"].append(required_event)
            # The plot type's n-th required event, for the skeleton's turn n.
            target["_turns"].append(numbers[stage_id][index])
    return slots


def _required_event_numbers(
    plot: Mapping[str, Any], structure_id: str
) -> dict[str, list[int]]:
    """1-based positions of the plot type's required events, by stage."""

    result: dict[str, list[int]] = {}
    for number, event in enumerate(plot["required_events"], start=1):
        result.setdefault(event["stages"][structure_id], []).append(number)
    return result


def _required_events_by_stage(
    plot: Mapping[str, Any], structure_id: str
) -> dict[str, list[str]]:
    raw_events = plot.get("required_events")
    if not isinstance(raw_events, list):
        raise ValueError("プロット型の required_events が不正です")
    result: dict[str, list[str]] = {}
    for event in raw_events:
        if not isinstance(event, Mapping):
            raise ValueError("required_events の項目が不正です")
        description = event.get("description")
        stages = event.get("stages")
        if not isinstance(description, str) or not isinstance(stages, Mapping):
            raise ValueError("required_events の項目が不正です")
        stage_id = stages.get(structure_id)
        if not isinstance(stage_id, str):
            raise ValueError(f"required_events に構造の割当がありません: {structure_id}")
        result.setdefault(stage_id, []).append(description)
    return result


def _cast_by_role(cast: Sequence[Any]) -> dict[str, list[Mapping[str, Any]]]:
    by_role: dict[str, list[Mapping[str, Any]]] = {}
    for person in cast:
        if not isinstance(person, Mapping):
            raise ValueError("assignment の人物が不正です")
        person_id = person.get("id")
        role = person.get("role")
        if not isinstance(person_id, str) or not isinstance(role, str) or role not in _REQUIRED_ROLES:
            raise ValueError("assignment の人物IDまたは役が不正です")
        by_role.setdefault(role, []).append(person)
    return by_role


def _assign_characters(
    context: CodeTaskContext,
    stage: Mapping[str, Any],
    cast_by_role: Mapping[str, Sequence[Mapping[str, Any]]],
    cast: Sequence[Any],
    character_outputs: Mapping[str, Mapping[str, Any]],
    absent_roles: Sequence[str],
) -> tuple[list[dict[str, Any]], str | None]:
    roles = stage.get("roles")
    if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
        raise ValueError("段階の roles が不正です")
    selected: list[Mapping[str, Any]] = []
    selected_ids: set[str] = set()
    absent = False
    for role in roles:
        candidates = cast_by_role.get(role, ())
        if candidates:
            person = context.random.choice(candidates)
            person_id = person["id"]
            if person_id not in selected_ids:
                selected.append(person)
                selected_ids.add(person_id)
        elif role in absent_roles:
            absent = True
        else:
            raise ValueError(f"段階が要求する役の人物がありません: {role}")

    remaining = [
        person
        for person in cast
        if isinstance(person, Mapping) and person.get("id") not in selected_ids
    ]
    # An event needs at least one character: when every required role is absent
    # (a small cast), the "0-2 more" draw cannot come up empty.
    minimum_extra = 0 if selected else 1
    extra_count = context.random.randint(minimum_extra, min(2, len(remaining))) if remaining else 0
    if extra_count:
        selected.extend(context.random.sample(remaining, extra_count))

    characters: list[dict[str, Any]] = [
        _character_record(person, character_outputs) for person in selected
    ]
    absent_note = stage.get("absent_role_note") if absent else None
    if absent_note is not None and not isinstance(absent_note, str):
        raise ValueError("段階の absent_role_note が不正です")
    return characters, absent_note


def _character_record(
    person: Mapping[str, Any], character_outputs: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    person_id = person.get("id")
    if not isinstance(person_id, str):
        raise ValueError("人物IDが不正です")
    output = character_outputs.get(person_id)
    if not isinstance(output, Mapping):
        raise ValueError(f"S5 の人物出力がありません: {person_id}")
    name = output.get("name")
    motive = output.get("motive")
    if not isinstance(name, str) or not name or not isinstance(motive, str) or not motive:
        raise ValueError(f"S5 の人物出力が不正です: {person_id}")
    character = {
        "id": person_id,
        "name": name,
        "role": person["role"],
        "motive": motive,
    }
    for field in ("intro", "voice"):
        value = output.get(field)
        if isinstance(value, str) and value:
            character[field] = value
    return character


def _world_outputs(
    dependency_outputs: Mapping[str, Any],
    assignment: Mapping[str, Any] | None = None,
) -> dict[str, list[Mapping[str, Any]]]:
    task_contexts: dict[str, tuple[int, str]] = {}
    world_tasks = assignment.get("world_tasks") if isinstance(assignment, Mapping) else None
    if isinstance(world_tasks, list):
        for order, world_task in enumerate(world_tasks):
            if not isinstance(world_task, Mapping):
                continue
            task_key = world_task.get("id")
            section_id = world_task.get("section_id")
            kind = world_task.get("kind")
            if isinstance(task_key, str) and isinstance(section_id, str) and kind in {"single", "list"}:
                task_contexts[f"S4.{kind == 'single' and 'section' or 'item'}-{task_key}"] = (
                    order,
                    section_id,
                )
    captured: list[tuple[int, str, Mapping[str, Any]]] = []
    for task_id, output in dependency_outputs.items():
        if not isinstance(task_id, str) or not task_id.startswith("S4."):
            continue
        context = task_contexts.get(task_id)
        if context is not None:
            order, section_id = context
        elif task_id.startswith("S4.section-"):
            order, section_id = 0, task_id.removeprefix("S4.section-")
        elif task_id.startswith("S4.item-"):
            order, section_id = 0, task_id.removeprefix("S4.item-").rsplit("-", 1)[0]
        else:
            continue
        if not isinstance(output, Mapping):
            raise ValueError(f"S4 の出力が不正です: {task_id}")
        captured.append((order, task_id, output))
    result: dict[str, list[Mapping[str, Any]]] = {}
    for _order, _task_id, output in sorted(captured, key=lambda item: (item[0], item[1])):
        task_id = _task_id
        context = task_contexts.get(task_id)
        if context is not None:
            section_id = context[1]
        elif task_id.startswith("S4.section-"):
            section_id = task_id.removeprefix("S4.section-")
        else:
            section_id = task_id.removeprefix("S4.item-").rsplit("-", 1)[0]
        result.setdefault(section_id, []).append(output)
    return result


def _character_outputs(
    dependency_outputs: Mapping[str, Any], cast: Sequence[Any]
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for person in cast:
        if not isinstance(person, Mapping) or not isinstance(person.get("id"), str):
            raise ValueError("assignment の人物が不正です")
        person_id = person["id"]
        name_output = dependency_outputs.get(f"S5.name-{person_id}")
        motive_output = dependency_outputs.get(f"S5.motive-{person_id}")
        if f"S5.motive-{person_id}" not in dependency_outputs:
            # The motive task was skipped (task-model §6.7).
            motive_output = {"motive": "（未定）"}
        if not isinstance(name_output, Mapping) or not isinstance(motive_output, Mapping):
            raise ValueError(f"S5 の名前または動機の出力がありません: {person_id}")
        name = name_output.get("name")
        motive = motive_output.get("motive")
        if not isinstance(name, str) or not isinstance(motive, str):
            raise ValueError(f"S5 の名前または動機の出力が不正です: {person_id}")
        character_output = {"name": name, "motive": motive}
        # P1-17 adds S5.voice and other character fields independently.  Keep
        # the slot format compatible with both branches: detail cards use the
        # fields when present and omit them while the task is not available.
        for field in ("intro", "voice"):
            optional_output = dependency_outputs.get(f"S5.{field}-{person_id}")
            if not isinstance(optional_output, Mapping):
                continue
            value = optional_output.get(field)
            if isinstance(value, str) and value:
                character_output[field] = value
        result[person_id] = character_output
    return result


def _select_world_sections(
    stage: Mapping[str, Any],
    world_by_id: Mapping[str, Mapping[str, Any]],
    generated_world: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, str]]:
    stage_sections = stage.get("world_sections")
    if not isinstance(stage_sections, list) or not all(
        isinstance(section_id, str) for section_id in stage_sections
    ):
        raise ValueError("段階の world_sections が不正です")
    selected_ids = [
        section_id
        for section_id in stage_sections
        if section_id in world_by_id and section_id in generated_world
    ][:2]
    if not selected_ids and "place" in world_by_id and "place" in generated_world:
        selected_ids = ["place"]
    if not selected_ids:
        raise ValueError("利用できる世界セクションがありません")

    result: list[dict[str, str]] = []
    for section_id in selected_ids:
        outputs = generated_world[section_id]
        bodies: list[str] = []
        for output in outputs:
            body = output.get("body")
            if not isinstance(body, str) or not body:
                raise ValueError(f"S4 の本文が不正です: {section_id}")
            name = output.get("name")
            bodies.append(f"{name}：{body}" if isinstance(name, str) and name else body)
        if not bodies:
            raise ValueError(f"S4 の出力が空です: {section_id}")
        # S4 facets are concatenated in task order before becoming an event
        # excerpt.  Keep the excerpt local enough for the S7 card budget while
        # preserving the beginning of that deterministic concatenation.
        joined = "\n".join(bodies)
        result.append(
            {
                "id": section_id,
                "name": world_by_id[section_id]["name"],
                "body": _world_excerpt(joined, 1200),
            }
        )
    return result


def _world_excerpt(body: str, budget: int) -> str:
    """Keep a leading sequence of complete paragraphs whenever it fits."""
    if len(body) <= budget:
        return body
    prefix = body[:budget]
    # Facets are joined by newlines, and paragraphs inside a facet retain
    # their newlines. Do not start a later paragraph without room to finish.
    boundary = body.rfind("\n", 0, budget + 1)
    if boundary > 0:
        return body[:boundary].rstrip()
    # A single paragraph can exceed the whole excerpt budget. In that case,
    # prefer its last complete sentence over a mid-sentence cut.
    sentence_end = max(prefix.rfind(mark) for mark in "。！？!?.")
    return prefix[:sentence_end + 1] if sentence_end >= 0 else prefix


def _interleave_threads(
    context: CodeTaskContext,
    threads: Sequence[Any],
    per_thread: Sequence[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    if not per_thread:
        raise ValueError("thread がありません")

    part_threads = [
        (index, thread)
        for index, thread in enumerate(threads)
        if isinstance(thread, Mapping)
        and thread.get("kind") == "main"
        and thread.get("part") is not None
    ]
    if part_threads:
        main_threads = [
            thread
            for thread in threads
            if isinstance(thread, Mapping) and thread.get("kind") == "main"
        ]
        if len(main_threads) != len(part_threads) or any(
            not isinstance(thread, Mapping) or thread.get("kind") != "main"
            for thread in threads
        ):
            raise ValueError("大河には副筋を置けません")
        numbered_parts: list[tuple[int, int, list[dict[str, Any]]]] = []
        for index, thread in part_threads:
            part = thread.get("part")
            if isinstance(part, bool) or not isinstance(part, int) or part < 1:
                raise ValueError("大河の part が不正です")
            numbered_parts.append((part, index, per_thread[index]))
        numbered_parts.sort(key=lambda item: item[0])
        if [part for part, _index, _slots in numbered_parts] != list(
            range(1, len(numbered_parts) + 1)
        ):
            raise ValueError("大河の part は1から連続していなければなりません")
        # 大河の部は、それぞれが主筋である。部を単一の主筋として
        # 連結し、副筋を部の途中へ差し込まない。
        result: list[dict[str, Any]] = []
        for _part, _index, slots in numbered_parts:
            result.extend(slots)
        return result

    main_index = next(
        (index for index, thread in enumerate(threads) if isinstance(thread, Mapping) and thread.get("kind") == "main"),
        0,
    )
    main_slots = per_thread[main_index]
    main_stage_ids: list[str] = []
    for slot in main_slots:
        stage_id = slot["_stage_id"]
        if not main_stage_ids or main_stage_ids[-1] != stage_id:
            main_stage_ids.append(stage_id)
    main_groups: list[list[dict[str, Any]]] = []
    for stage_id in main_stage_ids:
        main_groups.append([slot for slot in main_slots if slot["_stage_id"] == stage_id])

    # A side thread is interleaved in a gap between two main-thread stages.
    # The one-stage case has no interior gap, so retain one fallback gap
    # after the main stage for custom structures.
    interior_gap_count = max(0, len(main_groups) - 1)
    gap_count = interior_gap_count or 1
    side_by_gap: dict[int, list[tuple[int, dict[str, Any]]]] = {
        gap: [] for gap in range(gap_count)
    }
    for index, slots in enumerate(per_thread):
        if (
            index == main_index
            or not isinstance(threads[index], Mapping)
            or threads[index].get("kind") == "main"
        ):
            continue
        gaps = sorted(context.random.randrange(gap_count) for _ in slots)
        for gap, slot in zip(gaps, slots):
            side_by_gap[gap].append((index, slot))

    result: list[dict[str, Any]] = []
    if interior_gap_count:
        result.extend(main_groups[0])
    for gap in range(gap_count):
        grouped: dict[int, list[dict[str, Any]]] = {}
        for thread_index, slot in side_by_gap[gap]:
            grouped.setdefault(thread_index, []).append(slot)
        thread_order = list(grouped)
        context.random.shuffle(thread_order)
        for thread_index in thread_order:
            result.extend(grouped[thread_index])
        if interior_gap_count:
            result.extend(main_groups[gap + 1])
        elif gap == 0:
            result.extend(main_groups[0])
    return result


def _finalise_slots(
    chronological: Sequence[Mapping[str, Any]], scene_counts: Sequence[int]
) -> list[dict[str, Any]]:
    if len(chronological) != len(scene_counts):
        raise ValueError("出来事と場面数の件数が一致しません")
    result: list[dict[str, Any]] = []
    for index, raw_slot in enumerate(chronological, start=1):
        beats = _assign_beats(scene_counts[index - 1])
        slot = {
            "id": f"e{index:03d}",
            "thread": raw_slot["thread"],
            "thread_context": deepcopy(raw_slot["thread_context"]),
            "plot": deepcopy(raw_slot["plot"]),
            "stage": deepcopy(raw_slot["stage"]),
            "required_events": list(raw_slot["required_events"]),
            "characters": deepcopy(raw_slot["characters"]),
            "absent_role_note": raw_slot["absent_role_note"],
            "object": deepcopy(raw_slot["object"]),
            "theme": deepcopy(raw_slot["theme"]),
            "world_sections": deepcopy(raw_slot["world_sections"]),
            "beats": beats,
        }
        result.append(slot)
    return result


def _scene_counts(context: CodeTaskContext, event_count: int) -> list[int]:
    """Read or calculate the §8/§10 story allocation for chronological events."""

    scale = getattr(context, "scale", None)
    run_dir = getattr(context, "run_dir", None)
    if not isinstance(scale, Mapping) and isinstance(run_dir, Path):
        manifest = load_manifest(run_dir / "manifest.json")
        scale = manifest.get("scale")

    if isinstance(scale, Mapping):
        derived = scale.get("derived")
        if not isinstance(derived, Mapping):
            raise ValueError("manifest の scale.derived が不正です")
        volume = derived.get("volume")
        if not isinstance(volume, Mapping) or not isinstance(volume.get("story"), Mapping):
            initial_volume = compute_initial_volume(
                scale, repository_root=_REPOSITORY_ROOT
            )
            if isinstance(volume, Mapping):
                volume = {**volume, "story": initial_volume["story"]}
            else:
                volume = initial_volume
        story = volume.get("story")
        if not isinstance(story, Mapping):
            raise ValueError("分量配分の story がありません")
        counts = story.get("scene_counts")
        if (
            isinstance(counts, list)
            and len(counts) == event_count
            and all(
                isinstance(count, int) and not isinstance(count, bool) and count >= 1
                for count in counts
            )
        ):
            return list(counts)
        raise ValueError("分量配分の出来事数とS6のスロット数が一致しません")

    # Lightweight S6 unit contexts do not have a manifest.  Apply the same
    # short-scale floor and lower bound as volume.py so they still exercise the
    # production-sized DAG rather than silently creating one scene per event.
    volume_table = load_table("volume", repository_root=_REPOSITORY_ROOT)
    total_beats = max(
        event_count,
        math.ceil(
            volume_table["floor_chars"]["story"]
            / volume_table["task_chars"]["story_beat"][0]
        ),
    )
    base, extra = divmod(total_beats, event_count)
    return [base + (1 if index < extra else 0) for index in range(event_count)]


def _assign_beats(scene_count: int) -> list[dict[str, str]]:
    """Return the beat functions assigned by tables/beats.yaml."""

    if isinstance(scene_count, bool) or not isinstance(scene_count, int) or scene_count < 1:
        raise ValueError("場面数が不正です")
    table = load_table("beats", repository_root=_REPOSITORY_ROOT)
    beat_by_id = {
        beat["id"]: beat for beat in table["beats"] if isinstance(beat, Mapping)
    }
    if scene_count < 4:
        selected = next(
            (
                entry["beats"]
                for entry in table["short_sequences"]
                if entry.get("scene_count") == scene_count
            ),
            None,
        )
        if selected is None:
            raise ValueError(f"場面数に対応する短い並びがありません: {scene_count}")
        beat_ids = list(selected)
    else:
        sequence = list(table["sequence"])
        if scene_count <= len(sequence):
            beat_ids = sequence[:scene_count]
        else:
            # Keep the opening, turn, and aftermath fixed while repeating the
            # table's repeatable development function in the middle.
            beat_ids = [sequence[0], *([sequence[1]] * (scene_count - 3)), *sequence[2:]]

    result: list[dict[str, str]] = []
    for beat_id in beat_ids:
        beat = beat_by_id.get(beat_id)
        if not isinstance(beat, Mapping):
            raise ValueError(f"未知の場面の働きです: {beat_id}")
        result.append(
            {
                "id": beat["id"],
                "name": beat["name"],
                "definition": beat["definition"],
            }
        )
    return result


def _plan_skeleton(
    threads: Sequence[Any],
    chronological: Sequence[Mapping[str, Any]],
    plot_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Decide the skeleton's elements and the slots they are assigned to.

    Element 2 (決定的な瞬間) goes to the first slot of the main thread whose stage
    is a climax stage, element 3 (結末の後) to the main thread's last slot, and
    each turn n to the slot that holds the plot type's n-th required event.  In
    a saga (parts, each with its own main thread) the skeleton's main thread is
    the last part's.
    """

    thread_ids = [thread["id"] for thread in threads]
    mains = [thread for thread in threads if thread.get("kind") == "main"]
    parts = [thread for thread in mains if thread.get("part") is not None]
    if parts:
        main = max(parts, key=lambda thread: thread["part"])
    else:
        main = mains[0] if mains else threads[0]
    main_id = main["id"]
    slot_ids = [f"e{index:03d}" for index in range(1, len(chronological) + 1)]
    main_positions = [
        index for index, slot in enumerate(chronological) if slot["thread"] == main_id
    ]
    if not main_positions:
        raise ValueError("主筋のスロットがありません")
    climax_position = next(
        (index for index in main_positions if chronological[index]["stage"].get("climax") is True),
        main_positions[-1],
    )
    main_plot = main["plot_type"]

    def element(task: str, kind: str, name: str, position: int | None, **extra: Any) -> dict[str, Any]:
        return {
            "task": task, "kind": kind, "name": name, "thread": main_id, "n": None,
            "slot": None if position is None else slot_ids[position],
            "plot_type": main_plot, **extra,
        }

    elements = [
        element(SKELETON_LACK, "lack", "欠落", None),
        element(SKELETON_CLIMAX, "climax", "決定的な瞬間", climax_position),
        element(SKELETON_RESOLUTION, "resolution", "結末の後", main_positions[-1]),
    ]
    for thread in threads:
        # Ledger order within a thread is ascending n (P3-12 shares this order).
        turns = sorted(
            ((index, number)
             for index, slot in enumerate(chronological)
             if slot["thread"] == thread["id"]
             for number in slot["_turns"]),
            key=lambda item: item[1],
        )
        expected = len(plot_by_id[thread["plot_type"]]["required_events"])
        if sorted(number for _index, number in turns) != list(range(1, expected + 1)):
            raise ValueError(f"必須の出来事を割り当てたスロットが不足しています: {thread['id']}")
        for index, number in turns:
            elements.append(
                {
                    "task": f"{SKELETON_TURN}-{thread['id']}-{number}", "kind": "turn",
                    "name": "転換", "thread": thread["id"],
                    "n": number, "slot": slot_ids[index], "plot_type": thread["plot_type"],
                }
            )
    return {
        "main_thread": main_id, "thread_order": thread_ids, "saga": bool(parts),
        "elements": elements,
    }


def _assign_protagonist(
    chronological: Sequence[Mapping[str, Any]],
    plan: Mapping[str, Any],
    cast: Sequence[Any],
    character_outputs: Mapping[str, Mapping[str, Any]],
) -> None:
    """Put the protagonist in the slots of elements 2 and 3 (the journey's resolution)."""

    protagonist = next(
        (person for person in cast if isinstance(person, Mapping) and person.get("role") == "protagonist"),
        None,
    )
    if protagonist is None:
        raise ValueError("主人公がいません")
    for element in plan["elements"]:
        if element["kind"] not in {"climax", "resolution"}:
            continue
        slot = chronological[int(element["slot"][1:]) - 1]
        if any(person["id"] == protagonist["id"] for person in slot["characters"]):
            continue
        slot["characters"].insert(0, _character_record(protagonist, character_outputs))


def _build_downstream_tasks(
    context: CodeTaskContext,
    slots: Sequence[Mapping[str, Any]],
    plan: Mapping[str, Any] | None = None,
) -> list[TaskSpec]:
    staged = plan is not None
    parent_task_id = context.task_id
    scale = getattr(context, "scale", None)
    run_dir = getattr(context, "run_dir", None)
    if not isinstance(scale, Mapping) and isinstance(run_dir, Path):
        scale = load_manifest(run_dir / "manifest.json").get("scale")
    candidate_count = (load_table("scales", repository_root=_REPOSITORY_ROOT)["dev"].get("event_candidates")
                       if isinstance(scale, Mapping) and scale.get("preset") == "dev" else None)
    additions: list[TaskSpec] = []
    s7_ids_in_time_order: list[str] = []
    previous_by_thread: dict[str, str] = {}
    from .plot_quality import CONDITION_KINDS, condition_ids
    # story-pipeline §9.5: only S6.expand's own definition version decides
    # whether the condition-pair checks are added (v5 and later).
    definition = getattr(context, "definition", None)
    own_version = definition.get("version", 0) if isinstance(definition, Mapping) else 0
    check_conditions = isinstance(own_version, int) and own_version >= _CONDITION_CHECK_VERSION
    conditions_by_thread: dict[str, tuple[str, ...]] = {}
    final_judge_by_thread: dict[str, tuple[str, ...]] = {}
    skeleton_ids: tuple[str, ...] = ()
    if plan is not None:
        # ADR-0011: the skeleton replaces the plot conditions; no S7.condition,
        # S8.condition_check or S8.condition_judge is created.
        skeleton_specs = _skeleton_tasks(parent_task_id, plan)
        additions.extend(skeleton_specs)
        skeleton_ids = tuple(spec.task_id for spec in skeleton_specs)
    for slot in slots:
        if slot["thread"] in conditions_by_thread:
            continue
        if staged:
            conditions_by_thread[slot["thread"]] = ()
            final_judge_by_thread[slot["thread"]] = ()
            continue
        ids = condition_ids(slot["id"])
        conditions_by_thread[slot["thread"]] = ids
        final_judge_by_thread[slot["thread"]] = ()
        for number, task_id in enumerate(ids):
            upstream = getattr(context, "task", {}).get("deps", ())
            assignment_deps = tuple(x for x in upstream if x == "S3.assign")
            kind = CONDITION_KINDS[number]
            previous_judge = (
                (f"S8.condition_judge-{slot['id']}-{CONDITION_KINDS[number - 1]}",)
                if check_conditions and number >= 2 else ()
            )
            additions.append(TaskSpec(task_id, "S7.condition",
                deps=(parent_task_id, *assignment_deps, *ids[:number], *previous_judge),
                index=(slot["id"], kind)))
            if not check_conditions or number == 0:
                continue
            check_id = f"S8.condition_check-{slot['id']}-{kind}"
            judge_id = f"S8.condition_judge-{slot['id']}-{kind}"
            additions.append(TaskSpec(check_id, "S8.condition_check",
                deps=(parent_task_id, task_id, *ids[:number]),
                index=(slot["id"], kind)))
            additions.append(TaskSpec(judge_id, "S8.condition_judge",
                deps=(parent_task_id, check_id),
                index=(slot["id"], kind)))
            final_judge_by_thread[slot["thread"]] = (judge_id,)

    for position, slot in enumerate(slots):
        event_id = slot["id"]
        thread_id = slot["thread"]
        s7_id = f"S7.assemble-{event_id}"
        deps = [parent_task_id]
        previous_judge = previous_by_thread.get(thread_id)
        if previous_judge is not None:
            deps.append(previous_judge)
        previous_event = (
            previous_judge.replace("S8.judge-", "S7.assemble-", 1)
            if previous_judge is not None else None
        )
        if staged:
            # The event ledger needs every earlier event confirmed (in time
            # order, across threads) and the whole skeleton decided.
            deps = [parent_task_id, *skeleton_ids, *(f"S8.judge-{earlier['id']}" for earlier in slots[:position])]
            previous_event = None
        additions.extend(event_tasks(
            parent_task_id, event_id, deps, previous_event, conditions_by_thread[thread_id],
            candidate_count=candidate_count, checks=final_judge_by_thread[thread_id],
        ))
        s7_ids_in_time_order.append(s7_id)
        previous_by_thread[thread_id] = f"S8.judge-{event_id}"

        additions.append(
            TaskSpec(
                f"S8.plan-{event_id}",
                "S8.plan",
                deps=_unique((parent_task_id, s7_id, *s7_ids_in_time_order[:-1], *conditions_by_thread[thread_id],
                              *final_judge_by_thread[thread_id])),
                index=(event_id,),
            )
        )

    detail_ids: list[str] = []
    detail_judges: list[str] = []
    for position, slot in enumerate(slots):
        event_id = slot["id"]
        beat_definitions = slot.get("beats")
        if not isinstance(beat_definitions, list) or not beat_definitions:
            raise ValueError(f"slot の場面定義がありません: {event_id}")
        previous_detail: str | None = None
        for number, _beat in enumerate(beat_definitions, start=1):
            detail_id = f"S7.detail-{event_id}-b{number}"
            deps = [parent_task_id, f"S7.assemble-{event_id}", f"S8.judge-{event_id}", *conditions_by_thread[slot["thread"]]]
            if staged:
                # Cumulative context: every earlier event is confirmed first.
                deps.extend(f"S8.judge-{earlier['id']}" for earlier in slots[:position])
            if previous_detail is not None:
                deps.extend((previous_detail, previous_detail.replace("S7.detail-", "S8.detail_judge-", 1)))
            additions.append(
                TaskSpec(
                    detail_id,
                    "S7.detail",
                    deps=_unique(deps),
                    index=(event_id, f"b{number}"),
                )
            )
            comparison_id = f"S8.detail-{event_id}-b{number}"
            judge_id = f"S8.detail_judge-{event_id}-b{number}"
            compare_deps = [parent_task_id, detail_id, f"S7.assemble-{event_id}"]
            if previous_detail:
                compare_deps.append(previous_detail)
            additions.append(TaskSpec(comparison_id, "S8.detail", deps=tuple(compare_deps), index=(event_id, f"b{number}")))
            additions.append(TaskSpec(judge_id, "S8.detail_judge", deps=(parent_task_id, comparison_id, detail_id), index=(event_id, f"b{number}")))
            detail_judges.append(judge_id)
            detail_ids.append(detail_id)
            previous_detail = detail_id

    judge_ids = [f"S8.judge-{slot['id']}" for slot in slots]
    # S9 consumes S3--S8 outputs.  Keep the upstream task IDs as direct
    # dependencies so the generic selector layer exposes those outputs to
    # the assembler instead of only exposing S6/S8 transitively.
    task = getattr(context, "task", {})
    upstream_ids = task.get("deps", ()) if isinstance(task, Mapping) else ()
    if not isinstance(upstream_ids, (list, tuple)):
        raise ValueError("S6.expand の依存タスクが不正です")
    additions.append(
        TaskSpec(
            "S9.assemble",
            "S9.assemble",
            # S8.plan creates these judge nodes after the actual S7 `who`
            # values are available.  The orchestrator retains these declared
            # S8.judge dependencies until those nodes are added.
            deps=_unique(
                (
                    parent_task_id,
                    *upstream_ids,
                    *skeleton_ids,
                    *s7_ids_in_time_order,
                    *judge_ids,
                    *detail_ids,
                    *detail_judges,
                )
            ),
        )
    )
    return additions


def _skeleton_tasks(parent: str, plan: Mapping[str, Any]) -> list[TaskSpec]:
    """The skeleton's tasks and their reverse-order dependencies (story-pipeline S6.plot §3).

    Turn n depends on the later turns of its thread; in a saga it also depends on
    every turn of the later parts, so that the back-calculation from the ending
    continues across the parts.  Every turn depends on elements 1 to 3.
    """

    elements = plan["elements"]
    turns = [element for element in elements if element["kind"] == "turn"]
    order = {thread_id: position for position, thread_id in enumerate(plan["thread_order"])}
    specs = [
        TaskSpec(SKELETON_LACK, SKELETON_LACK, deps=(parent,)),
        TaskSpec(SKELETON_CLIMAX, SKELETON_CLIMAX, deps=(parent, SKELETON_LACK)),
        TaskSpec(
            SKELETON_RESOLUTION, SKELETON_RESOLUTION,
            deps=(parent, SKELETON_LACK, SKELETON_CLIMAX),
        ),
    ]
    for element in turns:
        later = [
            other["task"]
            for other in turns
            if (other["thread"] == element["thread"] and other["n"] > element["n"])
            or (plan.get("saga") and order[other["thread"]] > order[element["thread"]])
        ]
        specs.append(
            TaskSpec(
                element["task"], SKELETON_TURN,
                deps=(parent, SKELETON_LACK, SKELETON_CLIMAX, SKELETON_RESOLUTION, *later),
                index=(element["thread"], str(element["n"])),
            )
        )
    return specs


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return tuple(result)


__all__ = ["allocate_stage_counts", "story_s6_expand"]
