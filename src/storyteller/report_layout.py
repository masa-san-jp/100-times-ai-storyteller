"""Where a batch's trend-analysis ("report") run lives (docs/spec/report.md §1).

The report run has the same layout as a run (``manifest.json`` and ``tasks/``)
but sits in ``batches/<batch_id>/report/`` instead of ``runs/<run_id>/``.  This
module has no dependency on the orchestrator so that the orchestrator, the CLI
and the LLM adapter can all find report runs the same way.
"""

from __future__ import annotations

import json
from pathlib import Path


def report_dir(data_dir: str | Path, batch_id: str) -> Path:
    """Return ``batches/<batch_id>/report`` (the directory may not exist yet)."""
    return Path(data_dir) / "batches" / batch_id / "report"


def report_manifest_paths(data_dir: str | Path) -> list[Path]:
    """Return the manifests of every report run, ordered by batch ID."""
    batches = Path(data_dir) / "batches"
    if not batches.is_dir():
        return []
    paths = [entry / "report" / "manifest.json" for entry in sorted(batches.iterdir())]
    return [path for path in paths if path.is_file()]


def find_report_dir(data_dir: str | Path, run_id: str) -> Path | None:
    """Return the report directory whose manifest has *run_id*, if any."""
    for path in report_manifest_paths(data_dir):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        if isinstance(manifest, dict) and manifest.get("run_id") == run_id:
            return path.parent
    return None
