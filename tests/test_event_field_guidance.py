from __future__ import annotations

import pytest

from storyteller.cards import generate_task_card
from storyteller.selectors import resolve_inputs
from storyteller.story_s7 import EVENT_FIELDS, EVENT_LABELS, event_source_for_card
from tests.test_story_s7 import _load_definition, _slot
from tests.test_story_input_budgets import _assert_fits


@pytest.mark.parametrize('field', EVENT_FIELDS)
def test_only_selected_field_scope_is_card_instruction(field):
    definition = _load_definition()
    source = {'slots': [_slot()]}
    bound = event_source_for_card({'index': ('e001', field)}, 'S6.expand', source)
    assert source == {'slots': [_slot()]}
    inputs = resolve_inputs(definition, outputs={'S6.expand': bound}, index=('0', field))
    inputs['conditions'] = [{'text': '灯りを消したときだけ水門が開く。'}]
    inputs['previous_result'] = '閉じた水門の前まで到着した。'
    inputs['decided_fields'] = {'何が起きたか': 'カナは灯りを消す。'}
    assert inputs['event_field'] == EVENT_LABELS[field]
    scopes = _scopes(definition)
    assert 'event_field_instruction' not in inputs
    card = generate_task_card(definition, 'ticket', inputs=inputs)
    assert '作り方の指示：' + scopes[field] in card
    for step in definition['card']['steps']:
        if step.partition('：')[0] not in EVENT_FIELDS:
            assert step in card
    for other in EVENT_FIELDS:
        if other != field:
            assert scopes[other] not in card
    assert '灯りを消したときだけ水門が開く。' in card
    assert '閉じた水門の前まで到着した。' in card
    assert definition['version'] == 13
    assert 'event_subject' in definition['validate']['checks']


@pytest.mark.parametrize('field', EVENT_FIELDS)
def test_selected_scope_fits_full_event_budget_with_retry(field):
    slot = _slot()
    fitted = _assert_fits('S7.event', {
        'event_field': EVENT_LABELS[field],
        'stage_definition': slot['stage']['definition'],
        'stage_guidance': slot['stage']['guidance'],
        'required_events': slot['required_events'],
        'absent_role_note': None, 'object': slot['object'],
        'characters': [dict(slot['characters'][0], id=f'c{i}', voice='声' * 1500) for i in range(3)],
        'world_sections': [dict(section, body='面' * 12000) for section in slot['world_sections']],
        'previous_result': '結' * 120, 'conflict': slot['plot']['conflict'],
        'climax': slot['plot']['climax'], 'theme': slot['theme'],
        'decided_fields': {EVENT_LABELS[prior]: '記' * 120 for prior in EVENT_FIELDS[:EVENT_FIELDS.index(field)]},
        'conditions': [{'text': '灯りを消したときだけ水門が開く。'}],
    })
    assert fitted['conditions'] == [{'text': '灯りを消したときだけ水門が開く。'}]
    assert fitted['previous_result'] == '結' * 120


def test_old_version_cannot_share_event_cache_key(tmp_path):
    from copy import deepcopy
    from storyteller.cards import prepare_task_inputs
    from storyteller.orchestrator import Orchestrator
    from tests.test_story_s7_elements import _harness

    harness, run = _harness(tmp_path)
    claim = harness.claim_next(run, executor_id='test')
    assert claim is not None
    manifest = harness.load_run(run)
    task = harness.validate_claim(claim['ticket'])['task_id']
    context = harness._build_context(manifest, run, task)
    fitted = prepare_task_inputs(context.definition, context.inputs)
    assert 'event_field_instruction' not in fitted
    old_definitions = deepcopy(harness.task_definitions)
    old_definitions['S7.event']['version'] = 11
    old_harness = Orchestrator(tmp_path, old_definitions)
    assert old_harness._cache_key_for_task(manifest, task, fitted) != harness._cache_key_for_task(manifest, task, fitted)


def _scopes(definition):
    return {key: body for step in definition['card']['steps']
            for key, separator, body in [step.partition('：')]
            if separator and key in EVENT_FIELDS}


@pytest.mark.parametrize('field', EVENT_FIELDS)
@pytest.mark.parametrize('fault', ['missing', 'duplicate'])
def test_all_field_scope_keys_are_validated(field, fault):
    from storyteller.event_fields import selected_event_steps
    definition = _load_definition()
    steps = definition['card']['steps']
    selected = next(step for step in steps if step.startswith(field + '：'))
    if fault == 'missing':
        steps.remove(selected)
    else:
        steps.append(selected)
    with pytest.raises(ValueError, match=fault):
        selected_event_steps(definition, {'event_field': EVENT_LABELS['what']})


def test_unknown_requested_field_is_rejected():
    from storyteller.event_fields import selected_event_steps
    with pytest.raises(ValueError, match='unknown event_field'):
        selected_event_steps(_load_definition(), {'event_field': 'その他'})
