"""Build task cards from validated task definitions."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

import yaml

from storyteller.selectors import resolve_inputs


class TaskCardError(ValueError):
    """The task definition cannot be rendered as a task card."""


class InputBudgetError(TaskCardError):
    """The input section cannot be made small enough for its budget."""


def generate_task_card(
    task_definition: Mapping[str, Any],
    ticket: str,
    inputs: Mapping[str, Any] | None = None,
    *,
    outputs: Mapping[str, Any] | None = None,
    run_input: Any = None,
    index: Sequence[str] | str | None = None,
    retry_reason: str | None = None,
    continuation_tail: str | None = None,
    extend_to_min: bool = False,
) -> str:
    """Render one Markdown card.

    ``inputs`` may contain already-resolved slot values.  When it is omitted,
    slots are resolved from ``outputs`` and ``run_input`` using the selector
    language.  The task definition's ID is deliberately never rendered.
    """

    if not isinstance(ticket, str) or not ticket:
        raise TaskCardError("ticket must be a non-empty string")
    if task_definition.get("kind") != "llm":
        raise TaskCardError("only llm task definitions have task cards")

    rendered_slots = prepare_task_inputs(
        task_definition,
        inputs=inputs,
        outputs=outputs,
        run_input=run_input,
        index=index,
    )
    input_body = _render_input_body(task_definition, rendered_slots)

    output = task_definition.get("output")
    card = task_definition.get("card")
    if output not in {"json", "text"} or not isinstance(card, Mapping):
        raise TaskCardError("task definition is missing card output details")

    lines = [
        f"# タスク {ticket}",
        "",
        "## あなたの役割",
        _required_string(card, "role"),
        "",
        "## 入力",
        input_body,
        "",
        "## 手順",
    ]
    steps = card.get("steps")
    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)):
        raise TaskCardError("card.steps must be a non-empty sequence")
    for number, step in enumerate(steps, start=1):
        if not isinstance(step, str) or not step:
            raise TaskCardError("card.steps must contain non-empty strings")
        lines.append(f"{number}. {step}")
    if continuation_tail is not None:
        lines.append(
            "既出の文章を繰り返さず、同じ内容をさらに具体的に書き足す。"
            if extend_to_min
            else "既出の文章を繰り返さず、末尾の直後から続きを書いて完結させる。"
        )

    lines.extend(["", "## 出力形式"])
    if output == "json":
        lines.extend(
            [
                "次のJSONだけを出力すること。前後に説明を書かないこと。",
                _required_string(card, "output_example"),
            ]
        )
    else:
        lines.append("本文だけを出力すること。前後に説明・見出し・注釈を書かないこと。")
        example = card.get("output_example")
        if example is not None:
            if not isinstance(example, str) or not example:
                raise TaskCardError("card.output_example must be a non-empty string")
            lines.extend(["例：", example])

    lines.extend(
        [
            "",
            "## 守ること",
            "- 入力に書かれていないことを付け加えない。",
            "- 出力形式以外の文章を書かない。",
            "- 日本語で書く（ID・列挙値を除く）。",
        ]
    )
    if output == "json" and "sources" in str(card.get("output_example", "")):
        lines.append("- sources には、入力に [ ] で示された ID だけを書く。")
    if retry_reason is not None:
        lines.extend(["", "## 前回の不合格理由", _limit_retry_reason(retry_reason)])
    if continuation_tail is not None:
        lines.extend(["", "## これまでの出力の末尾", _limit_tail(continuation_tail)])
    return "\n".join(lines)


# These names make the rendering entry point easy to discover without adding
# a second implementation.  They are also useful to later orchestration code.
build_task_card = generate_task_card
render_task_card = generate_task_card


def prepare_task_inputs(
    task_definition: Mapping[str, Any],
    inputs: Mapping[str, Any] | None = None,
    *,
    outputs: Mapping[str, Any] | None = None,
    run_input: Any = None,
    index: Sequence[str] | str | None = None,
) -> dict[str, Any]:
    """Resolve and fit the values that are actually rendered in a card.

    The orchestrator stores this result beside the card.  Validation must use
    these values, rather than the unbounded source values, because a card may
    have dropped or truncated optional input to meet its character budget.
    """

    if inputs is None:
        slot_values = resolve_inputs(
            task_definition, outputs, run_input, index=index
        )
    else:
        slot_values = dict(inputs)
    return _fit_inputs(task_definition, slot_values)


def _fit_inputs(
    definition: Mapping[str, Any], values: Mapping[str, Any]
) -> dict[str, Any]:
    slots = definition.get("inputs", {})
    if not isinstance(slots, Mapping):
        raise TaskCardError("task definition inputs must be a mapping")
    current = dict(values)
    missing_required = [
        name for name, slot in slots.items() if slot.get("required", False) and name not in current
    ]
    if missing_required:
        raise TaskCardError(f"required input is missing: {missing_required[0]}")

    # The protagonist does not need a second copy of their own context.  The
    # S5 definitions keep these slots optional so the same definition can be
    # used for every character, while this task's character determines
    # whether the slots are rendered.
    character = current.get("character")
    if isinstance(character, Mapping) and character.get("role") == "protagonist":
        for name in ("protagonist_name", "protagonist_role", "protagonist_intro"):
            current.pop(name, None)

    budget = definition.get("max_input_chars", 3000)
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
        raise TaskCardError("max_input_chars must be a positive integer")
    if _input_char_count(definition, current) <= budget:
        return current

    # The specification says optional slots are processed first, and both
    # groups are processed in reverse definition order.
    order = [
        *(
            name
            for name in reversed(list(slots))
            if not slots[name].get("required", False)
        ),
        *(
            name
            for name in reversed(list(slots))
            if slots[name].get("required", False)
        ),
    ]
    for name in order:
        if name not in current:
            continue
        slot = slots[name]
        mode = slot.get("truncate", "none")
        if mode == "drop":
            if slot.get("required", False):
                continue
            del current[name]
        elif mode in {"head", "tail"}:
            current[name] = _truncate_value_until_fit(
                definition, current, name, mode, budget
            )
        elif mode != "none":
            raise TaskCardError(f"unknown truncate mode: {mode}")
        if _input_char_count(definition, current) <= budget:
            return current

    raise InputBudgetError("入力が予算を超える")


def _truncate_value_until_fit(
    definition: Mapping[str, Any],
    values: dict[str, Any],
    name: str,
    mode: str,
    budget: int,
) -> Any:
    value = values[name]
    candidates: list[Any]
    if isinstance(value, str):
        value = unicodedata.normalize("NFC", value)
        values[name] = value
        candidates = (
            [value[:size] for size in range(len(value) - 1, -1, -1)]
            if mode == "head"
            else [value[size:] for size in range(1, len(value) + 1)] + [""]
        )
    elif isinstance(value, list):
        candidates = (
            [value[:size] for size in range(len(value) - 1, -1, -1)]
            if mode == "head"
            else [value[size:] for size in range(1, len(value) + 1)] + [[]]
        )
    else:
        return value

    original = value
    for candidate in candidates:
        values[name] = candidate
        if _input_char_count(definition, values) <= budget:
            return candidate
    values[name] = original
    return original


def _input_char_count(definition: Mapping[str, Any], values: Mapping[str, Any]) -> int:
    return _char_len(_render_input_body(definition, values))


def input_char_count(
    definition: Mapping[str, Any], values: Mapping[str, Any]
) -> int:
    """Return the NFC character count of the rendered input section."""
    return _input_char_count(definition, values)


def _render_input_body(
    definition: Mapping[str, Any], values: Mapping[str, Any]
) -> str:
    lines: list[str] = []
    slots = definition.get("inputs", {})
    if not isinstance(slots, Mapping):
        raise TaskCardError("task definition inputs must be a mapping")
    for name, slot in slots.items():
        if name not in values:
            continue
        if not isinstance(slot, Mapping) or not isinstance(slot.get("label"), str):
            raise TaskCardError(f"input slot has no label: {name}")
        lines.append(f"### {slot['label']}")
        lines.append(
            _render_value(
                values[name],
                context_only=name in {"role_definition", "plot_requirements"},
            )
        )
    return "\n".join(lines)


def _render_value(value: Any, *, context_only: bool = False) -> str:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        if all(
            isinstance(item, Mapping) and "id" in item and "text" in item
            for item in value
        ):
            return "\n".join(
                f"[{item['id']}] {unicodedata.normalize('NFC', str(item['text']))}"
                for item in value
            )
        return "\n".join(f"- {_render_list_item(item)}" for item in value)
    if isinstance(value, Mapping):
        value = _prepare_mapping_for_card(value, context_only=context_only)
        return yaml.safe_dump(
            value, allow_unicode=True, sort_keys=False, default_flow_style=False
        ).rstrip("\n")
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False).rstrip("\n")


def _prepare_mapping_for_card(
    value: Mapping[str, Any],
    *,
    context_only: bool = False,
) -> dict[str, Any]:
    """Make source IDs explicit and keep context identifiers out of cards."""

    prepared: dict[str, Any] = {}
    for key, item in value.items():
        if context_only and key in {"id", "set_id"}:
            continue
        if key in {"role_definition", "plot_context"} and isinstance(item, Mapping):
            prepared[key] = _prepare_mapping_for_card(item, context_only=True)
        elif key in {"id", "set_id"} and isinstance(item, str):
            prepared[key] = f"[{item}]"
        elif isinstance(item, Mapping):
            prepared[key] = _prepare_mapping_for_card(item)
        elif isinstance(item, list):
            prepared[key] = [
                _prepare_mapping_for_card(entry) if isinstance(entry, Mapping) else entry
                for entry in item
            ]
        else:
            prepared[key] = item
    return prepared


def _render_list_item(value: Any) -> str:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        value = _prepare_mapping_for_card(value)
    if isinstance(value, (Mapping, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _required_string(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise TaskCardError(f"card.{key} must be a non-empty string")
    return value


def _limit_retry_reason(reason: str) -> str:
    text = unicodedata.normalize("NFC", str(reason))
    if _char_len(text) <= 500:
        return text
    return text[:499] + "…"


def _limit_tail(tail: str) -> str:
    text = unicodedata.normalize("NFC", str(tail))
    return text[-6000:]


def _char_len(value: str) -> int:
    return len(unicodedata.normalize("NFC", value))
