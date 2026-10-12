from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from storyteller.cards import _fit_inputs
from storyteller.formatting import build_plan, check_body, load_profile
from storyteller.new_run import create_story_orchestrator
from storyteller.orchestrator import CodeTaskResult, Orchestrator, TaskSpec
from storyteller.plot_quality import condition_ids, prose_errors, unique_events
from storyteller.repair import REPAIR_ROLE
from storyteller.story_s6 import _build_downstream_tasks
from storyteller.story_s8 import story_s8_plan, story_s8_judge
from storyteller.story_s9 import _assemble_events
from storyteller.validation import validate_output
from tests.test_format_novel import _story, _thread, _event as novel_event
from tests.test_story_s7 import _slot, _event

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize('body', [
    'ウネル（c1）は歩いた。', 'ウネル(c12)は歩いた。', '出来事 e001 を記す。',
    'この場面は、出来事の発端として主人公の目的を示す。',
    'ここまでが、この出来事の発端にあたる。',
])
def test_reported_metadata_is_rejected_and_repairable(body):
    harness = create_story_orchestrator(ROOT)
    for kind in ('S7.event', 'S7.detail', 'F2.write', 'F2.write_once'):
        definition = deepcopy(harness.task_definitions[kind])
        definition['validate']['checks'] = ['no_story_metadata']
        result = validate_output(definition, body)
        assert not result.passed and result.repairable
        assert any('no_story_metadata' in error for error in result.errors)
    assert check_body(body, {}, [], ROOT)


def test_story_prose_and_real_names_are_allowed():
    assert not prose_errors('ウネルは干潮を待って道を渡った。オヨミリアが後を追った。')
    assert not prose_errors('灯台の高さは30メートルだった。')


def test_metadata_rejection_produces_a_repair_inference(tmp_path):
    base = create_story_orchestrator(tmp_path / 'unused')
    definitions = {name: deepcopy(base.task_definitions[name]) for name in ('F2.write_once',)}
    definitions['F1.split'] = {'id': 'F1.split', 'version': 1, 'kind': 'code', 'handler': 'plan'}
    part = {'id': 'u1p1', 'events': [], 'characters': [], 'range': '1〜200字',
            'conditions': ['干潮のときだけ道が現れる。']}

    def plan(context):
        return CodeTaskResult(output={'parts': [part], 'rules': []}, add_tasks=[
            TaskSpec('F2.write_once-u1p1-novel', 'F2.write_once', deps=(context.task_id,), index=('u1p1', 'novel'))])

    harness = Orchestrator(tmp_path / 'data', definitions, {'plan': plan}, harness_root=ROOT, repository_root=ROOT)
    run = harness.create_run(seed=1, task_specs=[TaskSpec('F1.split', 'F1.split')])
    claim = harness.claim_next(run, executor_id='test')
    rejected = harness.submit(claim['ticket'], 'ウネル（c1）は歩いた。この場面は発端を示す。')
    assert not rejected.accepted
    repair = harness.claim_next(run, executor_id='test')
    assert REPAIR_ROLE in repair['card'] and 'no_story_metadata' in repair['card']
    assert '干潮のときだけ道が現れる。' in repair['card']
    assert harness.submit(repair['ticket'], 'ウネルは干潮を待って道を渡った。').accepted
    assert harness.advance(run)['status'] == 'completed'


def test_conditions_survive_card_budget_reduction():
    definition = create_story_orchestrator(ROOT).task_definitions['S7.event']
    from tests.test_story_s7 import _slot
    slot = _slot()
    values = {'stage_definition': slot['stage']['definition'], 'stage_guidance': slot['stage']['guidance'],
              'required_events': [], 'absent_role_note': None, 'object': slot['object'],
              'characters': slot['characters'], 'world_sections': [{'body': '世界。' * 3000}],
              'conflict': slot['plot']['conflict'], 'theme': None, 'event_field': '何が起きたか',
              'conditions': {'e001-trigger': '干潮のときだけ道が現れる。'}}
    fitted = _fit_inputs(definition, values)
    assert fitted['conditions'] == values['conditions']
    assert fitted['world_sections'] != values['world_sections']


