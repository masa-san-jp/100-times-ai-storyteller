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
    for item, expected in [('groups-001', ['c']), ('groups-002', ['d'])]:
        assert [entry['id'] for entry in consistency_entries(
            dict(id=item, section_id='groups', kind='list'), entries)] == expected


def _harness(tmp_path, *, fact_count=2, consistency_definition=None):
    base = create_story_orchestrator(ROOT)
    assignment = _assignment()
    assignment['world_tasks'] = assignment['world_tasks'][:1]
    assignment['world_fact_tasks'] = [
        dict(id='place.power', key='place.power', section_id='place', viewpoint='水路の流れ',
             item_id=None, element='text', label='電力源'),
        dict(id='place.rule', key='place.rule', section_id='place', viewpoint='水路の流れ',
             item_id=None, element='text', label='通行条件'),
    ]
    assignment['world_fact_tasks'].extend(
        dict(id=f'place.procedure{number}', key=f'place.procedure{number}',
             section_id='place', viewpoint='水路の流れ', item_id=None,
             element='text', label=f'手続{number}の成立条件')
        for number in range(1, fact_count - 1)
    )
    definitions = {name: deepcopy(base.task_definitions[name]) for name in
                   ('S4.section', 'S4.consistency', 'S4.consistency_judge')}
    if consistency_definition is not None:
        definitions['S4.consistency'] = deepcopy(consistency_definition)
    sources = {'S3.assign': assignment, 'S4.calendar_name': {'name': 'カナ暦', 'reading': 'カナ'},
               'S4.fact-place.power': '潮力', 'S4.fact-place.rule': '日中だけ門を開ける'}
    sources.update({f'S4.fact-place.procedure{number}': f'許可札{number}が必要'
                    for number in range(1, fact_count - 1)})
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
    fact_ids = [f"S4.fact-{entry['id']}" for entry in assignment['world_fact_tasks']]
    for offset in range(0, len(fact_ids), 6):
        chunk = f'k{offset // 6 + 1}'
        check_id = f'S4.consistency-place-1-f1-{chunk}'
        checks.append(check_id)
        specs.append(TaskSpec(check_id, 'S4.consistency',
            deps=('S3.assign', 'S4.calendar_name', body_id, *fact_ids[offset:offset + 6]),
            index=('place-1-f1', chunk)))
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


def _consistency_definitions():
    import yaml
    current = yaml.safe_load((ROOT / 'harness/story/tasks/S4.consistency.yaml').read_text(encoding='utf-8'))
    legacy = yaml.safe_load((ROOT / 'tests/fixtures/s4-consistency-v2.yaml').read_text(encoding='utf-8'))
    return current, legacy


def test_comparison_scope_is_the_only_v3_contract_change():
    current, legacy = _consistency_definitions()
    assert current['version'] == 3 and legacy['version'] == 2
    expected = deepcopy(legacy)
    expected['version'] = 3
    expected['card']['steps'] = current['card']['steps']
    assert current == expected
    assert current['card']['steps'][-2:] == legacy['card']['steps'][1:]
    steps = '\n'.join(current['card']['steps'])
    for instruction in (
        '同じ対象・属性の記述が両立しない場合だけ',
        '別の行事・手続・対象を同一視しない',
        '別対象の名称や数値が違っても、共存できれば矛盾ではない',
        '今回の分割に対応する事実がない記述を、他の対象の事実で補って矛盾としない',
        '明示的な同一視・否定・唯一性',
        '同じ対象の名称・数値の違い、制度・成立条件の意味違反も引き続き検査する',
    ):
        assert instruction in steps
    assert current['max_input_chars'] == 2200
    assert current['on_exhausted'] == 'skip'


