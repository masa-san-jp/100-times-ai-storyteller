from __future__ import annotations

import errno
import json
import multiprocessing
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import storyteller.storage as storage
from storyteller.storage import LockTimeoutError, acquire_lock


class Clock:
    def __init__(self) -> None:
        self.now = 10_000_000_000
        self.sleeps: list[float] = []

    def monotonic_ns(self) -> int:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += round(seconds * 1_000_000_000)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(storage, "time", SimpleNamespace(
            monotonic_ns=self.monotonic_ns,
            monotonic=lambda: self.now / 1_000_000_000,
            time=time.time,
            sleep=self.sleep,
        ))


def waiter(lock: Path, start: int, deadline: int, token: str = "a" * 32) -> Path:
    directory = lock.with_name(lock.name + ".waiters")
    directory.mkdir(exist_ok=True)
    path = directory / f"{start}-{deadline}-{token}.wait"
    path.touch(exist_ok=False)
    return path


def _queued_worker(lock_name: str, log_name: str, label: int, ready, common_start) -> None:
    """Signal after registration closes, then record acquisition under the lock."""
    lock = Path(lock_name)
    original_open, original_close = storage.os.open, storage.os.close
    registered_fds = set()

    def open_entry(path, flags, mode=0o777):
        fd = original_open(path, flags, mode)
        if Path(path).suffix == ".wait":
            registered_fds.add(fd)
        return fd

    def close_entry(fd):
        original_close(fd)
        if fd in registered_fds:
            registered_fds.remove(fd)
            ready.set()

    storage.os.open, storage.os.close = open_entry, close_entry
    if common_start is not None:
        real_now = storage.time.monotonic_ns
        first_call = True

        def same_start():
            nonlocal first_call
            if first_call:
                first_call = False
                return common_start
            return real_now()

        storage.time.monotonic_ns = same_start
        storage.secrets.token_hex = lambda count: f"{7 - label:032x}"
    with acquire_lock(lock):
        with Path(log_name).open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"{label}\n")
        time.sleep(0.01)


@pytest.mark.parametrize("same_start", [False, True])
def test_eight_queued_processes_keep_order_before_a_newcomer(
    tmp_path: Path, same_start: bool
) -> None:
    lock, log = tmp_path / "manifest.lock", tmp_path / "order.txt"
    context = multiprocessing.get_context("spawn")
    children = []
    try:
        with acquire_lock(lock):
            common_start = time.monotonic_ns() if same_start else None
            for label in range(8):
                ready = context.Event()
                child = context.Process(
                    target=_queued_worker,
                    args=(str(lock), str(log), label, ready, common_start),
                )
                child.start()
                children.append(child)
                assert ready.wait(5), f"worker {label} did not register"
            entries = list(lock.with_name(lock.name + ".waiters").iterdir())
            assert len(entries) == 8
            assert all(entry.stat().st_size == 0 for entry in entries)
        with acquire_lock(lock):
            with log.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write("newcomer\n")
        for child in children:
            child.join(5)
            assert child.exitcode == 0
        expected = list(reversed(range(8))) if same_start else list(range(8))
        assert log.read_text(encoding="utf-8").splitlines() == [
            *(str(label) for label in expected), "newcomer"
        ]
        assert not lock.exists()
        assert list(lock.with_name(lock.name + ".waiters").iterdir()) == []
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(5)


def test_numeric_and_token_order_and_unknown_entries_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    clock.install(monkeypatch)
    lock = tmp_path / "manifest.lock"
    first = waiter(lock, 9, clock.now + 100, "b" * 32)
    tied = waiter(lock, 9, clock.now + 100, "a" * 32)
    later = waiter(lock, 10, clock.now + 100, "0" * 32)
    directory = first.parent
    unknown = ["notes", "-1-99-" + "a" * 32 + ".wait",
               "10-9-" + "b" * 32 + ".wait", "1-99-no-token.wait"]
    for name in unknown:
        (directory / name).touch()
    nonempty = waiter(lock, 1, clock.now + 100, "c" * 32)
    nonempty.write_text("unknown data", encoding="utf-8")
    named_directory = directory / f"2-{clock.now + 100}-{'d' * 32}.wait"
    named_directory.mkdir()
    assert storage._first_waiter(directory) == (9, "a" * 32)
    assert set(directory.iterdir()) == {
        first, tied, later, nonempty, named_directory, *(directory / n for n in unknown)
    }


