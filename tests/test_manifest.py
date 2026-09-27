from __future__ import annotations

import json
from pathlib import Path

import pytest

from storyteller.manifest import load_manifest, validate_manifest, write_manifest
from storyteller.validation import SchemaValidationError


def manifest() -> dict:
    return {
        "schema_version": 1,
        "run_id": "20260927-031500-123abc",
        "batch_id": None,
        "created_at": "2026-09-27T03:15:00Z",
        "updated_at": "2026-09-27T03:15:00Z",
        "status": "active",
        "seed": 123456,
        "seed_source": "argument",
        "input": {},
        "scale": {},
        "harness": {"schemas/manifest.schema.json": "a" * 64},
        "table_snapshot": {},
        "tasks": {
            "D1.items": {
                "type": "D1.items",
                "kind": "code",
                "state": "ready",
                "deps": [],
                "index": [],
                "attempt": 0,
                "tries": 0,
                "invalidations": 0,
                "continuation_step": 0,
                "cache_key": None,
                "claim": None,
                "history": [],
                "error": None,
            }
        },
        "warnings": [],
    }


def test_manifest_schema_accepts_phase_zero_manifest() -> None:
    assert validate_manifest(manifest())["schema_version"] == 1


def test_manifest_schema_rejects_unknown_top_level_and_task_fields() -> None:
    invalid = manifest()
    invalid["unexpected"] = True
    with pytest.raises(SchemaValidationError):
        validate_manifest(invalid)

    invalid = manifest()
    invalid["tasks"]["D1.items"]["unexpected"] = True
    with pytest.raises(SchemaValidationError):
        validate_manifest(invalid)


def test_manifest_schema_validates_claim_and_timestamp() -> None:
    value = manifest()
    value["tasks"]["D1.items"]["claim"] = {
        "ticket": "b" * 32,
        "executor_id": "codex.worker-1",
        "isolation": "permission",
        "lease_expires_at": "2026-09-27T03:15:03Z",
    }
    validate_manifest(value)

    value["tasks"]["D1.items"]["claim"]["ticket"] = "not-a-ticket"
    with pytest.raises(SchemaValidationError):
        validate_manifest(value)


def test_manifest_schema_requires_both_input_kind_and_digest() -> None:
    value = manifest()
    value["input"] = {"type": "free"}
    with pytest.raises(SchemaValidationError):
        validate_manifest(value)

    value["input"] = {"type": "free", "sha256": "c" * 64}
    validate_manifest(value)


def test_manifest_is_written_atomically_and_loaded(tmp_path: Path) -> None:
    path = tmp_path / "runs" / "run" / "manifest.json"
    write_manifest(path, manifest())
    assert json.loads(path.read_text(encoding="utf-8")) == manifest()
    assert load_manifest(path) == manifest()


def test_invalid_manifest_is_not_written(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text("old", encoding="utf-8")
    invalid = manifest()
    invalid["status"] = "unknown"
    with pytest.raises(SchemaValidationError):
        write_manifest(path, invalid)
    assert path.read_text(encoding="utf-8") == "old"
