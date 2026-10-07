"""Code-owned S1 IDs and the sequential S1/S2 card views."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


def assign_material_id(task: Mapping[str, Any], value: Any) -> Any:
    """Keep each material's ID stable across retries, skips and cache hits."""
    if task.get("type") != "S1.extract" or not isinstance(value, Mapping):
        return value
    index = task.get("index", [])
    if (len(index) != 2 or not re.fullmatch(r"p[0-9]{3}", index[0])
            or index[1] not in {"1", "2", "3", "4", "5"}):
        return value
    number = (int(index[0][1:]) - 1) * 5 + int(index[1])
    return {**value, "id": f"m{number:03d}"}


def materials_from_outputs(outputs: Mapping[str, Any]) -> list[dict[str, str]]:
    """Collect code-owned materials from labeled elements or code-assembled lists."""
    materials = []
    for output in outputs.values():
        if not isinstance(output, Mapping):
            continue
        for material in output.get("materials", [output]):
            if (isinstance(material, Mapping)
                    and isinstance(material.get("id"), str)
                    and re.fullmatch(r"m[0-9]{3,}", material["id"])
                    and isinstance(material.get("text"), str)
                    and isinstance(material.get("kind"), str)):
                materials.append({key: material[key] for key in ("id", "text", "kind")})
    return sorted(materials, key=lambda item: int(item["id"][1:]))


def sequential_sources(
    task: Mapping[str, Any], manifest: Mapping[str, Any],
    outputs: Mapping[str, Any], sources: dict[str, Any],
) -> dict[str, Any]:
    """Expose only accepted earlier elements from the current group."""
    task_type = task.get("type")
    if task_type == "S1.extract":
        earlier = {key: value for key, value in outputs.items()
                   if manifest["tasks"][key]["type"] == "S1.extract"}
        sources["S1.extract"] = {"previous": materials_from_outputs(earlier)}
    elif task_type in {"S2.expand", "S2.counter"}:
        sources["S2.expand"] = {"previous": [
            value for key, value in outputs.items()
            if manifest["tasks"][key]["type"] == "S2.expand" and isinstance(value, str)
        ]}
    return sources