@pytest.mark.parametrize('body_slot', ['section_body', 'item_body'])
def test_v2_card_and_full_inputs_stay_compatible_with_v3_scope(body_slot):
    from storyteller.cards import generate_task_card
    from storyteller.orchestrator import _prepare_card_inputs
    from storyteller.cache import cache_key

    current, legacy = _consistency_definitions()
    inputs = {
        'facts': [{'id': f'g{number:06x}', 'text': '確定した制度の見出し：' + '値' * 40}
                  for number in range(6)],
        body_slot: '文' * 1499 + '。',
    }
    original = deepcopy(inputs)
    fitted = _prepare_card_inputs(current, inputs)
    assert fitted == inputs == _prepare_card_inputs(legacy, inputs) == original
    assert input_char_count(current, fitted) <= 2200
    legacy_card = generate_task_card(legacy, 'old-ticket', inputs=fitted, retry_reason='理' * 500)
    for number, step in enumerate(legacy['card']['steps'], 1):
        assert f'{number}. {step}' in legacy_card
    assert '今回の分割に対応する事実がない記述' not in legacy_card
    current_card = generate_task_card(current, 'old-ticket', inputs=fitted, retry_reason='理' * 500)
    assert fitted[body_slot] in current_card
    assert all(row['text'] in current_card and row['text'] in legacy_card for row in fitted['facts'])
    assert '今回の分割に対応する事実がない記述' in current_card
    assert cache_key('S4.consistency', 2, fitted, seed=193) != cache_key('S4.consistency', 3, fitted, seed=193)


@pytest.mark.parametrize('legacy_run', [False, True])
def test_public_claim_separates_v2_cached_judgement_and_keeps_legacy_reuse(tmp_path, legacy_run):
    from storyteller.cache import cache_key, cache_path, save_cache
    from storyteller.orchestrator import _prepare_card_inputs
    from storyteller.seed import derive_task_seed

    current, legacy = _consistency_definitions()
    harness, run, body_id, checks = _harness(
        tmp_path, consistency_definition=legacy if legacy_run else current)
    claim = harness.claim_next(run, executor_id='test')
    body = '町は潮の動きで発電し、乾燥小屋の風車とは使い分けている。' + _filler_text('cache-scope', '水路の描写', 900)
    assert harness.submit(claim['ticket'], body).accepted
    manifest = harness.load_run(run)
    context = harness._build_context(manifest, run, checks[0])
    fitted = _prepare_card_inputs(context.definition, context.inputs)
    old_key = cache_key('S4.consistency', 2, fitted, seed=derive_task_seed(1, checks[0], 0))
    cached = {'answer': 'no', 'reason': '確定した条件と描写は両立する。'}
    save_cache(harness.data_dir, old_key, cached)
    old_bytes = cache_path(harness.data_dir, old_key).read_bytes()
    check_claim = harness.claim_next(run, executor_id='test')
    if legacy_run:
        assert check_claim is None
        manifest = harness.load_run(run)
        assert manifest['tasks'][checks[0]]['state'] == 'done'
        assert manifest['tasks'][checks[0]]['tries'] == 0
        assert manifest['tasks'][checks[0]]['cache_key'] == old_key
    else:
        assert check_claim is not None
        assert harness.validate_claim(check_claim['ticket'])['task_id'] == checks[0]
        assert '別の行事・手続・対象を同一視しない' in check_claim['card']
        assert harness.load_run(run)['tasks'][checks[0]]['cache_key'] != old_key
        assert harness.submit(check_claim['ticket'], json.dumps(cached, ensure_ascii=False)).accepted
        manifest = harness.advance(run)
    assert manifest['status'] == 'completed'
    assert manifest['tasks'][body_id]['invalidations'] == 0
    assert cache_path(harness.data_dir, old_key).read_bytes() == old_bytes