def test_expired_and_pre_reboot_entries_do_not_block_a_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    clock.install(monkeypatch)
    lock = tmp_path / "manifest.lock"
    expired = waiter(lock, 1, clock.now)
    future = waiter(lock, clock.now + 1, clock.now + 100)
    with acquire_lock(lock):
        assert not expired.exists()
        assert not future.exists()
    assert clock.sleeps == []


def test_future_check_reads_clock_after_observing_a_new_arrival(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    clock.install(monkeypatch)
    lock = tmp_path / "manifest.lock"
    directory = lock.with_name(lock.name + ".waiters")
    directory.mkdir()
    real_scandir = storage.os.scandir
    arrival = None

    class ArrivingEntries:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def __iter__(self):
            nonlocal arrival
            # The arrival occurs only once iteration begins, after any clock
            # snapshot a scanner might have taken before observing entries.
            clock.now += 100
            arrival = waiter(lock, clock.now, clock.now + 1_000)
            with real_scandir(directory) as entries:
                yield from entries

    def arriving_scandir(path):
        return ArrivingEntries()

    monkeypatch.setattr(storage.os, "scandir", arriving_scandir)
    assert storage._first_waiter(directory) == (clock.now, "a" * 32)
    assert arrival is not None and arrival.exists()


@pytest.mark.parametrize("disappear_at", ["stat", "unlink"])
def test_concurrent_entry_removal_does_not_fail_the_follower(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, disappear_at: str
) -> None:
    clock = Clock()
    clock.install(monkeypatch)
    lock = tmp_path / "manifest.lock"
    expired = waiter(lock, 1, 2)
    original_scandir, original_unlink = storage.os.scandir, storage.os.unlink
    if disappear_at == "stat":
        class RemovedEntry:
            name = expired.name
            path = str(expired)

            def stat(self, **kwargs):
                expired.unlink()
                raise FileNotFoundError(self.path)

        class Entries:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def __iter__(self):
                return iter([RemovedEntry()])

        first = True

        def scandir(path):
            nonlocal first
            if first:
                first = False
                return Entries()
            return original_scandir(path)

        monkeypatch.setattr(storage.os, "scandir", scandir)
    else:
        def unlink(path, *args, **kwargs):
            original_unlink(path, *args, **kwargs)
            if Path(path) == expired:
                raise FileNotFoundError(str(path))
        monkeypatch.setattr(storage.os, "unlink", unlink)
    with acquire_lock(lock):
        assert lock.exists()
    assert not expired.exists()


@pytest.mark.parametrize("stage", ["registration", "scan", "body"])
def test_original_deadline_includes_registration_scan_and_body_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    clock = Clock()
    clock.install(monkeypatch)
    lock = tmp_path / "manifest.lock"
    original_open, original_scan = storage.os.open, storage._first_waiter
    body_attempts = []

    def open_file(path, flags, mode=0o777):
        descriptor = original_open(path, flags, mode)
        if Path(path) == lock:
            body_attempts.append(path)
            if stage == "body":
                clock.now += 10_000_000_001
        elif stage == "registration":
            clock.now += 10_000_000_001
        return descriptor

    def scan(path, **kwargs):
        result = original_scan(path, **kwargs)
        if stage == "scan":
            clock.now += 10_000_000_001
        return result

    monkeypatch.setattr(storage.os, "open", open_file)
    monkeypatch.setattr(storage, "_first_waiter", scan)
    with pytest.raises(LockTimeoutError):
        with acquire_lock(lock):
            pytest.fail("request exceeded its original deadline")
    assert len(body_attempts) == (1 if stage == "body" else 0)
    assert not lock.exists()
    assert list(lock.with_name(lock.name + ".waiters").iterdir()) == []
    assert clock.sleeps == []


@pytest.mark.parametrize("predecessor_expires", [False, True])
def test_waiting_uses_original_deadline_and_expired_predecessor_stops_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, predecessor_expires: bool
) -> None:
    clock = Clock()
    clock.install(monkeypatch)
    lock = tmp_path / "manifest.lock"
    predecessor = waiter(lock, clock.now - 1,
                         clock.now + (200_000_000 if predecessor_expires else 20_000_000_000))
    if predecessor_expires:
        with acquire_lock(lock):
            assert not predecessor.exists()
        assert clock.now == 10_200_000_000
    else:
        with pytest.raises(LockTimeoutError):
            with acquire_lock(lock):
                pytest.fail("a later request bypassed a live predecessor")
        assert clock.now == 20_000_000_000
        assert list(predecessor.parent.iterdir()) == [predecessor]
    assert all(0 < delay <= 0.1 for delay in clock.sleeps)


