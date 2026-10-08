"""Durable run, claim, and submission processing for the Phase 0 orchestrator."""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import re
import secrets
import shutil
import socket
import sys
import unicodedata
from copy import deepcopy
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final

from .cards import (
    InputBudgetError,
    TaskCardError,
    generate_task_card,
    input_char_count,
    prepare_task_inputs,
)
from .cache import cache_key, lookup_cache, save_cache
from .task_outputs import read_task_output
from .failure_reports import write_failure_reports
from .task_ranges import preflight_range, fixed_range_value
from .story_quality import join_continuation
from .glossary import build_glossary, registration_outputs, related_glossary, registered_names
from .manifest import load_manifest, write_manifest
from .seed import MAX_SEED, derive_task_seed, generated_seed, task_random
from .selectors import SelectorError, resolve_inputs
from .storage import (
    LockTimeoutError,
    atomic_write_json,
    atomic_write_text,
    manifest_lock,
)
from .validation import (
    ValidationResult,
    output_char_count,
    text_is_truncated,
    validate_document,
    validate_task_definition_output_example,
    validate_output,
)


TASK_DEFINITION_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "schemas"
    / "task-definition.schema.json"
)

_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_TASK_TYPE = re.compile(r"^[A-Z][0-9]+\.[a-z][a-z0-9_]*$")
_RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
_TICKET = re.compile(r"^[a-f0-9]{32}$")
_EXECUTOR_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_ISOLATIONS = frozenset({"permission", "placement", "adapter", "none"})
_HARNESS_KINDS = frozenset({"story", "dummy"})
_TERMINAL_RUN_STATUSES = frozenset({"halted", "completed", "duplicate"})
_MISSING: Final = object()

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "blocked": frozenset({"ready", "skipped"}),
    "ready": frozenset({"claimed", "done", "failed", "skipped"}),
    "claimed": frozenset({"done", "ready", "failed", "skipped"}),
    "done": frozenset(),
    "failed": frozenset({"ready", "blocked"}),
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


class ClaimError(OrchestrationError):
    """A task cannot be claimed or its claim cannot be used."""

    exit_code = 3


class InvalidClaimError(ClaimError):
    """A submitted ticket no longer identifies a valid claim."""


class HaltedRunError(OrchestrationError):
    """The run was halted because the harness changed."""

    exit_code = 6


