"""Local plot constraints and deterministic prose/event quality gates."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any


CONDITION_KINDS = ("trigger", "prohibition", "resolution")
CONDITION_LABELS = {
    "trigger": "何をすると何が起こるかという発動条件",
    "prohibition": "禁じられている行動とその帰結",
    "resolution": "問題を解決できる条件",
}
_INTERNAL_ID = re.compile(
    r"(?<![A-Za-z0-9])(?:c[0-9]+|e[0-9]{3,}|t[0-9]{3,}|w[0-9]+|"
    r"m[0-9]{3,}|g[0-9a-f]{6}|[a-z_]+:[itx][0-9]+)(?![A-Za-z0-9])"
)
_STRUCTURE_EXPLANATION = re.compile(
    r"この(?:場面|出来事|段階|章)(?:は|が|の役割|の働き)|ここまでが|"
    r"(?:物語|場面|出来事)の構造|次の(?:場面|段階|転換)へつながる"
)
_POLARITY_OR_NUMBER = re.compile(r"[0-9]+|ない|なかった|禁止|せず|できず")


def selected_condition_steps(definition: Mapping[str, Any], inputs: Mapping[str, Any]) -> Sequence[str]:
    """Select one condition scope from the task definition, without authoring it."""
    steps = definition.get("card", {}).get("steps")
    if definition.get("id") != "S7.condition":
        return steps
    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)):
        raise ValueError("S7.condition card.steps must be a sequence")
    scopes = {}
    for step in steps:
        if not isinstance(step, str):
            raise ValueError("S7.condition card.steps must contain strings")
        key, separator, body = step.partition("：")
        if key in CONDITION_KINDS and separator:
            if key in scopes:
                raise ValueError(f"duplicate condition instruction: {key}")
            if not body.strip():
                raise ValueError(f"empty condition instruction: {key}")
            scopes[key] = body
    if not scopes and definition.get("version", 0) < 2:
        return steps
    missing = set(CONDITION_KINDS) - scopes.keys()
    if missing:
        raise ValueError("missing condition instruction: " + ", ".join(sorted(missing)))
    label = inputs.get("condition_kind")
    kind = next((key for key, value in CONDITION_LABELS.items() if value == label), None)
    if kind is None:
        raise ValueError("unknown condition_kind")
    result = []
    for step in steps:
        key, separator, body = step.partition("：")
        if key in CONDITION_KINDS and separator:
            if key == kind:
                result.append("作り方の指示：" + body)
        else:
            result.append(step)
    return result


def condition_ids(event_id: str) -> tuple[str, ...]:
    return tuple(f"S7.condition-{event_id}-{kind}" for kind in CONDITION_KINDS)


def conditions_from_outputs(
    outputs: Mapping[str, Any], expected_ids: Sequence[str] = (),
) -> list[str]:
    keys = sorted(expected_ids or [key for key in outputs if key.startswith("S7.condition-")])
    return [outputs[key] if isinstance(outputs.get(key), str) else "（未定）" for key in keys]


def normalize(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).lower().split())


def similarity(a: str, b: str) -> float:
    def grams(text: str) -> set[str]:
        text = normalize(text)
        return {text[i:i + 3] for i in range(len(text) - 2)}

    x, y = grams(a), grams(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def prose_errors(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text)
    errors = []
    if _INTERNAL_ID.search(text):
        errors.append("no_story_metadata: 内部 ID を本文に書かず、人物や場所の名前に直してください")
    if _STRUCTURE_EXPLANATION.search(text):
        errors.append("no_story_metadata: 構造の説明を除き、実際の行動と結果だけを書いてください")
    return errors


def unique_events(
    events: Sequence[Mapping[str, Any]], threshold: float,
) -> list[Mapping[str, Any]]:
    """Retain first occurrence; separate times/places or changed outcomes survive."""
    kept: list[Mapping[str, Any]] = []
    for event in events:
        duplicate = False
        for prior in kept:
            context_fields = ("thread", "when", "where", "who", "why", "intent", "conditions")
            if any(event.get(key) != prior.get(key) for key in context_fields):
                continue
            pairs = [(str(event.get(key, "")), str(prior.get(key, "")))
                     for key in ("what", "result")]
            if any("未定" in a or "未定" in b for a, b in pairs):
                continue
            # A small spelling change must not hide changed quantities or
            # negation, even when most of the surrounding sentence matches.
            if any(_POLARITY_OR_NUMBER.findall(normalize(a)) !=
                   _POLARITY_OR_NUMBER.findall(normalize(b)) for a, b in pairs):
                continue
            if normalize(pairs[1][0]) != normalize(pairs[1][1]):
                continue
            if all(normalize(a) == normalize(b) or similarity(a, b) >= threshold
                   for a, b in pairs):
                duplicate = True
                break
        if not duplicate:
            kept.append(event)
    return kept
