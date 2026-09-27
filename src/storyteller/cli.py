"""Command-line entry point for the storyteller harness."""

from __future__ import annotations

import argparse
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    """Build the ``st`` command-line parser."""
    parser = argparse.ArgumentParser(
        prog="st",
        description="100 TIMES AI STORYTELLER agent harness",
    )
    parser.add_subparsers(dest="command", title="commands")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
