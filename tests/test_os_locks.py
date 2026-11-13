"""Locks are never taken from a live holder, however long it is stopped
(data-layout §5).  On a busy, shared machine a live process can be stopped
for a long time; only the end of its process releases what it holds.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from storyteller.storage import LockTimeoutError, acquire_lock

ROOT = Path(__file__).parents[1]


def _hold_in_child(lock: Path) -> subprocess.Popen:
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time\n"
         f"sys.path.insert(0, {str(ROOT / 'src')!r})\n"
         "from storyteller.storage import acquire_lock\n"
         f"with acquire_lock({str(lock)!r}):\n"
         "    print('held', flush=True)\n"
         "    time.sleep(600)\n"],
        stdout=subprocess.PIPE, text=True, encoding="utf-8",
    )
    assert child.stdout.readline().strip() == "held"
    return child


def _age(path: Path, seconds: float) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_a_stopped_live_holder_keeps_its_lock(tmp_path: Path) -> None:
    lock = tmp_path / "manifest.lock"
    child = _hold_in_child(lock)
    try:
        _age(lock, 3600)  # looks abandoned by age alone
        with pytest.raises(LockTimeoutError):
            with acquire_lock(lock, timeout=0.5):
                pytest.fail("the lock was taken from a live holder")
        assert lock.exists()
    finally:
        child.kill()
        child.wait()
    # The holder's process has ended: its lock is left over and reclaimed.
    with acquire_lock(lock, timeout=5):
        pass


def test_a_live_waiter_keeps_its_place_however_old_its_entry(tmp_path: Path) -> None:
    lock = tmp_path / "manifest.lock"
    holder = _hold_in_child(lock)
    waiter = subprocess.Popen(
        [sys.executable, "-c",
         "import sys\n"
         f"sys.path.insert(0, {str(ROOT / 'src')!r})\n"
         "from storyteller.storage import acquire_lock\n"
         f"with acquire_lock({str(lock)!r}):\n"
         "    print('acquired', flush=True)\n"],
        stdout=subprocess.PIPE, text=True, encoding="utf-8",
    )
    try:
        entries = lock.with_name(lock.name + ".waiters")
        for _ in range(100):
            if entries.is_dir() and any(entries.iterdir()):
                break
            time.sleep(0.05)
        (entry,) = entries.iterdir()
        _age(entry, 3600)
        # A newcomer must not pass the live, old-looking waiter.
        with pytest.raises(LockTimeoutError):
            with acquire_lock(lock, timeout=1.5):
                pytest.fail("a newcomer bypassed a live waiter")
        assert entry.exists()
        holder.kill()
        holder.wait()
        assert waiter.stdout.readline().strip() == "acquired"
        assert waiter.wait(10) == 0
    finally:
        for process in (holder, waiter):
            if process.poll() is None:
                process.kill()
                process.wait()


def test_an_entry_left_by_an_ended_waiter_is_removed(tmp_path: Path) -> None:
    lock = tmp_path / "manifest.lock"
    entries = lock.with_name(lock.name + ".waiters")
    entries.mkdir()
    # A huge deadline: only the missing OS lock shows that its waiter ended.
    left = entries / f"{time.monotonic_ns() - 1}-{time.monotonic_ns() + (1 << 61)}-{'b' * 32}.wait"
    left.touch()
    _age(left, 60)
    with acquire_lock(lock, timeout=5):
        assert not left.exists()