def test_dag_passes_same_conditions_to_every_field_and_beat():
    slots = [dict(_slot(f'e{n:03d}'), thread='t001', beats=[{'id': 'opening'}]) for n in (1, 2)]
    context = SimpleNamespace(task_id='S6.expand', task={'deps': ['S3.assign']})
    tasks = _build_downstream_tasks(context, slots)
    conditions = condition_ids('e001')
    assert [t.task_id for t in tasks if t.type == 'S7.condition'] == list(conditions)
    for task in tasks:
        if task.type in ('S7.event', 'S7.assemble', 'S7.detail', 'S8.plan'):
            assert set(conditions) <= set(task.deps)
    assert 'S8.detail_judge-e001-b1' in next(t for t in tasks if t.task_id == 'S9.assemble').deps


def test_conditions_are_compared_even_for_the_first_event():
    event = {**_event(), 'sources': ['c1'], 'conditions': ['干潮のときだけ道が現れる。']}
    context = SimpleNamespace(task_id='S8.plan-e001', task={'index': ['e001']},
        inputs={'slots': [{'id': 'e001', 'thread': 't001'}]},
        dependency_outputs={'S7.assemble-e001': event, 'S7.condition-e001-trigger': event['conditions'][0]})
    plan = story_s8_plan(context)
    assert plan.output['targets'] == [{'conditions': event['conditions']}]
    assert [t.type for t in plan.add_tasks] == ['S8.compare', 'S8.judge']
    assert 'conditions' not in plan.output['current']  # each condition appears once in the bounded card
    judgement = story_s8_judge(SimpleNamespace(task_id='S8.judge-e001', task={'index': ['e001']},
        inputs={'comparisons': {'e001-k1': {'answer': 'yes', 'reason': '押印の条件に置き換えている。'}}}))
    assert judgement.invalidations == [('S7.event-e001-what', '比較結果に矛盾あり：押印の条件に置き換えている。')]


def test_repeated_event_is_not_another_writing_target():
    story = _story([_thread(1, "main", "three-beat")], [novel_event(1, "t001", "setup"), novel_event(2, "t001", "setup"), novel_event(3, "t001", "resolution")])
    story["events"][2]["result"] = "翌日の入口が開いた。"
    duplicate = deepcopy(story['events'][0])
    duplicate['id'] = 'e002'
    story['events'][1] = duplicate
    story['events'][0]['conditions'] = ['干潮のときだけ道が現れる。']
    story['events'][1]['conditions'] = story['events'][0]['conditions']
    profile = load_profile('novel')
    plan = build_plan(story, profile, ROOT)
    assigned = [event for part in plan['parts'] for event in part['event_ids']]
    assert 'e001' in assigned and 'e002' not in assigned and 'e003' in assigned
    assert any(part['conditions'] == story['events'][0]['conditions'] for part in plan['parts'])
    assert len(plan['questions']) == 4
    slots = [{'id': event['id'], 'thread': event['thread'], 'stage': event['stage']} for event in story['events']]
    outputs = {f'S7.assemble-{event["id"]}': event for event in story['events']}
    canonical = _assemble_events(slots, outputs)
    assert [event['id'] for event in canonical] == ['e001', 'e003']
    assert canonical[-1]['foreshadowing'] == ''


def test_distinct_time_place_motive_or_outcome_is_preserved():
    first = dict(_event(), id='e001', thread='t001')
    for field, value in [('when', '翌日。'), ('where', '港。'), ('why', '仲間を探す。'),
                         ('result', '隠れていた通路は現れない。')]:
        second = dict(first, id='e002', **{field: value})
        assert len(unique_events([first, second], 0.5)) == 2


