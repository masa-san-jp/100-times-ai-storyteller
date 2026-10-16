"""P1-66: one YAML-owned skill role, complete prior skills, exact-copy repair."""
from copy import deepcopy
import json

import pytest
import yaml

from storyteller.cache import cache_key, cache_path, save_cache
from storyteller.cards import InputBudgetError, TaskCardError, generate_task_card, input_char_count
from storyteller.character_facts import SKILL_KEYS, prior_skill_values, selected_skill_steps
from storyteller.orchestrator import Orchestrator, TaskSpec, _prepare_card_inputs
from storyteller.seed import derive_task_seed
from storyteller.validation import validate_output
from storyteller.world_facts import specialize_fact_definition
from tests.test_character_facts import ROOT, _definition, _inputs
from tests.test_story_s5 import _character

BASIC = 'ＡＢＣの流れを整える'
APPLICATION = '別の対象の流れを整える'
JOINT = '仲間と分担して流れを整える'


def legacy():
    return yaml.safe_load((ROOT / 'tests/fixtures/s5-fact-v2.yaml').read_text(encoding='utf-8'))


def test_v3_contract_only_adds_version_inputs_and_yaml_skill_scopes():
    new, old = _definition(), legacy()
    expected = deepcopy(old)
    expected['version'] = 3
    expected['inputs'] = new['inputs']
    expected['card']['steps'] = new['card']['steps']
    assert new == expected
    assert {k: v for k, v in new['inputs'].items() if k not in ('fact_key', 'prior_skills')} == old['inputs']
    for name in ('fact_key', 'prior_skills'):
        assert new['inputs'][name]['required'] is True
        assert new['inputs'][name]['from'] == 'S3.assign'
        assert 'truncate' not in new['inputs'][name]
    assert selected_skill_steps(new, {'fact_key': 'occupation'}) == old['card']['steps']
    assert new['max_input_chars'] == 3000 and new['on_exhausted'] == 'skip'


@pytest.mark.parametrize('key', SKILL_KEYS)
def test_card_selects_one_yaml_role_without_mutating_other_material(key):
    definition = _definition()
    inputs = _inputs('text', fact_key=key, prior_skills=[BASIC] if key != 'skills.1' else [])
    before = deepcopy(inputs)
    card = generate_task_card(definition, 'ticket', inputs)
    for step in definition['card']['steps']:
        scope, separator, text = step.partition('：')
        if scope in SKILL_KEYS and separator:
            assert ('作り方の指示：' + text in card) is (scope == key)
        else:
            assert step in card
    assert inputs == before
    changed = deepcopy(definition)
    changed['card']['steps'] = [f'{scope}：定義側の担当{n}' for n, scope in enumerate(SKILL_KEYS)]
    assert selected_skill_steps(changed, inputs) == [f'作り方の指示：定義側の担当{SKILL_KEYS.index(key)}']


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'empty', 'unknown'])
def test_invalid_skill_scope_fails_closed(change):
    definition = _definition()
    inputs = _inputs('text', fact_key='skills.2')
    steps = definition['card']['steps']
    if change == 'missing':
        definition['card']['steps'] = [s for s in steps if not s.startswith('skills.3：')]
    elif change == 'duplicate':
        steps.append('skills.2：別の手順')
    elif change == 'empty':
        definition['card']['steps'] = [s if not s.startswith('skills.2：') else 'skills.2： ' for s in steps]
    else:
        inputs['fact_key'] = 'skills.4'
    with pytest.raises(TaskCardError):
        generate_task_card(definition, 'ticket', inputs)


def test_required_prior_skills_survive_budget_and_cannot_be_truncated():
    definition = _definition()
    inputs = _inputs('text', fact_key='skills.3', prior_skills=[BASIC, APPLICATION],
                     previous_facts=[{'id': 'c1', 'text': '先行事実' * 400} for _ in range(10)])
    fitted = _prepare_card_inputs(definition, inputs)
    assert fitted['prior_skills'] == [BASIC, APPLICATION] and fitted['fact_key'] == 'skills.3'
    assert input_char_count(definition, fitted) <= 3000
    for name in ('fact_key', 'prior_skills'):
        with pytest.raises(TaskCardError, match='required input is missing'):
            _prepare_card_inputs(definition, {k: v for k, v in inputs.items() if k != name})
    with pytest.raises(InputBudgetError):
        _prepare_card_inputs(definition, {**inputs, 'prior_skills': ['字' * 3000]})


