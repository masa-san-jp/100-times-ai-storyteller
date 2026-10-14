from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

import pytest

from storyteller.character_facts import assign_character_fact_tasks
from storyteller.new_run import create_story_orchestrator
from storyteller.tables import load_table
from storyteller.world_facts import assign_world_fact_tasks, description_entries, referred_entries, selected_key_entries
from tests.test_dev_scale import _new_dev_run
from tests.test_story_cli_e2e import _claimed_task, _fake_output, _task_input

ROOT = Path(__file__).parents[1]


def test_dev_repeated_facts_keep_every_scalar_field():
    catalog = load_table('character_facts')
    counts = load_table('scales')['dev']['character_fact_counts']
    entries = assign_character_fact_tasks(
        [{'id': 'c1'}, {'id': 'c2'}], load_table('name_sounds')['sets'], random.Random(0), ROOT,
        counts=counts,
    )
    scalar_keys = {row['key'] for row in catalog['facts']
                   if not row['key'].startswith(('family.', 'timeline.')) and row['key'] != 'skills'}
    for person in ('c1', 'c2'):
        keys = {row['key'] for row in entries if row['person_id'] == person}
        assert keys == scalar_keys | {'family.1.relationship', 'family.1.name',
                                    'timeline.1.year', 'timeline.1.event', 'skills.1'}


