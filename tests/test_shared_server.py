"""A shared machine and inference server: waiting is not failure, and no task
runs twice (task-model §5, §6.3; VISION P7).

Whether an executor is alive is decided by the OS lock it holds, never by
elapsed time.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from storyteller.adapter import AutoRunner, ModelConfig
from storyteller.orchestrator import Orchestrator
from storyteller.storage import file_lock_is_held, try_hold_file_lock
from tests.test_failure_handling import definition

ROOT = Path(__file__).parents[1]


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


def test_os_lock_is_held_only_while_the_holder_process_lives(tmp_path: Path) -> None:
    path = tmp_path / "claim.lock"
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time\n"
         f"sys.path.insert(0, {str(ROOT / 'src')!r})\n"
         "from storyteller.storage import try_hold_file_lock\n"
         f"hold = try_hold_file_lock({str(path)!r})\n"
         "print('held' if hold else 'busy', flush=True)\n"
         "time.sleep(600)\n"],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        time.sleep(1.5)  # a live holder stays the holder however long it waits
        assert file_lock_is_held(path)
        assert try_hold_file_lock(path) is None
    finally:
        holder.kill()
        holder.wait()
    assert not file_lock_is_held(path)


def _claimed(tmp_path: Path, clock: Clock):
    harness = Orchestrator(tmp_path, {"D1.value": definition(lease_minutes=1)}, clock=clock)
    run = harness.create_run(seed=1, input_data={"material": "素材"})
    claim = harness.claim_next(run, executor_id="auto-a", isolation="adapter")
    hold = try_hold_file_lock(harness.task_dir(run, "D1.value") / "claim.lock")
    assert hold is not None
    return harness, run, claim, hold


def test_live_auto_claim_is_kept_past_its_lease(tmp_path: Path) -> None:
    clock = Clock()
    harness, run, claim, hold = _claimed(tmp_path, clock)
    clock.now += timedelta(hours=3)
    assert harness.claim_next(run, executor_id="auto-b", isolation="adapter") is None
    assert harness.load_run(run)["tasks"]["D1.value"]["state"] == "claimed"
    assert harness.submit(claim["ticket"], "42").accepted
    hold.release()


def test_auto_claim_returns_to_ready_once_its_process_has_ended(tmp_path: Path) -> None:
    clock = Clock()
    harness, run, claim, hold = _claimed(tmp_path, clock)
    hold.release()  # what the OS does when the process ends
    clock.now += timedelta(minutes=2)
    again = harness.claim_next(run, executor_id="auto-b", isolation="adapter")
    assert again is not None and again["ticket"] != claim["ticket"]


def test_revoked_task_is_not_reissued_until_its_executor_stops(tmp_path: Path) -> None:
    clock = Clock()
    harness, run, claim, hold = _claimed(tmp_path, clock)
    harness.revoke_claim(run, "D1.value", reason="入力が変わった")
    assert harness.load_run(run)["tasks"]["D1.value"]["state"] == "ready"
    assert harness.claim_next(run, executor_id="auto-b", isolation="adapter") is None
    hold.release()
    assert harness.claim_next(run, executor_id="auto-b", isolation="adapter") is not None


class _HangingServer:
    """Answers the first request only by noticing that the client hung up."""

    def __init__(self) -> None:
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.first_request = threading.Event()
        self.first_closed_by_client = threading.Event()
        self.requests = 0
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                connection, _ = self.listener.accept()
            except OSError:
                return
            self.requests += 1
            threading.Thread(target=self._handle, args=(connection, self.requests), daemon=True).start()

    def _handle(self, connection: socket.socket, number: int) -> None:
        with connection:
            data = b""
            while b"\r\n\r\n" not in data:
                data += connection.recv(65536)
            if number == 1:
                self.first_request.set()
                connection.settimeout(30)
                try:
                    while connection.recv(65536):
                        pass  # the body, then nothing: keep the client waiting
                    self.first_closed_by_client.set()
                except (OSError, socket.timeout):
                    pass
                return
            body = (b'{"message":{"content":"42"},"done_reason":"stop","total_duration":9000000000,'
                    b'"load_duration":1000000000,"prompt_eval_duration":1000000000,"eval_duration":1000000000}')
            connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                               + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)


def test_queue_wait_is_wall_time_minus_the_server_work() -> None:
    from storyteller.adapter import OllamaAdapter
    response = OllamaAdapter._decode_response({
        "message": {"content": "42"}, "total_duration": 9_000_000_000,
        "load_duration": 1_000_000_000, "prompt_eval_duration": 1_000_000_000, "eval_duration": 1_000_000_000,
    })
    assert response.server_seconds == 3.0  # total_duration includes the queue


def test_revoked_auto_claim_closes_its_connection_and_drops_the_result(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("storyteller.adapter._ClaimWatch.interval", 0.2)
    server = _HangingServer()
    harness = Orchestrator(tmp_path, {"D1.value": definition()})
    run = harness.create_run(seed=1, input_data={"material": "素材"})
    model = ModelConfig("test:model", "ollama", f"http://127.0.0.1:{server.port}", 0.2, 50, 1000, "off")

    def revoke() -> None:
        assert server.first_request.wait(30)
        harness.revoke_claim(run, "D1.value", reason="入力が変わった")

    threading.Thread(target=revoke, daemon=True).start()
    AutoRunner(tmp_path, model, harness).run(run_id=run, workers=1, until_empty=True)
    # The first request was abandoned by closing its connection (the server
    # stops the work), and the task was done by a fresh claim afterwards.
    assert server.first_closed_by_client.wait(5)
    assert server.requests == 2
    assert harness.load_run(run)["tasks"]["D1.value"]["state"] == "done"
