"""Shared parsing and contract checks for one inference element."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any


JSON_FIELDS = {
    "name": {"name", "reading"},
    "labeled": {"text", "kind"},
    "judgement": {"answer", "reason"},
}
VALUE_ELEMENTS = frozenset({"number", "integer", "choice"})


def clean_value(raw: str, unit: str = "") -> str:
    """Remove presentation noise specified in task-model §2.5."""
    text = unicodedata.normalize("NFKC", raw).strip().removesuffix("。").strip()
    unit = unicodedata.normalize("NFKC", unit)
    if unit and text.endswith(unit):
        text = text[:-len(unit)].strip()
    return text


def parse_element_value(
    raw: str, element: str, *, unit: str = "", choices: Sequence[str] = (),
    choice_max: int = 1, value_range: Mapping[str, int | float] | None = None,
) -> int | float | str | list[str]:
    """Parse a scalar or ID set; reject prose, nonfinite and out-of-range values."""
    text = clean_value(raw, unit)
    if element == "choice":
        selected = text.split()
        if (not selected or len(selected) > choice_max
                or len(set(selected)) != len(selected)
                or not set(selected).issubset(choices)):
            raise ValueError("choice: 選択肢の ID と選べる数の上限に従ってください")
        return selected[0] if choice_max == 1 else selected
    text = text.replace(",", "")
    if element == "integer":
        if not re.fullmatch(r"[+-]?[0-9]+", text):
            raise ValueError("integer: 整数1つを解析できません")
        value = int(text)
    elif element == "number":
        if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", text):
            raise ValueError("number: 数値1つを解析できません")
        value = float(text)
        if not math.isfinite(value):
            raise ValueError("number: 有限の数値を指定してください")
    else:
        raise ValueError(f"値の要素ではありません: {element}")
    limits = value_range or {}
    if ("min" in limits and value < limits["min"]) or ("max" in limits and value > limits["max"]):
        raise ValueError(f"range: 値 {value} が範囲 {dict(limits)} の外です")
    return value


def choice_ids(value: Any) -> list[str]:
    """Read supplied ID choices, including the explicit none option."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        if isinstance(value.get("id"), str):
            return [value["id"]]
        return [identifier for item in value.values() for identifier in choice_ids(item)]
    if isinstance(value, list):
        return [identifier for item in value for identifier in choice_ids(item)]
    return []


def resolve_range(
    limits: Mapping[str, Any], values: Mapping[str, Any],
) -> dict[str, int | float]:
    """Resolve run-value placeholders without evaluating arbitrary expressions."""
    result = {}
    for bound, value in limits.items():
        if isinstance(value, str):
            match = re.fullmatch(r"\{([a-z][a-z0-9_.]*)\}", value)
            if not match:
                raise ValueError(f"range: 不正な参照 {value}")
            current: Any = values
            for part in match[1].split("."):
                if not isinstance(current, Mapping) or part not in current:
                    raise ValueError(f"range: run の値がありません: {value}")
                current = current[part]
            value = current
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (
            isinstance(value, float) and not math.isfinite(value)
        ):
            raise ValueError(f"range: 境界が数値ではありません: {value!r}")
        result[bound] = value
    if "min" in result and "max" in result and result["min"] > result["max"]:
        raise ValueError("range: min が max より大きいです")
    return result


def element_contract_errors(definition: Mapping[str, Any], schema: Any = None) -> list[str]:
    """Check normal element/output/schema contracts (migration exceptions live outside)."""
    element = definition.get("element")
    expected = "json" if element in JSON_FIELDS else "text"
    errors = []
    if element not in {*JSON_FIELDS, "text", *VALUE_ELEMENTS}:
        return ["element: 未知の要素です"]
    if definition.get("output") != expected:
        errors.append(f"element: {element} の output は {expected} です")
    if expected == "text" and definition.get("validate", {}).get("schema"):
        errors.append("element: text 出力に JSON オブジェクトのスキーマを指定できません")
    if expected == "json":
        fields = JSON_FIELDS[element]
        if not isinstance(schema, Mapping) or schema.get("type") != "object":
            return [*errors, "element: JSON の出力スキーマが必要です"]
        if (set(schema.get("properties", {})) != fields
                or set(schema.get("required", [])) != fields
                or schema.get("additionalProperties") is not False):
            errors.append(f"element: {element} の項目は {sorted(fields)} だけです")
        properties = schema.get("properties", {})
        if any(properties.get(field, {}).get("type") != "string" for field in fields):
            errors.append("element: JSON の各項目は文字列です")
        enum_field = {"labeled": "kind", "judgement": "answer"}.get(element)
        if enum_field and not properties.get(enum_field, {}).get("enum"):
            errors.append(f"element: {enum_field} に列挙値が必要です")
    return errors
