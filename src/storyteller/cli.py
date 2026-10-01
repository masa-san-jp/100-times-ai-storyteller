"""Command-line entry point for the Phase 0 storyteller harness."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .adapter import register_auto_command
from .dev.dummy import create_dummy_orchestrator
from .input_data import PersonalInformationError
from .new_run import create_free_run, create_story_orchestrator
from .manifest import load_manifest
from .orchestrator import (
    ClaimError,
    HaltedRunError,
    InvalidClaimError,
    InvalidTransition,
    OrchestrationError,
)
from .storage import DataDirectoryError, LockTimeoutError, resolve_data_dir
from .workspace import register_cli_commands


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_TASK = 2
EXIT_INVALID_CLAIM = 3
EXIT_REJECTED = 5
EXIT_HALTED = 6
EXIT_INTERNAL = 10
EXIT_PERSONAL_INFORMATION = 4
_RUN_ID_PATTERN = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
_EXECUTOR_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class CliArgumentParser(argparse.ArgumentParser):
    """Use the CLI contract's code 1 for invalid arguments."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        print(f"st: error: {message}", file=sys.stderr)
        raise CliArgumentError(message)


class CliArgumentError(ValueError):
    """An argparse error represented by the documented code 1."""


def _configure_stdio() -> None:
    """Make CLI output independent of the host's default text encoding."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", newline="\n")
        except (AttributeError, OSError, TypeError, ValueError):
            # pytest and embedders may provide a stream without a reconfigurable
            # text layer.  Keep using that stream rather than failing at startup.
            continue


def _add_phase0_commands(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="command", title="commands")
    register_auto_command(subparsers)

    next_parser = subparsers.add_parser("next", help="claim and print one task card")
    next_parser.add_argument("--run", dest="run_id")
    next_parser.add_argument("--executor-id")
    next_parser.add_argument("--wait", type=int, default=0)
    next_parser.add_argument("--json", action="store_true", dest="as_json")

    submit_parser = subparsers.add_parser("submit", help="submit task output")
    submit_parser.add_argument("ticket")
    submit_parser.add_argument("path", nargs="?", default="-")
    submit_parser.add_argument("--truncated", action="store_true")

    status_parser = subparsers.add_parser("status", help="show run status")
    status_scope = status_parser.add_mutually_exclusive_group()
    status_scope.add_argument("--run", dest="run_id")
    # --batch is intentionally not registered until Phase 2.
    status_parser.add_argument("--json", action="store_true", dest="as_json")

    retry_parser = subparsers.add_parser("retry", help="retry one failed task")
    retry_parser.add_argument("task_id")

    resume_parser = subparsers.add_parser("resume", help="resume a halted run")
    resume_parser.add_argument("--accept-harness-change", dest="run_id", required=True)

    dev_parser = subparsers.add_parser("dev", help="development commands")
    dev_subparsers = dev_parser.add_subparsers(dest="dev_command", title="commands")
    dummy_parser = dev_subparsers.add_parser(
        "new-dummy", help="create a run from the bundled dummy harness"
    )
    dummy_parser.add_argument("--seed", type=int)

    register_cli_commands(subparsers)

    new_parser = subparsers.add_parser("new", help="create a story run")
    input_group = new_parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--narrative")
    input_group.add_argument("--free")
    new_parser.add_argument("--scale", required=True)
    new_parser.add_argument("--axis", action="append", default=[])
    new_parser.add_argument("--parts", type=int)
    new_parser.add_argument("--count", type=int)
    new_parser.add_argument("--seed", type=int)
    new_parser.add_argument("--plot-type")


def build_parser() -> argparse.ArgumentParser:
    """Build the ``st`` command-line parser."""
    parser = CliArgumentParser(
        prog="st",
        description="100 TIMES AI STORYTELLER agent harness",
    )
    _add_phase0_commands(parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface."""
    _configure_stdio()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.command is None:
            parser.print_help()
            return EXIT_OK
        if args.command == "dev" and args.dev_command is None:
            raise CliArgumentError("dev のサブコマンドが必要です")
        return _run_command(args)
    except CliArgumentError as error:
        _print_error(error)
        return EXIT_ERROR
    except HaltedRunError as error:
        _print_error(error)
        return EXIT_HALTED
    except InvalidClaimError as error:
        _print_error(error)
        return EXIT_INVALID_CLAIM
    except PersonalInformationError as error:
        _print_error(error)
        return EXIT_PERSONAL_INFORMATION
    except LockTimeoutError as error:
        _print_error(error)
        return EXIT_INTERNAL
    except (DataDirectoryError, InvalidTransition, ClaimError, OrchestrationError,
            FileNotFoundError, ValueError, OSError, UnicodeError) as error:
        _print_error(error)
        return EXIT_ERROR
    except Exception as error:  # pragma: no cover - last-resort CLI boundary
        _print_error(error)
        return EXIT_INTERNAL


