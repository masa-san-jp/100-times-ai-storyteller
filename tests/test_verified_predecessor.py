"""P3-08: wait for a predecessor's existing verification, without changing its body."""
from copy import deepcopy
import json

import pytest
import yaml

from storyteller.formatting import start_formatting
from storyteller.orchestrator import ClaimError, InvalidClaimError
from tests.test_formatting import (
    PROFILE, _start, _drive, _good_responder, dummy_format,
)

FIRST = 'F2.write-u01p1-dummy'
SECOND = 'F2.write-u01p2-dummy'
JUDGE = 'F4.judge-u01p1-b1-dummy'
VERIFY = 'F4.verify-u01p1-dummy'
OLD = '最初の本文' + 'あ' * 43 + '。'
FIXED = '修正した本文' + 'い' * 42 + '。'


def setup(tmp_path, dummy_format, version=2):
    data, run, h = _start(tmp_path, dummy_format, events=3)
    h.task_definitions['F1.split']['version'] = version
    h.advance(run)
    return h, run


def submit(h, run, task, output):
    claim = h.claim_task(run, task, executor_id='test')
    result = h.submit(claim['ticket'], output)
    assert result.accepted, result.errors
    return claim


def answer(h, run, yes=False):
    return submit(h, run, JUDGE, json.dumps({
        'answer': 'yes' if yes else 'no',
        'reason': '入力にない人物が登場する。' if yes else '問題はない。',
    }, ensure_ascii=False))


@pytest.mark.parametrize('version', [1, 2])
def test_public_rejection_revokes_legacy_successor_but_v2_waits_for_corrected_body(tmp_path, dummy_format, version):
    h, run = setup(tmp_path, dummy_format, version)
    assert h.load_run(run)['tasks'][FIRST]['deps'] == ['F1.split-dummy']
    submit(h, run, FIRST, OLD)
    if version == 1:
        successor = h.claim_task(run, SECOND, executor_id='test')
        assert OLD in successor['card']
    else:
        with pytest.raises(ClaimError):
            h.claim_task(run, SECOND, executor_id='test')
    answer(h, run, yes=True)
    h.advance(run)
    tasks = h.load_run(run)['tasks']
    assert tasks[FIRST]['invalidations'] == 1
    assert tasks[SECOND]['state'] == 'blocked' and tasks[SECOND]['invalidations'] == 0
    if version == 1:
        with pytest.raises(InvalidClaimError):
            h.validate_claim(successor['ticket'])
        assert any(event.get('from') == 'claimed' and event['to'] == 'blocked'
                   for event in tasks[SECOND]['history'])
    else:
        assert not any(event['to'] == 'claimed' for event in tasks[SECOND]['history'])
    submit(h, run, FIRST, FIXED)
    if version == 2:
        with pytest.raises(ClaimError):
            h.claim_task(run, SECOND, executor_id='test')
    answer(h, run)
    h.advance(run)
    assert h.load_run(run)['tasks'][VERIFY]['state'] == 'done'
    successor = h.claim_task(run, SECOND, executor_id='test')
    assert FIXED in successor['card'] and OLD not in successor['card']
    assert h.submit(successor['ticket'], '後続の本文を確定する。').accepted
    _drive(h, run, _good_responder(h, run))
    assert h.advance(run)['status'] == 'completed'
    assert h.load_run(run)['tasks'][SECOND]['invalidations'] == 0


def test_no_rejection_keeps_exact_inputs_outputs_seeded_cache_and_artifacts(tmp_path, dummy_format):
    results = []
    for version in (1, 2):
        h, run = setup(tmp_path / str(version), dummy_format, version)
        log = _drive(h, run, _good_responder(h, run))
        manifest = h.load_run(run)
        assert manifest['status'] == 'completed'
        artifacts = {}
        keys = {}
        for name, task in manifest['tasks'].items():
            if task['kind'] == 'llm':
                directory = h.task_dir(run, name)
                artifacts[name] = ((directory / 'input.json').read_bytes(),
                                   next(directory.glob('output.*')).read_bytes())
                keys[name] = task['cache_key']
        results.append((artifacts, keys, (h.run_dir(run) / 'formats/dummy/dummy.md').read_bytes()))
        assert all(accepted for _, _, accepted in log)
    assert results[0] == results[1]


