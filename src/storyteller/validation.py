"""Load human-authored YAML and validate it against a JSON Schema.

YAML is deliberately decoded with :func:`yaml.safe_load` and validated
immediately.  In particular, YAML 1.1 values such as ``no`` may be decoded
as booleans; the schema is what catches that type error instead of silently
accepting it as a string.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError


class YamlValidationError(ValueError):
    """Base error for reading or validating a YAML document."""


class YamlLoadError(YamlValidationError):
    """The YAML file could not be read or parsed."""


class SchemaValidationError(YamlValidationError):
    """The decoded YAML document does not satisfy its JSON Schema."""

    def __init__(self, path: Path, errors: list[ValidationError]) -> None:
        self.path = path
        self.errors = errors
        details = "; ".join(_format_validation_error(error) for error in errors)
        super().__init__(f"{path}: {details}")


class TaskDefinitionValidationError(YamlValidationError):
    """A task definition violates the output-example placeholder rule."""


class OutputValidationError(ValueError):
    """Base error for output validation configuration or parsing."""


class OutputParseError(OutputValidationError):
    """The submitted output could not be parsed in the required format."""


class ValidationConfigurationError(OutputValidationError):
    """A task's validation configuration cannot be evaluated."""


@dataclass(frozen=True)
class ValidationResult:
    """The result of validating one submitted task output.

    ``value`` is the parsed JSON object for JSON tasks, or the original text
    for text tasks.  Output failures are reported in ``errors`` so the caller
    can persist the reason and decide how to retry; this module does not
    change task or run state.
    """

    value: Any
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.errors

    @property
    def valid(self) -> bool:
        """Alias for :attr:`passed` for callers using validation terminology."""
        return self.passed


_PROPER_NOUN_PATTERNS = (
    re.compile(r"[\u30a0-\u30ffー・]{3,}"),
    re.compile(r"「[^「」]*」|『[^『』]*』"),
    re.compile(r"(?<![A-Za-z])[A-Z][A-Za-z]{1,}(?![A-Za-z])"),
    re.compile(
        r"[\u3400-\u4dbf\u4e00-\u9fff]+"
        r"(?:王国|帝国|教団|国|町|村|市|島|山|川|会|社|家|様|氏)"
    ),
)
_COMPLETE_ENDINGS = frozenset("。．.！!？?」』）)】…")
_DEFAULT_HARNESS_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_CACHE: dict[
    tuple[Path, int, int], tuple[Any, Draft202012Validator]
] = {}


def text_ends_complete(value: str) -> bool:
    """Return whether text ends with one of the spec's completion marks."""
    if not isinstance(value, str):
        return False
    stripped = value.rstrip()
    return bool(stripped) and stripped[-1] in _COMPLETE_ENDINGS


def text_is_truncated(value: str) -> bool:
    """Return whether a long text output is incomplete by the task spec."""
    return (
        isinstance(value, str)
        and _char_length(value) >= 400
        and not text_ends_complete(value)
    )


def validate_output(
    task_definition: Mapping[str, Any],
    raw_output: Any,
    inputs: Mapping[str, Any] | None = None,
    *,
    harness_root: str | Path | None = None,
    common_words_path: str | Path | None = None,
) -> ValidationResult:
    """Validate one LLM output according to ``task_definition``.

    The input mapping contains the values rendered in the task card, keyed by
    input-slot name.  It is used by source and input-dependent checks.  The
    function only parses and validates data; retry counters, attempts, and
    task state are deliberately outside its scope.
    """

    output_kind = task_definition.get("output")
    if output_kind not in {"json", "text"}:
        raise ValidationConfigurationError("task definition output must be json or text")

    try:
        value = _strip_output_strings(parse_output(raw_output, output_kind))
    except OutputParseError as error:
        return ValidationResult(None, errors=(str(error),))

    slot_values = dict(inputs or {})
    validation = task_definition.get("validate") or {}
    if not isinstance(validation, Mapping):
        raise ValidationConfigurationError("task definition validate must be a mapping")

    errors: list[str] = []
    warnings: list[str] = []
    schema_ref = validation.get("schema")
    if schema_ref is not None:
        if not isinstance(schema_ref, str) or not schema_ref:
            raise ValidationConfigurationError("validate.schema must be a non-empty string")
        schema_root = Path(harness_root) if harness_root is not None else _DEFAULT_HARNESS_ROOT
        schema_path = schema_root / schema_ref
        try:
            validate_document(value, schema_path)
        except (YamlValidationError, OSError) as error:
            errors.append(f"schema: {error}")

    checks = validation.get("checks", [])
    if not isinstance(checks, list):
        raise ValidationConfigurationError("validate.checks must be a list")
    for check in checks:
        try:
            name, arguments = _normalise_check(check)
            check_errors, check_warnings = _run_check(
                name,
                arguments,
                value,
                output_kind=output_kind,
                inputs=slot_values,
                harness_root=harness_root,
                common_words_path=common_words_path,
            )
        except ValidationConfigurationError:
            raise
        errors.extend(check_errors)
        warnings.extend(check_warnings)

    return ValidationResult(value, tuple(errors), tuple(warnings))