@dataclass(frozen=True)
class SubmissionResult:
    """The durable result of accepting or rejecting one submission."""

    accepted: bool
    run_id: str
    task_id: str
    value: Any = None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.accepted


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
    invalidations: list[tuple[str, str]] = field(default_factory=list)
    manifest_updates: dict[str, Any] = field(default_factory=dict)


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
    _invalidations: list[tuple[str, str]] = field(default_factory=list, repr=False)

    def add_task(self, task: TaskSpec) -> TaskSpec:
        """Stage a dynamic task for addition after this handler succeeds."""
        self._add_tasks.append(task)
        return task

    def skip_task(self, task_id: str) -> None:
        """Stage an existing task to become ``skipped`` after success."""
        self._skip_tasks.append(task_id)

    def invalidate_task(self, task_id: str, reason: str) -> None:
        """Stage a completed task for invalidation after this task succeeds."""
        if not isinstance(task_id, str) or not task_id:
            raise DAGError("invalid invalidation task ID")
        if not isinstance(reason, str) or not reason.strip():
            raise DAGError("invalidation requires a non-empty reason")
        self._invalidations.append((task_id, reason))


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
        harness_root: str | Path | None = None,
        repository_root: str | Path | None = None,
        harness_kind: str | None = None,
    ) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve(strict=False)
        self.definition_root = (
            Path(harness_root).expanduser().resolve(strict=False)
            if harness_root is not None
            else Path(__file__).resolve().parents[2]
        )
        self.repository_root = (
            Path(repository_root).expanduser().resolve(strict=False)
            if repository_root is not None
            else _repository_root_for_definition_root(self.definition_root)
        )
        resolved_harness_kind = harness_kind or (
            "dummy" if _is_dummy_harness_root(self.definition_root) else "story"
        )
        if resolved_harness_kind not in _HARNESS_KINDS:
            raise ValueError(f"invalid harness_kind: {resolved_harness_kind!r}")
        self.harness_kind = resolved_harness_kind
        self.task_definitions = _validate_task_definitions(
            task_definitions,
            schema_root=self.definition_root,
        )
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
        plot_type: str | None = None,
        harness: Mapping[str, str] | None = None,
        scale: Mapping[str, Any] | None = None,
        table_snapshot: Mapping[str, Any] | None = None,
        run_id: str | None = None,
        harness_kind: str | None = None,
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
        resolved_harness_kind = harness_kind or self.harness_kind
        if resolved_harness_kind not in _HARNESS_KINDS:
            raise ValueError(f"invalid harness_kind: {resolved_harness_kind!r}")
        if resolved_harness_kind != self.harness_kind:
            raise ValueError(
                "harness_kind must match the orchestrator's harness_kind"
            )

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
            input_record: dict[str, Any] = {}
            if input_data is not None:
                source_sha256 = _sha256_file(input_path)
                if (
                    input_type == "free"
                    and isinstance(input_data, Mapping)
                    and isinstance(input_data.get("source_sha256"), str)
                ):
                    source_sha256 = input_data["source_sha256"]
                if input_type == "free":
                    input_record = {
                        "kind": "free",
                        "source_sha256": source_sha256,
                        "plot_type": plot_type,
                    }
                else:
                    input_record = {
                        "type": input_type,
                        "sha256": source_sha256,
                    }

            resolved_harness = (
                dict(harness)
                if harness is not None
                else _discover_harness_files(
                    self.definition_root,
                    repository_root=self.repository_root,
                )
            )
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
                "harness": resolved_harness,
                "harness_kind": resolved_harness_kind,
                "input_ratio": None,
                "table_snapshot": dict(table_snapshot or {}),
                "tasks": {},
                "warnings": [],
            }
            self._preflight_ranges(manifest, resolved_run_id, specs)
            for spec in specs:
                record = _new_task_record(
                    spec, self.task_definitions[spec.type], manifest
                )
                manifest["tasks"][spec.task_id] = record
                self.task_dir(resolved_run_id, spec.task_id).mkdir(
                    parents=True, exist_ok=False
                )
            _update_run_status(manifest)
            self._persist_manifest(run_dir, manifest)
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
                if self._halt_if_harness_changed(manifest):
                    _touch_manifest(manifest, self._now())
                    self._persist_manifest(run_dir, manifest)
                    return manifest
                if manifest["status"] in _TERMINAL_RUN_STATUSES:
                    _touch_manifest(manifest, self._now())
                    self._persist_manifest(run_dir, manifest)
                    return manifest
                _refresh_blocked_tasks(manifest, self._now())
                self._complete_ready_cached_tasks(manifest, run_id)
                _refresh_blocked_tasks(manifest, self._now())
                task_id = _next_ready_code_task(manifest)
                if task_id is None:
                    _update_run_status(manifest)
                    _touch_manifest(manifest, self._now())
                    self._persist_manifest(run_dir, manifest)
                    return manifest
                # ハンドラから Orchestrator を呼び戻さないこと。ここでは
                # 1コードタスクの読み込み・実行・書き込みだけをロックする。
                self._execute_code_task(manifest, run_id, task_id)
                _touch_manifest(manifest, self._now())
                _update_run_status(manifest)
                self._persist_manifest(run_dir, manifest)

    def claim_next(
        self,
        run_id: str | None = None,
        *,
        executor_id: str | None = None,
        isolation: str = "none",
    ) -> dict[str, str] | None:
        """Claim the next ready LLM task and return its executor-facing card.

        The returned mapping intentionally contains no task ID.  A ticket is
        the only identifier an executor needs in order to submit its output.
        The caller may restrict selection to one run; otherwise active runs
        are considered in creation order, followed by DAG depth and task ID.
        """

        resolved_executor_id = _resolve_executor_id(executor_id)
        _validate_isolation(isolation)

        run_ids = self._claimable_run_ids(run_id)
        # Code tasks are synchronous.  Advancing before looking for an LLM
        # task makes a newly-unblocked task visible to this invocation.
        for candidate_run_id in run_ids:
            try:
                manifest = self.load_run(candidate_run_id)
                if manifest["status"] == "active":
                    self.advance(candidate_run_id)
            except LockTimeoutError:
                raise
            except (OSError, ValueError, ClaimError) as error:
                _warn_skipped_run(candidate_run_id, error)

        for candidate_run_id in self._claimable_run_ids(run_id):
            retry_same_run = False
            while True:
                run_dir = self.run_dir(candidate_run_id)
                try:
                    with manifest_lock(run_dir):
                        manifest = load_manifest(run_dir / "manifest.json")
                        if self._halt_if_harness_changed(manifest):
                            _touch_manifest(manifest, self._now())
                            self._persist_manifest(run_dir, manifest)
                            break
                        if manifest["status"] != "active" and not retry_same_run:
                            break
                        now = self._now()
                        changed = _refresh_claims(
                            manifest,
                            run_dir,
                            now,
                            self._clock,
                        )
                        _refresh_blocked_tasks(manifest, now)
                        changed = (
                            self._complete_ready_cached_tasks(manifest, candidate_run_id)
                            or changed
                        )
                        _refresh_blocked_tasks(manifest, now)
                        task_id = _next_ready_llm_task(manifest)
                        if task_id is None:
                            if changed:
                                _update_run_status(manifest)
                            _touch_manifest(manifest, now)
                            self._persist_manifest(run_dir, manifest)
                            break

                        result = self._claim_task_locked(
                            manifest,
                            candidate_run_id,
                            task_id,
                            executor_id=resolved_executor_id,
                            isolation=isolation,
                            now=now,
                        )
                        _update_run_status(manifest)
                        _touch_manifest(manifest, now)
                        self._persist_manifest(run_dir, manifest)
                        if result is not None:
                            return result
                        # A card error or O_EXCL race consumed this candidate.
                        # Re-enter the same run before moving to the next one.
                        retry_same_run = True
                except LockTimeoutError:
                    raise
                except (OSError, ValueError, ClaimError) as error:
                    _warn_skipped_run(candidate_run_id, error)
                    break
        return None

    # These aliases keep the operation discoverable to callers that use the
    # task-oriented spelling.  They share one implementation and therefore
    # have identical locking and ordering semantics.
    next_task = claim_next
    _claim_next = claim_next

    def claim_task(
        self,
        run_id: str,
        task_id: str,
        *,
        executor_id: str | None = None,
        isolation: str = "none",
    ) -> dict[str, str]:
        """Claim one specific ready LLM task.

        ``claim_next`` is the normal selection entry point.  This narrower
        seam is useful to the submit/retry layers and to tests that need to
        inspect one task without depending on the rest of a DAG.
        """

        resolved_executor_id = _resolve_executor_id(executor_id)
        _validate_isolation(isolation)
        manifest = self.load_run(run_id)
        if manifest["status"] == "active":
            self.advance(run_id)

        run_dir = self.run_dir(run_id)
        with manifest_lock(run_dir):
            manifest = load_manifest(run_dir / "manifest.json")
            if self._halt_if_harness_changed(manifest):
                _touch_manifest(manifest, self._now())
                self._persist_manifest(run_dir, manifest)
                raise HaltedRunError(f"run は停止中です: {run_id}")
            if manifest["status"] == "halted":
                raise HaltedRunError(f"run は停止中です: {run_id}")
            if manifest["status"] != "active":
                raise ClaimError(f"run is not active: {run_id}")
            now = self._now()
            _refresh_claims(manifest, run_dir, now, self._clock)
            _refresh_blocked_tasks(manifest, now)
            cache_changed = self._complete_ready_cached_tasks(manifest, run_id)
            _refresh_blocked_tasks(manifest, now)
            task = _get_task(manifest, task_id)
            if task["kind"] != "llm" or task["state"] != "ready":
                if cache_changed:
                    _update_run_status(manifest)
                    _touch_manifest(manifest, now)
                    self._persist_manifest(run_dir, manifest)
                raise ClaimError(f"task is not ready for claim: {task_id}")
            result = self._claim_task_locked(
                manifest,
                run_id,
                task_id,
                executor_id=resolved_executor_id,
                isolation=isolation,
                now=now,
            )
            if result is None:
                raise ClaimError(f"task is already claimed: {task_id}")
            _update_run_status(manifest)
            _touch_manifest(manifest, now)
            self._persist_manifest(run_dir, manifest)
            return result

    _claim_task = claim_task

    def resume_harness_change(self, run_id: str) -> dict[str, Any]:
        """Accept the current harness snapshot and resume a halted run."""

        run_dir = self.run_dir(run_id)
        with manifest_lock(run_dir):
            manifest = load_manifest(run_dir / "manifest.json")
            if manifest["status"] != "halted":
                raise OrchestrationError(f"run は halted ではありません: {run_id}")
            manifest["harness"] = self._current_harness(manifest)
            manifest["status"] = "active"
            _refresh_blocked_tasks(manifest, self._now())
            _update_run_status(manifest)
            _touch_manifest(manifest, self._now())
            self._persist_manifest(run_dir, manifest)
            return manifest

    # Short aliases used by callers implementing the future ``st resume`` CLI.
    resume = resume_harness_change
    accept_harness_change = resume_harness_change

    def _current_harness(self, manifest: Mapping[str, Any]) -> dict[str, str]:
        """Return a freshly enumerated snapshot of the current harness files."""

        return _discover_harness_files(
            self.definition_root,
            repository_root=self.repository_root,
        )

    def _harness_changed(self, manifest: Mapping[str, Any]) -> bool:
        return self._current_harness(manifest) != dict(manifest.get("harness", {}))

    def _halt_if_harness_changed(self, manifest: dict[str, Any]) -> bool:
        if manifest["status"] in _TERMINAL_RUN_STATUSES:
            return False
        if not self._harness_changed(manifest):
            return False
        _halt_run(manifest)
        return True

    def validate_claim(self, ticket: str) -> dict[str, Any]:
        """Return claim metadata when *ticket* is currently valid.

        Submission code in later work items calls this before validating an
        output.  It deliberately performs no state transition, so an invalid
        ticket is rejected with the claim-specific exit code without changing
        the task.
        """

        if not isinstance(ticket, str) or not _TICKET.fullmatch(ticket):
            raise InvalidClaimError("存在しない ticket です")
        found = self._find_claim_ticket(ticket)
        if found is None:
            raise InvalidClaimError("存在しない ticket です")
        found_run_id, found_task_id = found
        run_dir = self.run_dir(found_run_id)
        with manifest_lock(run_dir):
            manifest = load_manifest(run_dir / "manifest.json")
            if self._halt_if_harness_changed(manifest):
                _touch_manifest(manifest, self._now())
                self._persist_manifest(run_dir, manifest)
                raise HaltedRunError(f"run は停止中です: {found_run_id}")
            if manifest["status"] == "halted":
                raise HaltedRunError(f"run は停止中です: {found_run_id}")
            task = _get_task(manifest, found_task_id)
            claim = task.get("claim")
            claim_path = self.task_dir(found_run_id, found_task_id) / "claim.json"
            if (
                task["state"] != "claimed"
                or not isinstance(claim, Mapping)
                or claim.get("ticket") != ticket
            ):
                raise InvalidClaimError("ticket の claim は無効です")
            payload = _read_claim_file(claim_path)
            if payload.get("ticket") != ticket or _claim_expired(
                payload, self._now()
            ):
                raise InvalidClaimError("ticket の lease が切れています")
            if _manifest_claim(payload) != dict(claim):
                raise InvalidClaimError("manifest と claim.json が一致しません")
            return {
                "run_id": found_run_id,
                "task_id": found_task_id,
                "claim": dict(claim),
            }

    # ``require_claim`` reads naturally at submit call sites.
    require_claim = validate_claim

    def submit(
        self,
        ticket: str,
        raw_output: Any,
        *,
        truncated: bool = False,
    ) -> SubmissionResult:
        """Validate and durably submit the output for a live claim.

        Claim verification happens before parsing.  The final manifest update
        repeats the ticket and lease check while holding the manifest lock, so
        a revoke or an expiring lease cannot race a successful submission.
        """

        claim_info = self.validate_claim(ticket)
        run_id = claim_info["run_id"]
        task_id = claim_info["task_id"]
        run_dir = self.run_dir(run_id)

        with manifest_lock(run_dir):
            manifest = load_manifest(run_dir / "manifest.json")
            if self._halt_if_harness_changed(manifest):
                _touch_manifest(manifest, self._now())
                self._persist_manifest(run_dir, manifest)
                raise HaltedRunError(f"run は停止中です: {run_id}")
            if manifest["status"] == "halted":
                raise HaltedRunError(f"run は停止中です: {run_id}")
            task = _get_task(manifest, task_id)
            definition = self.task_definitions[task["type"]]
            inputs = _load_card_inputs(self.task_dir(run_id, task_id))
            from .world_facts import specialize_fact_definition
            definition = specialize_fact_definition(definition, inputs)

        continuation = _continuation_enabled(definition)
        extend_to_min = (
            definition.get("output") == "text" and definition.get("extend_to_min", False)
        )
        partial = _read_partial_output(self.task_dir(run_id, task_id))
        continuation_warnings: tuple[str, ...] = ()
        if continuation and _is_truncated_submission(raw_output, truncated):
            if int(task.get("continuation_step", 0)) >= 2:
                validation = ValidationResult(
                    None,
                    errors=("継続の上限に達した出力がなお途中で切れています",),
                )
                submission_output = (
                    partial + raw_output
                    if isinstance(raw_output, str)
                    else raw_output
                )
                return self._commit_submission(
                    ticket,
                    run_id,
                    task_id,
                    submission_output,
                    validation,
                    inputs,
                    discard_partial=True,
                )
            return self._commit_truncated_submission(
                ticket,
                run_id,
                task_id,
                raw_output,
            )

        if truncated and definition.get("output") == "json":
            raise OrchestrationError(
                "JSON出力に --truncated を指定できません"
            )

        if truncated:
            validation = ValidationResult(
                None,
                errors=("--truncated は continuation: true のタスクでのみ指定できます",),
            )
            submission_output = raw_output
        else:
            submission_output = raw_output
            if (continuation or extend_to_min) and partial:
                if not isinstance(raw_output, str):
                    submission_output = raw_output
                else:
                    submission_output, continuation_warnings = join_continuation(partial, raw_output)
            validation_definition = definition
            minimum = _minimum_text_chars(definition) if extend_to_min else 0
            short = (
                isinstance(submission_output, str)
                and output_char_count(submission_output) < minimum
            )
            if short and int(task["continuation_step"]) < 2:
                return self._commit_truncated_submission(
                    ticket, run_id, task_id, raw_output,
                    reason="字数が目標に満たないため継続",
                )
            if short:
                validation_definition = deepcopy(definition)
                for check in validation_definition.get("validate", {}).get("checks", []):
                    if (
                        isinstance(check, dict)
                        and "min_chars" in check
                        and "field" not in check["min_chars"]
                    ):
                        check["min_chars"]["n"] = (check["min_chars"]["n"] + 1) // 2
            if definition.get("range"):
                context = self._build_context(manifest, run_id, task_id)
                validation_definition = dict(validation_definition, range=preflight_range(
                    definition, {**manifest, **context.inputs}, run_input=context.run_input,
                    outputs=_source_outputs(manifest, task, definition, context.dependency_outputs),
                    index=task["index"],
                ))
            validation = validate_output(
                validation_definition,
                submission_output,
                inputs=inputs,
                harness_root=self.definition_root,
                common_words_path=self.repository_root / "tables" / "common_words.yaml",
                index=task["index"],
                run_values={**manifest, **inputs},
                registered_names=registered_names(build_glossary(
                    registration_outputs(run_dir, manifest)
                )) if "glossary" in definition.get("inputs", {}) else (),
            )
            if continuation_warnings:
                validation = ValidationResult(
                    validation.value, validation.errors,
                    (*continuation_warnings, *validation.warnings),
                )
            if short and validation.passed:
                validation = ValidationResult(
                    validation.value,
                    validation.errors,
                    (
                        *validation.warnings,
                        f"字数が目標に届かず採用 {output_char_count(validation.value)}/{minimum}",
                    ),
                )
        return self._commit_submission(
            ticket,
            run_id,
            task_id,
            submission_output,
            validation,
            inputs,
            discard_partial=bool(extend_to_min),
        )

    # Names used by internal callers and by the future CLI layer.
    submit_output = submit
    process_submission = submit

    def record_executor_failure(
        self, ticket: str, reason: str, *, fatal: bool = False,
    ) -> SubmissionResult:
        """Record an executor or adapter failure as a failed submission."""

        if not isinstance(reason, str) or not reason.strip():
            raise OrchestrationError("実行者の失敗理由が空です")
        claim_info = self.validate_claim(ticket)
        run_id = claim_info["run_id"]
        task_id = claim_info["task_id"]
        run_dir = self.run_dir(run_id)
        with manifest_lock(run_dir):
            manifest = load_manifest(run_dir / "manifest.json")
            if self._halt_if_harness_changed(manifest):
                _touch_manifest(manifest, self._now())
                self._persist_manifest(run_dir, manifest)
                raise HaltedRunError(f"run は停止中です: {run_id}")
            if manifest["status"] == "halted":
                raise HaltedRunError(f"run は停止中です: {run_id}")
            inputs = _load_card_inputs(self.task_dir(run_id, task_id))
        return self._commit_submission(
            ticket,
            run_id,
            task_id,
            None,
            ValidationResult(None, errors=(reason.strip(),)),
            inputs,
            fatal=fatal,
        )

    submit_failure = record_executor_failure

    def _commit_submission(
        self,
        ticket: str,
        run_id: str,
        task_id: str,
        raw_output: Any,
        validation: Any,
        card_inputs: Mapping[str, Any],
        *,
        discard_partial: bool = False,
        fatal: bool = False,
    ) -> SubmissionResult:
        run_dir = self.run_dir(run_id)
        with manifest_lock(run_dir):
            manifest = load_manifest(run_dir / "manifest.json")
            if self._halt_if_harness_changed(manifest):
                _touch_manifest(manifest, self._now())
                self._persist_manifest(run_dir, manifest)
                raise HaltedRunError(f"run は停止中です: {run_id}")
            if manifest["status"] == "halted":
                raise HaltedRunError(f"run は停止中です: {run_id}")
            task = _get_task(manifest, task_id)
            claim = _require_claim_locked(
                manifest,
                run_dir,
                task_id,
                ticket,
                self._now(),
            )
            at = self._now()
            from .world_facts import specialize_fact_definition
            definition = specialize_fact_definition(self.task_definitions[task["type"]], card_inputs)
            discard_partial = discard_partial or (
                definition.get("output") == "text" and definition.get("extend_to_min", False)
            )
            errors = tuple(str(error) for error in validation.errors)
            warnings = tuple(str(warning) for warning in validation.warnings)
            recorded_warnings = tuple(
                f"{task_id}: {warning}" for warning in warnings
            )
            if validation.passed:
                stored_value = _postprocess_accepted_output(task, validation.value)
                key = self._cache_key_for_task(
                    manifest,
                    task_id,
                    card_inputs,
                )
                save_cache(self.data_dir, key, stored_value)
                _write_submitted_output(
                    self.task_dir(run_id, task_id),
                    definition,
                    stored_value,
                )
                task["cache_key"] = key
                _release_claim(run_dir, task_id, task)
                _set_task_state(
                    manifest,
                    task_id,
                    "done",
                    at,
                    reason="提出が検証に合格",
                    executor_id=claim["executor_id"],
                )
                task["error"] = None
                manifest["warnings"].extend(recorded_warnings)
                _refresh_blocked_tasks(manifest, at)
                _update_run_status(manifest)
                _touch_manifest(manifest, at)
                self._persist_manifest(run_dir, manifest)
                _remove_partial_output(self.task_dir(run_id, task_id))
                return SubmissionResult(
                    True,
                    run_id,
                    task_id,
                    value=stored_value,
                    warnings=recorded_warnings,
                )

            reason = "；".join(errors) or "提出が検証に不合格"
            next_attempt = int(task["attempt"]) + 1
            next_tries = int(task["tries"]) + 1
            attempt_number = _next_attempt_number(
                self.task_dir(run_id, task_id) / "attempts"
            )
            attempt_record = {
                "output": raw_output,
                "errors": list(errors) or [reason],
                "reason": reason,
                "warnings": list(warnings),
                "ticket": ticket,
                "executor_id": claim["executor_id"],
                "at": at,
                "attempt": next_attempt,
                "tries": next_tries,
            }
            task["attempt"] = next_attempt
            task["tries"] = next_tries
            if discard_partial:
                task["continuation_step"] = 0
            task["error"] = reason
            task["cache_key"] = None
            task["claim"] = None
            task_definition = self.task_definitions[task["type"]]
            max_attempts = int(task_definition.get("max_attempts", 5))
            exhausted = task["tries"] >= max_attempts
            on_exhausted = task_definition.get("on_exhausted", "fail")
            if fatal:
                next_state = "failed"
            elif exhausted and on_exhausted == "skip":
                next_state = "skipped"
            elif exhausted:
                next_state = "failed"
            else:
                next_state = "ready"
            _set_task_state(
                manifest,
                task_id,
                next_state,
                at,
                reason=reason,
                executor_id=claim["executor_id"],
            )
            if next_state == "skipped":
                manifest["warnings"].append(
                    f"{task_id}: 試行の上限に達したため省略: {reason}"
                )
                _refresh_blocked_tasks(manifest, at)
            atomic_write_json(
                self.task_dir(run_id, task_id)
                / "attempts"
                / f"{attempt_number}.json",
                attempt_record,
            )
            _update_run_status(manifest)
            _touch_manifest(manifest, at)
            self._persist_manifest(run_dir, manifest)
            _release_claim(run_dir, task_id, task)
            _remove_task_outputs(run_dir, task_id)
            if discard_partial:
                _remove_partial_output(self.task_dir(run_id, task_id))
            return SubmissionResult(
                False,
                run_id,
                task_id,
                value=validation.value,
                errors=errors or (reason,),
                warnings=warnings,
            )

    def _commit_truncated_submission(
        self,
        ticket: str,
        run_id: str,
        task_id: str,
        raw_output: Any,
        *,
        reason: str = "出力が途中で切れたため継続",
    ) -> SubmissionResult:
        """Persist one continuation chunk without validating or caching it."""
        if not isinstance(raw_output, str):
            raise OrchestrationError("継続する出力は文字列でなければなりません")

        run_dir = self.run_dir(run_id)
        with manifest_lock(run_dir):
            manifest = load_manifest(run_dir / "manifest.json")
            if self._halt_if_harness_changed(manifest):
                _touch_manifest(manifest, self._now())
                self._persist_manifest(run_dir, manifest)
                raise HaltedRunError(f"run は停止中です: {run_id}")
            if manifest["status"] == "halted":
                raise HaltedRunError(f"run は停止中です: {run_id}")
            task = _get_task(manifest, task_id)
            claim = _require_claim_locked(
                manifest,
                run_dir,
                task_id,
                ticket,
                self._now(),
            )
            continuation_step = int(task["continuation_step"])
            if continuation_step >= 2:
                raise OrchestrationError("継続の上限に達しています")
            partial_path = self.task_dir(run_id, task_id) / "partial.md"
            previous = _read_partial_output(self.task_dir(run_id, task_id))
            combined, warnings = join_continuation(previous, raw_output)
            recorded_warnings = tuple(f"{task_id}: {warning}" for warning in warnings)
            atomic_write_text(
                partial_path,
                combined,
                encoding="utf-8",
            )
            at = self._now()
            task["continuation_step"] = continuation_step + 1
            task["cache_key"] = None
            task["error"] = None
            manifest["warnings"].extend(recorded_warnings)
            _release_claim(run_dir, task_id, task)
            _set_task_state(
                manifest,
                task_id,
                "ready",
                at,
                reason=reason,
                executor_id=claim["executor_id"],
            )
            _refresh_blocked_tasks(manifest, at)
            _update_run_status(manifest)
            _touch_manifest(manifest, at)
            self._persist_manifest(run_dir, manifest)
            return SubmissionResult(
                True,
                run_id,
                task_id,
                value=raw_output,
                warnings=recorded_warnings,
            )

    def retry_failed(self, run_id: str, task_id: str) -> dict[str, Any]:
        """Reset a failed task's retry counters and put it back in ``ready``."""

        run_dir = self.run_dir(run_id)
        with manifest_lock(run_dir):
            manifest = load_manifest(run_dir / "manifest.json")
            task = _get_task(manifest, task_id)
            if task["state"] != "failed":
                raise InvalidTransition("st retry の対象は failed のタスクだけです")
            previous_error = task.get("error")
            if isinstance(previous_error, str) and previous_error:
                task["history"].append(
                    {
                        "at": self._now(),
                        "from": "failed",
                        "to": "failed",
                        "reason": f"retry: {previous_error}",
                    }
                )
            task["tries"] = 0
            task["invalidations"] = 0
            task["continuation_step"] = 0
            task["cache_key"] = None
            task["error"] = None
            dependencies_ready = all(
                dependency in manifest["tasks"]
                and manifest["tasks"][dependency]["state"] in {"done", "skipped"}
                for dependency in task["deps"]
            )
            partial = self.task_dir(run_id, task_id) / "partial.md"
            try:
                partial.unlink()
            except FileNotFoundError:
                pass
            _set_task_state(
                manifest,
                task_id,
                "ready" if dependencies_ready else "blocked",
                self._now(),
                reason="failed タスクを再試行",
            )
            _update_run_status(manifest)
            _touch_manifest(manifest, self._now())
            self._persist_manifest(run_dir, manifest)
            return manifest

    retry_task = retry_failed
    retry = retry_failed

    def invalidate_task(
        self,
        run_id: str,
        task_id: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """Invalidate a completed task and reset its downstream tasks."""

        run_dir = self.run_dir(run_id)
        with manifest_lock(run_dir):
            manifest = load_manifest(run_dir / "manifest.json")
            _validate_invalidation_request(manifest, task_id, reason)
            _apply_invalidation(
                manifest,
                run_dir,
                task_id,
                reason,
                self.task_definitions,
                self._now(),
            )
            _update_run_status(manifest)
            _touch_manifest(manifest, self._now())
            self._persist_manifest(run_dir, manifest)
            return manifest

    invalidate = invalidate_task

    def revoke_claim(
        self,
        run_id: str,
        task_id: str,
        *,
        reason: str = "claimを取り消し",
        expected_ticket: str | None = None,
    ) -> dict[str, Any]:
        """Cancel a live claim and return the task to ``ready``.

        The claim file is retained as ``claim.revoked.<n>.json`` so a ticket
        from the cancelled executor can never become valid again.
        """

        if not isinstance(reason, str) or not reason.strip():
            raise ClaimError("claim cancellation requires a reason")
        run_dir = self.run_dir(run_id)
        with manifest_lock(run_dir):
            manifest = load_manifest(run_dir / "manifest.json")
            task = _get_task(manifest, task_id)
            current_claim = task.get("claim")
            if expected_ticket is not None and (
                task["state"] != "claimed"
                or not isinstance(current_claim, Mapping)
                or current_claim.get("ticket") != expected_ticket
            ):
                return manifest
            if task["state"] != "claimed":
                raise ClaimError(f"task is not claimed: {task_id}")
            claim_path = self.task_dir(run_id, task_id) / "claim.json"
            manifest_claim = task.get("claim")
            if claim_path.exists():
                if not isinstance(manifest_claim, Mapping):
                    raise ClaimError("manifest の claim が不正です")
                payload = _read_claim_file(claim_path)
                if (
                    payload["task_id"] != task_id
                    or payload["ticket"] != manifest_claim.get("ticket")
                ):
                    raise ClaimError("claim.json の ticket が manifest と一致しません")
            _release_claim(
                run_dir,
                task_id,
                task,
                archive_prefix="claim.revoked",
            )
            executor_id = (
                manifest_claim.get("executor_id")
                if isinstance(manifest_claim, Mapping)
                else None
            )
            _set_task_state(
                manifest,
                task_id,
                "ready",
                self._now(),
                reason=reason,
                executor_id=executor_id,
            )
            _update_run_status(manifest)
            _touch_manifest(manifest, self._now())
            self._persist_manifest(run_dir, manifest)
            return manifest

    _revoke_claim = revoke_claim

    def _claimable_run_ids(self, run_id: str | None) -> list[str]:
        if run_id is not None:
            # Let run_dir provide the same path-safety error as other APIs,
            # then load the manifest to give callers a useful missing-run
            # error before any claim work starts.
            run_path = self.run_dir(run_id)
            try:
                self.load_run(run_id)
            except (OSError, ValueError) as error:
                if run_path.exists():
                    _warn_skipped_run(run_id, error)
                    return []
                raise
            manifest = self.load_run(run_id)
            if manifest["status"] == "halted":
                raise HaltedRunError(f"run は停止中です: {run_id}")
            return [run_id]

        runs_dir = self.data_dir / "runs"
        if not runs_dir.is_dir():
            return []
        records: list[tuple[str, str]] = []
        for entry in runs_dir.iterdir():
            if not entry.is_dir() or not _RUN_ID.fullmatch(entry.name):
                continue
            try:
                manifest = load_manifest(entry / "manifest.json")
            except (OSError, ValueError) as error:
                _warn_skipped_run(entry.name, error)
                continue
            if manifest["status"] in {"active", "stalled"}:
                records.append((manifest["created_at"], entry.name))
        records.sort(key=lambda item: (item[0], item[1]))
        return [entry[1] for entry in records]

    def _claim_task_locked(
        self,
        manifest: dict[str, Any],
        run_id: str,
        task_id: str,
        *,
        executor_id: str,
        isolation: str,
        now: str,
    ) -> dict[str, str] | None:
        task = _get_task(manifest, task_id)
        if task["kind"] != "llm" or task["state"] != "ready":
            return None

        definition = self.task_definitions[task["type"]]
        ticket = secrets.token_hex(16)
        claimed_at = now
        lease_expires_at = _lease_expires_at(
            claimed_at,
            definition.get("lease_minutes", 30),
        )
        payload = {
            "ticket": ticket,
            "task_id": task_id,
            "executor_id": executor_id,
            "isolation": isolation,
            "claimed_at": claimed_at,
            "lease_expires_at": lease_expires_at,
        }
        claim_path = self.task_dir(run_id, task_id) / "claim.json"

        try:
            card = self._build_task_card(manifest, run_id, task_id, ticket)
            context = self._build_context(manifest, run_id, task_id)
            card_inputs = _prepare_card_inputs(
                self.task_definitions[task["type"]], context.inputs
            )
        except Exception as error:
            # Input rendering failure is a task failure according to the
            # task-card contract.
            _fail_task(
                manifest,
                task_id,
                now,
                _error_text(error),
                run_dir=self.run_dir(run_id),
            )
            return None

        try:
            _create_claim_file(claim_path, payload)
        except FileExistsError:
            # Another claim creator won the O_EXCL race.  The next invocation
            # will reconcile the file with the manifest before selecting.
            return None

        manifest_claim = _manifest_claim(payload)
        task["claim"] = manifest_claim
        _set_task_state(
            manifest,
            task_id,
            "claimed",
            now,
            reason="実行者がclaim",
            executor_id=executor_id,
        )
        try:
            atomic_write_text(self.task_dir(run_id, task_id) / "card.md", card)
            atomic_write_json(
                self.task_dir(run_id, task_id) / "input.json", card_inputs
            )
        except BaseException:
            _release_claim(self.run_dir(run_id), task_id, task)
            _set_task_state(
                manifest,
                task_id,
                "ready",
                now,
                reason="タスクカードの保存に失敗",
                executor_id=executor_id,
            )
            raise
        return {
            "ticket": ticket,
            "card": card,
            "lease_expires_at": lease_expires_at,
        }

    def _build_task_card(
        self,
        manifest: Mapping[str, Any],
        run_id: str,
        task_id: str,
        ticket: str,
    ) -> str:
        card, _ = self._build_task_card_and_inputs(manifest, run_id, task_id, ticket)
        return card

    def _build_task_card_and_inputs(
        self,
        manifest: Mapping[str, Any],
        run_id: str,
        task_id: str,
        ticket: str,
    ) -> tuple[str, dict[str, Any]]:
        task = _get_task(manifest, task_id)
        definition = self.task_definitions[task["type"]]
        context = self._build_context(manifest, run_id, task_id)
        definition = context.definition
        card_inputs = _prepare_card_inputs(definition, context.inputs)
        retry_reason = task.get("error")
        if not isinstance(retry_reason, str):
            retry_reason = None
        continuation_tail = None
        extend_to_min = False
        if int(task["continuation_step"]) > 0 and (
            _continuation_enabled(definition) or definition.get("extend_to_min", False)
        ):
            partial = _read_partial_output(self.task_dir(run_id, task_id))
            remaining = int(definition.get("max_input_chars", 3000)) - input_char_count(
                definition,
                card_inputs,
            )
            if remaining < 1000:
                raise TaskCardError("継続の予算が足りない")
            normalized_partial = unicodedata.normalize("NFC", partial)
            continuation_tail = normalized_partial[-min(remaining, 6000) :]
            last_ready = next(
                (event for event in reversed(task["history"]) if event.get("to") == "ready"), {}
            )
            extend_to_min = last_ready.get("reason") == "字数が目標に満たないため継続"
        return (
            generate_task_card(
                definition,
                ticket,
                inputs=card_inputs,
                retry_reason=retry_reason,
                continuation_tail=continuation_tail,
                extend_to_min=extend_to_min,
            ),
            card_inputs,
        )

    def _find_claim_ticket(self, ticket: str) -> tuple[str, str] | None:
        runs_dir = self.data_dir / "runs"
        if not runs_dir.is_dir():
            return None
        for entry in sorted(runs_dir.iterdir(), key=lambda path: path.name):
            if not entry.is_dir() or not _RUN_ID.fullmatch(entry.name):
                continue
            try:
                manifest = load_manifest(entry / "manifest.json")
            except (OSError, ValueError) as error:
                _warn_skipped_run(entry.name, error)
                continue
            for task_id, task in manifest["tasks"].items():
                claim = task.get("claim")
                if isinstance(claim, Mapping) and claim.get("ticket") == ticket:
                    return entry.name, task_id
        return None

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
        """Apply one internal state-machine transition.

        The direct ``claimed`` transition remains a small test/DAG seam and
        intentionally does not create a lease.  Real executor claims must go
        through :meth:`claim_task` or :meth:`claim_next`.
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
            if state == "failed" and (
                not isinstance(reason, str) or not reason.strip()
            ):
                raise InvalidTransition("failed transition requires a non-empty reason")
            if task["state"] == "claimed" and state != "claimed":
                _release_claim(run_dir, task_id, task)
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
            self._persist_manifest(run_dir, manifest)
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
            self._preflight_ranges(manifest, run_id, additions)
            _create_dynamic_task_dirs(run_id, additions, self.task_dir)
            _commit_dynamic_tasks(manifest, additions, self.task_definitions)
            _refresh_blocked_tasks(manifest, self._now())
            _update_run_status(manifest)
            _touch_manifest(manifest, self._now())
            self._persist_manifest(run_dir, manifest)
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
                run_dir=self.run_dir(run_id),
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
            normalised.invalidations = _select_invalidation_requests(
                manifest,
                normalised.invalidations,
            )
            for invalidated_id, reason in normalised.invalidations:
                _validate_invalidation_request(manifest, invalidated_id, reason)
            _validate_manifest_updates(normalised.manifest_updates)
            self._preflight_ranges(
                manifest, run_id, normalised.add_tasks,
                pending_outputs={task_id: normalised.output},
                updates=normalised.manifest_updates,
            )
            created_dirs = _create_dynamic_task_dirs(
                run_id,
                normalised.add_tasks,
                self.task_dir,
            )
            self._write_task_output(run_id, task_id, normalised.output)
        except Exception as error:  # code-task exceptions become failed tasks
            _remove_created_task_dirs(created_dirs)
            _fail_task(
                manifest,
                task_id,
                self._now(),
                _error_text(error),
                run_dir=self.run_dir(run_id),
            )
            return

        if task["state"] == "claimed":
            _release_claim(self.run_dir(run_id), task_id, task)
        _set_task_state(
            manifest,
            task_id,
            "done",
            self._now(),
            reason="コードタスクの実行に成功",
        )
        task["error"] = None
        if normalised.manifest_updates:
            for key, value in normalised.manifest_updates.items():
                if key == "table_snapshot":
                    manifest[key] = dict(value)
                elif key == "input_ratio":
                    manifest[key] = value
                elif key == "scale":
                    manifest[key] = dict(value)
                else:
                    manifest[key].extend(value)
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
        for invalidated_id, reason in normalised.invalidations:
            _apply_invalidation(
                manifest,
                self.run_dir(run_id),
                invalidated_id,
                reason,
                self.task_definitions,
                self._now(),
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
        from .world_facts import supplement_fact_outputs
        dependency_outputs = supplement_fact_outputs(self.run_dir(run_id), manifest, task, dependency_outputs, definition=definition)
        source_outputs = _source_outputs(
            manifest, task, definition, dependency_outputs
        )
        from .story_materials import sequential_sources
        source_outputs = sequential_sources(task, manifest, dependency_outputs, source_outputs)
        run_input = _read_run_input(self.run_dir(run_id))
        try:
            if "glossary" in definition.get("inputs", {}):
                # Supplement the assignment view with a derived glossary;
                # the persisted assignment and registration outputs stay intact.
                registrations = registration_outputs(self.run_dir(run_id), manifest)
                glossary = build_glossary(registrations)
                if "S5.facts" in source_outputs:
                    from .character_facts import sheet_for_card
                    source_outputs["S5.facts"] = {
                        key: sheet_for_card(sheet, glossary)
                        for key, sheet in source_outputs["S5.facts"].items()
                    }
                base_definition = {**definition, "inputs": {
                    name: slot for name, slot in definition["inputs"].items()
                    if name != "glossary"
                }}
                base_inputs = resolve_inputs(
                    base_definition, source_outputs, run_input, index=task["index"]
                )
                assignment = registrations.get("S3.assign", {})
                source_outputs["S3.assign"] = {
                    **source_outputs.get("S3.assign", {}),
                    "glossary": related_glossary(
                        glossary, assignment, base_inputs, task["index"]
                    ),
                }
            inputs = resolve_inputs(
                definition,
                source_outputs,
                run_input,
                index=task["index"],
            )
        except (KeyError, SelectorError, TypeError) as error:
            raise TaskCardError(f"入力の生成に失敗しました: {error}") from error
        from .world_facts import specialize_fact_definition
        definition = specialize_fact_definition(definition, inputs)
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

    def _complete_ready_cached_tasks(
        self,
        manifest: dict[str, Any],
        run_id: str,
    ) -> bool:
        """Complete ready LLM tasks whose accepted output is already cached."""

        changed = False
        for task_id, task in manifest["tasks"].items():
            if task["kind"] != "llm" or task["state"] != "ready":
                continue
            try:
                context = self._build_context(manifest, run_id, task_id)
                card_inputs = _prepare_card_inputs(
                    self.task_definitions[task["type"]],
                    context.inputs,
                )
                fixed = None
                if context.definition.get("range"):
                    limits = preflight_range(
                        context.definition, {**manifest, **context.inputs},
                        run_input=context.run_input, outputs=_source_outputs(
                            manifest, task, context.definition, context.dependency_outputs
                        ), index=task["index"],
                    )
                    fixed = fixed_range_value(context.definition, limits)
                if fixed is not None:
                    atomic_write_json(self.task_dir(run_id, task_id) / "input.json", card_inputs)
                    _write_submitted_output(self.task_dir(run_id, task_id), context.definition, fixed)
                    _set_task_state(manifest, task_id, "done", self._now(), reason="値の範囲が1つに確定")
                    task["error"] = None
                    changed = True
                    continue
                key = self._cache_key_for_task(manifest, task_id, card_inputs)
            except Exception as error:
                _fail_task(
                    manifest,
                    task_id,
                    self._now(),
                    _error_text(error),
                    run_dir=self.run_dir(run_id),
                )
                changed = True
                continue

            task["cache_key"] = key
            hit, output = lookup_cache(self.data_dir, key)
            if not hit:
                changed = True
                continue
            output = _postprocess_accepted_output(task, output)
            atomic_write_json(self.task_dir(run_id, task_id) / "input.json", card_inputs)
            _write_submitted_output(
                self.task_dir(run_id, task_id),
                context.definition,
                output,
            )
            _set_task_state(
                manifest,
                task_id,
                "done",
                self._now(),
                reason="キャッシュの出力を再利用",
            )
            task["error"] = None
            _remove_partial_output(self.task_dir(run_id, task_id))
            changed = True
        return changed

    def _cache_key_for_task(
        self,
        manifest: Mapping[str, Any],
        task_id: str,
        card_inputs: Mapping[str, Any],
    ) -> str:
        """Calculate the key for the exact input shown on a task card."""

        task = _get_task(manifest, task_id)
        definition = self.task_definitions[task["type"]]
        task_seed = derive_task_seed(
            int(manifest["seed"]), task_id, int(task["attempt"])
        )
        return cache_key(
            task["type"],
            int(definition["version"]),
            card_inputs,
            candidate=_candidate_number(task_id),
            seed=task_seed,
            attempt=int(task["attempt"]),
            share_across_runs=bool(definition.get("share_across_runs", False)),
        )

    def _now(self) -> str:
        return _timestamp(self._clock)

    def _preflight_ranges(
        self, manifest: Mapping[str, Any], run_id: str, specs: Sequence[TaskSpec], *,
        pending_outputs: Mapping[str, Any] | None = None,
        updates: Mapping[str, Any] | None = None,
    ) -> None:
        """Reject defective bounds before creating any of the added task directories."""
        if not any(self.task_definitions[spec.type].get("range") for spec in specs):
            return
        values = {**manifest, **(updates or {})}
        run_input = _read_run_input(self.run_dir(run_id))
        prospective = deepcopy(manifest)
        for spec in specs:
            prospective["tasks"][spec.task_id] = _new_task_record(
                spec, self.task_definitions[spec.type], prospective
            )
        for key in pending_outputs or {}:
            prospective["tasks"][key]["state"] = "done"
        for spec in specs:
            definition = self.task_definitions[spec.type]
            if not definition.get("range"):
                continue
            task = {"deps": list(spec.deps), "index": list(spec.index)}
            dependencies = _read_dependency_outputs(self.run_dir(run_id), prospective, task)
            dependencies.update({key: value for key, value in (pending_outputs or {}).items()
                                 if key in spec.deps})
            sources = _source_outputs(prospective, task, definition, dependencies)
            limits = preflight_range(definition, values, run_input=run_input,
                                     outputs=sources, index=spec.index)
            fixed_range_value(definition, limits)

    def _persist_manifest(self, run_dir: Path, manifest: Mapping[str, Any]) -> None:
        write_failure_reports(
            run_dir, manifest, self.task_definitions, self.repository_root,
            resolve_inputs=lambda task_id: self._build_context(
                manifest, manifest["run_id"], task_id
            ).inputs,
        )
        write_manifest(run_dir / "manifest.json", manifest)


# A shorter name is useful to callers that treat this as a run engine.
RunEngine = Orchestrator


def create_run(
    data_dir: str | Path,
    task_definitions: Mapping[str, Mapping[str, Any]],
    *,
    harness_root: str | Path | None = None,
    repository_root: str | Path | None = None,
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
        harness_root=harness_root,
        repository_root=repository_root,
    ).create_run(**kwargs)


def advance_run(
    data_dir: str | Path,
    run_id: str,
    task_definitions: Mapping[str, Mapping[str, Any]],
    *,
    code_handlers: Mapping[str, CodeTaskHandler] | None = None,
    clock: Callable[[], datetime] | None = None,
    harness_root: str | Path | None = None,
    repository_root: str | Path | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for advancing one run's code tasks."""
    return Orchestrator(
        data_dir,
        task_definitions,
        code_handlers,
        clock=clock,
        harness_root=harness_root,
        repository_root=repository_root,
    ).advance(run_id)


def _validate_task_definitions(
    definitions: Mapping[str, Mapping[str, Any]],
    *,
    schema_root: str | Path | None = None,
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
        validate_task_definition_output_example(
            copied,
            schema_root=(
                schema_root
                if schema_root is not None
                else TASK_DEFINITION_SCHEMA_PATH.resolve().parents[1]
            ),
        )
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
    *,
    allow_deferred_dependencies: bool = False,
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
        missing_dependencies = [
            dependency for dependency in spec.deps if dependency not in task_ids
        ]
        if missing_dependencies:
            if not allow_deferred_dependencies or not all(
                _is_deferred_dependency(dependency)
                for dependency in missing_dependencies
            ):
                missing = missing_dependencies[0]
                raise DAGError(
                    f"task {spec.task_id!r} depends on missing task {missing!r}"
                )
        if definitions[spec.type]["kind"] not in {"code", "llm"}:
            raise DAGError(f"invalid task kind: {spec.type}")
        _validate_input_dependencies(
            spec,
            specs_by_id,
            definitions[spec.type],
            allow_deferred_dependencies=allow_deferred_dependencies,
        )

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
            if dependency in specs_by_id:
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
    *,
    allow_deferred_dependencies: bool = False,
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
            dependency in specs_by_id and specs_by_id[dependency].type == source
            for dependency in spec.deps
        ):
            if allow_deferred_dependencies and any(
                dependency not in specs_by_id
                and _deferred_dependency_type(dependency) == source
                for dependency in spec.deps
            ):
                continue
            if slot.get("required") is False or (
                "required" not in slot
                and source in {"S4.section", "S4.item"}
            ):
                continue
            raise DAGError(
                f"task {spec.task_id!r} input slot {slot_name!r} requires a "
                f"dependency of type {source!r}"
            )


def _is_deferred_dependency(task_id: str) -> bool:
    return _deferred_dependency_type(task_id) == "S8.judge"


def _deferred_dependency_type(task_id: str) -> str | None:
    if re.fullmatch(r"S8\.judge-e[0-9]{3}", task_id):
        return "S8.judge"
    return None


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


def _resolve_executor_id(executor_id: str | None) -> str:
    if executor_id is None:
        host = re.sub(r"[^A-Za-z0-9._-]", "-", socket.gethostname())
        host = host or "host"
        pid_suffix = f"-{os.getpid()}"
        host = host[: 64 - len(pid_suffix)]
        executor_id = f"{host}{pid_suffix}"
    if not isinstance(executor_id, str) or not _EXECUTOR_ID.fullmatch(executor_id):
        raise ClaimError("executor_id は英数字・'.'・'_'・'-' を1〜64文字で指定してください")
    return executor_id


def _warn_skipped_run(run_id: str, error: BaseException) -> None:
    print(
        f"警告: run {run_id} の manifest または claim に異常があるため、"
        f"claim の対象からスキップします: {error}",
        file=sys.stderr,
    )


def _validate_isolation(isolation: str) -> None:
    if not isinstance(isolation, str) or isolation not in _ISOLATIONS:
        values = ", ".join(sorted(_ISOLATIONS))
        raise ClaimError(f"isolation は {values} のいずれかです")


def _parse_utc_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
        value,
    ):
        raise ClaimError("claim の時刻形式が不正です")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ClaimError("claim の時刻形式が不正です") from error
    return parsed.astimezone(timezone.utc)


