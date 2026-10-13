"""P1-41: synthetic reproduction; no private run data is stored in tests."""
from copy import deepcopy
import json
from pathlib import Path
import unicodedata

import pytest
import yaml

from storyteller.cards import input_char_count, prepare_task_inputs
from storyteller.orchestrator import Orchestrator, TaskSpec, _prepare_card_inputs
from storyteller.task_outputs import task_sources
from storyteller.validation import validate_output

ROOT = Path(__file__).resolve().parents[1]


def profile_shape():
    definition = yaml.safe_load((ROOT / 'harness/story/tasks/S5.profile.yaml').read_text(encoding='utf-8'))
    for key, slot in definition['inputs'].items():
        slot.update({'from': 'D1.data', 'select': key})
    values = {
        'facts': {'name': 'ナギ', 'age': 71, 'birth_year': 200, 'calendar': '巡暦',
                  'height_cm': 168, 'skills': ['紙の補修']},
        'character': {'id': 'c1', 'role': 'protagonist', 'elements': {
            'ability': {'id': 'ability:t1', 'text': '紙をつなぐ', 'source': 'original provenance'},
            'want': {'id': 'want:t2', 'text': '記録を読む', 'source': 'another provenance'},
        }},
        'name': 'ナギ', 'role_definition': '',
        'plot_requirements': {'source': '削除しない本文', 'conditions': ['記録を読む']},
    }
    required_size = input_char_count(definition, prepare_task_inputs(definition, values))
    values['role_definition'] = '役' * (1935 - required_size)
    assert input_char_count(definition, prepare_task_inputs(definition, values)) == 1935
    values['glossary'] = [{'id': 'g123abc', 'text': ''}]
    with_glossary = input_char_count(definition, prepare_task_inputs(definition, values))
    values['glossary'][0]['text'] = '語' * (2681 - with_glossary)
    assert input_char_count(definition, _prepare_card_inputs(definition, values)) == 2681
    return definition, values


def make_profile(tmp_path):
    definition, values = profile_shape()
    definitions = {'D1.data': {'id': 'D1.data', 'version': 1, 'kind': 'code', 'handler': 'data'}, 'S5.profile': definition}
    harness = Orchestrator(tmp_path, definitions, {'data': lambda context: deepcopy(values)})
    run = harness.create_run(seed=11, tasks=[TaskSpec('D1.data', 'D1.data'),
        TaskSpec('S5.profile-c1', 'S5.profile', deps=('D1.data',), index=('c1',))])
    first = harness.claim_next(run, executor_id='test')
    assert first is not None
    return harness, run, first, definition, values


def test_observed_545_char_partial_refits_and_preserves_whole_tail(tmp_path):
    harness, run, first, definition, original = make_profile(tmp_path)
    directory = harness.task_dir(run, 'S5.profile-c1')
    initial = json.loads((directory / 'input.json').read_text(encoding='utf-8'))
    assert input_char_count(definition, initial) == 2681
    partial = 'ナギは' + '歩' * 541 + '。'
    assert len(partial) == 545
    assert harness.submit(first['ticket'], partial).accepted
    assert not (tmp_path / 'cache').exists()
    # Re-open to exercise durable partial and inputs rather than memory.
    harness = Orchestrator(tmp_path, harness.task_definitions, harness.code_handlers)
    continuation = harness.claim_next(run, executor_id='second')
    assert continuation is not None
    fitted = json.loads((directory / 'input.json').read_text(encoding='utf-8'))
    assert continuation['card'].split('## これまでの出力の末尾\n')[1] == partial
    assert input_char_count(definition, fitted) + len(partial) <= 2800
    assert fitted['facts'] == original['facts']
    assert fitted['name'] == original['name']
    assert fitted['role_definition'] == original['role_definition']
    assert fitted['plot_requirements'] == original['plot_requirements']
    assert fitted['glossary'] != initial['glossary']
    assert 'source' not in fitted['character']['elements']['ability']
    manifest = harness.load_run(run)
    key = harness._cache_key_for_task(manifest, 'S5.profile-c1', fitted)
    assert manifest['tasks']['S5.profile-c1']['cache_key'] == key
    assert key != harness._cache_key_for_task(manifest, 'S5.profile-c1', initial)
    assert harness.submit(continuation['ticket'], '続' * 300 + '。').accepted
    manifest = harness.load_run(run)
    assert manifest['tasks']['S5.profile-c1']['state'] == 'done'
    assert manifest['tasks']['S5.profile-c1']['cache_key'] == key
    assert (tmp_path / 'cache' / key[:2] / f'{key}.json').exists()
    assert {'c1', 'ability:t1', 'want:t2'}.issubset(task_sources(directory))
    assert json.loads((harness.task_dir(run, 'D1.data') / 'output.json').read_text(encoding='utf-8')) == original