# The longer name is useful at orchestration call sites and keeps the public
# entry point discoverable without duplicating the implementation.
validate_task_output = validate_output


def parse_json_object(raw_output: Any) -> dict[str, Any]:
    """Parse a JSON object, rescuing the first balanced object in the text."""

    if not isinstance(raw_output, str):
        raise OutputParseError("JSON出力が文字列ではありません")

    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError:
        parsed = None
    else:
        if isinstance(parsed, dict):
            return parsed
        raise OutputParseError("JSON出力がオブジェクトではありません")

    candidate = _first_json_object(raw_output)
    if candidate is not None:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            parsed = None
        else:
            if isinstance(parsed, dict):
                return parsed
    raise OutputParseError("JSONオブジェクトを解析できません")


def parse_output(raw_output: Any, output_kind: str) -> Any:
    """Parse a submitted output according to ``output: json`` or ``text``."""

    if output_kind == "json":
        return parse_json_object(raw_output)
    if output_kind == "text":
        if isinstance(raw_output, str):
            return raw_output
        raise OutputParseError("出力が文字列ではありません")
    raise ValidationConfigurationError("output must be json or text")


def _first_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for position in range(start, len(text)):
        character = text[position]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[start : position + 1]
            if depth < 0:
                return None
    return None


def _normalise_check(check: Any) -> tuple[str, Mapping[str, Any]]:
    if isinstance(check, str):
        return check, {}
    if isinstance(check, Mapping) and len(check) == 1:
        name, arguments = next(iter(check.items()))
        if not isinstance(name, str):
            raise ValidationConfigurationError("validation check name must be a string")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, Mapping):
            raise ValidationConfigurationError(f"check {name} arguments must be a mapping")
        return name, arguments
    raise ValidationConfigurationError("validation check must be a name or one-key mapping")