def _lease_expires_at(claimed_at: str, lease_minutes: Any) -> str:
    if isinstance(lease_minutes, bool) or not isinstance(
        lease_minutes, (int, float)
    ) or lease_minutes <= 0:
        raise ClaimError("lease_minutes は0より大きい数でなければなりません")
    try:
        lease_seconds = max(1, math.ceil(float(lease_minutes) * 60))
    except (OverflowError, ValueError) as error:
        raise ClaimError("lease_minutes は有限の数でなければなりません") from error
    expiry = _parse_utc_timestamp(claimed_at) + timedelta(seconds=lease_seconds)
    return expiry.isoformat().replace("+00:00", "Z")


def _manifest_claim(payload: Mapping[str, Any]) -> dict[str, str]:
    return {
        "ticket": payload["ticket"],
        "executor_id": payload["executor_id"],
        "isolation": payload["isolation"],
        "lease_expires_at": payload["lease_expires_at"],
    }


def _validate_claim_payload(payload: Any) -> dict[str, str]:
    if not isinstance(payload, Mapping):
        raise ClaimError("claim.json はオブジェクトでなければなりません")
    expected = {
        "ticket",
        "task_id",
        "executor_id",
        "isolation",
        "claimed_at",
        "lease_expires_at",
    }
    if set(payload) != expected:
        raise ClaimError("claim.json の項目が不正です")
    ticket = payload["ticket"]
    task_id = payload["task_id"]
    executor_id = payload["executor_id"]
    isolation = payload["isolation"]
    if not isinstance(ticket, str) or not _TICKET.fullmatch(ticket):
        raise ClaimError("claim.json の ticket が不正です")
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise ClaimError("claim.json の task_id が不正です")
    if not isinstance(executor_id, str) or not _EXECUTOR_ID.fullmatch(executor_id):
        raise ClaimError("claim.json の executor_id が不正です")
    _validate_isolation(isolation)
    claimed_at = payload["claimed_at"]
    lease_expires_at = payload["lease_expires_at"]
    _parse_utc_timestamp(claimed_at)
    _parse_utc_timestamp(lease_expires_at)
    return {
        "ticket": ticket,
        "task_id": task_id,
        "executor_id": executor_id,
        "isolation": isolation,
        "claimed_at": claimed_at,
        "lease_expires_at": lease_expires_at,
    }


