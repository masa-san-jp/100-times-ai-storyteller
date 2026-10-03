"""Check opening diversity across all facets of one world section."""

from __future__ import annotations

from collections.abc import Mapping
from itertools import combinations
from pathlib import Path

from .manifest import load_manifest
from .orchestrator import CodeTaskContext, CodeTaskResult
from .tables import load_table
from .validation import load_and_validate_yaml


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def story_s4_diversity(context: CodeTaskContext) -> CodeTaskResult:
    """Retry later task IDs with similar first 80 characters, within limits."""
    threshold = load_table("dedup", repository_root=_REPOSITORY_ROOT)["opening_threshold"]
    section_definition = load_and_validate_yaml(
        _REPOSITORY_ROOT / "harness/story/tasks/S4.section.yaml",
        _REPOSITORY_ROOT / "schemas/task-definition.schema.json",
    )
    max_invalidations = section_definition.get("max_invalidations", 2)
    manifest = load_manifest(context.run_dir / "manifest.json")
    openings: dict[str, str] = {}
    for task_id, output in sorted(context.dependency_outputs.items()):
        if manifest["tasks"][task_id]["type"] != "S4.section":
            continue
        if not isinstance(output, Mapping) or not isinstance(output.get("body"), str):
            raise ValueError(f"S4 の本文がありません: {task_id}")
        openings[task_id] = output["body"][:80]
    grams = {
        task_id: {opening[i:i + 3] for i in range(len(opening) - 2)}
        for task_id, opening in openings.items()
    }
    similar: dict[str, list[str]] = {}
    for earlier, later in combinations(openings, 2):
        union = grams[earlier] | grams[later]
        score = len(grams[earlier] & grams[later]) / len(union) if union else 0.0
        if score >= threshold:
            similar.setdefault(later, []).append(
                f"{earlier} の書き出し「{openings[earlier]}」"
            )

    invalidations: list[tuple[str, str]] = []
    warnings: list[str] = []
    for task_id, matches in similar.items():
        reason = f"書き出しが似すぎています：{'／'.join(matches)}。この書き出しと似ないように書き始める。"
        if manifest["tasks"][task_id]["invalidations"] < max_invalidations:
            invalidations.append((task_id, reason))
        else:
            warning = (
                f"{task_id}: 書き出しの類似が無効化上限（{max_invalidations}回）に達したため、"
                f"最後の出力を採用。{reason}"
            )
            if warning not in manifest["warnings"]:
                warnings.append(warning)
    return CodeTaskResult(
        output={"invalidated": [task_id for task_id, _ in invalidations]},
        invalidations=invalidations,
        manifest_updates={"warnings": warnings} if warnings else {},
    )