def _run_check(
    name: str,
    arguments: Mapping[str, Any],
    output: Any,
    *,
    output_kind: str,
    inputs: Mapping[str, Any],
    harness_root: str | Path | None,
    common_words_path: str | Path | None,
) -> tuple[list[str], list[str]]:
    if name == "sources_exist":
        if not isinstance(output, Mapping) or not isinstance(output.get("sources"), list):
            return ["sources_exist: sources が配列ではありません"], []
        allowed = _ids_from_inputs(inputs)
        unknown = [source for source in output["sources"] if source not in allowed]
        if unknown:
            return [f"sources_exist: 未知のIDがあります: {unknown!r}"], []
        return [], []

    if name in {"max_chars", "min_chars"}:
        field, field_error = _field_argument(arguments)
        if field_error:
            return [f"{name}: {field_error}"], []
        value, missing = _read_field(output, field)
        if missing or not isinstance(value, str):
            return [f"{name}: 対象フィールドが文字列ではありません"], []
        limit = arguments.get("n")
        if not _is_nonnegative_integer(limit):
            raise ValidationConfigurationError(f"{name}.n must be a non-negative integer")
        length = _char_length(value)
        failed = length > limit if name == "max_chars" else length < limit
        if failed:
            operator = "以下" if name == "max_chars" else "以上"
            return [f"{name}: 文字数 {length} が {limit}{operator}ではありません"], []
        return [], []

    if name == "count":
        field, field_error = _required_field_argument(arguments)
        if field_error:
            return [f"count: {field_error}"], []
        value, missing = _read_field(output, field)
        if missing or not isinstance(value, list):
            return ["count: 対象フィールドが配列ではありません"], []
        count = len(value)
        if "n" in arguments:
            expected = arguments["n"]
            if not _is_nonnegative_integer(expected):
                raise ValidationConfigurationError("count.n must be a non-negative integer")
            passed = count == expected
            condition = f"{expected}件"
        else:
            minimum = arguments.get("min")
            maximum = arguments.get("max")
            if minimum is not None and not _is_nonnegative_integer(minimum):
                raise ValidationConfigurationError("count.min must be a non-negative integer")
            if maximum is not None and not _is_nonnegative_integer(maximum):
                raise ValidationConfigurationError("count.max must be a non-negative integer")
            if minimum is None and maximum is None:
                raise ValidationConfigurationError("count requires n, min, or max")
            passed = (minimum is None or count >= minimum) and (maximum is None or count <= maximum)
            condition = f"{minimum if minimum is not None else 0}〜{maximum if maximum is not None else '上限なし'}件"
        if not passed:
            return [f"count: 件数 {count} が {condition}ではありません"], []
        return [], []

    if name == "ids_subset":
        field, field_error = _required_field_argument(arguments)
        slot = arguments.get("slot")
        if field_error or not isinstance(slot, str) or not slot:
            return ["ids_subset: field と slot が必要です"], []
        value, missing = _read_field(output, field)
        if missing or not isinstance(value, list):
            return ["ids_subset: 対象フィールドが配列ではありません"], []
        if slot not in inputs:
            return [f"ids_subset: 入力スロットがありません: {slot}"], []
        allowed = _ids_from_value(inputs[slot])
        actual = [_item_id(item) for item in value]
        unknown = [item for item in actual if item not in allowed]
        if unknown:
            return [f"ids_subset: 入力にないIDがあります: {unknown!r}"], []
        return [], []

    if name == "uses_given":
        field, field_error = _required_field_argument(arguments)
        slot = arguments.get("slot")
        required = arguments.get("n")
        if field_error or not isinstance(slot, str) or not slot:
            return ["uses_given: field と slot が必要です"], []
        if not _is_positive_integer(required):
            raise ValidationConfigurationError("uses_given.n must be a positive integer")
        value, missing = _read_field(output, field)
        if missing or not isinstance(value, str):
            return ["uses_given: 対象フィールドが文字列ではありません"], []
        if slot not in inputs:
            return [f"uses_given: 入力スロットがありません: {slot}"], []
        given = [text for text in _input_texts(inputs[slot]) if text]
        used = sum(_normalise(text) in _normalise(value) for text in given)
        if used < required:
            return [f"uses_given: 入力要素の使用数 {used} が {required} 未満です"], []
        return [], []

    if name == "ends_complete":
        field, field_error = _field_argument(arguments)
        if field_error:
            return [f"ends_complete: {field_error}"], []
        value, missing = _read_field(output, field)
        if missing or not isinstance(value, str):
            return ["ends_complete: 対象フィールドが文字列ではありません"], []
        if not text_ends_complete(value):
            return ["ends_complete: 完結を示す末尾ではありません"], []
        return [], []

    if name == "avoid_listed":
        fields = arguments.get("fields")
        if fields is not None and (
            not isinstance(fields, list)
            or not all(isinstance(field, str) and field for field in fields)
        ):
            raise ValidationConfigurationError(
                "avoid_listed.fields must be a list of strings"
            )
        table = arguments.get("table")
        if not isinstance(table, str) or not table:
            raise ValidationConfigurationError(
                "avoid_listed.table must be a non-empty string"
            )
        phrases = _load_avoid_phrases(table, harness_root)
        texts = _avoid_listed_texts(output, output_kind, fields)
        normalized_texts = [_normalize_for_substring(text) for text in texts]
        normalized_phrases = [
            _normalize_for_substring(phrase) for phrase in phrases
        ]
        found = sorted(
            {
                phrase
                for phrase, normalized in zip(phrases, normalized_phrases)
                if normalized and any(normalized in text for text in normalized_texts)
            }
        )
        if found:
            return [
                f"avoid_listed: 禁止表現が含まれています: {', '.join(found)}"
            ], []
        return [], []

    if name == "no_new_proper_nouns":
        mode = arguments.get("mode")
        if mode not in {"warn", "fail"}:
            raise ValidationConfigurationError("no_new_proper_nouns.mode must be warn or fail")
        fields = arguments.get("fields")
        if fields is not None and (
            not isinstance(fields, list) or not all(isinstance(field, str) and field for field in fields)
        ):
            raise ValidationConfigurationError("no_new_proper_nouns.fields must be a list of strings")
        texts = _proper_noun_texts(output, output_kind, fields)
        input_text = _input_text(inputs)
        allowed_words = _load_common_words(
            harness_root=harness_root,
            common_words_path=common_words_path,
        )
        candidates = sorted(
            {
                candidate
                for text in texts
                for candidate in _proper_noun_candidates(text)
                if candidate not in input_text and candidate not in allowed_words
            }
        )
        if not candidates:
            return [], []
        message = f"no_new_proper_nouns: 新しい固有名詞候補: {', '.join(candidates)}"
        if mode == "warn":
            return [], [message]
        return [message], []

    raise ValidationConfigurationError(f"unknown validation check: {name}")


