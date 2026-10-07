"""Local Issue drafts with card inputs redacted (task-model §6.3)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import subprocess
from typing import Any

from .cards import generate_task_card, prepare_task_inputs, _render_input_body, _render_value
from .storage import atomic_write_text
from .validation import output_char_count


@lru_cache(maxsize=None)
def harness_version(root: Path) -> str:
    try:
        package = version("100-times-ai-storyteller")
    except PackageNotFoundError:
        from . import __version__
        package = __version__
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, encoding="utf-8", timeout=5, check=True,
        )
        commit = result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = "取得できません"
    return f"パッケージ: {package}\ngit: {commit}"


def redact_card(
    card: str, definition: Mapping[str, Any], inputs: Mapping[str, Any],
) -> str:
    """Remove the exact rendered input, including user-supplied Markdown headings."""
    input_body = _render_input_body(definition, inputs)
    marker = "## 入力\n"
    start = card.index(marker) + len(marker)
    if card[start:start + len(input_body)] != input_body:
        raise ValueError("保存されたカードと入力が一致しません")
    summary = "\n".join(
        f"### {slot['label']}\n[入力本文を伏せました: "
        f"{output_char_count(_render_value(inputs[name], context_only=name in {'role_definition', 'plot_requirements'}))}文字]"
        for name, slot in definition.get("inputs", {}).items() if name in inputs
    )
    card = card[:start] + summary + card[start + len(input_body):]
    marker = "\n## これまでの出力の末尾\n"
    if marker in card:
        card = card[:card.index(marker)] + marker + "[本文を伏せました]"
    return card


def write_failure_reports(
    run_dir: Path, manifest: Mapping[str, Any],
    definitions: Mapping[str, Mapping[str, Any]], repository_root: Path, *,
    resolve_inputs: Callable[[str], Mapping[str, Any]] | None = None,
) -> None:
    """Cover all failure paths at the orchestrator's persistence boundary."""
    for task_id, task in manifest["tasks"].items():
        if task["state"] != "failed":
            continue
        directory = run_dir / "tasks" / task_id
        definition = definitions[task["type"]]
        lines = [f"# タスク失敗: {task['type']}", "",
                 f"定義の version: {definition['version']}",
                 harness_version(repository_root), "", f"理由: {task['error']}", "",
                 "## 試行"]
        attempts = sorted((directory / "attempts").glob("*.json"), key=lambda p: int(p.stem))
        executor = next((event.get("executor_id") for event in reversed(task["history"])
                         if event.get("executor_id")), "コード／未実行")
        for path in attempts:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(record, Mapping):
                    raise ValueError("試行の記録がオブジェクトではありません")
            except (OSError, ValueError):
                lines.extend(["", f"### 試行 {path.stem}", "試行の記録を読み込めません。"])
                continue
            raw = record.get("output")
            preview = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
            lines.extend(["", f"### 試行 {path.stem}",
                          f"実行者: {record.get('executor_id', executor)}",
                          f"理由: {record.get('reason', '')}", "", preview[:300]])
        lines.extend(["", f"実行者: {executor}", "", "## カード", ""])
        card_path = directory / "card.md"
        if definition["kind"] == "llm":
            try:
                if card_path.is_file():
                    card = card_path.read_text(encoding="utf-8")
                    inputs = json.loads((directory / "input.json").read_text(encoding="utf-8"))
                elif resolve_inputs is not None:
                    unlimited = {**definition, "max_input_chars": 2**63}
                    inputs = prepare_task_inputs(unlimited, inputs=resolve_inputs(task_id))
                    card = generate_task_card(unlimited, "未発行", inputs=inputs)
                else:
                    raise ValueError("カードは未生成です")
                card = redact_card(card, definition, inputs)
            except (OSError, ValueError, KeyError, TypeError):
                card = "カードを再現できません。\n" + "\n".join(
                    f"### {slot['label']}\n[入力の文字数を取得できません]"
                    for slot in definition.get("inputs", {}).values()
                )
        else:
            card = "コードタスクのためカードはありません。"
        lines.append(card)
        atomic_write_text(directory / "failure.md", "\n".join(lines) + "\n")
