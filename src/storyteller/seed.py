"""Deterministic seed derivation for runs and tasks."""

from __future__ import annotations

import hashlib
import random
import secrets


MAX_SEED = (1 << 32) - 1


def _require_seed(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")


def _require_index(value: int, name: str = "index") -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")


def _sha256_seed(value: str) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def derive_run_seed(batch_seed: int, index: int) -> int:
    """Derive a run seed from a batch seed and its zero-based index."""
    _require_seed(batch_seed, "batch_seed")
    _require_index(index)
    return _sha256_seed(f"{batch_seed}:{index}")


def derive_task_seed(run_seed: int, task_id: str, attempt: int) -> int:
    """Derive a task seed from its run, task ID, and attempt number."""
    _require_seed(run_seed, "run_seed")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task_id must be a non-empty string")
    _require_index(attempt, "attempt")
    return _sha256_seed(f"{run_seed}:{task_id}:{attempt}")


def generated_seed() -> int:
    """Return a new 32-bit seed for a run without an explicit seed."""
    return secrets.randbits(32)


def task_random(run_seed: int, task_id: str, attempt: int) -> random.Random:
    """Return an independently seeded RNG for a code task."""
    return random.Random(derive_task_seed(run_seed, task_id, attempt))