def _field_argument(arguments: Mapping[str, Any]) -> tuple[str | None, str | None]:
    field = arguments.get("field")
    if field is None:
        return None, None
    if not isinstance(field, str) or not field:
        return None, "field が空です"
    return field, None


def _required_field_argument(arguments: Mapping[str, Any]) -> tuple[str | None, str | None]:
    field = arguments.get("field")
    if not isinstance(field, str) or not field:
        return None, "field が必要です"
    return field, None


def _read_field(value: Any, field: str | None) -> tuple[Any, bool]:
    if field is None:
        return value, False
    current = value
    for part in field.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None, True
        current = current[part]
    return current, False


def _ids_from_inputs(inputs: Mapping[str, Any]) -> set[Any]:
    ids: set[Any] = set()
    for value in inputs.values():
        ids.update(_ids_from_value(value))
    return ids


def _ids_from_value(value: Any) -> set[Any]:
    if isinstance(value, list):
        ids: set[Any] = set()
        for item in value:
            ids.update(_ids_from_value(item))
        return ids
    if isinstance(value, Mapping):
        ids: set[Any] = set()
        for key in ("id", "set_id"):
            identifier = value.get(key)
            if identifier is not None:
                ids.add(identifier)
        for item in value.values():
            if isinstance(item, (Mapping, list)):
                ids.update(_ids_from_value(item))
        return ids
    return set()


def _item_id(value: Any) -> Any:
    if isinstance(value, Mapping):
        return value.get("id")
    return value


def _input_text(inputs: Mapping[str, Any]) -> str:
    return "\n".join(_value_text(value) for value in inputs.values())


def _value_text(value: Any) -> str:
    if isinstance(value, Mapping):
        return "\n".join(f"{key}: {_value_text(item)}" for key, item in value.items())
    if isinstance(value, list):
        return "\n".join(_value_text(item) for item in value)
    return str(value)


def _input_texts(value: Any) -> list[str]:
    if isinstance(value, list):
        texts: list[str] = []
        for item in value:
            texts.extend(_input_texts(item))
        return texts
    if isinstance(value, Mapping):
        if "text" in value:
            return [str(value["text"])]
        if isinstance(value.get("sounds"), list):
            return _input_texts(value["sounds"])
        texts: list[str] = []
        for item in value.values():
            if isinstance(item, (Mapping, list)):
                texts.extend(_input_texts(item))
            elif isinstance(item, str):
                texts.append(item)
        return texts
    return [_item_text(value)]


def _item_text(value: Any) -> str:
    if isinstance(value, Mapping):
        if "text" in value:
            return str(value["text"])
        return _value_text(value)
    return str(value)


def _proper_noun_texts(
    output: Any,
    output_kind: str,
    fields: Any,
) -> list[str]:
    if fields is None:
        if output_kind == "text":
            return [output]
        return _all_string_values(output, skip_key="sources")
    texts: list[str] = []
    for field in fields:
        value, missing = _read_field(output, field)
        if not missing:
            texts.extend(_all_string_values(value))
    return texts


def _avoid_listed_texts(
    output: Any,
    output_kind: str,
    fields: Any,
) -> list[str]:
    if fields is None:
        if output_kind == "text":
            return [output]
        return _all_string_values(output, skip_key="sources")
    texts: list[str] = []
    for field in fields:
        value, missing = _read_field(output, field)
        if not missing:
            texts.extend(_all_string_values(value))
    return texts


