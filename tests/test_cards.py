from __future__ import annotations

import pytest

from storyteller.cards import InputBudgetError, generate_task_card
from storyteller.selectors import SelectorError, resolve_inputs, resolve_selector


def llm_task(**overrides):
    task = {
        "id": "D2.echo",
        "version": 1,
        "kind": "llm",
        "output": "json",
        "inputs": {
            "items": {
                "label": "項目",
                "from": "D1.items",
                "select": "items",
                "required": True,
            },
            "context": {
                "label": "文脈",
                "from": "input",
                "select": "input.context",
                "truncate": "drop",
            },
        },
        "card": {
            "role": "与えられた項目だけを使って、応答を書く。",
            "steps": ["項目を一つずつ確認する。", "JSONを出力する。"],
            "output_example": '{"text": "...", "sources": ["d1"]}',
        },
    }
    task.update(overrides)
    return task


def test_selector_resolves_roots_access_and_slot_placeholder():
    source = {"cast": [{"id": "d1"}, {"id": "d2"}]}
    assert resolve_selector("cast[{slot}].id", source, index="1") == "d2"
    assert resolve_selector("output.cast[0].id", source) == "d1"
    assert resolve_selector("input.context", source, {"context": "given"}) == "given"

    resolved = resolve_inputs(
        {
            "inputs": {
                "item": {
                    "from": "D1.items",
                    "select": "items[{slot}]",
                }
            }
        },
        {"D1.items": {"items": ["a", "b"]}},
        index="1",
    )
    assert resolved == {"item": "b"}


def test_selector_rejects_functions_and_missing_values():
    with pytest.raises(SelectorError):
        resolve_selector("first(items)", {"items": [1]})
    with pytest.raises(SelectorError):
        resolve_selector("items[{slot}]", {"items": [1]})
    with pytest.raises(SelectorError):
        resolve_selector("missing", {})


def test_optional_selector_omits_a_missing_value():
    definition = {
        "inputs": {
            "optional": {
                "from": "D1.output",
                "select": "plot.climax",
                "required": False,
            },
            "required": {
                "from": "D1.output",
                "select": "plot.conflict",
                "required": True,
            },
        }
    }

    assert resolve_inputs(
        definition,
        {"D1.output": {"plot": {"conflict": "葛藤"}}},
    ) == {"required": "葛藤"}


def test_card_has_fixed_sections_and_hides_task_metadata():
    card = generate_task_card(
        llm_task(),
        "0123456789abcdef",
        outputs={"D1.items": {"items": [{"id": "d1", "text": "alpha"}]}},
        run_input={"context": "user input"},
    )

    assert card.startswith("# タスク 0123456789abcdef\n")
    assert "## あなたの役割" in card
    assert "## 入力" in card
    assert "### 項目\n[d1] alpha" in card
    assert "## 手順\n1. 項目を一つずつ確認する。" in card
    assert "## 出力形式\n次のJSONだけを出力すること。前後に説明を書かないこと。" in card
    assert "## 守ること" in card
    assert "D2.echo" not in card
    assert "D1.items" not in card
    assert "run" not in card
    assert "S1" not in card


def test_card_truncates_optional_slots_in_reverse_order():
    task = llm_task(
        max_input_chars=20,
        inputs={
            "first": {
                "label": "一",
                "from": "input",
                "select": "input.first",
                "truncate": "none",
            },
            "second": {
                "label": "二",
                "from": "input",
                "select": "input.second",
                "truncate": "drop",
            },
        },
    )
    card = generate_task_card(
        task,
        "ticket",
        run_input={"first": "必須ではないが残る", "second": "削除される"},
    )
    assert "### 一\n必須ではないが残る" in card
    assert "### 二" not in card


def test_card_truncates_arrays_by_items_and_strings_by_characters():
    task = llm_task(
        max_input_chars=10,
        inputs={
            "items": {
                "label": "項目",
                "from": "input",
                "select": "input.items",
                "required": True,
                "truncate": "head",
            }
        },
    )
    card = generate_task_card(task, "ticket", run_input={"items": ["a", "b", "c"]})
    assert "### 項目\n- a" in card
    assert "- b" not in card

    string_task = llm_task(
        max_input_chars=7,
        inputs={
            "text": {
                "label": "文",
                "from": "input",
                "select": "input.text",
                "required": True,
                "truncate": "tail",
            }
        },
    )
    string_card = generate_task_card(
        string_task, "ticket", run_input={"text": "abcdef"}
    )
    assert "### 文\nf" in string_card
    assert "abcdef" not in string_card


def test_required_input_that_cannot_be_truncated_is_an_error():
    task = llm_task(max_input_chars=1, inputs={
        "items": {
            "label": "項目",
            "from": "input",
            "select": "input.items",
            "required": True,
            "truncate": "none",
        }
    })
    with pytest.raises(InputBudgetError, match="入力が予算を超える"):
        generate_task_card(task, "ticket", run_input={"items": "too long"})


def test_retry_reason_and_continuation_tail_are_limited():
    reason = "あ" * 600
    tail = "末" * 7000
    card = generate_task_card(
        llm_task(output="text", max_input_chars=3000),
        "ticket",
        inputs={"items": ["one"]},
        retry_reason=reason,
        continuation_tail=tail,
    )
    assert "## 前回の不合格理由\n" + ("あ" * 499) + "…" in card
    assert "## これまでの出力の末尾\n" + ("末" * 6000) in card
    assert "既出の文章を繰り返さず" in card
