"""Deterministic planning for story-pipeline S8 comparisons."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from .manifest import load_manifest
from .orchestrator import CodeTaskContext, CodeTaskResult, TaskSpec


_EVENT_ID = re.compile(r"^e[0-9]{3}$")
_S7_MAX_INVALIDATIONS = 2


def story_s8_plan(context: CodeTaskContext) -> CodeTaskResult:
    """Choose comparison events from the completed S7 ``who`` fields.

    S6 deliberately does not choose comparison targets: the characters that
    actually appear in an event are only known after S7 has run.  This code
    task receives the current and all earlier S7 outputs as dependencies, so
    re-running it after an S7 invalidation naturally recalculates the list.
    """

    event_id = _event_id(context)
    slots = _slots_from_context(context)
    slot_index = next(
        (index for index, slot in enumerate(slots) if slot.get("id") == event_id),
        None,
    )
    if slot_index is None:
        raise ValueError(f"S6 の slot がありません: {event_id}")
    current_slot = slots[slot_index]
    current_output = _event_output(context.dependency_outputs, event_id)
    current_who = _who(current_output, event_id)

    previous_slots = slots[:slot_index]
    previous_same_thread = next(
        (
            slot
            for slot in reversed(previous_slots)
            if slot.get("thread") == current_slot.get("thread")
        ),
        None,
    )
    target_ids: list[str] = []
    if previous_same_thread is not None:
        target_ids.append(_slot_id(previous_same_thread))

    # Scan backwards once so that the nearest prior event wins when several
    # characters point to the same event.  The direct same-thread predecessor
    # is already (a), and must not be selected again as (b).
    current_who_set = set(current_who)
    for slot in reversed(previous_slots):
        target_id = _slot_id(slot)
        if target_id in target_ids:
            continue
        prior_output = _event_output(context.dependency_outputs, target_id)
        prior_who = _who(prior_output, target_id)
        if current_who_set.intersection(prior_who):
            target_ids.append(target_id)
        if len(target_ids) >= 3:
            break

    target_ids = target_ids[:3]
    compare_ids: list[str] = []
    additions: list[TaskSpec] = []
    for number, target_id in enumerate(target_ids, start=1):
        compare_id = f"S8.compare-{event_id}-k{number}"
        additions.append(
            TaskSpec(
                compare_id,
                "S8.compare",
                deps=(context.task_id,),
                index=(event_id, f"k{number}"),
            )
        )
        compare_ids.append(compare_id)

    judge_deps = (context.task_id, *compare_ids)
    additions.append(
        TaskSpec(
            f"S8.judge-{event_id}",
            "S8.judge",
            deps=judge_deps,
            index=(event_id,),
        )
    )
    output = {
        "slot": event_id,
        "comparison_targets": target_ids,
        "current": deepcopy(current_output),
        "targets": [
            {
                "id": target_id,
                "event": deepcopy(
                    _event_output(context.dependency_outputs, target_id)
                ),
            }
            for target_id in target_ids
        ],
    }
    # S7 invalidation causes this plan and its comparison/judge descendants to
    # be re-run.  The descendant task records remain in the DAG, so reuse them
    # instead of trying to add duplicate IDs on the second planning pass.
    run_dir = getattr(context, "run_dir", None)
    if run_dir is not None:
        manifest = load_manifest(run_dir / "manifest.json")
        additions = [
            addition
            for addition in additions
            if addition.task_id not in manifest.get("tasks", {})
        ]
    return CodeTaskResult(output=output, add_tasks=additions)


def story_s8_judge(context: CodeTaskContext) -> CodeTaskResult:
    """Invalidate an S7 event when any comparison reports a contradiction."""

    event_id = _judge_event_id(context)
    comparisons = context.inputs.get("comparisons")
    if comparisons is None:
        comparisons = []
    if isinstance(comparisons, Mapping):
        # S8.compare is indexed.  The selector contract therefore supplies
        # an index-to-output object, even when only one comparison exists.
        # Keep accepting one direct comparison for small/custom contexts.
        if "answer" in comparisons:
            comparisons = [comparisons]
        else:
            comparisons = list(comparisons.values())
    if not isinstance(comparisons, Sequence) or isinstance(comparisons, (str, bytes)):
        raise ValueError("S8.compare の出力が不正です")

    yes_results = [
        comparison
        for comparison in comparisons
        if isinstance(comparison, Mapping) and comparison.get("answer") == "yes"
    ]
    invalidated = bool(yes_results)
    invalidations: list[tuple[str, str]] = []
    manifest_updates: dict[str, list[str]] = {}
    if invalidated:
        reasons = [
            reason
            for comparison in yes_results
            for reason in [comparison.get("reason")]
            if isinstance(reason, str) and reason.strip()
        ]
        detail = "／".join(reasons)
        reason = (
            "比較結果に矛盾あり"
            if not detail
            else f"比較結果に矛盾あり：{detail}"
        )
        target_id = f"S7.event-{event_id}"
        invalidation_count, existing_warnings = _s7_invalidation_state(
            context, target_id
        )
        if invalidation_count is None or invalidation_count < _S7_MAX_INVALIDATIONS:
            invalidations.append((target_id, reason))
        else:
            warning = (
                f"{target_id}: 矛盾あり判定が無効化上限（{_S7_MAX_INVALIDATIONS}回）に達したため、"
                "最後の出力を採用"
            )
            if warning not in existing_warnings:
                manifest_updates["warnings"] = [warning]

    return CodeTaskResult(
        output={"slot": event_id, "invalidated": bool(invalidations)},
        invalidations=invalidations,
        manifest_updates=manifest_updates,
    )


def _s7_invalidation_state(
    context: CodeTaskContext,
    task_id: str,
) -> tuple[int | None, list[str]]:
    """Read the durable S7 invalidation count before requesting a retry.

    Lightweight unit-test contexts do not have a run directory; in those
    contexts the normal invalidation behavior remains observable.  A real
    orchestrator context always has the manifest, which is the source of the
    retry count shared across processes.
    """

    run_dir = getattr(context, "run_dir", None)
    if run_dir is None:
        return None, []
    manifest = load_manifest(run_dir / "manifest.json")
    tasks = manifest.get("tasks")
    if not isinstance(tasks, Mapping):
        raise ValueError("manifest の tasks がありません")
    task = tasks.get(task_id)
    if not isinstance(task, Mapping):
        raise ValueError(f"S7 のタスクがありません: {task_id}")
    count = task.get("invalidations")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError(f"S7 の invalidations が不正です: {task_id}")
    warnings = manifest.get("warnings", [])
    if not isinstance(warnings, list) or not all(
        isinstance(warning, str) for warning in warnings
    ):
        raise ValueError("manifest の warnings が不正です")
    return count, warnings


def _event_id(context: CodeTaskContext) -> str:
    task = getattr(context, "task", {})
    index = task.get("index") if isinstance(task, Mapping) else None
    if isinstance(index, list) and len(index) == 1 and isinstance(index[0], str):
        event_id = index[0]
    else:
        prefix = "S8.plan-"
        task_id = getattr(context, "task_id", "")
        event_id = task_id.removeprefix(prefix)
    if not _EVENT_ID.fullmatch(event_id):
        raise ValueError(f"S8.plan のスロットIDが不正です: {event_id}")
    return event_id


def _judge_event_id(context: CodeTaskContext) -> str:
    task = getattr(context, "task", {})
    index = task.get("index") if isinstance(task, Mapping) else None
    if isinstance(index, list) and len(index) >= 1 and isinstance(index[0], str):
        event_id = index[0]
    else:
        prefix = "S8.judge-"
        task_id = getattr(context, "task_id", "")
        event_id = task_id.removeprefix(prefix)
    if not _EVENT_ID.fullmatch(event_id):
        raise ValueError(f"S8.judge のスロットIDが不正です: {event_id}")
    return event_id


def _slots_from_context(context: CodeTaskContext) -> list[Mapping[str, Any]]:
    value = context.inputs.get("slots") if hasattr(context, "inputs") else None
    if value is None:
        value = context.dependency_outputs.get("S6.expand")
    if isinstance(value, Mapping):
        value = value.get("slots")
    if not isinstance(value, list) or not all(isinstance(slot, Mapping) for slot in value):
        raise ValueError("S6.expand の slots がありません")
    return value


def _slot_id(slot: Mapping[str, Any]) -> str:
    value = slot.get("id")
    if not isinstance(value, str) or not _EVENT_ID.fullmatch(value):
        raise ValueError("slot の ID が不正です")
    return value


def _event_output(
    dependency_outputs: Mapping[str, Any], event_id: str
) -> Mapping[str, Any]:
    task_id = f"S7.event-{event_id}"
    value = dependency_outputs.get(task_id)
    if not isinstance(value, Mapping):
        raise ValueError(f"S7 の出力がありません: {task_id}")
    return value


def _who(output: Mapping[str, Any], event_id: str) -> list[str]:
    value = output.get("who")
    if not isinstance(value, list) or not all(isinstance(person_id, str) for person_id in value):
        raise ValueError(f"S7 の who が不正です: {event_id}")
    return value


__all__ = ["story_s8_judge", "story_s8_plan"]
