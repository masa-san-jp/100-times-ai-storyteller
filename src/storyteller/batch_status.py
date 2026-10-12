"""`st status --batch`: batch progress, stalled/halted runs and failed-task reasons."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .manifest import load_manifest

_TASK_STATES = ("blocked", "ready", "claimed", "done", "failed", "skipped")
_BATCH_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def batch_status(data_dir: Path, batch_id: str) -> dict[str, Any]:
    """Build the `--batch` payload from the manifests of the batch's runs."""
    if not isinstance(batch_id, str) or not _BATCH_ID_PATTERN.fullmatch(batch_id):
        raise ValueError(f"invalid batch_id: {batch_id!r}")
    batch_manifest = data_dir / "batches" / batch_id / "manifest.json"
    manifests = _batch_run_manifests(data_dir, batch_id)
    if not manifests and not batch_manifest.is_file():
        raise ValueError(f"バッチが見つかりません: {batch_id}")

    runs: list[dict[str, Any]] = []
    problem_runs: list[dict[str, Any]] = []
    failed_tasks: list[dict[str, Any]] = []
    for manifest in manifests:
        counts = {state: 0 for state in _TASK_STATES}
        for task in manifest["tasks"].values():
            counts[task["state"]] += 1
        run_id = manifest["run_id"]
        runs.append({"run_id": run_id, "status": manifest["status"], "counts": counts})
        if manifest["status"] in {"stalled", "halted"}:
            problem_runs.append(
                {
                    "run_id": run_id,
                    "status": manifest["status"],
                    "warnings": list(manifest["warnings"]),
                }
            )
        for task_id in sorted(manifest["tasks"]):
            task = manifest["tasks"][task_id]
            if task["state"] == "failed":
                failed_tasks.append(
                    {
                        "run_id": run_id,
                        "task_id": task_id,
                        "reason": task["error"],
                    }
                )
    statuses = [run["status"] for run in runs]
    return {
        "batch_id": batch_id,
        "requested": _requested(batch_manifest),
        "completed": statuses.count("completed"),
        "duplicates": statuses.count("duplicate"),
        "created": len(runs),
        "runs": runs,
        "problem_runs": problem_runs,
        "failed_tasks": failed_tasks,
    }


def print_batch_status(payload: dict[str, Any]) -> None:
    requested = payload["requested"]
    print(f"batch\t{payload['batch_id']}")
    print(
        "requested\t{}\tcompleted\t{}\tduplicates\t{}\tcreated\t{}".format(
            "-" if requested is None else requested,
            payload["completed"],
            payload["duplicates"],
            payload["created"],
        )
    )
    print("RUN_ID\tSTATUS\tBLOCKED\tREADY\tCLAIMED\tDONE\tFAILED\tSKIPPED")
    for run in payload["runs"]:
        print(
            "\t".join(
                [run["run_id"], run["status"]]
                + [str(run["counts"][state]) for state in _TASK_STATES]
            )
        )
    if payload["problem_runs"]:
        print("RUN_ID\tSTATUS\tWARNINGS")
        for run in payload["problem_runs"]:
            print(f"{run['run_id']}\t{run['status']}\t{' | '.join(run['warnings']) or '-'}")
    if payload["failed_tasks"]:
        print("RUN_ID\tTASK_ID\tREASON")
        for task in payload["failed_tasks"]:
            print(f"{task['run_id']}\t{task['task_id']}\t{task['reason'] or '-'}")


def _batch_run_manifests(data_dir: Path, batch_id: str) -> list[dict[str, Any]]:
    runs_dir = data_dir / "runs"
    if not runs_dir.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for entry in runs_dir.iterdir():
        manifest_path = entry / "manifest.json"
        if not entry.is_dir() or not manifest_path.is_file():
            continue
        try:
            manifest = load_manifest(manifest_path)
        except (ValueError, OSError):
            continue
        if manifest.get("batch_id") == batch_id:
            found.append(manifest)
    found.sort(key=lambda item: (item["created_at"], item["run_id"]))
    return found


def _requested(batch_manifest: Path) -> int | None:
    """The requested count from the batch manifest, or null when unavailable."""
    try:
        data = json.loads(batch_manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("requested") if isinstance(data, dict) else None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None
