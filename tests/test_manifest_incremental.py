from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

import storyteller.manifest as module
from storyteller.manifest import load_manifest, validate_manifest, write_manifest
from storyteller.validation import SchemaValidationError
from tests.test_manifest import manifest


TASK = ("tasks", "D1.items")
VALID_CLAIM = {
    "ticket": "b" * 32, "executor_id": "worker-1", "isolation": "none",
    "lease_expires_at": "2026-09-27T03:15:03Z",
}
CASES = [
    (TASK + ("state",), "done"), (TASK + ("state",), "unknown"),
    (TASK + ("state",), True), (TASK + ("cache_key",), "key"),
    (TASK + ("cache_key",), 1), (TASK + ("attempt",), True),
    (TASK + ("attempt",), 1.0), (TASK + ("attempt",), -1),
    (TASK + ("tries",), "1"), (TASK + ("invalidations",), False),
    (TASK + ("continuation_step",), 0.5), (TASK + ("kind",), "llm"),
    (TASK + ("kind",), "wrong"), (TASK + ("type",), ""),
    (TASK + ("deps",), ["D0.done"]), (TASK + ("deps",), [True]),
    (TASK + ("index",), ["a"]), (TASK + ("index",), [""]),
    (TASK + ("history",), [{"count": True}]),
    (TASK + ("history",), [{"count": 1.0}]),
    (TASK + ("history",), [True]), (TASK + ("history",), {}),
    (TASK + ("error",), "error"), (TASK + ("error",), []),
    (TASK + ("executor",), "worker-2"), (TASK + ("executor",), "../bad"),
    (TASK + ("claim",), VALID_CLAIM),
    (TASK + ("claim",), {**VALID_CLAIM, "ticket": "bad"}),
    (TASK + ("claim",), {**VALID_CLAIM, "executor_id": True}),
    (TASK + ("claim",), {**VALID_CLAIM, "lease_expires_at": "yesterday"}),
    (TASK + ("claim",), {**VALID_CLAIM, "extra": True}),
    (TASK + ("claim",), {"ticket": "b" * 32}),
    (TASK + ("unknown",), True), (TASK, None),
    (("tasks", "D2.new"), {"type": "D2.new"}),
    (("status",), "bogus"), (("seed",), True),
    (("input_ratio",), True), (("input_ratio",), 1),
    (("warnings",), [True]), (("unknown",), True), (("tasks",), []),
]


def baseline() -> dict:
    result = manifest()
    result["seed"] = 1
    result["input_ratio"] = 1.0
    task = result["tasks"]["D1.items"]
    task.update(attempt=1, invalidations=0, executor="worker-1", history=[{"count": 1}])
    return result


def set_value(document, path, value):
    node = document
    for name in path[:-1]:
        node = node[name]
    node[path[-1]] = deepcopy(value)


def assert_same_validation(path: Path, candidate: dict, schema_path: Path | None = None) -> None:
    kwargs = {} if schema_path is None else {"schema_path": schema_path}
    before = path.read_bytes()
    stamp = path.with_name("manifest.validated").read_bytes()
    try:
        validate_manifest(candidate, **kwargs)
    except SchemaValidationError as full_error:
        with pytest.raises(SchemaValidationError) as incremental_error:
            write_manifest(path, candidate, **kwargs)
        # The public error keeps the full validator's path and message.
        assert str(incremental_error.value) == str(full_error)
        assert path.read_bytes() == before
        assert path.with_name("manifest.validated").read_bytes() == stamp
    else:
        write_manifest(path, candidate, **kwargs)
        assert load_manifest(path, **kwargs) == candidate


@pytest.mark.parametrize("field_path,value", CASES)
def test_incremental_and_full_validation_agree_on_field_and_type_boundaries(
    tmp_path: Path, field_path: tuple[str, ...], value,
) -> None:
    path = tmp_path / "manifest.json"
    original = baseline()
    write_manifest(path, original)
    candidate = deepcopy(original)  # Mutation outside the public load API.
    set_value(candidate, field_path, value)
    assert_same_validation(path, candidate)


@pytest.mark.parametrize("field", ["state", "executor"])
def test_required_and_optional_key_removal_keeps_full_validation(
    tmp_path: Path, field: str,
) -> None:
    path = tmp_path / "manifest.json"
    original = baseline()
    write_manifest(path, original)
    candidate = deepcopy(original)
    del candidate["tasks"]["D1.items"][field]
    assert_same_validation(path, candidate)


