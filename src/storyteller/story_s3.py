"""Deterministic assignment and DAG expansion for story-pipeline S3."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from .manifest import load_manifest
from .orchestrator import CodeTaskContext, CodeTaskResult, TaskSpec
from .tables import element_rows, load_table
from .validation import validate_document
from .volume import apply_volume_update, compute_character_volume, compute_initial_volume
from .world_facts import (
    assign_world_fact_tasks, assign_calendar, build_world_fact_specs,
    description_entries, fact_ids_by_section,
)
from .character_facts import assign_character_fact_tasks, initial_character_specs


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_CHARACTER_AXES = ("want", "ability", "duty", "age", "gender", "species")
_WORLD_AXES = ("place", "era", "object")
_REQUIRED_ROLES = ("messenger", "supporter", "adversary")
_RANDOM_ROLES = (*_REQUIRED_ROLES, "bystander")
_MATERIAL_KINDS = frozenset(
    {"desire", "fear", "suppression", "conflict", "image", "value", "theme", "mood"}
)


def story_s3_assign(context: CodeTaskContext) -> CodeTaskResult:
    """Build one seeded assignment and add the S4/S5/S6 task graph."""

    manifest = load_manifest(context.run_dir / "manifest.json")
    repository_root = _REPOSITORY_ROOT
    element_axes = tuple(
        axis["key"]
        for axis in load_table("element_axes", repository_root=repository_root)["axes"]
    )
    snapshot = _snapshot_for_run(manifest, context.data_dir, element_axes)
    table_pools = _table_pools(snapshot, element_axes, context.data_dir, repository_root)
    input_pools = _input_pools(context)
    materials = _load_materials(context, manifest)

    scales = load_table("scales", repository_root=repository_root)
    structures = load_table("structures", repository_root=repository_root)["templates"]
    plots = load_table("plot_types", repository_root=repository_root)["types"]
    plot_by_id = {plot["id"]: plot for plot in plots}
    eligible_plots = [
        plot
        for plot in plots
        if plot.get("structure") == "standard" or plot.get("structure") in structures
    ]
    if not eligible_plots:
        raise ValueError("利用できるプロット型がありません")

    r = _input_ratio_for_run(manifest, scales, context.random)
    threads = _build_threads(context, manifest, scales, plot_by_id, eligible_plots)
    cast_roles, absent_roles = _assign_roles(
        context,
        threads,
        structures,
        int(_derived_value(manifest, "cast")),
    )
    cast, world = _assign_elements_and_sounds(
        context,
        input_pools,
        table_pools,
        r,
        cast_roles,
        materials,
    )
    _attach_s5_context(cast, plot_by_id, threads)
    initial_volume = compute_initial_volume(
        manifest["scale"], repository_root=repository_root
    )
    assignment = {
        "r": r,
        "threads": threads,
        "cast": cast,
        "absent_roles": absent_roles,
        "world": world,
        "world_sections": _selected_world_sections(manifest, repository_root),
    }
    assignment["world_tasks"] = _assign_world_tasks(
        context,
        assignment["world_sections"],
        initial_volume["world"],
        input_pools,
        table_pools,
        world,
        r,
        repository_root,
    )
    name_sets = load_table("name_sounds", repository_root=repository_root)["sets"]
    assignment["calendar"] = assign_calendar(context.random, name_sets, repository_root)
    assignment["world_fact_tasks"] = assign_world_fact_tasks(
        assignment["world_sections"], assignment["world_tasks"],
        name_sets, context.random,
        current_year=assignment["calendar"]["current_year"], repository_root=repository_root,
    )
    assignment["character_fact_tasks"] = assign_character_fact_tasks(assignment["cast"], name_sets, context.random, repository_root)
    validate_document(assignment, repository_root / "schemas" / "story" / "assignment.schema.json")

    # story-pipeline.md §8 / ADR-0007: decide each character's item counts
    # (including how many backstory periods and relationship entries to
    # write) now that S3 knows the cast, and record the allocation in the
    # manifest alongside any volume already computed at S0.
    volume_result = compute_character_volume(
        manifest["scale"],
        [{"id": person["id"], "role": person["role"]} for person in cast],
        repository_root=repository_root,
    )
    character_counts = {
        entry["id"]: entry["item_counts"] for entry in volume_result["characters"]
    }
    updated_scale = apply_volume_update(
        manifest,
        {**initial_volume, **volume_result},
    )["scale"]
    manifest_for_tasks = {**manifest, "scale": updated_scale}

    additions = _build_downstream_tasks(
        context.task_id,
        manifest_for_tasks,
        assignment,
        scales,
        repository_root,
        character_counts,
    )
    return CodeTaskResult(
        output=assignment,
        add_tasks=additions,
        manifest_updates={
            "input_ratio": r,
            "table_snapshot": snapshot,
            "scale": updated_scale,
        },
    )


def _derived_value(manifest: Mapping[str, Any], key: str) -> Any:
    try:
        return manifest["scale"]["derived"][key]
    except (KeyError, TypeError) as error:
        raise ValueError(f"manifest の derived.{key} がありません") from error


def _draw_input_ratio(scales: Mapping[str, Any], random_source: Any) -> float:
    lower, upper = _input_ratio_bounds(scales)
    return random_source.uniform(lower, upper)


def _input_ratio_bounds(scales: Mapping[str, Any]) -> tuple[float, float]:
    value = scales.get("input_ratio")
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != 2
        or not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)
    ):
        raise ValueError("scales.input_ratio が不正です")
    lower, upper = float(value[0]), float(value[1])
    if lower < 0 or upper > 1 or lower > upper:
        raise ValueError("scales.input_ratio の範囲が不正です")
    return lower, upper


def _input_ratio_for_run(
    manifest: Mapping[str, Any], scales: Mapping[str, Any], random_source: Any
) -> float:
    recorded = manifest.get("input_ratio")
    if recorded is None:
        return _draw_input_ratio(scales, random_source)
    if isinstance(recorded, bool) or not isinstance(recorded, (int, float)):
        raise ValueError("manifest の input_ratio が不正です")
    lower, upper = _input_ratio_bounds(scales)
    ratio = float(recorded)
    if not lower <= ratio <= upper:
        raise ValueError("manifest の input_ratio が scales の範囲外です")
    return ratio


def _table_snapshot(data_dir: Path, axes: Sequence[str]) -> dict[str, int]:
    snapshot: dict[str, int] = {}
    for axis in axes:
        path = data_dir / "tables" / f"{axis}.json"
        if not path.is_file():
            snapshot[axis] = 0
            continue
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
        snapshot[axis] = len(_supplemental_items(value, axis))
    return snapshot


def _snapshot_for_run(
    manifest: Mapping[str, Any], data_dir: Path, axes: Sequence[str]
) -> dict[str, int]:
    if manifest.get("input_ratio") is None:
        return _table_snapshot(data_dir, axes)
    recorded = manifest.get("table_snapshot")
    if not isinstance(recorded, Mapping):
        raise ValueError("manifest の table_snapshot が不正です")
    snapshot: dict[str, int] = {}
    for axis in axes:
        count = recorded.get(axis)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"manifest の table_snapshot の値が不正です: {axis}")
        snapshot[axis] = count
    return snapshot


def _supplemental_items(value: Any, axis: str) -> list[dict[str, str]]:
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, Mapping) and isinstance(value.get("items"), list):
        raw_items = value["items"]
    else:
        raise ValueError(f"増補テーブル {axis} の形式が不正です")
    result: list[dict[str, str]] = []
    for item in raw_items:
        if isinstance(item, str) and item:
            result.append({"text": item})
        elif isinstance(item, Mapping) and isinstance(item.get("text"), str) and item["text"]:
            row = {"text": item["text"]}
            if isinstance(item.get("source"), str) and item["source"]:
                row["source"] = item["source"]
            result.append(row)
        else:
            raise ValueError(f"増補テーブル {axis} に不正な行があります")
    return result


def _table_pools(
    snapshot: Mapping[str, int],
    axes: Sequence[str],
    data_dir: Path,
    repository_root: Path,
) -> dict[str, list[dict[str, str]]]:
    pools: dict[str, list[dict[str, str]]] = {}
    for axis in axes:
        rows: list[dict[str, str]] = []
        for row in element_rows(axis, repository_root=repository_root):
            rows.append(dict(row))
        count = snapshot.get(axis, 0)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"table_snapshot の値が不正です: {axis}")
        if count:
            path = data_dir / "tables" / f"{axis}.json"
            with path.open("r", encoding="utf-8") as stream:
                supplemental = _supplemental_items(json.load(stream), axis)
            for index, row in enumerate(supplemental[:count], start=1):
                rows.append({"id": f"{axis}:x{index}", **row})
        pools[axis] = rows
    return pools


def _input_pools(context: CodeTaskContext) -> dict[str, list[dict[str, str]]]:
    value = context.inputs.get("pools")
    if value is None:
        output = context.dependency_outputs.get("S2.merge")
        if isinstance(output, Mapping):
            value = output.get("pools")
    if not isinstance(value, Mapping):
        raise ValueError("S2.merge の pools がありません")
    pools: dict[str, list[dict[str, str]]] = {}
    for axis, items in value.items():
        if not isinstance(axis, str) or not isinstance(items, list):
            continue
        normalized: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, Mapping):
                continue
            item_id, text = item.get("id"), item.get("text")
            if not isinstance(item_id, str) or not isinstance(text, str) or item_id in seen:
                continue
            row = {"id": item_id, "text": text}
            if isinstance(item.get("source"), str):
                row["source"] = item["source"]
            normalized.append(row)
            seen.add(item_id)
        pools[axis] = normalized
    return pools


def _load_materials(
    context: CodeTaskContext,
    manifest: Mapping[str, Any],
) -> list[dict[str, str]]:
    materials: list[dict[str, str]] = []
    for task_id, task in manifest.get("tasks", {}).items():
        if not isinstance(task_id, str) or not isinstance(task, Mapping):
            continue
        if task.get("type") != "S1.extract" or task.get("state") != "done":
            continue
        output_path = context.run_dir / "tasks" / task_id / "output.json"
        try:
            with output_path.open("r", encoding="utf-8") as stream:
                output = json.load(stream)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"S1 の出力を読み込めません: {task_id}") from error
        from .story_materials import materials_from_outputs
        materials.extend(material for material in materials_from_outputs({task_id: output})
                         if material["kind"] in _MATERIAL_KINDS)
    materials.sort(key=lambda item: item["id"])
    return materials


def _build_threads(
    context: CodeTaskContext,
    manifest: Mapping[str, Any],
    scales: Mapping[str, Any],
    plot_by_id: Mapping[str, Mapping[str, Any]],
    eligible_plots: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    derived_threads = _derived_value(manifest, "threads")
    if isinstance(derived_threads, bool) or not isinstance(derived_threads, int) or derived_threads < 1:
        raise ValueError("manifest の derived.threads が不正です")
    total_events = _derived_value(manifest, "events")
    if isinstance(total_events, bool) or not isinstance(total_events, int) or total_events < 1:
        raise ValueError("manifest の derived.events が不正です")

    input_plot_type = manifest.get("input", {}).get("plot_type") if isinstance(manifest.get("input"), Mapping) else None
    eligible_ids = [str(plot["id"]) for plot in eligible_plots]
    if isinstance(input_plot_type, str) and input_plot_type:
        if input_plot_type not in plot_by_id or plot_by_id[input_plot_type] not in eligible_plots:
            raise ValueError(f"指定されたプロット型を利用できません: {input_plot_type}")
        plot_ids = [input_plot_type]
    else:
        plot_ids = [context.random.choice(eligible_ids)]
    used_plot_ids = set(plot_ids)
    while len(plot_ids) < derived_threads:
        candidates = [plot_id for plot_id in eligible_ids if plot_id not in used_plot_ids]
        if not candidates:
            candidates = eligible_ids
        selected = context.random.choice(candidates)
        plot_ids.append(selected)
        used_plot_ids.add(selected)

    is_parts = isinstance(manifest.get("scale"), Mapping) and isinstance(
        manifest["scale"].get("axes"), Mapping
    ) and manifest["scale"]["axes"].get("threads") == "parts"
    if is_parts:
        event_counts = _split_part_events(total_events, derived_threads, context.random)
    else:
        subthread_range = scales.get("subthread_events")
        if (
            not isinstance(subthread_range, Sequence)
            or isinstance(subthread_range, (str, bytes))
            or len(subthread_range) != 2
        ):
            raise ValueError("scales.subthread_events が不正です")
        side_counts = [context.random.randint(int(subthread_range[0]), int(subthread_range[1])) for _ in range(max(0, derived_threads - 1))]
        main_events = total_events - sum(side_counts)
        if main_events < 3:
            raise ValueError("主筋の出来事数が3未満です")
        event_counts = [main_events, *side_counts]

    threads: list[dict[str, Any]] = []
    for index, (plot_id, event_count) in enumerate(zip(plot_ids, event_counts), start=1):
        is_main = is_parts or index == 1
        plot = plot_by_id[plot_id]
        structure = _structure_for(plot, event_count, is_main)
        threads.append(
            {
                "id": f"t{index:03d}",
                "kind": "main" if is_main else "subthread",
                "part": index if is_parts else None,
                "plot_type": plot_id,
                "plot_type_name": plot["name"],
                "events": event_count,
                "structure": structure,
            }
        )
    return threads


def _split_part_events(total: int, count: int, random_source: Any) -> list[int]:
    if total < count * 3:
        raise ValueError("部ごとの主筋に必要な出来事数が不足しています")
    base, remainder = divmod(total, count)
    extras = [1] * remainder + [0] * (count - remainder)
    random_source.shuffle(extras)
    return [base + extra for extra in extras]


def _structure_for(plot: Mapping[str, Any], events: int, is_main: bool) -> str:
    structure = plot.get("structure")
    if not isinstance(structure, str) or not structure:
        raise ValueError("プロット型の structure が不正です")
    if not is_main:
        return "kishotenketsu"
    if structure != "standard":
        return structure
    if events <= 5:
        return "three-beat"
    if events <= 11:
        return "kishotenketsu"
    return "heros-journey-12"


def _assign_roles(
    context: CodeTaskContext,
    threads: Sequence[Mapping[str, Any]],
    structures: Mapping[str, Any],
    cast_count: int,
) -> tuple[list[str], list[str]]:
    if cast_count < 1:
        raise ValueError("人物数は1以上でなければなりません")
    required_counts: Counter[str] = Counter()
    for thread in threads:
        structure = structures.get(thread["structure"])
        if not isinstance(structure, Mapping) or not isinstance(structure.get("stages"), list):
            raise ValueError(f"構造テンプレートがありません: {thread['structure']}")
        for stage in structure["stages"]:
            if not isinstance(stage, Mapping) or not isinstance(stage.get("roles"), list):
                continue
            for role in stage["roles"]:
                if role in _REQUIRED_ROLES:
                    required_counts[role] += 1
    ordered_required = sorted(
        (role for role in _REQUIRED_ROLES if required_counts[role] > 0),
        key=lambda role: (-required_counts[role], _REQUIRED_ROLES.index(role)),
    )
    roles = ["protagonist"]
    absent: list[str] = []
    for role in ordered_required:
        if len(roles) < cast_count:
            roles.append(role)
        else:
            absent.append(role)
    while len(roles) < cast_count:
        roles.append(context.random.choice(_RANDOM_ROLES))
    return roles, absent


def _assign_elements_and_sounds(
    context: CodeTaskContext,
    input_pools: Mapping[str, Sequence[Mapping[str, str]]],
    table_pools: Mapping[str, Sequence[Mapping[str, str]]],
    ratio: float,
    roles: Sequence[str],
    materials: Sequence[Mapping[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    name_sets = load_table("name_sounds", repository_root=_REPOSITORY_ROOT)["sets"]
    used: dict[str, set[str]] = {axis: set() for axis in (*_CHARACTER_AXES, "taboo", *_WORLD_AXES)}
    cast: list[dict[str, Any]] = []
    for index, role in enumerate(roles, start=1):
        if index == 1 and role != "protagonist":
            raise ValueError("主人公は人物ID c1 に割り当てられなければなりません")
        elements: dict[str, Any] = {}
        for axis in _CHARACTER_AXES:
            elements[axis] = _choose_element(context, axis, ratio, input_pools, table_pools, used[axis])
        if index == 1:
            elements["taboo"] = _choose_element(context, "taboo", ratio, input_pools, table_pools, used["taboo"])
        sound_set = context.random.choice(name_sets)
        sounds = context.random.sample(sound_set["sounds"], 6)
        person_id = "c1" if role == "protagonist" else f"c{index}"
        person = {
            "id": person_id,
            "role": role,
            "elements": elements,
            "name_sound": {
                "set_id": sound_set["id"],
                "description": sound_set["description"],
                "sounds": sounds,
            },
        }
        if index == 1:
            suppressed = _choose_material(materials, "suppression", context.random)
            if suppressed is not None:
                person["suppressed_self_image"] = suppressed
        cast.append(person)

    world: dict[str, Any] = {}
    for axis in _WORLD_AXES:
        world[axis] = _choose_element(context, axis, ratio, input_pools, table_pools, used[axis])
    theme_materials = [item for item in materials if item.get("kind") in {"theme", "conflict"}]
    world["theme"] = deepcopy(context.random.choice(theme_materials)) if theme_materials else None
    return cast, world


def _attach_s5_context(
    cast: list[dict[str, Any]],
    plot_by_id: Mapping[str, Mapping[str, Any]],
    threads: Sequence[Mapping[str, Any]],
) -> None:
    """Attach fixed table context needed to render S5 cards."""

    roles = {
        role["id"]: role
        for role in load_table("roles", repository_root=_REPOSITORY_ROOT)["roles"]
    }
    if not threads or not isinstance(threads[0].get("plot_type"), str):
        raise ValueError("主筋のプロット型がありません")
    main_plot = plot_by_id.get(threads[0]["plot_type"])
    if not isinstance(main_plot, Mapping):
        raise ValueError("主筋のプロット型が見つかりません")
    plot_context = {
        "id": main_plot["id"],
        "name": main_plot["name"],
        "character_requirements": main_plot["character_requirements"],
    }
    for person in cast:
        role_id = person.get("role")
        role = roles.get(role_id)
        if not isinstance(role, Mapping):
            raise ValueError(f"人物の役定義が見つかりません: {role_id}")
        person["role_definition"] = {
            "id": role["id"],
            "name": role["name"],
            "definition": role["definition"],
        }
        person["plot_context"] = dict(plot_context)


def _choose_material(
    materials: Sequence[Mapping[str, str]],
    kind: str,
    random_source: Any,
) -> dict[str, str] | None:
    candidates = [material for material in materials if material.get("kind") == kind]
    return deepcopy(random_source.choice(candidates)) if candidates else None


def _choose_element(
    context: CodeTaskContext,
    axis: str,
    ratio: float,
    input_pools: Mapping[str, Sequence[Mapping[str, str]]],
    table_pools: Mapping[str, Sequence[Mapping[str, str]]],
    used: set[str],
) -> dict[str, str]:
    input_candidates = [item for item in input_pools.get(axis, ()) if item.get("id") not in used]
    table_candidates = [item for item in table_pools.get(axis, ()) if item.get("id") not in used]
    if not input_candidates and not table_candidates:
        raise ValueError(f"要素プールが空です: {axis}")
    if input_candidates and table_candidates:
        candidates = input_candidates if context.random.random() < ratio else table_candidates
    else:
        candidates = input_candidates or table_candidates
    selected = deepcopy(context.random.choice(candidates))
    used.add(selected["id"])
    return selected


def _assign_world_tasks(
    context: CodeTaskContext,
    sections: Sequence[Mapping[str, Any]],
    world_volume: Mapping[str, Any],
    input_pools: Mapping[str, Sequence[Mapping[str, str]]],
    table_pools: Mapping[str, Sequence[Mapping[str, str]]],
    world: Mapping[str, Any],
    ratio: float,
    repository_root: Path,
) -> list[dict[str, Any]]:
    """Assign the per-task S4 context before any world LLM task runs.

    The assignment is deliberately materialized in S3 rather than inferred by
    an S4 card.  This keeps the choice of facet, cut, and name sounds in code
    and makes the exact S4 task index sufficient to recover its context.
    """

    allocations = world_volume.get("sections")
    if not isinstance(allocations, list):
        raise ValueError("世界の分量配分がありません")
    allocation_by_id = {
        entry.get("id"): entry for entry in allocations if isinstance(entry, Mapping)
    }
    name_sets = load_table("name_sounds", repository_root=repository_root)["sets"]
    used: dict[str, set[str]] = {axis: set() for axis in _WORLD_AXES}
    for axis in _WORLD_AXES:
        selected = world.get(axis)
        if isinstance(selected, Mapping) and isinstance(selected.get("id"), str):
            used[axis].add(selected["id"])

    result: list[dict[str, Any]] = []
    for section in sections:
        section_id = section.get("id")
        kind = section.get("kind")
        if not isinstance(section_id, str) or kind not in {"single", "list"}:
            raise ValueError("世界セクションの定義が不正です")
        allocation = allocation_by_id.get(section_id)
        if not isinstance(allocation, Mapping):
            raise ValueError(f"世界の分量配分がありません: {section_id}")
        section_context = {
            "id": section_id,
            "name": section["name"],
            "definition": section["definition"],
        }
        if kind == "single":
            viewpoints = section.get("viewpoints")
            viewpoint_allocations = allocation.get("viewpoints")
            if not isinstance(viewpoints, list) or not isinstance(viewpoint_allocations, list):
                raise ValueError(f"世界セクションの観点配分が不正です: {section_id}")
            if len(viewpoints) != len(viewpoint_allocations):
                raise ValueError(f"世界セクションの観点数が一致しません: {section_id}")
            for viewpoint_number, (viewpoint, viewpoint_allocation) in enumerate(
                zip(viewpoints, viewpoint_allocations), start=1
            ):
                if not isinstance(viewpoint, str) or not isinstance(viewpoint_allocation, Mapping):
                    raise ValueError(f"世界セクションの観点が不正です: {section_id}")
                facet_count = viewpoint_allocation.get("facet_count")
                if isinstance(facet_count, bool) or not isinstance(facet_count, int) or facet_count < 1:
                    raise ValueError(f"世界セクションの面数が不正です: {section_id}")
                for facet_number in range(1, facet_count + 1):
                    task_key = f"{section_id}-{viewpoint_number}-f{facet_number}"
                    element_axis, element = _choose_world_cut(
                        context, input_pools, table_pools, used, ratio
                    )
                    result.append(
                        {
                            "id": task_key,
                            "section_id": section_id,
                            "kind": "single",
                            "section": deepcopy(section_context),
                            "viewpoint": viewpoint,
                            "facet_number": facet_number,
                            "element_axis": element_axis,
                            "element": element,
                        }
                    )
        else:
            item_count = allocation.get("item_count")
            if isinstance(item_count, bool) or not isinstance(item_count, int) or item_count < 1:
                raise ValueError(f"世界セクションの項目数が不正です: {section_id}")
            for ordinal in range(1, item_count + 1):
                task_key = f"{section_id}-{ordinal:03d}"
                element_axis, element = _choose_world_cut(
                    context, input_pools, table_pools, used, ratio
                )
                sound_set = context.random.choice(name_sets)
                result.append(
                    {
                        "id": task_key,
                        "section_id": section_id,
                        "kind": "list",
                        "section": deepcopy(section_context),
                        "element_axis": element_axis,
                        "element": element,
                        "name_sound": {
                            "set_id": sound_set["id"],
                            "description": sound_set["description"],
                            "sounds": context.random.sample(sound_set["sounds"], 6),
                        },
                    }
                )
    return result


def _choose_world_cut(
    context: CodeTaskContext,
    input_pools: Mapping[str, Sequence[Mapping[str, str]]],
    table_pools: Mapping[str, Sequence[Mapping[str, str]]],
    used: Mapping[str, set[str]],
    ratio: float,
) -> tuple[str, dict[str, str]]:
    axes = list(_WORLD_AXES)
    context.random.shuffle(axes)
    for axis in axes:
        input_candidates = [
            item for item in input_pools.get(axis, ()) if item.get("id") not in used[axis]
        ]
        table_candidates = [
            item for item in table_pools.get(axis, ()) if item.get("id") not in used[axis]
        ]
        if input_candidates or table_candidates:
            return axis, _choose_element(
                context, axis, ratio, input_pools, table_pools, used[axis]
            )
    raise ValueError("S4 の切り口に使える要素がありません")


def _build_downstream_tasks(
    parent_task_id: str,
    manifest: Mapping[str, Any],
    assignment: Mapping[str, Any],
    scales: Mapping[str, Any],
    repository_root: Path,
    character_counts: Mapping[str, Mapping[str, int]],
) -> list[TaskSpec]:
    world_sections = load_table("world_sections", repository_root=repository_root)["sections"]
    section_by_id = {section["id"]: section for section in world_sections}
    derived = manifest.get("scale", {}).get("derived", {})
    selected_ids = derived.get("world_sections", []) if isinstance(derived, Mapping) else []
    if not isinstance(selected_ids, list):
        raise ValueError("manifest の world_sections が不正です")
    volume = derived.get("volume") if isinstance(derived, Mapping) else None
    world_volume = volume.get("world") if isinstance(volume, Mapping) else None
    if not isinstance(world_volume, Mapping):
        raise ValueError("manifest の scale.derived.volume.world がありません")
    world_tasks = assignment.get("world_tasks")
    if not isinstance(world_tasks, list):
        raise ValueError("assignment の world_tasks がありません")
    tasks_by_section: dict[str, list[Mapping[str, Any]]] = {}
    for world_task in world_tasks:
        if not isinstance(world_task, Mapping):
            raise ValueError("assignment の world_tasks が不正です")
        task_key = world_task.get("id")
        section_id = world_task.get("section_id")
        kind = world_task.get("kind")
        if (
            not isinstance(task_key, str)
            or not isinstance(section_id, str)
            or kind not in {"single", "list"}
        ):
            raise ValueError("assignment の world task の ID または種別が不正です")
        tasks_by_section.setdefault(section_id, []).append(world_task)

    world_task_ids: dict[str, list[str]] = {}
    additions: list[TaskSpec] = []
    section_specs: list[tuple[str, Mapping[str, Any], list[Mapping[str, Any]]]] = []
    for section_id in selected_ids:
        if section_id not in section_by_id:
            raise ValueError(f"未知の世界セクションです: {section_id}")
        section = section_by_id[section_id]
        section_tasks = tasks_by_section.get(section_id, [])
        expected_kind = section["kind"]
        if any(task.get("kind") != expected_kind for task in section_tasks):
            raise ValueError(f"世界タスクの種別が不正です: {section_id}")
        if not section_tasks:
            raise ValueError(f"世界タスクがありません: {section_id}")
        task_type = "S4.section" if expected_kind == "single" else "S4.item"
        task_ids = [f"{task_type}-{task['id']}" for task in section_tasks]
        world_task_ids[section_id] = task_ids
        section_specs.append((section_id, section, section_tasks))

    # Build dependencies only after every selected section has an ID.  The
    # catalog order is not guaranteed to be a topological order.
    facts_by_section = fact_ids_by_section(assignment)
    additions.extend(build_world_fact_specs(parent_task_id, assignment))
    for section_id, section, section_tasks in section_specs:
        prerequisite_ids = [
            task_id
            for prerequisite in section.get("prerequisites", [])
            for task_id in world_task_ids.get(prerequisite, [])
        ]
        deps = _unique_dependencies((parent_task_id, *prerequisite_ids))
        task_type = "S4.section" if section["kind"] == "single" else "S4.item"
        for world_task in section_tasks:
            item_deps = (*deps, "S4.calendar_name", *(
                f"S4.fact-{entry['id']}" for entry in description_entries(
                    world_task, assignment["world_fact_tasks"]
                )
            ))
            if task_type == "S4.item":
                name_id = f"S4.item_name-{world_task['id']}"
                additions.append(
                    TaskSpec(
                        name_id, "S4.item_name", deps=(parent_task_id,),
                        index=(world_task["id"],),
                    )
                )
                item_deps = (*item_deps, name_id)
            additions.append(
                TaskSpec(
                    f"{task_type}-{world_task['id']}",
                    task_type,
                    deps=item_deps,
                    index=(world_task["id"],),
                )
            )
        if section["kind"] == "single":
            additions.append(
                TaskSpec(
                    f"S4.diversity-{section_id}",
                    "S4.diversity",
                    deps=(parent_task_id, *world_task_ids[section_id]),
                    index=(section_id,),
                )
            )

    cast = assignment.get("cast")
    if not isinstance(cast, list):
        raise ValueError("assignment の cast が不正です")
    person_ids = [
        person.get("id")
        for person in cast
        if isinstance(person, Mapping) and isinstance(person.get("id"), str)
    ]
    if len(person_ids) != len(cast):
        raise ValueError("assignment の人物IDが不正です")
    protagonist_ids = [
        person.get("id")
        for person in cast
        if isinstance(person, Mapping) and person.get("role") == "protagonist"
    ]
    if protagonist_ids != ["c1"]:
        raise ValueError("主人公の人物IDは c1 でなければなりません")
    name_task_ids = [f"S5.name-{person_id}" for person_id in person_ids]
    protagonist_name_id = "S5.name-c1"
    protagonist_intro_id = "S5.intro-c1"

    # story-pipeline.md S5: every character's relationship item is given only
    # the counterpart's name, role, and intro.  A single relationship task
    # definition cannot address two different characters by index at once
    # (the selector language's ``{slot}`` is always this task's own first
    # index; task-model.md §2.4), so one small code task per potential
    # counterpart resolves that counterpart's context once, shared by every
    # other character's relationship task toward them.
    relationship_context_ids: dict[str, str] = {}
    if len(person_ids) > 1:
        for target_id in person_ids:
            context_id = f"S5.relationship_context-{target_id}"
            relationship_context_ids[target_id] = context_id
            additions.append(
                TaskSpec(
                    context_id,
                    "S5.relationship_context",
                    deps=_unique_dependencies(
                        (parent_task_id, f"S5.name-{target_id}", f"S5.intro-{target_id}")
                    ),
                    index=(target_id,),
                )
            )

    for person in cast:
        person_id = person.get("id") if isinstance(person, Mapping) else None
        if not isinstance(person_id, str):
            raise ValueError("assignment の人物IDが不正です")
        counts = character_counts.get(person_id)
        if not isinstance(counts, Mapping):
            raise ValueError(f"人物の分量配分がありません: {person_id}")
        name_id = f"S5.name-{person_id}"
        fact_ids = tuple(f"S5.fact-{entry['id']}" for entry in assignment["character_fact_tasks"] if entry["person_id"] == person_id)
        profile_id = f"S5.profile-{person_id}"
        intro_id = f"S5.intro-{person_id}"
        appearance_id = f"S5.appearance-{person_id}"
        motive_id = f"S5.motive-{person_id}"
        catchphrase_id = f"S5.catchphrase-{person_id}"
        is_protagonist = person.get("role") == "protagonist"
        protagonist_name_deps = () if is_protagonist else (protagonist_name_id,)
        protagonist_intro_deps = () if is_protagonist else (protagonist_intro_id,)
        # profile の後に並行して書く項目（story-pipeline.md S5）。
        parallel_deps = _unique_dependencies((parent_task_id, profile_id, *fact_ids, *protagonist_name_deps))

        personality_id = f"S5.personality-{person_id}"
        values_id = f"S5.values-{person_id}"
        voice_id = f"S5.voice-{person_id}"
        inner_conflict_id = f"S5.inner_conflict-{person_id}"
        backstory_count = _item_count(counts, "backstory", minimum=1)
        backstory_ids = [
            f"S5.backstory-{person_id}-p{ordinal}" for ordinal in range(1, backstory_count + 1)
        ]
        relationship_targets = [other_id for other_id in person_ids if other_id != person_id]
        relationship_ids = [
            f"S5.relationship-{person_id}-{other_id}" for other_id in relationship_targets
        ]
        expected_relationships = _item_count(counts, "relationship", minimum=0)
        if expected_relationships != len(relationship_targets):
            raise ValueError(f"人物の relationship の配分件数が不正です: {person_id}")

        additions.extend(initial_character_specs(parent_task_id, person_id, assignment))
        additions.extend(
            [
                TaskSpec(name_id, "S5.name", deps=(parent_task_id,), index=(person_id,)),
                TaskSpec(
                    profile_id,
                    "S5.profile",
                    deps=_unique_dependencies((parent_task_id, *protagonist_name_deps, name_id, *fact_ids)),
                    index=(person_id,),
                ),
                TaskSpec(intro_id, "S5.intro", deps=parallel_deps, index=(person_id,)),
                TaskSpec(appearance_id, "S5.appearance", deps=parallel_deps, index=(person_id,)),
                TaskSpec(personality_id, "S5.personality", deps=parallel_deps, index=(person_id,)),
                TaskSpec(values_id, "S5.values", deps=parallel_deps, index=(person_id,)),
                TaskSpec(voice_id, "S5.voice", deps=parallel_deps, index=(person_id,)),
                TaskSpec(
                    inner_conflict_id, "S5.inner_conflict", deps=parallel_deps, index=(person_id,)
                ),
                TaskSpec(
                    motive_id,
                    "S5.motive",
                    deps=_unique_dependencies(
                        (parent_task_id, profile_id, *fact_ids, *name_task_ids, *protagonist_intro_deps)
                    ),
                    index=(person_id,),
                ),
            ]
        )
        additions.extend(
            TaskSpec(backstory_id, "S5.backstory", deps=parallel_deps, index=(person_id, f"p{ordinal}"))
            for ordinal, backstory_id in enumerate(backstory_ids, start=1)
        )
        additions.extend(
            TaskSpec(
                relationship_id,
                "S5.relationship",
                deps=_unique_dependencies(
                    (*parallel_deps, relationship_context_ids[other_id])
                ),
                index=(person_id, other_id),
            )
            for relationship_id, other_id in zip(relationship_ids, relationship_targets)
        )
        additions.append(
            TaskSpec(
                catchphrase_id,
                "S5.catchphrase",
                deps=_unique_dependencies(
                    (
                        parent_task_id,
                        motive_id,
                        *fact_ids,
                        personality_id,
                        values_id,
                        voice_id,
                        inner_conflict_id,
                        *backstory_ids,
                        *relationship_ids,
                        *protagonist_name_deps,
                        *protagonist_intro_deps,
                    )
                ),
                index=(person_id,),
            )
        )
    s4_ids = tuple(
        addition.task_id
        for addition in additions
        if addition.type.startswith("S4.")
    )
    s5_ids = tuple(
        addition.task_id for addition in additions if addition.type.startswith("S5.")
    )
    additions.append(
        TaskSpec(
            "S6.expand",
            "S6.expand",
            deps=_unique_dependencies((parent_task_id, *s4_ids, *s5_ids)),
        )
    )
    return additions


def _selected_world_sections(
    manifest: Mapping[str, Any], repository_root: Path
) -> list[dict[str, Any]]:
    selected_ids = manifest.get("scale", {}).get("derived", {}).get("world_sections", [])
    if not isinstance(selected_ids, list):
        raise ValueError("manifest の world_sections が不正です")
    sections = load_table("world_sections", repository_root=repository_root)["sections"]
    section_by_id = {section["id"]: section for section in sections}
    selected: list[dict[str, Any]] = []
    for section_id in selected_ids:
        if not isinstance(section_id, str) or section_id not in section_by_id:
            raise ValueError(f"未知の世界セクションです: {section_id}")
        selected.append(deepcopy(section_by_id[section_id]))
    return selected


def _world_level(manifest: Mapping[str, Any], scales: Mapping[str, Any]) -> int:
    axes = manifest.get("scale", {}).get("axes", {})
    if not isinstance(axes, Mapping):
        raise ValueError("manifest の axes が不正です")
    levels: list[int] = []
    for axis_name in ("space", "change"):
        axis_value = axes.get(axis_name)
        for entry in scales["axes"][axis_name]["values"]:
            if entry["value"] == axis_value:
                levels.append(int(entry["level"]))
                break
        else:
            raise ValueError(f"規模の軸の値が見つかりません: {axis_name}")
    return max(levels)


def _world_count(scales: Mapping[str, Any], section_id: str, level: int) -> int:
    counts = scales.get("world_counts", {}).get(section_id)
    if not isinstance(counts, list) or level >= len(counts):
        raise ValueError(f"世界セクションの件数がありません: {section_id}")
    count = counts[level]
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError(f"世界セクションの件数が不正です: {section_id}")
    return count


def _item_count(counts: Mapping[str, Any], key: str, *, minimum: int) -> int:
    value = counts.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"人物の項目数が不正です: {key}")
    return value


def _unique_dependencies(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return tuple(result)


__all__ = ["story_s3_assign"]