def test_repeated_scene_failure_is_repairable():
    previous = '潮が引いた夜、ウネルは帳簿を開いて道を探し、入口を確かめた。' * 8
    result = validate_output({'output': 'text', 'validate': {'checks': ['no_repeated_scene']}},
                             previous, {'previous_scene': previous}, harness_root=ROOT)
    assert not result.passed and result.repairable


def test_failed_beat_validation_only_retries_that_beat(monkeypatch):
    from storyteller.story_s8 import story_s8_detail_judge
    context = SimpleNamespace(task={'index': ['e001', 'b2']},
        inputs={'judgement': {'e001-b2': {'answer': 'yes', 'reason': '干潮を押印に置き換えている。'}}})
    result = story_s8_detail_judge(context)
    assert result.invalidations == [('S7.detail-e001-b2', '干潮を押印に置き換えている。')]
    monkeypatch.setattr('storyteller.story_s8._s7_invalidation_state', lambda context, target: (2, []))
    exhausted = story_s8_detail_judge(context)
    assert not exhausted.invalidations and exhausted.manifest_updates['warnings']


def test_f4_condition_failure_invalidates_the_offending_text():
    from storyteller.formatting import make_handlers, f2_id, judge_id
    invalidated = []
    plan = {'continuation': True, 'parts': [{'id': 'u1p1', 'conditions': ['干潮のときだけ道が現れる。']}],
            'code_checks': [], 'questions': load_profile('novel')['checks']['llm'][2:3]}
    write = f2_id('u1p1', 'novel')
    context = SimpleNamespace(task={'index': ['u1p1', 'novel']}, inputs={'plan': plan},
        dependency_outputs={write: '仲間が三人集まったので道が現れた。',
            judge_id('u1p1', 1, 'novel'): {'answer': 'yes', 'reason': '入力にない仲間の人数を条件に加えている。'}},
        invalidate_task=lambda task, reason: invalidated.append((task, reason)))
    result = make_handlers(ROOT)['format_f4_verify'](context)
    assert not result.output['passed'] and invalidated[0][0] == write
    assert '仲間の人数' in invalidated[0][1]


def test_skipped_condition_is_explicitly_undecided():
    from storyteller.plot_quality import conditions_from_outputs
    ids = condition_ids("e001")
    values = {ids[0]: "干潮のときだけ道が現れる。", ids[2]: "道を渡ると問題が解決する。"}
    conditions = conditions_from_outputs(values, ids)
    assert len(conditions) == 3 and "（未定）" in conditions
    assert values[ids[0]] in conditions and values[ids[2]] in conditions


def test_detail_judgement_keeps_full_body_and_conditions_within_model_context():
    from storyteller.cards import generate_task_card
    from storyteller.validation import output_char_count
    definition = create_story_orchestrator(ROOT).task_definitions["S8.detail"]
    event = {"what": "行" * 119 + "。", "result": "果" * 119 + "。",
             "conditions": ["条" * 159 + "。"] * 3}
    values = {"event": event, "body": "文" * 2499 + "。", "previous": "前" * 600}
    fitted = _fit_inputs(definition, values)
    assert fitted["body"] == values["body"] and fitted["event"] == event
    card = generate_task_card(definition, "a" * 32, inputs=values)
    assert 2 * len(card) + 8192 <= 16384


