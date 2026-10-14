from copy import deepcopy
from types import SimpleNamespace

import pytest

from storyteller.scale import derive_scale, expansion_axes, ScaleError
from storyteller.tables import load_table
from storyteller.story_s2 import story_s2_plan
from storyteller.validation import validate_document, SchemaValidationError
from tests.test_dev_scale import ROOT


def plan(monkeypatch, scale):
    import storyteller.story_s2 as module
    monkeypatch.setattr(module, 'load_manifest', lambda _: {'scale': deepcopy(scale)})
    return story_s2_plan(SimpleNamespace(run_dir=ROOT, task_id='S2.plan', dependency_outputs={
        'S1.extract': {'id': 'm001', 'kind': 'theme', 'text': '境界を守る記録。'}}))


def test_dev_saves_catalog_order_and_keeps_pool_needs(monkeypatch):
    import storyteller.scale as module
    original = load_table('scales')
    axes = tuple(a['key'] for a in load_table('element_axes')['axes'])
    changed = deepcopy(original)
    changed['dev']['expansion_axes'] = [axes[2], axes[0]]
    monkeypatch.setattr(module, 'load_table', lambda name, **kw: changed if name == 'scales' else load_table(name, **kw))
    scale = derive_scale('dev', seed=7).value
    assert scale['derived']['expansion_axes'] == [axes[0], axes[2]]
    assert set(scale['derived']['pool_need']) == set(axes)
    result = plan(monkeypatch, scale)
    assert list(dict.fromkeys(p['axis']['key'] for p in result.output['plans'].values())) == [axes[0], axes[2]]


@pytest.mark.parametrize('invalid', [[], ['want', 'want'], ['unknown'], 'want', [None]])
def test_invalid_selection_rejected_in_derive_and_saved_plan(monkeypatch, invalid):
    import storyteller.scale as module
    table = load_table('scales')
    table['dev']['expansion_axes'] = invalid
    monkeypatch.setattr(module, 'load_table', lambda name, **kw: table if name == 'scales' else load_table(name, **kw))
    with pytest.raises(ScaleError):
        derive_scale('dev', seed=7)
    scale = {'preset': 'dev', 'derived': {'pool_need': {a['key']: 2 for a in load_table('element_axes')['axes']}, 'expansion_axes': invalid}}
    with pytest.raises(ScaleError):
        plan(monkeypatch, scale)


@pytest.mark.parametrize('invalid', [[], ['want', 'want'], 'want', [None]])
def test_selection_schema_rejects_bad_shapes(invalid):
    table = load_table('scales')
    table['dev']['expansion_axes'] = invalid
    with pytest.raises(SchemaValidationError):
        validate_document(table, ROOT / 'schemas/tables/scales.schema.json')


def test_representative_axis_only_and_old_dev_resume_keeps_all_axes(monkeypatch):
    scale = derive_scale('dev', seed=7).value
    selected = plan(monkeypatch, scale)
    assert {p['axis']['key'] for p in selected.output['plans'].values()} == {'want'}
    tasks = selected.add_tasks
    assert sum(t.type == 'S2.expand' for t in tasks) == scale['derived']['pool_need']['want']
    assert sum(t.type == 'S2.counter' for t in tasks) >= 1
    assert not any(getattr(t, 'state', None) == 'skipped' for t in tasks)
    old = deepcopy(scale)
    old['derived'].pop('expansion_axes')
    original = plan(monkeypatch, old)
    assert {p['axis']['key'] for p in original.output['plans'].values()} == set(old['derived']['pool_need'])
    # Recreating the planner must use the saved scale, never today's dev table.
    assert plan(monkeypatch, old) == original
    assert len(original.add_tasks) > len(selected.add_tasks)


@pytest.mark.parametrize('preset', ['vignette', 'short', 'novella', 'novel', 'saga'])
def test_production_plans_ignore_dev_only_selection(monkeypatch, preset):
    table = load_table('scales')
    scale = derive_scale(preset, seed=7).value
    assert 'expansion_axes' not in scale['derived']
    before = plan(monkeypatch, scale)
    ignored = deepcopy(scale)
    ignored['derived']['expansion_axes'] = ['want']
    assert plan(monkeypatch, ignored) == before

LEGACY_PLAN_HASHES = {'dev': 'fed713ab81df3ec20a230826703b65e13155c838ef9888aa5bcc6e8f93a14c07', 'vignette': '0e3ea2e100d1fd4566819eecd0539cfc4aef1a464074e70d3218a38f8ec92739', 'short': '0e3ea2e100d1fd4566819eecd0539cfc4aef1a464074e70d3218a38f8ec92739', 'novella': '62971ada7b241e828a226a3a5e69d690c67d228cf98145c8a666751ee23d1830', 'novel': '9d2ac2bdd87121bb9bf88448206c66725f2e6c11effa3960e2ed06c507cfe3f6', 'saga': '8ba4c80eaf750d06076447a193877b04a53ca40a51da59339169bdf828644c81'}

@pytest.mark.parametrize('preset', LEGACY_PLAN_HASHES)
def test_legacy_plan_tasks_and_inputs_are_exact(monkeypatch, preset):
    from dataclasses import asdict
    from hashlib import sha256
    import json
    scale = derive_scale(preset, seed=7).value
    scale['derived'].pop('expansion_axes', None)
    result = plan(monkeypatch, scale)
    encoded = json.dumps({'output': result.output, 'tasks': [asdict(t) for t in result.add_tasks]},
                         ensure_ascii=False, sort_keys=True).encode('utf-8')
    assert sha256(encoded).hexdigest() == LEGACY_PLAN_HASHES[preset]


def test_saved_legacy_dev_run_reopens_with_all_axes(tmp_path):
    import json
    from storyteller.new_run import create_story_orchestrator
    from storyteller.storage import atomic_write_json
    from tests.test_dev_scale import _new_dev_run, _manifest
    data_dir, run = _new_dev_run(tmp_path)
    manifest = _manifest(data_dir, run)
    manifest['scale']['derived'].pop('expansion_axes')
    atomic_write_json(data_dir / 'runs' / run / 'manifest.json', manifest)
    resumed = create_story_orchestrator(data_dir)
    for number in range(2):
        claim = resumed.claim_next(run, executor_id='test')
        assert claim is not None
        assert resumed.submit(claim['ticket'], json.dumps({
            'text': f'境界の向こうを確かめる観点{number}', 'kind': 'theme'}, ensure_ascii=False)).accepted
    resumed.advance(run)
    output = json.loads((resumed.task_dir(run, 'S2.plan') / 'output.json').read_text(encoding='utf-8'))
    assert {entry['axis']['key'] for entry in output['plans'].values()} == set(manifest['scale']['derived']['pool_need'])
    assert 'expansion_axes' not in resumed.load_run(run)['scale']['derived']
