"""P3-03: ``scene`` unit mapping and the Fountain syntax check.

``scene`` (format-pipeline.md section 4.1) groups consecutive events with the
same ``where`` into one scene; a change of ``where``, or a ``when`` holding a
phrase of ``tables/time_jumps.yaml``, starts a new scene.  Both the mapping and
the syntax check are deterministic code (P2).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .format_units import UNIT_MAPPERS, Unit, unit_id
from .tables import load_table


def _line(text: str) -> str:
    return " ".join(text.split())


def has_time_jump(when: str, phrases: list[str]) -> bool:
    """Return whether *when* holds a time-jump phrase."""

    return any(phrase in when for phrase in phrases)


def map_scene(
    story: Mapping[str, Any], profile: Mapping[str, Any], repository_root: Path
) -> list[Unit]:
    """Group consecutive events into scenes (``unit_mapping: scene``).

    The scene title is ``<where> - <when>`` of its first event (used as the
    Fountain scene heading).
    """

    phrases = load_table("time_jumps", repository_root=repository_root)["phrases"]
    threads = {thread["id"]: thread.get("part") for thread in story["threads"]}
    groups: list[list[Mapping[str, Any]]] = []
    for event in story["events"]:
        if groups:
            last = groups[-1][-1]
            same_place = _line(last["where"]) == _line(event["where"])
            if same_place and not has_time_jump(event["when"], phrases):
                groups[-1].append(event)
                continue
        groups.append([event])
    return [
        Unit(
            id=unit_id(position, len(groups)),
            title=f"{_line(group[0]['where'])} - {_line(group[0]['when'])}",
            event_ids=tuple(event["id"] for event in group),
            part=threads.get(group[0]["thread"]),
        )
        for position, group in enumerate(groups, start=1)
    ]


UNIT_MAPPERS["scene"] = map_scene


# --- Fountain syntax --------------------------------------------------------

_FORCED_HEADING = re.compile(r"^\.(?!\.)")
_PARENTHETICAL = re.compile(r"^\(.*\)$")


def fountain_errors(text: str, *, document: bool = False) -> list[str]:
    """Check Fountain syntax.  Returns messages (empty = valid).

    With ``document=False`` the text is the body of one scene: it must not hold
    a scene heading (the template adds it), a section (``#``) or a synopsis
    (``=``).  With ``document=True`` the text is a whole script: a title page
    (``Key: value`` lines) may open it, scene headings are allowed, and the
    script must hold at least one scene heading.  In both modes dialogue is a
    forced character cue (``@name``) set off by a blank line and followed
    directly by a dialogue or parenthetical line, a parenthetical stays inside
    a dialogue block, and notes (``[[ ]]``) and boneyard (``/* */``) are closed.
    """

    errors: list[str] = []
    stripped = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    if "/*" in stripped or "*/" in stripped:
        errors.append("Fountain：ボーンヤード（/* */）が閉じていません")
    without_notes = re.sub(r"\[\[.*?\]\]", "", stripped, flags=re.DOTALL)
    if "[[" in without_notes or "]]" in without_notes:
        errors.append("Fountain：ノート（[[ ]]）が閉じていません")
    lines = stripped.replace("\r\n", "\n").split("\n")
    start = 0
    if document:
        while start < len(lines) and re.match(r"^[A-Za-z][A-Za-z ]*:", lines[start]):
            start += 1
            while start < len(lines) and lines[start][:1] in {" ", "\t"}:
                start += 1
        if start and start < len(lines) and lines[start].strip():
            errors.append("Fountain：タイトルページの後に空行がありません")
    headings = 0
    state = "none"  # none | cue | dialogue
    previous_blank = True
    for number, raw in enumerate(lines[start:], start=start + 1):
        line = raw.rstrip()
        if not line.strip():
            if state == "cue":
                errors.append(f"Fountain：{number - 1}行目の人物名の直後に台詞がありません")
            state = "none"
            previous_blank = True
            continue
        if _FORCED_HEADING.match(line):
            headings += 1
            if not document:
                errors.append(f"Fountain：{number}行目にシーン見出しがあります（本文には書けません）")
            elif not previous_blank:
                errors.append(f"Fountain：{number}行目のシーン見出しの前に空行がありません")
            state = "none"
        elif line.startswith("#") or line.startswith("="):
            if not (document and line.startswith("===")):
                errors.append(f"Fountain：{number}行目が見出し・要約の記法で始まっています（{line[0]}）")
            state = "none"
        elif line.startswith("@"):
            if state != "none" or not previous_blank:
                errors.append(f"Fountain：{number}行目の人物名の前に空行がありません")
            if not line[1:].strip():
                errors.append(f"Fountain：{number}行目の人物名が空です")
            state = "cue"
        elif _PARENTHETICAL.match(line.strip()) and state in {"cue", "dialogue"}:
            state = "dialogue"
        elif line.strip().startswith("(") and state in {"cue", "dialogue"}:
            errors.append(f"Fountain：{number}行目のカッコが閉じていません")
        elif _PARENTHETICAL.match(line.strip()) and state == "none" and previous_blank:
            errors.append(f"Fountain：{number}行目のカッコ書きが台詞の外にあります")
        elif state in {"cue", "dialogue"}:
            state = "dialogue"
        elif line.startswith(">") and not line.rstrip().endswith("<"):
            if not line[1:].strip():
                errors.append(f"Fountain：{number}行目の遷移が空です")
        previous_blank = False
    if state == "cue":
        errors.append("Fountain：最後の人物名の直後に台詞がありません")
    if document and headings == 0:
        errors.append("Fountain：シーン見出しがありません")
    return errors


def check_fountain_syntax(body: str, part: Mapping[str, Any]) -> list[str]:
    """F4 code check ``fountain_syntax`` for the body of one F2 task."""

    return fountain_errors(body)
