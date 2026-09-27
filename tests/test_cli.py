import io
import json

from storyteller.cli import build_parser, main


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
    assert "D1.echo" not in claim["card"]

    monkeypatch.setattr("sys.stdin", io.StringIO('{"text":"alpha"}'))
    assert main(["submit", claim["ticket"]]) == 0
    assert capsys.readouterr().out == "accepted\n"
    assert main(["next", "--run", run_id]) == 2


def test_submit_rejection_uses_exit_code_five(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STORYTELLER_HOME", str(tmp_path / "data"))
    assert main(["dev", "new-dummy", "--seed", "8"]) == 0
    run_id = capsys.readouterr().out.strip()
    assert main(["next", "--run", run_id, "--json"]) == 0
    ticket = json.loads(capsys.readouterr().out)["ticket"]

    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert main(["submit", ticket]) == 5
    assert capsys.readouterr().out.startswith("rejected: ")


def test_submit_invalid_ticket_uses_exit_code_three(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STORYTELLER_HOME", str(tmp_path / "data"))
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))

    assert main(["submit", "0" * 32]) == 3
    assert "ticket" in capsys.readouterr().err