def _read_claim_file(path: Path) -> dict[str, str]:
    try:
        with path.open("r", encoding="utf-8", newline=None) as stream:
            payload = json.load(stream)
    except FileNotFoundError as error:
        raise InvalidClaimError("claim.json がありません") from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InvalidClaimError(f"claim.json を読み込めません: {path}") from error
    try:
        return _validate_claim_payload(payload)
    except ClaimError as error:
        raise InvalidClaimError(str(error)) from error


def _claim_expired(payload: Mapping[str, Any], now: str) -> bool:
    claimed_at = _parse_utc_timestamp(payload["claimed_at"])
    lease_expires_at = _parse_utc_timestamp(payload["lease_expires_at"])
    return lease_expires_at <= claimed_at or (
        _parse_utc_timestamp(now) >= lease_expires_at
    )


def _create_claim_file(path: Path, payload: Mapping[str, Any]) -> None:
    """Create claim.json with O_EXCL; unlike normal files it must not replace."""

    data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    descriptor = os.open(
        path,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0),
        0o666,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _release_claim(
    run_dir: Path,
    task_id: str,
    task: dict[str, Any],
    *,
    archive_prefix: str | None = None,
) -> None:
    """Remove or archive a task claim and clear its manifest record."""

    claim_path = run_dir / "tasks" / task_id / "claim.json"
    if archive_prefix is None:
        try:
            claim_path.unlink()
        except FileNotFoundError:
            pass
    elif claim_path.exists():
        _rename_claim(claim_path, archive_prefix)
    task["claim"] = None


