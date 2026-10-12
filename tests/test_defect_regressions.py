import pytest
import yaml

from storyteller import formatting, format_slides
from storyteller.character_facts import supplement_character_outputs, assemble_character_sheet, render_character_sheet
from storyteller.world_facts import supplement_fact_outputs
from storyteller.glossary import build_glossary
from storyteller.orchestrator import _read_dependency_outputs, CodeTaskError
from tests.test_formatting import dummy_format, _start, _drive, ROOT
from tests.test_character_facts import _harness, _finish, _accepted_outputs


@pytest.mark.parametrize('exhaustion', ['attempts', 'invalidations'])
def test_skipped_slide_completes_after_each_limit(tmp_path, dummy_format, exhaustion):
    profile = {**formatting.load_profile('slides', formats_root=ROOT / 'formats'), 'name': 'dummy', 'chars_per_event': [10, 40]}
    (dummy_format / 'dummy/profile.yaml').write_text(yaml.safe_dump(profile, allow_unicode=True), encoding='utf-8')
    (dummy_format / 'dummy/template.md').write_text((ROOT / 'formats/slides/template.md').read_text(), encoding='utf-8')
    _, run_id, harness = _start(tmp_path, dummy_format, events=1)
    def respond(task_id, card):
        if task_id.startswith('F2.'):
            return {} if exhaustion == 'attempts' else 'あ' * 24 + '。'
        return '{"answer":"no","reason":"問題なし。"}'
    _drive(harness, run_id, respond)
    manifest = harness.load_run(run_id)
    assert manifest['status'] == 'completed'
    body = (harness.run_dir(run_id) / 'formats/dummy/dummy.md').read_text()
    assert '（未定）' in body
    assert format_slides.check_deck(body, expected_slides=3) == []
    skipped = [task for task in manifest['tasks'].values() if task['type'].startswith('F2.')]
    assert all(task['state'] == 'skipped' for task in skipped)
    assert all(task['tries'] == 5 if exhaustion == 'attempts' else task['invalidations'] == 2 for task in skipped)


@pytest.mark.parametrize('kind', ['S4', 'S5'])
def test_skipped_ancestor_preserves_earlier_accepted_fact(tmp_path, kind):
    first, skipped, current = [f'{kind}.fact-{name}' for name in ('first', 'skipped', 'current')]
    directory = tmp_path / 'tasks' / first
    directory.mkdir(parents=True)
    (directory / 'output.md').write_text('確定済み。', encoding='utf-8')
    manifest = {'tasks': {first: {'type': f'{kind}.fact', 'state': 'done', 'deps': []}, skipped: {'type': f'{kind}.fact', 'state': 'skipped', 'deps': [first]}}}
    task = {'type': f'{kind}.fact', 'deps': [skipped], 'index': ['current']}
    supplement = supplement_fact_outputs if kind == 'S4' else supplement_character_outputs
    outputs = supplement(tmp_path, manifest, task, {})
    assert outputs[first] == '確定済み。'
    assert skipped not in outputs


def test_done_output_loss_is_an_error_but_skipped_is_allowed(tmp_path):
    manifest = {'tasks': {'F2.write-a': {'state': 'done', 'type': 'F2.write'}}}
    task = {'deps': ['F2.write-a']}
    with pytest.raises(CodeTaskError, match='出力ファイルがありません'):
        _read_dependency_outputs(tmp_path, manifest, task)
    manifest['tasks']['F2.write-a']['state'] = 'skipped'
    assert _read_dependency_outputs(tmp_path, manifest, task) == {}


def test_all_missing_family_and_timeline_fields_stay_undecided(tmp_path):
    harness, run_id, _ = _harness(tmp_path)
    _finish(harness, run_id)
    outputs = _accepted_outputs(harness, run_id)
    entries = outputs['S5.fact_plan-c1']['facts']
    for entry in entries:
        if entry['key'].startswith(('family.', 'timeline.')):
            outputs.pop(f"S5.fact-{entry['id']}", None)
    sheet = assemble_character_sheet(outputs, 'c1')
    assert sheet['family'] and sheet['timeline']
    assert all(member == {'relationship': '（未定）', 'name': '（未定）'} for member in sheet['family'])
    assert all(row == {'year': None, 'event': '（未定）'} for row in sheet['timeline'])
    assert '| — | なし |' not in render_character_sheet(sheet, build_glossary(outputs))


def test_format_placeholder_requires_manifest_skipped(tmp_path, dummy_format):
    from types import SimpleNamespace
    from storyteller.manifest import write_manifest
    _, run_id, harness = _start(tmp_path, dummy_format, events=1)
    claim = harness.claim_next(run_id, executor_id='tester')
    manifest = harness.load_run(run_id)
    write = next(key for key, task in manifest['tasks'].items() if task['type'].startswith('F2.'))
    plan_path = harness.run_dir(run_id) / 'tasks/F1.split-dummy/output.json'
    import json
    plan = json.loads(plan_path.read_text())
    context = SimpleNamespace(task={'index': [plan['parts'][0]['id'], 'dummy']},
                              run_dir=harness.run_dir(run_id), inputs={'plan': plan}, dependency_outputs={})
    verify = formatting.make_handlers(ROOT)['format_f4_verify']
    manifest['tasks'][write]['state'] = 'done'
    write_manifest(harness.run_dir(run_id) / 'manifest.json', manifest)
    with pytest.raises(ValueError, match='省略されていない'):
        verify(context)
    manifest['tasks'][write]['state'] = 'skipped'
    write_manifest(harness.run_dir(run_id) / 'manifest.json', manifest)
    assert verify(context).output['passed'] is False


def test_partial_family_and_timeline_keep_accepted_fields(tmp_path):
    harness, run_id, _ = _harness(tmp_path)
    _finish(harness, run_id)
    outputs = _accepted_outputs(harness, run_id)
    full = assemble_character_sheet(outputs, 'c1')
    entries = outputs['S5.fact_plan-c1']['facts']
    for entry in entries:
        if entry['key'] in {'family.1.name', 'timeline.1.event'}:
            del outputs[f"S5.fact-{entry['id']}"]
    sheet = assemble_character_sheet(outputs, 'c1')
    assert sheet['family'][0] == {**full['family'][0], 'name': '（未定）'}
    expected = [{**row, 'event': '（未定）'} if index == 0 else row
                for index, row in enumerate(full['timeline'])]
    assert sheet['timeline'] == expected
