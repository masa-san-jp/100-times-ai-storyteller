"""Batches: ``st new --count``, duplicate detection and replenishment.

Implements story-pipeline.md §7 and the batch manifest of data-layout.md §3.2.
All updates of ``batches/<batch_id>/manifest.json`` happen under ``batch.lock``.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .seed import derive_run_seed, generated_seed
from .storage import atomic_write_json, batch_lock
from .tables import load_table
from .validation import validate_document

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_BATCH_SCHEMA = _REPOSITORY_ROOT / "schemas" / "batch-manifest.schema.json"
_SCHEMA_VERSION = 1


def max_created(requested: int) -> int:
    """Upper limit of runs a batch may create (story-pipeline.md §7)."""
    return max(3 * requested, requested + 10)


def batch_dir(data_dir: str | Path, batch_id: str) -> Path:
    return Path(data_dir) / "batches" / batch_id


def load_batch_manifest(data_dir: str | Path, batch_id: str) -> dict[str, Any]:
    path = batch_dir(data_dir, batch_id) / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    validate_document(manifest, _BATCH_SCHEMA, source_path=path)
    return manifest


def _write_batch_manifest(directory: Path, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = _now()
    validate_document(manifest, _BATCH_SCHEMA)
    atomic_write_json(directory / "manifest.json", manifest)


def _now() -> str:
    value = datetime.now(timezone.utc).replace(microsecond=0)
    return value.isoformat().replace("+00:00", "Z")


def _batch_id(created_at: str, seed: int) -> str:
    date, time = created_at.rstrip("Z").split("T")
    prefix = f"{seed:08x}"[:6]
    return f"{date.replace('-', '')}-{time.replace(':', '')}-{prefix}"


def create_batch(
    data_dir: str | Path,
    input_path: str | Path,
    *,
    input_kind: str,
    count: int,
    preset: str,
    axis_overrides: Mapping[str, str] | None,
    seed: int | None,
    parts: int | None,
    plot_type: str | None,
    repository_root: str | Path,
) -> str:
    """Create a batch manifest and its first ``count`` runs; return the batch ID."""

    from .input_data import read_free_input
    from .narrative import read_narrative

    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("--count は1以上の整数で指定してください")
    if input_kind == "free":
        source = read_free_input(input_path)
    elif input_kind == "narrative":
        source = read_narrative(input_path)
    else:
        raise ValueError(f"未知の入力の種類です: {input_kind}")
    document = source.as_document()

    batch_seed = seed if seed is not None else generated_seed()
    created_at = _now()
    batch_id = _batch_id(created_at, batch_seed)
    directory = batch_dir(data_dir, batch_id)
    directory.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "batch_id": batch_id,
        "created_at": created_at,
        "updated_at": created_at,
        "seed": batch_seed,
        "requested": count,
        "max_created": max_created(count),
        "completed": 0,
        "duplicates": 0,
        "params": {
            "preset": preset,
            "axis_overrides": dict(axis_overrides or {}),
            "parts": parts,
            "plot_type": plot_type,
        },
        "runs": [],
    }
    with batch_lock(directory):
        _write_batch_manifest(directory, manifest)
    # Each run is created outside ``batch.lock`` and registered under it, so the
    # lock is held only briefly (data-layout.md §5) even for a large ``count``.
    # Indices below ``count`` are reserved for these runs; replenishment uses
    # the indices after them (see ``_next_index``).
    for index in range(count):
        run_id = _create_run(data_dir, manifest, document, index, repository_root)
        with batch_lock(directory):
            current = load_batch_manifest(data_dir, batch_id)
            current["runs"].append(
                {"run_id": run_id, "index": index, "outcome": "pending"}
            )
            _write_batch_manifest(directory, current)
    return batch_id


def _next_index(manifest: Mapping[str, Any]) -> int:
    """Index of the next replenishment run; indices below ``requested`` are reserved."""
    indices = [entry["index"] for entry in manifest["runs"]]
    return max([manifest["requested"] - 1, *indices]) + 1


def _create_run(
    data_dir: str | Path,
    manifest: Mapping[str, Any],
    document: Mapping[str, Any],
    index: int,
    repository_root: str | Path,
) -> str:
    """Create run ``index`` of the batch and return its run ID."""

    from .new_run import create_run_from_paragraphs

    params = manifest["params"]
    run_id = create_run_from_paragraphs(
        data_dir,
        dict(document),
        document["paragraphs"],
        input_type=str(document["kind"]),
        preset=params["preset"],
        axis_overrides=dict(params["axis_overrides"]),
        seed=derive_run_seed(manifest["seed"], index),
        parts=params["parts"],
        plot_type=params["plot_type"],
        repository_root=repository_root,
        batch_id=manifest["batch_id"],
        seed_source="batch",
    )
    return run_id


def _normalize(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split()).lower()


def story_fingerprint(story: Mapping[str, Any]) -> str:
    """sha256 over cast names/profiles and all event fields (§7)."""
    parts: list[str] = []
    for person in story.get("cast", []):
        parts.append(str(person.get("name", "")))
        parts.append(str(person.get("profile", "")))
    for event in story.get("events", []):
        parts.append(json.dumps(event, ensure_ascii=False, sort_keys=True))
    return hashlib.sha256(_normalize("".join(parts)).encode("utf-8")).hexdigest()


def _near_text(story: Mapping[str, Any]) -> str:
    return "".join(
        str(event.get("what", "")) + str(event.get("result", ""))
        for event in story.get("events", [])
    )


def _trigrams(text: str) -> set[str]:
    return {text[i : i + 3] for i in range(len(text) - 2)}


def trigram_jaccard(left: str, right: str) -> float:
    a, b = _trigrams(left), _trigrams(right)
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def is_duplicate_story(
    story: Mapping[str, Any],
    others: Sequence[Mapping[str, Any]],
    threshold: float,
) -> bool:
    """Whether ``story`` equals (fingerprint) or nearly equals (3-gram) any of ``others``."""
    fingerprint = story_fingerprint(story)
    text = _near_text(story)
    for other in others:
        if story_fingerprint(other) == fingerprint:
            return True
        if trigram_jaccard(text, _near_text(other)) >= threshold:
            return True
    return False


def settle_batch_run(
    data_dir: str | Path,
    batch_id: str,
    run_id: str,
    story: Mapping[str, Any],
    run_input: Mapping[str, Any],
    *,
    repository_root: str | Path = _REPOSITORY_ROOT,
) -> bool:
    """Decide duplicate or accepted for ``run_id`` and replenish; return True if duplicate.

    Runs under ``batch.lock``.  A batch without a manifest is left alone.
    """

    directory = batch_dir(data_dir, batch_id)
    if not (directory / "manifest.json").is_file():
        return False
    threshold = load_table("dedup", repository_root=repository_root)[
        "near_duplicate_threshold"
    ]
    with batch_lock(directory):
        manifest = load_batch_manifest(data_dir, batch_id)
        entry = next((r for r in manifest["runs"] if r["run_id"] == run_id), None)
        if entry is None:
            return False
        if entry["outcome"] != "pending":
            # S9 was executed again (retry after a later failure): keep the
            # earlier decision and do not replenish twice.
            return entry["outcome"] == "duplicate"
        others = [
            _read_story(data_dir, r["run_id"])
            for r in manifest["runs"]
            if r["outcome"] == "accepted" and r["run_id"] != run_id
        ]
        duplicate = is_duplicate_story(story, others, threshold)
        entry["outcome"] = "duplicate" if duplicate else "accepted"
        manifest["completed"] = sum(r["outcome"] == "accepted" for r in manifest["runs"])
        manifest["duplicates"] = sum(r["outcome"] == "duplicate" for r in manifest["runs"])
        index = _next_index(manifest)
        if duplicate and index < manifest["max_created"]:
            new_run_id = _create_run(
                data_dir, manifest, run_input, index, repository_root
            )
            manifest["runs"].append(
                {"run_id": new_run_id, "index": index, "outcome": "pending"}
            )
        _write_batch_manifest(directory, manifest)
    return duplicate


def _read_story(data_dir: str | Path, run_id: str) -> Mapping[str, Any]:
    path = Path(data_dir) / "runs" / run_id / "story" / "story.json"
    return json.loads(path.read_text(encoding="utf-8"))
