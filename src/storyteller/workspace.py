"""Create isolated workspaces for external storyteller executors."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shlex
import socket
from pathlib import Path
from typing import Any

from .storage import atomic_write_text, resolve_data_dir


_EXECUTOR_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SUPPORTED_AGENTS = frozenset({"claude-code", "codex", "generic"})
_PERMISSION_AGENTS = frozenset({"claude-code"})
_CLAUDE_ALLOWED_TOOLS = (
    '"Bash(st next *)"',
    '"Bash(st submit *)"',
    '"Edit(./out.txt)"',
)


class WorkspaceError(ValueError):
    """Raised when an executor workspace cannot be created."""


def protocol_template() -> str:
    """Return the executor protocol shipped with the package."""
    template_path = Path(__file__).with_name("workspace_template") / "AGENTS.md"
    try:
        return template_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise WorkspaceError(f"実行者プロトコルのテンプレートを読めません: {template_path}") from error


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _absolute_path(path: str | os.PathLike[str]) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _executor_id() -> str:
    host = socket.gethostname()
    host = re.sub(r"[^A-Za-z0-9._-]", "-", host).strip(".-") or "executor"
    suffix = secrets.token_hex(4)
    value = f"{host[:55]}-{suffix}"
    return value if _EXECUTOR_ID_RE.fullmatch(value) else f"executor-{suffix}"


def _validate_executor_id(value: str) -> str:
    if not _EXECUTOR_ID_RE.fullmatch(value):
        raise WorkspaceError(
            "--executor-id は英数字・ピリオド・アンダースコア・ハイフンを "
            "1〜64文字で指定してください"
        )
    return value


def _claude_path_pattern(path: Path) -> str:
    """Make an absolute Claude Code Read rule for a directory."""
    portable = path.as_posix().rstrip("/")
    return f"Read(//{portable.lstrip('/')}/**)"


def _claude_settings(repository: Path, data_dir: Path) -> dict[str, Any]:
    """Return a least-privilege Claude Code project settings document."""
    return {
        "permissions": {
            "allow": [
                "Bash(st next *)",
                "Bash(st submit *)",
                "Edit(./out.txt)",
            ],
            "deny": [
                "Read(../**)",
                _claude_path_pattern(repository),
                _claude_path_pattern(data_dir),
            ],
        }
    }


def _codex_config(data_dir: Path) -> str:
    """Return a Codex sandbox config with the data directory as its only extra root."""
    data_dir_literal = json.dumps(data_dir.as_posix(), ensure_ascii=False)
    return (
        'sandbox_mode = "workspace-write"\n'
        'approval_policy = "never"\n'
        "\n"
        "[sandbox_workspace_write]\n"
        "network_access = false\n"
        f"writable_roots = [{data_dir_literal}]\n"
    )


def _workspace_config(data_dir: Path, executor_id: str, agent: str) -> str:
    # Keep this small and stable because it is also read by every `st next`.
    return (
        f"data_dir: {json.dumps(data_dir.as_posix(), ensure_ascii=False)}\n"
        f"executor_id: {executor_id}\n"
        f"agent: {agent}\n"
        f"isolation: {'permission' if agent in _PERMISSION_AGENTS else 'placement'}\n"
    )


def _launch_command(workspace: Path, agent: str) -> str | None:
    """Return the copyable product-specific command shown after init."""
    workspace_literal = shlex.quote(workspace.as_posix())
    if agent == "claude-code":
        allowed_tools = " ".join(_CLAUDE_ALLOWED_TOOLS)
        return (
            f"(cd {workspace_literal} && claude -p \"<指示>\" "
            f"--allowedTools {allowed_tools})"
        )
    if agent == "codex":
        return f'codex exec -C {workspace_literal} "<指示>"'
    return None


def init_workspace(
    path: str | os.PathLike[str],
    *,
    data_dir: str | os.PathLike[str] | None = None,
    executor_id: str | None = None,
    agent: str = "generic",
) -> Path:
    """Create an executor workspace and return its absolute path."""
    if agent not in _SUPPORTED_AGENTS:
        raise WorkspaceError(f"未対応の実行者種別です: {agent}")

    raw_workspace = Path(path).expanduser()
    if not raw_workspace.is_absolute():
        raw_workspace = Path.cwd() / raw_workspace
    raw_workspace = raw_workspace.absolute()
    workspace = raw_workspace.resolve(strict=False)
    repository = _repository_root()
    if _is_within(raw_workspace, repository) or _is_within(workspace, repository):
        raise WorkspaceError(
            f"実行者ワークスペースはリポジトリの外に作成してください: {repository}"
        )
    if workspace.exists() and not workspace.is_dir():
        raise WorkspaceError(f"ワークスペースのパスがディレクトリではありません: {workspace}")

    resolved_data_dir = (
        _absolute_path(data_dir)
        if data_dir is not None
        else resolve_data_dir(package_file=Path(__file__))
    )
    resolved_executor_id = _validate_executor_id(executor_id or _executor_id())
    workspace.mkdir(parents=True, exist_ok=True)

    protocol = protocol_template()
    atomic_write_text(workspace / "AGENTS.md", protocol)
    atomic_write_text(workspace / "CLAUDE.md", protocol)
    atomic_write_text(
        workspace / ".storyteller-workspace.yaml",
        _workspace_config(resolved_data_dir, resolved_executor_id, agent),
    )

    if agent == "claude-code":
        settings_path = workspace / ".claude" / "settings.json"
        atomic_write_text(
            settings_path,
            json.dumps(
                _claude_settings(repository, resolved_data_dir),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
    elif agent == "codex":
        atomic_write_text(workspace / ".codex" / "config.toml", _codex_config(resolved_data_dir))

    return workspace


def _run_init_command(args: argparse.Namespace) -> int:
    workspace = init_workspace(
        args.path,
        data_dir=args.data_dir,
        executor_id=args.executor_id,
        agent=args.agent,
    )
    print(workspace)
    command = _launch_command(workspace, args.agent)
    if command is not None:
        print(f"起動コマンド: {command}")
    return 0


def register_cli_commands(subparsers: Any) -> None:
    """Register the workspace CLI surface without putting its logic in cli.py."""
    workspace_parser = subparsers.add_parser(
        "workspace", help="create an executor workspace"
    )
    workspace_subparsers = workspace_parser.add_subparsers(
        dest="workspace_command", title="commands"
    )
    init_parser = workspace_subparsers.add_parser(
        "init", help="create an executor workspace"
    )
    init_parser.add_argument("path")
    init_parser.add_argument("--data-dir")
    init_parser.add_argument("--executor-id")
    init_parser.add_argument(
        "--agent", choices=sorted(_SUPPORTED_AGENTS), default="generic"
    )
    init_parser.set_defaults(command_handler=_run_init_command)
