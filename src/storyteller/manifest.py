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
* A known-valid snapshot is bound to its schema hash. ``write_manifest``
  validates changes only when the manifest/container shapes are independent.
  For task objects whose only constraints are their keys and properties,
  entire changed field values use the original subschemas. Other shapes or
  any error fall back to full validation, retaining its public error messages.
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
# manifest path -> (schema hash, text of the last known-valid document).
_known_valid: dict[str, tuple[str, str]] = {}
_fast_validators: dict[tuple[str, int, int], tuple[str, Any, Any, Any, bool]] = {}


def validate_manifest(
    manifest: Mapping[str, Any],
    *,
    schema_path: str | Path = MANIFEST_SCHEMA_PATH,
) -> Mapping[str, Any]:
    """Validate and return a manifest without applying schema defaults."""
    return validate_document(manifest, schema_path)


def _is_default_schema(schema_path: str | Path) -> bool:
    return Path(schema_path) == MANIFEST_SCHEMA_PATH


def _schema_validators() -> tuple[str, Any, Any, Any, bool] | None:
    """Return schema identity, full validators and independent task fields."""
    try:
        stat = MANIFEST_SCHEMA_PATH.stat()
        key = (str(MANIFEST_SCHEMA_PATH), stat.st_mtime_ns, stat.st_size)
        cached = _fast_validators.get(key)
        if cached is not None:
            return cached
        raw = MANIFEST_SCHEMA_PATH.read_bytes()
        schema = json.loads(raw.decode("utf-8"))
        task = schema["$defs"]["task"]
        properties = schema.get("properties")
        required = schema.get("required")
        independent = (
            set(schema) <= {"$schema", "$id", "title", "$defs", "type",
                            "additionalProperties", "required", "properties"}
            and schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
            and schema.get("type") == "object"
            and schema.get("additionalProperties") is False
            and isinstance(properties, dict) and isinstance(required, list)
            and all(isinstance(name, str) and name in properties for name in required)
            and properties.get("tasks") == {
                "type": "object", "additionalProperties": {"$ref": "#/$defs/task"},
            }
        )
        fields = None
        if (isinstance(task, dict)
            and set(task) == {"type", "additionalProperties", "required", "properties"}
            and task["type"] == "object" and task["additionalProperties"] is False
            and isinstance(task["required"], list)
            and isinstance(task["properties"], dict)
            and all(isinstance(name, str) and name in task["properties"]
                    for name in task["required"])):
            # These object constraints depend only on the keys/type, while
            # each property is fully validated by its original subschema.
            # Any conditional, combinator, reference or unknown object-level
            # keyword defeats this proof and retains full task validation.
            fields = task["properties"]
        top_validator = Draft202012Validator(schema)
        entry = (
            hashlib.sha256(raw).hexdigest(), top_validator,
            top_validator.evolve(schema={"$ref": "#/$defs/task"}),
            fields, independent,
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


def _remember(path: Path, text: str, schema_sha256: str) -> None:
    key = str(path)
    _known_valid.pop(key, None)
    _known_valid[key] = (schema_sha256, text)
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
            _remember(manifest_path, text, validators[0])
            return manifest

    validate_manifest(manifest, schema_path=schema_path)
    if validators is not None:
        _remember(manifest_path, text, validators[0])
    return manifest


def _same_json_value(left: Any, right: Any) -> bool:
    """Compare JSON values without Python's bool/int/float equivalence."""
    if left is right:
        return True
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _same_json_value(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_json_value(a, b) for a, b in zip(left, right)
        )
    return left == right


def _validate_changes(
    manifest: Mapping[str, Any], known_text: str,
    validators: tuple[str, Any, Any, Any, bool],
) -> bool:
    """Validate only what differs from a known-valid document.

    Returns ``False`` when the shortcut does not apply or finds an error; the
    caller then runs the full validation. JSON types are compared explicitly
    so equal Python values such as ``1`` and ``True`` cannot hide changes.
    """
    tasks = manifest.get("tasks") if isinstance(manifest, Mapping) else None
    if not validators[4] or not isinstance(tasks, dict):
        return False
    try:
        known = json.loads(known_text)
        known_tasks = known["tasks"]
    except (ValueError, KeyError, TypeError):
        return False
    _, top_validator, task_validator, fields, _ = validators
    top = {key: value for key, value in manifest.items() if key != "tasks"}
    known_top = {key: value for key, value in known.items() if key != "tasks"}
    if not _same_json_value(top, known_top):
        top["tasks"] = {}
        if not top_validator.is_valid(top):
            return False
    for task_id, task in tasks.items():
        old = known_tasks.get(task_id)
        if (fields is not None and isinstance(task, dict) and isinstance(old, dict)
            and task.keys() == old.keys()):
            # The old object already satisfied type, required and forbidden
            # extra-key constraints, and its keys are unchanged. Validate the
            # entire value of every changed field (including nested data),
            # using descend so references retain the original root/resource.
            for name, value in task.items():
                if not _same_json_value(value, old[name]):
                    if next(task_validator.descend(value, fields[name]), None) is not None:
                        return False
        elif not _same_json_value(task, old) and not task_validator.is_valid(task):
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
    validators = _schema_validators() if fast else None
    known = _known_valid.get(str(manifest_path)) if validators is not None else None
    if (known is None or known[0] != validators[0]
        or not _validate_changes(manifest, known[1], validators)):
        validate_manifest(manifest, schema_path=schema_path)

    text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
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
        _remember(manifest_path, text, validators[0])
