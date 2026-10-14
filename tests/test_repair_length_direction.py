from __future__ import annotations

from hashlib import sha256
import json

import pytest

from storyteller.orchestrator import Orchestrator
from storyteller.repair import generate_repair_card
from tests.test_never_stop import MATERIAL
from tests.test_repair_transform_reason import task

RAW101 = 'ウヒリは2857年生まれ、0歳で高さ50cm。ルウで暮らし、親オエクと共に町の記録管理者として記録消失瞬間を一度だけ再現し、10月15日までに図書館で失われた日記を探すことを願う男性主張の妖怪支援者だ。'
RAW102 = RAW101.replace('管理者として', '管理者として、')
SHORTEN = '元の出力へ書き足さず、補正前の元出力を要点を保って上下限内へ短く書き直す。'


@pytest.mark.parametrize('raw', [RAW101, RAW102])
def test_real_submission_trimmed_below_minimum_gets_shortening_direction(tmp_path, raw):
    definition = task()
    harness = Orchestrator(tmp_path, {definition['id']: definition})
    run = harness.create_run(seed=5, input_data=MATERIAL)
    first = harness.claim_next(run, executor_id='test')
    rejected = harness.submit(first['ticket'], raw)
    assert not rejected.accepted
    assert rejected.errors == ('min_chars: 文字数 23 が 40以上ではありません',)
    path = harness.task_dir(run, definition['id']) / 'attempts/1.json'
    before = json.loads(path.read_text(encoding='utf-8'))
    assert before['output'] == raw
    assert before['warnings'] == ['字数の上限で切り詰め']
    again = harness.claim_next(run, executor_id='test')
    assert raw in again['card'] and SHORTEN in again['card']
    assert '40字以上' in again['card'] and '80字以内' in again['card']
    assert '足りなければ、元の内容を具体的に描写して書き足す。' not in again['card']
    assert json.loads(path.read_text(encoding='utf-8')) == before
    current = harness.load_run(run)['tasks'][definition['id']]
    assert current['attempt'] == current['tries'] == 1
    assert current['error'] == rejected.errors[0]


@pytest.mark.parametrize('reason,trimmed,expected', [
    ('min_chars: 文字数 23 が 40以上ではありません', False,
     '74b0bb3395be19fc3baa90a80af80cd1967b80acdb0506791c2271a7a3f156aa'),
    ('max_chars: 文字数 101 が 80以下ではありません', True,
     '6edc442a61f402afbe2f9b0817c2009f5ffe22322e4173c17505b00e4b2462ef'),
])
def test_unaffected_cards_match_previous_revision_exactly(reason, trimmed, expected):
    # Captured from 71d9f4e before this implementation, without runtime git access.
    card = generate_repair_card(task(), 'ticket', original_output='短い元の本文。',
        reason=reason, inputs={}, max_chars_trimmed=trimmed)
    assert sha256(card.encode('utf-8')).hexdigest() == expected
    assert SHORTEN not in card


def test_multiple_errors_still_select_shortening_only_with_trim_warning():
    reason = 'ends_complete: 文末が不完全；min_chars: 文字数 23 が 40以上ではありません'
    common = dict(original_output=RAW101, reason=reason, inputs={})
    old = generate_repair_card(task(), 'ticket', **common)
    transformed = generate_repair_card(task(), 'ticket', **common, max_chars_trimmed=True)
    assert SHORTEN not in old and '具体的に描写して書き足す' in old
    assert SHORTEN in transformed
    assert reason in transformed
