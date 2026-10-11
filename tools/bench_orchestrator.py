"""Measure claim_next / submit cost on a large run (P1-36).

Usage: uv run python tools/bench_orchestrator.py [--tasks 1500] [--rounds 5] [--profile]
Builds a throwaway run in a temporary directory and prints the time of one
claim_next and one submit, plus (with --profile) a cumulative-time breakdown
(load / validate / write / advance and so on).
"""

from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from storyteller.orchestrator import Orchestrator, TaskSpec  # noqa: E402


def llm_definition(task_id: str) -> dict:
    return {
        "id": task_id,
        "version": 1,
        "kind": "llm",
        "element": "text",
        "output": "json",
        "card": {
            "role": "与えられた入力を確認する。",
            "steps": ["JSONを出力する。"],
            "output_example": '{"value": "..."}',
        },
        "lease_minutes": 30,
    }


def code_definition(task_id: str) -> dict:
    return {"id": task_id, "version": 1, "kind": "code", "handler": "done"}


def build(data_dir: Path, tasks: int) -> tuple[Orchestrator, str]:
    definitions = {"D1.done": code_definition("D1.done"), "D1.work": llm_definition("D1.work")}
    specs = [TaskSpec("D1.done", "D1.done")]
    specs += [
        TaskSpec(
            f"D1.work-{index:04d}", "D1.work", deps=("D1.done",), index=(f"{index:04d}",)
        )
        for index in range(tasks - 1)
    ]
    orchestrator = Orchestrator(
        data_dir, definitions, {"done": lambda context: {"ok": True}}
    )
    return orchestrator, orchestrator.create_run(seed=1, tasks=specs)


def timed(function):
    start = time.perf_counter()
    result = function()
    return time.perf_counter() - start, result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=int, default=1500)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--sort", default="cumulative", help="pstats sort key")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        orchestrator, run_id = build(Path(tmp), args.tasks)
        warm = orchestrator.claim_next(run_id)  # warm the schema cache
        orchestrator.submit(warm["ticket"], '{"value": "x"}')
        claims: list[float] = []
        submits: list[float] = []
        profiler = cProfile.Profile() if args.profile else None
        for _ in range(args.rounds):
            if profiler:
                profiler.enable()
            claim_time, claim = timed(lambda: orchestrator.claim_next(run_id))
            ticket = claim["ticket"]
            submit_time, _ = timed(
                lambda: orchestrator.submit(ticket, '{"value": "x"}')
            )
            if profiler:
                profiler.disable()
            claims.append(claim_time)
            submits.append(submit_time)
        print(f"tasks={args.tasks} rounds={args.rounds}")
        print(f"claim_next: median {statistics.median(claims):.3f}s max {max(claims):.3f}s")
        print(f"submit:     median {statistics.median(submits):.3f}s max {max(submits):.3f}s")
        if profiler:
            out = io.StringIO()
            pstats.Stats(profiler, stream=out).sort_stats(args.sort).print_stats(30)
            print(out.getvalue())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