@pytest.mark.parametrize("state", ["empty", "live", "expired", "future", "body"])
def test_timeout_zero_never_registers_or_waits_and_attempts_body_at_most_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    clock = Clock()
    clock.install(monkeypatch)
    lock = tmp_path / "manifest.lock"
    prior = None
    if state == "live":
        prior = waiter(lock, 1, clock.now + 1)
    elif state == "expired":
        prior = waiter(lock, 1, clock.now)
    elif state == "future":
        prior = waiter(lock, clock.now + 1, clock.now + 100)
    elif state == "body":
        lock.write_text('{"token":"other"}', encoding="utf-8")
    original_open = storage.os.open
    attempted = []

    def open_file(path, flags, mode=0o777):
        attempted.append(Path(path))
        return original_open(path, flags, mode)

    monkeypatch.setattr(storage.os, "open", open_file)
    if state in {"live", "body"}:
        with pytest.raises(LockTimeoutError):
            with acquire_lock(lock, timeout=0):
                pytest.fail("an occupied lock was acquired")
    else:
        with acquire_lock(lock, timeout=0):
            assert lock.exists()
    assert attempted == ([] if state == "live" else [lock])
    assert clock.sleeps == []
    if prior is not None:
        assert prior.exists() == (state == "live")
    if state == "body":
        assert json.loads(lock.read_text(encoding="utf-8"))["token"] == "other"


@pytest.mark.parametrize("failure", ["sleep", "scan"])
def test_exception_while_waiting_cleans_only_own_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    clock = Clock()
    clock.install(monkeypatch)
    lock = tmp_path / "manifest.lock"
    prior = waiter(lock, 1, clock.now + 30_000_000_000)
    lock.write_text('{"token":"other"}', encoding="utf-8")

    def fail(*args, **kwargs):
        raise RuntimeError("injected interruption")

    monkeypatch.setattr(storage.time if failure == "sleep" else storage,
                        "sleep" if failure == "sleep" else "_first_waiter", fail)
    with pytest.raises(RuntimeError, match="injected interruption"):
        with acquire_lock(lock):
            pytest.fail("interrupted request returned a lock")
    assert list(prior.parent.iterdir()) == [prior]
    assert json.loads(lock.read_text(encoding="utf-8"))["token"] == "other"


@pytest.mark.parametrize("foreign_owner", [False, True])
def test_entry_cleanup_failure_releases_only_verified_body_before_notifying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, foreign_owner: bool
) -> None:
    lock = tmp_path / "manifest.lock"
    original_unlink = Path.unlink
    denied = PermissionError(errno.EACCES, "entry cleanup denied")

    def unlink(path, *args, **kwargs):
        if path.suffix == ".wait":
            if foreign_owner:
                lock.write_text('{"token":"new-owner"}', encoding="utf-8")
            raise denied
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(PermissionError) as caught:
        with acquire_lock(lock):
            pytest.fail("cleanup failure must not return an acquired lock")
    assert caught.value is denied
    assert lock.exists() == foreign_owner
    if foreign_owner:
        assert json.loads(lock.read_text(encoding="utf-8"))["token"] == "new-owner"
    assert len(list(lock.with_name(lock.name + ".waiters").iterdir())) == 1