def _load_avoid_phrases(table: str, harness_root: str | Path | None) -> list[str]:
    root = Path(harness_root) if harness_root is not None else _DEFAULT_HARNESS_ROOT
    relative_path = Path(table.replace("\\", "/"))
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValidationConfigurationError(
            f"avoid_listed.table はハーネス内の相対パスで指定してください: {table}"
        )
    path = root / relative_path
    try:
        with path.open("r", encoding="utf-8") as stream:
            document = yaml.safe_load(stream)
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise ValidationConfigurationError(
            f"avoid_listed のテーブルを読めません: {path}"
        ) from error
    if not isinstance(document, Mapping) or not isinstance(
        document.get("phrases"), list
    ) or not all(isinstance(phrase, str) and phrase for phrase in document["phrases"]):
        raise ValidationConfigurationError(
            f"avoid_listed のテーブル形式が不正です: {path}"
        )
    return list(document["phrases"])


def _all_string_values(value: Any, *, skip_key: str | None = None) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [
            text
            for key, item in value.items()
            if key != skip_key
            for text in _all_string_values(item, skip_key=skip_key)
        ]
    if isinstance(value, list):
        return [text for item in value for text in _all_string_values(item, skip_key=skip_key)]
    return []


def _proper_noun_candidates(text: str) -> set[str]:
    return {
        match.group(0)
        for pattern in _PROPER_NOUN_PATTERNS
        for match in pattern.finditer(text)
    }


def _load_common_words(
    *,
    harness_root: str | Path | None,
    common_words_path: str | Path | None,
) -> set[str]:
    path = Path(common_words_path) if common_words_path is not None else (
        (Path(harness_root) if harness_root is not None else _DEFAULT_HARNESS_ROOT)
        / "tables" / "common_words.yaml"
    )
    if not path.is_file():
        return set()
    try:
        with path.open("r", encoding="utf-8") as stream:
            document = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as error:
        raise ValidationConfigurationError(f"common_words.yaml を読めません: {path}") from error
    return set(_all_string_values(document))


