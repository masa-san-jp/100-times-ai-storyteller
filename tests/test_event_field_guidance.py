from __future__ import annotations

from tests.support.legacy_harness import legacy_story_harness, task_path  # noqa: F401

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
    assert '書式例「' in scopes[field]
    assert '例の素材を本文に持ち込まず、入力内の素材だけを使う。' in scopes[field]
    for step in definition['card']['steps']:
        if step.partition('：')[0] not in EVENT_FIELDS:
            assert step in card
    for other in EVENT_FIELDS:
        if other != field:
            assert scopes[other] not in card
    assert '灯りを消したときだけ水門が開く。' in card
    assert '閉じた水門の前まで到着した。' in card
    assert definition['version'] == 21
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


def test_actual_legacy_v11_card_keeps_its_original_steps():
    from pathlib import Path
    import yaml
    from storyteller.event_fields import selected_event_steps

    definition = yaml.safe_load((Path(__file__).parent / 'fixtures/s7-event-v11.yaml').read_text(encoding='utf-8'))
    assert definition['version'] == 11
    source = event_source_for_card({'index': ('e001', 'what')}, 'S6.expand', {'slots': [_slot()]})
    inputs = resolve_inputs(definition, outputs={'S6.expand': source}, index=('0', 'what'))
    assert selected_event_steps(definition, inputs) is definition['card']['steps']
    card = generate_task_card(definition, 'legacy-ticket', inputs=inputs)
    for number, step in enumerate(definition['card']['steps'], start=1):
        assert f'{number}. {step}' in card


@pytest.mark.parametrize('version', [12, 13])
def test_scope_key_presence_requires_all_eight_even_on_old_version(version):
    from storyteller.event_fields import selected_event_steps
    definition = _load_definition()
    definition['version'] = version
    definition['card']['steps'] = ['what：' + _scopes(definition)['what']]
    with pytest.raises(ValueError, match='missing event field instruction'):
        selected_event_steps(definition, {'event_field': EVENT_LABELS['what']})


@pytest.mark.parametrize('version', [13, 14, 15])
def test_current_version_cannot_bypass_checks_by_removing_every_scope(version):
    from storyteller.event_fields import selected_event_steps
    definition = _load_definition()
    definition['version'] = version
    definition['card']['steps'] = [step for step in definition['card']['steps'] if step.partition('：')[0] not in EVENT_FIELDS]
    with pytest.raises(ValueError, match='missing event field instruction'):
        selected_event_steps(definition, {'event_field': EVENT_LABELS['what']})


@pytest.mark.parametrize('field', EVENT_FIELDS)
def test_local_progress_uses_existing_materials_without_planning_other_stages(field):
    definition = _load_definition()
    source = event_source_for_card({'index': ('e001', field)}, 'S6.expand', {'slots': [_slot()]})
    inputs = resolve_inputs(definition, outputs={'S6.expand': source}, index=('0', field))
    inputs['previous_result'] = '閉じた水門の前まで到着した。'
    inputs['conditions'] = [{'text': '灯りを消したときだけ水門が開く。'}]
    card = generate_task_card(definition, 'progress', inputs=inputs)
    assert '当段階の局所行動・変化を具体化する' in card
    assert '根拠のない人物・物・設定・外部の出来事を加えず' in card
    assert '条件を変更せず、前後の段階・全体を計画しない' in card
    assert '閉じた水門の前まで到着した。' in card
    assert '灯りを消したときだけ水門が開く。' in card
    scope = _scopes(definition)['what']
    assert ('既存素材への観察・扱い・照合・選択' in card) == (field == 'what')
    assert ('固定条件の発動を毎回再実行しない' in card) == (field == 'what')
    assert ('作り方の指示：' + scope in card) == (field == 'what')


def test_previous_v14_definition_keeps_selected_scope_and_separate_cache(tmp_path):
    from copy import deepcopy
    from storyteller.cards import prepare_task_inputs
    from storyteller.event_fields import selected_event_steps
    from storyteller.orchestrator import Orchestrator
    from tests.test_story_s7_elements import _harness

    harness, run = _harness(tmp_path)
    claim = harness.claim_next(run, executor_id='test')
    manifest = harness.load_run(run)
    task = harness.validate_claim(claim['ticket'])['task_id']
    context = harness._build_context(manifest, run, task)
    fitted = prepare_task_inputs(context.definition, context.inputs)
    old_definitions = deepcopy(harness.task_definitions)
    from pathlib import Path
    import yaml
    old_definitions['S7.event'] = yaml.safe_load((Path(__file__).parent / 'fixtures/s7-event-v14.yaml').read_text(encoding='utf-8'))
    old_harness = Orchestrator(tmp_path, old_definitions)
    assert old_harness._cache_key_for_task(manifest, task, fitted) != harness._cache_key_for_task(manifest, task, fitted)
    steps = selected_event_steps(old_definitions['S7.event'], fitted)
    assert len(steps) == len(old_definitions['S7.event']['card']['steps']) - 7
    old_card = generate_task_card(old_definitions['S7.event'], 'legacy-v14', inputs=fitted)
    old_scopes = _scopes(old_definitions['S7.event'])
    assert old_scopes['what'] in old_card
    assert all(old_scopes[field] not in old_card for field in EVENT_FIELDS if field != 'what')