def test_entry_cleanup_and_owner_read_denial_notify_then_allow_stale_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    clock.install(monkeypatch)
    lock = tmp_path / "manifest.lock"
    original_unlink, original_read = Path.unlink, Path.read_text
    denied = PermissionError(errno.EACCES, "owner read denied")

    def unlink(path, *args, **kwargs):
        if path.suffix == ".wait":
            raise PermissionError(errno.EACCES, "entry cleanup denied")
        return original_unlink(path, *args, **kwargs)

    def read(path, *args, **kwargs):
        if path == lock:
            raise denied
        return original_read(path, *args, **kwargs)

    with monkeypatch.context() as errors:
        errors.setattr(storage.sys, "platform", "win32")
        errors.setattr(Path, "unlink", unlink)
        errors.setattr(Path, "read_text", read)
        with pytest.raises(PermissionError) as caught:
            with acquire_lock(lock):
                pytest.fail("unverified body must never be returned or deleted")
        assert caught.value is denied
        assert lock.exists()
        assert clock.now == 11_000_000_000
    clock.now = 21_000_000_000  # The failed request's original queue entry has expired.
    os.utime(lock, (time.time() - 31, time.time() - 31))
    with acquire_lock(lock):
        assert json.loads(lock.read_text(encoding="utf-8"))["token"]
    assert not lock.exists()
    assert list(lock.with_name(lock.name + ".waiters").iterdir()) == []


def test_registration_collision_never_deletes_another_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    clock.install(monkeypatch)
    lock = tmp_path / "manifest.lock"
    existing = waiter(lock, clock.now, clock.now + 10_000_000_000)
    monkeypatch.setattr(storage.secrets, "token_hex", lambda count: "a" * 32)
    with pytest.raises(FileExistsError):
        with acquire_lock(lock):
            pytest.fail("registration collided with another token entry")
    assert existing.exists()
    assert not lock.exists()


def _crash_after_registration(lock_name: str) -> None:
    original_open, original_close = storage.os.open, storage.os.close
    entries = set()

    def open_entry(path, flags, mode=0o777):
        fd = original_open(path, flags, mode)
        if Path(path).suffix == ".wait":
            entries.add(fd)
        return fd

    def close_entry(fd):
        original_close(fd)
        if fd in entries:
            os._exit(0)

    storage.os.open, storage.os.close = open_entry, close_entry
    with acquire_lock(lock_name, timeout=0.3):
        raise AssertionError("crash injection did not stop the request")


def test_crashed_process_entry_expires_without_body_lock_or_manual_cleanup(tmp_path: Path) -> None:
    lock = tmp_path / "manifest.lock"
    child = multiprocessing.get_context("spawn").Process(
        target=_crash_after_registration, args=(str(lock),)
    )
    child.start()
    child.join(5)
    try:
        assert child.exitcode == 0
        entries = list(lock.with_name(lock.name + ".waiters").iterdir())
        assert len(entries) == 1 and entries[0].stat().st_size == 0
        assert not lock.exists()
        with acquire_lock(lock):
            assert not entries[0].exists()
        assert list(entries[0].parent.iterdir()) == []
    finally:
        if child.is_alive():
            child.terminate()
            child.join(5)


