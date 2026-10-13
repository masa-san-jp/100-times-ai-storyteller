from __future__ import annotations

from copy import deepcopy

import pytest

from storyteller.cards import TaskCardError, generate_task_card, input_char_count, prepare_task_inputs
from storyteller.selectors import resolve_inputs
from storyteller.story_s7 import event_source_for_card
from tests.test_cards import llm_task
from tests.test_story_s7 import _load_definition, _slot


@pytest.mark.parametrize('order, message', [
    (None, 'string array'), ('items', 'string array'), ([1, 'context'], 'string array'),
    (['items', 'items'], 'duplicate'), (['items'], 'missing'),
    (['items', 'context', 'extra'], 'unknown'), ([], 'missing'),
])
def test_invalid_input_order_is_rejected(order, message):
    definition = llm_task()
    definition['card']['input_order'] = order
    with pytest.raises(TaskCardError, match=message):
        generate_task_card(definition, 'ticket', inputs={'items': ['一つ']})


def test_unspecified_and_definition_order_are_exactly_identical():
    definition = llm_task()
    inputs = {'items': ['一つ'], 'context': '文脈'}
    original = generate_task_card(definition, 'ticket', inputs=inputs)
    ordered = deepcopy(definition)
    ordered['card']['input_order'] = list(definition['inputs'])
    assert generate_task_card(ordered, 'ticket', inputs=inputs) == original


def test_fit_keeps_original_priority_even_when_grouped_display_size_changes():
    definition = llm_task(id='S7.example', output='text', max_input_chars=120)
    definition['inputs'] = {
        'object': {'label': '物', 'required': True, 'truncate': 'head'},
        'stage_guidance': {'label': '指針', 'required': True},
        'world_sections': {'label': '世界', 'truncate': 'drop'},
        'stage_definition': {'label': '段階', 'required': True},
        'previous_result': {'label': '直前', 'truncate': 'drop'},
    }
    inputs = {'object': '物' * 300, 'stage_guidance': '指針', 'world_sections': '世' * 300,
              'stage_definition': '段階', 'previous_result': '結' * 300}
    ordered = deepcopy(definition)
    ordered['card']['input_order'] = ['world_sections', 'previous_result', 'object', 'stage_definition', 'stage_guidance']
    from storyteller.cards import _render_input_body
    assert len(_render_input_body(definition, inputs)) != len(_render_input_body(ordered, inputs))
    original = deepcopy(inputs)
    fitted = prepare_task_inputs(definition, inputs=inputs)
    reordered = prepare_task_inputs(ordered, inputs=inputs)
    assert fitted == reordered
    assert inputs == original
    assert 'previous_result' not in fitted and 'world_sections' not in fitted
    assert len(fitted['object']) < 300
    assert input_char_count(definition, fitted) == input_char_count(ordered, fitted)
    assert generate_task_card(definition, 'ticket', inputs=fitted) != generate_task_card(ordered, 'ticket', inputs=fitted)


def test_s7_displays_spec_order_without_changing_slot_definition_or_values():
    definition = _load_definition()
    expected = ['conditions', 'previous_result', 'object', 'characters', 'world_sections', 'conflict',
                'climax', 'theme', 'glossary', 'event_field', 'decided_fields', 'stage_definition',
                'stage_guidance', 'required_events', 'absent_role_note']
    assert definition['card']['input_order'] == expected
    assert list(definition['inputs'])[0] == 'stage_definition'
    source = event_source_for_card({'index': ('e001', 'what')}, 'S6.expand', {'slots': [_slot()]})
    inputs = resolve_inputs(definition, outputs={'S6.expand': source}, index=('0', 'what'))
    inputs.update(conditions=[{'text': '灯りを消したときだけ水門が開く。'}], previous_result='閉じた水門の前まで来た。')
    original = deepcopy(inputs)
    default = deepcopy(definition)
    del default['card']['input_order']
    assert prepare_task_inputs(definition, inputs=inputs) == prepare_task_inputs(default, inputs=inputs)
    card = generate_task_card(definition, 'ticket', inputs=inputs)
    positions = [card.index('#### ' + definition['inputs'][name]['label']) for name in expected if name in inputs]
    assert positions == sorted(positions)
    assert '#### 関係する用語集' not in card
    assert inputs == original
    assert definition['version'] == 19


