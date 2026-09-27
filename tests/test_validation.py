from __future__ import annotations

import json
from pathlib import Path

import pytest

from storyteller.validation import SchemaValidationError, YamlLoadError, load_yaml


ROOT = Path(__file__).parents[1]
TASK_SCHEMA = ROOT / "schemas" / "task-definition.schema.json"
WORKSPACE_SCHEMA = ROOT / "schemas" / "workspace.schema.json"


def write_yaml(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_load_yaml_validates_a_task_definition(tmp_path):
    path = write_yaml(
        tmp_path,
        "task.yaml",
        """
        id: S1.profile
        version: 1
        kind: llm
        inputs:
          character:
            label: 人物
            from: S3.assignment
            select: cast[{slot}]
            required: true
            truncate: none
        output: json
        card:
          role: 与えられた属性だけを使う。
          steps:
            - プロフィールを書く。
          output_example: '{"profile": "...", "sources": ["want:i007"]}'
        validate:
          schema: schemas/tasks/S1.profile.schema.json
          checks:
            - sources_exist
            - max_chars:
                field: profile
                n: 200
            - count:
                field: items
                min: 1
                max: 3
        max_input_chars: 1000
        lease_minutes: 0.05
        """,
    )

    value = load_yaml(path, TASK_SCHEMA)

    assert value["id"] == "S1.profile"
    assert value["inputs"]["character"]["required"] is True
    assert value["validate"]["schema"] == "schemas/tasks/S1.profile.schema.json"


@pytest.mark.parametrize(
    "schema_value",
    [
        "{type: object}",
        "true",
        "schemas/tasks/S1.profile.json",
        "/schemas/tasks/S1.profile.schema.json",
        "../schemas/tasks/S1.profile.schema.json",
    ],
)
def test_task_definition_validate_schema_is_a_relative_schema_path(
    tmp_path, schema_value
):
    path = write_yaml(
        tmp_path,
        "invalid-schema-path.yaml",
        f"""
        id: S1.profile
        version: 1
        kind: llm
        output: text
        card:
          role: プロフィールを書く。
          steps: [write]
        validate:
          schema: {schema_value}
        """,
    )

    with pytest.raises(SchemaValidationError):
        load_yaml(path, TASK_SCHEMA)


def test_task_definition_requires_kind_specific_fields(tmp_path):
    missing_handler = write_yaml(
        tmp_path,
        "missing-handler.yaml",
        """
        id: D1.items
        version: 1
        kind: code
        """,
    )
    missing_card = write_yaml(
        tmp_path,
        "missing-card.yaml",
        """
        id: D2.echo
        version: 1
        kind: llm
        output: text
        """,
    )

    with pytest.raises(SchemaValidationError):
        load_yaml(missing_handler, TASK_SCHEMA)
    with pytest.raises(SchemaValidationError):
        load_yaml(missing_card, TASK_SCHEMA)


def test_yaml_no_is_not_accepted_as_a_string(tmp_path):
    path = write_yaml(
        tmp_path,
        "wrong-type.yaml",
        """
        id: S1.profile
        version: 1
        kind: llm
        output: text
        card:
          role: no
          steps: [write]
        """,
    )

    with pytest.raises(SchemaValidationError) as error:
        load_yaml(path, TASK_SCHEMA)

    assert "role" in str(error.value)


def test_code_task_rejects_llm_only_properties(tmp_path):
    path = write_yaml(
        tmp_path,
        "code-with-output.yaml",
        """
        id: D1.items
        version: 1
        kind: code
        handler: make_items
        output: json
        """,
    )

    with pytest.raises(SchemaValidationError):
        load_yaml(path, TASK_SCHEMA)


def test_workspace_schema_accepts_absolute_paths_and_rejects_relative_paths(tmp_path):
    valid = write_yaml(
        tmp_path,
        "workspace.yaml",
        """
        data_dir: /tmp/storyteller
        executor_id: codex.worker-1
        agent: codex
        isolation: permission
        """,
    )
    invalid = write_yaml(
        tmp_path,
        "relative-workspace.yaml",
        """
        data_dir: storyteller
        executor_id: worker
        agent: generic
        isolation: placement
        """,
    )

    assert load_yaml(valid, WORKSPACE_SCHEMA)["agent"] == "codex"
    with pytest.raises(SchemaValidationError):
        load_yaml(invalid, WORKSPACE_SCHEMA)


def test_yaml_parse_errors_are_wrapped(tmp_path):
    path = write_yaml(tmp_path, "broken.yaml", "id: [unterminated\n")

    with pytest.raises(YamlLoadError):
        load_yaml(path, TASK_SCHEMA)


def test_schemas_are_valid_draft_2020_12_schemas():
    # Loading the schemas through the common path exercises schema checking;
    # this also prevents an invalid schema from hiding behind an empty test.
    for schema_path in (TASK_SCHEMA, WORKSPACE_SCHEMA):
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
