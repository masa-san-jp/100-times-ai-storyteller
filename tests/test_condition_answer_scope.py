from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from storyteller.cards import TaskCardError, generate_task_card
from storyteller.plot_quality import CONDITION_KINDS, CONDITION_LABELS, selected_condition_steps
from storyteller.validation import load_and_validate_yaml

ROOT = Path(__file__).parents[1]


def definition():
    return load_and_validate_yaml(ROOT / 'harness/story/tasks/S7.condition.yaml',
                                  ROOT / 'schemas/task-definition.schema.json')


def inputs(kind='trigger'):
    return {'condition_kind': CONDITION_LABELS[kind],
            'object': {'text': '封印された帳簿'}, 'world_sections': [],
            'conflict': '記録を開くか保護するか', 'taboo': {'text': '破ってはならない'},
            'decided_conditions': ['帳簿を開くと光が消える。']}


@pytest.mark.parametrize('kind', CONDITION_KINDS)
def test_card_shows_exactly_one_yaml_scope_and_keeps_inputs_and_common_steps(kind):
    task = definition()
    original = deepcopy(task)
    source = inputs(kind)
    original_inputs = deepcopy(source)
    card = generate_task_card(task, 'ticket', inputs=source)
    for step in task['card']['steps']:
        key, separator, body = step.partition('：')
        if key in CONDITION_KINDS and separator:
            assert ('作り方の指示：' + body in card) is (key == kind)
        else:
            assert step in card
    assert '帳簿を開くと光が消える。' in card
    assert source == original_inputs and task == original
    rule = task['card']['material_rule']
    assert card.count(rule) == 1
    assert '入力に書かれていないことを付け加えない。' not in card
    assert CONDITION_LABELS[kind] in card
    assert all(key + '：' not in card for key in CONDITION_KINDS)


def test_condition_semantics_are_owned_by_yaml():
    task = definition()
    task['card']['steps'] = ['共有手順', 'trigger：発動専用', 'prohibition：禁止専用', 'resolution：解決専用']
    assert selected_condition_steps(task, inputs('prohibition')) == ['共有手順', '作り方の指示：禁止専用']


@pytest.mark.parametrize('mutation,error', [
    ('missing', 'missing condition'), ('duplicate', 'duplicate condition'),
    ('empty', 'empty condition'), ('unknown', 'unknown condition_kind'),
    ('no-scopes', 'missing condition'),
])
def test_new_condition_cards_reject_invalid_scopes(mutation, error):
    task = definition()
    source = inputs()
    if mutation == 'missing':
        task['card']['steps'] = [step for step in task['card']['steps'] if not step.startswith('resolution：')]
    elif mutation == 'duplicate':
        task['card']['steps'].append('trigger：二つ目')
    elif mutation == 'empty':
        task['card']['steps'] = [step if not step.startswith('trigger：') else 'trigger：  ' for step in task['card']['steps']]
    elif mutation == 'unknown':
        source['condition_kind'] = 'trigger'
    else:
        task['card']['steps'] = ['共通手順だけ']
    with pytest.raises(TaskCardError, match=error):
        generate_task_card(task, 'ticket', inputs=source)


def test_old_v1_without_scope_keys_preserves_exact_old_steps_and_card(monkeypatch):
    task = definition()
    task['version'] = 1
    del task['card']['material_rule']
    steps = [
        '対象の事物・世界・葛藤・禁忌から、指定された条件1つだけを具体的に書く。',
        '先に確定した条件を変更せず、それと両立する条件にする。入力にない人物・固有名詞を加えない。',
        '何をすると何が起こるか、または何が禁じられどの帰結になるかを1文で書く。',
        '本文だけを160字以内で書く。内部 ID や構造の説明を書かない。',
    ]
    task['card']['steps'] = steps
    assert selected_condition_steps(task, {'condition_kind': 'old unknown label'}) is steps
    card = generate_task_card(task, 'ticket', inputs=inputs())
    # Compare against the original direct card.steps renderer path, retaining
    # the same task ID because it also controls input grouping.
    monkeypatch.setattr('storyteller.cards.selected_condition_steps', lambda definition, source: definition['card']['steps'])
    assert card == generate_task_card(task, 'ticket', inputs=inputs())
    assert '\n'.join(f'{n}. {step}' for n, step in enumerate(steps, 1)) in card


def test_condition_contract_and_validation_are_unchanged():
    task = definition()
    assert task['version'] == 2
    assert set(task['inputs']) == {'condition_kind', 'object', 'world_sections', 'conflict', 'taboo', 'decided_conditions'}
    assert task['element'] == task['output'] == 'text'
    assert task['max_input_chars'] == 2800 and task['on_exhausted'] == 'skip'
    assert task['validate']['checks'] == [
        'no_copy_from_inputs', {'avoid_listed': {'table': 'tables/meta_terms.yaml'}},
        'no_story_metadata', {'min_chars': {'n': 1}}, {'max_chars': {'n': 160}},
    ]


def test_shared_action_with_a_different_condition_role_is_not_blanket_rejected():
    from storyteller.validation import validate_output
    task = definition()
    text = '帳簿を開いてはならず、開いたときは光が消えて記録を失う。'
    result = validate_output(task, text, inputs=inputs('prohibition'))
    assert result.passed, result.errors
