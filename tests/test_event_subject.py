from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from storyteller.new_run import create_story_orchestrator
from storyteller.orchestrator import CodeTaskResult, Orchestrator, TaskSpec
from storyteller.repair import REPAIR_ROLE
from storyteller.validation import validate_output
from tests.test_story_s7 import _slot

ROOT = Path(__file__).parents[1]
DEFINITION = {'output': 'text', 'element': 'text', 'validate': {'checks': ['event_subject']}}


def _validate(body, names=('アノミ',), field='何が起きたか'):
    return validate_output(DEFINITION, body,
        {'characters': [{'id': f'c{number}', 'name': name} for number, name in enumerate(names)],
         'event_field': field})


@pytest.mark.parametrize('field', ['何が起きたか', '結果', '出来事の結果'])
@pytest.mark.parametrize('body', ['ポケピ・コキは通路を開く。', '未知の人物が通路を開く。',
                                 '人物は通路を開く。', 'アノミの通路が開く。',
                                 '小アノミは通路を開く。', '通路が開く。'])
def test_wrong_actor_is_rejected_and_is_not_a_length_repair(body, field):
    result = _validate(body, field=field)
    assert not result.passed and not result.repairable
    assert result.errors[0].startswith('event_subject:') and 'アノミ' in result.errors[0]


@pytest.mark.parametrize('body', [
    'アノミはポケピ・コキに門を開ける。', 'アノミが門を開く。',
    'アノミとカナは門を開く。', 'カナ、アノミが門を開く。',
    'アノミとカナ、ウネルは門を開く。', '  「アノミは門を開く。」',
    '『「アノミが門を開く。」』', '"アノミは門を開く。"',
])
def test_single_and_assigned_joint_subjects_allow_other_people_in_the_body(body):
    assert _validate(body, names=('アノミ', 'カナ', 'ウネル')).passed


@pytest.mark.parametrize('body', ['アノミとポケピ・コキは門を開く。',
                                 'アノミ、未知名が門を開く。', 'アノミやカナは門を開く。'])
def test_joint_subject_must_contain_only_assigned_names(body):
    assert not _validate(body, names=('アノミ', 'カナ')).passed


@pytest.mark.parametrize('name,body', [
    ('「アノミ」', '「アノミ」は門を開く。'),
    ('「アノミ」', '『「アノミ」が門を開く。』'),
    ('『アノミ』', '「『アノミ』は門を開く。」'),
    ('"アノミ"', '"アノミ"は門を開く。'),
    ('アノミとカナ', 'アノミとカナは門を開く。'),
    ('ｱﾉﾐ', 'アノミは門を開く。'), ('アノミ', 'ｱﾉﾐが門を開く。'),
    ('Ａｎｏｍｉ', 'Anomiは門を開く。'),
])
def test_registered_literal_quotes_and_nfkc_names_are_preserved(name, body):
    assert _validate(body, names=(name,)).passed


def test_missing_quotes_in_the_registered_name_do_not_match():
    assert not _validate('アノミは門を開く。', names=('「アノミ」',)).passed
    assert not _validate('「アノミは門を開く。', names=('「アノミ」',)).passed


@pytest.mark.parametrize('field', ['いつ', 'どこで', '理由', '意思', '感情', '後の出来事への伏線', None])
def test_other_fields_have_no_subject_requirement(field):
    assert _validate('ポケピ・コキが町の境界にいた夜。', field=field).passed


@pytest.mark.parametrize('names', [(), ('未定',), ('（未定）',), ('',)])
def test_undecided_names_do_not_invent_an_actor(names):
    assert _validate('誰かが門を開く。', names=names).passed
    assert not _validate('誰かが門を開く。', names=(*names, 'アノミ')).passed


def _harness(tmp_path, *, candidates=False, field="what"):
    base = create_story_orchestrator(ROOT)
    definition = deepcopy(base.task_definitions['S7.event'])
    definition['max_attempts'] = 2
    definitions = {'S7.event': definition,
        'S6.expand': {'id': 'S6.expand', 'version': 1, 'kind': 'code', 'handler': 'slots'},
        'S7.select': base.task_definitions['S7.select'],
        'S9.finish': {'id': 'S9.finish', 'version': 1, 'kind': 'code', 'handler': 'finish'}}
    slot = _slot()
    slot['characters'][0]['name'] = 'アノミ'
    ids = [f'S7.event-e001-what-c{number}' for number in range(1, 4)] if candidates else [f'S7.event-e001-{field}']

    def slots(context):
        specs = [TaskSpec(task_id, 'S7.event', deps=(context.task_id,),
                          index=('e001', 'what', f'c{number}') if candidates else ('e001', field))
                 for number, task_id in enumerate(ids, 1)]
        tail = ids
        if candidates:
            specs.append(TaskSpec('S7.select-e001', 'S7.select', deps=(context.task_id, *ids), index=('e001',)))
            tail = ['S7.select-e001']
        specs.append(TaskSpec('S9.finish', 'S9.finish', deps=(context.task_id, *tail)))
        return CodeTaskResult(output={'slots': [slot]}, add_tasks=specs)

    harness = Orchestrator(tmp_path / 'data', definitions,
        {**base.code_handlers, 'slots': slots, 'finish': lambda context: dict(context.dependency_outputs)},
        harness_root=ROOT, repository_root=ROOT)
    run = harness.create_run(seed=157, task_specs=[TaskSpec('S6.expand', 'S6.expand')])
    return harness, run, ids


