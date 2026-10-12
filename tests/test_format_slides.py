"""P3-04: slides format (``formats/slides``) and its Marp syntax checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import storyteller.formatting as formatting
from storyteller import format_slides

from .test_format_novel import _event, _story, _thread

ROOT = Path(__file__).parents[1]
SLIDE = "# 旅立ち\n\n- 朝の水路を歩く\n- 扉が開く\n\n<!--\nカナは朝の水路を歩きました。\n-->"


def _slides_story(count: int) -> dict[str, Any]:
    events = [_event(n, "t001", "setup" if n == 1 else "turn") for n in range(1, count + 1)]
    return _story([_thread(1, "main", "three-beat")], events)


def test_profile_loads_and_one_event_is_one_slide_unit() -> None:
    profile = formatting.load_profile("slides")
    assert profile["unit"] == "slide" and profile["unit_mapping"] == "per_event"
    story = _slides_story(5)
    plan = formatting.build_plan(story, profile, ROOT)
    assert len(plan["units"]) == len(plan["parts"]) == 5
    assert all(len(part["event_ids"]) == 1 for part in plan["parts"])
    assert plan["code_checks"] == ["min_chars", "max_chars", "marp_syntax"]


def test_slide_count_is_events_plus_two_and_deck_passes_marp_check() -> None:
    story = _slides_story(4)
    template = (ROOT / "formats" / "slides" / "template.md").read_text(encoding="utf-8")
    units = [
        {"id": f"u0{n}", "title": "段階", "part": None, "body": SLIDE.replace("旅立ち", f"出来事{n}")}
        for n in range(1, 5)
    ]
    text = formatting.render_template(template, story, units, "slides")
    assert format_slides.check_deck(text, expected_slides=4 + 2) == []
    assert text.count("\n---\n") == 2 + 4  # front matter close + cover + 4 slides
    assert "# 試練：カナ" in text and "# おわり" in text
    # A missing slide is detected.
    assert format_slides.check_deck(text, expected_slides=7)


def test_check_slide_accepts_a_valid_slide_and_runs_through_check_body() -> None:
    assert format_slides.check_slide(SLIDE) == []
    profile = formatting.load_profile("slides")
    part = formatting.build_plan(_slides_story(2), profile, ROOT)["parts"][0]
    padded = SLIDE.replace("カナは", "カナは" + "あ" * part["min_chars"])
    assert formatting.check_body(padded, part, ["marp_syntax", "min_chars"], ROOT) == []
    assert formatting.check_body("# だけ", part, ["marp_syntax"], ROOT)


@pytest.mark.parametrize(
    "body",
    [
        "# 見出し\n\n本文\n",  # no notes
        "本文だけ\n<!-- ノート -->",  # no heading
        "# 見出し\n<!-- ノート -->",  # no body line
        "# 見出し\n1\n2\n3\n4\n<!-- ノート -->",  # 4 body lines
        "# 見出し\n本文\n---\n本文\n<!-- ノート -->",  # separator
        "# 見出し\n本文\n<!--   -->",  # empty notes
        "# 見出し\n本文\n<!-- ノート -->\n余分",  # trailing text
        "# 見出し\n## 二つ目\n本文\n<!-- ノート -->",  # second heading
        "# 見出し\n本文\n<!-- a --> <!-- b -->",  # two notes
    ],
)
def test_check_slide_rejects_malformed_slides(body: str) -> None:
    assert format_slides.check_slide(body)


def test_check_deck_rejects_broken_decks() -> None:
    assert format_slides.check_deck("# だけ")
    assert format_slides.check_deck("---\nmarp: false\n---\n# a")
    assert format_slides.check_deck("---\nmarp: true\n---\n# a\n\n---\n\n---\n# c")
    assert format_slides.check_deck("---\nmarp: true\n---\n# a\n<!-- x")
