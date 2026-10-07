"""Resolve numeric bounds before an executor can receive a task."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .elements import parse_element_value, resolve_range
from .selectors import resolve_inputs


def preflight_range(
    definition: Mapping[str, Any], values: Mapping[str, Any], *,
    run_input: Any = None, outputs: Mapping[str, Any] | None = None,
    index: tuple[str, ...] | list[str] = (),
) -> dict[str, int | float]:
    """Resolve only the input slots used by a range, without fitting/truncation."""
    limits = definition.get("range", {})
    if not limits:
        return {}
    resolved_values = dict(values)
    if isinstance(run_input, Mapping):
        resolved_values.update(run_input)
    referenced = {
        value[1:-1].split(".")[0] for value in limits.values() if isinstance(value, str)
    }
    slots = {name: slot for name, slot in definition.get("inputs", {}).items()
             if name in referenced}
    if slots:
        resolved_values.update(resolve_inputs(
            {"inputs": slots}, outputs or {}, run_input, index=index,
        ))
    result = resolve_range(limits, resolved_values)
    return result


def fixed_range_value(definition: Mapping[str, Any], limits: Mapping[str, Any]) -> str | None:
    """Return the code-owned scalar when the allowable interval is a point."""
    if "min" not in limits or "max" not in limits or limits["min"] != limits["max"]:
        return None
    value = limits["min"]
    if definition["element"] == "integer":
        if int(value) != value:
            raise ValueError("range: 整数の値が存在しません")
        value = int(value)
    text = str(value)
    parse_element_value(text, definition["element"], value_range=limits)
    return text