def _run_command(args: argparse.Namespace) -> int:
    command_handler = getattr(args, "_command_handler", None)
    if command_handler is None:
        command_handler = getattr(args, "command_handler", None)
    if command_handler is not None:
        return command_handler(args)
    if args.command == "next":
        return _next(args)
    if args.command == "submit":
        return _submit(args)
    if args.command == "status":
        return _status(args)
    if args.command == "retry":
        return _retry(args)
    if args.command == "resume":
        return _resume(args)
    if args.command == "new":
        return _new(args)
    if args.command == "dev" and args.dev_command == "new-dummy":
        return _new_dummy(args)
    raise CliArgumentError("command is required")


def _data_dir() -> Path:
    return resolve_data_dir()


def _orchestrator(data_dir: Path, run_id: str | None = None):
    """Build the orchestrator recorded by one run's manifest."""
    if run_id is None:
        raise InvalidClaimError("ticket を含む claim が見つかりません")
    harness_kind = _load_manifest(data_dir, run_id)["harness_kind"]
    if harness_kind == "story":
        return create_story_orchestrator(data_dir)
    if harness_kind == "dummy":
        return create_dummy_orchestrator(data_dir)
    raise ValueError(f"未知の harness_kind です: {harness_kind!r}")


def _candidate_run_ids(data_dir: Path) -> list[str]:
    """Return active/stalled runs in the durable creation order."""
    runs_dir = data_dir / "runs"
    if not runs_dir.is_dir():
        return []
    candidates: list[tuple[str, str]] = []
    for entry in runs_dir.iterdir():
        if not entry.is_dir():
            continue
        try:
            manifest = _load_manifest(data_dir, entry.name)
        except (FileNotFoundError, OSError, ValueError):
            continue
        if manifest["status"] in {"active", "stalled"}:
            candidates.append((manifest["created_at"], manifest["run_id"]))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [run_id for _, run_id in candidates]


def _orchestrator_for_ticket(data_dir: Path, ticket: str):
    runs_dir = data_dir / "runs"
    if runs_dir.is_dir():
        for entry in runs_dir.iterdir():
            if not entry.is_dir():
                continue
            try:
                manifest = load_manifest(entry / "manifest.json")
            except (FileNotFoundError, OSError, ValueError):
                continue
            if any(
                (task.get("claim") or {}).get("ticket") == ticket
                for task in manifest["tasks"].values()
            ):
                return _orchestrator(data_dir, manifest["run_id"])
    # Keep the single-run seam available to embedders that provide their own
    # orchestrator; the built-in implementation raises InvalidClaimError
    # without making a harness guess when no manifest exists.
    if not runs_dir.is_dir():
        return _orchestrator(data_dir)
    raise InvalidClaimError(f"ticket を含む claim が見つかりません: {ticket}")


def _workspace_settings() -> tuple[str | None, str]:
    """Read executor settings when invoked from an executor workspace."""
    config_path = Path.cwd() / ".storyteller-workspace.yaml"
    if not config_path.exists():
        return None, "none"
    from .validation import load_and_validate_yaml

    schema_path = Path(__file__).resolve().parents[2] / "schemas" / "workspace.schema.json"
    config = load_and_validate_yaml(config_path, schema_path)
    return str(config["executor_id"]), str(config["isolation"])


