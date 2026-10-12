"""Slides format (``slides``): Marp syntax checks (format-pipeline.md section 4.2).

Two checks, both pure functions of the text (P2):

* :func:`check_slide` validates what one F2 task writes: one slide made of a
  heading line, one to three body lines and a speaker-notes comment.
* :func:`check_deck` validates the assembled Marp deck (front matter, slide
  separators, and the slide count = events + 2).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

MAX_BODY_LINES = 3
CHECK_NAME = "marp_syntax"
_SEPARATOR = re.compile(r"^-{3,}\s*$")
_HEADING = re.compile(r"^# \S")


def check_slide(body: str, part: Mapping[str, Any] | None = None) -> list[str]:
    """Check one slide (heading, 1-3 body lines, speaker notes)."""

    errors: list[str] = []
    text = body.strip()
    if text.count("<!--") != 1 or text.count("-->") != 1:
        return ["Marp：スピーカーノート（<!-- … -->）がちょうど1つ必要です"]
    start, end = text.index("<!--"), text.index("-->")
    if end < start:
        return ["Marp：スピーカーノートの <!-- と --> の順序が不正です"]
    if text[end + 3:].strip():
        errors.append("Marp：スピーカーノートの後に文字があります")
    if not text[start + 4:end].strip():
        errors.append("Marp：スピーカーノートが空です")
    lines = [line.strip() for line in text[:start].splitlines() if line.strip()]
    if any(_SEPARATOR.match(line) for line in text.splitlines()):
        errors.append("Marp：1つの本文にスライドの区切り（---）があります")
    if not lines or not _HEADING.match(lines[0]):
        errors.append("Marp：1行目が見出し（「# 」で始まる行）ではありません")
        return errors
    if any(line.startswith("#") for line in lines[1:]):
        errors.append("Marp：見出しが2つ以上あります")
    content = lines[1:]
    if not 1 <= len(content) <= MAX_BODY_LINES:
        errors.append(f"Marp：本文は1〜{MAX_BODY_LINES}行にしてください（現在 {len(content)} 行）")
    return errors


def check_deck(text: str, expected_slides: int | None = None) -> list[str]:
    """Check an assembled Marp deck; optionally its slide count."""

    lines = text.strip().splitlines()
    if not lines or lines[0].strip() != "---":
        return ["Marp：先頭に front matter（---）がありません"]
    try:
        close = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return ["Marp：front matter が閉じていません"]
    if not any(line.strip() == "marp: true" for line in lines[1:close]):
        return ["Marp：front matter に marp: true がありません"]
    slides = [[]]
    for line in lines[close + 1:]:
        if _SEPARATOR.match(line.strip()):
            slides.append([])
        else:
            slides[-1].append(line)
    errors: list[str] = []
    for number, slide in enumerate(slides, start=1):
        content = [line for line in slide if line.strip()]
        if not content or not _HEADING.match(content[0].strip()):
            errors.append(f"Marp：{number}枚目が見出しで始まっていません")
    if text.count("<!--") != text.count("-->"):
        errors.append("Marp：コメント（<!-- -->）が閉じていません")
    if expected_slides is not None and len(slides) != expected_slides:
        errors.append(f"Marp：枚数が {len(slides)} 枚です（期待 {expected_slides} 枚 = 出来事数 + 2）")
    return errors
