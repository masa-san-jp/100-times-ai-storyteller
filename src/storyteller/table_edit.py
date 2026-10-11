"""``st table list/add/check``: editing support for element tables.

The default tables under ``tables/elements`` are read-only.  Only the
supplemental tables in the data directory (``tables/<axis>.json``) are
written, append-only and under ``tables.lock``.
"""

from __future__ import annotations

import json
import sys
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .storage import atomic_write_json, resolve_data_dir, tables_lock
from .tables import element_rows, load_element_table, load_table


class TableEditError(ValueError):
    """A table edit was rejected or a table is invalid (exit code 1)."""


def normalize_text(value: str) -> str:
    """Duplicate-detection form shared with S2 (NFKC, whitespace removed)."""

    return "".join(unicodedata.normalize("NFKC", value).split())


def axis_keys() -> list[str]:
    return [axis["key"] for axis in load_table("element_axes")["axes"]]


def _require_axis(axis: str) -> None:
    if axis not in axis_keys():
        raise TableEditError(f"unknown axis: {axis}")


def supplement_path(data_dir: Path, axis: str) -> Path:
    return data_dir / "tables" / f"{axis}.json"


def row_problem(row: Any) -> str | None:
    """Return why ``row`` is not a valid element row, or None."""

    if isinstance(row, str):
        return None if row else "text must be a non-empty string"
    if not isinstance(row, Mapping):
        return "row must be an object or a string"
    extra = set(row) - {"text", "source"}
    if extra:
        return f"unknown field: {sorted(extra)[0]}"
    text = row.get("text")
    if not isinstance(text, str) or not text:
        return "text must be a non-empty string"
    if "source" in row and (not isinstance(row["source"], str) or not row["source"]):
        return "source must be a non-empty string"
    return None


def row_text(row: Any) -> str:
    """Return the text of a valid row (a bare string row is its own text)."""

    return row if isinstance(row, str) else row["text"]


def _read_supplement(path: Path) -> tuple[Any, list[Any]]:
    """Return (document, rows).  A missing file is an empty list."""

    if not path.exists():
        return None, []
    try:
        with path.open("r", encoding="utf-8") as stream:
            document = json.load(stream)
    except (OSError, ValueError) as error:
        raise TableEditError(f"cannot read {path}: {error}") from error
    if isinstance(document, list):
        return document, document
    if isinstance(document, Mapping) and isinstance(document.get("items"), list):
        return document, document["items"]
    raise TableEditError(f"{path}: must be a list or an object with items")


def _default_texts(axis: str) -> set[str]:
    rows = load_element_table(axis)["items"]
    return {normalize_text(row["text"]) for row in rows}


def check_axis(data_dir: Path, axis: str) -> list[str]:
    """Return human-readable problems for one axis (empty when valid)."""

    problems: list[str] = []
    try:
        defaults = _default_texts(axis)
    except Exception as error:  # schema or load failure of the default table
        problems.append(f"{axis}: default table: {error}")
        defaults = set()
    try:
        _, rows = _read_supplement(supplement_path(data_dir, axis))
    except TableEditError as error:
        return [*problems, f"{axis}: {error}"]
    seen: dict[str, int] = {}
    for number, row in enumerate(rows, start=1):
        reason = row_problem(row)
        if reason:
            problems.append(f"{axis}:x{number}: {reason}")
            continue
        text = normalize_text(row_text(row))
        if not text:
            problems.append(f"{axis}:x{number}: text is empty after normalization")
        elif text in defaults:
            problems.append(f"{axis}:x{number}: duplicates a default table row")
        elif text in seen:
            problems.append(f"{axis}:x{number}: duplicates {axis}:x{seen[text]}")
        else:
            seen[text] = number
    return problems


def add_row(data_dir: Path, axis: str, text: str, source: str | None) -> str:
    """Append one row to the supplemental table and return its ID."""

    _require_axis(axis)
    row: dict[str, str] = {"text": text}
    if source is not None:
        row["source"] = source
    reason = row_problem(row)
    if reason:
        raise TableEditError(reason)
    normalized = normalize_text(text)
    if not normalized:
        raise TableEditError("text is empty after normalization")
    path = supplement_path(data_dir, axis)
    (data_dir / "tables").mkdir(parents=True, exist_ok=True)
    with tables_lock(data_dir):
        problems = check_axis(data_dir, axis)
        if problems:
            raise TableEditError(
                f"supplemental table is invalid; run `st table check {axis}`: {problems[0]}"
            )
        if normalized in _default_texts(axis):
            raise TableEditError("duplicates a default table row")
        document, rows = _read_supplement(path)
        for existing in rows:
            if normalize_text(row_text(existing)) == normalized:
                raise TableEditError("duplicates a supplemental table row")
        if document is None:
            document = {"items": rows}
        rows.append(row)
        atomic_write_json(path, document)
        return f"{axis}:x{len(rows)}"


def _list_axis(data_dir: Path, axis: str) -> int:
    defaults = element_rows(axis)
    _, rows = _read_supplement(supplement_path(data_dir, axis))
    default_texts = {normalize_text(row["text"]) for row in defaults}
    duplicates = 0
    for number, row in enumerate(rows, start=1):
        text = row if isinstance(row, str) else row.get("text", "") if isinstance(row, Mapping) else str(row)
        mark = ""
        if normalize_text(text) in default_texts:
            mark = "  [既定と重複]"
            duplicates += 1
        print(f"{axis}:x{number}\t{text}{mark}")
    print(f"default: {len(defaults)}, supplement: {len(rows)}, duplicates with default: {duplicates}")
    return 0


def _run_list(args: Any) -> int:
    data_dir = resolve_data_dir()
    if args.axis is not None:
        _require_axis(args.axis)
        return _list_axis(data_dir, args.axis)
    for axis in axis_keys():
        _, rows = _read_supplement(supplement_path(data_dir, axis))
        print(f"{axis}\tdefault: {len(element_rows(axis))}\tsupplement: {len(rows)}")
    return 0


def _run_add(args: Any) -> int:
    print(add_row(resolve_data_dir(), args.axis, args.text, args.source))
    return 0


def _run_check(args: Any) -> int:
    data_dir = resolve_data_dir()
    if args.axis is not None:
        _require_axis(args.axis)
    axes = [args.axis] if args.axis is not None else axis_keys()
    problems = [p for axis in axes for p in check_axis(data_dir, axis)]
    for problem in problems:
        print(f"st: error: {problem}", file=sys.stderr)
    return 1 if problems else 0


def register_cli_commands(subparsers: Any) -> None:
    """Register ``st table`` without putting its logic in cli.py."""

    table_parser = subparsers.add_parser("table", help="inspect and extend element tables")
    sub = table_parser.add_subparsers(dest="table_command", title="commands")
    list_parser = sub.add_parser("list", help="list element tables")
    list_parser.add_argument("axis", nargs="?")
    list_parser.set_defaults(command_handler=_run_list)
    add_parser = sub.add_parser("add", help="append a row to a supplemental table")
    add_parser.add_argument("axis")
    add_parser.add_argument("text")
    add_parser.add_argument("--source")
    add_parser.set_defaults(command_handler=_run_add)
    check_parser = sub.add_parser("check", help="validate element tables")
    check_parser.add_argument("axis", nargs="?")
    check_parser.set_defaults(command_handler=_run_check)
