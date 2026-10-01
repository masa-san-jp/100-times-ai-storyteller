from __future__ import annotations

import json
from pathlib import Path

import pytest

import storyteller.validation as validation_module
from storyteller.validation import (
    OutputParseError,
    SchemaValidationError,
    TaskDefinitionValidationError,
    YamlLoadError,
    load_yaml,
    parse_json_object,
    validate_document,
    validate_output,
)


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


def test_task_definition_output_example_allows_placeholders_enums_and_source_ids(
    tmp_path,
):
    path = write_yaml(
        tmp_path,
        "task.yaml",
        """
        id: S1.extract
        version: 1
        kind: llm
        output: json
        card:
          role: 素材を抽出する。
          steps: [抽出する。]
          output_example: '{"materials": [{"text": "...", "kind": "conflict"}], "sources": ["p001"]}'
        validate:
          schema: schemas/tasks/S1.extract.schema.json
        """,
    )

    value = load_yaml(path, TASK_SCHEMA)

    assert value["card"]["output_example"]


@pytest.mark.parametrize(
    "output_example",
    [
        '{"materials": [{"text": "具体的な文例", "kind": "conflict"}], "sources": ["p001"]}',
        '{"materials": [{"text": "...", "kind": "具体的な種類"}], "sources": ["p001"]}',
    ],
)
def test_task_definition_rejects_concrete_output_example_strings(
    tmp_path, output_example
):
    path = write_yaml(
        tmp_path,
        "task.yaml",
        f"""
        id: S1.extract
        version: 1
        kind: llm
        output: json
        card:
          role: 素材を抽出する。
          steps: [抽出する。]
          output_example: '{output_example}'
        validate:
          schema: schemas/tasks/S1.extract.schema.json
        """,
    )

    with pytest.raises(TaskDefinitionValidationError, match="output_example"):
        load_yaml(path, TASK_SCHEMA)


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


def output_task(output="json", checks=None, schema=None):
    validation = {}
    if checks is not None:
        validation["checks"] = checks
    if schema is not None:
        validation["schema"] = schema
    return {"output": output, "validate": validation}


def test_json_output_is_rescued_from_surrounding_text_and_ignores_braces_in_strings():
    raw = '説明 {"text":"閉じた } 文字列", "nested":{"ok":true}} 末尾'

    assert parse_json_object(raw) == {
        "text": "閉じた } 文字列",
        "nested": {"ok": True},
    }


def test_json_arrays_and_unparseable_json_are_rejected():
    with pytest.raises(OutputParseError):
        parse_json_object("[1, 2]")
    with pytest.raises(OutputParseError):
        parse_json_object("説明だけ")


def test_json_schema_is_applied_after_json_rescue(tmp_path: Path):
    schema = tmp_path / "output.schema.json"
    schema.write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "required": ["text"],
                "properties": {"text": {"type": "string"}},
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )
    task = output_task(schema="output.schema.json")

    assert validate_output(task, 'prefix {"text":"ok"}', harness_root=tmp_path).passed
    result = validate_output(task, '{"text": 3}', harness_root=tmp_path)
    assert not result.passed
    assert any("schema" in error for error in result.errors)


def test_character_count_excludes_ascii_fullwidth_and_line_whitespace() -> None:
    task = output_task(
        output="json",
        checks=[{"max_chars": {"field": "text", "n": 3}}],
    )

    result = validate_output(
        task,
        json.dumps({"text": " あ　い\t\nう "}, ensure_ascii=False),
    )

    assert result.passed


def test_submitted_string_values_are_stripped_before_validation_and_storage() -> None:
    task = output_task(output="json")

    result = validate_output(
        task,
        json.dumps(
            {"text": "　本文　", "items": ["\n項目\t"]},
            ensure_ascii=False,
        ),
    )

    assert result.passed
    assert result.value == {"text": "本文", "items": ["項目"]}


