from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml

from storyteller.cards import generate_task_card
from storyteller.cache import cache_key
from storyteller.orchestrator import Orchestrator
from storyteller.validation import validate_output, ValidationConfigurationError, validate_document, SchemaValidationError
from storyteller.volume import dev_task_definition
from tests.test_story_s7_detail import _definition
from tests.test_story_s7 import _event

ROOT = Path(__file__).resolve().parents[1]
CHECK = 'no_repeated_sentence'


def check(text):
    return validate_output({'output': 'text', 'validate': {'checks': [CHECK]}}, text)


@pytest.mark.parametrize('length,passed', [(19, True), (20, False)])
@pytest.mark.parametrize('delimiter', ['。', '！', '？', '.', '!', '?', '\n', '\r\n'])
def test_exact_threshold_and_all_sentence_delimiters(length, passed, delimiter):
    sentence = '文' * length
    raw = sentence + delimiter + sentence
    result = check(raw)
    assert result.passed == passed
    assert result.value == raw and result.repairable == (not passed)


def test_nfkc_spaces_and_nonconsecutive_duplicates():
    sentence = 'ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴ'
    text = sentence + '！途中の違う文。' + 'A B C D E F G H I J K L M N O P Q R S T?'
    assert not check(text).passed


@pytest.mark.parametrize('text', ['短い同じ文。短い同じ文。', 'あ' * 20 + '。' + 'あ' * 20 + 'い。',
    'あ' * 20 + '。' + 'あ' * 19 + 'い。', 'あ' * 19 + '。' + 'あ' * 19 + '。'])
def test_short_partial_shared_or_similar_sentences_are_allowed(text):
    assert check(text).passed


def test_no_copy_failure_does_not_become_repairable():
    sentence = '文' * 65
    result = validate_output({'output': 'text', 'validate': {'checks': [CHECK, 'no_copy_from_inputs']}},
        sentence + '。' + sentence + '。', inputs={'body': sentence}, harness_root=ROOT)
    assert not result.passed and not result.repairable
    assert any(e.startswith(CHECK + ':') for e in result.errors)
    assert any(e.startswith('no_copy_from_inputs:') for e in result.errors)


def test_json_arguments_and_schema_reject_wrong_configuration():
    with pytest.raises(ValidationConfigurationError):
        validate_output({'output': 'json', 'validate': {'checks': [CHECK]}}, '{}')
    with pytest.raises(ValidationConfigurationError):
        validate_output({'output': 'text', 'validate': {'checks': [{CHECK: {'n': 20}}]}}, '本文')
    d = _definition()
    d['validate']['checks'] = [{CHECK: {'n': 20}}]
    with pytest.raises(SchemaValidationError):
        validate_document(d, ROOT / 'schemas/task-definition.schema.json')


def test_public_submit_repair_preserves_original_then_accepts(tmp_path):
    d, _ = dev_task_definition(_definition(), {'preset': 'dev'}, ROOT)
    # Isolate the real task's validation and repair in a synthetic public run.
    d['inputs'] = {}
    h = Orchestrator(tmp_path, {'S7.detail': d}, harness_root=ROOT)
    run = h.create_run(seed=62, tasks={'S7.detail': {'type': 'S7.detail'}})
    sentence = '紙の端に残る細い印が机の上の光で静かに浮かぶ。'
    original = sentence * 2
    claim = h.claim_next(run, executor_id='test')
    result = h.submit(claim['ticket'], original)
    assert not result.accepted and any(e.startswith(CHECK + ':') for e in result.errors)
    record_path = h.task_dir(run, 'S7.detail') / 'attempts/1.json'
    record = json.loads(record_path.read_text(encoding='utf-8'))
    assert record['output'] == original and record['repairable'] is True
    repair = h.claim_next(run, executor_id='test')
    assert original in repair['card'] and '同じ長い文を繰り返さず' in repair['card']
    assert h.submit(repair['ticket'], sentence + '人物は紙を少し傾け、印の途切れた箇所を指で確かめる。').accepted
    assert json.loads(record_path.read_text(encoding='utf-8')) == record


def test_old_v13_definition_card_validation_and_cache_are_unchanged():
    old = yaml.safe_load((ROOT / 'tests/fixtures/s7-detail-v13.yaml').read_text(encoding='utf-8'))
    new = _definition()
    assert old['version'] == 13 and new['version'] == 14
    expected = deepcopy(old)
    expected['version'] = 14
    expected['validate']['checks'].insert(2, CHECK)
    assert new == expected
    inputs = {**_event(), 'beat': {'id': 'opening', 'name': '発端', 'definition': '発端を示す。'},
              'characters': [], 'world_sections': [], 'previous_scene': ''}
    assert generate_task_card(old, 'ticket', inputs) == generate_task_card(new, 'ticket', inputs)
    text = ('文' * 20 + '。') * 80
    assert validate_output(old, text, inputs={}, harness_root=ROOT).passed
    assert not validate_output(new, text, inputs={}, harness_root=ROOT).passed
    assert cache_key('S7.detail', 13, inputs, seed=62) != cache_key('S7.detail', 14, inputs, seed=62)
