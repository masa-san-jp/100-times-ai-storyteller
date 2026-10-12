"""Generate element tasks and deterministically assemble S7 events."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .orchestrator import CodeTaskContext, CodeTaskResult, TaskSpec
from .story_s7_candidates import (
    candidate_settings, candidate_task_ids, is_candidate_task_id, select_task_id,
    selected_text,
)
from .task_outputs import task_sources
from .validation import output_char_count


EVENT_FIELDS = (
    "what", "where", "when", "why", "intent", "result", "emotion", "foreshadowing"
)
EVENT_LABELS = dict(zip(EVENT_FIELDS, (
    "何が起きたか", "どこで起きたか", "いつ起きたか", "出来事の理由",
    "人物の意思", "出来事の結果", "人物が受ける感情", "後の出来事への伏線",
)))


def event_source_for_card(
    task: Mapping[str, Any], source: str, value: Any,
    dependency_outputs: Mapping[str, Any] | None = None,
) -> Any:
    """Bind the element index and strip internal task indexes from card inputs."""
    if source == "S6.expand":
        return {**value, "event_field": EVENT_LABELS[task["index"][1]]}
    if source == "S7.event":
        decided = {}
        select_output = (dependency_outputs or {}).get(select_task_id(task["index"][0]))
        if select_output is not None:
            # The chosen candidate stands in for the candidates of ``what``.
            decided[EVENT_LABELS["what"]] = selected_text(select_output)
        for index, text in value.items():
            if not is_candidate_task_id(f"S7.event-{index}"):
                decided[EVENT_LABELS[index.rsplit("-", 1)[1]]] = text
        return decided
    if source == "S7.assemble":
        # Only the same-thread predecessor is a dependency of an element.
        return next(iter(value.values()))["result"]
    return value


def event_tasks(
    parent: str, event_id: str, first_deps: Sequence[str], previous_event: str | None
) -> list[TaskSpec]:
    """Keep every earlier field available, but only the previous event's result."""
    additions = []
    field_ids: list[str] = []
    previous_result = (previous_event,) if previous_event else ()
    count, _ = candidate_settings()
    for field in EVENT_FIELDS:
        if field == "what" and count > 1:
            # ``what`` is generated ``count`` times with the same card; the
            # code task S7.select picks one (task-model.md §7).
            ids = candidate_task_ids(event_id, count)
            for number, task_id in enumerate(ids, start=1):
                additions.append(TaskSpec(
                    task_id, "S7.event",
                    deps=tuple(dict.fromkeys((*first_deps, *previous_result))),
                    index=(event_id, field, f"c{number}"),
                ))
            pick = select_task_id(event_id)
            additions.append(TaskSpec(
                pick, "S7.select", deps=(parent, *ids), index=(event_id,),
            ))
            field_ids.extend((*ids, pick))
            continue
        task_id = f"S7.event-{event_id}-{field}"
        deps = first_deps if not field_ids else (parent, *field_ids)
        additions.append(TaskSpec(
            task_id, "S7.event",
            deps=tuple(dict.fromkeys((*deps, *previous_result))), index=(event_id, field),
        ))
        field_ids.append(task_id)
    additions.append(TaskSpec(
        f"S7.assemble-{event_id}", "S7.assemble",
        deps=(parent, *field_ids), index=(event_id,),
    ))
    return additions


def story_s7_assemble(context: CodeTaskContext) -> CodeTaskResult:
    """Preserve the canonical event shape without asking an LLM for who/sources."""
    slot = context.inputs["slot"]
    event_id = slot["id"]
    output = {"who": [person["id"] for person in slot["characters"]]}
    sources: list[str] = []
    for field in EVENT_FIELDS:
        task_id = f"S7.event-{event_id}-{field}"
        pick = select_task_id(event_id)
        if field == "what" and pick in context.dependency_outputs:
            chosen = context.dependency_outputs[pick]
            value = selected_text(chosen)
            task_id = f"S7.event-{event_id}-what-{chosen['candidate']}"
        elif task_id in context.dependency_outputs:
            value = context.dependency_outputs[task_id]
        else:
            output[field] = "（未定）"  # the element task was skipped (task-model §6.7)
            continue
        if not isinstance(value, str) or not 1 <= output_char_count(value) <= 120:
            raise ValueError(f"S7 の項目の本文が不正です: {task_id}")
        output[field] = value
        for source in task_sources(context.run_dir / "tasks" / task_id):
            if source not in sources:
                sources.append(source)
    output["sources"] = sources
    return CodeTaskResult(output=output)