@pytest.mark.parametrize('seed,expected,next_draw', [
    (0, 'ed2fa70278de2bdd36c105f49f562339565c99a0bfeb86234210d56018aec372', 0.12481071462810711),
    (17, '03d54ac2ef324e3e0fe1fea45ecb6d89ee44b1d1111a8e7552e58b0cff08c7cd', 0.07502981832768818),
    (81, 'ac62ab429661a7b69e0d34ee35f1e96ef28082a35d6ed3df01fd4c3da48c0e75', 0.12192689514910537),
])
def test_normal_facts_preserve_prior_output_and_random_sequence(seed, expected, next_draw):
    # Captured from the implementation before adding the optional development counts.
    random_source = random.Random(seed)
    entries = assign_character_fact_tasks(
        [{'id': 'c1'}, {'id': 'c2'}], load_table('name_sounds')['sets'], random_source, ROOT,
    )
    digest = hashlib.sha256(json.dumps(entries, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    assert digest == expected
    assert random_source.random() == next_draw


def test_dev_assignment_keeps_ordered_description_reference_closure_and_checks(tmp_path):
    data_dir, run = _new_dev_run(tmp_path)
    harness = create_story_orchestrator(data_dir)
    for _ in range(100):
        manifest = harness.advance(run)
        if manifest['tasks'].get('S3.assign', {}).get('state') == 'done':
            break
        claim = harness.claim_next(run, executor_id='test')
        assert claim is not None
        task_id, task = _claimed_task(data_dir, run, claim['ticket'])
        assert task['type'].startswith(('S1.', 'S2.'))
        output = _fake_output(task_id, task, _task_input(data_dir, run, task_id), send_comparison_yes=False)
        assert harness.submit(claim['ticket'], output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)).accepted
    else:
        pytest.fail('S3 assignment did not complete')
    assignment = json.loads((harness.task_dir(run, 'S3.assign') / 'output.json').read_text(encoding='utf-8'))
    original = assign_world_fact_tasks(
        assignment['world_sections'], assignment['world_tasks'], load_table('name_sounds')['sets'], random.Random(0),
        current_year=assignment['calendar']['current_year'], repository_root=ROOT,
    )
    wanted = {row['id'] for row in selected_key_entries(original, load_table('scales')['dev']['world_fact_keys'], ROOT)}
    selected = assignment['world_fact_tasks']
    assert [row['id'] for row in selected] == [row['id'] for row in original if row['id'] in wanted]
    assert len(selected) < len(original)
    assert len(selected) == 7
    selected_ids = {row['id'] for row in selected}
    assert all(other['id'] in selected_ids for row in selected for other in referred_entries(row, original))
    for person in assignment['cast']:
        keys = {row['key'] for row in assignment['character_fact_tasks'] if row['person_id'] == person['id']}
        assert {'family.1.name', 'timeline.1.event', 'skills.1'} <= keys
        assert not any(key.startswith(('family.2.', 'timeline.2.', 'skills.2')) for key in keys)
    checks = [task for task in manifest['tasks'].values() if task['type'] == 'S4.consistency']
    checked = {dependency for task in checks for dependency in task['deps'] if dependency.startswith('S4.fact-')}
    assert checked == {f"S4.fact-{row['id']}" for row in selected}


def test_selected_keys_follow_recursive_item_local_references():
    entries = [
        {'id': 'place.area', 'key': 'place.area', 'item_id': None, 'refers': ['place.location_name']},
        {'id': 'place.location_name', 'key': 'place.location_name', 'item_id': None},
        {'id': 'a-age', 'key': 'people.age', 'item_id': 'a', 'refers': ['people.residence']},
        {'id': 'a-home', 'key': 'people.residence', 'item_id': 'a', 'refers': ['place.area']},
        {'id': 'b-home', 'key': 'people.residence', 'item_id': 'b'},
    ]
    assert [row['id'] for row in selected_key_entries(entries, ['people.age'], ROOT)] == [
        'place.area', 'place.location_name', 'a-age', 'a-home',
    ]


@pytest.mark.parametrize('key', ['unknown.key', 'people.age'])
def test_selected_keys_reject_unknown_or_unavailable_facts(key):
    with pytest.raises(ValueError, match='キーが不正'):
        selected_key_entries([], [key], ROOT)


def test_selected_key_schema_requires_nonempty_keys_and_multiple_candidates():
    from copy import deepcopy
    from storyteller.validation import validate_document, SchemaValidationError
    table = load_table('scales')
    schema = ROOT / 'schemas/tables/scales.schema.json'
    validate_document(table, schema)
    for update in ({'world_fact_keys': []}, {'event_candidates': 1}):
        changed = deepcopy(table)
        changed['dev'].update(update)
        with pytest.raises(SchemaValidationError):
            validate_document(changed, schema)
    changed = deepcopy(table)
    del changed['dev']['world_fact_keys']
    with pytest.raises(SchemaValidationError):
        validate_document(changed, schema)
    changed['dev']['world_facts_scope'] = 'described_viewpoints'
    validate_document(changed, schema)


@pytest.mark.parametrize('seed,expected', [
    (0, '6def5fa80ddda788718319c99081ff4410ea57ce82198361e4cf43a294c8638d'),
    (17, '2f3b9159405ed802c1e3c8ef6a2798b8a43d6c89723c0518e23e2017681d05ba'),
    (81, 'e6f4fd37f5b37b18b432468249f0a85f64d128dfbb742173a79eb1ccca221ea0'),
])
def test_normal_assignment_card_cache_and_candidate_snapshots(tmp_path, seed, expected):
    from tests.test_story_s3 import _run, _assignment
    from storyteller.story_s7 import event_tasks
    # Captured from f983ee9, before this implementation. Only the random
    # claim ticket heading is omitted; all task/card/cache content is retained.
    harness, run = _run(tmp_path / 'data', seed, run_number=(0, 17, 81).index(seed) + 1)
    manifest = harness.load_run(run)
    claim = harness.claim_next(run, executor_id='snapshot')
    task_id = next(key for key, task in harness.load_run(run)['tasks'].items() if task['state'] == 'claimed')
    inputs = json.loads((harness.task_dir(run, task_id) / 'input.json').read_text(encoding='utf-8'))
    value = {
        'assignment': _assignment(harness, run),
        'tasks': {key: {field: task[field] for field in ('type', 'deps', 'index')}
                  for key, task in manifest['tasks'].items()},
        'card': claim['card'].split('\n', 1)[1],
        'cache': harness._cache_key_for_task(harness.load_run(run), task_id, inputs),
        'event_tasks': [{'id': task.task_id, 'type': task.type, 'deps': task.deps, 'index': task.index}
                        for task in event_tasks('S6.expand', 'e001', ['S6.expand'], None)],
    }
    digest = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
    assert digest == expected