def test_sources_exist_removes_unknown_ids_and_keeps_valid_ids() -> None:
    task = output_task(checks=["sources_exist"])
    inputs = {
        "character": {
            "id": "c1",
            "role_definition": {"id": "adversary", "definition": "定義"},
            "plot_context": {"id": "romance", "character_requirements": "要件"},
            "name_sound": {"set_id": "sound-05"},
        },
        "role_definition": {"id": "adversary", "definition": "定義"},
        "plot_requirements": {"id": "romance", "character_requirements": "要件"},
    }

    result = validate_output(
        task,
        json.dumps(
            {
                "sources": [
                    "c1",
                    "role_definition:adversary",
                    "plot_context:romance",
                    "name_sound:set_id sound-05",
                ]
            },
            ensure_ascii=False,
        ),
        inputs=inputs,
    )

    assert result.passed
    assert result.value == {"sources": ["c1"]}
    assert result.warnings == (
        "未知の出典 ID を除去: "
        "['role_definition:adversary', 'plot_context:romance', "
        "'name_sound:set_id sound-05']",
    )


def test_sources_exist_rejects_when_all_ids_are_unknown() -> None:
    result = validate_output(
        output_task(checks=["sources_exist"]),
        '{"sources": ["missing"]}',
        inputs={"character": {"id": "c1"}},
    )

    assert not result.passed
    assert result.value == {"sources": []}
    assert any("有効な出典 ID がありません" in error for error in result.errors)


@pytest.mark.parametrize(
    ("check", "good", "bad"),
    [
        (
            "sources_exist",
            {"sources": ["a1"]},
            {"sources": ["unknown"]},
        ),
        (
            {"max_chars": {"field": "text", "n": 3}},
            {"text": "あいう"},
            {"text": "あいうえ"},
        ),
        (
            {"min_chars": {"field": "text", "n": 3}},
            {"text": "あいう"},
            {"text": "あい"},
        ),
        (
            {"count": {"field": "items", "n": 2}},
            {"items": [1, 2]},
            {"items": [1]},
        ),
        (
            {"count": {"field": "items", "min": 1, "max": 2}},
            {"items": [1]},
            {"items": [1, 2, 3]},
        ),
        (
            {"ids_subset": {"field": "ids", "slot": "items"}},
            {"ids": ["a1", "a2"]},
            {"ids": ["a3"]},
        ),
        (
            {"uses_given": {"field": "text", "slot": "items", "n": 2}},
            {"text": "alpha と beta を使う"},
            {"text": "alpha だけを使う"},
        ),
        (
            "ends_complete",
            "これは完結。",
            "これは途中",
        ),
    ],
)
def test_each_output_check_accepts_and_rejects_examples(check, good, bad):
    inputs = {"items": [{"id": "a1", "text": "alpha"}, {"id": "a2", "text": "beta"}]}
    output = "text" if isinstance(good, str) else "json"
    task = output_task(output=output, checks=[check])

    good_raw = good if output == "text" else json.dumps(good, ensure_ascii=False)
    bad_raw = bad if output == "text" else json.dumps(bad, ensure_ascii=False)
    assert validate_output(task, good_raw, inputs=inputs).passed
    assert not validate_output(task, bad_raw, inputs=inputs).passed


def test_no_new_proper_nouns_supports_warn_and_fail(tmp_path: Path):
    common_words = tmp_path / "common_words.yaml"
    common_words.write_text("- 一般語\n", encoding="utf-8")
    inputs = {"given": ["既知の素材"]}
    output = {"text": "未知のカタカナ語と一般語と「新名称」"}

    warn = validate_output(
        output_task(
            checks=[
                {"no_new_proper_nouns": {"field": "text", "mode": "warn"}}
            ]
        ),
        json.dumps(output, ensure_ascii=False),
        inputs=inputs,
        common_words_path=common_words,
    )
    assert warn.passed
    assert warn.warnings
    assert "新名称" in warn.warnings[0]

    fail = validate_output(
        output_task(
            checks=[
                {"no_new_proper_nouns": {"field": "text", "mode": "fail"}}
            ]
        ),
        json.dumps(output, ensure_ascii=False),
        inputs=inputs,
        common_words_path=common_words,
    )
    assert not fail.passed
    assert "新名称" in fail.errors[0]