@pytest.mark.parametrize("pre_reboot", [False, True])
def test_denied_obsolete_entry_cleanup_warns_once_per_request_and_never_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], pre_reboot: bool,
) -> None:
    clock = Clock()
    clock.install(monkeypatch)
    lock = tmp_path / "manifest.lock"
    obsolete = waiter(lock, clock.now + 100 if pre_reboot else 1,
                      clock.now + 200 if pre_reboot else 2)
    original_unlink = storage.os.unlink
    original_open = storage.os.open
    attempts = 0

    def denied_cleanup(path, *args, **kwargs):
        if Path(path) == obsolete:
            raise PermissionError(errno.EACCES, "obsolete cleanup denied", str(path))
        return original_unlink(path, *args, **kwargs)

    def collision_twice(path, flags, mode=0o777):
        nonlocal attempts
        if Path(path) == lock:
            attempts += 1
            if attempts % 3:
                raise FileExistsError(errno.EEXIST, "body still held", str(path))
        return original_open(path, flags, mode)

    monkeypatch.setattr(storage.os, "unlink", denied_cleanup)
    monkeypatch.setattr(storage.os, "open", collision_twice)
    # The same request observes the denied file on all three polls, but warns
    # once. A separate request must provide its own warning as well.
    for _ in range(2):
        with acquire_lock(lock):
            assert lock.exists()
        output = capsys.readouterr()
        assert output.out == ""
        assert output.err.count("警告:") == 1
        assert str(obsolete) in output.err and "obsolete cleanup denied" in output.err
    assert obsolete.exists()
    assert attempts == 6


@pytest.mark.parametrize("failure", ["scan", "own_cleanup"])
def test_queue_permission_failures_remain_errors_without_optional_cleanup_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], failure: str,
) -> None:
    clock = Clock()
    clock.install(monkeypatch)
    lock = tmp_path / "manifest.lock"
    denied = PermissionError(errno.EACCES, f"{failure} denied")
    original_unlink = Path.unlink

    if failure == "scan":
        def scan(path):
            raise denied
        monkeypatch.setattr(storage.os, "scandir", scan)
    else:
        # Make our own entry expire during registration. It must be removed in
        # request cleanup, never swallowed as another request's optional entry.
        original_close = storage.os.close

        def close(fd):
            original_close(fd)
            clock.now += 10_000_000_001

        def unlink(path, *args, **kwargs):
            if path.suffix == ".wait":
                raise denied
            return original_unlink(path, *args, **kwargs)

        monkeypatch.setattr(storage.os, "close", close)
        monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(PermissionError) as caught:
        with acquire_lock(lock):
            pytest.fail("mandatory queue operation was denied")
    assert caught.value is denied
    assert not lock.exists()
    assert capsys.readouterr().err == ""


def test_timeout_zero_windows_body_denial_keeps_original_error_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = Clock()
    clock.install(monkeypatch)
    lock = tmp_path / "manifest.lock"
    denied = PermissionError(errno.EACCES, "delete pending", str(lock))
    attempted = []

    def open_file(path, flags, mode=0o777):
        attempted.append(Path(path))
        assert flags & os.O_EXCL and flags & os.O_CREAT
        raise denied

    monkeypatch.setattr(storage, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(storage.os, "open", open_file)
    with pytest.raises(PermissionError) as caught:
        with acquire_lock(lock, timeout=0):
            pytest.fail("zero timeout must not retry an access denial")
    assert caught.value is denied
    assert attempted == [lock] and clock.sleeps == []
    assert not lock.with_name(lock.name + ".waiters").exists()


def test_optional_cleanup_does_not_suppress_other_io_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    clock = Clock()
    clock.install(monkeypatch)
    lock = tmp_path / "manifest.lock"
    obsolete = waiter(lock, 1, 2)
    failure = OSError(errno.EIO, "injected disk failure", str(obsolete))
    original_unlink = storage.os.unlink

    def fail(path, *args, **kwargs):
        if Path(path) == obsolete:
            raise failure
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(storage.os, "unlink", fail)
    with pytest.raises(OSError) as caught:
        with acquire_lock(lock):
            pytest.fail("unrelated I/O failure must remain visible")
    assert caught.value is failure
    assert not lock.exists()
    assert list(obsolete.parent.iterdir()) == [obsolete]
    assert capsys.readouterr().err == ""