def _require_claim_locked(
    manifest: Mapping[str, Any],
    run_dir: Path,
    task_id: str,
    ticket: str,
    now: str,
) -> dict[str, str]:
    """Re-read and validate a claim while the run manifest lock is held."""

    task = _get_task(manifest, task_id)
    claim = task.get("claim")
    claim_path = run_dir / "tasks" / task_id / "claim.json"
    if (
        task["state"] != "claimed"
        or not isinstance(claim, Mapping)
        or claim.get("ticket") != ticket
    ):
        raise InvalidClaimError("ticket の claim は無効です")
    try:
        payload = _read_claim_file(claim_path)
        if payload["task_id"] != task_id or payload["ticket"] != ticket:
            raise InvalidClaimError("claim.json の ticket が一致しません")
        if _claim_expired(payload, now):
            raise InvalidClaimError("ticket の lease が切れています")
        if _manifest_claim(payload) != dict(claim):
            raise InvalidClaimError("manifest と claim.json が一致しません")
    except ClaimError as error:
        if isinstance(error, InvalidClaimError):
            raise
        raise InvalidClaimError(str(error)) from error
    return dict(claim)


def _load_card_inputs(task_dir: Path) -> dict[str, Any]:
    path = task_dir / "input.json"
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise OrchestrationError(f"カード入力を読み込めません: {path}") from error
    if not isinstance(value, Mapping):
        raise OrchestrationError(f"カード入力がオブジェクトではありません: {path}")
    return dict(value)