@pytest.mark.parametrize('answer,duplicate', [
    ('  ABCの流れを整える\n', True), ('ＡＢＣの流れを整える', True),
    ('abcの流れを整える', False), ('ABC の流れを整える', False),
    ('ＡＢＣの流れを整える。', False), (APPLICATION, False),
])
def test_only_nfkc_and_outer_whitespace_exact_duplicates_are_rejected(answer, duplicate):
    source = _inputs('text', fact_key='skills.2', prior_skills=[BASIC])
    result = validate_output(_definition(), answer, source, harness_root=ROOT)
    assert result.passed is not duplicate
    if duplicate:
        assert result.repairable and any('no_repeated_skill:' in e and BASIC in e for e in result.errors)
    for definition, inputs in ((legacy(), source), (_definition(), {**source, 'fact_key': 'occupation'}),
                               (_definition('S4.fact'), source)):
        assert not any('no_repeated_skill' in e for e in validate_output(definition, answer, inputs, harness_root=ROOT).errors)


def _harness(tmp_path, *, definition=None, count=3, other=False):
    entries = [{'id': f'{person}-skills.{n}', 'key': f'skills.{n}', 'person_id': person,
                'label': '技能', 'element': 'text', 'max_chars': 40}
               for person in ('c1', 'c2') for n in range(1, 4)]
    assignment = {'cast': [_character('protagonist', 'c1', '本人'), _character('supporter', 'c2', '本人')],
                  'calendar': {'current_year': 300}, 'world_fact_tasks': [], 'world_tasks': [],
                  'character_fact_tasks': entries}
    sources = {'S3.assign': assignment, 'S5.name-c1': {'name': 'カナ', 'reading': 'カナ'},
               'S5.name-c2': {'name': 'ミオ', 'reading': 'ミオ'},
               'S4.calendar_name': {'name': 'カナ暦', 'reading': 'カナ'}}
    definitions = {'S5.fact': definition or _definition()}
    specs = []
    for task_id in sources:
        task_type, *index = task_id.split('-', 1)
        definitions[task_type] = {'id': task_type, 'version': 1, 'kind': 'code', 'handler': 'source'}
        specs.append(TaskSpec(task_id, task_type, index=tuple(index)))
    base = ('S3.assign', 'S5.name-c1', 'S4.calendar_name')
    for number in range(1, count + 1):
        prior = (f'S5.fact-c1-skills.{number - 1}',) if number > 1 else ()
        foreign = ('S5.fact-c2-skills.1',) if other and number == 2 else ()
        specs.append(TaskSpec(f'S5.fact-c1-skills.{number}', 'S5.fact', deps=(*base, *prior, *foreign),
                              index=(f'c1-skills.{number}',)))
    if other:
        specs.append(TaskSpec('S5.fact-c2-skills.1', 'S5.fact',
                              deps=('S3.assign', 'S5.name-c2', 'S4.calendar_name'), index=('c2-skills.1',)))
    h = Orchestrator(tmp_path, definitions, {'source': lambda c: sources[c.task_id]},
                     harness_root=ROOT, repository_root=ROOT)
    run = h.create_run(seed=66, task_specs=specs[:len(sources)])
    h.advance(run)
    h._add_tasks(run, 'S3.assign', specs[len(sources):])
    return h, run, entries


def _claim(h, run):
    claim = h.claim_next(run, executor_id='test')
    info = h.validate_claim(claim['ticket'])
    inputs = json.loads((h.task_dir(run, info['task_id']) / 'input.json').read_text(encoding='utf-8'))
    return claim, inputs