@pytest.mark.parametrize("constraint", ["allOf", "conditional", "dependentSchemas", "$ref", "unknown", "additionalProperties"])
def test_object_level_constraints_disable_field_shortcut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, constraint: str,
) -> None:
    schema = json.loads(module.MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    task_schema = schema["$defs"]["task"]
    if constraint == "allOf":
        task_schema["allOf"] = [{"properties": {"attempt": {"maximum": 1}}}]
    elif constraint == "conditional":
        task_schema.update({"if": {"properties": {"state": {"const": "done"}}},
                            "then": {"properties": {"claim": {"type": "object"}}}})
    elif constraint == "dependentSchemas":
        task_schema["dependentSchemas"] = {"executor": {"properties": {"attempt": {"maximum": 1}}}}
    elif constraint == "$ref":
        schema["$defs"]["referenced-task"] = task_schema
        schema["$defs"]["task"] = {"$ref": "#/$defs/referenced-task"}
    elif constraint == "unknown":
        task_schema["future-keyword"] = {"rule": "unknown"}
    else:
        task_schema["additionalProperties"] = True
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    monkeypatch.setattr(module, "MANIFEST_SCHEMA_PATH", schema_path)
    path = tmp_path / "manifest.json"
    original = baseline()
    write_manifest(path, original, schema_path=schema_path)
    assert module._schema_validators()[3] is None
    for field, value in [("attempt", 2), ("state", "done"), ("claim", {"ticket": "bad"})]:
        write_manifest(path, original, schema_path=schema_path)
        candidate = deepcopy(original)
        candidate["tasks"]["D1.items"][field] = value
        assert_same_validation(path, candidate, schema_path)


def test_field_reference_and_nested_combinators_use_the_original_schema_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = json.loads(module.MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    schema["$defs"]["log"] = {
        "type": "array", "items": {"allOf": [
            {"type": "object", "required": ["count"]},
            {"properties": {"count": {"type": "integer", "minimum": 1}}},
        ]},
    }
    schema["$defs"]["task"]["properties"]["history"] = {"$ref": "#/$defs/log"}
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    monkeypatch.setattr(module, "MANIFEST_SCHEMA_PATH", schema_path)
    path = tmp_path / "manifest.json"
    original = baseline()
    for history in [[{"count": 2}], [{"count": True}], [{"count": 0}], [{}], [1]]:
        write_manifest(path, original, schema_path=schema_path)
        candidate = deepcopy(original)
        candidate["tasks"]["D1.items"]["history"] = history
        assert_same_validation(path, candidate, schema_path)


def test_known_valid_schema_identity_expires_before_an_unchanged_task_is_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = json.loads(module.MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    monkeypatch.setattr(module, "MANIFEST_SCHEMA_PATH", schema_path)
    path = tmp_path / "manifest.json"
    original = baseline()
    write_manifest(path, original, schema_path=schema_path)
    schema["$defs"]["task"]["properties"]["attempt"]["minimum"] = 2
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    candidate = deepcopy(original)
    candidate["updated_at"] = "2026-09-27T03:15:01Z"
    assert_same_validation(path, candidate, schema_path)


@pytest.mark.parametrize("container_constraint", ["minProperties", "maxProperties", "propertyNames", "patternProperties", "root-condition", "root-allOf", "root-unknown"])
def test_manifest_and_tasks_container_constraints_disable_incremental_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, container_constraint: str,
) -> None:
    schema = json.loads(module.MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    tasks_schema = schema["properties"]["tasks"]
    if container_constraint == "minProperties":
        tasks_schema["minProperties"] = 1
    elif container_constraint == "maxProperties":
        tasks_schema["maxProperties"] = 1
    elif container_constraint == "propertyNames":
        tasks_schema["propertyNames"] = {"pattern": "^D1\\."}
    elif container_constraint == "patternProperties":
        tasks_schema["patternProperties"] = {"^D2\\.": {"properties": {"attempt": {"maximum": 0}}}}
    elif container_constraint == "root-condition":
        schema.update({"if": {"properties": {"status": {"const": "completed"}}},
                       "then": {"properties": {"tasks": {"maxProperties": 0}}}})
    elif container_constraint == "root-allOf":
        schema["allOf"] = [{"properties": {"tasks": {"minProperties": 1}}}]
    else:
        schema["future-keyword"] = True
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    monkeypatch.setattr(module, "MANIFEST_SCHEMA_PATH", schema_path)
    path = tmp_path / "manifest.json"
    original = baseline()
    write_manifest(path, original, schema_path=schema_path)
    assert not module._schema_validators()[4]
    candidates = []
    deleted = deepcopy(original)
    deleted["tasks"] = {}
    candidates.append(deleted)
    added = deepcopy(original)
    added["tasks"]["D2.new"] = deepcopy(original["tasks"]["D1.items"])
    candidates.append(added)
    completed = deepcopy(original)
    completed["status"] = "completed"
    candidates.append(completed)
    for candidate in candidates:
        write_manifest(path, original, schema_path=schema_path)
        assert_same_validation(path, candidate, schema_path)


def test_field_reference_to_manifest_property_keeps_original_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = json.loads(module.MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    schema["$defs"]["task"]["properties"]["attempt"] = {"$ref": "#/properties/seed"}
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    monkeypatch.setattr(module, "MANIFEST_SCHEMA_PATH", schema_path)
    path = tmp_path / "manifest.json"
    original = baseline()
    for attempt in [2, 1.0, True, "1"]:
        write_manifest(path, original, schema_path=schema_path)
        candidate = deepcopy(original)
        candidate["tasks"]["D1.items"]["attempt"] = attempt
        assert_same_validation(path, candidate, schema_path)
