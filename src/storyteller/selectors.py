"""Resolve the small, deliberately non-Turing-complete selector language.

Selectors are part of a task definition and are evaluated by the
orchestrator.  They are intentionally implemented without ``eval``: a
selector can only read a value and follow named or numeric accesses.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


class SelectorError(ValueError):
    """A selector is malformed or points at a value that does not exist."""


class MissingSelectorValueError(SelectorError):
    """A selector points at a value that is not present in its source."""


_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_INTEGER = re.compile(r"0|[1-9][0-9]*")
_STRING_INDEX = re.compile(r"[A-Za-z0-9_.:-]+")


def resolve_selector(
    expression: str,
    source: Any,
    run_input: Any = None,
    *,
    index: Sequence[str] | str | None = None,
) -> Any:
    """Resolve *expression* against one task output and the run input.

    ``source`` is the output selected by the input slot's ``from`` value.
    A bare name reads a top-level key from that output; ``output`` and
    ``input`` explicitly select the two roots.  The ``{slot}`` placeholder
    in an array access is replaced by the first task index.

    Function calls are rejected.  Phase 0 registers no selector functions,
    and rejecting them here also ensures that arbitrary code is never
    evaluated.
    """

    if not isinstance(expression, str) or not expression:
        raise SelectorError("selector must be a non-empty string")

    position = 0
    if expression.startswith("["):
        # A source selected by ``from`` is already the selector root.  This
        # form is used for indexed task types, for example
        # ``[{slot}].name`` and ``[c1].name``.
        value = source
    else:
        root_match = _NAME.match(expression)
        if root_match is None:
            raise SelectorError(f"invalid selector: {expression!r}")
        root = root_match.group(0)
        position = root_match.end()
        if position < len(expression) and expression[position] == "(":
            raise SelectorError(f"selector function is not registered: {root}")

        if root == "output":
            value = source
        elif root == "input":
            value = run_input
        else:
            if not isinstance(source, Mapping) or root not in source:
                raise MissingSelectorValueError(
                    f"selector value does not exist: {root}"
                )
            value = source[root]

    while position < len(expression):
        marker = expression[position]
        if marker == ".":
            match = _NAME.match(expression, position + 1)
            if match is None:
                raise SelectorError(f"invalid selector access: {expression!r}")
            value = _read_named(value, match.group(0), expression)
            position = match.end()
            continue
        if marker == "[":
            end = expression.find("]", position + 1)
            if end < 0:
                raise SelectorError(f"unclosed selector access: {expression!r}")
            token = expression[position + 1 : end]
            value = _read_index(value, token, index, expression)
            position = end + 1
            continue
        raise SelectorError(f"invalid selector syntax: {expression!r}")

    return value


def resolve_inputs(
    task_definition: Mapping[str, Any],
    outputs: Mapping[str, Any] | None = None,
    run_input: Any = None,
    *,
    index: Sequence[str] | str | None = None,
) -> dict[str, Any]:
    """Resolve every input slot in definition order.

    ``outputs`` maps task type IDs (the value of ``from``) to their output.
    ``from: input`` reads from ``run_input``.  This function is separate from
    card rendering so the same deterministic selector behavior can be used
    by later DAG code.
    """

    available_outputs = outputs or {}
    resolved: dict[str, Any] = {}
    for slot_name, slot in task_definition.get("inputs", {}).items():
        source_name = slot["from"]
        if source_name == "input":
            source = run_input
        else:
            if source_name not in available_outputs:
                if slot.get("required") is False or (
                    "required" not in slot
                    and source_name in {"S4.section", "S4.item"}
                ):
                    continue
                raise SelectorError(f"input source does not exist: {source_name}")
            source = available_outputs[source_name]
        try:
            resolved[slot_name] = resolve_selector(
                slot["select"], source, run_input, index=index
            )
        except MissingSelectorValueError:
            if slot.get("required") is False or (
                "required" not in slot and source_name in {"S4.section", "S4.item"}
            ):
                continue
            raise
    return resolved


def _read_named(value: Any, name: str, expression: str) -> Any:
    if not isinstance(value, Mapping) or name not in value:
        raise MissingSelectorValueError(
            f"selector value does not exist: {expression!r}"
        )
    return value[name]


def _read_index(
    value: Any,
    token: str,
    index: Sequence[str] | str | None,
    expression: str,
) -> Any:
    if token == "{slot}":
        if index is None:
            raise SelectorError(f"selector requires a task index: {expression!r}")
        slot = index[0] if not isinstance(index, str) else index
        if not slot:
            raise SelectorError(f"selector requires a non-empty task index: {expression!r}")
        token = slot
    elif not _INTEGER.fullmatch(token) and not _STRING_INDEX.fullmatch(token):
        raise SelectorError(f"invalid selector index: {expression!r}")
    try:
        position: int | str = int(token) if _INTEGER.fullmatch(token) else token
        if isinstance(position, str) and isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            for item in value:
                if isinstance(item, Mapping) and item.get("id") == position:
                    return item
            raise KeyError(position)
        return value[position]  # type: ignore[index]
    except (IndexError, KeyError, TypeError) as error:
        raise MissingSelectorValueError(
            f"selector value does not exist: {expression!r}"
        ) from error
