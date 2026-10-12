from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from storyteller.new_run import create_story_orchestrator
from storyteller.orchestrator import Orchestrator, TaskSpec
from storyteller.cards import input_char_count
from storyteller.world_facts import description_entries, consistency_entries
from tests.test_story_s4 import _assignment
from tests.test_story_cli_e2e import _filler_text

ROOT = Path(__file__).parents[1]


def test_single_facets_share_all_viewpoint_facts_while_list_items_stay_local():
    entries = [dict(id='a', key='a', section_id='place', viewpoint='地形', item_id=None),
               dict(id='b', key='b', section_id='place', viewpoint='制度', item_id=None),
               dict(id='c', key='c', section_id='groups', viewpoint='制度', item_id='groups-001'),
               dict(id='d', key='d', section_id='groups', viewpoint='制度', item_id='groups-002')]
    assert [e['id'] for e in consistency_entries(
        dict(id='place-1-f1', section_id='place', kind='single', viewpoint='地形'), entries)] == ['a', 'b']
    assert [e['id'] for e in description_entries(
        dict(id='place-1-f1', section_id='place', kind='single', viewpoint='地形'), entries)] == ['a']
    assert [e['id'] for e in description_entries(
        dict(id='groups-001', section_id='groups', kind='list'), entries)] == ['c']


def _harness(tmp_path):
    base = create_story_orchestrator(ROOT)
    assignment = _assignment()
    assignment['world_tasks'] = assignment['world_tasks'][:1]
    assignment['world_fact_tasks'] = [
        dict(id='place.power', key='place.power', section_id='place', viewpoint='水路の流れ',
             item_id=None, element='text', label='電力源'),
        dict(id='place.rule', key='place.rule', section_id='place', viewpoint='水路の流れ',
             item_id=None, element='text', label='通行条件'),
    ]
    definitions = {name: deepcopy(base.task_definitions[name]) for name in
                   ('S4.section', 'S4.consistency', 'S4.consistency_judge')}
    sources = {'S3.assign': assignment, 'S4.calendar_name': {'name': 'カナ暦', 'reading': 'カナ'},
               'S4.fact-place.power': '潮力', 'S4.fact-place.rule': '日中だけ門を開ける'}
    specs = []
    handlers = {**base.code_handlers}
    for task_id, value in sources.items():
        task_type = task_id.split('-')[0]
        definitions[task_type] = {'id': task_type, 'version': 1, 'kind': 'code', 'handler': task_type}
        handlers[task_type] = lambda context: sources[context.task_id]
        index = (task_id.split('-', 1)[1],) if '-' in task_id else ()
        specs.append(TaskSpec(task_id, task_type, index=index))
    body_id = 'S4.section-place-1-f1'
    specs.append(TaskSpec(body_id, 'S4.section', deps=tuple(sources), index=('place-1-f1',)))
    checks = []
    check_id = "S4.consistency-place-1-f1-k1"
    checks.append(check_id)
    specs.append(TaskSpec(check_id, 'S4.consistency',
        deps=('S3.assign', 'S4.calendar_name', body_id, 'S4.fact-place.power', 'S4.fact-place.rule'),
        index=('place-1-f1', 'k1')))
    judge = 'S4.consistency_judge-place-1-f1'
    specs.append(TaskSpec(judge, 'S4.consistency_judge', deps=(body_id, *checks), index=('place-1-f1',)))
    definitions['S6.expand'] = {'id': 'S6.expand', 'version': 1, 'kind': 'code', 'handler': 'consumer'}
    handlers['consumer'] = lambda context: {'body': context.dependency_outputs.get(body_id, '（未定）')}
    specs.append(TaskSpec('S6.expand', 'S6.expand', deps=(body_id, judge)))
    harness = Orchestrator(tmp_path / 'data', definitions, handlers, harness_root=ROOT, repository_root=ROOT)
    run = harness.create_run(seed=1, task_specs=specs)
    return harness, run, body_id, checks


@pytest.mark.parametrize('exhaust', [False, True])
def test_semantic_contradiction_retries_only_description_and_skips_at_limit(tmp_path, exhaust):
    harness, run, body_id, checks = _harness(tmp_path)
    original_fact_files = {}
    for iteration in range(3 if exhaust else 2):
        claim = harness.claim_next(run, executor_id='test')
        assert claim is not None
        assert '電力源：潮力' in claim['card'] and '通行条件：日中だけ門を開ける' in claim['card']
        contradiction = exhaust or iteration == 0
        lead = '町の電力は風力だけで賄われ、潮の動きは使わない。' if contradiction else '町の発電は潮の動きを使う。'
        body = lead + _filler_text(f'world-{iteration}', '水路の描写', 900)
        assert harness.submit(claim['ticket'], body).accepted
        for check_id in checks:
            claim = harness.claim_next(run, executor_id='test')
            assert claim is not None and body in claim['card']
            inputs = json.loads((harness.task_dir(run, check_id) / 'input.json').read_text(encoding='utf-8'))
            assert len(inputs['facts']) == 2  # bounded chunk, each fact remains a complete row
            assert input_char_count(harness.task_definitions['S4.consistency'], inputs) <= 2200
            facts = [fact['text'] for fact in inputs['facts']]
            assert facts == ['電力源：潮力', '通行条件：日中だけ門を開ける']
            yes = contradiction
            result = harness.submit(claim['ticket'], json.dumps({
                'answer': 'yes' if yes else 'no',
                'reason': '潮力という確定事実を風力だけに置き換えている。' if yes else '確定した事実と両立する。'}, ensure_ascii=False))
            assert result.accepted
        manifest = harness.advance(run)
        for task_id in ('S4.fact-place.power', 'S4.fact-place.rule'):
            path = harness.task_dir(run, task_id) / 'output.json'
            original_fact_files.setdefault(task_id, path.read_text(encoding='utf-8'))
            assert path.read_text(encoding='utf-8') == original_fact_files[task_id]
            assert manifest['tasks'][task_id]['invalidations'] == 0
        if contradiction and iteration < 2:
            assert manifest['tasks'][body_id]['state'] == 'ready'
            assert manifest['tasks']['S6.expand']['state'] == 'blocked'
    assert manifest['status'] == 'completed'
    assert manifest['tasks'][body_id]['state'] == ('skipped' if exhaust else 'done')
    consumer = json.loads((harness.task_dir(run, 'S6.expand') / 'output.json').read_text(encoding='utf-8'))
    if exhaust:
        assert consumer['body'] == '（未定）'
        assert any(body_id in warning for warning in manifest['warnings'])
        assert (harness.task_dir(run, body_id) / 'failure.md').exists()
    else:
        assert consumer['body']['body'] == body


