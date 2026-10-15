from __future__ import annotations

from copy import deepcopy
import json

import pytest

from storyteller.cache import cache_key
from storyteller.orchestrator import Orchestrator, TaskSpec
from storyteller.validation import validate_output
from storyteller.world_facts import specialize_fact_definition
from tests.test_character_facts import ROOT, _definition, _inputs
from tests.test_story_s5 import _character

RAW = '2857年、ルイセで記録管理者c2が失われた手紙を発見。'
FIXED = '2857年、ルイセで記録管理者ウヒリが失われた手紙を発見。'


def test_public_submit_rejects_timeline_id_then_repairs_with_confirmed_name(tmp_path):
    definition = _definition()
    definitions = {'S5.fact': definition}
    for source in ('S3.assign', 'S5.name', 'S4.calendar_name'):
        definitions[source] = {'id': source, 'version': 1, 'kind': 'code', 'handler': source}
    entry = {'id': 'c2-timeline.1.event', 'key': 'timeline.1.event', 'person_id': 'c2',
             'label': '年表の出来事', 'element': 'text', 'max_chars': 60}
    assignment = {'cast': [_character('supporter', 'c2', '本人')],
                  'calendar': {'current_year': 2857}, 'world_fact_tasks': [],
                  'character_fact_tasks': [entry]}
    harness = Orchestrator(tmp_path, definitions,
        {'S3.assign': lambda context: assignment,
         'S5.name': lambda context: {'name': 'ウヒリ', 'reading': 'ウヒリ'},
         'S4.calendar_name': lambda context: {'name': 'カナ暦', 'reading': 'カナ'}},
        harness_root=ROOT, repository_root=ROOT)
    task_id = 'S5.fact-c2-timeline.1.event'
    run = harness.create_run(seed=1, task_specs=[
        TaskSpec('S3.assign', 'S3.assign'), TaskSpec('S5.name-c2', 'S5.name', index=('c2',)),
        TaskSpec('S4.calendar_name', 'S4.calendar_name'),
    ])
    harness.advance(run)
    harness._add_tasks(run, 'S3.assign', [
        TaskSpec(task_id, 'S5.fact', deps=('S3.assign', 'S5.name-c2', 'S4.calendar_name'), index=(entry['id'],)),
    ])
    claim = harness.claim_next(run, executor_id='test')
    assert '人物は入力の確定名で表す' in claim['card']
    rejected = harness.submit(claim['ticket'], RAW)
    assert not rejected.accepted
    assert any('no_story_metadata' in error for error in rejected.errors)
    path = harness.task_dir(run, task_id) / 'attempts/1.json'
    record = json.loads(path.read_text(encoding='utf-8'))
    assert record['output'] == RAW and record['repairable'] is True
    repair = harness.claim_next(run, executor_id='test')
    assert RAW in repair['card'] and '構造の説明と内部 ID を本文から除く' in repair['card']
    accepted = harness.submit(repair['ticket'], FIXED)
    assert accepted.accepted and accepted.value == FIXED
    assert (harness.task_dir(run, task_id) / 'output.md').read_text(encoding='utf-8') == FIXED


def test_new_text_check_is_versioned_without_changing_legacy_or_world_checks():
    current = _definition()
    old = deepcopy(current)
    old['version'] = 1
    source = _inputs('text', max_chars=60)
    before = deepcopy(source)
    a = specialize_fact_definition(current, source)
    b = specialize_fact_definition(old, source)
    assert a['validate']['checks'] == [*b['validate']['checks'], 'no_story_metadata']
    assert validate_output(old, RAW, source, harness_root=ROOT).passed
    world = _definition('S4.fact')
    first = specialize_fact_definition(world, source)
    world['version'] = 99
    assert specialize_fact_definition(world, source)['validate'] == first['validate']
    assert 'no_story_metadata' not in first['validate']['checks']
    assert source == before and current['version'] == 3
    assert cache_key(current['id'], 1, source, seed=1) != cache_key(current['id'], 2, source, seed=1)


@pytest.mark.parametrize('shape,value,extra', [
    ('choice', 'g123456', {'choices': [{'id': 'g123456', 'name': 'ルイセ'}]}),
    ('number', '50', {}), ('integer', '0', {}), ('year', '2857', {'bounds': {'min': 1, 'max': 3000}}),
    ('name', {'name': 'カナ', 'reading': 'カナ'}, {'name_sound': {'sounds': ['カ', 'ナ']}}),
])
def test_machine_fact_values_do_not_get_text_metadata_check(shape, value, extra):
    source = _inputs(shape, **extra)
    specialized = specialize_fact_definition(_definition(), source)
    assert 'no_story_metadata' not in specialized['validate']['checks']
    output = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value
    result = validate_output(_definition(), output, source, harness_root=ROOT)
    assert result.passed, result.errors