def _prepare_card_inputs(
    definition: Mapping[str, Any], inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Leave room for the required continuation tail when inputs can shrink.

    Use the normal slot order and truncation rules. If required input cannot
    fit with a reserved tail, retain the normal budget: a complete first
    submission is still possible, and §8 handles an insufficient tail if a
    continuation actually becomes necessary.
    """
    budget = int(definition.get("max_input_chars", 3000))
    collection_slot = {
        "S5.motive": "names", "S5.relationship": "other_person",
    }.get(definition.get("id"))
    if collection_slot is not None and isinstance(inputs.get(collection_slot), Mapping):
        # Indexed name outputs are a collection, not a single object. Expose
        # its entries as an array so the defined head rule can shorten it by
        # person while keeping each retained person's source ID explicit.
        inputs = dict(inputs)
        inputs[collection_slot] = [
            {"id": person_id, **value}
            for person_id, value in inputs[collection_slot].items()
        ]
    if definition.get("output") == "text" and definition.get("extend_to_min") and budget > 1000:
        reserved = dict(definition, max_input_chars=budget - 1000)
        try:
            return prepare_task_inputs(reserved, inputs=inputs)
        except InputBudgetError:
            pass
    return prepare_task_inputs(definition, inputs=inputs)


def _minimum_text_chars(definition: Mapping[str, Any]) -> int:
    return max(
        (
            check["min_chars"]["n"]
            for check in definition.get("validate", {}).get("checks", [])
            if isinstance(check, Mapping)
            and "min_chars" in check
            and "field" not in check["min_chars"]
        ),
        default=0,
    )


def _continuation_enabled(definition: Mapping[str, Any]) -> bool:
    """Apply the task-model default for the optional continuation field."""
    return definition.get("element", "text") == "text" and bool(
        definition.get(
            "continuation",
            definition.get("output") == "text",
        )
    )


def _is_truncated_submission(raw_output: Any, truncated: bool) -> bool:
    return bool(truncated) or (
        isinstance(raw_output, str) and text_is_truncated(raw_output)
    )


def _read_partial_output(task_dir: Path) -> str:
    path = task_dir / "partial.md"
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeError) as error:
        raise OrchestrationError(f"継続中の出力を読み込めません: {path}") from error


def _remove_partial_output(task_dir: Path) -> None:
    try:
        (task_dir / "partial.md").unlink()
    except FileNotFoundError:
        pass


def _write_submitted_output(
    task_dir: Path,
    definition: Mapping[str, Any],
    value: Any,
) -> None:
    output_kind = definition.get("output")
    if output_kind == "json":
        atomic_write_json(task_dir / "output.json", value)
        try:
            (task_dir / "output.md").unlink()
        except FileNotFoundError:
            pass
    elif output_kind == "text":
        if not isinstance(value, str):
            raise OrchestrationError("text タスクの検証結果が文字列ではありません")
        atomic_write_text(task_dir / "output.md", value)
        try:
            (task_dir / "output.json").unlink()
        except FileNotFoundError:
            pass
    else:
        raise OrchestrationError("task definition output must be json or text")


def _postprocess_accepted_output(task: Mapping[str, Any], value: Any) -> Any:
    from .story_materials import assign_material_id

    return assign_material_id(task, value)


def _normalise_invalidation_requests(value: Any) -> list[tuple[str, str]]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise DAGError("invalidations must be a sequence")
    requests: list[tuple[str, str]] = []
    for request in value:
        if isinstance(request, Mapping):
            task_id = request.get("task_id")
            reason = request.get("reason")
        elif isinstance(request, Sequence) and not isinstance(request, (str, bytes)):
            if len(request) != 2:
                raise DAGError("invalidation must contain task_id and reason")
            task_id, reason = request
        else:
            raise DAGError("invalid invalidation request")
        if not isinstance(task_id, str) or not task_id:
            raise DAGError("invalid invalidation task ID")
        if not isinstance(reason, str) or not reason.strip():
            raise DAGError("invalidation requires a non-empty reason")
        requests.append((task_id, reason))
    return requests


def _select_invalidation_requests(
    manifest: Mapping[str, Any],
    requests: Sequence[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Deduplicate requests and keep only the outermost invalidations."""

    unique: list[tuple[str, str]] = []
    seen: set[str] = set()
    for task_id, reason in requests:
        _get_task(manifest, task_id)
        if task_id in seen:
            continue
        seen.add(task_id)
        unique.append((task_id, reason))

    excluded = {
        task_id
        for task_id, _ in unique
        if any(
            task_id in _dependency_closure(manifest, other_id)
            for other_id, _ in unique
            if other_id != task_id
        )
    }
    return [request for request in unique if request[0] not in excluded]


def _validate_invalidation_request(
    manifest: Mapping[str, Any],
    task_id: str,
    reason: str,
) -> None:
    if not isinstance(reason, str) or not reason.strip():
        raise DAGError("invalidation requires a non-empty reason")
    task = _get_task(manifest, task_id)
    if task["state"] != "done":
        raise InvalidTransition("無効化できるのは done のタスクだけです")


def _apply_invalidation(
    manifest: dict[str, Any],
    run_dir: Path,
    task_id: str,
    reason: str,
    definitions: Mapping[str, Mapping[str, Any]],
    at: str,
) -> None:
    """Apply one invalidation; callers hold the manifest lock."""

    target = _get_task(manifest, task_id)
    if target["state"] != "done":
        return
    definition = definitions[target["type"]]
    max_invalidations = int(definition.get("max_invalidations", 2))
    current_count = int(target["invalidations"])
    if current_count >= max_invalidations:
        target["error"] = reason
        _force_task_state(manifest, task_id, "failed", at, reason=reason)
        _update_run_status(manifest)
        _touch_manifest(manifest, at)
        write_manifest(run_dir / "manifest.json", manifest)
        return

    next_count = current_count + 1
    target["error"] = reason
    target["cache_key"] = None
    target["attempt"] = int(target["attempt"]) + 1
    target["invalidations"] = next_count
    _force_task_state(manifest, task_id, "ready", at, reason=reason)

    descendants = _dependent_closure(manifest, task_id)
    cleanup_outputs = [task_id]
    revoked_claims: list[str] = []
    for dependent_id in descendants:
        dependent = _get_task(manifest, dependent_id)
        if dependent["state"] in {"done", "ready", "claimed"}:
            if dependent["state"] == "claimed":
                revoked_claims.append(dependent_id)
                dependent["claim"] = None
            cleanup_outputs.append(dependent_id)
            dependent["cache_key"] = None
            _force_task_state(
                manifest,
                dependent_id,
                "blocked",
                at,
                reason=f"依存タスク {task_id} が無効化された",
            )

    # Persist the state transition first.  The output and claim files are
    # cleanup artifacts and must not be removed before this durable boundary.
    _update_run_status(manifest)
    _touch_manifest(manifest, at)
    write_manifest(run_dir / "manifest.json", manifest)
    for cleanup_id in cleanup_outputs:
        _remove_task_outputs(run_dir, cleanup_id)
    for revoked_id in revoked_claims:
        _release_claim(
            run_dir,
            revoked_id,
            _get_task(manifest, revoked_id),
            archive_prefix="claim.revoked",
        )


def _dependent_closure(manifest: Mapping[str, Any], task_id: str) -> list[str]:
    reverse: dict[str, list[str]] = {candidate: [] for candidate in manifest["tasks"]}
    for candidate_id, task in manifest["tasks"].items():
        for dependency in task["deps"]:
            reverse.setdefault(dependency, []).append(candidate_id)
    result: list[str] = []
    seen = {task_id}
    queue = list(reverse.get(task_id, []))
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        result.append(current)
        queue.extend(reverse.get(current, []))
    return result


def _dependency_closure(manifest: Mapping[str, Any], task_id: str) -> set[str]:
    result: set[str] = set()
    queue = list(_get_task(manifest, task_id)["deps"])
    while queue:
        current = queue.pop(0)
        if current in result:
            continue
        result.add(current)
        queue.extend(_get_task(manifest, current)["deps"])
    return result


def _force_task_state(
    manifest: dict[str, Any],
    task_id: str,
    state: str,
    at: str,
    *,
    reason: str,
) -> None:
    """Record the two state changes that only invalidation may perform."""

    task = _get_task(manifest, task_id)
    old_state = task["state"]
    if old_state == state:
        return
    task["state"] = state
    task["history"].append(
        {"at": at, "from": old_state, "to": state, "reason": reason}
    )


def _remove_task_outputs(run_dir: Path, task_id: str) -> None:
    task_dir = run_dir / "tasks" / task_id
    for name in ("output.json", "output.md"):
        try:
            (task_dir / name).unlink()
        except FileNotFoundError:
            pass


def _next_attempt_number(attempts_dir: Path) -> int:
    """Return the smallest unused durable attempt-file number."""

    for number in range(1, 2**31):
        if not (attempts_dir / f"{number}.json").exists():
            return number
    raise OrchestrationError(f"試行履歴の番号を割り当てられません: {attempts_dir}")


def _rename_claim(path: Path, prefix: str) -> Path:
    for number in range(1, 2**31):
        destination = path.with_name(f"{prefix}.{number}.json")
        # All callers hold the run's manifest lock.  Checking before
        # os.replace is therefore enough to preserve the smallest-unused
        # suffix; unlike a blind replace it also avoids overwriting history.
        if destination.exists():
            continue
        try:
            os.replace(path, destination)
        except FileNotFoundError:
            raise InvalidClaimError("claim.json がありません") from None
        return destination
    raise ClaimError(f"claim の退避先を作成できません: {path}")


def _refresh_claims(
    manifest: dict[str, Any],
    run_dir: Path,
    now: str,
    clock: Callable[[], datetime],
) -> bool:
    """Reconcile claim files and manifest records before selecting work."""

    del clock  # Kept in the seam so clock ownership remains explicit.
    changed = False
    for task_id, task in manifest["tasks"].items():
        claim_path = run_dir / "tasks" / task_id / "claim.json"
        if not claim_path.exists():
            if task["state"] == "claimed":
                executor_id = None
                if isinstance(task.get("claim"), Mapping):
                    executor_id = task["claim"].get("executor_id")
                _release_claim(run_dir, task_id, task)
                _set_task_state(
                    manifest,
                    task_id,
                    "ready",
                    now,
                    reason="claim.json がありません",
                    executor_id=executor_id,
                )
                changed = True
            continue

        # A claim left after a terminal transition is orphaned, regardless of
        # whether its JSON happens to be well formed.  Quarantine it without
        # making a completed or failed run unclaimable.
        if task["state"] in {"done", "failed", "skipped"}:
            _release_claim(
                run_dir,
                task_id,
                task,
                archive_prefix="claim.expired",
            )
            changed = True
            continue

        try:
            payload = _read_claim_file(claim_path)
            if payload["task_id"] != task_id:
                raise ClaimError(f"claim.json の task_id が一致しません: {task_id}")
            if task["kind"] != "llm":
                raise ClaimError(f"code task に claim.json があります: {task_id}")
        except (ClaimError, InvalidClaimError):
            # A malformed, mismatched, or code-task claim is isolated to this
            # task.  Other tasks and runs remain eligible for selection.
            executor_id = None
            if isinstance(task.get("claim"), Mapping):
                executor_id = task["claim"].get("executor_id")
            _release_claim(
                run_dir,
                task_id,
                task,
                archive_prefix="claim.expired",
            )
            if task["state"] == "claimed":
                _set_task_state(
                    manifest,
                    task_id,
                    "ready",
                    now,
                    reason="claim.json が不正です",
                    executor_id=executor_id,
                )
            changed = True
            continue

        if _claim_expired(payload, now):
            executor_id = None
            if isinstance(task.get("claim"), Mapping):
                executor_id = task["claim"].get("executor_id")
            _release_claim(
                run_dir,
                task_id,
                task,
                archive_prefix="claim.expired",
            )
            if task["state"] == "claimed":
                _set_task_state(
                    manifest,
                    task_id,
                    "ready",
                    now,
                    reason="leaseが切れた",
                    executor_id=executor_id,
                )
            changed = True
            continue

        record = _manifest_claim(payload)
        if task["state"] == "ready":
            task["claim"] = record
            _set_task_state(
                manifest,
                task_id,
                "claimed",
                now,
                reason="既存のclaimを復元",
                executor_id=payload["executor_id"],
            )
            changed = True
        elif task["state"] == "claimed":
            if task.get("claim") != record:
                task["claim"] = record
                changed = True
        else:
            # blocked tasks cannot own a live claim.  Keep the anomaly local.
            _release_claim(
                run_dir,
                task_id,
                task,
                archive_prefix="claim.expired",
            )
            changed = True
    return changed


def _task_depths(manifest: Mapping[str, Any]) -> dict[str, int]:
    memo: dict[str, int] = {}
    visiting: set[str] = set()

    def depth(task_id: str) -> int:
        if task_id in memo:
            return memo[task_id]
        if task_id in visiting:
            raise DAGError(f"task graph contains a cycle at {task_id}")
        visiting.add(task_id)
        task = _get_task(manifest, task_id)
        value = max(
            (
                depth(dependency) + 1
                for dependency in task["deps"]
                if dependency in manifest["tasks"]
            ),
            default=0,
        )
        visiting.remove(task_id)
        memo[task_id] = value
        return value

    for task_id in manifest["tasks"]:
        depth(task_id)
    return memo


def _next_ready_llm_task(manifest: Mapping[str, Any]) -> str | None:
    depths = _task_depths(manifest)
    candidates = [
        task_id
        for task_id, task in manifest["tasks"].items()
        if task["kind"] == "llm" and task["state"] == "ready"
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda task_id: (depths[task_id], task_id))


def _candidate_number(task_id: str) -> int:
    """Extract a ``c<n>`` candidate suffix from a task ID."""

    match = re.search(r"(?:^|-)c([1-9][0-9]*)$", task_id)
    if match is not None:
        return int(match.group(1))
    return 0


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
            dependency in manifest["tasks"]
            and manifest["tasks"][dependency]["state"] in {"done", "skipped"}
            for dependency in task["deps"]
        ):
            raise InvalidTransition("blocked task dependencies are not complete")
    if state == "skipped" and old_state not in {"blocked", "ready", "claimed"}:
        raise InvalidTransition("only blocked, ready, or claimed tasks may be skipped")


