"""P3-09 scoped semantic reviews through real claims and verification."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml

from storyteller import format_review, formatting
from storyteller.cards import InputBudgetError
from storyteller.orchestrator import ClaimError, _prepare_card_inputs
from storyteller.validation import SchemaValidationError, validate_document, validate_output
from tests.test_formatting import PROFILE, _completed_story_run, dummy_format, _drive
from storyteller.new_run import create_story_orchestrator

ROOT = Path(__file__).parents[1]
KINDS = ('event_coverage', 'named_actor', 'condition_conflict', 'replay')
CHECKS = [{'kind': kind, 'question': f'{kind}の質問',
           'severity': 'advisory' if kind == 'replay' else 'error'} for kind in KINDS]
BODY = 'カナが道を歩く。' + 'あ' * 15 + '。'
# Later parts tell another scene: a paragraph equal to the previous part's is a copy (F4 v3).
LATER_BODY = 'カナが道を歩く。' + 'い' * 15 + '。'


def reply(answer='no', **kwargs):
    return {'answer': answer, 'reason': '対象について判定した。',
            'source_quote': '', 'body_quote': '', 'subject': '', **kwargs}


def setup(tmp_path, dummy_format, version=3, continuation=True):
    profile = deepcopy(PROFILE)
    profile['continuation'] = continuation
    profile['checks']['llm'] = deepcopy(CHECKS)
    (dummy_format / 'dummy/profile.yaml').write_text(yaml.safe_dump(profile, allow_unicode=True), encoding='utf-8')
    data, run = _completed_story_run(tmp_path, events=3)
    story_path = data / 'runs' / run / 'story/story.json'
    story = json.loads(story_path.read_text(encoding='utf-8'))
    for event in story['events']:
        event['conditions'] = ['鐘が鳴る時だけ門を開く。']
    story_path.write_text(json.dumps(story, ensure_ascii=False), encoding='utf-8')
    h = create_story_orchestrator(data)
    h.task_definitions['F1.split']['version'] = version
    formatting.start_formatting(h, run, 'dummy', formats_root=dummy_format)
    h.advance(run)
    plan = output(h, run, 'F1.split-dummy')
    return h, run, plan, story


def output(h, run, task):
    return json.loads((h.task_dir(run, task) / 'output.json').read_text(encoding='utf-8'))


def submit(h, run, task, value):
    claim = h.claim_task(run, task, executor_id='test')
    result = h.submit(claim['ticket'], json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value)
    assert result.accepted, result.errors
    return claim


def test_targets_have_only_their_exact_source_and_review_inputs_are_scoped(tmp_path, dummy_format):
    h, run, plan, story = setup(tmp_path, dummy_format)
    part = plan['parts'][0]
    reviews = part['reviews']
    assert [r['kind'] for r in reviews] == ['event_coverage', 'event_coverage', 'named_actor', 'condition_conflict', 'replay']
    assert reviews[0]['source'] == {k: story['events'][0][k] for k in ('id', 'what', 'result')}
    assert reviews[2]['source'] == {'characters': [{'name': p['name'], 'reading': p['reading']} for p in story['cast'] if p['id'] in {c['id'] for c in part['characters']}]}
    assert reviews[3]['source'] == {'condition': '鐘が鳴る時だけ門を開く。'}
    assert reviews[4]['source'] == {}
    first, second = 'F2.write-u01p1-dummy', 'F2.write-u01p2-dummy'
    submit(h, run, first, BODY)
    with pytest.raises(ClaimError):
        h.claim_task(run, second, executor_id='test')
    claims = [h.claim_task(run, format_review.review_id(part['id'], n, 'dummy'), executor_id='test') for n in (1, 2)]
    for n, claim in enumerate(claims):
        inputs = json.loads((h.task_dir(run, format_review.review_id(part['id'], n + 1, 'dummy')) / 'input.json').read_text(encoding='utf-8'))
        assert inputs == {'review': reviews[n], 'body': BODY, 'format': 'dummy'}
        assert 'profile' not in inputs['review']['source']
        assert h.validate_claim(claim['ticket'])
    no_conditions = {**part, 'conditions': []}
    assert not format_review.expand_reviews([CHECKS[2]], no_conditions, story)


@pytest.mark.parametrize('mode', ['unknown', 'fabricated_source', 'fabricated_body', 'already_present', 'advisory', 'no_bad_quote', 'allowed_actor', 'skipped'])
def test_unconfirmed_or_advisory_does_not_discard_any_body(tmp_path, dummy_format, mode):
    h, run, plan, _ = setup(tmp_path, dummy_format)
    first = plan['parts'][0]
    source = first['reviews'][0]['source']
    body = (source['what'] + '。' + BODY) if mode == 'already_present' else BODY
    def respond(task, card):
        if task.startswith('F2.'):
            return body if 'u01p1' in task else LATER_BODY
        if mode == 'unknown':
            value = reply('unknown')
        elif mode == 'fabricated_source':
            value = reply('yes', source_quote='資料にない事実')
        elif mode == 'fabricated_body':
            value = reply('yes', source_quote=first['conditions'][0], body_quote='本文にない行動')
        elif mode == 'already_present' and '-b1-' in task:
            value = reply('yes', source_quote=source['what'])
        elif mode == 'advisory' and '-b5-' in task:
            value = reply('yes')
        elif mode == 'no_bad_quote':
            value = reply('no', body_quote='本文にない引用')
        elif mode == 'allowed_actor' and '-b3-' in task:
            value = reply('yes', subject='カナ', body_quote='カナが道を歩く。')
        else:
            value = reply()
        return json.dumps(value, ensure_ascii=False)
    if mode == 'skipped':
        submit(h, run, 'F2.write-u01p1-dummy', body)
        task = format_review.review_id(first['id'], 1, 'dummy')
        for _ in range(h.task_definitions['F4.review'].get('max_attempts', 5)):
            claim = h.claim_task(run, task, executor_id='test')
            assert not h.submit(claim['ticket'], '{}').accepted
        assert h.load_run(run)['tasks'][task]['state'] == 'skipped'
    log = _drive(h, run, respond)
    assert all(accepted for _, _, accepted in log)
    manifest = h.load_run(run)
    assert manifest['status'] == 'completed'
    assert all(t['invalidations'] == 0 for name, t in manifest['tasks'].items() if name.startswith('F2.'))
    assert (h.task_dir(run, 'F2.write-u01p1-dummy') / 'output.md').read_text(encoding='utf-8') == body
    verify = output(h, run, 'F4.verify-u01p1-dummy')
    assert verify['assessment'] == ('passed' if mode == 'advisory' else 'unresolved')
    assert verify['passed'] is (mode == 'advisory')
    assert any(('advisory:' if mode == 'advisory' else 'unresolved:') in w for w in manifest['warnings'])
    assert body in (h.run_dir(run) / 'formats/dummy/dummy.md').read_text(encoding='utf-8')


@pytest.mark.parametrize('kind', ['event_coverage', 'named_actor', 'condition_conflict', 'code'])
def test_real_evidence_and_code_failures_keep_existing_limits(tmp_path, dummy_format, kind):
    h, run, plan, _ = setup(tmp_path, dummy_format)
    part = plan['parts'][0]
    def respond(task, card):
        if task.startswith('F2.'):
            if 'u01p1' in task:
                if kind == 'code':
                    return '短い。'
                if kind == 'named_actor':
                    return 'ルウが道を歩く。' + 'あ' * 15 + '。'
                if kind == 'condition_conflict':
                    return '鐘が鳴らなくても門を開く。' + 'あ' * 10 + '。'
            return BODY
        value = reply()
        if 'u01p1' in task:
            if kind == 'condition_conflict' and '-b1-' in task:
                value = reply('unknown')
            if kind == 'event_coverage' and '-b1-' in task:
                value = reply('yes', source_quote=part['reviews'][0]['source']['what'])
            elif kind == 'named_actor' and '-b3-' in task:
                value = reply('yes', subject='ルウ', body_quote='ルウが道を歩く。')
            elif kind == 'condition_conflict' and '-b4-' in task:
                value = reply('yes', source_quote=part['conditions'][0], body_quote='鐘が鳴らなくても門を開く。')
        return json.dumps(value, ensure_ascii=False)
    _drive(h, run, respond)
    manifest = h.load_run(run)
    assert manifest['status'] == 'completed'
    record = manifest['tasks']['F2.write-u01p1-dummy']
    assert record['invalidations'] == 2
    assert record['state'] == ('done' if kind == 'condition_conflict' else 'skipped')
    if kind == 'condition_conflict':
        assert output(h, run, 'F4.verify-u01p1-dummy')['assessment'] == 'failed'
        assert any('unresolved:' in warning for warning in manifest['warnings'])
        assert any('最後の本文を採用' in warning for warning in manifest['warnings'])


def test_legacy_f1_with_typed_profile_uses_old_judge_and_unchanged_definition(tmp_path, dummy_format):
    h, run, plan, _ = setup(tmp_path, dummy_format, version=2)
    assert all('reviews' not in part for part in plan['parts'])
    assert plan['questions'] == [c['question'] for c in CHECKS]
    assert not any(t['type'] == 'F4.review' for t in h.load_run(run)['tasks'].values())
    old = h.task_definitions['F4.judge']
    assert old['version'] == 2
    assert validate_output(old, '{"answer":"no","reason":"問題なし"}', harness_root=ROOT).passed
    assert not validate_output(old, json.dumps(reply('unknown')), harness_root=ROOT).passed
    _drive(h, run, lambda task, card: '{"answer":"no","reason":"問題なし"}' if task.startswith('F4.') else BODY)
    assert h.load_run(run)['status'] == 'completed'


@pytest.mark.parametrize('continuation', [True, False])
def test_passed_reviews_cache_and_source_change_are_separate_from_legacy(tmp_path, dummy_format, continuation):
    h, run, plan, _ = setup(tmp_path, dummy_format, continuation=continuation)
    definition = h.task_definitions['F4.review']
    assert definition['max_input_chars'] == 14000
    assert all('truncate' not in value for value in definition['inputs'].values())
    _drive(h, run, lambda task, card: json.dumps(reply()) if task.startswith('F4.review') else BODY)
    assert h.load_run(run)['status'] == 'completed'
    assert output(h, run, 'F4.verify-u01p1-dummy')['assessment'] == 'passed'
    key = 'body' if continuation else 'body_once'
    inputs = json.loads((h.task_dir(run, 'F4.review-u01p1-b1-dummy') / 'input.json').read_text(encoding='utf-8'))
    assert inputs[key] == BODY
    from storyteller.cache import cache_key
    assert cache_key('F4.review', 1, inputs, seed=1) != cache_key('F4.judge', 2, inputs, seed=1)
    from storyteller.cache import lookup_cache
    record = h.load_run(run)['tasks']['F4.review-u01p1-b1-dummy']
    assert lookup_cache(h.data_dir, record['cache_key']) == (True, reply())
    altered = deepcopy(inputs)
    altered['review']['source']['what'] += '別の行動'
    assert cache_key('F4.review', 1, inputs, seed=1) != cache_key('F4.review', 1, altered, seed=1)
    for field in ('review', key):
        oversized = deepcopy(inputs)
        oversized[field] = ({**inputs['review'], 'source': {'condition': 'あ' * 14001}} if field == 'review' else 'あ' * 14001)
        with pytest.raises(InputBudgetError):
            _prepare_card_inputs(definition, oversized)


def test_slides_display_is_lossless_and_quotes_are_checked_against_raw_body():
    body = '# 見出し\r\n画面の本文\r\n<!--\r\n語りの全文。\r\n-->'
    definition = formatting.load_format_task_definitions()['F4.review']
    for key in ('body', 'body_once'):
        inputs = {'review': {'kind': 'replay', 'question': '再演か', 'severity': 'advisory', 'source': {}}, key: body, 'format': 'slides'}
        fitted = _prepare_card_inputs(definition, inputs)
        view = fitted[key]
        restored = view.removeprefix('【画面表示（見出し・本文）】\n').replace('\n【語り（スピーカーノート）】\n', '<!--').replace('\n【語りここまで】\n', '-->')
        assert restored == body
        assert inputs[key] == body
        assert format_review.assess(inputs['review'], reply('no', body_quote='【語り（スピーカーノート）】'), body)[0] == 'unresolved'
        assert format_review.card_inputs({**inputs, key: '# 不正なスライド'})[key] == '# 不正なスライド'
        assert format_review.card_inputs({**inputs, 'format': 'novel'})[key] == body


def test_typed_profile_and_review_schema_contracts():
    schema = ROOT / 'schemas/format-profile.schema.json'
    for name in ('novel', 'screenplay', 'slides'):
        assert formatting.load_profile(name)['checks']['llm'][0]['kind'] == 'event_coverage'
    for checks in ([CHECKS[0], '旧質問'], [{**CHECKS[3], 'severity': 'error'}], [{**CHECKS[0], 'kind': 'unknown'}]):
        profile = {**PROFILE, 'checks': {'code': [], 'llm': checks}}
        with pytest.raises(SchemaValidationError):
            validate_document(profile, schema)
    definition = formatting.load_format_task_definitions()['F4.review']
    for answer in ('yes', 'no', 'unknown'):
        assert validate_output(definition, json.dumps(reply(answer)), harness_root=ROOT).passed
    invalid = reply(); del invalid['subject']
    assert not validate_output(definition, json.dumps(invalid), harness_root=ROOT).passed
