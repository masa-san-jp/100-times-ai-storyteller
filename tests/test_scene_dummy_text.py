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