def _set_task_state(
    manifest: dict[str, Any],
    task_id: str,
    state: str,
    at: str,
    *,
    reason: str | None = None,
    executor_id: str | None = None,
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
    if executor_id is not None:
        event["executor_id"] = executor_id
    task["history"].append(event)


def _refresh_blocked_tasks(manifest: dict[str, Any], at: str) -> None:
    changed = True
    while changed:
        changed = False
        for task_id, task in manifest["tasks"].items():
            if task["state"] != "blocked":
                continue
            if all(
                dependency in manifest["tasks"]
                and manifest["tasks"][dependency]["state"] in {"done", "skipped"}
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
    manifest: dict[str, Any],
    task_id: str,
    at: str,
    error: str,
    *,
    run_dir: Path | None = None,
) -> None:
    task = _get_task(manifest, task_id)
    if not isinstance(error, str) or not error.strip():
        raise InvalidTransition("failed transition requires a non-empty reason")
    _validate_transition(manifest, task_id, "failed")
    if manifest["tasks"][task_id]["state"] == "claimed" and run_dir is not None:
        _release_claim(run_dir, task_id, manifest["tasks"][task_id])
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
    _validate_graph(
        combined_specs,
        definitions,
        allow_deferred_dependencies=True,
    )


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
    normalised.invalidations = [
        *_normalise_invalidation_requests(context._invalidations),
        *_normalise_invalidation_requests(normalised.invalidations),
    ]
    return normalised


def _validate_manifest_updates(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise DAGError("コードタスクの manifest 更新はオブジェクトでなければなりません")
    for key, item in value.items():
        if not isinstance(key, str) or key not in {
            "input_ratio",
            "table_snapshot",
            "warnings",
            "scale",
        }:
            raise DAGError(f"コードタスクが更新できない manifest 項目です: {key!r}")
        if key == "input_ratio" and (
            isinstance(item, bool) or not isinstance(item, (int, float))
        ):
            raise DAGError("manifest の input_ratio は数値でなければなりません")
        if key == "scale" and not isinstance(item, Mapping):
            raise DAGError("manifest の scale はオブジェクトでなければなりません")
        if key == "table_snapshot" and not isinstance(item, Mapping):
            raise DAGError("manifest の table_snapshot はオブジェクトでなければなりません")
        if key == "warnings" and (
            not isinstance(item, list) or not all(isinstance(entry, str) for entry in item)
        ):
            raise DAGError("manifest の warnings は文字列の配列でなければなりません")


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
    for slot_name, slot_definition in input_slots.items():
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
        if not matching:
            continue

        indexed = any(
            _dependency_index_key(dependency, manifest["tasks"][dependency]) is not None
            for dependency in matching
        )
        if indexed:
            # References to an indexed task type always expose an object whose
            # keys are the dependency indexes.  Indexed sources stay grouped
            # unless a task explicitly asks for the previous scene text,
            # whose single dependency is reduced to its required tail.
            if slot_name == "previous_scene" and slot == "S7.detail" and len(matching) == 1:
                value = dependency_outputs[matching[0]]
                source_value = value[-600:] if isinstance(value, str) else value
            else:
                source_value = {}
                for dependency in matching:
                    dependency_record = manifest["tasks"][dependency]
                    index_key = _dependency_index_key(dependency, dependency_record)
                    if index_key is None:
                        raise TaskCardError(
                            f"添字付きタスクの添字がありません: {dependency}"
                        )
                    value = dependency_outputs[dependency]
                    if (
                        task.get("type") == "S8.compare"
                        and slot == "S8.plan"
                        and len(matching) == 1
                    ):
                        value = _prepare_s8_compare_plan(value, task)
                    source_value[index_key] = value
        elif len(matching) == 1:
            # An unindexed task type refers to its output directly.
            source_value = dependency_outputs[matching[0]]
        else:
            # There cannot normally be multiple unindexed dependencies of the
            # same type, but preserve all values if a custom harness creates
            # such a graph rather than silently discarding one.
            source_value = [dependency_outputs[task_id] for task_id in matching]
        if slot_name == "prerequisite_items" and slot == "S4.item":
            source_value = _summarize_world_items(source_value)
        if slot_name == "prerequisite_sections" and slot == "S4.section":
            source_value = _summarize_world_sections(source_value)
        if slot == "S4.fact":
            from .world_facts import fact_source_context
            source_value = fact_source_context(
                dependency_outputs.get("S3.assign", {}), dependency_outputs, task,
            )
        if task.get("type") == "S7.event":
            from .story_s7 import event_source_for_card
            source_value = event_source_for_card(task, slot, source_value)
        result[slot] = source_value
    return result


def _dependency_index_key(
    task_id: str,
    task: Mapping[str, Any],
) -> str | None:
    """Return the complete index used as a key for an indexed dependency."""

    raw_index = task.get("index")
    if isinstance(raw_index, (list, tuple)) and raw_index:
        if not all(isinstance(value, str) and value for value in raw_index):
            raise TaskCardError(f"タスクの添字が不正です: {task_id}")
        return "-".join(raw_index)

    # A few lightweight test/custom-harness contexts omit the manifest index
    # while retaining the canonical ``<type>-<index>`` task ID.  Recovering it
    # here keeps the source contract identical for those contexts; real run
    # manifests always carry the explicit index list.
    task_type = task.get("type")
    prefix = f"{task_type}-" if isinstance(task_type, str) else ""
    if prefix and task_id.startswith(prefix):
        suffix = task_id[len(prefix) :]
        if suffix:
            return suffix
    return None


def _prepare_s8_compare_plan(
    value: Any,
    task: Mapping[str, Any],
) -> Any:
    """Resolve the numbered comparison target for an S8.compare card.

    S8.compare tasks retain the current event as their first index, while the
    second ``k<n>`` index selects one of S8.plan's targets.  The generic
    selector language only exposes the first index, so present the selected
    target under the current event ID before resolving the task definition.
    """

    if not isinstance(value, Mapping):
        return value
    index = task.get("index")
    if not isinstance(index, list) or len(index) < 2 or not index:
        return value
    match = re.fullmatch(r"k([0-9]+)", str(index[1]))
    if match is None or not isinstance(index[0], str):
        return value
    target_number = int(match.group(1))
    targets = value.get("targets")
    if not isinstance(targets, list) or not 1 <= target_number <= len(targets):
        return value
    prepared = deepcopy(dict(value))
    prepared["targets"] = {index[0]: deepcopy(targets[target_number - 1])}
    prepared["current"] = {
        "id": index[0],
        "event": deepcopy(value.get("current")),
    }
    return prepared


def _summarize_world_items(value: Any) -> list[dict[str, str]]:
    """Make list-section outputs compact prerequisite context for S4 cards."""

    if isinstance(value, Mapping):
        values = list(value.values())
    elif isinstance(value, list):
        values = value
    else:
        values = [value]
    summaries: list[dict[str, str]] = []
    for item in values:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        body = item.get("body")
        if not isinstance(name, str) or not isinstance(body, str):
            continue
        summary = " ".join(body.split())[:120]
        summaries.append({"name": name, "summary": summary})
    return summaries


def _summarize_world_sections(value: Any) -> list[dict[str, str]]:
    """Keep only prerequisite section bodies for the next S4 card."""

    if isinstance(value, Mapping):
        values = list(value.values())
    elif isinstance(value, list):
        values = value
    else:
        values = [value]
    summaries: list[dict[str, str]] = []
    for section in values:
        if not isinstance(section, Mapping):
            continue
        body = section.get("body")
        if isinstance(body, str) and body:
            summaries.append({"body": body})
    return summaries


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
            if output_json.is_file() or output_md.is_file():
                outputs[task_id] = read_task_output(run_dir / "tasks" / task_id, dependency["type"])
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


def _is_dummy_harness_root(root: Path) -> bool:
    return root.parts[-4:] == ("src", "storyteller", "dev", "dummy")


def _repository_root_for_definition_root(root: Path) -> Path:
    if _is_dummy_harness_root(root):
        return root.parents[3]
    return root


def _discover_harness_files(
    root: Path,
    *,
    repository_root: Path | None = None,
) -> dict[str, str]:
    """Hash the files covered by a real or dummy harness snapshot."""

    repository_root = repository_root or root
    if _is_dummy_harness_root(root):
        directories = (root,)
        relative_root = (
            repository_root if root.is_relative_to(repository_root) else root
        )
    else:
        directories = tuple(
            root / name for name in ("harness", "tables", "schemas", "formats")
        )
        relative_root = root
    files: dict[str, str] = {}
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                relative = path.relative_to(relative_root).as_posix()
                files[relative] = _sha256_file(path)
    return files


def _halt_run(manifest: dict[str, Any]) -> None:
    if manifest["status"] == "halted":
        return
    manifest["status"] = "halted"
    warning = "ハーネスの変更を検出したため run を停止しました"
    if warning not in manifest["warnings"]:
        manifest["warnings"].append(warning)
    print(f"警告: {warning}: {manifest['run_id']}", file=sys.stderr)


def _error_text(error: Exception) -> str:
    if isinstance(error, KeyError):
        key = error.args[0] if error.args else "不明なキー"
        return f"必要な値が見つかりません: {key}"
    message = str(error)
    return message or error.__class__.__name__