def test_avoid_listed_checks_selected_fields_after_nfkc_normalization(tmp_path: Path):
    deny_list = tmp_path / "cliches.yaml"
    deny_list.write_text("phrases: [運命, hero]\n", encoding="utf-8")
    task = output_task(
        checks=[
            {
                "avoid_listed": {
                    "fields": ["items"],
                    "table": "cliches.yaml",
                }
            }
        ]
    )

    accepted = validate_output(
        task,
        json.dumps({"items": ["定義に沿った要素"]}, ensure_ascii=False),
        harness_root=tmp_path,
    )
    rejected = validate_output(
        task,
        json.dumps({"items": ["ｈｅｒｏのような表現"]}, ensure_ascii=False),
        harness_root=tmp_path,
    )

    assert accepted.passed
    assert not rejected.passed
    assert "hero" in rejected.errors[0]


def test_avoid_listed_real_cliches_table_has_no_short_substring_false_positives():
    task = output_task(
        checks=[
            {
                "avoid_listed": {
                    "fields": ["items"],
                    "table": "tables/cliches.yaml",
                }
            }
        ]
    )

    accepted = validate_output(
        task,
        json.dumps(
            {"items": ["祭りの夜に灯火を掲げて歩く", "囲炉裏の炭火で暖を取る"]},
            ensure_ascii=False,
        ),
        harness_root=ROOT,
    )

    assert accepted.passed


def test_no_new_proper_nouns_excludes_input_and_allowed_words(tmp_path: Path):
    common_words = tmp_path / "common_words.yaml"
    common_words.write_text("words:\n  - 東京\n", encoding="utf-8")
    task = output_task(
        output="text",
        checks=[{"no_new_proper_nouns": {"mode": "fail"}}],
    )
    result = validate_output(
        task,
        "東京と既知の素材。",
        inputs={"source": "既知の素材"},
        common_words_path=common_words,
    )

    assert result.passed


def test_validate_document_caches_file_schema_and_validator(tmp_path, monkeypatch):
    schema_path = tmp_path / "output.schema.json"
    schema_path.write_text(
        json.dumps({"type": "string"}),
        encoding="utf-8",
    )
    load_calls = 0
    validator_calls = 0
    original_load = validation_module.json.load
    original_validator = validation_module.Draft202012Validator

    def counting_load(stream):
        nonlocal load_calls
        load_calls += 1
        return original_load(stream)

    def counting_validator(*args, **kwargs):
        nonlocal validator_calls
        validator_calls += 1
        return original_validator(*args, **kwargs)

    monkeypatch.setattr(validation_module.json, "load", counting_load)
    monkeypatch.setattr(validation_module, "Draft202012Validator", counting_validator)

    assert validate_document("first", schema_path) == "first"
    assert validate_document("second", schema_path) == "second"

    assert load_calls == 1
    assert validator_calls == 1


def test_validate_document_reloads_file_schema_when_it_changes(tmp_path, monkeypatch):
    schema_path = tmp_path / "output.schema.json"
    schema_path.write_text(
        json.dumps({"type": "string"}),
        encoding="utf-8",
    )
    load_calls = 0
    original_load = validation_module.json.load

    def counting_load(stream):
        nonlocal load_calls
        load_calls += 1
        return original_load(stream)

    monkeypatch.setattr(validation_module.json, "load", counting_load)

    assert validate_document("value", schema_path) == "value"
    schema_path.write_text(
        json.dumps({"type": "integer"}),
        encoding="utf-8",
    )

    with pytest.raises(SchemaValidationError):
        validate_document("value", schema_path)

    assert load_calls == 2
