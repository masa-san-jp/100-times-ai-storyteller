"""Manifest validation and durable JSON access.

A manifest holds every task of a run, so validating all of it with JSON
Schema on every read and write costs about a second at 1,500 tasks.  The
schema has no constraint that spans tasks, which allows two safe shortcuts
that leave the schema and the accepted documents unchanged:

* ``write_manifest`` first records ``manifest.validated`` (the SHA-256 of the
  bytes it is about to write, plus the schema's hash).  ``load_manifest``
  skips validation only when the file's bytes hash to that value, so any
  other content (a hand edit, a manifest from another writer) is still fully
  validated.
* After a document is known to be valid in this process, ``write_manifest``
  validates only the top-level fields and the tasks that differ from that
  document.  Any failure falls back to a full validation, so error messages
  are the ones a full validation reports.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .storage import atomic_write_text
from .validation import validate_document


MANIFEST_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "schemas" / "manifest.schema.json"
)
VALIDATED_STAMP_NAME = "manifest.validated"

_KNOWN_VALID_LIMIT = 16
# manifest path -> text of the last document known to satisfy the schema.
_known_valid: dict[str, str] = {}
_fast_validators: dict[tuple[str, int, int], tuple[str, Any, Any]] = {}


def validate_manifest(
    manifest: Mapping[str, Any],
    *,
    schema_path: str | Path = MANIFEST_SCHEMA_PATH,
) -> Mapping[str, Any]:
    """Validate and return a manifest without applying schema defaults."""
    return validate_document(manifest, schema_path)


def _is_default_schema(schema_path: str | Path) -> bool:
    return Path(schema_path) == MANIFEST_SCHEMA_PATH


def _schema_validators() -> tuple[str, Any, Any] | None:
    """Return ``(schema sha256, top-level validator, task validator)``."""
    try:
        stat = MANIFEST_SCHEMA_PATH.stat()
        key = (str(MANIFEST_SCHEMA_PATH), stat.st_mtime_ns, stat.st_size)
        cached = _fast_validators.get(key)
        if cached is not None:
            return cached
        raw = MANIFEST_SCHEMA_PATH.read_bytes()
        schema = json.loads(raw.decode("utf-8"))
        task_schema = {
            "$schema": schema["$schema"],
            "$defs": schema["$defs"],
            "$ref": "#/$defs/task",
        }
        entry = (
            hashlib.sha256(raw).hexdigest(),
            Draft202012Validator(schema),
            Draft202012Validator(task_schema),
        )
    except (OSError, ValueError, KeyError):
        return None
    _fast_validators.clear()
    _fast_validators[key] = entry
    return entry


def _stamp_path(manifest_path: Path) -> Path:
    return manifest_path.with_name(VALIDATED_STAMP_NAME)


def _stamp_text(data: bytes, schema_sha256: str) -> str:
    return json.dumps(
        {"manifest_sha256": hashlib.sha256(data).hexdigest(), "schema_sha256": schema_sha256}
    )


def _remember(path: Path, text: str) -> None:
    key = str(path)
    _known_valid.pop(key, None)
    _known_valid[key] = text
    while len(_known_valid) > _KNOWN_VALID_LIMIT:
        _known_valid.pop(next(iter(_known_valid)))


def load_manifest(
    path: str | Path,
    *,
    schema_path: str | Path = MANIFEST_SCHEMA_PATH,
) -> dict[str, Any]:
    """Load a JSON manifest and validate it against the manifest schema."""
    manifest_path = Path(path)
    try:
        data = manifest_path.read_bytes()
        text = data.decode("utf-8")
        manifest = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid manifest: {manifest_path}") from error

    fast = _is_default_schema(schema_path)
    validators = _schema_validators() if fast else None
    if validators is not None:
        try:
            stamp = _stamp_path(manifest_path).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            stamp = None
        if stamp == _stamp_text(data, validators[0]):
            _remember(manifest_path, text)
            return manifest

    validate_manifest(manifest, schema_path=schema_path)
    if validators is not None:
        _remember(manifest_path, text)
    return manifest


def _validate_changes(manifest: Mapping[str, Any], known_text: str) -> bool:
    """Validate only what differs from a known-valid document.

    Returns ``False`` when the shortcut does not apply or finds an error; the
    caller then runs the full validation.  Tasks are compared with ``==``, so
    a field changing between ``1`` and ``True`` is the one change it cannot
    see; the code that edits manifests never does that.
    """
    validators = _schema_validators()
    tasks = manifest.get("tasks") if isinstance(manifest, Mapping) else None
    if validators is None or not isinstance(tasks, dict):
        return False
    try:
        known = json.loads(known_text)
        known_tasks = known["tasks"]
    except (ValueError, KeyError, TypeError):
        return False
    _, top_validator, task_validator = validators
    top = {key: value for key, value in manifest.items() if key != "tasks"}
    known_top = {key: value for key, value in known.items() if key != "tasks"}
    if top != known_top:
        top["tasks"] = {}
        if not top_validator.is_valid(top):
            return False
    for task_id, task in tasks.items():
        if known_tasks.get(task_id) != task and not task_validator.is_valid(task):
            return False
    return True


def write_manifest(
    path: str | Path,
    manifest: Mapping[str, Any],
    *,
    schema_path: str | Path = MANIFEST_SCHEMA_PATH,
) -> None:
    """Validate a manifest and write it as UTF-8 JSON with an atomic replace."""
    manifest_path = Path(path)
    fast = _is_default_schema(schema_path)
    known_text = _known_valid.get(str(manifest_path)) if fast else None
    if known_text is None or not _validate_changes(manifest, known_text):
        validate_manifest(manifest, schema_path=schema_path)

    text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    validators = _schema_validators() if fast else None
    if validators is not None:
        # The stamp is written first: it only says "these bytes are valid",
        # which stays true whatever happens to the manifest after that.
        try:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            _stamp_path(manifest_path).write_text(
                _stamp_text(text.encode("utf-8"), validators[0]), encoding="utf-8"
            )
        except OSError:
            pass
    atomic_write_text(manifest_path, text)
    if validators is not None:
        _remember(manifest_path, text)