def test_exhausted_format_quality_failure_warns_and_continues(tmp_path):
    from storyteller.formatting import make_handlers, f2_id, judge_id
    from tests.test_story_s7_elements import _harness
    from storyteller.manifest import load_manifest, write_manifest
    harness, run = _harness(tmp_path)
    path = harness.run_dir(run) / "manifest.json"
    manifest = load_manifest(path)
    write = f2_id("u1p1", "novel")
    manifest["tasks"][write] = {**manifest["tasks"]["S6.expand"], "invalidations": 2}
    write_manifest(path, manifest)
    invalidated = []
    plan = {"continuation": True, "parts": [{"id": "u1p1"}], "code_checks": [],
            "questions": load_profile("novel")["checks"]["llm"][2:3]}
    context = SimpleNamespace(task={"index": ["u1p1", "novel"]}, run_dir=harness.run_dir(run),
        inputs={"plan": plan}, dependency_outputs={write: "仲間が三人集まったので道が現れた。",
            judge_id("u1p1", 1, "novel"): {"answer": "yes", "reason": "入力にない条件を加えている。"}},
        invalidate_task=lambda task, reason: invalidated.append((task, reason)))
    result = make_handlers(ROOT)["format_f4_verify"](context)
    assert not result.output["passed"] and not invalidated
    assert result.manifest_updates["warnings"]


def test_opposite_permission_outcomes_survive_s9_and_f1():
    story = _story([_thread(1, 'main', 'three-beat')],
                   [novel_event(1, 't001', 'setup'), novel_event(2, 't001', 'setup')])
    first = story['events'][0]
    first['result'] = 'ウネルの交渉により門の使用を許可される。'
    second = deepcopy(first)
    second.update(id='e002', result='ウネルの交渉により門の使用を拒否される。')
    story['events'][1] = second
    slots = [{'id': e['id'], 'thread': e['thread'], 'stage': e['stage']} for e in story['events']]
    outputs = {f'S7.assemble-{e["id"]}': e for e in story['events']}
    assert len(_assemble_events(slots, outputs)) == 2
    plan = build_plan(story, load_profile('novel'), ROOT)
    assert {e for p in plan['parts'] for e in p['event_ids']} == {'e001', 'e002'}


@pytest.mark.parametrize('kind', ['prohibition', 'resolution'])
def test_condition_repair_claim_retains_previously_decided_conditions(tmp_path, kind):
    base = create_story_orchestrator(ROOT)
    definitions = {'S7.condition': base.task_definitions['S7.condition']}
    for source in ('S3.assign', 'S6.expand'):
        definitions[source] = {'id': source, 'version': 1, 'kind': 'code', 'handler': source}
    specs = [TaskSpec(source, source) for source in ('S3.assign', 'S6.expand')]
    for number, cid in enumerate(condition_ids('e001')):
        specs.append(TaskSpec(cid, 'S7.condition', deps=('S3.assign', 'S6.expand',
                             *condition_ids('e001')[:number]),
                             index=('e001', ('trigger', 'prohibition', 'resolution')[number])))
    harness = Orchestrator(tmp_path / 'data', definitions,
        {'S3.assign': lambda context: {'world': {'object': _slot()['object']},
                                     'cast': [{'elements': {'taboo': '約束を破らない。'}}]},
         'S6.expand': lambda context: {'slots': [_slot()]}},
        harness_root=ROOT, repository_root=ROOT)
    run = harness.create_run(seed=1, task_specs=specs)
    prior = ['潮が引くと門が現れる。', '夜には門を開けない。']
    count = 1 if kind == 'prohibition' else 2
    for condition in prior[:count]:
        claim = harness.claim_next(run, executor_id='test')
        assert harness.submit(claim['ticket'], condition).accepted
    claim = harness.claim_next(run, executor_id='test')
    assert not harness.submit(claim['ticket'], '文' * 161).accepted
    repair = harness.claim_next(run, executor_id='test')
    assert REPAIR_ROLE in repair['card']
    assert all(condition in repair['card'] for condition in prior[:count])
    assert '変更・追加しない' in repair['card']


