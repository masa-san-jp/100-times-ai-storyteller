"""Check explicit, current identity claims; never infer an omitted subject."""

from collections.abc import Mapping
import re
import unicodedata
from typing import Any


def identity_errors(output: str, facts: Any, *, require_subject: bool = False) -> list[str]:
    """Match the deliberately narrow grammar in story-pipeline §10.1."""
    if not isinstance(facts, Mapping) or not isinstance(facts.get("name"), str):
        return []
    name = unicodedata.normalize("NFKC", facts["name"])
    if not name:
        return []
    text = unicodedata.normalize("NFKC", output)
    errors = []
    if require_subject:
        opening = text.lstrip()
        subject_start = re.escape(name) + r"\s*[はが]"
        while (opening and opening[0] in '「『"'
               and not re.match(subject_start, opening)):
            opening = opening[1:].lstrip()
        if not re.match(subject_start, opening):
            errors.append(f"character_identity: 冒頭の主語は対象人物 {name} とする")
    calendar = facts.get("calendar", "")
    calendar = unicodedata.normalize("NFKC", calendar) if isinstance(calendar, str) else ""
    # Mark quoted dialogue without rewriting it: replacing a quoted name
    # with spaces could falsely join a subject to another calendar's year.
    # Literal registered names may themselves contain quotation marks.
    stack: list[str] = []
    quoted = [False] * len(text)
    closing = {"「": "」", "『": "』", '"': '"'}
    protected_names = tuple(value for value in (name, calendar)
                            if value and any(mark in value for mark in closing))
    position = 0
    while position < len(text):
        if not stack and protected_names:
            literal = next((value for value in protected_names
                            if text.startswith(value, position)), None)
            if literal is not None:
                position += len(literal)
                continue
        char = text[position]
        if stack and char == stack[-1]:
            quoted[position] = True
            stack.pop()
        elif char in closing:
            quoted[position] = True
            stack.append(closing[char])
        elif stack:
            quoted[position] = True
        position += 1
    subject = r"(?:^|[\s、。!?;])" + re.escape(name) + r"\s*"
    year = (rf"(?:{re.escape(calendar)})?\s*" if calendar else "") + r"([0-9]+)年"
    patterns = {
        "age": subject + r"(?:は(?:現在)?|の年齢は)\s*([0-9]+)歳(?!の(?:とき|時|頃|ころ)|当時|だった|であった|ではない|でない|ではなく|ではなかった|でなかった)",
        "birth_year": subject + r"(?:(?:は|が)\s*" + year + r"(?:に生まれた|生まれ)|の生年は\s*" + year + r")(?!ではない|でない|ではなく|わけではない|のではない|ではなかった|でなかった|わけでない|わけではなかった|のではなかった)",
    }
    for field, pattern in patterns.items():
        expected = facts.get(field)
        if isinstance(expected, bool) or not isinstance(expected, int):
            continue
        for match in re.finditer(pattern, text):
            if any(quoted[match.start():match.end()]):
                continue
            actual = int(next(group for group in match.groups() if group is not None))
            if actual != expected:
                errors.append(f"character_identity: {name} の {field} は {expected}（出力: {actual}）")
    return errors
