from __future__ import annotations

import json

import pytest

from storyteller.cards import InputBudgetError
from storyteller.orchestrator import Orchestrator
from storyteller.repair import generate_repair_card
from tests.test_never_stop import llm, MATERIAL

NOTE = '字数上限で文末まで切り詰めた後の検証結果です。'
RAW = 'ウヒルは2857年に生まれ、身長170cmの0歳の妖怪。父オエテと共にルウに住み、影の字で音速記録機を操り、12月31日までに地下図書館の失われた記録を解読したいと願う。'
ERROR = 'min_chars: 文字数 28 が 40以上ではありません'


def task():
    return llm('D1.profile', 'text', [
        {'min_chars': {'n': 40}}, {'max_chars': {'n': 80, 'fix': 'trim'}},
    ], max_input_chars=2800)


def submit_trim_failure(tmp_path):
    definition = task()
    harness = Orchestrator(tmp_path, {definition['id']: definition})
    run = harness.create_run(seed=5, input_data=MATERIAL)
    claim = harness.claim_next(run, executor_id='test')
    submitted = harness.submit(claim['ticket'], RAW)
    assert not submitted.accepted
    assert submitted.errors == (ERROR,)
    attempt_path = harness.task_dir(run, definition['id']) / 'attempts/1.json'
    record = json.loads(attempt_path.read_text(encoding='utf-8'))
    assert record['output'] == RAW
    assert record['errors'] == [ERROR]
    assert record['warnings'] == ['字数の上限で切り詰め']
    return harness, run, definition, attempt_path, record


def test_submit_raw85_trim28_then_reclaim_explains_transform(tmp_path):
    harness, run, definition, path, record = submit_trim_failure(tmp_path)
    claim = harness.claim_next(run, executor_id='test')
    assert RAW in claim['card']
    assert NOTE in claim['card'] and ERROR in claim['card']
    assert '40字以上' in claim['card'] and '80字以内' in claim['card']
    assert json.loads(path.read_text(encoding='utf-8')) == record
    current = harness.load_run(run)['tasks'][definition['id']]
    assert current['attempt'] == current['tries'] == 1
    assert current['error'] == ERROR


@pytest.mark.parametrize('warning', ['missing', None, [], ['他の警告']])
def test_old_record_or_unrelated_warning_keeps_exact_existing_card(tmp_path, warning):
    harness, run, definition, path, record = submit_trim_failure(tmp_path)
    if warning == 'missing':
        del record['warnings']
    else:
        record['warnings'] = warning
    path.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    claim = harness.claim_next(run, executor_id='test')
    expected = generate_repair_card(definition, claim['ticket'], original_output=RAW,
                                    reason=ERROR, inputs={})
    assert claim['card'] == expected
    assert NOTE not in claim['card']


@pytest.mark.parametrize('reason_size', [430, 30000])
@pytest.mark.parametrize('budget', [2800, 400, 230])
def test_transformed_reason_fits_limits_and_keeps_final_failure(reason_size, budget):
    definition = task()
    definition['max_input_chars'] = budget
    reason = 'min_chars: ' + '理' * reason_size + '40以上ではありません'
    card = generate_repair_card(definition, 'a' * 32,
        original_output='先頭。' + '文' * 6000 + '末尾。', reason=reason,
        inputs={}, max_chars_trimmed=True)
    body = card.split('## 入力\n', 1)[1].split('\n\n## 手順', 1)[0]
    rendered_reason = card.split('### 不合格の理由\n', 1)[1].split('\n### 満たすべき条件', 1)[0]
    assert len(body) <= budget
    assert len(rendered_reason) <= 500
    if budget == 230:
        assert len(rendered_reason) == 64
    assert NOTE in rendered_reason
    assert 'min_chars:' in rendered_reason and '40以上ではありません' in rendered_reason
    assert '先頭。' in card and '末尾。' in card
    assert '40字以上' in card and '80字以内' in card


def test_transform_explanation_does_not_allow_budget_overflow():
    definition = task()
    definition['max_input_chars'] = 20
    with pytest.raises(InputBudgetError):
        generate_repair_card(definition, 'ticket', original_output=RAW, reason=ERROR,
                             inputs={}, max_chars_trimmed=True)
