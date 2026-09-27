"""Load human-authored YAML and validate it against a JSON Schema.

YAML is deliberately decoded with :func:`yaml.safe_load` and validated
immediately.  In particular, YAML 1.1 values such as ``no`` may be decoded
as booleans; the schema is what catches that type error instead of silently
accepting it as a string.
"""

from __future__ import annotations

import json
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
            with schema_path.open("r", encoding="utf-8") as stream:
                schema_document = json.load(stream)
        except (OSError, json.JSONDecodeError) as error:
            raise YamlLoadError(f"{schema_path}: {error}") from error
    else:
        schema_document = schema

    try:
        validator = Draft202012Validator(schema_document)
        validator.check_schema(schema_document)
    except SchemaError as error:
        schema_name = str(schema) if isinstance(schema, (str, Path)) else "schema"
        raise YamlLoadError(f"{schema_name}: invalid JSON Schema: {error.message}") from error

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


def _format_validation_error(error: ValidationError) -> str:
    location = "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.path
    )
    return f"{location.lstrip('.') or '<root>'}: {error.message}"
