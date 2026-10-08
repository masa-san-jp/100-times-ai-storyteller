"""Deterministic text quality checks and card input classification."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import Any


# These are processing context, including context embedded in S3 assignments.
_INSTRUCTION_KEYS = frozenset({
    "name_sound", "role_definition", "plot_context", "plot_requirements",
    "plot_type", "protagonist_role", "section", "viewpoint", "axis", "cliches",
    "stage_definition", "stage_guidance", "required_events", "absent_role_note",
    "beat", "climax", "role", "event_field",
})
_METADATA_KEYS = frozenset({"id", "set_id", "source", "sources", "kind"})
_ABSENT = object()


def split_input_value(value: Any) -> tuple[Any, Any]:
    """Partition nested assignment context without changing stored inputs."""
    if isinstance(value, Mapping):
        material, instructions = {}, {}
        for key, item in value.items():
            if key in _INSTRUCTION_KEYS:
                instructions[key] = item
            else:
                body, context = split_input_value(item)
                if body is not _ABSENT:
                    material[key] = body
                if context is not _ABSENT:
                    instructions[key] = context
        if instructions:
            # Keep a nested person's context associated with that person.
            for key in ("id", "name"):
                if key in value:
                    instructions.setdefault(key, value[key])
        return material or _ABSENT, instructions or _ABSENT
    if isinstance(value, list):
        parts = [split_input_value(item) for item in value]
        return ([body for body, _ in parts if body is not _ABSENT],
                [context for _, context in parts if context is not _ABSENT] or _ABSENT)
    return value, _ABSENT


def split_card_inputs(inputs: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    material, instructions = {}, {}
    for name, value in inputs.items():
        if name in _INSTRUCTION_KEYS:
            instructions[name] = value
        else:
            body, context = split_input_value(value)
            if body is not _ABSENT:
                material[name] = body
            if context is not _ABSENT:
                instructions[name] = context
    return material, instructions


def body_texts(value: Any) -> list[str]:
    """Extract prose values, excluding identifiers and source metadata."""
    if isinstance(value, str):
        return [unicodedata.normalize("NFC", value)]
    if isinstance(value, Mapping):
        return [text for key, item in value.items() if key not in _METADATA_KEYS
                for text in body_texts(item)]
    if isinstance(value, list):
        return [text for item in value for text in body_texts(item)]
    return []


def longest_common_substring(text: str, others: list[str]) -> str:
    """Find an exact shared substring, measuring NFC non-whitespace chars."""
    text = unicodedata.normalize("NFC", text)
    transitions: list[dict[str, int]] = [{}]
    links, lengths = [-1], [0]
    last = 0
    for character in text:
        current = len(transitions)
        transitions.append({})
        lengths.append(lengths[last] + 1)
        links.append(0)
        parent = last
        while parent >= 0 and character not in transitions[parent]:
            transitions[parent][character] = current
            parent = links[parent]
        if parent >= 0:
            target = transitions[parent][character]
            if lengths[parent] + 1 == lengths[target]:
                links[current] = target
            else:
                clone = len(transitions)
                transitions.append(transitions[target].copy())
                lengths.append(lengths[parent] + 1)
                links.append(links[target])
                while parent >= 0 and transitions[parent].get(character) == target:
                    transitions[parent][character] = clone
                    parent = links[parent]
                links[target] = links[current] = clone
        last = current
    best_text = ""
    best_start = best_end = best_score = 0
    for other in others:
        other = unicodedata.normalize("NFC", other)
        weights = [0]
        for character in other:
            weights.append(weights[-1] + (not character.isspace()))
        state = length = 0
        for end, character in enumerate(other, start=1):
            while state and character not in transitions[state]:
                state = links[state]
                length = lengths[state]
            if character in transitions[state]:
                state = transitions[state][character]
                length += 1
            else:
                length = 0
            score = weights[end] - weights[end - length]
            if score > best_score:
                best_text = other
                best_start, best_end, best_score = end - length, end, score
    return best_text[best_start:best_end]


def join_continuation(previous: str, chunk: str) -> tuple[str, tuple[str, ...]]:
    """Drop repeated paragraphs while preserving ordinary chunk boundaries."""
    if not previous:
        return chunk, ()
    known = [unicodedata.normalize("NFC", part.strip())
             for part in re.split(r"\n[ \t]*\n", previous) if part.strip()]
    grams = [{part[i:i + 3] for i in range(len(part) - 2)} for part in known]
    kept, warnings = [], []
    # Keep separators with the preceding paragraph to retain the original text.
    for match in re.finditer(r"(.*?)(\n[ \t]*\n|\Z)", chunk, re.DOTALL):
        paragraph = unicodedata.normalize("NFC", match[1].strip())
        if not paragraph:
            kept.append(match[0])
            continue
        current = {paragraph[i:i + 3] for i in range(len(paragraph) - 2)}
        duplicate = paragraph in known or any(
            union and len(current & earlier) / len(union) >= 0.8
            for earlier in grams if (union := current | earlier)
        )
        if duplicate:
            warnings.append("継続の重複段落を除去: " + paragraph[:80])
        else:
            kept.append(match[0])
            known.append(paragraph)
            grams.append(current)
    return previous + "".join(kept), tuple(warnings)