@pytest.mark.parametrize('quality_only', [False, True])
def test_skip_or_quality_limit_releases_next_without_successor_reexecution(tmp_path, dummy_format, quality_only):
    if quality_only:
        profile = deepcopy(PROFILE)
        profile['checks']['llm'] = ['同じ行動や結果を進展なく最初から語り直しているか？']
        (dummy_format / 'dummy/profile.yaml').write_text(yaml.safe_dump(profile, allow_unicode=True), encoding='utf-8')
    h, run = setup(tmp_path, dummy_format)
    good = _good_responder(h, run)

    def respond(task, card):
        return '{"answer":"yes","reason":"同じ行動を語り直している。"}' if task == JUDGE else good(task, card)

    log = _drive(h, run, respond)
    manifest = h.load_run(run)
    assert manifest['status'] == 'completed'
    assert manifest['tasks'][FIRST]['invalidations'] == 2
    assert manifest['tasks'][FIRST]['state'] == ('done' if quality_only else 'skipped')
    assert manifest['tasks'][VERIFY]['state'] == 'done'
    assert manifest['tasks'][SECOND]['state'] == 'done'
    assert manifest['tasks'][SECOND]['invalidations'] == 0
    assert sum(task == SECOND for task, _, _ in log) == 1
    assert any(('最後の本文を採用' if quality_only else '省略') in warning for warning in manifest['warnings'])
    assembled = (h.run_dir(run) / 'formats/dummy/dummy.md').read_text(encoding='utf-8')
    assert ('（未定）' in assembled) is (not quality_only)


def test_same_part_questions_and_other_formats_are_still_parallel(tmp_path, dummy_format):
    profile = deepcopy(PROFILE)
    profile['checks']['llm'].append('入力の出来事のうち、描かれていないものがありますか')
    (dummy_format / 'dummy/profile.yaml').write_text(yaml.safe_dump(profile, allow_unicode=True), encoding='utf-8')
    other = {**profile, 'name': 'other'}
    (dummy_format / 'other').mkdir()
    (dummy_format / 'other/profile.yaml').write_text(yaml.safe_dump(other, allow_unicode=True), encoding='utf-8')
    (dummy_format / 'other/template.md').write_text('{{#units}}{{unit.body}}{{/units}}', encoding='utf-8')
    h, run = setup(tmp_path, dummy_format)
    start_formatting(h, run, 'other', formats_root=dummy_format)
    h.advance(run)
    first = h.claim_task(run, FIRST, executor_id='test')
    foreign = h.claim_task(run, 'F2.write-u01p1-other', executor_id='test')
    assert h.validate_claim(foreign['ticket'])
    assert h.submit(first['ticket'], OLD).accepted
    judges = [h.claim_task(run, f'F4.judge-u01p1-b{n}-dummy', executor_id='test') for n in (1, 2)]
    assert all(h.validate_claim(claim['ticket']) for claim in judges)
    with pytest.raises(ClaimError):
        h.claim_task(run, SECOND, executor_id='test')


def test_finished_legacy_split_is_not_reexpanded_when_definition_version_changes(tmp_path, dummy_format):
    h, run = setup(tmp_path, dummy_format, version=1)
    before = {name: task['deps'] for name, task in h.load_run(run)['tasks'].items()}
    h.task_definitions['F1.split']['version'] = 2
    h.advance(run)
    after = {name: task['deps'] for name, task in h.load_run(run)['tasks'].items()}
    assert before == after
    assert VERIFY not in after[SECOND]
