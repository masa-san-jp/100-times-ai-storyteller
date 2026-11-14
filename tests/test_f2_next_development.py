"""#224: F2 writes the next development, and copies of the previous part are removed."""

from __future__ import annotations

from storyteller.formatting import strip_copied_paragraphs
from tests.test_formatting import _body, _claim_task, _drive, _good_responder, _start, dummy_format  # noqa: F401

NEW_LABEL = "### これまでの物語（直前の場面。参照用）"
NEW_STEP = "これまでの物語がある場合は、それに続く次の展開として、この出来事を書く。これまでの物語の文章は写さず、繰り返さない。"
# The shape gpt-oss:20b wrote with the v5 card on 2026-10-29: the previous
# part's last paragraph copied at the start, then a little new text.
COPIED = "氷結した水車の影は、二人の背後で揺らめきながらも、静かに光を受け止めていた。"


def test_strip_copied_paragraphs_removes_only_whole_paragraphs_of_the_previous_body() -> None:
    previous = "前の場面の書き出し。\n\n" + COPIED + "\n"
    body = COPIED + "\n\nホシは紙身体に最後のページを刻み、裏側に淡い光が宿った。\n\n短い。"
    assert strip_copied_paragraphs(body, previous) == (
        "ホシは紙身体に最後のページを刻み、裏側に淡い光が宿った。\n\n短い。", 1)
    # A sentence inside a longer paragraph, short paragraphs and no previous body stay.
    assert strip_copied_paragraphs("前置き。" + COPIED, previous)[1] == 0
    assert strip_copied_paragraphs("前の場面の書き出し。", previous)[1] == 0
    assert strip_copied_paragraphs(body, None) == (body, 0)


def test_the_card_asks_for_the_next_development_not_a_continuation(tmp_path, dummy_format) -> None:
    _, run, h = _start(tmp_path, dummy_format)
    good = _good_responder(h, run)
    cards = []

    def respond(task_id: str, card: str) -> str:
        cards.append((task_id, card))
        return good(task_id, card)

    _drive(h, run, respond)
    second = [card for task_id, card in cards if task_id == "F2.write-u01p2-dummy"]
    assert second and NEW_LABEL in second[0] and NEW_STEP in second[0]
    assert "直前の本文の末尾（最大600字）" not in second[0] and "そこから自然につなげる" not in second[0]


def test_a_paragraph_copied_from_the_previous_part_is_removed_and_counted_out(tmp_path, dummy_format) -> None:
    _, run, h = _start(tmp_path, dummy_format)
    good = _good_responder(h, run)

    def respond(task_id: str, card: str) -> str:
        if task_id == "F2.write-u01p1-dummy":
            return _body(task_id, h, run)[:-1] + "。\n\n" + COPIED
        if task_id == "F2.write-u01p2-dummy":
            return COPIED + "\n\n" + _body(task_id, h, run)
        return good(task_id, card)

    _drive(h, run, respond)
    manifest = h.load_run(run)
    assert manifest["status"] == "completed"
    assert manifest["tasks"]["F2.write-u01p2-dummy"]["invalidations"] == 0
    assert any("F2.write-u01p2-dummy: 直前の部分の本文と同じ段落をコードで除去：1段落" in warning
               for warning in manifest["warnings"])
    text = (h.run_dir(run) / "formats" / "dummy" / "dummy.md").read_text(encoding="utf-8")
    assert text.count(COPIED) == 1
    # The saved F2 output keeps what the executor wrote.
    saved = (h.run_dir(run) / "tasks" / "F2.write-u01p2-dummy" / "output.md").read_text(encoding="utf-8")
    assert saved.startswith(COPIED)
