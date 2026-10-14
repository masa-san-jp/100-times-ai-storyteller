"""P1-58: concrete action/outcome instructions, unchanged legacy cards."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from storyteller.cards import generate_task_card, prepare_task_inputs
from storyteller.selectors import resolve_inputs
from storyteller.story_s7 import EVENT_FIELDS, event_source_for_card
from tests.test_story_s7 import _load_definition, _slot


LEGACY_CARD_SHA256 = {'what': '4d8379e276be47b5fb02c400d9d56acfe987d93c0a1e67e4f42a86a9f585128e', 'where': '8416aab7ed18ad71e08254dfec72d1042f80b351b67967b793174baa714fd4c6', 'when': 'fd3ccb6a529ee940bf0dd5f3ce041b0676f17709321e9b686d0f433807ffb6bb', 'why': '1ce1153c465ceb0c29d6616363d80fe549b1b9ca109249c325ca3dedc98175af', 'intent': '9a7798f50c0484da31e73ba5b96fc3fc6d2920bcaaef6a55b146ba821f07ea3d', 'result': '7b15b2e1ed226bdf07fb5443a7c9f8234e6ef712e96ed03ba0d8be7f22d4bb3a', 'emotion': 'c6a1a8795555bd4b89fb4533d7af7150cbf128ee1539c1e0ef9b19e3c830945a', 'foreshadowing': '14647d8e94b842799852fc760613d9cca78804768f43e73326c288b721ebeef3'}


def previous_definition():
    return yaml.safe_load((Path(__file__).parent / 'fixtures/s7-event-v19.yaml').read_text(encoding='utf-8'))


@pytest.mark.parametrize('field', EVENT_FIELDS)
def test_only_action_and_result_change_without_increasing_card_length(field):
    new, old = _load_definition(), previous_definition()
    assert new['version'] == 20 and old['version'] == 19
    expected = deepcopy(old)
    expected['version'] = 20
    for index, step in enumerate(new['card']['steps']):
        if step.partition('：')[0] in ('what', 'result'):
            expected['card']['steps'][index] = step
    assert new == expected
    source = event_source_for_card({'index': ('e001', field)}, 'S6.expand', {'slots': [_slot()]})
    inputs = resolve_inputs(new, outputs={'S6.expand': source}, index=('0', field))
    original = deepcopy(inputs)
    fitted = prepare_task_inputs(new, inputs)
    assert fitted == prepare_task_inputs(old, inputs) and original == inputs
    before = generate_task_card(old, 'a' * 32, fitted, retry_reason='理' * 500)
    after = generate_task_card(new, 'a' * 32, fitted, retry_reason='理' * 500)
    assert len(after) <= len(before)
    assert sha256(before.encode("utf-8")).hexdigest() == LEGACY_CARD_SHA256[field]
    # A frozen v19 definition renders identically, including field selection.
    assert generate_task_card(previous_definition(), 'a' * 32, fitted, retry_reason='理' * 500) == before
    if field in ('what', 'result'):
        assert '段階の達成宣言だけにしない' in after
        assert ('誰が入力素材をどう扱うか' if field == 'what' else '入力のどれがどう変わったか') in after
    else:
        assert after == before


def test_v19_and_v20_cache_are_separate_with_identical_inputs(tmp_path):
    from storyteller.orchestrator import Orchestrator
    from tests.test_story_s7_elements import _harness
    harness, run = _harness(tmp_path)
    claim = harness.claim_next(run, executor_id='test')
    manifest = harness.load_run(run)
    task = harness.validate_claim(claim['ticket'])['task_id']
    context = harness._build_context(manifest, run, task)
    fitted = prepare_task_inputs(context.definition, context.inputs)
    old = deepcopy(harness.task_definitions)
    old['S7.event'] = previous_definition()
    assert fitted == prepare_task_inputs(old['S7.event'], context.inputs)
    legacy = Orchestrator(tmp_path, old)
    assert legacy._cache_key_for_task(manifest, task, fitted) != harness._cache_key_for_task(manifest, task, fitted)
