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
        element: labeled
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
          output_example: '{"profile": "..."}'
        validate:
          schema: schemas/tasks/S1.profile.schema.json
          checks:
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


def test_task_definition_output_example_allows_placeholders_and_enums(
    tmp_path,
):
    path = write_yaml(
        tmp_path,
        "task.yaml",
        """
        id: S1.extract
        version: 1
        kind: llm
        element: labeled
        output: json
        card:
          role: 素材を抽出する。
          steps: [抽出する。]
          output_example: '{"materials": [{"text": "...", "kind": "conflict"}]}'
        validate:
          schema: schemas/tasks/S1.extract.schema.json
        """,
    )

    value = load_yaml(path, TASK_SCHEMA)

    assert value["card"]["output_example"]


@pytest.mark.parametrize(
    "output_example",
    [
        '{"materials": [{"text": "具体的な文例", "kind": "conflict"}]}',
        '{"materials": [{"text": "...", "kind": "具体的な種類"}]}',
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
        element: labeled
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
        element: labeled
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
        element: labeled
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
        element: labeled
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


def test_task_definition_schema_defaults_max_attempts_to_five():
    schema = json.loads(TASK_SCHEMA.read_text(encoding="utf-8"))
    assert schema["properties"]["max_attempts"]["default"] == 5


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
    task = {"output": output, "validate": validation}
    return task


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


@pytest.mark.parametrize(
    ("check", "good", "bad"),
    [
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


def test_ends_complete_fix_append_completes_the_sentence_and_warns() -> None:
    task = output_task(
        output="json",
        checks=[{"ends_complete": {"field": "intro", "fix": "append"}}],
    )

    result = validate_output(task, '{"intro": "文の途中"}')

    assert result.passed
    assert result.value == {"intro": "文の途中。"}
    assert result.warnings == ("文末を補完",)


@pytest.mark.parametrize("ending", list("。．.！!？?」』）)】…"))
@pytest.mark.parametrize("output_kind", ["json", "text"])
def test_max_chars_trim_uses_the_last_ending_within_the_limit(ending, output_kind):
    arguments = {"n": 6, "fix": "trim"}
    text = f"あ。いう{ending}えお。"
    if output_kind == "json":
        arguments["field"] = "nested.body"
        raw = json.dumps({"nested": {"body": text}, "other": "そのまま"}, ensure_ascii=False)
        expected = {"nested": {"body": f"あ。いう{ending}"}, "other": "そのまま"}
    else:
        raw = text
        expected = f"あ。いう{ending}"
    task = output_task(output=output_kind, checks=[{"max_chars": arguments}])

    result = validate_output(task, raw)

    assert result.passed, result.errors
    assert result.value == expected
    assert result.warnings == ("字数の上限で切り詰め",)


def test_max_chars_trim_counts_nfc_characters_and_excludes_whitespace():
    task = output_task(output="text", checks=[{"max_chars": {"n": 4, "fix": "trim"}}])

    result = validate_output(task, "か\u3099 \t。\nあ　！続き。")

    assert result.passed
    assert result.value == "が \t。\nあ　！"
    assert result.warnings == ("字数の上限で切り詰め",)


@pytest.mark.parametrize("text", ["あ。い！", "か\u3099\t。\nあ　！"])
def test_max_chars_trim_does_nothing_at_or_below_the_limit(text):
    task = output_task(output="text", checks=[{"max_chars": {"n": 4, "fix": "trim"}}])

    result = validate_output(task, text)

    assert result.passed
    assert result.value == text
    assert result.warnings == ()


@pytest.mark.parametrize("text, limit", [("あいうえ。", 4), ("あいうえ", 4 - 1), ("。", 0)])
def test_max_chars_trim_rejects_without_an_ending_within_the_limit(text, limit):
    task = output_task(output="text", checks=[{"max_chars": {"n": limit, "fix": "trim"}}])

    result = validate_output(task, text)

    assert not result.passed
    assert result.value == text
    assert result.warnings == ()
    assert any("max_chars" in error for error in result.errors)


def test_other_checks_inspect_the_trimmed_output_regardless_of_check_order(tmp_path):
    (tmp_path / "denied.yaml").write_text("phrases: [禁止]\n", encoding="utf-8")
    task = output_task(output="text", checks=[
        {"avoid_listed": {"table": "denied.yaml"}},
        "ends_complete",
        {"max_chars": {"n": 4, "fix": "trim"}},
    ])

    result = validate_output(task, "本文。禁止", harness_root=tmp_path)

    assert result.passed
    assert result.value == "本文。"


def test_min_chars_is_checked_after_trimming():
    task = output_task(output="text", checks=[
        {"min_chars": {"n": 4}},
        {"max_chars": {"n": 5, "fix": "trim"}},
    ])

    result = validate_output(task, "あ。いうえお。")

    assert not result.passed
    assert result.value == "あ。"
    assert any("min_chars" in error for error in result.errors)


def test_task_definition_schema_accepts_trim_only_for_max_chars():
    definition = load_yaml(ROOT / "harness/story/tasks/S7.detail.yaml", TASK_SCHEMA)
    validate_document(definition, TASK_SCHEMA)
    definition["validate"]["checks"] = [{"min_chars": {"n": 1, "fix": "trim"}}]
    with pytest.raises(SchemaValidationError):
        validate_document(definition, TASK_SCHEMA)
    definition["validate"]["checks"] = [{"max_chars": {"n": 1, "fix": "append"}}]
    with pytest.raises(SchemaValidationError):
        validate_document(definition, TASK_SCHEMA)


def test_task_output_schemas_have_no_string_length_constraints():
    def check_keys(value):
        if isinstance(value, dict):
            assert not {"minLength", "maxLength"}.intersection(value)
            for child in value.values():
                check_keys(child)
        elif isinstance(value, list):
            for child in value:
                check_keys(child)

    paths = sorted((ROOT / "schemas/tasks").glob("*.schema.json"))
    assert paths
    for path in paths:
        check_keys(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("field, maximum", [
    ("intro", 50), ("motive", 120),
])
def test_short_s5_items_still_reject_excess_length(field, maximum):
    definition = load_yaml(ROOT / f"harness/story/tasks/S5.{field}.yaml", TASK_SCHEMA)
    result = validate_output(
        definition,
        json.dumps({field: "人。" * (maximum // 2 + 1)}, ensure_ascii=False),
        inputs={"character": {"id": "c1"}},
    )

    assert not result.passed
    assert any("max_chars" in error for error in result.errors)
    assert not any("schema:" in error for error in result.errors)
    assert "字数の上限で切り詰め" not in result.warnings


@pytest.mark.parametrize("length, passed", [(9, False), (10, True), (40, True), (41, False)])
def test_s2_counterpart_keeps_its_former_schema_char_range(length, passed):
    definition = load_yaml(ROOT / "harness/story/tasks/S2.expand.yaml", TASK_SCHEMA)
    result = validate_output(
        definition,
        json.dumps({"items": ["素" * 10] * 5, "counterpart": "対" * length}, ensure_ascii=False),
        inputs={"material": {"id": "m001"}},
    )

    assert result.passed is passed
    assert not any("schema:" in error for error in result.errors)


@pytest.mark.parametrize("task_id, field, minimum, maximum", [
    ("S1.extract", "materials", 10, 30),
    ("S2.expand", "items", 10, 40),
])
@pytest.mark.parametrize("position", range(5))
@pytest.mark.parametrize("boundary", ["minimum", "maximum"])
def test_array_item_char_ranges_are_enforced_by_checks(task_id, field, minimum, maximum, position, boundary):
    definition = load_yaml(ROOT / f"harness/story/tasks/{task_id}.yaml", TASK_SCHEMA)
    texts = ["素" * minimum] * 5
    source_id = "p001" if task_id == "S1.extract" else "m001"

    def submit():
        values = [{"text": text, "kind": "theme"} for text in texts] if field == "materials" else texts
        output = {field: values}
        if task_id == "S2.expand":
            output["counterpart"] = "対" * minimum
        return validate_output(definition, json.dumps(output, ensure_ascii=False), inputs={"source": {"id": source_id}})

    texts[position] = "素" * (minimum if boundary == "minimum" else maximum)
    assert submit().passed
    texts[position] = "素" * (minimum - 1 if boundary == "minimum" else maximum + 1)
    rejected = submit()
    assert not rejected.passed
    assert any(("min_chars" if boundary == "minimum" else "max_chars") in error for error in rejected.errors)
    assert not any("schema:" in error for error in rejected.errors)


def test_ends_complete_fix_append_does_nothing_when_already_complete() -> None:
    task = output_task(
        output="json",
        checks=[{"ends_complete": {"field": "intro", "fix": "append"}}],
    )

    result = validate_output(task, '{"intro": "文は完結。"}')

    assert result.passed
    assert result.value == {"intro": "文は完結。"}
    assert result.warnings == ()


def test_ends_complete_without_fix_still_rejects_an_incomplete_sentence() -> None:
    task = output_task(
        output="json",
        checks=[{"ends_complete": {"field": "intro"}}],
    )

    result = validate_output(task, '{"intro": "文の途中"}')

    assert not result.passed
    assert result.value == {"intro": "文の途中"}


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