@pytest.mark.parametrize('order', [None, 'items', [1], ['items', 'items']])
def test_schema_rejects_invalid_input_order_shapes(order):
    import json
    from jsonschema import Draft202012Validator
    from tests.test_story_s7 import ROOT

    definition = llm_task()
    definition['card']['input_order'] = order
    schema = json.loads((ROOT / 'schemas/task-definition.schema.json').read_text(encoding='utf-8'))
    assert any(list(error.absolute_path)[:2] == ['card', 'input_order']
               for error in Draft202012Validator(schema).iter_errors(definition))


def test_v17_display_order_changes_cache_key_without_changing_fitted_values(tmp_path):
    from storyteller.orchestrator import Orchestrator
    from tests.test_story_s7_elements import _harness

    harness, run = _harness(tmp_path)
    claim = harness.claim_next(run, executor_id='test')
    manifest = harness.load_run(run)
    task = harness.validate_claim(claim['ticket'])['task_id']
    context = harness._build_context(manifest, run, task)
    fitted = prepare_task_inputs(context.definition, inputs=context.inputs)
    old = deepcopy(harness.task_definitions)
    old['S7.event']['version'] = 16
    del old['S7.event']['card']['input_order']
    assert fitted == prepare_task_inputs(old['S7.event'], inputs=context.inputs)
    old_harness = Orchestrator(tmp_path, old)
    assert old_harness._cache_key_for_task(manifest, task, fitted) != harness._cache_key_for_task(manifest, task, fitted)


@pytest.mark.parametrize('budget_delta, accepted', [(-1, False), (0, True), (1, True)])
def test_display_heading_increase_is_checked_without_refitting_values(budget_delta, accepted):
    from storyteller.cards import InputBudgetError, _char_len, _render_input_body

    definition = {
        'id': 'S.test', 'version': 1, 'kind': 'llm', 'element': 'text', 'output': 'text',
        'max_input_chars': 100,
        'inputs': {key: {'label': key, 'required': True, 'truncate': truncate}
                   for key, truncate in [('body', 'head'), ('glossary', 'none'), ('stage_guidance', 'none')]},
        'card': {'role': 'r', 'steps': ['s'], 'input_order': ['body', 'stage_guidance', 'glossary']},
    }
    inputs = {'body': 'あ' * 80, 'glossary': {'name': '用語', 'text': '定義'}, 'stage_guidance': '指針'}
    fitted = prepare_task_inputs(definition, inputs=inputs)
    assert input_char_count(definition, fitted) == 100
    display_size = _char_len(_render_input_body(definition, fitted))
    assert display_size == 110
    original = deepcopy(fitted)
    definition['max_input_chars'] = display_size + budget_delta
    assert prepare_task_inputs(definition, inputs=fitted) == original
    if accepted:
        card = generate_task_card(definition, 'ticket', inputs=fitted)
        assert _render_input_body(definition, fitted) in card
    else:
        with pytest.raises(InputBudgetError, match='入力が予算を超える'):
            generate_task_card(definition, 'ticket', inputs=fitted)
    assert fitted == original


def test_original_fit_can_succeed_while_actual_display_is_rejected():
    from storyteller.cards import InputBudgetError

    definition = {
        'id': 'S.test', 'version': 1, 'kind': 'llm', 'element': 'text', 'output': 'text',
        'max_input_chars': 100,
        'inputs': {key: {'label': key, 'required': True, 'truncate': truncate}
                   for key, truncate in [('body', 'head'), ('glossary', 'none'), ('stage_guidance', 'none')]},
        'card': {'role': 'r', 'steps': ['s'], 'input_order': ['body', 'stage_guidance', 'glossary']},
    }
    inputs = {'body': 'あ' * 80, 'glossary': {'name': '用語', 'text': '定義'}, 'stage_guidance': '指針'}
    original = deepcopy(inputs)
    before = prepare_task_inputs(definition, inputs=inputs)
    with pytest.raises(InputBudgetError, match='入力が予算を超える'):
        generate_task_card(definition, 'ticket', inputs=inputs)
    assert prepare_task_inputs(definition, inputs=inputs) == before
    assert inputs == original
