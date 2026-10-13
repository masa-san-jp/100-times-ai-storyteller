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


# Only the requested field's scope is exposed to the executor.
EVENT_FIELD_INSTRUCTIONS = {
    "what": "現在の段階の働きを満たす行動だけを書く。割り当て人物の名前を冒頭に置き、は・がで主語にする。共同主語は割り当て人物名をと・、でつなぐ。ほかの人物への言及はできるが主語を置き換えない。",
    "where": "この出来事が起きる場所・位置だけを書く。完了した行動と結果全体を再述しない。",
    "when": "この出来事の時点・時間的な関係だけを書く。完了した行動と結果全体を再述しない。",
    "why": "先に決まった行動を引き起こす原因だけを書く。人物の動機をそのまま写さず、完了した行動と結果全体を再述しない。",
    "intent": "行動前に人物が目指すことだけを書く。達成済みの変化にせず、完了した行動と結果全体を再述しない。",
    "result": "先に決まった行動の後に確定する変化だけを書く。割り当て人物の名前を冒頭に置き、は・がで主語にする。共同主語は割り当て人物名をと・、でつなぐ。ほかの人物への言及はできるが主語を置き換えない。",
    "emotion": "この出来事を受けた人物の感情状態だけを書く。完了した行動と結果全体を再述しない。",
    "foreshadowing": "入力の物・言葉・状態に基づく未解消の手がかりだけを書く。入力にない将来の出来事を断定せず、完了した行動と結果全体を再述しない。",
}


def event_source_for_card(
    task: Mapping[str, Any], source: str, value: Any,
    dependency_outputs: Mapping[str, Any] | None = None,
) -> Any:
    """Bind the element index and strip internal task indexes from card inputs."""
    if source == "S6.expand":
        field = task["index"][1]
        return {
            **value,
            "event_field": EVENT_LABELS[field],
            "event_field_instruction": EVENT_FIELD_INSTRUCTIONS[field],
        }
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
    parent: str, event_id: str, first_deps: Sequence[str], previous_event: str | None, conditions: Sequence[str] = ()
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
                    deps=tuple(dict.fromkeys((*first_deps, *previous_result, *conditions))),
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
            deps=tuple(dict.fromkeys((*deps, *previous_result, *conditions))), index=(event_id, field),
        ))
        field_ids.append(task_id)
    additions.append(TaskSpec(
        f"S7.assemble-{event_id}", "S7.assemble",
        deps=(parent, *field_ids, *conditions), index=(event_id,),
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
    from .plot_quality import conditions_from_outputs
    conditions = conditions_from_outputs(context.dependency_outputs,
        [key for key in getattr(context, "task", {}).get("deps", ()) if key.startswith("S7.condition-")])
    if conditions:
        output["conditions"] = conditions
    output["sources"] = sources
    return CodeTaskResult(output=output)
