"""P1-42: non-mention is not negation; required conditions stay binding.

PROBE_CASES contains only synthetic public inputs for an independent real-model
probe. Expected answers are fixture expectations, never submitted model outputs.
"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from storyteller.cache import save_cache
from storyteller.cards import generate_task_card, input_char_count, prepare_task_inputs
from storyteller.orchestrator import Orchestrator
from storyteller.story_s8 import story_s8_detail_judge, story_s8_judge

ROOT = Path(__file__).resolve().parents[1]
PROBE_CASES = {
    'missing-premise': {'condition': '窓を開けると風鈴が鳴る。',
                        'body': '風鈴が鳴り、その音が庭へ届いた。', 'expected': 'no'},
    'missing-consequence': {'condition': '紙を乾かすと文字が読める。',
                            'body': '紙を乾かし、作業を終えた。', 'expected': 'no'},
    'necessary-condition': {'condition': '窓を開けなければ風鈴は鳴らない。',
                           'body': '窓を閉めたまま、風鈴が鳴った。', 'expected': 'yes'},
    'negated-consequence': {'condition': '窓を開けると風鈴が鳴る。',
                           'body': '窓を開けたが、風鈴は鳴らなかった。', 'expected': 'yes'},
    'forbidden-action': {'condition': '封書を破いてはならない。',
                         'body': '封書を破いた。', 'expected': 'yes'},
    'reversed-order': {'condition': '紙を乾かす前に帳簿を閉じてはならない。',
                       'body': '帳簿を閉じた後に紙を乾かした。', 'expected': 'yes'},
}


def definition(task):
    return yaml.safe_load((ROOT / f'harness/story/tasks/{task}.yaml').read_text(encoding='utf-8'))


def probe_inputs(task, case):
    if task == 'S8.compare':
        return {'comparison_kind': 'conditions',
                'event_a': {'comparison_kind': 'conditions', 'conditions': [case['condition']]},
                'event_b': {'id': 'e001', 'event': {'what': case['body']}}}
    return {'event': {'what': case['body'], 'conditions': [case['condition']]},
            'body': case['body'], 'previous': ''}


@pytest.mark.parametrize('task', ['S8.compare', 'S8.detail'])
def test_previous_judge_version_cache_does_not_replace_a_fresh_claim(tmp_path, task):
    d = definition(task)
    # Keep the real judge's card, validation, bounds and output schema; resolve
    # synthetic fixture inputs from this run instead of a full story DAG.
    for key, slot in d['inputs'].items():
        slot.update({'from': 'input', 'select': key})
    h = Orchestrator(tmp_path, {task: d})
    run = h.create_run(seed=15, input_data=probe_inputs(task, PROBE_CASES['missing-premise']))
    manifest = h.advance(run)
    context = h._build_context(manifest, run, task)
    fitted = prepare_task_inputs(context.definition, context.inputs)
    previous = deepcopy(d)
    previous['version'] -= 1
    old_harness = Orchestrator(tmp_path, {task: previous})
    old_key = old_harness._cache_key_for_task(manifest, task, fitted)
    new_key = h._cache_key_for_task(manifest, task, fitted)
    assert old_key != new_key
    save_cache(tmp_path, old_key, {'answer': 'yes', 'reason': '前提が欠けている。'})
    claim = h.claim_next(run, executor_id='test')
    assert claim is not None
    assert h.validate_claim(claim['ticket'])['task_id'] == task
    assert h.load_run(run)['tasks'][task]['state'] == 'claimed'
    assert not (h.task_dir(run, task) / 'output.json').exists()


@pytest.mark.parametrize('case_name', ['necessary-condition', 'negated-consequence',
                                     'forbidden-action', 'reversed-order'])
def test_genuine_condition_violation_yes_still_invalidates_event_and_beat(case_name):
    case = PROBE_CASES[case_name]
    reason = f'条件「{case["condition"]}」と本文「{case["body"]}」が食い違う。'
    event = story_s8_judge(SimpleNamespace(task_id='S8.judge-e002', task={'index': ['e002']},
        inputs={'comparisons': [{'answer': 'yes', 'reason': reason}]}))
    assert event.output['invalidated'] is True
    assert event.invalidations and all(reason in detail for _, detail in event.invalidations)
    beat = story_s8_detail_judge(SimpleNamespace(task={'index': ['e002', 'b1']},
        inputs={'judgement': {'e002-b1': {'answer': 'yes', 'reason': reason}}}))
    assert beat.invalidations == [('S7.detail-e002-b1', reason)]


@pytest.mark.parametrize('task', ['S8.compare', 'S8.detail'])
def test_public_missing_premise_and_genuine_violation_probe_cards_are_prepared(tmp_path, task):
    d = definition(task)
    for name, case in PROBE_CASES.items():
        inputs = probe_inputs(task, case)
        original = deepcopy(inputs)
        fitted = prepare_task_inputs(d, inputs)
        card = generate_task_card(d, f'probe-{name}', fitted)
        assert inputs == original and fitted == inputs
        assert input_char_count(d, fitted) <= d['max_input_chars']
        assert case['condition'] in card and case['body'] in card
        assert '非言及は否定ではなく' in card
        assert '唯一の必要条件を導かない' in card
        assert '必要条件・前後関係の追加・反転・置換' in card
        # These are inputs/cards for a future real-model probe, not an output.
        directory = tmp_path / task / name
        directory.mkdir(parents=True)
        (directory / 'input.json').write_text(json.dumps(inputs, ensure_ascii=False), encoding='utf-8')
        (directory / 'card.md').write_text(card, encoding='utf-8')
        assert not (directory / 'output.json').exists()


# Public synthetic event pairs for a real executor probe. These expectations do
# not simulate an LLM decision and no judgement is submitted by this test.
EVENT_REPLAY_CASES = {
    'joint-to-first': {'who': ['c1'], 'when': '朝', 'where': '書庫',
                       'what': 'カナは帳簿を開いた。', 'result': 'カナは記録を見つけた。', 'expected': 'yes'},
    'joint-to-second': {'who': ['c2'], 'when': '朝', 'where': '書庫',
                        'what': 'ミナは帳簿を開いた。', 'result': 'ミナは記録を見つけた。', 'expected': 'yes'},
    'independent-person': {'who': ['c3'], 'when': '翌朝', 'where': '別の書庫',
                           'what': 'リナは別の帳簿を開いた。', 'result': 'リナは別件の記録を見つけた。', 'expected': 'no'},
    'new-result': {'who': ['c1'], 'when': '朝', 'where': '書庫',
                   'what': 'カナは帳簿を開いた。', 'result': 'カナは記録から出口の位置を突き止めた。', 'expected': 'no'},
}


def event_replay_inputs(case_name):
    case = EVENT_REPLAY_CASES[case_name]
    return {'comparison_kind': 'events',
              'event_a': {'comparison_kind': 'events', 'id': 'e001', 'event': {
                  'who': ['c1', 'c2'], 'when': '朝', 'where': '書庫',
                  'what': 'カナとミナは帳簿を開いた。', 'result': 'カナとミナは記録を見つけた。'}},
              'event_b': {'id': 'e002', 'event': {k: v for k, v in case.items() if k != 'expected'}}}


@pytest.mark.parametrize('case_name', EVENT_REPLAY_CASES)
def test_joint_actor_replay_probe_preserves_pair_and_scope(tmp_path, case_name):
    d = definition('S8.compare')
    assert d['version'] == 11
    inputs = event_replay_inputs(case_name)
    original = deepcopy(inputs)
    fitted = prepare_task_inputs(d, inputs)
    assert inputs == fitted == original
    card = generate_task_card(d, 'joint-replay-probe', fitted)
    for event in (inputs['event_a']['event'], inputs['event_b']['event']):
        assert event['what'] in card and event['result'] in card
    for instruction in ('矛盾と再演を別に判定', '共同主体から一部主体への変更',
                        '主体差だけから独立性を補わない', '結果が進展する場合は再演としない'):
        assert instruction in card
    steps = d['card']['steps']
    replay = next(i for i, step in enumerate(steps) if step.startswith('events では矛盾と再演'))
    factual = next(i for i, step in enumerate(steps) if step.startswith('入力にない同時性'))
    final = next(i for i, step in enumerate(steps) if step.startswith('矛盾または進展'))
    assert factual < replay < final
    assert '主体差があっても再演で yes' in steps[replay]
    assert '同じ帳簿で同じ記録を発見しただけ' in steps[replay]
    (tmp_path / 'input.json').write_text(json.dumps(inputs, ensure_ascii=False), encoding='utf-8')
    (tmp_path / 'card.md').write_text(card, encoding='utf-8')
    assert not (tmp_path / 'output.json').exists()


def test_detail_nonmention_example_precedes_final_answer():
    d = definition('S8.detail')
    assert d['version'] == 4
    steps = d['card']['steps']
    example = next(i for i, step in enumerate(steps) if step.startswith('例：'))
    final = next(i for i, step in enumerate(steps) if step.startswith('違反なら answer'))
    assert example < final
    assert '「灯った」だけ' in steps[example] and '「押した」だけ' in steps[example]
    assert '押さなかった・灯らなかったと補わない' in steps[example]
    assert '他の違反・再演も検査' in steps[example]


def test_detail_example_card_does_not_increase_version_three_size():
    d = definition('S8.detail')
    # Freeze the public v3 instructions, not model outputs or private data.
    old = deepcopy(d)
    old['version'] = 3
    old['card']['role'] = 'この場面の本文だけを検査する。'
    old['card']['steps'] = [
        '確定した出来事・条件の追加・反転・置換を検査する。前提・行動・帰結・前後関係を照合し、同じ前後関係を逆転と判定しない。未定の条件は判定しない。',
        '条件は実施済みの場面ではなく、適合だけでは再演にならない。直前の場面の行動・結果の進展ない再演を検査する。',
        '非言及は否定ではなく、それだけを矛盾にしない。前提→帰結から逆や唯一の必要条件を導かない。明示された禁止・必要条件・前後関係の追加・反転・置換は yes とする。',
        '構造の説明・内部 ID も検査する。',
        '違反なら answer は yes、なければ no。reason は入力で食い違う前提・行動・帰結・順序か、進展ない再演の行動・結果を1文で具体的に示す。入力にない事情を補わない。',
    ]
    inputs = probe_inputs('S8.detail', PROBE_CASES['missing-premise'])
    assert len(generate_task_card(d, 'a' * 32, inputs)) <= len(generate_task_card(old, 'a' * 32, inputs))
