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
    opening = text.lstrip().lstrip('「『"').lstrip()
    if require_subject and not re.match(re.escape(name) + r"\s*[はが]", opening):
        errors.append(f"character_identity: 冒頭の主語は対象人物 {name} とする")
    # Remove quoted dialogue, including nested Japanese quotes. Quoted past
    # claims are not assertions about the current target's identity.
    stack: list[str] = []
    unquoted = []
    closing = {"「": "」", "『": "』", '"': '"'}
    for char in text:
        if stack and char == stack[-1]:
            stack.pop()
            unquoted.append(" ")
        elif char in closing:
            stack.append(closing[char])
            unquoted.append(" ")
        else:
            unquoted.append(" " if stack else char)
    text = "".join(unquoted)
    subject = r"(?:^|[\s、。!?;])" + re.escape(name) + r"\s*"
    calendar = facts.get("calendar", "")
    calendar = unicodedata.normalize("NFKC", calendar) if isinstance(calendar, str) else ""
    year = (rf"(?:{re.escape(calendar)})?\s*" if calendar else "") + r"([0-9]+)年"
    patterns = {
        "age": subject + r"(?:は(?:現在)?|の年齢は)\s*([0-9]+)歳(?!の(?:とき|時|頃|ころ)|当時|だった|であった|ではない|でない|ではなく)",
        "birth_year": subject + r"(?:(?:は|が)\s*" + year + r"(?:に生まれた|生まれ)|の生年は\s*" + year + r")(?!ではない|でない|ではなく|わけではない|のではない)",
    }
    for field, pattern in patterns.items():
        expected = facts.get(field)
        if isinstance(expected, bool) or not isinstance(expected, int):
            continue
        for match in re.finditer(pattern, text):
            actual = int(next(group for group in match.groups() if group is not None))
            if actual != expected:
                errors.append(f"character_identity: {name} の {field} は {expected}（出力: {actual}）")
    return errors
