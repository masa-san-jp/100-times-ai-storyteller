from __future__ import annotations

from copy import deepcopy

import pytest

from storyteller.cache import save_cache
from storyteller.cards import generate_task_card, prepare_task_inputs
from storyteller.orchestrator import Orchestrator
from storyteller.selectors import resolve_inputs
from storyteller.story_s7 import EVENT_FIELDS, event_source_for_card
from tests.test_story_input_budgets import _assert_fits, _definition
from tests.test_story_s7 import _event, _slot


PREVIOUS_RULES = {
    'S7.event': '固定の発動・禁止・解決条件は不変の因果制約であり、今の段階ですべて発動・解決する命令ではない。条件を追加・反転・置換しない。内部ID・構造の説明を書かない。',
    'S7.detail': '確定した発動・禁止・解決条件を守り、入力にない条件を追加・反転・置換しない。本文に内部 ID と構造の説明を書かない。',
}


def _inputs(task, field='what'):
    definition = _definition(task)
    slot = _slot()
    if task == 'S7.event':
        source = event_source_for_card({'index': ('e001', field)}, 'S6.expand', {'slots': [slot]})
        inputs = resolve_inputs(definition, outputs={'S6.expand': source}, index=('0', field))
    else:
        inputs = {**_event(), 'characters': slot['characters'], 'world_sections': slot['world_sections'],
                  'beat': {'id': 'opening', 'name': '発端', 'definition': '発端を示す。'},
                  'previous_scene': 'カナは封書を受け取った。'}
    inputs['conditions'] = [{'text': '封書を破いてはならない。'}]
    return inputs


@pytest.mark.parametrize('field', EVENT_FIELDS)
def test_every_event_field_card_explicitly_prohibits_the_action(field):
    definition = _definition('S7.event')
    inputs = _inputs('S7.event', field)
    before = deepcopy(inputs)
    card = generate_task_card(definition, 'ticket', inputs=inputs)
    assert '禁止行為を実施しない。' in card
    assert '封書を破いてはならない。' in card
    assert 'すべて発動・解決する命令ではない' in card
    assert '条件を追加・反転・置換せず' in card
    assert '内部ID・構造説明を書かない' in card
    assert inputs == before
    rule = next(step for step in definition['card']['steps'] if step.startswith('発動・禁止'))
    assert len(rule) <= len(PREVIOUS_RULES['S7.event'])
    assert definition['version'] == 21


def test_detail_prohibition_and_maximum_retry_budget():
    definition = _definition('S7.detail')
    inputs = _inputs('S7.detail')
    # Use the existing maximal continuation and retry reservation assertion.
    fitted = _assert_fits('S7.detail', {
        **inputs,
        'characters': [dict(_slot()['characters'][0], id=f'c{i}', voice='声' * 1500) for i in range(3)],
        'world_sections': [dict(section, body='面' * 12000) for section in _slot()['world_sections']],
        'previous_scene': '前' * 600,
    })
    assert fitted['conditions'] == inputs['conditions']
    card = generate_task_card(definition, 'ticket', inputs=inputs, retry_reason='理' * 500)
    assert '禁止行為を実施しない。' in card
    assert '封書を破いてはならない。' in card
    assert '条件を追加・反転・置換せず' in card
    assert '内部ID・構造説明を書かない' in card
    assert len(definition['card']['steps'][0]) <= len(PREVIOUS_RULES['S7.detail'])
    assert definition['version'] == 13


@pytest.mark.parametrize('task', ['S7.event', 'S7.detail'])
def test_old_version_cache_cannot_replace_new_prohibition_card(tmp_path, task):
    definition = _definition(task)
    for key, slot in definition['inputs'].items():
        slot.update({'from': 'input', 'select': key})
    harness = Orchestrator(tmp_path, {task: definition})
    run = harness.create_run(seed=15, input_data=_inputs(task))
    manifest = harness.advance(run)
    context = harness._build_context(manifest, run, task)
    fitted = prepare_task_inputs(context.definition, inputs=context.inputs)
    previous = deepcopy(definition)
    previous['version'] -= 1
    rule_index = next(i for i, step in enumerate(previous['card']['steps']) if '禁止行為を実施しない。' in step)
    previous['card']['steps'][rule_index] = PREVIOUS_RULES[task]
    assert prepare_task_inputs(previous, inputs=context.inputs) == fitted
    old_harness = Orchestrator(tmp_path, {task: previous})
    old_key = old_harness._cache_key_for_task(manifest, task, fitted)
    new_key = harness._cache_key_for_task(manifest, task, fitted)
    assert old_key != new_key
    save_cache(tmp_path, old_key, '古い出力。')
    claim = harness.claim_next(run, executor_id='test')
    assert claim is not None
    assert '禁止行為を実施しない。' in claim['card']
    assert harness.load_run(run)['tasks'][task]['state'] == 'claimed'
    assert not (harness.task_dir(run, task) / 'output.txt').exists()