@pytest.mark.parametrize('partial', ['a' * 544 + 'e\u0301', '長' * 1300])
def test_nfc_short_whole_tail_and_long_minimum_with_shrinkable_input(tmp_path, partial):
    normalized = unicodedata.normalize('NFC', partial)
    definition = {'id': 'D1.story', 'version': 1, 'kind': 'llm', 'element': 'text',
        'output': 'text', 'continuation': True, 'max_input_chars': 1800,
        'inputs': {'given': {'label': '素材', 'from': 'input', 'select': 'given',
                            'required': True, 'truncate': 'head'}},
        'card': {'role': '素材を扱う', 'steps': ['本文を書く']}}
    harness = Orchestrator(tmp_path, {'D1.story': definition})
    run = harness.create_run(seed=1, input_data={'given': '素' * 1700})
    first = harness.claim_next(run, executor_id='first')
    assert first is not None
    assert harness.submit(first['ticket'], partial, truncated=True).accepted
    second = harness.claim_next(run, executor_id='second')
    assert second is not None
    tail = second['card'].split('## これまでの出力の末尾\n')[1]
    fitted = json.loads((harness.task_dir(run, 'D1.story') / 'input.json').read_text(encoding='utf-8'))
    assert tail == normalized[-len(tail):]
    assert len(tail) >= min(1000, len(normalized))
    if len(normalized) < 1000:
        assert tail == normalized
    assert len(tail) + input_char_count(definition, fitted) <= 1800


def test_long_partial_required_input_failure_is_preserved(tmp_path):
    harness, run, first, _, _ = make_profile(tmp_path)
    assert harness.submit(first['ticket'], '長' * 1100, truncated=True).accepted
    assert harness.claim_next(run, executor_id='next') is None
    task = harness.load_run(run)['tasks']['S5.profile-c1']
    assert task['state'] == 'failed'
    assert task['error'] == '継続の予算が足りない'


def test_source_projection_nonmutation_context_and_identity_validation(tmp_path):
    harness, run, _, definition, values = make_profile(tmp_path)
    context = harness._build_context(harness.load_run(run), run, 'S5.profile-c1')
    assert context.inputs['character'] == values['character']
    assert context.dependency_outputs['D1.data'] == values
    assert context.outputs['D1.data'] == values
    original = deepcopy(context.inputs)
    fitted = _prepare_card_inputs(definition, context.inputs)
    assert context.inputs == original
    assert fitted['plot_requirements']['source'] == '削除しない本文'
    assert fitted['character']['elements']['ability'] == {'id': 'ability:t1', 'text': '紙をつなぐ'}
    identity = dict(definition, validate={'checks': [{'character_identity': {'require_subject': True}}]})
    assert validate_output(identity, 'ナギは71歳。ナギは巡暦200年に生まれた。', fitted).passed
    assert not validate_output(identity, 'ナギは72歳。', fitted).passed
    assert not validate_output(identity, 'ナギは巡暦201年に生まれた。', fitted).passed
    other = dict(definition, id='D1.description')
    assert prepare_task_inputs(other, values)['character']['elements']['ability']['source'] == 'original provenance'


def test_empty_input_allows_exact_1000_char_tail_budget(tmp_path):
    definition = {'id': 'D1.story', 'version': 1, 'kind': 'llm', 'element': 'text',
        'output': 'text', 'continuation': True, 'max_input_chars': 1000,
        'card': {'role': '本文を扱う', 'steps': ['本文を書く']}}
    harness = Orchestrator(tmp_path, {'D1.story': definition})
    run = harness.create_run(seed=1)
    first = harness.claim_next(run, executor_id='first')
    assert first is not None
    partial = '前' * 1200
    assert harness.submit(first['ticket'], partial, truncated=True).accepted
    second = harness.claim_next(run, executor_id='second')
    assert second is not None
    assert second['card'].split('## これまでの出力の末尾\n')[1] == partial[-1000:]
