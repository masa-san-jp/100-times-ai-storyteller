"""Manifest validation and durable JSON access."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .storage import atomic_write_json
from .validation import validate_document


MANIFEST_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "schemas" / "manifest.schema.json"
)


def validate_manifest(
    manifest: Mapping[str, Any],
    *,
    schema_path: str | Path = MANIFEST_SCHEMA_PATH,
) -> Mapping[str, Any]:
    """Validate and return a manifest without applying schema defaults."""
    return validate_document(manifest, schema_path)


def load_manifest(
    path: str | Path,
    *,
    schema_path: str | Path = MANIFEST_SCHEMA_PATH,
) -> dict[str, Any]:
    """Load a JSON manifest and validate it against the manifest schema."""
    manifest_path = Path(path)
    try:
        with manifest_path.open("r", encoding="utf-8", newline=None) as stream:
            manifest = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid manifest: {manifest_path}") from error

    validate_manifest(manifest, schema_path=schema_path)
    return manifest


def write_manifest(
    path: str | Path,
    manifest: Mapping[str, Any],
    *,
    schema_path: str | Path = MANIFEST_SCHEMA_PATH,
) -> None:
    """Validate a manifest and write it as UTF-8 JSON with an atomic replace."""
    validate_manifest(manifest, schema_path=schema_path)
    atomic_write_json(path, manifest)
