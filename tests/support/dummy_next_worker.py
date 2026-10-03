from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from dummy_clock import st_command


def main() -> int:
    start_signal = Path(sys.argv[1])
    result_path = Path(sys.argv[2])
    data_dir = sys.argv[3]
    run_id = sys.argv[4]
    while not start_signal.is_file():
        time.sleep(0.01)
    command = st_command(datetime.fromisoformat(sys.argv[5]))
    environment = os.environ.copy()
    environment["STORYTELLER_HOME"] = data_dir
    result = subprocess.run(
        [*command, "next", "--run", run_id, "--json"],
        text=True,
        encoding="utf-8",
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        check=False,
        timeout=30,
    )
    result_path.write_text(
        json.dumps(
            {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
