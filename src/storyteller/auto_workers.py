"""Worker supervision for ``st auto --workers N`` (P4-01).

A worker that dies from an unexpected error must not take the other workers
down.  Its claim is left as it is: the lease expires, and the next claim by any
worker treats it as an invalid claim (task-model.md section 5).  A dead worker
is replaced a limited number of times; if every slot gives up, the run of
``st auto`` fails instead of reporting success.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable

MAX_RESTARTS = 3


class WorkersFailed(RuntimeError):
    """Raised when no worker could finish its work normally."""


def run_supervised(
    workers: int,
    worker: Callable[[], None],
    *,
    max_restarts: int = MAX_RESTARTS,
) -> None:
    """Run ``workers`` copies of ``worker`` in threads, restarting dead ones."""

    finished = 0
    given_up: list[BaseException] = []
    guard = threading.Lock()

    def slot(index: int) -> None:
        nonlocal finished
        restarts = 0
        while True:
            try:
                worker()
            except Exception as error:  # noqa: BLE001 - supervise any worker failure
                restarts += 1
                print(
                    f"警告: ワーカー {index + 1} が異常終了しました（{type(error).__name__}: {error}）。"
                    "その claim は lease が切れた後に再取得されます",
                    file=sys.stderr,
                )
                if restarts > max_restarts:
                    with guard:
                        given_up.append(error)
                    return
                continue
            with guard:
                finished += 1
            return

    if workers == 1:
        # Run in the caller's thread so that its exceptions and interrupts propagate.
        slot(0)
        _finish(finished, given_up)
        return
    def thread_slot(index: int) -> None:
        # BaseException (e.g. SystemExit) in a thread would otherwise vanish
        # without being counted; record it as a failure and do not restart.
        try:
            slot(index)
        except BaseException as error:  # noqa: BLE001
            print(
                f"警告: ワーカー {index + 1} が中断されました（{type(error).__name__}: {error}）",
                file=sys.stderr,
            )
            with guard:
                given_up.append(error)

    threads = [
        threading.Thread(target=thread_slot, args=(index,), name=f"st-auto-{index + 1}")
        for index in range(workers)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    _finish(finished, given_up)


def _finish(finished: int, given_up: list[BaseException]) -> None:
    if finished == 0 and given_up:
        raise WorkersFailed(f"すべてのワーカーが異常終了しました: {given_up[-1]}") from given_up[-1]
    if given_up:
        print(
            f"警告: {len(given_up)} 個のワーカーを再起動できず、残りのワーカーで続けました",
            file=sys.stderr,
        )


__all__ = ["MAX_RESTARTS", "WorkersFailed", "run_supervised"]
