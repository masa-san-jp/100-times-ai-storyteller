"""Load and validate the human-authored story tables.

Tables are YAML source material.  This module keeps their filenames and their
schema selection in one place so callers cannot accidentally use an
unvalidated table.  Element tables are discovered from ``element_axes.yaml``;
the filename is the axis key and the item ID is derived later from its row
number as specified by the story pipeline.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .validation import load_and_validate_yaml


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_TABLE_SCHEMA_ROOT = Path("schemas") / "tables"

_TABLE_SPECS: dict[str, tuple[Path, Path]] = {
    "scales": (Path("tables/scales.yaml"), _TABLE_SCHEMA_ROOT / "scales.schema.json"),
    "structures": (
        Path("tables/structures.yaml"),
        _TABLE_SCHEMA_ROOT / "structures.schema.json",
    ),
    "plot_types": (
        Path("tables/plot_types.yaml"),
        _TABLE_SCHEMA_ROOT / "plot_types.schema.json",
    ),
    "world_sections": (
        Path("tables/world_sections.yaml"),
        _TABLE_SCHEMA_ROOT / "world_sections.schema.json",
    ),
    "element_axes": (
        Path("tables/element_axes.yaml"),
        _TABLE_SCHEMA_ROOT / "element_axes.schema.json",
    ),
    "name_sounds": (
        Path("tables/name_sounds.yaml"),
        _TABLE_SCHEMA_ROOT / "name_sounds.schema.json",
    ),
    "common_words": (
        Path("tables/common_words.yaml"),
        _TABLE_SCHEMA_ROOT / "common_words.schema.json",
    ),
    "roles": (
        Path("tables/roles.yaml"),
        _TABLE_SCHEMA_ROOT / "roles.schema.json",
    ),
    "cliches": (
        Path("tables/cliches.yaml"),
        _TABLE_SCHEMA_ROOT / "cliches.schema.json",
    ),
}
_ELEMENT_SCHEMA = _TABLE_SCHEMA_ROOT / "elements.schema.json"


class TableNameError(ValueError):
    """Raised when a caller requests a table that is not part of the harness."""


def table_names() -> tuple[str, ...]:
    """Return the names of the non-element tables in stable order."""

    return tuple(_TABLE_SPECS)


def table_path(name: str, *, repository_root: str | Path | None = None) -> Path:
    """Return the path for a named table without reading it."""

    root = Path(repository_root) if repository_root is not None else _REPOSITORY_ROOT
    relative_path, _ = _spec_for(name)
    return root / relative_path


def load_table(
    name: str,
    *,
    repository_root: str | Path | None = None,
) -> Any:
    """Read one table and validate it with its table JSON Schema.

    ``name`` is one of the names returned by :func:`table_names`, or
    ``elements/<axis>`` for an element table.
    """

    root = Path(repository_root) if repository_root is not None else _REPOSITORY_ROOT
    relative_path, schema_relative_path = _spec_for(name)
    return load_and_validate_yaml(
        root / relative_path,
        root / schema_relative_path,
    )


def load_element_table(
    axis: str,
    *,
    repository_root: str | Path | None = None,
) -> dict[str, list[dict[str, str]]]:
    """Read and validate the default element table for ``axis``."""

    value = load_table(f"elements/{axis}", repository_root=repository_root)
    return value


def element_id(axis: str, row_number: int) -> str:
    """Return the stable default-table ID for a one-based row number."""

    if not axis.isidentifier() or row_number < 1:
        raise ValueError("axis must be an identifier and row_number must be positive")
    return f"{axis}:t{row_number}"


def element_rows(
    axis: str,
    *,
    repository_root: str | Path | None = None,
) -> tuple[dict[str, str | None], ...]:
    """Return element rows with IDs derived from their immutable row numbers."""

    table = load_element_table(axis, repository_root=repository_root)
    return tuple(
        {"id": element_id(axis, index), **row}
        for index, row in enumerate(table["items"], start=1)
    )


def load_all_tables(
    *,
    repository_root: str | Path | None = None,
) -> dict[str, Any]:
    """Read and validate every default table, including all ten axes.

    The result uses the non-element table names as keys and stores element
    tables below ``elements`` keyed by their axis.  In particular, the axis
    catalog is read and validated before the element filenames are resolved.
    """

    tables: dict[str, Any] = {
        name: load_table(name, repository_root=repository_root)
        for name in table_names()
    }
    axes = tables["element_axes"].get("axes")
    if not isinstance(axes, list):
        raise TableNameError("element_axes table must contain an axes list")

    elements: dict[str, Any] = {}
    for axis in axes:
        if not isinstance(axis, Mapping) or not isinstance(axis.get("key"), str):
            raise TableNameError("element_axes contains an invalid axis key")
        key = axis["key"]
        elements[key] = load_element_table(key, repository_root=repository_root)
    tables["elements"] = elements
    return tables


# This name reads naturally at validation call sites and keeps the public API
# compatible with code that calls the operation a validation pass.
validate_all_tables = load_all_tables


def _spec_for(name: str) -> tuple[Path, Path]:
    if name in _TABLE_SPECS:
        return _TABLE_SPECS[name]
    if name.startswith("elements/"):
        axis = name.removeprefix("elements/")
        if axis and "/" not in axis and axis.isidentifier():
            return Path("tables/elements") / f"{axis}.yaml", _ELEMENT_SCHEMA
    raise TableNameError(f"unknown story table: {name}")
