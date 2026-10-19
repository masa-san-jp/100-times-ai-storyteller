"""S0 run creation for the Phase 1 free-input path."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .input_data import read_free_input
from .orchestrator import Orchestrator, TaskSpec
from .scale import derive_scale
from .seed import generated_seed
from .story_ledger import ANALYSIS_KINDS, WHOLE_INPUT_WARNING, whole_input_text
from .story_s2 import story_s2_merge, story_s2_plan
from .story_s3 import story_s3_assign
from .story_s4 import story_s4_diversity, story_s4_consistency_judge
from .character_facts import story_s5_fact_plan
from .formatting import load_format_task_definitions, make_handlers as make_format_handlers
from .story_s5 import story_s5_relationship_context
from .story_s6 import story_s6_expand
from .story_s7 import story_s7_assemble
from .story_s7_candidates import story_s7_select
from .story_s8 import (
    story_s8_condition_judge, story_s8_detail_judge, story_s8_judge, story_s8_plan,
)
from .story_s9 import story_s9_assemble
from .tables import load_table
from .validation import load_and_validate_yaml
from .volume import apply_volume_update, compute_initial_volume


_TASK_DEFINITION_SCHEMA = "schemas/task-definition.schema.json"
STAGED_S6_VERSION = 6


def create_free_run(
    data_dir: str | Path,
    input_path: str | Path,
    *,
    preset: str,
    axis_overrides: dict[str, str] | None,
    seed: int | None,
    parts: int | None,
    plot_type: str | None,
    repository_root: str | Path,
) -> str:
    """Validate free input, derive S0, and create the initial S1 DAG."""

    free_input = read_free_input(input_path)
    return create_run_from_paragraphs(
        data_dir,
        free_input.as_document(),
        free_input.paragraphs,
        input_type="free",
        preset=preset,
        axis_overrides=axis_overrides,
        seed=seed,
        parts=parts,
        plot_type=plot_type,
        repository_root=repository_root,
    )


def create_run_from_paragraphs(
    data_dir: str | Path,
    input_document: dict[str, Any],
    paragraphs: Any,
    *,
    input_type: str,
    preset: str,
    axis_overrides: dict[str, str] | None,
    seed: int | None,
    parts: int | None,
    plot_type: str | None,
    repository_root: str | Path,
    batch_id: str | None = None,
    seed_source: str | None = None,
) -> str:
    """Derive S0 from a normalized input and create the initial S1 DAG."""

    resolved_seed = seed if seed is not None else generated_seed()
    scale = derive_scale(
        preset,
        overrides=axis_overrides,
        seed=resolved_seed,
        parts=parts,
        repository_root=repository_root,
    )
    scale_value = apply_volume_update(
        {"scale": scale.value},
        compute_initial_volume(scale.value, repository_root=repository_root),
    )["scale"]
    if plot_type is not None:
        plot_ids = {plot["id"] for plot in load_table("plot_types", repository_root=repository_root)["types"]}
        if plot_type not in plot_ids:
            raise ValueError(f"未知のプロット型です: {plot_type}")

    material_count = (load_table("scales", repository_root=repository_root)["dev"]["materials_per_paragraph"]
                      if preset == "dev" else 5)

    task_definitions = _load_story_task_definitions(repository_root)
    # ADR-0011: S6.expand v6 and later is the staged graph.  S0 creates the three
    # analyses of the whole input and every S1.extract waits for them.
    staged = task_definitions["S6.expand"]["version"] >= STAGED_S6_VERSION
    analysis_specs = [
        TaskSpec(task_id=f"S1.analysis-{kind}", type="S1.analysis", index=(kind,))
        for kind in ANALYSIS_KINDS
    ] if staged else []
    analysis_ids = tuple(spec.task_id for spec in analysis_specs)
    task_specs = [
        *analysis_specs,
        *(
            TaskSpec(
                task_id=f"S1.extract-{paragraph['id']}-{number}",
                type="S1.extract",
                deps=(
                    *analysis_ids,
                    *(f"S1.extract-{paragraph['id']}-{earlier}" for earlier in range(1, number)),
                ),
                index=(paragraph["id"], str(number)),
            )
            for paragraph in paragraphs
            for number in range(1, material_count + 1)
        ),
    ]
    s1_task_ids = tuple(spec.task_id for spec in task_specs if spec.type == "S1.extract")
    task_specs.append(
        TaskSpec(
            task_id="S2.plan",
            type="S2.plan",
            deps=s1_task_ids,
        )
    )
    orchestrator = Orchestrator(
        data_dir,
        task_definitions,
        harness_root=repository_root,
        repository_root=repository_root,
    )
    run_id = orchestrator.create_run(
        task_specs=task_specs,
        seed=resolved_seed,
        input_data=input_document,
        input_type=input_type,
        plot_type=plot_type,
        scale=scale_value,
        table_snapshot={axis: 0 for axis in _element_axes(repository_root)},
        harness_kind="story",
        batch_id=batch_id,
        seed_source=seed_source,
    )
    warnings = list(scale.warnings)
    if staged and whole_input_text(input_document)[1]:
        warnings.append(WHOLE_INPUT_WARNING)
    if warnings:
        manifest = orchestrator.load_run(run_id)
        manifest["warnings"].extend(warnings)
        from .manifest import write_manifest

        write_manifest(orchestrator.run_dir(run_id) / "manifest.json", manifest)
    return run_id


def create_story_orchestrator(
    data_dir: str | Path, *, formats_root: str | Path | None = None
) -> Orchestrator:
    """Return the story orchestrator used by the S0/S1 hand-off.

    It also knows the formatting tasks (``st format``), which read format
    profiles from ``formats_root`` (default: the repository's ``formats/``).
    """

    repository_root = Path(__file__).resolve().parents[2]
    return Orchestrator(
        data_dir,
        {
            **_load_story_task_definitions(repository_root),
            **load_format_task_definitions(repository_root),
        },
        {
            **make_format_handlers(repository_root, formats_root),
            "story_s2_plan": story_s2_plan,
            "story_s2_merge": story_s2_merge,
            "story_s3_assign": story_s3_assign,
            "story_s4_diversity": story_s4_diversity,
            "story_s4_consistency_judge": story_s4_consistency_judge,
            "story_s5_fact_plan": story_s5_fact_plan,
            "story_s5_relationship_context": story_s5_relationship_context,
            "story_s6_expand": story_s6_expand,
            "story_s7_assemble": story_s7_assemble,
            "story_s7_select": story_s7_select,
            "story_s8_judge": story_s8_judge,
            "story_s8_plan": story_s8_plan,
            "story_s8_detail_judge": story_s8_detail_judge,
            "story_s8_condition_judge": story_s8_condition_judge,
            "story_s9_assemble": story_s9_assemble,
        },
        harness_root=repository_root,
        repository_root=repository_root,
        harness_kind="story",
    )


def _load_story_task_definitions(
    repository_root: str | Path,
) -> dict[str, dict[str, Any]]:
    """Load the story task definitions from their harness source files."""

    root = Path(repository_root)
    tasks_dir = root / "harness" / "story" / "tasks"
    schema_path = root / _TASK_DEFINITION_SCHEMA
    definitions: dict[str, dict[str, Any]] = {}
    for path in sorted(tasks_dir.glob("*.yaml")):
        definition = load_and_validate_yaml(path, schema_path)
        if not isinstance(definition, dict):
            raise ValueError(f"タスク定義がオブジェクトではありません: {path}")
        task_id = definition.get("id")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError(f"タスク定義に id がありません: {path}")
        if task_id in definitions:
            raise ValueError(f"タスク定義 ID が重複しています: {task_id}")
        definitions[task_id] = definition
    if "S1.extract" not in definitions:
        raise ValueError("S1.extract のタスク定義がありません")
    return definitions


def _element_axes(repository_root: str | Path) -> tuple[str, ...]:
    table = load_table("element_axes", repository_root=repository_root)
    return tuple(axis["key"] for axis in table["axes"])
