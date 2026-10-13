"""Pure selection of an event field's task-definition instructions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

EVENT_FIELDS = (
    "what", "where", "when", "why", "intent", "result", "emotion", "foreshadowing"
)
EVENT_LABELS = dict(zip(EVENT_FIELDS, (
    "何が起きたか", "どこで起きたか", "いつ起きたか", "出来事の理由",
    "人物の意思", "出来事の結果", "人物が受ける感情", "後の出来事への伏線",
)))


def selected_event_steps(definition: Mapping[str, Any], inputs: Mapping[str, Any]) -> Sequence[str]:
    """Validate the YAML's eight scopes and retain only the requested scope."""
    steps = definition.get("card", {}).get("steps")
    if definition.get("id") != "S7.event":
        return steps
    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)):
        raise ValueError("S7.event card.steps must be a sequence")
    scopes = {}
    for step in steps:
        if not isinstance(step, str):
            raise ValueError("S7.event card.steps must contain strings")
        key, separator, body = step.partition("：")
        if key in EVENT_FIELDS and separator:
            if key in scopes:
                raise ValueError(f"duplicate event field instruction: {key}")
            if not body.strip():
                raise ValueError(f"empty event field instruction: {key}")
            scopes[key] = body
    missing = set(EVENT_FIELDS) - scopes.keys()
    if missing:
        raise ValueError("missing event field instruction: " + ", ".join(sorted(missing)))
    label = inputs.get("event_field")
    field = next((key for key, value in EVENT_LABELS.items() if value == label), None)
    if field is None:
        raise ValueError("unknown event_field")
    result = []
    for step in steps:
        key, separator, body = step.partition("：")
        if key in EVENT_FIELDS and separator:
            if key == field:
                result.append("作り方の指示：" + body)
        else:
            result.append(step)
    return result