def _condition_card_harness(tmp_path, target, *, skip_kind=None, long=False):
    base = create_story_orchestrator(ROOT)
    event = _event()
    if long:
        event.update({field: field[0] * 119 + '。' for field in
                      ('when', 'where', 'why', 'intent', 'what', 'result', 'emotion')})
    slot = dict(_slot(), beats=[{'id': 'opening', 'name': '発端', 'definition': '開始を説明する。'}])
    conditions = ['発' * 159 + '。', '禁' * 159 + '。', '解' * 159 + '。']
    definitions = {target: deepcopy(base.task_definitions[target])}
    for source in ('S6.expand', 'S7.assemble'):
        definitions[source] = {'id': source, 'version': 1, 'kind': 'code', 'handler': source}
    definitions['S7.condition'] = {'id': 'S7.condition', 'version': 1, 'kind': 'llm',
        'element': 'text', 'output': 'text', 'card': {'role': '条件を書く。', 'steps': ['本文を書く。']},
        'validate': {'checks': [{'max_chars': {'n': 160}}]}, 'continuation': False,
        'max_attempts': 1, 'on_exhausted': 'skip'}
    target_id = 'S7.detail-e001-b1' if target == 'S7.detail' else 'S7.event-e001-what'
    specs = [TaskSpec('S6.expand', 'S6.expand'),
             TaskSpec('S7.assemble-e001', 'S7.assemble', index=('e001',))]
    for number, (cid, kind) in enumerate(zip(condition_ids('e001'), ('trigger', 'prohibition', 'resolution'))):
        deps = (condition_ids('e001')[number - 1],) if number else ()
        specs.append(TaskSpec(cid, 'S7.condition', deps=deps, index=('e001', kind)))
    specs.append(TaskSpec(target_id, target, deps=tuple(s.task_id for s in specs),
                          index=('e001', 'b1' if target == 'S7.detail' else 'what')))
    harness = Orchestrator(tmp_path / 'data', definitions,
        {'S6.expand': lambda context: {'slots': [slot]},
         'S7.assemble': lambda context: event}, harness_root=ROOT, repository_root=ROOT)
    run = harness.create_run(seed=1, task_specs=specs)
    for kind, condition in zip(('trigger', 'prohibition', 'resolution'), conditions):
        claim = harness.claim_next(run, executor_id='test')
        harness.submit(claim['ticket'], '長' * 161 if kind == skip_kind else condition)
    claim = harness.claim_next(run, executor_id='test')
    return harness, run, target_id, claim, conditions


@pytest.mark.parametrize('target', ['S7.event', 'S7.detail'])
@pytest.mark.parametrize('skip_kind', ['prohibition', 'trigger'])
def test_actual_claim_shows_skipped_condition_as_undecided(tmp_path, target, skip_kind):
    harness, run, task_id, claim, conditions = _condition_card_harness(
        tmp_path, target, skip_kind=skip_kind)
    values = json.loads((harness.task_dir(run, task_id) / 'input.json').read_text(encoding='utf-8'))
    assert len(values['conditions']) == 3
    assert values['conditions'][f'e001-{skip_kind}'] == '（未定）'
    assert '（未定）' in claim['card']
    assert sum(condition in claim['card'] for condition in conditions) == 2


def test_max_length_detail_can_claim_continuation_with_all_conditions(tmp_path):
    from storyteller.cards import input_char_count
    harness, run, task_id, claim, conditions = _condition_card_harness(
        tmp_path, 'S7.detail', long=True)
    values = json.loads((harness.task_dir(run, task_id) / 'input.json').read_text(encoding='utf-8'))
    definition = harness.task_definitions['S7.detail']
    assert definition['max_input_chars'] - input_char_count(definition, values) >= 1000
    assert all(condition in claim['card'] for condition in conditions)
    assert harness.submit(claim['ticket'], '人物は静かに歩き始めた。').accepted
    continuation = harness.claim_next(run, executor_id='test')
    assert continuation is not None
    assert all(condition in continuation['card'] for condition in conditions)
    assert 'これまでの出力の末尾' in continuation['card']
    assert harness.load_run(run)['tasks'][task_id]['state'] == 'claimed'
    assert harness.submit(continuation['ticket'], '歩' * 1499 + '。').accepted
    assert harness.advance(run)['status'] == 'completed'
