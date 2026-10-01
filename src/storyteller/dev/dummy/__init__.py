"""The small deterministic harness used by ``st dev new-dummy``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...orchestrator import CodeTaskResult, Orchestrator, TaskSpec
from ...validation import load_and_validate_yaml


ROOT = Path(__file__).resolve().parent
TASK_SCHEMA = Path(__file__).resolve().parents[4] / "schemas" / "task-definition.schema.json"


def _definitions() -> dict[str, dict[str, Any]]:
    definitions: dict[str, dict[str, Any]] = {}
    for path in sorted((ROOT / "tasks").glob("*.yaml")):
        definition = load_and_validate_yaml(path, TASK_SCHEMA)
        definitions[definition["id"]] = definition
    return definitions


def _dummy_items(context: Any) -> dict[str, Any]:
    return {
        "items": [
            {"id": "d1", "text": "alpha"},
            {"id": "d2", "text": "beta"},
            {"id": "d3", "text": "gamma"},
        ]
    }


def _dummy_check(context: Any) -> dict[str, Any]:
    for task_id, output in context.dependency_outputs.items():
        if isinstance(output, dict) and "INVALID" in output.get("text", ""):
            context.invalidate_task(task_id, "text に INVALID が含まれています")
    return {"checked": True}


def _dummy_assemble(context: Any) -> dict[str, str]:
    return {"text": context.inputs["text"]}


def _dummy_handlers() -> dict[str, Any]:
    def expand(context: Any) -> CodeTaskResult:
        item_tasks = [
            TaskSpec(
                f"D2.echo-{item_id}",
                "D2.echo",
                deps=(context.task_id,),
                index=(item_id,),
            )
            for item_id in ("d1", "d2", "d3")
        ]
        item_ids = tuple(task.task_id for task in item_tasks)
        return CodeTaskResult(
            output=_dummy_items(context),
            add_tasks=[
                *item_tasks,
                TaskSpec(
                    "D3.check",
                    "D3.check",
                    deps=(context.task_id, *item_ids),
                ),
                TaskSpec(
                    "D4.story",
                    "D4.story",
                    deps=(context.task_id, "D3.check", *item_ids),
                ),
                TaskSpec(
                    "D5.assemble",
                    "D5.assemble",
                    deps=(context.task_id, "D4.story"),
                ),
            ],
        )

    return {
        "dummy_items": expand,
        "dummy_check": _dummy_check,
        "dummy_assemble": _dummy_assemble,
    }


def create_dummy_orchestrator(data_dir: str | Path) -> Orchestrator:
    """Build an orchestrator whose definitions and handlers come from dummy/."""
    return Orchestrator(
        data_dir,
        _definitions(),
        _dummy_handlers(),
        harness_root=ROOT,
        repository_root=ROOT.parents[3],
        harness_kind="dummy",
    )


__all__ = ["ROOT", "create_dummy_orchestrator"]
