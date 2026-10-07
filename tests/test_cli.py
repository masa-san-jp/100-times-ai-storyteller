import io
import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import jsonschema
import pytest

from storyteller.cli import build_parser, main
from storyteller.new_run import create_story_orchestrator
from storyteller.orchestrator import SubmissionResult, TaskSpec
from tests.support.dummy_clock import FIXED_TIME, dummy_clock, st_command


@pytest.fixture(autouse=True)
def freeze_dummy_clock():
    with dummy_clock():
        yield


def _run_st(
    data_dir: Path,
    *arguments: str,
    input: str | None = None,
    cwd: Path | None = None,
    timeout: float = 30,
    now: datetime = FIXED_TIME,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["STORYTELLER_HOME"] = str(data_dir)
    return subprocess.run(
        [*st_command(now), *arguments],
        input=input,
        text=True,
        encoding="utf-8",
        capture_output=True,
        cwd=cwd,
        env=environment,
        check=False,
        timeout=timeout,
    )


def _run_st_with_cp1252_stdio(
    data_dir: Path,
    *arguments: str,
    input: bytes | None = None,
    cwd: Path | None = None,
    timeout: float = 30,
) -> subprocess.CompletedProcess[bytes]:
    environment = os.environ.copy()
    environment["STORYTELLER_HOME"] = str(data_dir)
    environment["PYTHONIOENCODING"] = "cp1252"
    return subprocess.run(
        [*st_command(), *arguments],
        input=input,
        capture_output=True,
        cwd=cwd,
        env=environment,
        check=False,
        timeout=timeout,
    )


def _new_dummy(data_dir: Path, seed: int = 7) -> str:
    result = _run_st(data_dir, "dev", "new-dummy", "--seed", str(seed))
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _claim(data_dir: Path, run_id: str) -> dict[str, str]:
    result = _run_st(data_dir, "next", "--run", run_id, "--json")
    assert result.returncode == 0, result.stderr
    claim = json.loads(result.stdout)
    return claim


def _manifest_path(data_dir: Path, run_id: str) -> Path:
    return data_dir / "runs" / run_id / "manifest.json"


def _load_manifest(data_dir: Path, run_id: str) -> dict:
    return json.loads(_manifest_path(data_dir, run_id).read_text(encoding="utf-8"))


def _save_manifest(data_dir: Path, run_id: str, manifest: dict) -> None:
    _manifest_path(data_dir, run_id).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _task_id_for_ticket(data_dir: Path, run_id: str, ticket: str) -> str:
    manifest = _load_manifest(data_dir, run_id)
    for task_id, task in manifest["tasks"].items():
        if (task.get("claim") or {}).get("ticket") == ticket:
            return task_id
    raise AssertionError(f"ticket が見つかりません: {ticket}")


def _drain_run(data_dir: Path, run_id: str) -> None:
    while True:
        result = _run_st(data_dir, "next", "--run", run_id, "--json")
        if result.returncode == 2:
            return
        assert result.returncode == 0, result.stderr
        claim = json.loads(result.stdout)
        task_id = _task_id_for_ticket(data_dir, run_id, claim["ticket"])
        if task_id.startswith("D2.echo-"):
            item_id = task_id.rsplit("-", 1)[1]
            output = "ok"
        else:
            output = "alpha beta gamma。"
        accepted = _run_st(
            data_dir, "submit", claim["ticket"], input=output
        )
        assert accepted.returncode == 0, accepted.stdout + accepted.stderr


def test_cli_help_lists_the_command_section(capsys):
    assert main([]) == 0

    output = capsys.readouterr().out
    assert "usage: st" in output
    assert "commands:" in output


def test_parser_accepts_help(capsys):
    parser = build_parser()

    try:
        parser.parse_args(["--help"])
    except SystemExit as error:
        assert error.code == 0
    else:
        raise AssertionError("--help should exit successfully")

    assert "100 TIMES AI STORYTELLER" in capsys.readouterr().out


def test_phase0_cli_creates_dummy_and_claims_json_card(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STORYTELLER_HOME", str(tmp_path / "data"))

    assert main(["dev", "new-dummy", "--seed", "7"]) == 0
    run_id = capsys.readouterr().out.strip()

    assert main(["next", "--run", run_id, "--json"]) == 0
    claim = json.loads(capsys.readouterr().out)
    assert set(claim) == {"ticket", "card", "lease_expires_at"}
    assert "D1.items" not in claim["card"]

    monkeypatch.setattr(
        "sys.stdin", io.StringIO("alpha")
    )
    assert main(["submit", claim["ticket"]]) == 0
    assert capsys.readouterr().out == "accepted\n"
    assert main(["next", "--run", run_id]) == 0


def test_mixed_story_and_dummy_runs_use_manifest_harness_kind_for_next_and_submit(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    story = create_story_orchestrator(data_dir)
    # Keep creation order explicit when mixing a story run with the frozen dummy.
    story._clock = lambda: FIXED_TIME - timedelta(seconds=1)
    story_run_id = story.create_run(
        task_specs=[TaskSpec("S1.extract-p001", "S1.extract", index=("p001",))],
        seed=0x100001,
        input_data={
            "kind": "free",
            "paragraphs": [{"id": "p001", "text": "水路の町で門番が朝を迎えた。"}],
        },
    )
    dummy_run_id = _new_dummy(data_dir, seed=0x200002)
    assert _load_manifest(data_dir, story_run_id)["harness_kind"] == "story"
    assert _load_manifest(data_dir, dummy_run_id)["harness_kind"] == "dummy"

    claimed: list[tuple[str, str]] = []
    for _ in range(2):
        result = _run_st(data_dir, "next", "--json")
        assert result.returncode == 0, result.stderr
        ticket = json.loads(result.stdout)["ticket"]
        run_id = next(
            candidate
            for candidate in (story_run_id, dummy_run_id)
            if any(
                (task.get("claim") or {}).get("ticket") == ticket
                for task in _load_manifest(data_dir, candidate)["tasks"].values()
            )
        )
        task_id = _task_id_for_ticket(data_dir, run_id, ticket)
        claimed.append((run_id, task_id))

        if task_id.startswith("S1."):
            output = json.dumps(
                {
                    "materials": [
                        {"text": f"水路に残る記憶と{index}番目の影", "kind": "image"}
                        for index in range(5)
                    ],
                },
                ensure_ascii=False,
            )
        else:
            assert task_id.startswith("D2.echo-")
            item_id = task_id.rsplit("-", 1)[1]
            output = "確認できた項目です。"
        submitted = _run_st(data_dir, "submit", ticket, input=output)
        assert submitted.returncode == 0, submitted.stdout + submitted.stderr

    assert {run_id for run_id, _ in claimed} == {story_run_id, dummy_run_id}
    assert _load_manifest(data_dir, story_run_id)["status"] != "halted"
    assert _load_manifest(data_dir, dummy_run_id)["status"] != "halted"


def test_submit_rejection_uses_exit_code_five(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STORYTELLER_HOME", str(tmp_path / "data"))
    assert main(["dev", "new-dummy", "--seed", "8"]) == 0
    run_id = capsys.readouterr().out.strip()
    assert main(["next", "--run", run_id, "--json"]) == 0
    ticket = json.loads(capsys.readouterr().out)["ticket"]

    monkeypatch.setattr("sys.stdin", io.StringIO("あ" * 51))
    assert main(["submit", ticket]) == 5
    assert capsys.readouterr().out.startswith("rejected: ")


def test_submit_invalid_ticket_uses_exit_code_three(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STORYTELLER_HOME", str(tmp_path / "data"))
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))

    assert main(["submit", "0" * 32]) == 3
    assert "ticket" in capsys.readouterr().err


def test_subprocess_json_and_status_outputs_are_machine_readable(tmp_path):
    data_dir = tmp_path / "data"
    run_id = _new_dummy(data_dir)

    claim_result = _run_st(data_dir, "next", "--run", run_id, "--json")
    assert claim_result.returncode == 0
    claim = json.loads(claim_result.stdout)
    assert "\\n" in claim_result.stdout
    assert "1件の項目" in claim_result.stdout
    assert set(claim) == {"ticket", "card", "lease_expires_at"}

    accepted = _run_st(
        data_dir,
        "submit",
        claim["ticket"],
        input="ok",
    )
    assert accepted.returncode == 0
    assert accepted.stdout == "accepted\n"

    schema_path = Path(__file__).parents[1] / "schemas" / "status.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    status_result = _run_st(data_dir, "status", "--run", run_id, "--json")
    assert status_result.returncode == 0
    status = json.loads(status_result.stdout)
    jsonschema.validate(status, schema)

    all_status_result = _run_st(data_dir, "status", "--json")
    assert all_status_result.returncode == 0
    jsonschema.validate(json.loads(all_status_result.stdout), schema)


def test_subprocess_stdio_is_utf8_with_cp1252_default(tmp_path):
    data_dir = tmp_path / "data"
    created = _run_st_with_cp1252_stdio(data_dir, "dev", "new-dummy", "--seed", "7")
    assert created.returncode == 0, created.stderr.decode("utf-8")
    run_id = created.stdout.decode("utf-8").strip()

    claim_result = _run_st_with_cp1252_stdio(
        data_dir, "next", "--run", run_id, "--json"
    )
    assert claim_result.returncode == 0, claim_result.stderr.decode("utf-8")
    assert b"\r\n" not in claim_result.stdout
    claim = json.loads(claim_result.stdout.decode("utf-8"))
    assert "1件の項目" in claim["card"]

    accepted = _run_st_with_cp1252_stdio(
        data_dir,
        "submit",
        claim["ticket"],
        input="日本語の提出".encode("utf-8"),
    )
    assert accepted.returncode == 0, accepted.stderr.decode("utf-8")
    assert accepted.stdout == b"accepted\n"


def test_subprocess_exit_codes_cover_success_no_task_and_submission_states(tmp_path):
    empty_data = tmp_path / "empty"
    assert _run_st(empty_data, "next").returncode == 2
    assert _run_st(empty_data, "next", "--wait", "0").returncode == 2

    data_dir = tmp_path / "data"
    run_id = _new_dummy(data_dir, seed=8)
    claim = _claim(data_dir, run_id)
    rejected = _run_st(data_dir, "submit", claim["ticket"], input="あ" * 51)
    assert rejected.returncode == 5
    assert rejected.stdout.startswith("rejected: ")

    replacement = _claim(data_dir, run_id)
    accepted = _run_st(
        data_dir,
        "submit",
        replacement["ticket"],
        input="ok",
    )
    assert accepted.returncode == 0
    assert accepted.stdout == "accepted\n"
    assert _run_st(data_dir, "status").returncode == 0
    _drain_run(data_dir, run_id)
    assert _run_st(data_dir, "next", "--run", run_id).returncode == 2

    failed_data = tmp_path / "failed"
    failed_run = _new_dummy(failed_data, seed=9)
    failed_task_id = ""
    for _ in range(5):
        failed_claim = _claim(failed_data, failed_run)
        if not failed_task_id:
            failed_task_id = _task_id_for_ticket(
                failed_data, failed_run, failed_claim["ticket"]
            )
        failed = _run_st(
            failed_data,
            "submit",
            failed_claim["ticket"],
            input="あ" * 51,
        )
        assert failed.returncode == 5
    assert _run_st(failed_data, "retry", failed_task_id).returncode == 0


def test_subprocess_rejects_invalid_arguments_state_and_truncated_short_text(tmp_path):
    data_dir = tmp_path / "data"
    assert _run_st(data_dir, "resume").returncode == 1
    assert _run_st(data_dir, "next", "--wait", "-1").returncode == 1
    assert _run_st(data_dir, "next", "--run", "not-a-run").returncode == 1
    assert _run_st(data_dir, "next", "--run", "20260101-000000-abcdef").returncode == 1
    assert _run_st(data_dir, "next", "--executor-id", "invalid/id").returncode == 1
    assert _run_st(data_dir, "retry", "D1.echo").returncode == 1

    run_id = _new_dummy(data_dir, seed=10)
    claim = _claim(data_dir, run_id)
    truncated = _run_st(
        data_dir,
        "submit",
        claim["ticket"],
        "--truncated",
        input="ok",
    )
    assert truncated.returncode == 5

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".storyteller-workspace.yaml").write_text(
        "data_dir: [broken\n", encoding="utf-8"
    )
    broken_config = _run_st(
        tmp_path / "ignored-data",
        "status",
        cwd=workspace,
    )
    assert broken_config.returncode == 1


def test_subprocess_exit_code_three_covers_missing_and_expired_tickets(tmp_path):
    missing_data = tmp_path / "missing"
    missing = _run_st(missing_data, "submit", "0" * 32, input="{}")
    assert missing.returncode == 3

    expired_data = tmp_path / "expired"
    run_id = _new_dummy(expired_data, seed=11)
    claim = _claim(expired_data, run_id)
    expired = _run_st(
        expired_data,
        "submit",
        claim["ticket"],
        input="{}",
        now=FIXED_TIME + timedelta(seconds=3),
    )
    assert expired.returncode == 3


def test_subprocess_exit_code_six_covers_halted_next_and_submit_and_resume(tmp_path):
    data_dir = tmp_path / "data"
    run_id = _new_dummy(data_dir, seed=12)
    claim = _claim(data_dir, run_id)
    manifest = _load_manifest(data_dir, run_id)
    manifest["status"] = "halted"
    _save_manifest(data_dir, run_id, manifest)

    assert _run_st(data_dir, "next", "--run", run_id).returncode == 6
    halted_submit = _run_st(
        data_dir,
        "submit",
        claim["ticket"],
        input="ok",
    )
    assert halted_submit.returncode == 6
    assert _run_st(
        data_dir,
        "resume",
        "--accept-harness-change",
        run_id,
    ).returncode == 0


def test_subprocess_exit_code_ten_covers_manifest_lock_timeout(tmp_path):
    data_dir = tmp_path / "data"
    run_id = _new_dummy(data_dir, seed=13)
    manifest = _load_manifest(data_dir, run_id)
    manifest["status"] = "stalled"
    _save_manifest(data_dir, run_id, manifest)
    lock_path = data_dir / "runs" / run_id / "manifest.lock"
    lock_path.write_text("{}\n", encoding="utf-8")
    timed_out = _run_st(
        data_dir,
        "next",
        "--run",
        run_id,
        timeout=15,
    )
    assert timed_out.returncode == 10


def test_sync_folder_warning_is_written_to_stderr(tmp_path):
    result = _run_st(tmp_path / "Google Drive" / "storyteller", "status")

    assert result.returncode == 0
    assert "同期フォルダ" in result.stderr


def test_submit_prints_continued_for_a_continuation_submission(
    tmp_path, monkeypatch, capsys
):
    class FakeOrchestrator:
        def submit(self, ticket, raw_output, *, truncated):
            assert ticket == "ticket"
            assert raw_output == "chunk"
            assert truncated is True
            return SubmissionResult(True, "run", "D1.story")

        def load_run(self, run_id):
            assert run_id == "run"
            return {
                "tasks": {
                    "D1.story": {"state": "ready", "continuation_step": 1}
                }
            }

    monkeypatch.setenv("STORYTELLER_HOME", str(tmp_path / "data"))
    monkeypatch.setattr("storyteller.cli._orchestrator", lambda data_dir: FakeOrchestrator())
    monkeypatch.setattr("sys.stdin", io.StringIO("chunk"))

    assert main(["submit", "ticket", "--truncated"]) == 0
    assert capsys.readouterr().out == "continued\n"
