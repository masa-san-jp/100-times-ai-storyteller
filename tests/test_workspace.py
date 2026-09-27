from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

from storyteller import workspace
from storyteller.cli import main
from storyteller.validation import load_and_validate_yaml


ROOT = Path(__file__).parents[1]
WORKSPACE_SCHEMA = ROOT / "schemas" / "workspace.schema.json"


def _assert_same_path(actual: str, expected: Path) -> None:
    """Compare paths by meaning, not by OS-specific spelling."""
    assert Path(actual).resolve(strict=False) == expected.resolve(strict=False)


def _protocol_from_spec() -> str:
    document = (ROOT / "docs" / "spec" / "executor-protocol.md").read_text(
        encoding="utf-8"
    )
    match = re.search(r"```markdown\n(?P<body>.*?)\n```", document, re.DOTALL)
    assert match is not None
    return match.group("body") + "\n"


def test_protocol_templates_match_the_spec() -> None:
    expected = _protocol_from_spec()
    for name in ("AGENTS.md", "CLAUDE.md"):
        template = ROOT / "src" / "storyteller" / "workspace_template" / name
        assert template.read_text(encoding="utf-8") == expected


def test_workspace_init_writes_valid_config_and_protocol(tmp_path: Path) -> None:
    target = tmp_path / "executor"
    data_dir = tmp_path / "storyteller-data"

    created = workspace.init_workspace(
        target,
        data_dir=data_dir,
        executor_id="executor.test-1",
        agent="generic",
    )

    assert created == target.resolve()
    assert (target / "AGENTS.md").read_text(encoding="utf-8") == _protocol_from_spec()
    assert (target / "CLAUDE.md").read_text(encoding="utf-8") == _protocol_from_spec()
    config = load_and_validate_yaml(target / ".storyteller-workspace.yaml", WORKSPACE_SCHEMA)
    _assert_same_path(config["data_dir"], data_dir)
    assert config["executor_id"] == "executor.test-1"
    assert config["agent"] == "generic"
    assert config["isolation"] == "placement"
    assert not (target / ".claude").exists()
    assert not (target / ".codex").exists()


def test_claude_settings_allow_only_executor_surface_and_deny_outside_reads(
    tmp_path: Path,
) -> None:
    target = tmp_path / "claude-executor"
    data_dir = tmp_path / "storyteller-data"

    workspace.init_workspace(
        target,
        data_dir=data_dir,
        executor_id="claude.worker",
        agent="claude-code",
    )

    settings = json.loads(
        (target / ".claude" / "settings.json").read_text(encoding="utf-8")
    )
    permissions = settings["permissions"]
    assert permissions["allow"] == [
        "Bash(st next *)",
        "Bash(st submit *)",
        "Edit(./out.txt)",
    ]
    assert "Read(../**)" in permissions["deny"]
    assert any("AGENTS.md" not in rule for rule in permissions["deny"])
    assert any("storyteller-data" in rule for rule in permissions["deny"])


def test_codex_config_uses_workspace_sandbox_and_data_root(tmp_path: Path) -> None:
    target = tmp_path / "codex-executor"
    data_dir = tmp_path / "storyteller-data"

    workspace.init_workspace(
        target,
        data_dir=data_dir,
        executor_id="codex.worker",
        agent="codex",
    )

    config = tomllib.loads(
        (target / ".codex" / "config.toml").read_text(encoding="utf-8")
    )
    assert config["sandbox_mode"] == "workspace-write"
    assert config["approval_policy"] == "never"
    assert config["sandbox_workspace_write"]["network_access"] is False
    writable_roots = config["sandbox_workspace_write"]["writable_roots"]
    assert len(writable_roots) == 1
    _assert_same_path(writable_roots[0], data_dir)
    workspace_config = load_and_validate_yaml(
        target / ".storyteller-workspace.yaml", WORKSPACE_SCHEMA
    )
    assert workspace_config["isolation"] == "placement"


def test_codex_workspace_records_placement_in_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    target = tmp_path / "codex-executor"
    data_dir = tmp_path / "storyteller-data"
    workspace.init_workspace(
        target,
        data_dir=data_dir,
        executor_id="codex.worker",
        agent="codex",
    )

    monkeypatch.chdir(target)
    assert main(["dev", "new-dummy"]) == 0
    run_id = capsys.readouterr().out.strip()
    assert main(["next", "--run", run_id, "--json"]) == 0
    capsys.readouterr()

    manifest = json.loads(
        (data_dir / "runs" / run_id / "manifest.json").read_text(encoding="utf-8")
    )
    claims = [task["claim"] for task in manifest["tasks"].values() if task["claim"]]
    assert len(claims) == 1
    assert claims[0]["isolation"] == "placement"


def test_workspace_init_rejects_a_path_inside_the_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    target = repository / "executor"

    original_repository_root = workspace._repository_root
    try:
        workspace._repository_root = lambda: repository
        with pytest.raises(workspace.WorkspaceError, match="リポジトリの外"):
            workspace.init_workspace(target)
    finally:
        workspace._repository_root = original_repository_root


def test_cli_registers_workspace_init_without_implementing_it_in_cli(
    tmp_path: Path, capsys
) -> None:
    target = tmp_path / "executor"
    data_dir = tmp_path / "data"

    assert main(
        [
            "workspace",
            "init",
            str(target),
            "--data-dir",
            str(data_dir),
            "--agent",
            "generic",
        ]
    ) == 0
    assert capsys.readouterr().out == f"{target.resolve()}\n"


@pytest.mark.parametrize(
    ("agent", "expected_command"),
    [
        (
            "claude-code",
            'claude -p "<指示>" --allowedTools "Bash(st next *)" '
            '"Bash(st submit *)" "Edit(./out.txt)"',
        ),
        ("codex", 'codex exec -C {workspace} "<指示>"'),
    ],
)
def test_cli_workspace_init_prints_agent_launch_command(
    tmp_path: Path, capsys, agent: str, expected_command: str
) -> None:
    target = tmp_path / f"{agent}-executor"

    assert main(
        [
            "workspace",
            "init",
            str(target),
            "--data-dir",
            str(tmp_path / "data"),
            "--agent",
            agent,
        ]
    ) == 0

    output = capsys.readouterr().out
    assert output.startswith(f"{target.resolve()}\n起動コマンド: ")
    assert expected_command.format(workspace=target.resolve().as_posix()) in output