def _next(args: argparse.Namespace) -> int:
    if args.wait < 0:
        raise CliArgumentError("--wait は0以上の整数で指定してください")
    if args.executor_id is not None and not _EXECUTOR_ID_PATTERN.fullmatch(args.executor_id):
        raise CliArgumentError("--executor-id が不正です")
    data_dir = _data_dir()
    configured_executor, isolation = _workspace_settings()
    executor_id = (
        args.executor_id if args.executor_id is not None else configured_executor
    )
    deadline = time.monotonic() + args.wait
    while True:
        run_ids = [args.run_id] if args.run_id is not None else _candidate_run_ids(data_dir)
        for run_id in run_ids:
            orchestrator = _orchestrator(data_dir, run_id)
            result = orchestrator.claim_next(
                run_id,
                executor_id=executor_id,
                isolation=isolation,
            )
            if result is None:
                continue
            if args.as_json:
                _print_json(result)
            else:
                print(result["card"], end="" if result["card"].endswith("\n") else "\n")
            return EXIT_OK
        if args.wait == 0 or time.monotonic() >= deadline:
            return EXIT_NO_TASK
        time.sleep(min(5.0, max(0.0, deadline - time.monotonic())))


def _read_submit_input(path: str) -> str:
    if path == "-":
        buffer = getattr(sys.stdin, "buffer", None)
        if buffer is not None:
            return buffer.read().decode("utf-8")
        # In-process callers and test doubles may expose only a text stream.
        return sys.stdin.read()
    with Path(path).open("r", encoding="utf-8", newline=None) as stream:
        return stream.read()


def _submit(args: argparse.Namespace) -> int:
    data_dir = _data_dir()
    raw_output = _read_submit_input(args.path)
    orchestrator = _orchestrator_for_ticket(data_dir, args.ticket)
    result = orchestrator.submit(
        args.ticket,
        raw_output,
        truncated=args.truncated,
    )
    if result.accepted:
        manifest = orchestrator.load_run(result.run_id)
        task = manifest["tasks"][result.task_id]
        if task["state"] == "ready" and task["continuation_step"] > 0:
            print("continued")
        else:
            print("accepted")
        return EXIT_OK
    reason = "；".join(result.errors) or "提出が検証に不合格"
    print(f"rejected: {reason}")
    return EXIT_REJECTED


def _status(args: argparse.Namespace) -> int:
    data_dir = _data_dir()
    if args.run_id is not None:
        payload = _run_status(data_dir, args.run_id)
    else:
        payload = {"runs": _all_run_statuses(data_dir)}
    if args.as_json:
        _print_json(payload)
    else:
        _print_human_status(payload)
    return EXIT_OK


def _load_manifest(data_dir: Path, run_id: str) -> dict[str, Any]:
    if not isinstance(run_id, str) or not _RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError(f"invalid run_id: {run_id!r}")
    return load_manifest(data_dir / "runs" / run_id / "manifest.json")


def _all_run_statuses(data_dir: Path) -> list[dict[str, Any]]:
    runs_dir = data_dir / "runs"
    if not runs_dir.is_dir():
        return []
    manifests: list[dict[str, Any]] = []
    for entry in runs_dir.iterdir():
        if not entry.is_dir():
            continue
        try:
            manifest = _load_manifest(data_dir, entry.name)
        except (ValueError, OSError):
            continue
        counts = {state: 0 for state in _TASK_STATES}
        for task in manifest["tasks"].values():
            counts[task["state"]] += 1
        manifests.append(
            {
                "run_id": manifest["run_id"],
                "status": manifest["status"],
                "counts": counts,
                "created_at": manifest["created_at"],
            }
        )
    manifests.sort(key=lambda item: (item["created_at"], item["run_id"]))
    return [
        {key: value for key, value in item.items() if key != "created_at"}
        for item in manifests
    ]


_TASK_STATES = ("blocked", "ready", "claimed", "done", "failed", "skipped")