def test_six_full_fact_rows_and_maximum_body_fit_model_context():
    from storyteller.cards import generate_task_card
    from storyteller.orchestrator import _prepare_card_inputs
    import yaml
    definition = create_story_orchestrator(ROOT).task_definitions['S4.consistency']
    facts = [{'id': f'g{number:06x}', 'text': '確定した制度の見出し：' + '値' * 40} for number in range(6)]
    body = '文' * 1499 + '。'
    inputs = {'facts': facts, 'section_body': body}
    assert _prepare_card_inputs(definition, inputs) == inputs
    assert input_char_count(definition, inputs) <= 2800
    card = generate_task_card(definition, 'a' * 32, inputs=inputs, retry_reason='理' * 500)
    assert body in card and all(fact['text'] in card for fact in facts)
    model = yaml.safe_load((ROOT / 'config/models.yaml').read_text(encoding='utf-8'))['models']['gpt-oss:20b']
    assert 2 * len(card) + model['max_tokens'] <= model['context_length']


@pytest.mark.parametrize('keys', [
    ['social_structure.habitat_area', 'place.protected_area', 'place.area'],
    ['social_structure.forest_area', 'place.summer_temperature', 'place.highest_elevation'],
])
def test_generation_claim_preserves_transitive_fact_references(tmp_path, keys):
    from storyteller.tables import load_table
    catalog = {entry['key']: entry for entry in load_table('world_facts')['facts']}
    entries = [dict(catalog[key], id=key, item_id=None) for key in keys]
    assignment = _assignment()
    world_task = deepcopy(assignment['world_tasks'][0])
    world_task.update(id='social_structure-1-f1', section_id='social_structure',
                      viewpoint=entries[0]['viewpoint'])
    assignment.update(world_tasks=[world_task], world_fact_tasks=entries)
    assert [entry['key'] for entry in description_entries(world_task, entries)] == keys
    base = create_story_orchestrator(ROOT)
    definitions = {'S4.section': base.task_definitions['S4.section']}
    sources = {'S3.assign': assignment, 'S4.calendar_name': {'name': 'カナ暦', 'reading': 'カナ'},
               **{f'S4.fact-{key}': str(value) for key, value in zip(keys, (2, 3, 4))}}
    specs = []
    handlers = {}
    for task_id in sources:
        task_type = task_id.split('-')[0]
        definitions[task_type] = {'id': task_type, 'version': 1, 'kind': 'code', 'handler': task_type}
        handlers[task_type] = lambda context: sources[context.task_id]
        specs.append(TaskSpec(task_id, task_type,
                              index=(task_id.split('-', 1)[1],) if '-' in task_id else ()))
    body_id = 'S4.section-social_structure-1-f1'
    specs.append(TaskSpec(body_id, 'S4.section', deps=tuple(sources), index=(world_task['id'],)))
    harness = Orchestrator(tmp_path / 'data', definitions, handlers, harness_root=ROOT, repository_root=ROOT)
    run = harness.create_run(seed=1, task_specs=specs)
    claim = harness.claim_next(run, executor_id='test')
    inputs = json.loads((harness.task_dir(run, body_id) / 'input.json').read_text(encoding='utf-8'))
    assert len(inputs['facts']) == 3
    assert all(catalog[key]['label'] in claim['card'] for key in keys)
    assert all(f'S4.fact-{key}' in harness.load_run(run)['tasks'][body_id]['deps'] for key in keys)


def test_fact_reference_closure_is_cycle_safe_and_keeps_ids_once():
    entries = [dict(id='a', key='a', section_id='place', viewpoint='地形', item_id=None, refers=['b']),
               dict(id='b', key='b', section_id='place', viewpoint='制度', item_id=None, refers=['a'])]
    world_task = dict(id='place-1-f1', section_id='place', kind='single', viewpoint='地形')
    assert [entry['id'] for entry in description_entries(world_task, entries)] == ['a', 'b']


def test_generated_dag_includes_three_level_catalog_references(tmp_path):
    from tests.test_world_facts import _planned
    harness, run, assignment = _planned(tmp_path, sections=['place', 'social_structure'])
    manifest = harness.load_run(run)
    entries = {entry['key']: entry for entry in assignment['world_fact_tasks']}
    for keys in ([
        'social_structure.habitat_area', 'place.protected_area', 'place.area'
    ], [
        'social_structure.forest_area', 'place.summer_temperature', 'place.highest_elevation'
    ]):
        world_task = next(task for task in assignment['world_tasks']
                          if task['section_id'] == 'social_structure'
                          and task['viewpoint'] == entries[keys[0]]['viewpoint'])
        dependencies = manifest['tasks'][f"S4.section-{world_task['id']}"]['deps']
        assert {f'S4.fact-{key}' for key in keys} <= set(dependencies)
