"""Durable run and DAG processing for the Phase 0 orchestrator.

This module deliberately stops at the boundary between the orchestrator and
an executor.  It creates and advances a run, executes registered ``code``
tasks immediately, and leaves ``llm`` tasks ready for a later claim/submit
implementation.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import shutil
from copy import deepcopy
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from .cards import TaskCardError
from .manifest import load_manifest, write_manifest
from .seed import MAX_SEED, derive_task_seed, generated_seed, task_random
from .selectors import SelectorError, resolve_inputs
from .storage import atomic_write_json, manifest_lock
from .validation import validate_document


TASK_DEFINITION_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "schemas"
    / "task-definition.schema.json"
)

_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_TASK_TYPE = re.compile(r"^[A-Z][0-9]+\.[a-z][a-z0-9_]*$")
_RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
_MISSING: Final = object()

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "blocked": frozenset({"ready", "skipped"}),
    "ready": frozenset({"claimed", "done", "failed", "skipped"}),
    "claimed": frozenset({"done", "ready", "failed"}),
    "done": frozenset(),
    "failed": frozenset({"ready"}),
    "skipped": frozenset(),
}


class OrchestrationError(ValueError):
    """Base error for invalid run or DAG operations."""


class DAGError(OrchestrationError):
    """The task graph is invalid or violates a graph invariant."""


class InvalidTransition(OrchestrationError):
    """A task state transition is not allowed by the task model."""


class CodeTaskError(OrchestrationError):
    """A code task could not be executed or its result was invalid."""


@dataclass(frozen=True)
class TaskSpec:
    """A task node to add to a run's DAG.

    ``type`` names a validated task definition.  ``task_id`` may add an
    index, for example ``D2.echo-d1``.  Dynamic tasks must list the code task
    adding them in ``deps``; this is checked before the node is persisted.
    """

    task_id: str
    type: str
    deps: tuple[str, ...] = ()
    index: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not _TASK_ID.fullmatch(self.task_id):
            raise DAGError(f"invalid task id: {self.task_id!r}")
        if not isinstance(self.type, str) or not self.type:
            raise DAGError("task type must be a non-empty string")
        if any(not isinstance(value, str) or not value for value in self.deps):
            raise DAGError(f"invalid dependency in task {self.task_id!r}")
        if any(not isinstance(value, str) or not value for value in self.index):
            raise DAGError(f"invalid index in task {self.task_id!r}")


@dataclass
class CodeTaskResult:
    """The result of a registered code-task handler.

    A handler may return a raw JSON value instead.  The result class is used
    when it also needs to add DAG nodes or mark existing nodes as skipped.
    """

    output: Any = None
    add_tasks: list[TaskSpec] = field(default_factory=list)
    skip_tasks: list[str] = field(default_factory=list)


@dataclass
class CodeTaskContext:
    """Inputs and run-local helpers supplied to a code-task handler."""

    run_id: str
    task_id: str
    task: Mapping[str, Any]
    definition: Mapping[str, Any]
    inputs: Mapping[str, Any]
    run_input: Any
    outputs: Mapping[str, Any]
    dependency_outputs: Mapping[str, Any]
    data_dir: Path
    run_dir: Path
    seed: int
    random: Any
    _add_tasks: list[TaskSpec] = field(default_factory=list, repr=False)
    _skip_tasks: list[str] = field(default_factory=list, repr=False)

    def add_task(self, task: TaskSpec) -> TaskSpec:
        """Stage a dynamic task for addition after this handler succeeds."""
        self._add_tasks.append(task)
        return task

    def skip_task(self, task_id: str) -> None:
        """Stage an existing task to become ``skipped`` after success."""
        self._skip_tasks.append(task_id)


CodeTaskHandler = Callable[[CodeTaskContext], Any]


class Orchestrator:
    """Create runs and advance their code tasks.

    Task definitions are supplied by the caller because the CLI and harness
    discovery belong to later work items.  The manifest remains the durable
    source of DAG state, so a new ``Orchestrator`` can continue a run when the
    same definitions and handlers are supplied again.
    """

    def __init__(
        self,
        data_dir: str | Path,
        task_definitions: Mapping[str, Mapping[str, Any]],
        code_handlers: Mapping[str, CodeTaskHandler] | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve(strict=False)
        self.task_definitions = _validate_task_definitions(task_definitions)
        self.code_handlers = dict(code_handlers or {})
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def create_run(
        self,
        *,
        task_specs: Mapping[str, TaskSpec | Mapping[str, Any]]
        | Iterable[TaskSpec | Mapping[str, Any]]
        | None = None,
        tasks: Mapping[str, TaskSpec | Mapping[str, Any]]
        | Iterable[TaskSpec | Mapping[str, Any]]
        | None = None,
        seed: int | None = None,
        batch_id: str | None = None,
        seed_source: str | None = None,
        input_data: Any = None,
        input_type: str = "free",
        harness: Mapping[str, str] | None = None,
        scale: Mapping[str, Any] | None = None,
        table_snapshot: Mapping[str, Any] | None = None,
        run_id: str | None = None,
    ) -> str:
        """Create and persist a run, returning its run ID.

        ``tasks`` is retained as a readable alias for ``task_specs``.  If no
        nodes are supplied, one unindexed node is created for each task
        definition.  That default is useful for small internal tests; real
        harnesses provide the explicit DAG.
        """

        if task_specs is not None and tasks is not None:
            raise TypeError("pass only one of task_specs and tasks")
        raw_specs = task_specs if task_specs is not None else tasks
        specs = _normalise_task_specs(raw_specs, self.task_definitions)
        _validate_graph(specs, self.task_definitions)

        run_seed, resolved_seed_source = _resolve_run_seed(seed, seed_source)
        if batch_id is not None and (not isinstance(batch_id, str) or not batch_id):
            raise ValueError("batch_id must be a non-empty string or None")
        if input_type not in {"narrative", "free"}:
            raise ValueError("input_type must be narrative or free")

        created_at = _timestamp(self._clock)
        resolved_run_id = run_id or _make_run_id(created_at, run_seed)
        if not _RUN_ID.fullmatch(resolved_run_id):
            raise ValueError(f"invalid run_id: {resolved_run_id!r}")
        run_dir = self.run_dir(resolved_run_id)
        if run_dir.exists():
            raise FileExistsError(f"run already exists: {resolved_run_id}")

        run_dir.mkdir(parents=True, exist_ok=False)
        try:
            input_value = {} if input_data is None else input_data
            input_path = run_dir / "input.json"
            atomic_write_json(input_path, input_value)
            input_record: dict[str, str] = {}
            if input_data is not None:
                input_record = {
                    "type": input_type,
                    "sha256": _sha256_file(input_path),
                }

            manifest: dict[str, Any] = {
                "schema_version": 1,
                "run_id": resolved_run_id,
                "batch_id": batch_id,
                "created_at": created_at,
                "updated_at": created_at,
                "status": "active",
                "seed": run_seed,
                "seed_source": resolved_seed_source,
                "input": input_record,
                "scale": dict(scale or {}),
                "harness": dict(harness or {}),
                "table_snapshot": dict(table_snapshot or {}),
                "tasks": {},
                "warnings": [],
            }
            for spec in specs:
                record = _new_task_record(
                    spec, self.task_definitions[spec.type], manifest
                )
                manifest["tasks"][spec.task_id] = record
                self.task_dir(resolved_run_id, spec.task_id).mkdir(
                    parents=True, exist_ok=False
                )
            _update_run_status(manifest)
            write_manifest(run_dir / "manifest.json", manifest)
        except BaseException:
            # Remove the private directory created by this failed run attempt.
            shutil.rmtree(run_dir, ignore_errors=True)
            raise
        return resolved_run_id

    def run_dir(self, run_id: str) -> Path:
        """Return the durable directory for *run_id*."""
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise ValueError(f"invalid run_id: {run_id!r}")
        return self.data_dir / "runs" / run_id

    def task_dir(self, run_id: str, task_id: str) -> Path:
        """Return a task directory after rejecting path traversal."""
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            raise ValueError(f"invalid task id: {task_id!r}")
        return self.run_dir(run_id) / "tasks" / task_id

    def load_run(self, run_id: str) -> dict[str, Any]:
        """Load and validate a run manifest."""
        return load_manifest(self.run_dir(run_id) / "manifest.json")

    def advance(self, run_id: str) -> dict[str, Any]:
        """Execute ready code tasks until only external work remains.

        One manifest lock is held for each code task, including its handler
        call and durable write.  The lock is deliberately not held across the
        whole chain.
        """

        run_dir = self.run_dir(run_id)
        while True:
            with manifest_lock(run_dir):
                manifest = load_manifest(run_dir / "manifest.json")
                if manifest["status"] in {"halted", "duplicate"}:
                    _touch_manifest(manifest, self._now())
                    write_manifest(run_dir / "manifest.json", manifest)
                    return manifest
                _refresh_blocked_tasks(manifest, self._now())
                task_id = _next_ready_code_task(manifest)
                if task_id is None:
                    _update_run_status(manifest)
                    _touch_manifest(manifest, self._now())
                    write_manifest(run_dir / "manifest.json", manifest)
                    return manifest
                # ハンドラから Orchestrator を呼び戻さないこと。ここでは
                # 1コードタスクの読み込み・実行・書き込みだけをロックする。
                self._execute_code_task(manifest, run_id, task_id)
                _touch_manifest(manifest, self._now())
                _update_run_status(manifest)
                write_manifest(run_dir / "manifest.json", manifest)

    # This spelling describes the operation in the plan and is convenient for
    # tests without introducing a second implementation.
    process_code_tasks = advance

    def _transition_task(
        self,
        run_id: str,
        task_id: str,
        state: str,
        *,
        output: Any = _MISSING,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Apply one task-state transition without implementing claim/lease.

        This is the small state-machine seam used by later claim/submit code
        and by Phase 0 tests.  It records ``claimed`` as a state only; no
        claim file or lease is created here.
        """

        run_dir = self.run_dir(run_id)
        with manifest_lock(run_dir):
            manifest = load_manifest(run_dir / "manifest.json")
            task = _get_task(manifest, task_id)
            _validate_transition(manifest, task_id, state)
            if state == "done":
                if output is _MISSING:
                    raise InvalidTransition(
                        "a done task requires output when transitioned directly"
                    )
                self._write_task_output(run_id, task_id, output)
            _set_task_state(
                manifest,
                task_id,
                state,
                self._now(),
                reason=reason,
            )
            if state == "done":
                task["error"] = None
            elif state == "failed":
                task["error"] = reason
            _refresh_blocked_tasks(manifest, self._now())
            _update_run_status(manifest)
            _touch_manifest(manifest, self._now())
            write_manifest(run_dir / "manifest.json", manifest)
            return manifest

    def _complete_task(
        self,
        run_id: str,
        task_id: str,
        output: Any,
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Persist an output and move a ready/claimed task to ``done``."""
        return self._transition_task(
            run_id, task_id, "done", output=output, reason=reason
        )

    # A descriptive internal alias for tests that model an external executor.
    _mark_task_done = _complete_task

    def _skip_task(
        self,
        run_id: str,
        task_id: str,
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Move a blocked/ready task to ``skipped``."""
        return self._transition_task(
            run_id, task_id, "skipped", reason=reason or "不要になった"
        )

    def _add_tasks(
        self,
        run_id: str,
        parent_task_id: str,
        tasks: Iterable[TaskSpec | Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Add dynamic tasks whose dependencies include *parent_task_id*."""
        run_dir = self.run_dir(run_id)
        with manifest_lock(run_dir):
            manifest = load_manifest(run_dir / "manifest.json")
            additions = _normalise_task_specs(tasks, self.task_definitions)
            _validate_result_tasks(
                manifest,
                parent_task_id,
                additions,
                (),
                self.task_definitions,
            )
            _create_dynamic_task_dirs(run_id, additions, self.task_dir)
            _commit_dynamic_tasks(manifest, additions, self.task_definitions)
            _refresh_blocked_tasks(manifest, self._now())
            _update_run_status(manifest)
            _touch_manifest(manifest, self._now())
            write_manifest(run_dir / "manifest.json", manifest)
            return manifest

    def _execute_code_task(
        self,
        manifest: dict[str, Any],
        run_id: str,
        task_id: str,
    ) -> None:
        task = _get_task(manifest, task_id)
        definition = self.task_definitions[task["type"]]
        handler_name = definition["handler"]
        handler = self.code_handlers.get(handler_name)
        if handler is None:
            _fail_task(
                manifest,
                task_id,
                self._now(),
                f"コードタスクの処理が登録されていません: {handler_name}",
            )
            return

        created_dirs: list[Path] = []
        try:
            context = self._build_context(manifest, run_id, task_id)
            result = _call_handler(handler, context)
            normalised = _normalise_code_result(result, context)

            # Validate the complete result before writing output or changing
            # any task state.  This includes all additions as one graph.
            _validate_result_tasks(
                manifest,
                task_id,
                normalised.add_tasks,
                normalised.skip_tasks,
                self.task_definitions,
            )
            created_dirs = _create_dynamic_task_dirs(
                run_id,
                normalised.add_tasks,
                self.task_dir,
            )
            self._write_task_output(run_id, task_id, normalised.output)
        except Exception as error:  # code-task exceptions become failed tasks
            _remove_created_task_dirs(created_dirs)
            _fail_task(manifest, task_id, self._now(), _error_text(error))
            return

        _set_task_state(
            manifest,
            task_id,
            "done",
            self._now(),
            reason="コードタスクの実行に成功",
        )
        task["error"] = None
        _commit_dynamic_tasks(
            manifest,
            normalised.add_tasks,
            self.task_definitions,
        )
        for skipped_id in normalised.skip_tasks:
            _set_task_state(
                manifest,
                skipped_id,
                "skipped",
                self._now(),
                reason="コードタスクが不要と判断",
            )

    def _build_context(
        self,
        manifest: Mapping[str, Any],
        run_id: str,
        task_id: str,
    ) -> CodeTaskContext:
        task = _get_task(manifest, task_id)
        definition = self.task_definitions[task["type"]]
        dependency_outputs = _read_dependency_outputs(
            self.run_dir(run_id), manifest, task
        )
        source_outputs = _source_outputs(
            manifest, task, definition, dependency_outputs
        )
        run_input = _read_run_input(self.run_dir(run_id))
        try:
            inputs = resolve_inputs(
                definition,
                source_outputs,
                run_input,
                index=task["index"],
            )
        except (KeyError, SelectorError, TypeError) as error:
            raise TaskCardError(f"入力の生成に失敗しました: {error}") from error
        return CodeTaskContext(
            run_id=run_id,
            task_id=task_id,
            task=deepcopy(task),
            definition=definition,
            inputs=inputs,
            run_input=run_input,
            outputs=deepcopy(dependency_outputs),
            dependency_outputs=dependency_outputs,
            data_dir=self.data_dir,
            run_dir=self.run_dir(run_id),
            seed=derive_task_seed(
                int(manifest["seed"]), task_id, int(task["attempt"])
            ),
            random=task_random(
                int(manifest["seed"]), task_id, int(task["attempt"])
            ),
        )

    def _write_task_output(self, run_id: str, task_id: str, output: Any) -> None:
        atomic_write_json(self.task_dir(run_id, task_id) / "output.json", output)

    def _now(self) -> str:
        return _timestamp(self._clock)


# A shorter name is useful to callers that treat this as a run engine.
RunEngine = Orchestrator


def create_run(
    data_dir: str | Path,
    task_definitions: Mapping[str, Mapping[str, Any]],
    **kwargs: Any,
) -> str:
    """Convenience wrapper for creating one run."""
    code_handlers = kwargs.pop("code_handlers", None)
    clock = kwargs.pop("clock", None)
    return Orchestrator(
        data_dir,
        task_definitions,
        code_handlers,
        clock=clock,
    ).create_run(**kwargs)


def advance_run(
    data_dir: str | Path,
    run_id: str,
    task_definitions: Mapping[str, Mapping[str, Any]],
    *,
    code_handlers: Mapping[str, CodeTaskHandler] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for advancing one run's code tasks."""
    return Orchestrator(
        data_dir,
        task_definitions,
        code_handlers,
        clock=clock,
    ).advance(run_id)


def _validate_task_definitions(
    definitions: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not isinstance(definitions, Mapping) or not definitions:
        raise ValueError("task_definitions must be a non-empty mapping")
    result: dict[str, dict[str, Any]] = {}
    for key, definition in definitions.items():
        if not isinstance(key, str) or not key:
            raise ValueError("task definition keys must be non-empty strings")
        if not isinstance(definition, Mapping):
            raise ValueError(f"task definition must be a mapping: {key}")
        copied = dict(definition)
        validate_document(copied, TASK_DEFINITION_SCHEMA_PATH)
        if copied["id"] != key:
            raise ValueError(f"task definition key does not match id: {key}")
        result[key] = copied
    return result


def _normalise_task_specs(
    raw_specs: Mapping[str, TaskSpec | Mapping[str, Any]]
    | Iterable[TaskSpec | Mapping[str, Any]]
    | None,
    definitions: Mapping[str, Mapping[str, Any]],
) -> list[TaskSpec]:
    if raw_specs is None:
        return [TaskSpec(task_id=task_type, type=task_type) for task_type in definitions]
    if isinstance(raw_specs, Mapping):
        iterable: Iterable[TaskSpec | Mapping[str, Any]] = (
            _mapping_with_task_id(task_id, value)
            for task_id, value in raw_specs.items()
        )
    else:
        iterable = raw_specs
    specs: list[TaskSpec] = []
    seen: set[str] = set()
    for raw in iterable:
        spec = _coerce_task_spec(raw)
        if spec.task_id in seen:
            raise DAGError(f"duplicate task id: {spec.task_id}")
        if spec.type not in definitions:
            raise DAGError(f"task definition does not exist: {spec.type}")
        _validate_task_spec_id(spec)
        seen.add(spec.task_id)
        specs.append(spec)
    return specs


def _mapping_with_task_id(
    task_id: str,
    value: TaskSpec | Mapping[str, Any],
) -> TaskSpec | Mapping[str, Any]:
    if isinstance(value, TaskSpec):
        if value.task_id != task_id:
            raise DAGError(f"task mapping key does not match task_id: {task_id}")
        return value
    if not isinstance(value, Mapping):
        raise DAGError(f"task spec must be a mapping: {task_id}")
    merged = dict(value)
    merged.setdefault("task_id", task_id)
    return merged


def _coerce_task_spec(raw: TaskSpec | Mapping[str, Any]) -> TaskSpec:
    if isinstance(raw, TaskSpec):
        return raw
    if not isinstance(raw, Mapping):
        raise DAGError("task spec must be a TaskSpec or mapping")
    task_id = raw.get("task_id", raw.get("id"))
    task_type = raw.get("type", raw.get("task_type"))
    if task_type is None and isinstance(task_id, str):
        task_type = task_id
    deps = raw.get("deps", ())
    index = raw.get("index", ())
    if isinstance(deps, str) or not isinstance(deps, Sequence):
        raise DAGError(f"deps must be a sequence: {task_id!r}")
    if isinstance(index, str) or not isinstance(index, Sequence):
        raise DAGError(f"index must be a sequence: {task_id!r}")
    return TaskSpec(
        task_id=task_id,
        type=task_type,
        deps=tuple(deps),
        index=tuple(index),
    )


def _validate_graph(
    specs: Sequence[TaskSpec],
    definitions: Mapping[str, Mapping[str, Any]],
) -> None:
    if not specs:
        raise DAGError("task graph must not be empty")
    if len({spec.task_id for spec in specs}) != len(specs):
        raise DAGError("task graph contains duplicate task IDs")
    task_ids = {spec.task_id for spec in specs}
    specs_by_id = {spec.task_id: spec for spec in specs}
    for spec in specs:
        if spec.type not in definitions:
            raise DAGError(f"task definition does not exist: {spec.type}")
        _validate_task_spec_id(spec)
        if any(dependency not in task_ids for dependency in spec.deps):
            missing = next(
                dependency for dependency in spec.deps if dependency not in task_ids
            )
            raise DAGError(
                f"task {spec.task_id!r} depends on missing task {missing!r}"
            )
        if definitions[spec.type]["kind"] not in {"code", "llm"}:
            raise DAGError(f"invalid task kind: {spec.type}")
        _validate_input_dependencies(spec, specs_by_id, definitions[spec.type])

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise DAGError(f"task graph contains a cycle at {task_id}")
        if task_id in visited:
            return
        visiting.add(task_id)
        spec = next(spec for spec in specs if spec.task_id == task_id)
        for dependency in spec.deps:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for spec in specs:
        visit(spec.task_id)


def _validate_task_spec_id(spec: TaskSpec) -> None:
    """Ensure a task ID is exactly its type plus its declared index."""
    if not _TASK_TYPE.fullmatch(spec.type):
        raise DAGError(f"invalid task type: {spec.type!r}")
    expected = spec.type
    if spec.index:
        expected = f"{expected}-{'-'.join(spec.index)}"
    if spec.task_id != expected or not _TASK_ID.fullmatch(spec.task_id):
        raise DAGError(
            f"task id {spec.task_id!r} does not match type {spec.type!r} "
            f"and index {list(spec.index)!r}"
        )


def _validate_input_dependencies(
    spec: TaskSpec,
    specs_by_id: Mapping[str, TaskSpec],
    definition: Mapping[str, Any],
) -> None:
    input_slots = definition.get("inputs", {})
    if not isinstance(input_slots, Mapping):
        return
    for slot_name, slot in input_slots.items():
        if not isinstance(slot, Mapping):
            continue
        source = slot.get("from")
        if source == "input":
            continue
        if not isinstance(source, str):
            raise DAGError(
                f"task {spec.task_id!r} input slot {slot_name!r} has no valid source"
            )
        if not any(
            specs_by_id[dependency].type == source for dependency in spec.deps
        ):
            raise DAGError(
                f"task {spec.task_id!r} input slot {slot_name!r} requires a "
                f"dependency of type {source!r}"
            )


def _new_task_record(
    spec: TaskSpec,
    definition: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    deps_done = all(
        dependency in manifest["tasks"]
        and manifest["tasks"][dependency]["state"] in {"done", "skipped"}
        for dependency in spec.deps
    )
    return {
        "type": spec.type,
        "kind": definition["kind"],
        "state": "ready" if deps_done else "blocked",
        "deps": list(spec.deps),
        "index": list(spec.index),
        "attempt": 0,
        "tries": 0,
        "invalidations": 0,
        "continuation_step": 0,
        "cache_key": None,
        "claim": None,
        "history": [],
        "error": None,
    }


def _get_task(manifest: Mapping[str, Any], task_id: str) -> dict[str, Any]:
    try:
        task = manifest["tasks"][task_id]
    except KeyError as error:
        raise DAGError(f"task does not exist: {task_id}") from error
    if not isinstance(task, dict):
        raise DAGError(f"invalid task record: {task_id}")
    return task


def _validate_transition(
    manifest: Mapping[str, Any], task_id: str, state: str
) -> None:
    if state not in _ALLOWED_TRANSITIONS:
        raise InvalidTransition(f"unknown task state: {state}")
    task = _get_task(manifest, task_id)
    old_state = task["state"]
    if state not in _ALLOWED_TRANSITIONS[old_state]:
        raise InvalidTransition(f"{old_state} -> {state} is not allowed")
    if state == "ready" and old_state == "blocked":
        if not all(
            _get_task(manifest, dependency)["state"] in {"done", "skipped"}
            for dependency in task["deps"]
        ):
            raise InvalidTransition("blocked task dependencies are not complete")
    if state == "skipped" and old_state not in {"blocked", "ready"}:
        raise InvalidTransition("only blocked or ready tasks may be skipped")


def _set_task_state(
    manifest: dict[str, Any],
    task_id: str,
    state: str,
    at: str,
    *,
    reason: str | None = None,
) -> None:
    task = _get_task(manifest, task_id)
    old_state = task["state"]
    if old_state == state:
        return
    if state == "failed" and (
        not isinstance(reason, str) or not reason.strip()
    ):
        raise InvalidTransition("failed transition requires a non-empty reason")
    _validate_transition(manifest, task_id, state)
    task["state"] = state
    event: dict[str, Any] = {
        "at": at,
        "from": old_state,
        "to": state,
    }
    if reason:
        event["reason"] = reason
    task["history"].append(event)


def _refresh_blocked_tasks(manifest: dict[str, Any], at: str) -> None:
    changed = True
    while changed:
        changed = False
        for task_id, task in manifest["tasks"].items():
            if task["state"] != "blocked":
                continue
            if all(
                _get_task(manifest, dependency)["state"] in {"done", "skipped"}
                for dependency in task["deps"]
            ):
                _set_task_state(
                    manifest,
                    task_id,
                    "ready",
                    at,
                    reason="依存タスクが完了",
                )
                changed = True


def _next_ready_code_task(manifest: Mapping[str, Any]) -> str | None:
    for task_id, task in manifest["tasks"].items():
        if task["kind"] == "code" and task["state"] == "ready":
            return task_id
    return None


def _update_run_status(manifest: dict[str, Any]) -> None:
    status = manifest["status"]
    if status in {"halted", "duplicate"}:
        return
    tasks = list(manifest["tasks"].values())
    if any(task["state"] == "failed" for task in tasks):
        manifest["status"] = "stalled"
    elif all(task["state"] in {"done", "skipped"} for task in tasks):
        manifest["status"] = "completed"
    else:
        manifest["status"] = "active"


def _touch_manifest(manifest: dict[str, Any], at: str) -> None:
    manifest["updated_at"] = at


def _fail_task(
    manifest: dict[str, Any], task_id: str, at: str, error: str
) -> None:
    task = _get_task(manifest, task_id)
    if not isinstance(error, str) or not error.strip():
        raise InvalidTransition("failed transition requires a non-empty reason")
    _validate_transition(manifest, task_id, "failed")
    _set_task_state(manifest, task_id, "failed", at, reason=error)
    task["error"] = error


def _validate_result_tasks(
    manifest: Mapping[str, Any],
    parent_task_id: str,
    add_tasks: Sequence[TaskSpec],
    skip_tasks: Sequence[str],
    definitions: Mapping[str, Mapping[str, Any]],
) -> None:
    _get_task(manifest, parent_task_id)
    existing = set(manifest["tasks"])
    added: set[str] = set()
    for spec in add_tasks:
        if spec.task_id in existing or spec.task_id in added:
            raise DAGError(f"追加タスクのIDが重複しています: {spec.task_id}")
        if spec.type not in definitions:
            raise DAGError(f"タスク種別の定義がありません: {spec.type}")
        _validate_task_spec_id(spec)
        if parent_task_id not in spec.deps:
            raise DAGError(
                f"追加タスク {spec.task_id!r} は追加元 {parent_task_id!r} に依存する必要があります"
            )
        added.add(spec.task_id)
    for task_id in skip_tasks:
        if not isinstance(task_id, str):
            raise DAGError("skip task ID must be a string")
        if task_id not in existing:
            raise DAGError(f"task does not exist: {task_id}")
        if task_id == parent_task_id:
            raise InvalidTransition("a code task cannot skip itself")
        task = manifest["tasks"][task_id]
        if task["state"] not in {"blocked", "ready"}:
            raise InvalidTransition(f"{task['state']} -> skipped is not allowed")

    combined_specs = [
        TaskSpec(
            task_id=task_id,
            type=task["type"],
            deps=tuple(task["deps"]),
            index=tuple(task["index"]),
        )
        for task_id, task in manifest["tasks"].items()
    ]
    combined_specs.extend(add_tasks)
    _validate_graph(combined_specs, definitions)


def _create_dynamic_task_dirs(
    run_id: str,
    additions: Sequence[TaskSpec],
    task_dir: Callable[[str, str], Path],
) -> list[Path]:
    """Create addition directories before changing the manifest."""
    created: list[Path] = []
    try:
        for spec in additions:
            path = task_dir(run_id, spec.task_id)
            path.mkdir(parents=True, exist_ok=False)
            created.append(path)
    except BaseException:
        _remove_created_task_dirs(created)
        raise
    return created


def _remove_created_task_dirs(paths: Sequence[Path]) -> None:
    for path in reversed(paths):
        try:
            path.rmdir()
        except (FileNotFoundError, OSError):
            pass


def _commit_dynamic_tasks(
    manifest: dict[str, Any],
    additions: Sequence[TaskSpec],
    definitions: Mapping[str, Mapping[str, Any]],
) -> None:
    """Add already-validated tasks after their directories exist."""
    for spec in additions:
        manifest["tasks"][spec.task_id] = _new_task_record(
            spec, definitions[spec.type], manifest
        )


def _normalise_code_result(
    result: Any,
    context: CodeTaskContext,
) -> CodeTaskResult:
    if isinstance(result, CodeTaskResult):
        normalised = result
    else:
        normalised = CodeTaskResult(output=result)
    if not isinstance(normalised.add_tasks, list):
        normalised.add_tasks = list(normalised.add_tasks)
    if not isinstance(normalised.skip_tasks, list):
        normalised.skip_tasks = list(normalised.skip_tasks)
    normalised.add_tasks = [
        _coerce_task_spec(task) for task in [*context._add_tasks, *normalised.add_tasks]
    ]
    normalised.skip_tasks = [
        *context._skip_tasks,
        *normalised.skip_tasks,
    ]
    return normalised


def _call_handler(handler: CodeTaskHandler, context: CodeTaskContext) -> Any:
    """Call a handler while allowing simple callable objects and functions."""
    # Inspecting only whether the callable accepts one positional argument
    # avoids catching TypeError raised by the handler's own implementation.
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        return handler(context)
    try:
        signature.bind(context)
    except TypeError as error:
        raise CodeTaskError(
            "コードタスクの処理は context ひとつを受け取る必要があります"
        ) from error
    return handler(context)


def _source_outputs(
    manifest: Mapping[str, Any],
    task: Mapping[str, Any],
    definition: Mapping[str, Any],
    dependency_outputs: Mapping[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    dependencies = list(task["deps"])
    input_slots = definition.get("inputs", {})
    if not isinstance(input_slots, Mapping):
        return result
    for slot_definition in input_slots.values():
        if not isinstance(slot_definition, Mapping):
            continue
        slot = slot_definition.get("from")
        if not isinstance(slot, str) or slot == "input":
            continue
        matching = [
            dependency
            for dependency in dependencies
            if manifest["tasks"][dependency]["type"] == slot
            and manifest["tasks"][dependency]["state"] == "done"
            and dependency in dependency_outputs
        ]
        if len(matching) > 1 and task["index"]:
            indexed_task_id = f"{slot}-{task['index'][0]}"
            if indexed_task_id in matching:
                matching = [indexed_task_id]
        if len(matching) == 1:
            result[slot] = dependency_outputs[matching[0]]
        elif matching:
            result[slot] = [dependency_outputs[task_id] for task_id in matching]
    return result


def _read_dependency_outputs(
    run_dir: Path,
    manifest: Mapping[str, Any],
    task: Mapping[str, Any],
) -> dict[str, Any]:
    outputs: dict[str, Any] = {}
    for task_id in task["deps"]:
        dependency = manifest["tasks"][task_id]
        if dependency["state"] != "done":
            continue
        output_json = run_dir / "tasks" / task_id / "output.json"
        output_md = run_dir / "tasks" / task_id / "output.md"
        try:
            if output_json.is_file():
                with output_json.open("r", encoding="utf-8") as stream:
                    outputs[task_id] = json.load(stream)
            elif output_md.is_file():
                outputs[task_id] = output_md.read_text(encoding="utf-8")
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CodeTaskError(f"タスク出力を読み込めません: {task_id}") from error
    return outputs


def _read_run_input(run_dir: Path) -> Any:
    input_path = run_dir / "input.json"
    try:
        with input_path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CodeTaskError("run の入力を読み込めません") from error


def _resolve_run_seed(seed: int | None, seed_source: str | None) -> tuple[int, str]:
    if seed is None:
        if seed_source not in {None, "generated"}:
            raise ValueError("seed_source must be generated when seed is omitted")
        return generated_seed(), "generated"
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError("seed must be an integer")
    if not 0 <= seed <= MAX_SEED:
        raise ValueError("seed must be a 32-bit unsigned integer")
    resolved_source = seed_source or "argument"
    if resolved_source not in {"argument", "batch", "generated"}:
        raise ValueError("invalid seed_source")
    return seed, resolved_source


def _timestamp(clock: Callable[[], datetime]) -> str:
    value = clock()
    if not isinstance(value, datetime):
        raise TypeError("clock must return datetime")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc).replace(microsecond=0)
    return value.isoformat().replace("+00:00", "Z")


def _make_run_id(created_at: str, seed: int) -> str:
    date, time = created_at.rstrip("Z").split("T")
    compact_date = date.replace("-", "")
    compact_time = time.replace(":", "")
    seed_prefix = f"{seed:08x}"[:6]
    return f"{compact_date}-{compact_time}-{seed_prefix}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _error_text(error: Exception) -> str:
    if isinstance(error, KeyError):
        key = error.args[0] if error.args else "不明なキー"
        return f"必要な値が見つかりません: {key}"
    message = str(error)
    return message or error.__class__.__name__
