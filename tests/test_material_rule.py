from __future__ import annotations

from copy import deepcopy
import json

from jsonschema import Draft202012Validator

import pytest

from storyteller.cards import TaskCardError, generate_task_card
from storyteller.selectors import resolve_inputs
from storyteller.story_s7 import event_source_for_card
from tests.test_cards import llm_task
from tests.test_story_s7 import ROOT, _load_definition, _slot


@pytest.mark.parametrize('invalid', ['', None, 42, True, [], {}])
def test_material_rule_rejects_empty_or_nonstring_value(invalid):
    definition = llm_task()
    definition['card']['material_rule'] = invalid
    with pytest.raises(TaskCardError, match='material_rule'):
        generate_task_card(definition, 'ticket', inputs={'items': ['一つ']})
    schema = json.loads((ROOT / 'schemas/task-definition.schema.json').read_text(encoding='utf-8'))
    errors = list(Draft202012Validator(schema).iter_errors(definition))
    assert any(list(error.absolute_path) == ["card", "material_rule"] for error in errors)


def test_unspecified_material_rule_preserves_exact_existing_card():
    definition = llm_task()
    card = generate_task_card(definition, 'ticket', inputs={'items': ['一つ']})
    assert card == """# タスク ticket

## あなたの役割
与えられた項目だけを使って、応答を書く。

## 入力
### 項目
- 一つ

## 手順
1. 項目を一つずつ確認する。
2. JSONを出力する。
作り方の指示は本文に書かず、指定された処理を行うためだけに使う。

## 出力形式
次のJSONだけを出力すること。前後に説明を書かないこと。
{"text": "..."}

## 守ること
- 入力に書かれていないことを付け加えない。
- 出力形式以外の文章を書かない。
- 日本語で書く（ID・列挙値を除く）。"""
    explicit = deepcopy(definition)
    explicit['card']['material_rule'] = '入力に書かれていないことを付け加えない。'
    assert generate_task_card(explicit, 'ticket', inputs={'items': ['一つ']}) == card
    assert card.endswith('## 守ること\n- 入力に書かれていないことを付け加えない。\n- 出力形式以外の文章を書かない。\n- 日本語で書く（ID・列挙値を除く）。')


def test_s7_material_rule_replaces_only_one_footer_line():
    definition = _load_definition()
    source = event_source_for_card({'index': ('e001', 'what')}, 'S6.expand', {'slots': [_slot()]})
    inputs = resolve_inputs(definition, outputs={'S6.expand': source}, index=('0', 'what'))
    inputs['conditions'] = [{'text': '灯りを消したときだけ水門が開く。'}]
    original = deepcopy(inputs)
    card = generate_task_card(definition, 'ticket', inputs=inputs, retry_reason='主語を維持する。')
    default = deepcopy(definition)
    del default['card']['material_rule']
    default_card = generate_task_card(default, 'ticket', inputs=inputs, retry_reason='主語を維持する。')
    rule = definition['card']['material_rule']
    assert card == default_card.replace('- 入力に書かれていないことを付け加えない。', '- ' + rule)
    assert card.count(rule) == 1
    assert '入力に書かれていないことを付け加えない。' not in card
    assert '灯りを消したときだけ水門が開く。' in card
    assert inputs == original
    assert definition['version'] == 17
