from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from storyteller.cards import generate_task_card, prepare_task_inputs
from storyteller.selectors import resolve_inputs
from storyteller.story_s7 import EVENT_FIELDS, event_source_for_card
from tests.test_story_s7 import _load_definition, _slot


def _previous_definition():
    return yaml.safe_load((Path(__file__).parent / 'fixtures/s7-event-v17.yaml').read_text(encoding='utf-8'))


@pytest.mark.parametrize('field', EVENT_FIELDS)
def test_context_does_not_require_repeating_whole_event_in_every_field(field):
    definition = _load_definition()
    previous = _previous_definition()
    assert definition['version'] == 18 and previous['version'] == 17
    assert definition['inputs'] == previous['inputs']
    assert definition['validate'] == previous['validate']
    assert definition['card']['input_order'] == previous['card']['input_order']
    assert definition['card']['material_rule'] == previous['card']['material_rule']
    for key in ('output', 'candidates', 'select_candidate', 'max_input_chars', 'max_invalidations', 'continuation'):
        assert definition[key] == previous[key]
    scopes = lambda d: [step for step in d['card']['steps'] if step.partition('：')[0] in EVENT_FIELDS]
    assert scopes(definition) == scopes(previous)
    slot = _slot()
    slot['absent_role_note'] = '調停する役は不在。'
    source = event_source_for_card({'index': ('e001', field)}, 'S6.expand', {'slots': [slot]})
    inputs = resolve_inputs(definition, outputs={'S6.expand': source}, index=('0', field))
    inputs.update(conditions=[{'text': '灯りを消したときだけ水門が開く。'}], previous_result='水門はまだ閉じている。')
    original = deepcopy(inputs)
    assert prepare_task_inputs(definition, inputs=inputs) == prepare_task_inputs(previous, inputs=inputs)
    card = generate_task_card(definition, 'ticket', inputs=inputs, retry_reason='指定項目だけを書く。')
    assert '必須の出来事を指定項目で満たす' not in card
    assert '指定項目内でその条件・葛藤・解決すべき主題を反映する' not in card
    assert '段階の定義・指針・必須出来事は文脈。' in card
    assert '行動・変化は当段階の働きを満たし、他項目は矛盾させず専用範囲だけ書く。' in card
    assert '不在の役・象徴的事物・葛藤・主題は文脈。全項目での再述は不要。' in card
    assert '解決行動は当段階の要求時のみ。' in card
    for text in ('灯りを消したときだけ水門が開く。', '水門はまだ閉じている。', '調停する役は不在。', slot['required_events'][0]):
        assert text in card
    assert inputs == original


def test_v18_and_actual_v17_cache_keys_are_separate_with_identical_inputs(tmp_path):
    from storyteller.orchestrator import Orchestrator
    from tests.test_story_s7_elements import _harness

    harness, run = _harness(tmp_path)
    claim = harness.claim_next(run, executor_id='test')
    manifest = harness.load_run(run)
    task = harness.validate_claim(claim['ticket'])['task_id']
    context = harness._build_context(manifest, run, task)
    fitted = prepare_task_inputs(context.definition, inputs=context.inputs)
    old = deepcopy(harness.task_definitions)
    old['S7.event'] = _previous_definition()
    assert fitted == prepare_task_inputs(old['S7.event'], inputs=context.inputs)
    old_harness = Orchestrator(tmp_path, old)
    assert old_harness._cache_key_for_task(manifest, task, fitted) != harness._cache_key_for_task(manifest, task, fitted)
