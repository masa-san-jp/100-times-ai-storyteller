"""S7 candidate generation (task-model.md §7, story-pipeline.md S7).

The ``what`` item of an event is generated ``candidates`` times with the same
card (task IDs ``S7.event-<event>-what-c<n>``).  The code task
``S7.select-<event>`` then picks one by the ``select_candidate`` method of the
S7.event definition.  The LLM never judges the candidates (P4).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from .batch import load_batch_manifest, trigram_jaccard
from .manifest import load_manifest
from .orchestrator import CodeTaskContext, CodeTaskResult
from .validation import load_and_validate_yaml

_ROOT = Path(__file__).resolve().parents[2]
_DEFINITION = _ROOT / "harness" / "story" / "tasks" / "S7.event.yaml"
_SCHEMA = _ROOT / "schemas" / "task-definition.schema.json"
_CANDIDATE_SUFFIX = re.compile(r"-c[1-9][0-9]*$")


@lru_cache(maxsize=1)
def candidate_settings() -> tuple[int, str]:
    """``(candidates, select_candidate)`` of the S7.event definition."""
    definition = load_and_validate_yaml(_DEFINITION, _SCHEMA)
    return (
        int(definition.get("candidates", 1)),
        str(definition.get("select_candidate", "random")),
    )


def select_task_id(event_id: str) -> str:
    return f"S7.select-{event_id}"


def candidate_task_ids(event_id: str, count: int) -> list[str]:
    return [f"S7.event-{event_id}-what-c{n}" for n in range(1, count + 1)]


def is_candidate_task_id(task_id: str) -> bool:
    return task_id.startswith("S7.event-") and bool(_CANDIDATE_SUFFIX.search(task_id))


def what_task_ids(tasks: Mapping[str, Any], event_id: str) -> list[str]:
    """Task IDs that produce the ``what`` of an event (candidates, or the single task)."""
    candidates = [
        task_id
        for task_id in tasks
        if task_id.startswith(f"S7.event-{event_id}-what-c")
        and is_candidate_task_id(task_id)
    ]
    if candidates:
        return sorted(candidates, key=lambda task_id: int(task_id.rsplit("-c", 1)[1]))
    return [f"S7.event-{event_id}-what"]


def selected_text(output: Any) -> str:
    """The chosen ``what`` text of an S7.select output."""
    return output["text"] if isinstance(output, Mapping) else str(output)


def other_run_texts(context: CodeTaskContext) -> list[str]:
    """Selected outputs of the same task type in the other runs of the batch."""
    manifest = load_manifest(context.run_dir / "manifest.json")
    batch_id = manifest.get("batch_id")
    if not batch_id:
        return []
    batch_path = context.data_dir / "batches" / batch_id / "manifest.json"
    if not batch_path.is_file():
        return []
    texts: list[str] = []
    for entry in load_batch_manifest(context.data_dir, batch_id)["runs"]:
        if entry["run_id"] == context.run_id:
            continue
        tasks_dir = context.data_dir / "runs" / entry["run_id"] / "tasks"
        for path in sorted(tasks_dir.glob("S7.select-*/output.json")):
            try:
                texts.append(selected_text(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError, KeyError):
                continue
    return texts


def story_s7_select(context: CodeTaskContext) -> CodeTaskResult:
    """Choose one candidate of ``what``: ``least_similar`` in a batch, else random."""
    _, method = candidate_settings()
    candidates = context.inputs.get("candidates")
    if not isinstance(candidates, Mapping):
        raise ValueError("S7 の候補がありません")
    if not candidates:
        return CodeTaskResult(output={"candidate": None, "text": "（未定）",
                                      "method": "undecided", "similarity": None})
    ordered = sorted(candidates.items(), key=lambda item: int(item[0].rsplit("-c", 1)[1]))
    comparands = other_run_texts(context) if method == "least_similar" else []
    similarity: float | None = None
    if comparands:
        scores = [
            max(trigram_jaccard(text, other) for other in comparands)
            for _, text in ordered
        ]
        best = min(range(len(ordered)), key=lambda i: (scores[i], i))
        similarity = scores[best]
        used = "least_similar"
    else:
        best = context.random.randrange(len(ordered))
        used = "random"
    key, text = ordered[best]
    return CodeTaskResult(output={
        "candidate": key.rsplit("-", 1)[1],
        "text": text,
        "method": used,
        "similarity": similarity,
    })
