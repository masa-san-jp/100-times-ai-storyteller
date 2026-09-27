"""The small deterministic harness used by ``st dev new-dummy``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...orchestrator import Orchestrator
from ...validation import load_and_validate_yaml


ROOT = Path(__file__).resolve().parent
TASK_SCHEMA = Path(__file__).resolve().parents[4] / "schemas" / "task-definition.schema.json"


def _definitions() -> dict[str, dict[str, Any]]:
    definitions: dict[str, dict[str, Any]] = {}
    for path in sorted((ROOT / "tasks").glob("*.yaml")):
        definition = load_and_validate_yaml(path, TASK_SCHEMA)
        definitions[definition["id"]] = definition
    return definitions


def create_dummy_orchestrator(data_dir: str | Path) -> Orchestrator:
    """Build an orchestrator whose definitions and handlers come from dummy/."""
    return Orchestrator(
        data_dir,
        _definitions(),
        harness_root=ROOT,
        repository_root=ROOT.parents[3],
    )


__all__ = ["ROOT", "create_dummy_orchestrator"]
