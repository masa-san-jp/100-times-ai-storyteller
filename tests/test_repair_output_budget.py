from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from storyteller.cards import TaskCardError
from storyteller.new_run import create_story_orchestrator
from storyteller.orchestrator import Orchestrator, TaskSpec
from storyteller.repair import generate_repair_card
from tests.test_never_stop import llm
from tests.test_story_s7 import _event

ROOT = Path(__file__).parents[1]
LINE = '生成上限：2048トークン（モデル設定の上限が小さい場合はその値）。'


@pytest.mark.parametrize('output', ['json', 'text'])
def test_repair_limit_adds_only_the_same_output_format_line(output):
    definition = llm('D1.value', 'text', [{'max_chars': {'n': 80}}])
    definition['output'] = output
    if output == 'json':
        definition['card']['output_example'] = '{"reason": "..."}'
    before = deepcopy(definition)
    args = dict(original_output='元の出力。', reason='max_chars', inputs={})
    old = generate_repair_card(definition, 'ticket', **args)
    definition['max_output_tokens'] = 2048
    current = generate_repair_card(definition, 'ticket', **args)
    assert current == old.replace('## 出力形式\n', '## 出力形式\n' + LINE + '\n')
    assert current.count(LINE) == 1
    assert definition == {**before, 'max_output_tokens': 2048}


@pytest.mark.parametrize('bad', [True, False, 0, -1, 1.5, '2048', None, [], {}])
def test_repair_rejects_invalid_output_limits(bad):
    definition = llm('D1.value', 'text', [])
    definition['max_output_tokens'] = bad
    with pytest.raises(TaskCardError, match='max_output_tokens must be a positive integer'):
        generate_repair_card(definition, 'ticket', original_output='元の出力。', reason='字数', inputs={})


def test_real_detail_failure_and_public_reclaim_show_output_limit(tmp_path):
    detail = deepcopy(create_story_orchestrator(tmp_path / 'unused').task_definitions['S8.detail'])
    snapshot = deepcopy(detail)
    definitions = {'S8.detail': detail}
    for source in ('S7.assemble', 'S7.detail'):
        definitions[source] = {'id': source, 'version': 1, 'kind': 'code', 'handler': source}
    harness = Orchestrator(tmp_path / 'data', definitions,
        {'S7.assemble': lambda context: _event(), 'S7.detail': lambda context: '人物が記録を確かめた。'},
        harness_root=ROOT, repository_root=ROOT)
    run = harness.create_run(seed=1, task_specs=[
        TaskSpec('S7.assemble-e001', 'S7.assemble', index=('e001',)),
        TaskSpec('S7.detail-e001-b1', 'S7.detail', index=('e001', 'b1')),
        TaskSpec('S8.detail-e001-b1', 'S8.detail',
                 deps=('S7.assemble-e001', 'S7.detail-e001-b1'), index=('e001', 'b1')),
    ])
    first = harness.claim_next(run, executor_id='test')
    assert LINE in first['card']
    rejected = harness.submit(first['ticket'], json.dumps({'answer': 'no', 'reason': ''}))
    assert not rejected.accepted
    assert len(rejected.errors) == 1 and rejected.errors[0].startswith('min_chars:')
    path = harness.task_dir(run, 'S8.detail-e001-b1') / 'attempts/1.json'
    record = json.loads(path.read_text(encoding='utf-8'))
    assert record['repairable'] is True
    repair = harness.claim_next(run, executor_id='test')
    assert '前回の出力を、元の内容を保ったまま' in repair['card']
    assert repair['card'].count(LINE) == 1
    assert rejected.errors[0] in repair['card']
    assert detail == snapshot
    submitted = harness.submit(repair['ticket'], json.dumps({'answer': 'no', 'reason': '確定事項と矛盾しない。'}, ensure_ascii=False))
    assert submitted.accepted
