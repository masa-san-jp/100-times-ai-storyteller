"""S0 run creation for the Phase 1 free-input path."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .input_data import read_free_input
from .orchestrator import Orchestrator, TaskSpec
from .scale import derive_scale
from .seed import generated_seed
from .tables import load_table


# This definition is only the S0-to-S1 hand-off scaffold.  S1's full task
# definition and extraction behavior belong to P1-03.
_S1_DEFINITION: dict[str, Any] = {
    "id": "S1.extract",
    "version": 1,
    "kind": "llm",
    "inputs": {
        "paragraph": {
            "label": "段落",
            "from": "input",
            "select": "input.paragraphs[{slot}]",
            "required": True,
        }
    },
    "output": "json",
    "card": {
        "role": "与えられた段落から、物語の素材を抽出する。",
        "steps": ["段落だけを読み、素材を抽出する。"],
        "output_example": '{"materials": [], "sources": ["p001"]}',
    },
}


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
    resolved_seed = seed if seed is not None else generated_seed()
    scale = derive_scale(
        preset,
        overrides=axis_overrides,
        seed=resolved_seed,
        parts=parts,
        repository_root=repository_root,
    )
    if plot_type is not None:
        plot_ids = {plot["id"] for plot in load_table("plot_types", repository_root=repository_root)["types"]}
        if plot_type not in plot_ids:
            raise ValueError(f"未知のプロット型です: {plot_type}")
        # The manifest schema intentionally permits future scale metadata.
        # Retain a user-selected plot type so S3 can honor it later.
        scale.value["plot_type"] = plot_type

    task_specs = [
        TaskSpec(
            task_id=f"S1.extract-{paragraph['id']}",
            type="S1.extract",
            index=(paragraph["id"],),
        )
        for paragraph in free_input.paragraphs
    ]
    orchestrator = Orchestrator(
        data_dir,
        {"S1.extract": _S1_DEFINITION},
        harness_root=repository_root,
        repository_root=repository_root,
    )
    run_id = orchestrator.create_run(
        task_specs=task_specs,
        seed=resolved_seed,
        input_data=free_input.as_document(),
        input_type="free",
        scale=scale.value,
        table_snapshot={axis: 0 for axis in _element_axes(repository_root)},
    )
    if scale.warnings:
        manifest = orchestrator.load_run(run_id)
        manifest["warnings"].extend(scale.warnings)
        from .manifest import write_manifest

        write_manifest(orchestrator.run_dir(run_id) / "manifest.json", manifest)
    return run_id


def create_story_orchestrator(data_dir: str | Path) -> Orchestrator:
    """Return the small story orchestrator used by the S0/S1 hand-off."""

    repository_root = Path(__file__).resolve().parents[2]
    return Orchestrator(
        data_dir,
        {"S1.extract": _S1_DEFINITION},
        harness_root=repository_root,
        repository_root=repository_root,
    )


def _element_axes(repository_root: str | Path) -> tuple[str, ...]:
    table = load_table("element_axes", repository_root=repository_root)
    return tuple(axis["key"] for axis in table["axes"])
