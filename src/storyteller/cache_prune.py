"""``st cache prune``: delete old cache entries that no live run refers to.

Specified by data-layout §6.  An entry is deleted only when it was created
more than DAYS days ago and no ``active`` / ``stalled`` run (including the
run-shaped manifest of a batch report) records its key in a task's
``cache_key``.
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_KEY_FILE = re.compile(r"^[0-9a-f]{64}\.json$")
_LIVE_STATUSES = frozenset({"active", "stalled"})
_SECONDS_PER_DAY = 86400


class CachePruneError(RuntimeError):
    """A manifest could not be read, so references cannot be determined."""


@dataclass
class PruneResult:
    deleted: list[Path] = field(default_factory=list)
    kept_referenced: int = 0
    kept_recent: int = 0


def _manifest_paths(data_dir: Path) -> list[Path]:
    paths = sorted((data_dir / "runs").glob("*/manifest.json"))
    paths += sorted((data_dir / "batches").glob("*/report/manifest.json"))
    return paths


def referenced_keys(data_dir: Path) -> set[str]:
    """Return cache keys recorded by every ``active`` / ``stalled`` run.

    An unreadable manifest aborts the prune: its status is unknown, so
    nothing may be assumed unreferenced.
    """

    keys: set[str] = set()
    for path in _manifest_paths(data_dir):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if manifest["status"] not in _LIVE_STATUSES:
                continue
            tasks: dict[str, Any] = manifest["tasks"]
            for task in tasks.values():
                key = task.get("cache_key")
                if isinstance(key, str):
                    keys.add(key)
        except (OSError, UnicodeError, ValueError, KeyError, TypeError,
                AttributeError) as error:
            raise CachePruneError(f"manifest を読めません: {path}") from error
    return keys


def prune_cache(
    data_dir: str | Path, older_than_days: int, *, now: float | None = None
) -> PruneResult:
    """Delete unreferenced cache files older than *older_than_days*."""

    if isinstance(older_than_days, bool) or older_than_days < 0:
        raise ValueError("DAYS は 0 以上の整数で指定してください")
    root = Path(data_dir)
    cache_dir = root / "cache"
    result = PruneResult()
    if not cache_dir.is_dir():
        return result
    threshold = (time.time() if now is None else now) - older_than_days * _SECONDS_PER_DAY
    candidates: list[Path] = []
    for path in sorted(cache_dir.glob("??/*.json")):
        if not _KEY_FILE.fullmatch(path.name) or path.parent.name != path.name[:2]:
            continue
        try:
            if not path.is_file() or path.is_symlink():
                continue
            if path.stat().st_mtime >= threshold:
                result.kept_recent += 1
                continue
        except OSError:
            continue
        candidates.append(path)
    if not candidates:
        return result
    # Read references after choosing candidates, as late as possible.
    referenced = referenced_keys(root)
    for path in candidates:
        if path.stem in referenced:
            result.kept_referenced += 1
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        result.deleted.append(path)
        try:
            path.parent.rmdir()  # only succeeds when the directory is empty
        except OSError:
            pass
    return result


def _run_prune_command(args: Any) -> int:
    from .storage import resolve_data_dir

    try:
        result = prune_cache(resolve_data_dir(), args.older_than)
    except CachePruneError as error:
        print(f"st: error: {error}", file=sys.stderr)
        return 10
    print(
        f"deleted {len(result.deleted)} cache entries "
        f"(kept: {result.kept_referenced} referenced, {result.kept_recent} recent)"
    )
    return 0


def register_cli_commands(subparsers: Any) -> None:
    """Register ``st cache prune`` without putting its logic in cli.py."""

    cache_parser = subparsers.add_parser("cache", help="manage the task output cache")
    cache_subparsers = cache_parser.add_subparsers(dest="cache_command", title="commands")
    prune_parser = cache_subparsers.add_parser(
        "prune", help="delete old, unreferenced cache entries"
    )
    prune_parser.add_argument("--older-than", type=int, required=True, metavar="DAYS")
    prune_parser.set_defaults(command_handler=_run_prune_command)
