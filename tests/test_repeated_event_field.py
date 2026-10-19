from tests.support.legacy_harness import legacy_story_harness, task_path  # noqa: F401
from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml

from storyteller.orchestrator import Orchestrator
from storyteller.repair import generate_repair_card
from storyteller.validation import validate_output, ValidationConfigurationError
from tests.test_story_s7 import _load_definition
from storyteller.event_fields import EVENT_FIELDS, EVENT_LABELS

ROOT = Path(__file__).resolve().parents[1]


def inputs(what, field='result'):
    return {'event_field': EVENT_LABELS[field], 'decided_fields': {'何が起きたか': what},
            'characters': [{'name': 'カナ'}]}


def check(output, what, field='result'):
    d = {'output': 'text', 'element': 'text', 'validate': {'checks': ['no_repeated_event_field']}}
    return validate_output(d, output, inputs=inputs(what, field))


@pytest.mark.parametrize('output,what', [('Ａ は 紙を開く。', 'Aは紙を開く。'), ('カナは紙を開く。', 'カナは紙を開く。')])
def test_exact_nfkc_whitespace_copy_is_repairable_without_mutation(output, what):
    result = check(output, what)
    assert not result.passed and result.repairable and result.value == output


@pytest.mark.parametrize('what,output,field', [('', '本文', 'result'), ('  ', '本文', 'result'),
    (None, '本文', 'result'), ('カナは紙を開く。', 'カナは紙を開く。', 'what'),
    ('カナは紙を開く。', 'カナは紙を開く。続き。', 'result'),
    ('カナは紙を開く。', '紙を開く。', 'where'), ('カナは紙を開く。', 'カナは紙の穴を見つけた。', 'result')])
def test_noncopy_missing_and_what_are_allowed(what, output, field):
    assert check(output, what, field).passed


@pytest.mark.parametrize('length', [50, 75])
def test_public_submit_repair_and_resubmit_preserve_original(tmp_path, length):
    what = 'カナは薄い和紙を机の上に広げ、端の印を指でなぞって二つの図を重ね、欠けていた線のつながりを確かめた。'
    if length == 75:
        what += '再び印の向きをそろえ、重なった線を紙の端に写した。'
    assert len(what) == length
    d = _load_definition()
    d['card'].pop('input_order')
    # Resolve synthetic inputs through the public input mechanism.
    d['inputs'] = {k: {'label': k, 'from': 'input', 'select': k, 'required': True}
                   for k in inputs(what)}
    h = Orchestrator(tmp_path, {'S7.event': d}, harness_root=ROOT)
    run = h.create_run(seed=7, input_data=inputs(what), tasks={'S7.event': {'type': 'S7.event'}})
    claim = h.claim_next(run, executor_id='test')
    submitted = h.submit(claim['ticket'], what)
    assert not submitted.accepted
    assert any(e.startswith('no_repeated_event_field:') for e in submitted.errors)
    assert any(e.startswith('no_copy_from_inputs:') for e in submitted.errors) == (length >= 60)
    record = json.loads((h.task_dir(run, 'S7.event') / 'attempts/1.json').read_text(encoding='utf-8'))
    assert record['output'] == what and record['repairable'] is True
    repair = h.claim_next(run, executor_id='test')
    assert what in repair['card']
    scopes = {s.partition('：')[0]: s.partition('：')[2] for s in d['card']['steps'] if s.partition('：')[0] in EVENT_FIELDS}
    assert '作り方の指示：' + scopes['result'] in repair['card']
    assert all(scopes[f] not in repair['card'] for f in EVENT_FIELDS if f != 'result')
    assert h.submit(repair['ticket'], 'カナは紙の穴から向こう側が見える状態になった。').accepted
    assert json.loads((h.task_dir(run, 'S7.event') / 'attempts/1.json').read_text(encoding='utf-8')) == record


def test_normal_repair_card_is_exactly_unchanged():
    d = _load_definition()
    old = deepcopy(d)
    old['validate']['checks'].remove('no_repeated_event_field')
    kwargs = dict(original_output='カナは紙を開く。', reason='min_chars: 不足', inputs=inputs('カナは紙を開く。'))
    assert generate_repair_card(d, 'ticket', **kwargs) == generate_repair_card(old, 'ticket', **kwargs)


def test_json_and_arguments_are_configuration_errors():
    with pytest.raises(ValidationConfigurationError):
        validate_output({'output':'json', 'validate':{'checks':['no_repeated_event_field']}}, '{}')
    with pytest.raises(ValidationConfigurationError):
        validate_output({'output':'text', 'validate':{'checks':[{'no_repeated_event_field':{'n':1}}]}}, '本文')


def test_old_v20_validation_and_cache_remain_separate(tmp_path):
    old = yaml.safe_load((ROOT / 'tests/fixtures/s7-event-v20.yaml').read_text(encoding='utf-8'))
    new = _load_definition()
    assert old['version'] == 20 and new['version'] == 21
    expected = deepcopy(old)
    expected['version'] = 21
    expected['validate']['checks'].insert(2, 'no_repeated_event_field')
    assert new == expected
    assert validate_output(old, 'カナは紙を開く。', inputs=inputs('カナは紙を開く。'), harness_root=ROOT).passed
    assert not validate_output(new, 'カナは紙を開く。', inputs=inputs('カナは紙を開く。'), harness_root=ROOT).passed
    from storyteller.cache import cache_key
    assert cache_key('S7.event', 20, inputs('本文'), seed=1) != cache_key('S7.event', 21, inputs('本文'), seed=1)


def test_copy_overlap_exception_does_not_broaden_other_failures():
    what = 'カナは紙を開き、' + '印' * 65 + '。'
    d = _load_definition()
    alone = deepcopy(d)
    alone['validate']['checks'].remove('no_repeated_event_field')
    assert not validate_output(alone, what, inputs=inputs(what), harness_root=ROOT).repairable
    other = inputs(what)
    other['characters'] = [{'name': 'ミナ'}]
    result = validate_output(d, what, inputs=other, harness_root=ROOT)
    assert any(e.startswith('event_subject:') for e in result.errors)
    assert any(e.startswith('no_copy_from_inputs:') for e in result.errors)
    assert any(e.startswith('no_repeated_event_field:') for e in result.errors)
    assert not result.repairable


def test_what_repair_never_receives_field_scope():
    d = _load_definition()
    card = generate_repair_card(d, 'ticket', original_output='本文',
        reason='no_repeated_event_field: 合成理由', inputs=inputs('本文', 'what'))
    assert '作り方の指示：' not in card


@pytest.mark.parametrize('sentinel', ['（未定）', '(未定)', ' （ 未定 ） '])
def test_all_skipped_candidates_produce_unknown_what_context(sentinel):
    from types import SimpleNamespace
    from storyteller.story_s7_candidates import story_s7_select
    from storyteller.story_s7 import event_source_for_card
    selected = story_s7_select(SimpleNamespace(inputs={'candidates': {}})).output
    assert selected['text'] == '（未定）' and selected['candidate'] is None
    selected['text'] = sentinel
    context = event_source_for_card({'index': ('e001', 'result')}, 'S7.event', {},
        {'S7.select-e001': selected})
    assert context['何が起きたか'] == sentinel
    result = check(sentinel, context['何が起きたか'])
    baseline = validate_output({'output': 'text', 'element': 'text'}, sentinel)
    assert result.passed and result.value == baseline.value
