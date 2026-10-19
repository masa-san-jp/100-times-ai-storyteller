"""P1-61: fact consistency does not require listing the entire sheet."""
from tests.support.legacy_harness import legacy_story_harness, task_path  # noqa: F401
from copy import deepcopy
from pathlib import Path

import yaml

from storyteller.cache import cache_key
from storyteller.cards import generate_task_card, prepare_task_inputs
from tests.test_story_s5 import _character, _s5_inputs

ROOT = Path(__file__).resolve().parents[1]
OLD_FACT_STEP = '事実のシートに従い、年齢・生年・身長・出身地・居住地・所属・家族・年表・技能と矛盾させない。用語集 ID は対応する名前で表記し、事実にない数値や固有名詞を加えない。'
REQUIRED_STEP = '名前、役の定義、年齢、性別、種族、能力、願望、役割・課題を具体的に統合する。'


def definitions():
    new = yaml.safe_load((ROOT / 'harness/story/tasks/S5.profile.yaml').read_text(encoding='utf-8'))
    old = yaml.safe_load((ROOT / 'tests/fixtures/s5-profile-v13.yaml').read_text(encoding='utf-8'))
    return new, old


def test_only_profile_fact_scope_changes_and_required_eight_elements_remain():
    new, old = definitions()
    assert new['version'] == 15 and old['version'] == 13
    expected = deepcopy(old)
    expected['version'] = 15
    # ADR-0011 (S5.profile v15) adds the analysis of the whole input as the last, optional slot.
    expected['inputs']['analysis'] = new['inputs']['analysis']
    expected['card']['steps'][1] = new['card']['steps'][1]
    assert expected == new
    assert old['card']['steps'][1] == OLD_FACT_STEP
    assert REQUIRED_STEP in new['card']['steps']
    assert new['card']['steps'][0] == old['card']['steps'][0]
    assert '事実のシートと矛盾させない' in new['card']['steps'][1]
    assert '全項目の列挙は不要' in new['card']['steps'][1]


def test_inputs_budgets_and_exact_v13_card_stay_compatible():
    new, old = definitions()
    inputs = _s5_inputs('カナ', _character('protagonist', 'c1', '本人'))
    inputs['facts'] = {'name': 'カナ', 'age': 30, 'height_cm': 170, 'skills': ['流れの音を聞き分ける。']}
    original = deepcopy(inputs)
    fitted = prepare_task_inputs(new, inputs)
    assert fitted == prepare_task_inputs(old, inputs) and inputs == original
    old_card = generate_task_card(old, 'legacy-ticket', fitted, retry_reason='理' * 500)
    # Reconstruct the old public definition independently of the saved fixture.
    restored = deepcopy(new)
    restored['version'] = 13
    restored['card']['steps'][1] = OLD_FACT_STEP
    assert generate_task_card(restored, 'legacy-ticket', fitted, retry_reason='理' * 500) == old_card
    new_card = generate_task_card(new, 'legacy-ticket', fitted, retry_reason='理' * 500)
    assert len(new_card) <= len(old_card)
    assert REQUIRED_STEP in new_card
    assert cache_key('S5.profile', 13, fitted, seed=61) != cache_key('S5.profile', 14, fitted, seed=61)
