"""Code tasks and run-local bookkeeping for story pipeline S2."""

from __future__ import annotations

import json
import math
import unicodedata
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from .manifest import load_manifest
from .orchestrator import CodeTaskResult, CodeTaskContext, TaskSpec
from .tables import load_table
from .story_materials import materials_from_outputs
from .task_outputs import task_sources


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def story_s2_plan(context: CodeTaskContext) -> CodeTaskResult:
    """Create the per-material/per-axis expansion tasks and their merge task."""

    materials = materials_from_outputs(context.dependency_outputs)
    if not materials:
        raise ValueError("S1 の素材がありません")

    axes = load_table("element_axes", repository_root=_REPOSITORY_ROOT)["axes"]
    cliches = load_table("cliches", repository_root=_REPOSITORY_ROOT)
    manifest = load_manifest(context.run_dir / "manifest.json")
    pool_need = (
        manifest.get("scale", {}).get("derived", {}).get("pool_need", {})
    )
    if not isinstance(pool_need, Mapping):
        raise ValueError("manifest の pool_need が不正です")

    plans: dict[str, dict[str, Any]] = {}
    expand_tasks: list[TaskSpec] = []
    for axis in axes:
        axis_key = axis["key"]
        required = pool_need.get(axis_key)
        if isinstance(required, bool) or not isinstance(required, int) or required < 0:
            raise ValueError(f"pool_need の値が不正です: {axis_key}")
        task_count = math.ceil(required / 5) + 1
        for ordinal in range(task_count):
            material = materials[ordinal % len(materials)]
            plan_key = f"{material['id']}-{axis_key}"
            if plan_key in plans:
                plan_key = f"{plan_key}-r{ordinal + 1}"
            plans[plan_key] = {
                "material": deepcopy(material),
                "axis": deepcopy(axis),
            }
            previous_ids: list[str] = []
            for number in range(1, 6):
                task_id = f"S2.expand-{plan_key}-{number}"
                expand_tasks.append(TaskSpec(
                    task_id=task_id, type="S2.expand",
                    deps=(context.task_id, *previous_ids), index=(plan_key, str(number)),
                ))
                previous_ids.append(task_id)
            expand_tasks.append(TaskSpec(
                task_id=f"S2.counter-{plan_key}", type="S2.counter",
                deps=(context.task_id, *previous_ids), index=(plan_key,),
            ))

    expand_ids = tuple(task.task_id for task in expand_tasks)
    merge_task = TaskSpec(
        task_id="S2.merge",
        type="S2.merge",
        deps=(context.task_id, *expand_ids),
    )
    return CodeTaskResult(
        output={"plans": plans, "cliches": cliches},
        add_tasks=[*expand_tasks, merge_task],
    )


def story_s2_merge(context: CodeTaskContext) -> CodeTaskResult:
    """Build the run-local, input-derived element pools deterministically."""

    axes = load_table("element_axes", repository_root=_REPOSITORY_ROOT)["axes"]
    pools: dict[str, list[dict[str, str]]] = {
        axis["key"]: [] for axis in axes
    }
    seen: dict[str, set[str]] = {axis: set() for axis in pools}

    manifest = load_manifest(context.run_dir / "manifest.json")
    for dependency_id in context.task["deps"]:
        dependency = manifest["tasks"].get(dependency_id)
        if not isinstance(dependency, Mapping) or dependency.get("type") != "S2.expand":
            continue
        index = dependency.get("index")
        if not isinstance(index, list) or len(index) != 2:
            raise ValueError(f"S2.expand の添字が不正です: {dependency_id}")
        axis_key = _axis_from_plan(context.run_dir, index[0])
        output = context.dependency_outputs.get(dependency_id)
        if not isinstance(output, str):
            continue
        sources = task_sources(context.run_dir / "tasks" / dependency_id)
        if not sources:
            raise ValueError(f"S2.expand の source が不正です: {dependency_id}")
        normalized = normalize_element_text(output)
        if not normalized or normalized in seen[axis_key]:
            continue
        seen[axis_key].add(normalized)
        pools[axis_key].append({
            "id": f"{axis_key}:i{len(pools[axis_key]) + 1:02d}",
            "text": normalized, "source": sources[0],
        })
    return CodeTaskResult(
        output={"pools": pools},
        add_tasks=[
            TaskSpec(
                task_id="S3.assign",
                type="S3.assign",
                deps=(context.task_id,),
            )
        ],
    )


def normalize_element_text(value: str) -> str:
    """Return the S2 canonical form used for duplicate detection."""

    return "".join(unicodedata.normalize("NFKC", value).split())


def _axis_from_plan(run_dir: Path, plan_key: Any) -> str:
    if not isinstance(plan_key, str):
        raise ValueError("S2.plan の添字が不正です")
    path = run_dir / "tasks" / "S2.plan" / "output.json"
    try:
        with path.open("r", encoding="utf-8") as stream:
            output = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("S2.plan の出力を読み込めません") from error
    try:
        axis = output["plans"][plan_key]["axis"]["key"]
    except (KeyError, TypeError) as error:
        raise ValueError(f"S2.plan に存在しない計画です: {plan_key}") from error
    if not isinstance(axis, str) or not axis:
        raise ValueError(f"S2.plan の軸が不正です: {plan_key}")
    return axis


__all__ = [
    "normalize_element_text",
    "story_s2_merge",
    "story_s2_plan",
]
