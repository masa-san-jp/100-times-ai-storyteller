"""P1-42: code-owned comparison kinds and unchanged judgement contracts."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from storyteller.cache import save_cache
from storyteller.cards import generate_task_card, input_char_count, prepare_task_inputs, TaskCardError
from storyteller.orchestrator import Orchestrator, TaskSpec
from storyteller.story_quality import split_card_inputs
from storyteller.story_s8 import story_s8_judge, story_s8_plan
from storyteller.validation import validate_output

ROOT = Path(__file__).resolve().parents[1]


def definition(task):
    return yaml.safe_load((ROOT / f'harness/story/tasks/{task}.yaml').read_text(encoding='utf-8'))


def code(task, handler):
    return {'id': task, 'version': 1, 'kind': 'code', 'handler': handler}


def comparison_run(tmp_path):
    slots = [{'id': 'e001', 'thread': 't001'}, {'id': 'e002', 'thread': 't001'}]
    events = {
        'e001': {'who': ['c1'], 'what': 'カナは帳簿を開いた。', 'result': '通路を見つけた。',
                 'when': '夜', 'where': '書庫', 'conditions': ['閉じる前に紙を乾かせば記録を保てる。']},
        'e002': {'who': ['c1'], 'what': 'カナは紙を乾かした。', 'result': '記録を保った。',
                 'when': '朝', 'where': '書庫', 'conditions': ['閉じる前に紙を乾かせば記録を保てる。']},
    }
    original = deepcopy(events)
    defs = {'S6.expand': code('S6.expand', 'slots'),
            'S7.assemble': code('S7.assemble', 'event'),
            'S7.condition': code('S7.condition', 'condition'),
            **{task: definition(task) for task in ('S8.plan', 'S8.compare', 'S8.judge')}}
    handlers = {'slots': lambda context: {'slots': deepcopy(slots)},
                'event': lambda context: deepcopy(events[context.task['index'][0]]),
                'condition': lambda context: original['e001']['conditions'][0],
                'story_s8_plan': story_s8_plan, 'story_s8_judge': story_s8_judge}
    h = Orchestrator(tmp_path, defs, handlers)
    run = h.create_run(seed=12, tasks=[TaskSpec('S6.expand', 'S6.expand'),
        TaskSpec('S7.assemble-e001', 'S7.assemble', index=('e001',)),
        TaskSpec('S7.assemble-e002', 'S7.assemble', index=('e002',)),
        TaskSpec('S7.condition-e001-resolution', 'S7.condition', index=('e001', 'resolution')),
        TaskSpec('S8.plan-e002', 'S8.plan', deps=('S6.expand', 'S7.assemble-e001',
            'S7.assemble-e002', 'S7.condition-e001-resolution'), index=('e002',))])
    h.advance(run)
    return h, run, defs, original


def test_public_plan_to_claim_preserves_targets_and_marks_both_kinds(tmp_path):
    h, run, _, original = comparison_run(tmp_path)
    plan = json.loads((h.task_dir(run, 'S8.plan-e002') / 'output.json').read_text(encoding='utf-8'))
    assert plan['comparison_targets'] == ['e001']
    assert [target['comparison_kind'] for target in plan['targets']] == ['events', 'conditions']
    assert plan['targets'][0]['event'] == {k: v for k, v in original['e001'].items() if k != 'conditions'}
    assert plan['targets'][1]['conditions'] == original['e001']['conditions']
    seen = {}
    for ordinal in range(2):
        claim = h.claim_next(run, executor_id=f'test{ordinal}')
        assert claim is not None
        task_id = h.validate_claim(claim['ticket'])['task_id']
        fitted = json.loads((h.task_dir(run, task_id) / 'input.json').read_text(encoding='utf-8'))
        kind = fitted['comparison_kind']
        seen[kind] = fitted
        assert f'\n{kind}\n' in claim['card']
        assert fitted['event_b']['id'] == 'e002'
        assert fitted['event_b']['event'] == plan['current']
        assert input_char_count(h.task_definitions['S8.compare'], fitted) <= 2800
        material, instructions = split_card_inputs(fitted)
        assert instructions['comparison_kind'] == kind
        assert 'comparison_kind' not in material
    assert set(seen) == {'conditions', 'events'}
    assert seen['events']['event_a'] == plan['targets'][0]
    assert seen['conditions']['event_a'] == plan['targets'][1]
    for event in original:
        assert json.loads((h.task_dir(run, f'S7.assemble-{event}') / 'output.json').read_text(encoding='utf-8')) == original[event]
    # A comparison can be re-rendered without mutating its persisted plan.
    assert json.loads((h.task_dir(run, 'S8.plan-e002') / 'output.json').read_text(encoding='utf-8')) == plan


def test_old_comparison_version_cache_is_not_used_even_with_identical_new_input(tmp_path):
    h, run, defs, _ = comparison_run(tmp_path)
    task = 'S8.compare-e002-k1'
    manifest = h.load_run(run)
    context = h._build_context(manifest, run, task)
    fitted = prepare_task_inputs(context.definition, context.inputs)
    old = deepcopy(defs)
    old['S8.compare']['version'] -= 1
    old_harness = Orchestrator(tmp_path, old)
    old_key = old_harness._cache_key_for_task(manifest, task, fitted)
    new_key = h._cache_key_for_task(manifest, task, fitted)
    assert old_key != new_key
    save_cache(tmp_path, old_key, {'answer': 'no', 'reason': '古い判定'})
    claim = h.claim_next(run, executor_id='test')
    assert claim is not None
    assert h.validate_claim(claim['ticket'])['task_id'] == task
    assert h.load_run(run)['tasks'][task]['state'] == 'claimed'
    assert not (h.task_dir(run, task) / 'output.json').exists()


def test_comparison_kind_is_required_and_not_guessed_from_target_shape():
    d = definition('S8.compare')
    with pytest.raises(TaskCardError, match='comparison_kind'):
        generate_task_card(d, 'ticket', {'event_a': {'conditions': ['固定条件']}, 'event_b': {}})


@pytest.mark.parametrize('reason', ['固定条件では閉じる前の処置が必要だが、出来事では閉じた後に処置している。',
                                  '前の行動と結果を進展なく同じ場面で繰り返している。'])
def test_genuine_judgement_still_invalidates_what_without_code_reclassification(reason):
    # The code must not turn a yes into no because it dislikes the LLM reason.
    result = story_s8_judge(SimpleNamespace(task_id='S8.judge-e002', task={'index': ['e002']},
        inputs={'comparisons': [{'answer': 'yes', 'reason': reason}]}))
    assert result.output['invalidated'] is True
    assert result.invalidations and all(reason in detail for _, detail in result.invalidations)


def test_cards_keep_genuine_checks_and_only_add_scope_clarifications():
    compare = definition('S8.compare')
    steps = '\n'.join(compare['card']['steps'])
    assert all(text in steps for text in ('条件を追加・反転・置換', '進展なく繰り返す',
        '事実として両立しない', '同時性・同一場面・全人物の必須参加を補わない',
        '前提・行動・結果'))
    assert compare['element'] == 'judgement' and compare['max_input_chars'] == 2800
    assert validate_output(compare, json.dumps({'answer': 'yes', 'reason': '前後が逆転している。'}), harness_root=ROOT).passed
    assert not validate_output(compare, json.dumps({'answer': 'maybe', 'reason': '判定'}), harness_root=ROOT).passed
    detail = definition('S8.detail')
    assert detail['max_input_chars'] == 3500
    assert '同じ前後関係を逆転と判定しない' in '\n'.join(detail['card']['steps'])
    event = definition('S7.event')
    assert all(check in event['validate']['checks'] for check in ('event_subject', 'no_story_metadata', 'no_copy_from_inputs'))
    assert event['max_input_chars'] == 2800 and event['candidates'] == 3
    assert {'max_chars': {'n': 120}} in event['validate']['checks']
    assert 'すべて発動・解決する命令ではない' in '\n'.join(event['card']['steps'])
    assert '場所は場所、時は時、理由は原因' in '\n'.join(event['card']['steps'])
    assert '現在の段階' in '\n'.join(event['card']['steps'])