@pytest.mark.parametrize('exhaust', [False, True])
def test_public_duplicate_repair_and_skipped_ancestor_keep_prior_skills(tmp_path, exhaust):
    h, run, _ = _harness(tmp_path)
    claim, first = _claim(h, run)
    assert first['fact_key'] == 'skills.1' and first['prior_skills'] == []
    assert not any(dep.startswith('S5.fact-') for dep in h.load_run(run)['tasks']['S5.fact-c1-skills.1']['deps'])
    assert h.submit(claim['ticket'], BASIC).accepted
    for attempt in range(5 if exhaust else 1):
        claim, inputs = _claim(h, run)
        assert inputs['fact_key'] == 'skills.2' and inputs['prior_skills'] == [BASIC]
        rejected = h.submit(claim['ticket'], ' ABCの流れを整える ')
        assert not rejected.accepted and rejected.errors[0].startswith('no_repeated_skill:')
    if not exhaust:
        repair, _ = _claim(h, run)
        assert BASIC in repair['card'] and '先行技能とは異なる対象や状況' in repair['card']
        assert h.submit(repair['ticket'], APPLICATION).accepted
    third, inputs = _claim(h, run)
    assert inputs['fact_key'] == 'skills.3'
    assert inputs['prior_skills'] == ([BASIC] if exhaust else [BASIC, APPLICATION])
    if not exhaust:
        assert any(BASIC in row['text'] for row in inputs['previous_facts'])
    assert h.submit(third['ticket'], JOINT).accepted
    tasks = h.load_run(run)['tasks']
    assert h.load_run(run)['status'] == 'completed'
    assert tasks['S5.fact-c1-skills.2']['state'] == ('skipped' if exhaust else 'done')
    assert tasks['S5.fact-c1-skills.2']['tries'] == (5 if exhaust else 1)


def test_other_person_and_future_skills_never_enter_dedicated_prior_input(tmp_path):
    h, run, entries = _harness(tmp_path, other=True)
    other = h.claim_task(run, 'S5.fact-c2-skills.1', executor_id='test')
    assert h.submit(other['ticket'], '他人だけの技能').accepted
    first, inputs = _claim(h, run)
    assert inputs['prior_skills'] == [] and h.submit(first['ticket'], BASIC).accepted
    second, inputs = _claim(h, run)
    assert inputs['prior_skills'] == [BASIC]
    outputs = {f"S5.fact-{entry['id']}": entry['id'] for entry in entries}
    assert prior_skill_values(entries, outputs, 'c1-skills.2') == ['c1-skills.1']


def test_length_repair_retains_skill_role_and_every_prior_skill(tmp_path):
    h, run, _ = _harness(tmp_path)
    for value in (BASIC, APPLICATION):
        claim, _ = _claim(h, run)
        assert h.submit(claim['ticket'], value).accepted
    third, _ = _claim(h, run)
    rejected = h.submit(third['ticket'], '長' * 41)
    assert not rejected.accepted and any('max_chars' in error for error in rejected.errors)
    repair, inputs = _claim(h, run)
    assert inputs['prior_skills'] == [BASIC, APPLICATION]
    assert BASIC in repair['card'] and APPLICATION in repair['card']
    assert '先の2件とは異なる、他者との共同作業' in repair['card']
    assert h.submit(repair['ticket'], JOINT).accepted
    assert h.load_run(run)['status'] == 'completed'


@pytest.mark.parametrize('old_run', [False, True])
def test_legacy_card_and_cache_reuse_survive_while_v3_separates(tmp_path, old_run):
    old = legacy()
    h, run, _ = _harness(tmp_path, count=2, definition=old if old_run else _definition())
    first, _ = _claim(h, run)
    assert h.submit(first['ticket'], BASIC).accepted
    task_id = 'S5.fact-c1-skills.2'
    context = h._build_context(h.load_run(run), run, task_id)
    source = _prepare_card_inputs(old, {k: v for k, v in context.inputs.items() if k in old['inputs']})
    key = cache_key('S5.fact', 2, source, seed=derive_task_seed(66, task_id, 0))
    save_cache(h.data_dir, key, BASIC)
    before = cache_path(h.data_dir, key).read_bytes()
    claim = h.claim_next(run, executor_id='test')
    if old_run:
        assert claim is None
        card = generate_task_card(old, 'ticket', source)
        assert all(step in card for step in old['card']['steps']) and '作り方の指示：先行技能' not in card
    else:
        assert claim is not None
        assert h.load_run(run)['tasks'][task_id]['cache_key'] != key
        assert not h.submit(claim['ticket'], BASIC).accepted
        repair, _ = _claim(h, run)
        assert h.submit(repair['ticket'], APPLICATION).accepted
    assert h.load_run(run)['status'] == 'completed'
    assert cache_path(h.data_dir, key).read_bytes() == before


def test_other_fact_shapes_keep_their_v2_validation_and_steps():
    for shape, source in [('text', _inputs('text', fact_key='occupation')),
                          ('integer', _inputs('integer')), ('name', _inputs('name'))]:
        new = specialize_fact_definition(_definition(), source)
        old = specialize_fact_definition(legacy(), source)
        assert new['validate'] == old['validate']
        assert selected_skill_steps(new, source) == old['card']['steps']