@pytest.mark.parametrize("field", ["what", "result"])
def test_real_claim_retries_wrong_actor_without_repairing_meaning(tmp_path, field):
    harness, run, ids = _harness(tmp_path, field=field)
    claim = harness.claim_next(run, executor_id='test')
    rejected = harness.submit(claim['ticket'], 'ポケピ・コキは通路を開く。')
    assert not rejected.accepted and any('event_subject' in error for error in rejected.errors)
    retry = harness.claim_next(run, executor_id='test')
    assert REPAIR_ROLE not in retry['card'] and 'event_subject' in retry['card']
    assert harness.submit(retry['ticket'], 'アノミは通路を開く。').accepted
    assert harness.advance(run)['status'] == 'completed'
    assert (harness.task_dir(run, ids[0]) / 'output.md').read_text(encoding='utf-8') == 'アノミは通路を開く。'


def test_all_wrong_actor_candidates_skip_at_finite_limit_and_complete(tmp_path):
    harness, run, ids = _harness(tmp_path, candidates=True)
    for _ in range(6):
        claim = harness.claim_next(run, executor_id='test')
        assert claim is not None and REPAIR_ROLE not in claim['card']
        assert not harness.submit(claim['ticket'], 'ポケピ・コキは通路を開く。').accepted
    manifest = harness.advance(run)
    assert manifest['status'] == 'completed'
    assert all(manifest['tasks'][task_id]['state'] == 'skipped' for task_id in ids)
    assert all(any(task_id in warning and 'event_subject' in warning for warning in manifest['warnings'])
               for task_id in ids)
    selected = json.loads((harness.task_dir(run, 'S7.select-e001') / 'output.json').read_text(encoding='utf-8'))
    assert selected == {'candidate': None, 'text': '（未定）', 'method': 'undecided', 'similarity': None}
    assert manifest['tasks']['S7.select-e001']['state'] == 'done'


@pytest.mark.parametrize('field', ['what', 'result'])
@pytest.mark.parametrize('body,accepted_cache', [('ポケピ・コキは通路を開く。', False), ('アノミは通路を開く。', True)])
def test_subject_is_revalidated_on_cache_hit_without_spending_attempt(tmp_path, field, body, accepted_cache):
    from storyteller.cache import save_cache
    from storyteller.orchestrator import _prepare_card_inputs
    harness, run, ids = _harness(tmp_path, field=field)
    manifest = harness.advance(run)
    context = harness._build_context(manifest, run, ids[0])
    inputs = _prepare_card_inputs(context.definition, context.inputs)
    key = harness._cache_key_for_task(manifest, ids[0], inputs)
    save_cache(harness.data_dir, key, body)
    claim = harness.claim_next(run, executor_id='test')
    task = harness.load_run(run)['tasks'][ids[0]]
    assert task['attempt'] == task['tries'] == 0
    if accepted_cache:
        assert claim is None and task['state'] == 'done'
    else:
        assert claim is not None and task['state'] == 'claimed'
        assert harness.submit(claim['ticket'], 'アノミは通路を開く。').accepted
    assert harness.advance(run)['status'] == 'completed'
    assert (harness.task_dir(run, ids[0]) / 'output.md').read_text(encoding='utf-8') == 'アノミは通路を開く。'


def test_other_field_cache_keeps_the_existing_reuse_rule(tmp_path):
    from storyteller.cache import save_cache
    from storyteller.orchestrator import _prepare_card_inputs
    harness, run, ids = _harness(tmp_path, field='where')
    manifest = harness.advance(run)
    context = harness._build_context(manifest, run, ids[0])
    inputs = _prepare_card_inputs(context.definition, context.inputs)
    save_cache(harness.data_dir, harness._cache_key_for_task(manifest, ids[0], inputs), 'ポケピ・コキがいた場所。')
    assert harness.claim_next(run, executor_id='test') is None
    assert harness.advance(run)['status'] == 'completed'
    assert (harness.task_dir(run, ids[0]) / 'output.md').read_text(encoding='utf-8') == 'ポケピ・コキがいた場所。'


def test_malformed_candidate_collection_still_fails():
    from types import SimpleNamespace
    from storyteller.story_s7_candidates import story_s7_select
    for candidates in ('（未定）', None, []):
        with pytest.raises(ValueError, match='S7 の候補がありません'):
            story_s7_select(SimpleNamespace(inputs={'candidates': candidates}))


def test_full_event_dag_keeps_undecided_action_after_all_candidates_skip(tmp_path):
    from tests.test_story_s7_elements import _harness as event_harness
    from tests.test_story_s7 import _event
    from storyteller.story_s7 import EVENT_FIELDS
    harness, run = event_harness(tmp_path)
    for _ in range(15):
        claim = harness.claim_next(run, executor_id='test')
        assert claim is not None
        assert not harness.submit(claim['ticket'], 'ポケピ・コキは通路を開く。').accepted
    for field in EVENT_FIELDS[1:]:
        claim = harness.claim_next(run, executor_id='test')
        assert claim is not None
        assert harness.submit(claim['ticket'], _event()[field]).accepted
    manifest = harness.advance(run)
    assert manifest['status'] == 'completed'
    event = json.loads((harness.task_dir(run, 'S7.assemble-e001') / 'output.json').read_text(encoding='utf-8'))
    assert event['what'] == '（未定）' and event['result'] == _event()['result']
    assert event['who'] == ['c1']
    assert 'None' not in event['sources']