def _normalise(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _normalize_for_substring(value: str) -> str:
    """Normalize text for deny-list substring matching."""
    return unicodedata.normalize("NFKC", value).casefold()


def _char_length(value: str) -> int:
    return sum(not character.isspace() for character in _normalise(value))


def _strip_output_strings(value: Any) -> Any:
    """Strip surrounding whitespace from every submitted string value."""

    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        return {key: _strip_output_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_strip_output_strings(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_strip_output_strings(item) for item in value)
    return value


def _is_nonnegative_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_positive_integer(value: Any) -> bool:
    return _is_nonnegative_integer(value) and value > 0


def load_yaml(
    path: str | Path,
    schema_path: str | Path | None = None,
) -> Any:
    """Read a YAML file and optionally validate it against a JSON Schema.

    The returned value is the Python value produced by ``yaml.safe_load``;
    this function does not apply schema defaults or otherwise mutate it.
    When ``schema_path`` is supplied, validation happens before returning.
    """

    yaml_path = Path(path)
    try:
        with yaml_path.open("r", encoding="utf-8") as stream:
            document = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as error:
        raise YamlLoadError(f"{yaml_path}: {error}") from error

    if schema_path is not None:
        validate_document(document, schema_path, source_path=yaml_path)
        if Path(schema_path).name == "task-definition.schema.json":
            validate_task_definition_output_example(
                document,
                schema_root=Path(schema_path).resolve().parents[1],
                source_path=yaml_path,
            )
    return document


def validate_document(
    document: Any,
    schema: str | Path | Mapping[str, Any],
    *,
    source_path: str | Path | None = None,
) -> Any:
    """Validate ``document`` against a Draft 2020-12 JSON Schema.

    ``schema`` can be either a schema file or an already loaded mapping.  The
    document is returned unchanged on success, which makes the function easy
    to use in parsing pipelines while keeping validation explicit.
    """

    if isinstance(schema, (str, Path)):
        schema_path = Path(schema)
        try:
            resolved_schema_path = schema_path.resolve()
            schema_stat = resolved_schema_path.stat()
        except OSError as error:
            raise YamlLoadError(f"{schema_path}: {error}") from error

        cache_key = (
            resolved_schema_path,
            schema_stat.st_mtime_ns,
            schema_stat.st_size,
        )
        cached = _SCHEMA_CACHE.get(cache_key)
        if cached is not None:
            schema_document, validator = cached
        else:
            try:
                with resolved_schema_path.open("r", encoding="utf-8") as stream:
                    schema_document = json.load(stream)
            except (OSError, json.JSONDecodeError) as error:
                raise YamlLoadError(f"{schema_path}: {error}") from error

            try:
                validator = Draft202012Validator(schema_document)
                validator.check_schema(schema_document)
            except SchemaError as error:
                raise YamlLoadError(
                    f"{schema_path}: invalid JSON Schema: {error.message}"
                ) from error
            _SCHEMA_CACHE[cache_key] = (schema_document, validator)
    else:
        schema_document = schema

        try:
            validator = Draft202012Validator(schema_document)
            validator.check_schema(schema_document)
        except SchemaError as error:
            schema_name = str(schema) if isinstance(schema, (str, Path)) else "schema"
            raise YamlLoadError(
                f"{schema_name}: invalid JSON Schema: {error.message}"
            ) from error

    errors = sorted(
        validator.iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.path),
    )
    if errors:
        document_name = Path(source_path) if source_path is not None else Path("<document>")
        raise SchemaValidationError(document_name, errors)
    return document


def load_and_validate_yaml(
    path: str | Path,
    schema_path: str | Path,
) -> Any:
    """Explicit alias for the common read-and-validate operation."""

    return load_yaml(path, schema_path)


def validate_task_definition_output_example(
    definition: Any,
    *,
    schema_root: str | Path,
    source_path: str | Path | None = None,
) -> None:
    """Check that a JSON task-card example contains no concrete prose.

    The example may contain the ``...`` placeholder, enum values from its
    output schema, and the literal IDs in its ``sources`` arrays.  Other
    string values would steer an executor toward the example and are rejected
    when the task definition is loaded.
    """

    if not isinstance(definition, Mapping) or definition.get("kind") != "llm":
        return
    card = definition.get("card")
    if not isinstance(card, Mapping) or "output_example" not in card:
        return

    example = card["output_example"]
    output_kind = definition.get("output")
    if output_kind != "json":
        return
    if not isinstance(example, str):
        return

    try:
        parsed = json.loads(example)
    except json.JSONDecodeError as error:
        raise TaskDefinitionValidationError(
            _task_definition_error_prefix(source_path)
            + f"card.output_example は JSON でなければなりません: {error.msg}"
        ) from error

    schema_ref = (definition.get("validate") or {}).get("schema")
    enum_values: set[str] = set()
    if isinstance(schema_ref, str):
        output_schema_path = Path(schema_root) / schema_ref
        if output_schema_path.is_file():
            try:
                with output_schema_path.open("r", encoding="utf-8") as stream:
                    output_schema = json.load(stream)
            except (OSError, json.JSONDecodeError) as error:
                raise TaskDefinitionValidationError(
                    _task_definition_error_prefix(source_path)
                    + f"出力スキーマを読めません: {output_schema_path}"
                ) from error
            enum_values = _string_enum_values(output_schema)

    source_ids = _source_example_ids(parsed)
    for path, value in _string_values(parsed):
        if value == "..." or value in enum_values or value in source_ids:
            continue
        raise TaskDefinitionValidationError(
            _task_definition_error_prefix(source_path)
            + f"card.output_example の {path} に具体的な文字列があります: {value!r}"
        )


def _task_definition_error_prefix(source_path: str | Path | None) -> str:
    return f"{Path(source_path)}: " if source_path is not None else ""


def _string_enum_values(schema: Any) -> set[str]:
    values: set[str] = set()
    if isinstance(schema, Mapping):
        enum = schema.get("enum")
        if isinstance(enum, list):
            values.update(value for value in enum if isinstance(value, str))
        for value in schema.values():
            values.update(_string_enum_values(value))
    elif isinstance(schema, list):
        for value in schema:
            values.update(_string_enum_values(value))
    return values


def _source_example_ids(value: Any, *, in_sources: bool = False) -> set[str]:
    ids: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            ids.update(
                _source_example_ids(child, in_sources=in_sources or key == "sources")
            )
    elif isinstance(value, list):
        for child in value:
            if in_sources and isinstance(child, str):
                ids.add(child)
            else:
                ids.update(_source_example_ids(child, in_sources=in_sources))
    return ids


def _string_values(value: Any, path: str = "$") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, Mapping):
        result: list[tuple[str, str]] = []
        for key, child in value.items():
            result.extend(_string_values(child, f"{path}.{key}"))
        return result
    if isinstance(value, list):
        result = []
        for index, child in enumerate(value):
            result.extend(_string_values(child, f"{path}[{index}]"))
        return result
    return []


def _format_validation_error(error: ValidationError) -> str:
    location = "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.path
    )
    return f"{location.lstrip('.') or '<root>'}: {error.message}"
