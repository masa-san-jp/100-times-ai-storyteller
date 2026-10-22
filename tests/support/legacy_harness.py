"""The story task definitions before ADR-0011 (S6.expand v5 graph), for the tests of that graph.

ADR-0011 replaced these definitions in ``harness/story/tasks``.  The graphs
that S6.expand v5 and older create stay supported (story-pipeline S6.plot), so
their tests run against a frozen copy of the definitions as they were.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

LEGACY_TASKS = Path(__file__).resolve().parents[1] / "fixtures" / "legacy_story_tasks"


def legacy_definition(task_type: str) -> dict:
    return yaml.safe_load((LEGACY_TASKS / f"{task_type}.yaml").read_text(encoding="utf-8"))


def legacy_definitions() -> dict[str, dict]:
    return {path.stem: yaml.safe_load(path.read_text(encoding="utf-8"))
            for path in sorted(LEGACY_TASKS.glob("*.yaml"))}


@pytest.fixture(autouse=True)
def legacy_story_harness(monkeypatch):
    """Create runs from the pre-ADR-0011 definitions (S6.expand v5)."""

    import storyteller.new_run as new_run

    original = new_run._load_story_task_definitions

    def load(repository_root):
        definitions = original(repository_root)
        definitions.update(legacy_definitions())
        return definitions

    monkeypatch.setattr(new_run, "_load_story_task_definitions", load)


def task_path(task_type: str) -> Path:
    """The pre-ADR-0011 definition's path when it was replaced, else the current one."""

    legacy = LEGACY_TASKS / f"{task_type}.yaml"
    if legacy.is_file():
        return legacy
    return Path(__file__).resolve().parents[2] / "harness" / "story" / "tasks" / f"{task_type}.yaml"
