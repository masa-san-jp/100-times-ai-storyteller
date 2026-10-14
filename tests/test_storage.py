from __future__ import annotations

import errno
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from storyteller.storage import (
    DataDirectoryError,
    LockTimeoutError,
    acquire_lock,
    atomic_write_json,
    atomic_write_text,
    batch_lock_path,
    manifest_lock_path,
    prepare_atomic_write_bytes,
    resolve_data_dir,
    sync_folder_marker,
    tables_lock_path,
    warn_if_sync_folder,
)


def _workspace_config(path: Path, data_dir: Path) -> None:
    path.write_text(
        "\n".join(
            [
                f"data_dir: {data_dir}",
                "executor_id: test-executor",
                "agent: generic",
                "isolation: placement",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_resolve_data_dir_prefers_workspace_config(tmp_path: Path) -> None:
    configured = tmp_path / "configured"
    _workspace_config(tmp_path / ".storyteller-workspace.yaml", configured)

    resolved = resolve_data_dir(
        cwd=tmp_path,
        environ={"STORYTELLER_HOME": str(tmp_path / "environment")},
        package_file=tmp_path / "not" / "a" / "package.py",
    )

    assert resolved == configured.resolve()


def test_resolve_data_dir_uses_environment_then_editable_private(tmp_path: Path) -> None:
    environment_dir = tmp_path / "environment"
    assert (
        resolve_data_dir(
            cwd=tmp_path,
            environ={"STORYTELLER_HOME": str(environment_dir)},
            package_file=tmp_path / "not" / "a" / "package.py",
        )
        == environment_dir.resolve()
    )

    repository = tmp_path / "repo"
    package_file = repository / "src" / "storyteller" / "storage.py"
    (repository / "src" / "storyteller").mkdir(parents=True)
    (repository / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    assert (
        resolve_data_dir(
            cwd=tmp_path,
            environ={},
            package_file=package_file,
        )
        == (repository / "private").resolve()
    )


def test_resolve_data_dir_rejects_invalid_or_unavailable_configuration(
    tmp_path: Path,
) -> None:
    config = tmp_path / ".storyteller-workspace.yaml"
    config.write_text("data_dir: relative\n", encoding="utf-8")
    with pytest.raises(DataDirectoryError):
        resolve_data_dir(cwd=tmp_path, environ={}, package_file=tmp_path / "x.py")

    config.unlink()
    with pytest.raises(DataDirectoryError):
        resolve_data_dir(cwd=tmp_path, environ={}, package_file=tmp_path / "x.py")


def test_sync_folder_warning_is_case_insensitive_and_emitted_to_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sync_dir = tmp_path / "My Drive" / "storyteller"
    assert sync_folder_marker(sync_dir) == "My Drive"
    assert warn_if_sync_folder(sync_dir)
    assert "警告" in capsys.readouterr().err
    assert not warn_if_sync_folder(tmp_path / "local")


def test_atomic_text_write_normalises_lf_and_preserves_original_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "nested" / "state.txt"
    atomic_write_text(destination, "あ\r\nb\rc\n")
    assert destination.read_bytes() == "あ\nb\nc\n".encode("utf-8")

    destination.write_text("old", encoding="utf-8")

    def fail_replace(*args: object) -> None:
        raise OSError("injected failure")

    monkeypatch.setattr("storyteller.storage.os.replace", fail_replace)
    with pytest.raises(OSError, match="injected failure"):
        atomic_write_text(destination, "new")
    assert destination.read_text(encoding="utf-8") == "old"
    assert list(destination.parent.glob(f".{destination.name}.*.tmp")) == []


def test_atomic_write_retries_permission_error_from_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "state.txt"
    original_replace = os.replace
    failures = 2
    sleeps: list[float] = []

    def replace_with_transient_lock(source: object, target: object) -> None:
        nonlocal failures
        if failures:
            failures -= 1
            raise PermissionError("destination is temporarily open")
        original_replace(source, target)

    monkeypatch.setattr("storyteller.storage.os.replace", replace_with_transient_lock)
    monkeypatch.setattr("storyteller.storage.time.sleep", sleeps.append)

    atomic_write_text(destination, "new")

    assert destination.read_text(encoding="utf-8") == "new"
    assert sleeps == [0.05, 0.05]


def test_atomic_write_removes_temp_file_after_replace_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "state.txt"
    destination.write_text("old", encoding="utf-8")
    clock = iter((0.0, 0.5, 1.0))

    def always_fail(*args: object) -> None:
        raise PermissionError("destination is open")

    class FakeTime:
        @staticmethod
        def monotonic() -> float:
            return next(clock)

        @staticmethod
        def sleep(_: float) -> None:
            return None

    monkeypatch.setattr("storyteller.storage.os.replace", always_fail)
    monkeypatch.setattr("storyteller.storage.time", FakeTime)

    with pytest.raises(PermissionError, match="destination is open"):
        atomic_write_text(destination, "new")

    assert destination.read_text(encoding="utf-8") == "old"
    assert list(destination.parent.glob(f".{destination.name}.*.tmp")) == []


def test_atomic_json_write_is_utf8_json_with_lf(tmp_path: Path) -> None:
    destination = tmp_path / "state.json"
    atomic_write_json(destination, {"text": "日本語", "items": [1, 2]})
    assert json.loads(destination.read_text(encoding="utf-8")) == {
        "text": "日本語",
        "items": [1, 2],
    }
    assert b"\r" not in destination.read_bytes()


def test_prepared_write_fsyncs_before_publish_and_discards_unpublished_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "state.txt"
    destination.write_bytes(b"old")
    original_fsync = os.fsync
    synced = []

    def check_fsync(descriptor: int) -> None:
        assert destination.read_bytes() == b"old"
        original_fsync(descriptor)
        synced.append(descriptor)

    monkeypatch.setattr("storyteller.storage.os.fsync", check_fsync)
    with prepare_atomic_write_bytes(destination, b"discard"):
        assert len(synced) == 1
        assert destination.read_bytes() == b"old"
    assert list(tmp_path.glob(".*.tmp")) == []
    with prepare_atomic_write_bytes(destination, b"new") as publish:
        assert len(synced) == 2
        assert destination.read_bytes() == b"old"
        publish()
        assert destination.read_bytes() == b"new"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_lock_timeout_and_release(tmp_path: Path) -> None:
    lock_path = tmp_path / "manifest.lock"
    with acquire_lock(lock_path):
        assert lock_path.is_file()
        with pytest.raises(LockTimeoutError):
            with acquire_lock(lock_path, timeout=0.02, retry_interval=0.005):
                pass
    assert not lock_path.exists()


def test_windows_lock_open_retries_access_denied_without_bypassing_o_excl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "manifest.lock"
    original_open = os.open
    attempts = 0
    sleeps = []

    def pending_delete(path, flags, mode):
        nonlocal attempts
        assert Path(path) == lock_path
        assert flags & os.O_EXCL and flags & os.O_CREAT
        attempts += 1
        if attempts <= 2:
            raise PermissionError(errno.EACCES, "pending deletion", str(path))
        return original_open(path, flags, mode)

    monkeypatch.setattr("storyteller.storage.sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr("storyteller.storage.os.open", pending_delete)
    monkeypatch.setattr("storyteller.storage.time.sleep", sleeps.append)
    with acquire_lock(lock_path):
        assert attempts == 3
        assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid()
    assert sleeps == [0.1, 0.1]
    assert not lock_path.exists()


@pytest.mark.parametrize("platform,number", [
    ("linux", errno.EACCES), ("darwin", errno.EACCES), ("win32", errno.EPERM),
])
def test_lock_open_does_not_retry_other_permission_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str, number: int,
) -> None:
    error = PermissionError(number, "permanent denial")
    sleeps = []

    def deny(*args):
        raise error

    monkeypatch.setattr("storyteller.storage.sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr("storyteller.storage.os.open", deny)
    monkeypatch.setattr("storyteller.storage.time.sleep", sleeps.append)
    with pytest.raises(PermissionError) as caught:
        with acquire_lock(tmp_path / "manifest.lock"):
            pytest.fail("lock was not acquired")
    assert caught.value is error
    assert sleeps == []


@pytest.mark.parametrize("existing_lock", [False, True])
def test_windows_lock_open_denial_is_bounded_and_does_not_remove_an_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing_lock: bool,
) -> None:
    lock_path = tmp_path / "manifest.lock"
    if existing_lock:
        lock_path.write_text("another owner's lock", encoding="utf-8")
    original_open = os.open
    error = PermissionError(errno.EACCES, "access denied", str(lock_path))
    attempts = 0
    sleeps = []
    clock = SimpleNamespace(now=0.0)

    def deny(path, flags, mode):
        nonlocal attempts
        attempts += 1
        if existing_lock and attempts > 1:
            return original_open(path, flags, mode)
        raise error

    def sleep(seconds):
        sleeps.append(seconds)
        clock.now += seconds

    monkeypatch.setattr("storyteller.storage.sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr("storyteller.storage.os.open", deny)
    monkeypatch.setattr("storyteller.storage.time", SimpleNamespace(
        monotonic=lambda: clock.now, sleep=sleep, time=time.time,
    ))
    expected = LockTimeoutError if existing_lock else PermissionError
    with pytest.raises(expected) as caught:
        with acquire_lock(lock_path, timeout=0.2):
            pytest.fail("lock was not acquired")
    assert attempts == 3 and sleeps == [0.1, 0.1]
    if existing_lock:
        assert lock_path.read_text(encoding="utf-8") == "another owner's lock"
    else:
        assert caught.value is error
        assert not lock_path.exists()


def test_stale_lock_is_removed_and_reclaimed(tmp_path: Path) -> None:
    lock_path = tmp_path / "tables.lock"
    lock_path.write_text("stale", encoding="utf-8")
    old = time.time() - 31
    os.utime(lock_path, (old, old))

    with acquire_lock(lock_path, timeout=0.1):
        assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid()

    assert not lock_path.exists()


def test_lock_release_does_not_remove_a_reclaimed_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "manifest.lock"
    with acquire_lock(lock_path):
        replacement = {
            "pid": 999,
            "hostname": "other-host",
            "created_at": "2026-09-27T00:00:00Z",
            "token": "other-process-token",
        }
        lock_path.write_text(json.dumps(replacement), encoding="utf-8")

    assert json.loads(lock_path.read_text(encoding="utf-8"))["token"] == (
        "other-process-token"
    )


@pytest.mark.parametrize("reclaimed", [False, True])
def test_windows_lock_release_retries_read_denial_and_rechecks_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reclaimed: bool,
) -> None:
    lock_path = tmp_path / "manifest.lock"
    original_read = Path.read_text
    reads = 0
    sleeps = []

    def transient_denial(path, *args, **kwargs):
        nonlocal reads
        if path == lock_path:
            reads += 1
            if reads <= 2:
                if reclaimed:
                    lock_path.write_text('{"token":"another-owner"}', encoding="utf-8")
                raise PermissionError(errno.EACCES, "transient read denial", str(path))
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr("storyteller.storage.sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr("storyteller.storage.time.sleep", sleeps.append)
    with acquire_lock(lock_path):
        monkeypatch.setattr(Path, "read_text", transient_denial)
    assert reads == 3 and sleeps == [0.1, 0.1]
    if reclaimed:
        assert json.loads(original_read(lock_path, encoding="utf-8"))["token"] == "another-owner"
    else:
        assert not lock_path.exists()
        with acquire_lock(lock_path):
            pass


@pytest.mark.parametrize("platform,number", [
    ("win32", errno.EACCES), ("linux", errno.EACCES),
    ("darwin", errno.EACCES), ("win32", errno.EPERM),
])
def test_lock_release_reports_permanent_read_denial_without_deleting_unknown_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str, number: int,
) -> None:
    lock_path = tmp_path / "manifest.lock"
    error = PermissionError(number, "permanent read denial", str(lock_path))
    clock = SimpleNamespace(now=0.0)
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock.now += seconds

    def deny(*args, **kwargs):
        raise error

    monkeypatch.setattr("storyteller.storage.sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr("storyteller.storage.time", SimpleNamespace(
        monotonic=lambda: clock.now, sleep=sleep, time=time.time,
    ))
    with pytest.raises(PermissionError) as caught:
        with acquire_lock(lock_path):
            monkeypatch.setattr(Path, "read_text", deny)
    assert caught.value is error and lock_path.exists()
    if platform == "win32" and number == errno.EACCES:
        assert clock.now == pytest.approx(1.0)
        assert all(0 < delay <= 0.1 for delay in sleeps)
    else:
        assert sleeps == []


def test_lock_paths_follow_data_layout(tmp_path: Path) -> None:
    assert manifest_lock_path(tmp_path / "run") == tmp_path / "run" / "manifest.lock"
    assert batch_lock_path(tmp_path / "batch") == tmp_path / "batch" / "batch.lock"
    assert tables_lock_path(tmp_path) == tmp_path / "tables.lock"
