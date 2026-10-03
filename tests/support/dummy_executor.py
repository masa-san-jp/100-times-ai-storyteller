from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from tests.support.dummy_clock import FIXED_TIME, st_command


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKER_SCRIPT = Path(__file__).with_name("dummy_next_worker.py")


@dataclass(frozen=True)
class Response:
    output: Any
    truncated: bool = False


@dataclass(frozen=True)
class Claim:
    ticket: str
    card: str
    lease_expires_at: str


class DummyExecutor:
    """Drive the dummy harness using only ``st next`` and ``st submit``."""

    def __init__(
        self, data_dir: Path, run_id: str | None = None, *, now: datetime = FIXED_TIME
    ) -> None:
        self.data_dir = data_dir
        self.run_id = run_id
        self.now = now

    @property
    def environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment["STORYTELLER_HOME"] = str(self.data_dir)
        return environment

    def run_new_dummy(self, seed: int = 7) -> str:
        result = self._run("dev", "new-dummy", "--seed", str(seed))
        assert result.returncode == 0, result.stderr
        self.run_id = result.stdout.strip()
        return self.run_id

    def next(self) -> Claim | None:
        arguments = ["next", "--json"]
        if self.run_id is not None:
            arguments.extend(["--run", self.run_id])
        result = self._run(*arguments)
        if result.returncode == 2:
            return None
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        return Claim(
            ticket=payload["ticket"],
            card=payload["card"],
            lease_expires_at=payload["lease_expires_at"],
        )

    def submit(self, claim: Claim, response: Response) -> subprocess.CompletedProcess[str]:
        output = response.output
        if isinstance(output, Mapping):
            output = json.dumps(output, ensure_ascii=False)
        assert isinstance(output, str)
        arguments = ["submit", claim.ticket]
        if response.truncated:
            arguments.append("--truncated")
        return self._run(*arguments, input=output)

    def run(
        self,
        responder: Callable[[str], Response],
        *,
        stop_after_claim: bool = False,
        stop_after_successes: int | None = None,
    ) -> list[subprocess.CompletedProcess[str]]:
        results: list[subprocess.CompletedProcess[str]] = []
        successes = 0
        while True:
            claim = self.next()
            if claim is None:
                return results
            if stop_after_claim:
                return results
            result = self.submit(claim, responder(claim.card))
            results.append(result)
            if result.returncode == 0:
                successes += 1
                if stop_after_successes is not None and successes >= stop_after_successes:
                    return results

    def concurrent_next(self, directory: Path) -> list[dict[str, Any]]:
        start_signal = directory / "start.signal"
        processes: list[subprocess.Popen[bytes]] = []
        result_paths: list[Path] = []
        for number in (1, 2):
            result_path = directory / f"worker-{number}.json"
            result_paths.append(result_path)
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        str(WORKER_SCRIPT),
                        str(start_signal),
                        str(result_path),
                        str(self.data_dir),
                        self.run_id or "",
                        self.now.isoformat(),
                    ],
                    cwd=REPOSITORY_ROOT,
                    env=self.environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            )
        start_signal.write_text("go\n", encoding="utf-8")
        for process in processes:
            process.wait(timeout=30)
        return [json.loads(path.read_text(encoding="utf-8")) for path in result_paths]

    def _run(
        self,
        *arguments: str,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*st_command(self.now), *arguments],
            input=input,
            text=True,
            encoding="utf-8",
            capture_output=True,
            cwd=REPOSITORY_ROOT,
            env=self.environment,
            check=False,
            timeout=30,
        )


def item_id_from_card(card: str) -> str | None:
    match = re.search(r"(?:\[(d[123])\]|id:\s*(d[123]))", card)
    if match is None:
        return None
    return next(group for group in match.groups() if group is not None)
