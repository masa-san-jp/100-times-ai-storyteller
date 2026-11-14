"""F1.split v7: named_actor allows the cast named in the part's own events."""

from __future__ import annotations

from storyteller.format_review import expand_reviews

CHECK = [{"kind": "named_actor", "question": "入力にない人物が登場していますか", "severity": "error"}]
# The shape of the 2026-10-28 GX10 run: e001 names ヒトーサ in its text, but
# its `who` holds エリオネ only, so writing ヒトーサ was judged a new person.
STORY = {
    "cast": [
        {"id": "c1", "name": "エリオネ", "reading": "エリオネ"},
        {"id": "c2", "name": "ヒトーサ", "reading": "ヒトーサ"},
        {"id": "c3", "name": "灯守", "reading": "ヒモリ"},
        {"id": "c4", "name": "サトー", "reading": "サトー"},
    ],
    "events": [
        {"id": "e001", "who": ["c1"], "what": "エリオネはヒトーサからお守りを受け取った。",
         "result": "ヒモリの灯がともる。", "when": "夜明け前"},
        {"id": "e002", "who": ["c4"], "what": "サトーが去る。", "result": "静まる。"},
    ],
}
PART = {"id": "u01p1", "event_ids": ["e001"], "characters": [{"id": "c1", "text": "エリオネ"}]}


def _names(named_in_events: bool) -> list[str]:
    (review,) = expand_reviews(CHECK, PART, STORY, named_in_events=named_in_events)
    return [person["name"] for person in review["source"]["characters"]]


def test_v7_adds_the_cast_named_by_name_or_reading_in_the_parts_events() -> None:
    # ヒトーサ by name in `what`, 灯守 by reading in `result`; サトー is only in another part's event.
    assert _names(True) == ["エリオネ", "ヒトーサ", "灯守"]


def test_older_plans_keep_the_parts_characters_only() -> None:
    assert _names(False) == ["エリオネ"]