def _run_status(data_dir: Path, run_id: str) -> dict[str, Any]:
    manifest = _load_manifest(data_dir, run_id)
    tasks = []
    for task_id in sorted(manifest["tasks"]):
        task = manifest["tasks"][task_id]
        claim = task.get("claim")
        tasks.append(
            {
                "task_id": task_id,
                "type": task["type"],
                "state": task["state"],
                "tries": task["tries"],
                "invalidations": task["invalidations"],
                "executor_id": claim["executor_id"] if claim else None,
                "isolation": claim["isolation"] if claim else None,
                "error": task["error"],
            }
        )
    return {
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "warnings": list(manifest["warnings"]),
        "tasks": tasks,
    }


def _retry(args: argparse.Namespace) -> int:
    data_dir = _data_dir()
    matches: list[str] = []
    runs_dir = data_dir / "runs"
    if runs_dir.is_dir():
        for entry in runs_dir.iterdir():
            if not entry.is_dir():
                continue
            try:
                manifest = _load_manifest(data_dir, entry.name)
            except (ValueError, OSError):
                continue
            if args.task_id in manifest["tasks"]:
                matches.append(manifest["run_id"])
    if len(matches) != 1:
        raise ValueError(f"対象タスクを一意に特定できません: {args.task_id}")
    _orchestrator(data_dir, matches[0]).retry_failed(matches[0], args.task_id)
    return EXIT_OK


def _resume(args: argparse.Namespace) -> int:
    data_dir = _data_dir()
    _orchestrator(data_dir, args.run_id).resume_harness_change(args.run_id)
    return EXIT_OK


def _new_dummy(args: argparse.Namespace) -> int:
    data_dir = _data_dir()
    run_id = create_dummy_orchestrator(data_dir).create_run(
        task_specs=[{"task_id": "D1.items", "type": "D1.items"}],
        seed=args.seed,
    )
    print(run_id)
    return EXIT_OK


def _new(args: argparse.Namespace) -> int:
    if args.narrative is not None:
        raise CliArgumentError("P1-02 では --free のみ対応しています")
    if args.scale not in {"vignette", "short"}:
        raise CliArgumentError("この規模プリセットは Phase 2 で対応します")
    if args.count not in {None, 1}:
        raise CliArgumentError("--count は Phase 2 で対応します")
    overrides: dict[str, str] = {}
    for raw_override in args.axis:
        if "=" not in raw_override:
            raise CliArgumentError("--axis は NAME=VALUE で指定してください")
        name, value = raw_override.split("=", 1)
        if not name or not value or name in overrides:
            raise CliArgumentError("--axis は異なる NAME=VALUE を指定してください")
        overrides[name] = value
    repository_root = Path(__file__).resolve().parents[2]
    run_id = create_free_run(
        _data_dir(),
        args.free,
        preset=args.scale,
        axis_overrides=overrides,
        seed=args.seed,
        parts=args.parts,
        plot_type=args.plot_type,
        repository_root=repository_root,
    )
    print(run_id)
    return EXIT_OK


def _print_human_status(payload: dict[str, Any]) -> None:
    if "runs" in payload:
        print("RUN_ID\tSTATUS\tBLOCKED\tREADY\tCLAIMED\tDONE\tFAILED\tSKIPPED")
        for run in payload["runs"]:
            counts = run["counts"]
            print(
                "\t".join(
                    [run["run_id"], run["status"]]
                    + [str(counts[state]) for state in _TASK_STATES]
                )
            )
        return
    print(f"run\t{payload['run_id']}\t{payload['status']}")
    if payload["warnings"]:
        print("warnings\t" + " | ".join(payload["warnings"]))
    print("TASK_ID\tTYPE\tSTATE\tTRIES\tINVALIDATIONS\tEXECUTOR\tISOLATION\tERROR")
    for task in payload["tasks"]:
        print(
            "\t".join(
                [
                    task["task_id"], task["type"], task["state"],
                    str(task["tries"]), str(task["invalidations"]),
                    task["executor_id"] or "-", task["isolation"] or "-",
                    task["error"] or "-",
                ]
            )
        )


def _print_error(error: BaseException) -> None:
    print(f"st: error: {error}", file=sys.stderr)


def _print_json(value: Any) -> None:
    """Print one valid, UTF-8-friendly JSON document for CLI consumers."""
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    raise SystemExit(main())
