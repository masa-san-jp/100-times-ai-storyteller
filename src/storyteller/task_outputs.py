"""Read persisted task outputs and attach code-owned text attribution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .validation import input_source_ids


def text_task_sources(task_dir: Path) -> list[str]:
    """Use the saved, fitted card inputs, never the unbounded dependencies."""
    path = task_dir / "input.json"
    if not path.is_file():
        return []
    inputs = json.loads(path.read_text(encoding="utf-8"))
    return input_source_ids(inputs)


def read_task_output(task_dir: Path, task_type: str) -> Any:
    """Expose story text fields to existing selectors and code consumers.

    Text remains plain text in output.md. Its sources are reconstructed from
    the durable card input record and recorded in the canonical story.
    """
    json_path = task_dir / "output.json"
    if json_path.is_file():
        return json.loads(json_path.read_text(encoding="utf-8"))
    text = (task_dir / "output.md").read_text(encoding="utf-8")
    if task_type in {"S4.section", "S4.item"}:
        output = {"body": text, "sources": text_task_sources(task_dir)}
        if task_type == "S4.item":
            name_id = task_dir.name.replace("S4.item-", "S4.item_name-", 1)
            name = read_task_output(task_dir.parent / name_id, "S4.item_name")
            output["name"] = name["name"]
        return output
    if task_type.startswith("S5."):
        return {task_type.split(".", 1)[1]: text, "sources": text_task_sources(task_dir)}
    return text