def test_public_multi_chunk_check_waits_for_every_chunk_and_keeps_true_contradiction_retry(tmp_path):
    harness, run, body_id, checks = _harness(tmp_path, fact_count=13)
    assert len(checks) == 3
    judge_id = 'S4.consistency_judge-place-1-f1'
    reason = '手続11の実施に必要な許可札を不要としている。'
    fact_bytes = {}
    for attempt in range(2):
        body_claim = harness.claim_next(run, executor_id='test')
        assert harness.validate_claim(body_claim['ticket'])['task_id'] == body_id
        if attempt:
            assert reason in body_claim['card']
        lead = ('手続11は許可札なしで始められる。' if attempt == 0
                else '手続11では許可札を確かめてから始める。')
        body = lead + _filler_text(f'scope-chunk-{attempt}', '水路の描写', 900)
        assert harness.submit(body_claim['ticket'], body).accepted
        all_rows = []
        for index, check_id in enumerate(checks):
            check_claim = harness.claim_next(run, executor_id='test')
            assert harness.validate_claim(check_claim['ticket'])['task_id'] == check_id
            inputs = json.loads((harness.task_dir(run, check_id) / 'input.json').read_text(encoding='utf-8'))
            assert len(inputs['facts']) == (6 if index < 2 else 1)
            assert input_char_count(harness.task_definitions['S4.consistency'], inputs) <= 2200
            assert body in check_claim['card']
            assert '今回の分割に対応する事実がない記述' in check_claim['card']
            assert all(row['text'] in check_claim['card'] for row in inputs['facts'])
            all_rows.extend(inputs['facts'])
            contradiction = attempt == 0 and index == 2
            if contradiction:
                assert inputs['facts'][0]['text'] == '手続11の成立条件：許可札11が必要'
            result = harness.submit(check_claim['ticket'], json.dumps({
                'answer': 'yes' if contradiction else 'no',
                'reason': reason if contradiction else '確定した条件と描写は両立する。',
            }, ensure_ascii=False))
            assert result.accepted
            manifest = harness.load_run(run)
            if index < len(checks) - 1:
                assert manifest['tasks'][judge_id]['state'] == 'blocked'
                assert manifest['tasks']['S6.expand']['state'] == 'blocked'
        assert len(all_rows) == len({row['text'] for row in all_rows}) == 13
        assert [row['text'].split('：')[0] for row in all_rows] == [
            '電力源', '通行条件', *(f'手続{number}の成立条件' for number in range(1, 12))]
        manifest = harness.advance(run)
        for task_id, task in manifest['tasks'].items():
            if task['type'] == 'S4.fact':
                path = harness.task_dir(run, task_id) / 'output.json'
                fact_bytes.setdefault(task_id, path.read_bytes())
                assert path.read_bytes() == fact_bytes[task_id]
                assert task['invalidations'] == 0
        if attempt == 0:
            assert manifest['tasks'][body_id]['state'] == 'ready'
            assert manifest['tasks'][body_id]['invalidations'] == 1
            assert manifest['tasks']['S6.expand']['state'] == 'blocked'
    assert manifest['status'] == 'completed'
    assert manifest['tasks'][body_id]['invalidations'] == 1


@pytest.mark.parametrize('invalid,repairable', [
    ({'answer': 'maybe', 'reason': '理由。'}, False),
    ({'reason': '理由。'}, False),
    ({'answer': 'no', 'reason': ''}, True),
    ({'answer': 'no', 'reason': '字' * 161}, True),
    ({'answer': 'no', 'reason': '理由。', 'extra': True}, False),
    ('copy', False),
])
def test_public_scope_card_retains_schema_reason_and_copy_validation(tmp_path, invalid, repairable):
    harness, run, body_id, checks = _harness(tmp_path)
    body_claim = harness.claim_next(run, executor_id='test')
    body = '町の発電設備は潮の動きで水車を回し、役人は日が出ている間に水門で舟の通行証を確かめる。' + _filler_text('scope-validation', '水路の描写', 900)
    assert harness.submit(body_claim['ticket'], body).accepted
    check_claim = harness.claim_next(run, executor_id='test')
    if invalid == 'copy':
        invalid = {'answer': 'no', 'reason': body[:100]}
    rejected = harness.submit(check_claim['ticket'], json.dumps(invalid, ensure_ascii=False))
    assert not rejected.accepted
    retry = harness.claim_next(run, executor_id='test')
    assert retry is not None and retry['ticket'] != check_claim['ticket']
    assert harness.validate_claim(retry['ticket'])['task_id'] == checks[0]
    if repairable:
        assert '元の内容を保ったまま、条件を満たすように書き直す' in retry['card']
        assert '1字以上にする（reason）' in retry['card']
        assert '160字以内にする（reason）' in retry['card']
    else:
        assert '同じ対象・属性の記述が両立しない場合だけ' in retry['card']
    assert harness.submit(retry['ticket'], json.dumps({
        'answer': 'no', 'reason': '確定した条件と描写は両立する。',
    }, ensure_ascii=False)).accepted
    manifest = harness.advance(run)
    assert manifest['status'] == 'completed'
    assert manifest['tasks'][body_id]['invalidations'] == 0
