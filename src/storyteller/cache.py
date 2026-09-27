"""Cache keys and durable storage for accepted LLM task outputs.

The cache is deliberately independent from submission handling.  A caller
that has already accepted an output can store it with :func:`save_cache`,
while the orchestrator only needs :func:`lookup_cache` when an LLM task is
ready.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .storage import atomic_write_json


_CACHE_KEY = re.compile(r"^[0-9a-f]{64}$")


def normalize_input(value: Any) -> Any:
    """Return *value* with every string recursively NFKC-normalized.

    JSON arrays retain their order and JSON scalar values other than strings
    are returned unchanged.  Mapping keys are strings in JSON, so they are
    normalized as well and collisions are rejected instead of silently
    changing the input.
    """

    if isinstance(value, str):
        return unicodedata.normalize("NFKC", value)
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("cache input object keys must be strings")
            normalized_key = unicodedata.normalize("NFKC", key)
            if normalized_key in normalized:
                raise ValueError(
                    f"cache input keys collide after NFKC normalization: {key!r}"
                )
            normalized[normalized_key] = normalize_input(item)
        return normalized
    if isinstance(value, Sequence) and not isinstance(
        value, (bytes, bytearray, memoryview)
    ):
        return [normalize_input(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise TypeError(f"cache input is not JSON-compatible: {type(value).__name__}")


# The longer spelling is useful at the P0-11 submission boundary.
normalize_cache_input = normalize_input


def cache_key(
    task_type: str,
    version: int,
    input_data: Any,
    candidate: int = 0,
    seed: int | None = None,
    attempt: int | None = None,
    share_across_runs: bool = False,
) -> str:
    """Calculate the task cache key specified by task-model §9."""

    if not isinstance(task_type, str) or not task_type:
        raise ValueError("task_type must be a non-empty string")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("version must be a positive integer")
    if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 0:
        raise ValueError("candidate must be a non-negative integer")
    if not isinstance(share_across_runs, bool):
        raise TypeError("share_across_runs must be a boolean")

    payload: dict[str, Any] = {
        "type": task_type,
        "version": version,
        "input": normalize_input(input_data),
        "candidate": candidate,
    }
    if share_across_runs:
        if (
            attempt is None
            or isinstance(attempt, bool)
            or not isinstance(attempt, int)
            or attempt < 0
        ):
            raise ValueError(
                "attempt must be a non-negative integer when sharing across runs"
            )
        payload["attempt"] = attempt
    else:
        if seed is None or isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("seed must be an integer when sharing is disabled")
        payload["seed"] = seed

    canonical = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


compute_cache_key = cache_key


def cache_path(data_dir: str | Path, key: str) -> Path:
    """Return ``cache/<key[0:2]>/<key>.json`` for a validated key."""

    if not isinstance(key, str) or not _CACHE_KEY.fullmatch(key):
        raise ValueError("cache key must be a 64-character lowercase hex string")
    return Path(data_dir) / "cache" / key[:2] / f"{key}.json"


def lookup_cache(data_dir: str | Path, key: str) -> tuple[bool, Any]:
    """Return ``(hit, output)``; malformed or missing entries are cache misses."""

    path = cache_path(data_dir, key)
    try:
        with path.open("r", encoding="utf-8", newline=None) as stream:
            value = json.load(stream)
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return False, None
    return True, value


def read_cache(data_dir: str | Path, key: str) -> Any | None:
    """Read a cached output, returning ``None`` for a miss.

    LLM outputs are expected to be objects or text, but :func:`lookup_cache`
    is available when a caller must distinguish a cached JSON ``null`` from a
    miss.
    """

    hit, value = lookup_cache(data_dir, key)
    return value if hit else None


def save_cache(data_dir: str | Path, key: str, output: Any) -> Path:
    """Atomically save an already accepted output and return its path.

    Validation belongs to the submission layer.  This function intentionally
    performs no acceptance decision so P0-11 can call it after validation.
    """

    path = cache_path(data_dir, key)
    atomic_write_json(path, output)
    return path


write_cache = save_cache
store_cache = save_cache


def cache_exists(data_dir: str | Path, key: str) -> bool:
    """Return whether *key* contains a readable cached JSON value."""

    hit, _ = lookup_cache(data_dir, key)
    return hit
