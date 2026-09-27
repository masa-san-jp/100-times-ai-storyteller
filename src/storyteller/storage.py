"""Data-directory resolution and durable file operations.

The orchestration state is shared by separate invocations of ``st``.  This
module keeps the small set of filesystem rules that all of those invocations
need in one place.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import sys
import time
from collections.abc import Mapping, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

import yaml


SYNC_FOLDER_MARKERS = (
    "Google Drive",
    "GoogleDrive",
    "My Drive",
    "マイドライブ",
    "Dropbox",
    "iCloud Drive",
    "Mobile Documents",
    "OneDrive",
)

DEFAULT_LOCK_TIMEOUT = 10.0
DEFAULT_LOCK_RETRY_INTERVAL = 0.1
DEFAULT_STALE_LOCK_AGE = 30.0


class DataDirectoryError(ValueError):
    """Raised when the data-directory configuration cannot be used."""


class LockTimeoutError(TimeoutError):
    """Raised when a lock cannot be acquired before its timeout."""

    exit_code = 10


def _as_path(value: os.PathLike[str] | str) -> Path:
    return Path(value)


def _absolute(path: Path) -> Path:
    """Return an absolute path without requiring it to exist."""
    return path.expanduser().resolve(strict=False)


def _load_workspace_data_dir(config_path: Path) -> Path:
    try:
        with config_path.open("r", encoding="utf-8", newline=None) as stream:
            config = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as error:
        raise DataDirectoryError(
            f"invalid workspace configuration: {config_path}"
        ) from error

    if not isinstance(config, Mapping):
        raise DataDirectoryError(
            f"workspace configuration must be a mapping: {config_path}"
        )

    data_dir = config.get("data_dir")
    if not isinstance(data_dir, str) or not data_dir:
        raise DataDirectoryError(
            f"workspace configuration requires data_dir: {config_path}"
        )

    configured_path = Path(data_dir)
    if not configured_path.is_absolute():
        raise DataDirectoryError(
            f"workspace data_dir must be absolute: {config_path}"
        )
    return _absolute(configured_path)


def _editable_repository(package_file: Path) -> Path | None:
    """Find the repository for a source checkout installed in editable mode."""
    package_dir = package_file.resolve(strict=False).parent
    repository = package_dir.parent.parent
    if (repository / "pyproject.toml").is_file():
        return repository
    return None


def sync_folder_marker(path: os.PathLike[str] | str) -> str | None:
    """Return the matching sync-folder marker in *path*, if any."""
    absolute_path = _absolute(_as_path(path))
    folded_parts = tuple(part.casefold() for part in absolute_path.parts)
    for marker in SYNC_FOLDER_MARKERS:
        folded_marker = marker.casefold()
        if any(folded_marker in part for part in folded_parts):
            return marker
    return None


def warn_if_sync_folder(
    path: os.PathLike[str] | str,
    *,
    stream: TextIO | None = None,
) -> bool:
    """Warn once for this invocation when *path* is under a sync folder.

    The caller invokes this once while starting a command.  The function does
    not keep process-global state so separate ``st`` invocations each receive
    their own warning.
    """
    marker = sync_folder_marker(path)
    if marker is None:
        return False

    if stream is None:
        stream = sys.stderr
    absolute_path = _absolute(_as_path(path))
    print(
        f"警告: データディレクトリ {absolute_path} は同期フォルダ "
        f"({marker}) の配下です。排他制御が機能しない可能性があります。",
        file=stream,
    )
    return True


def resolve_data_dir(
    *,
    cwd: os.PathLike[str] | str | None = None,
    environ: Mapping[str, str] | None = None,
    package_file: os.PathLike[str] | str | None = None,
    warning_stream: TextIO | None = None,
) -> Path:
    """Resolve the data directory using the documented precedence.

    The workspace file in the current directory wins over
    ``STORYTELLER_HOME``.  When neither is present, ``private/`` is accepted
    only when this package is running from a source checkout that has a
    ``pyproject.toml`` two levels above the package directory.
    """
    current_dir = _absolute(_as_path(cwd or Path.cwd()))
    config_path = current_dir / ".storyteller-workspace.yaml"
    environment = os.environ if environ is None else environ

    if config_path.exists():
        if not config_path.is_file():
            raise DataDirectoryError(
                f"workspace configuration is not a file: {config_path}"
            )
        data_dir = _load_workspace_data_dir(config_path)
    elif "STORYTELLER_HOME" in environment:
        configured_home = environment["STORYTELLER_HOME"]
        if not configured_home:
            raise DataDirectoryError("STORYTELLER_HOME must not be empty")
        data_dir = _absolute(Path(configured_home))
    else:
        source_file = _as_path(package_file or __file__)
        repository = _editable_repository(source_file)
        if repository is None:
            raise DataDirectoryError(
                "data directory is not configured: set STORYTELLER_HOME "
                "or create .storyteller-workspace.yaml"
            )
        data_dir = repository / "private"

    data_dir = _absolute(data_dir)
    warn_if_sync_folder(data_dir, stream=warning_stream)
    return data_dir


def _normalise_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _open_temp_file(path: Path) -> tuple[int, Path]:
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(100):
        temporary_path = path.parent / (
            f".{path.name}.{secrets.token_hex(4)}.tmp"
        )
        try:
            descriptor = os.open(
                temporary_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o666,
            )
        except FileExistsError:
            continue
        return descriptor, temporary_path
    raise OSError(f"could not create a temporary file beside {path}")


def atomic_write_bytes(path: os.PathLike[str] | str, data: bytes) -> None:
    """Write bytes through a same-directory, fsynced temporary file."""
    destination = _as_path(path)
    descriptor, temporary_path = _open_temp_file(destination)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        replace_deadline = time.monotonic() + 1.0
        while True:
            try:
                os.replace(temporary_path, destination)
            except PermissionError:
                remaining = replace_deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(0.05, remaining))
            else:
                break
    except BaseException:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


def atomic_write_text(
    path: os.PathLike[str] | str,
    text: str,
    *,
    encoding: str = "utf-8",
) -> None:
    """Write UTF-8 text with LF line endings without damaging old content."""
    if encoding.lower().replace("-", "") != "utf8":
        raise ValueError("atomic text writes must use UTF-8")
    atomic_write_bytes(_as_path(path), _normalise_lf(text).encode("utf-8"))


def atomic_write_json(
    path: os.PathLike[str] | str,
    value: Any,
    *,
    indent: int = 2,
) -> None:
    """Serialise JSON as UTF-8/LF and replace the destination atomically."""
    text = json.dumps(value, ensure_ascii=False, indent=indent) + "\n"
    atomic_write_text(path, text)


def _lock_contents(token: str) -> bytes:
    details = {
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "created_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "token": token,
    }
    return (json.dumps(details, ensure_ascii=False) + "\n").encode("utf-8")


def _lock_is_stale(path: Path, stale_after: float, now: float) -> bool:
    try:
        age = now - path.stat().st_mtime
    except FileNotFoundError:
        return False
    return age > stale_after


def _remove_lock_if_owned(path: Path, token: str) -> None:
    try:
        contents = path.read_text(encoding="utf-8")
        details = json.loads(contents)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return
    if not isinstance(details, dict) or details.get("token") != token:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


@contextmanager
def acquire_lock(
    path: os.PathLike[str] | str,
    *,
    timeout: float = DEFAULT_LOCK_TIMEOUT,
    retry_interval: float = DEFAULT_LOCK_RETRY_INTERVAL,
    stale_after: float = DEFAULT_STALE_LOCK_AGE,
) -> Iterator[Path]:
    """Acquire an exclusive lock file and remove it on leaving the context."""
    if timeout < 0:
        raise ValueError("timeout must be non-negative")
    if retry_interval < 0:
        raise ValueError("retry_interval must be non-negative")
    if stale_after < 0:
        raise ValueError("stale_after must be non-negative")

    lock_path = _as_path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    token = secrets.token_hex(16)
    acquired = False

    while True:
        try:
            descriptor = os.open(
                lock_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o666,
            )
        except FileExistsError:
            if _lock_is_stale(lock_path, stale_after, time.time()):
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise LockTimeoutError(f"timed out acquiring lock: {lock_path}")
            remaining = max(0.0, deadline - time.monotonic())
            time.sleep(min(retry_interval, remaining))
            continue
        except OSError:
            raise
        else:
            try:
                with os.fdopen(descriptor, "wb", closefd=True) as stream:
                    stream.write(_lock_contents(token))
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                _remove_lock_if_owned(lock_path, token)
                raise
            acquired = True
            break

    try:
        yield lock_path
    finally:
        if acquired:
            _remove_lock_if_owned(lock_path, token)


def manifest_lock_path(run_dir: os.PathLike[str] | str) -> Path:
    return _as_path(run_dir) / "manifest.lock"


def batch_lock_path(batch_dir: os.PathLike[str] | str) -> Path:
    return _as_path(batch_dir) / "batch.lock"


def tables_lock_path(data_dir: os.PathLike[str] | str) -> Path:
    return _as_path(data_dir) / "tables.lock"


def manifest_lock(run_dir: os.PathLike[str] | str, **kwargs: Any) -> Iterator[Path]:
    return acquire_lock(manifest_lock_path(run_dir), **kwargs)


def batch_lock(batch_dir: os.PathLike[str] | str, **kwargs: Any) -> Iterator[Path]:
    return acquire_lock(batch_lock_path(batch_dir), **kwargs)


def tables_lock(data_dir: os.PathLike[str] | str, **kwargs: Any) -> Iterator[Path]:
    return acquire_lock(tables_lock_path(data_dir), **kwargs)
