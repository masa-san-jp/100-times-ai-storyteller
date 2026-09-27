"""Code tasks and run-local bookkeeping for story pipeline S2."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from .manifest import load_manifest
from .orchestrator import CodeTaskResult, CodeTaskContext, TaskSpec
from .tables import load_table


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_MATERIAL_ID = re.compile(r"^m([0-9]{3})$")


def story_s2_plan(context: CodeTaskContext) -> CodeTaskResult:
    """Create the per-material/per-axis expansion tasks and their merge task."""

    materials = _materials_from_s1(context.dependency_outputs)
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
            expand_tasks.append(
                TaskSpec(
                    task_id=f"S2.expand-{plan_key}",
                    type="S2.expand",
                    deps=(context.task_id,),
                    index=(plan_key,),
                )
            )

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


def story_s2_merge(context: CodeTaskContext) -> dict[str, Any]:
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
        if not isinstance(index, list) or len(index) != 1:
            raise ValueError(f"S2.expand の添字が不正です: {dependency_id}")
        axis_key = _axis_from_plan(context.run_dir, index[0])
        output = context.dependency_outputs.get(dependency_id)
        if not isinstance(output, Mapping):
            continue
        items = output.get("items")
        sources = output.get("sources")
        if not isinstance(items, list) or not isinstance(sources, list) or not sources:
            raise ValueError(f"S2.expand の出力が不正です: {dependency_id}")
        source = sources[0]
        if not isinstance(source, str):
            raise ValueError(f"S2.expand の source が不正です: {dependency_id}")
        for item in items:
            if not isinstance(item, str):
                raise ValueError(f"S2.expand の item が不正です: {dependency_id}")
            normalized = normalize_element_text(item)
            if not normalized or normalized in seen[axis_key]:
                continue
            seen[axis_key].add(normalized)
            pools[axis_key].append(
                {
                    "id": f"{axis_key}:i{len(pools[axis_key]) + 1:02d}",
                    "text": normalized,
                    "source": source,
                }
            )
    return {"pools": pools}


def normalize_element_text(value: str) -> str:
    """Return the S2 canonical form used for duplicate detection."""

    return "".join(unicodedata.normalize("NFKC", value).split())


def _materials_from_s1(outputs: Mapping[str, Any]) -> list[dict[str, Any]]:
    materials: list[dict[str, Any]] = []
    for output in outputs.values():
        if not isinstance(output, Mapping) or not isinstance(output.get("materials"), list):
            continue
        for material in output["materials"]:
            if not isinstance(material, Mapping):
                continue
            material_id = material.get("id")
            text = material.get("text")
            kind = material.get("kind")
            if (
                isinstance(material_id, str)
                and _MATERIAL_ID.fullmatch(material_id)
                and isinstance(text, str)
                and isinstance(kind, str)
            ):
                materials.append(
                    {"id": material_id, "text": text, "kind": kind}
                )
    materials.sort(key=lambda value: int(value["id"][1:]))
    return materials


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
