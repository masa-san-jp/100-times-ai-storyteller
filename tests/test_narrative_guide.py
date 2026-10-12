"""The narrative guide (docs/guides/narrative.md) covers all 13 items and its examples pass PII detection."""

from __future__ import annotations

import json
import re
from pathlib import Path

from storyteller.narrative import MAX_ITEM_CHARS, NARRATIVE_ITEMS, read_narrative
from storyteller.pii_check import detect_narrative_pii

GUIDE = Path(__file__).resolve().parents[1] / "docs" / "guides" / "narrative.md"
_HEADING = re.compile(r"^### `(?P<key>[a-z]+)`（(?P<name>[^）]+)）$")
_EXAMPLE = re.compile(r"^- (?P<kind>良い例|書きすぎの例)：(?P<text>.+)$")


def _parse() -> dict[str, dict[str, list[str]]]:
    sections: dict[str, dict[str, list[str]]] = {}
    current: dict[str, list[str]] | None = None
    for line in GUIDE.read_text(encoding="utf-8").splitlines():
        heading = _HEADING.match(line)
        if heading:
            key = heading["key"]
            assert key not in sections, f"duplicate section: {key}"
            assert NARRATIVE_ITEMS[key][0] == heading["name"]
            current = sections[key] = {"良い例": [], "書きすぎの例": []}
            continue
        example = _EXAMPLE.match(line)
        if example and current is not None:
            current[example["kind"]].append(example["text"])
    return sections


def test_every_item_has_good_and_overwritten_examples() -> None:
    sections = _parse()
    assert list(sections) == list(NARRATIVE_ITEMS)
    for key, examples in sections.items():
        assert examples["良い例"], key
        assert examples["書きすぎの例"], key


def test_good_examples_fit_the_limit_and_pass_pii_detection() -> None:
    for key, examples in _parse().items():
        for text in examples["良い例"]:
            assert len(text) <= MAX_ITEM_CHARS, key
            assert detect_narrative_pii({key: text}) == [], key


def test_good_examples_form_an_importable_narrative(tmp_path: Path) -> None:
    items = {key: examples["良い例"][0] for key, examples in _parse().items()}
    path = tmp_path / "narrative.json"
    path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    assert read_narrative(path).items == items
