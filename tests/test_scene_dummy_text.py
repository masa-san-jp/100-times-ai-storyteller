from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from storyteller.validation import load_yaml, validate_output
from storyteller.volume import dev_task_definition
from tests.test_story_cli_e2e import _fake_output, _filler_text

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize('dev,length', [(False, 1500), (True, 60)])
def test_scene_dummy_is_exact_length_complete_unique_and_valid(dev, length):
    inputs = {'output_chars': [40, 80]} if dev else {}
    task = {'type': 'S7.detail', 'index': ['e001', 'b1']}
    text = _fake_output('S7.detail-e001-b1', task, inputs, send_comparison_yes=False)
    assert text == _fake_output('S7.detail-e001-b1', task, inputs, send_comparison_yes=False)
    assert len(text) == length and text.endswith('。')
    sentences = [s for s in re.split(r'[。！？!?]', text) if s]
    assert len(sentences) == len(set(sentences))
    definition = load_yaml(ROOT / 'harness/story/tasks/S7.detail.yaml')
    if dev:
        definition, _ = dev_task_definition(definition, {'preset': 'dev'}, ROOT)
    result = validate_output(definition, text, inputs=inputs, harness_root=ROOT)
    assert result.passed, result.errors


@pytest.mark.parametrize('args,expected', [
    (('S5.voice-c1', 'voice', 800), '6e7d6cc5339313f1809148324700a001a9ea24eb4a1d73b1019a44feb8eb747d'),
    (('S5.profile-c1', 'profile', 1150), '7c9479505e978e96097a765cd0f9af0888dfd2ca78e8f2ea59697350230d2002'),
])
def test_existing_character_filler_is_byte_for_byte_unchanged(args, expected):
    assert hashlib.sha256(_filler_text(*args).encode('utf-8')).hexdigest() == expected


def test_public_scene_continuation_keeps_prefix_and_reaches_exact_floor(tmp_path):
    from storyteller.orchestrator import Orchestrator
    from storyteller.validation import output_char_count
    from tests.test_story_cli_e2e import _continuation_prefix
    definition = load_yaml(ROOT / 'harness/story/tasks/S7.detail.yaml')
    definition['inputs'] = {}
    harness = Orchestrator(tmp_path, {'S7.detail': definition}, harness_root=ROOT)
    run = harness.create_run(seed=62, tasks={'S7.detail': {'type': 'S7.detail'}})
    task_id = 'S7.detail-e001-b2'
    minimum = 1500
    prefix = _continuation_prefix(task_id, 'S7.detail', minimum)
    original = _fake_output(task_id, {'type': 'S7.detail', 'index': ['e001', 'b2']}, {}, send_comparison_yes=False)
    assert prefix.endswith('\n\n') and output_char_count(prefix) == minimum // 2
    completed = prefix + original[len(prefix.rstrip()):]
    assert completed.startswith(prefix) and output_char_count(completed) == minimum
    sentences = [s for s in re.split(r'[。！？!?]', completed) if s.strip()]
    assert len(sentences) == len(set(sentences))
    assert validate_output(definition, completed, inputs={}, harness_root=ROOT).passed
    first = harness.claim_next(run, executor_id='test')
    insufficient = harness.submit(first['ticket'], prefix)
    assert insufficient.accepted
    current = harness.load_run(run)['tasks']['S7.detail']
    assert current['state'] == 'ready' and current['continuation_step'] == 1
    again = harness.claim_next(run, executor_id='test')
    assert 'これまでの出力の末尾' in again['card']
    assert '同じ内容をさらに具体的に書き足す' in again['card']
    final = harness.submit(again['ticket'], completed)
    assert final.accepted
    stored = (harness.task_dir(run, 'S7.detail') / 'output.md').read_text(encoding='utf-8')
    assert stored == completed and stored.startswith(prefix)
    assert harness.load_run(run)['tasks']['S7.detail']['state'] == 'done'


def test_other_task_continuation_prefix_is_exactly_unchanged():
    from tests.test_story_cli_e2e import _continuation_prefix
    subject = 'カナは'
    assert _continuation_prefix('S5.profile-c1', 'S5.profile', 800, subject) == (
        subject + _filler_text('S5.profile-c1', '書き足す前の説明', 400 - len(subject)) + '\n\n'
    )
