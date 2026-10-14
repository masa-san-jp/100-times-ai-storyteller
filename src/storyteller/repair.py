"""Repair cards: the first step of task-model §6.7 (ADR-0009).

When a submission fails only for reasons that rewriting can clear (length,
sentence ending, the spelling of a value or choice), the next attempt of the
same task is a repair inference.  Its card carries just the original output,
the reason, and the conditions to satisfy, and it asks for the same single
element as the task's normal card.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .cards import InputBudgetError, TaskCardError, _render_value

# The role line doubles as the marker that failure reports use to recognise a
# repair card and hide its body.
REPAIR_ROLE = "前回の出力を、元の内容を保ったまま、条件を満たすように書き直す。"
REPAIR_STEP = "元の内容を保ったまま、条件を満たすように書き直す。"
_MAX_ORIGINAL_CHARS = 6000
_OMITTED = "\n（中間を省略）\n"


def _elide_middle(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    available = limit - len(_OMITTED)
    if available < 2:
        raise InputBudgetError("修正カードの入力が予算を超える")
    head = (available + 1) // 2
    return text[:head] + _OMITTED + text[-(available - head):]


def latest_repair_source(task_dir: Path, task: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the latest attempt record when it asks for a repair, else ``None``."""
    if not isinstance(task.get("error"), str) or int(task.get("continuation_step", 0)) > 0:
        return None
    attempts = task_dir / "attempts"
    try:
        numbers = sorted(int(path.stem) for path in attempts.glob("*.json") if path.stem.isdigit())
        if not numbers:
            return None
        record = json.loads((attempts / f"{numbers[-1]}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(record, dict)
        or record.get("repairable") is not True
        or record.get("attempt") != task.get("attempt")
        or record.get("output") in (None, "")
    ):
        return None
    return record


def repair_conditions(
    definition: Mapping[str, Any], inputs: Mapping[str, Any], *, shorten_original: bool = False
) -> list[str]:
    """Describe what the rewritten output must satisfy, from the task definition."""
    lines: list[str] = []
    validation = definition.get("validate") or {}
    for check in validation.get("checks", []) if isinstance(validation, Mapping) else []:
        name, arguments = (
            next(iter(check.items())) if isinstance(check, Mapping) and len(check) == 1
            else (check, {})
        )
        if not isinstance(arguments, Mapping):
            arguments = {}
        where = f"（{arguments['field']}）" if "field" in arguments else ""
        if name == "max_chars":
            lines.append(f"{arguments.get('n')}字以内にする{where}。超えていれば、要点を残して短くする。")
        elif name == "min_chars":
            if shorten_original:
                lines.append(f"{arguments.get('n')}字以上にする{where}。元の出力へ書き足さず、補正前の元出力を要点を保って上下限内へ短く書き直す。")
            else:
                lines.append(f"{arguments.get('n')}字以上にする{where}。足りなければ、元の内容を具体的に描写して書き足す。")
        elif name == "no_story_metadata":
            lines.append("構造の説明と内部 ID を本文から除く。")
        elif name == "no_repeated_scene":
            lines.append("直前の場面の行動・結果を再演せず、担当部分だけを書く。")
        elif name == "ends_complete":
            lines.append(f"文末を「。」などで完結させる{where}。")
    element = definition.get("element")
    if element in {"number", "integer"}:
        lines.append("値を1つだけ書く。単位・説明・範囲を書かない。")
        bounds = inputs.get("bounds")
        if isinstance(bounds, Mapping) and bounds:
            lines.append(f"値の範囲：{json.dumps(dict(bounds), ensure_ascii=False)}")
    elif element == "choice":
        lines.append("入力の選択肢の ID だけを書く。説明を書かない。")
    return lines or ["不合格の理由を解消する。"]


def generate_repair_card(
    definition: Mapping[str, Any],
    ticket: str,
    *,
    original_output: Any,
    reason: str,
    inputs: Mapping[str, Any],
    max_chars_trimmed: bool = False,
) -> str:
    """Render the repair card for one failed attempt."""
    if not isinstance(ticket, str) or not ticket:
        raise TaskCardError("ticket must be a non-empty string")
    output = definition.get("output")
    card = definition.get("card")
    if output not in {"json", "text"} or not isinstance(card, Mapping):
        raise TaskCardError("task definition is missing card output details")
    original = (
        original_output
        if isinstance(original_output, str)
        else json.dumps(original_output, ensure_ascii=False)
    )
    original = _elide_middle(unicodedata.normalize("NFC", original), _MAX_ORIGINAL_CHARS)
    reason = unicodedata.normalize("NFC", str(reason))
    transform_note = "上限超過のため、文末まで切り詰めた後の検証結果です。" if max_chars_trimmed else ""

    def limited_reason(limit: int) -> str:
        if transform_note:
            prefix = transform_note + "\n"
            return prefix + _elide_middle(reason, limit - len(prefix))
        return reason[:limit - 1] + "…" if len(reason) > limit else reason

    repeated_scene = "no_repeated_scene" in reason
    rendered_reason = limited_reason(500)
    shorten_original = max_chars_trimmed and any(
        error.lstrip().startswith("min_chars:") for error in reason.split("；")
    )
    lines = [
        f"# タスク {ticket}", "", "## あなたの役割", REPAIR_ROLE, "", "## 入力",
        "### 元の出力", original, "### 不合格の理由", rendered_reason, "### 満たすべき条件",
        *(f"- {condition}" for condition in repair_conditions(definition, inputs, shorten_original=shorten_original)),
    ]
    for name in ("conditions", "decided_conditions"):
        if name in inputs:
            lines.extend(["### 確定した筋の条件（変更・追加しない）", _render_value(inputs[name])])
    previous_index = None
    if repeated_scene and isinstance(inputs.get("previous_scene"), str):
        lines.extend(["### 直前の場面（再演しない）", unicodedata.normalize("NFC", inputs["previous_scene"])[-600:]])
        previous_index = len(lines) - 1
    if definition.get("element") == "choice" and "choices" in inputs:
        lines.extend(["### 選択肢", _render_value(inputs["choices"])])
    budget = definition.get("max_input_chars", 3000)
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
        raise TaskCardError("max_input_chars must be a positive integer")
    input_start = lines.index("## 入力") + 1
    original_index = lines.index("### 元の出力") + 1
    reason_index = original_index + 2

    def input_size() -> int:
        return len(unicodedata.normalize("NFC", "\n".join(lines[input_start:])))

    if input_size() > budget:
        lines[reason_index] = limited_reason(160)
        minimum_original = min(len(original), len(_OMITTED) + 2)
        allowance = budget - input_size() + len(original)
        if allowance < minimum_original and previous_index is not None:
            lines[previous_index] = lines[previous_index][-100:]
            allowance = budget - input_size() + len(original)
        if allowance < minimum_original:
            lines[reason_index] = (limited_reason(64) if transform_note else
                                   lines[reason_index][:63] + "…" if len(lines[reason_index]) > 64 else lines[reason_index])
            allowance = budget - input_size() + len(original)
        if allowance < minimum_original:
            raise InputBudgetError("修正カードの入力が予算を超える")
        lines[original_index] = _elide_middle(original, allowance)
    if input_size() > budget:
        raise InputBudgetError("修正カードの入力が予算を超える")
    lines.extend(["", "## 手順", f"1. {REPAIR_STEP}",
                  "作り方の指示は本文に書かず、指定された処理を行うためだけに使う。", "",
                  "## 出力形式"])
    if output == "json":
        example = card.get("output_example")
        if not isinstance(example, str) or not example:
            raise TaskCardError("card.output_example must be a non-empty string")
        lines.extend(["次のJSONだけを出力すること。前後に説明を書かないこと。", example])
    else:
        lines.append(
            "値だけを書く。単位・説明を書かない。"
            if definition.get("element") in {"number", "integer", "choice"}
            else "本文だけを出力すること。前後に説明・見出し・注釈を書かないこと。"
        )
    lines.extend([
        "", "## 守ること",
        "- 元の出力にない事実・出来事・人物・固有名詞を付け加えない。",
        "- 出力形式以外の文章を書かない。",
        "- 日本語で書く（ID・列挙値を除く）。",
    ])
    return unicodedata.normalize("NFC", "\n".join(lines))


def is_repair_card(card: str) -> bool:
    return f"## あなたの役割\n{REPAIR_ROLE}\n" in card


def redact_repair_card(card: str) -> str:
    """Hide the original output of a repair card in a failure report."""
    start = card.index("## 入力\n") + len("## 入力\n")
    end = card.index("\n\n## 手順", start)
    return card[:start] + "[元の出力・理由・条件を伏せました]" + card[end:]
