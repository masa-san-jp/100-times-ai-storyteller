"""Inject a deterministic clock into the dummy harness, including CLI children."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

from storyteller.orchestrator import Orchestrator


FIXED_TIME = datetime(2026, 9, 27, 3, 15, tzinfo=timezone.utc)


@contextmanager
def dummy_clock(now: datetime = FIXED_TIME) -> Iterator[None]:
    # Use the existing clock seam without changing bundled task definitions.
    with patch(
        "storyteller.dev.dummy.Orchestrator",
        partial(Orchestrator, clock=lambda: now),
    ):
        yield


def st_command(now: datetime = FIXED_TIME) -> list[str]:
    return [sys.executable, str(Path(__file__).resolve()), now.isoformat()]


if __name__ == "__main__":
    from storyteller.cli import main

    with dummy_clock(datetime.fromisoformat(sys.argv[1])):
        raise SystemExit(main(sys.argv[2:]))
