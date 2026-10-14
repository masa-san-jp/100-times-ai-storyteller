"""P1-53 public fixtures, never submitted model outputs."""
from copy import deepcopy
import json
import pytest
from storyteller.cards import generate_task_card, prepare_task_inputs
from tests.test_condition_premises import definition, probe_inputs

PROHIBITION_CASES = {
'can': {'condition':'封書を破いてはならない。','body':'封書を破けるようになった。','expected':'no'},
'want': {'condition':'封書を破いてはならない。','body':'封書を破きたいと思った。','expected':'no'},
'if': {'condition':'封書を破いてはならない。','body':'封書を破けば文字が現れると説明した。','expected':'no'},
'explicit-permission': {'condition':'封書を破いてはならない。','body':'規則が変わり、封書を破いてよいことになった。','expected':'yes'},
'actual': {'condition':'封書を破いてはならない。','body':'封書を破いた。','expected':'yes'},
'possible-and-actual': {'condition':'封書を破いてはならない。','body':'封書を破けるようになり、その封書を破いた。','expected':'yes'},
'necessary': {'condition':'押さなければ灯らない。','body':'押さずに灯った。','expected':'yes'},
'negated-consequence': {'condition':'押すと灯る。','body':'押したが灯らなかった。','expected':'yes'},
}
BASELINE_CARDS = {'S8.compare': {'role': '与えられた記述と確定条件の矛盾、出来事の再演を確認する。', 'steps': ['最初に比較の種類を確認する。conditions なら記述Aは固定条件、events なら記述Aは過去の出来事である。', 'conditions では、記述Aの前提・行動・帰結を記述Bと照合し、条件を追加・反転・置換していれば yes とする。固定条件には実施済みの行動・結果がなく、条件に従った記述Bを再演とは判定しない。未定の条件は判定しない。', '非言及は否定ではなく、それだけを矛盾にしない。前提→帰結から逆や唯一の必要条件を導かない。明示された禁止・必要条件・前後関係の追加・反転・置換は yes とする。', '矛盾は事実として両立しないことだけを指す（同一人物の同時の別場所、確定結果の理由なき反転、存在しないはずの人物・物の出現など）。心情・関係・状況の変化や展開は矛盾でない。', '入力にない同時性・同一場面・全人物の必須参加を補わない。人物・場所・時の差だけでは事実の矛盾でない。', 'events では矛盾と再演を別に判定する。同じ行動と結果を進展なく繰り返す場合も yes。共同主体から一部主体への変更や言い換えだけでは免除しない。入力で独立した別人物・別場所・別時の出来事と示される場合や結果が進展する場合は再演としない。主体差だけから独立性を補わない。 例：同じ朝の書庫で、Aは甲と乙が帳簿を開き記録を発見し、Bは乙が同じ帳簿で同じ記録を発見しただけなら、主体差があっても再演で yes。', '矛盾または進展のない再演があれば answer を yes、なければ no にする。', 'reason は判定理由を1文で書く。yes の場合は入力で実際に食い違う前提・行動・結果、または進展なく再演した行動・結果を具体的に示す。', '入力にない出来事や事情を付け加えない。'], 'output_example': '{"answer": "...", "reason": "..."}'}, 'S8.detail': {'role': '本文を検査。', 'steps': ['確定事項の追加・反転・置換は違反。前提・行動・帰結・順序を照合。同じ前後関係を逆転と判定しない。未定は対象外。', '条件適合は再演でない。直前の場面の行動・結果の進展ない再演を検査。', '非言及は否定ではなく、それだけを矛盾にしない。前提→帰結から逆や唯一の必要条件を導かない。明示された禁止・必要条件・前後関係の追加・反転・置換は yes とする。', '構造説明・内部 ID も検査。', '例：「押すと灯る」に「灯った」だけ／「押した」だけは no。押さなかった・灯らなかったと補わない。他の違反・再演も検査。', '違反なら answer=yes、なければ no。reason は食い違う前提・行動・帰結・順序か再演の行動・結果を具体的に1文。入力外は禁止。'], 'output_example': '{"answer": "...", "reason": "..."}'}}

@pytest.mark.parametrize('task', ['S8.compare', 'S8.detail'])
@pytest.mark.parametrize('name', PROHIBITION_CASES)
def test_public_prohibition_probe_preserves_inputs_and_budget(tmp_path, task, name):
    d = definition(task)
    case = PROHIBITION_CASES[name]
    inputs = probe_inputs(task, case)
    original = deepcopy(inputs)
    fitted = prepare_task_inputs(d, inputs)
    assert fitted == inputs == original
    card = generate_task_card(d, 'a' * 32, fitted, retry_reason='理' * 500)
    old = deepcopy(d)
    old['card'] = deepcopy(BASELINE_CARDS[task])
    assert len(card) <= len(generate_task_card(old, 'a' * 32, fitted, retry_reason='理' * 500))
    for text in ('実施の併記は検査', '帰結の非言及でも違反は yes', case['condition'], case['body']):
        assert text in card
    (tmp_path / 'input.json').write_text(json.dumps(inputs, ensure_ascii=False), encoding='utf-8')
    (tmp_path / 'card.md').write_text(card, encoding='utf-8')
    assert not (tmp_path / 'output.json').exists()


def test_compare_ability_example_uses_only_actual_inputs_for_reason():
    d = definition('S8.compare')
    assert d['version'] == 14
    steps = '\n'.join(d['card']['steps'])
    assert '能力獲得だけは no' in steps
    assert '禁止は能力の不存在を意味しない' in steps
    assert '能力・可能性は実施・許可・禁止解除でない' in steps
    assert '明示許可・禁止解除は条件反転で yes' in steps
    assert '開いた／許可への変更は yes' in steps
    assert '例を素材・理由に使わない' in steps
    assert 'reason はA/Bの食い違う' in steps
