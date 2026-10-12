#!/usr/bin/env bash
# 開発用の規模 dev の run を作り、指定の実行者で最後まで回す（ADR-0009、scale.md）。
# 所要時間と、skipped のタスク・warnings を表示する。run が completed なら終了コード 0。
#
#   tools/dev_run.sh [--seed N] [--free FILE] [--data-dir DIR] (--auto PROVIDER MODEL [ENDPOINT] | --command CMD)
#
#   --auto     st auto で回す（PROVIDER は ollama または openai-compatible）。
#   --command  CMD を sh -c で実行する。環境変数 STORYTELLER_HOME と STORYTELLER_RUN_ID を渡す。
#              コーディングエージェントなどの実行者を起動するコマンドを書く。
#   --free     自由入力のファイル。省略時は同梱の短い例を使う。
#   --data-dir データディレクトリ。省略時は STORYTELLER_HOME、なければ一時ディレクトリ。
set -euo pipefail

usage() { sed -n 2,11p "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

seed=""; free=""; data_dir=""; mode=""; provider=""; model=""; endpoint=""; command_line=""
while [ $# -gt 0 ]; do
  case "$1" in
    --seed) seed="${2:?}"; shift 2 ;;
    --free) free="${2:?}"; shift 2 ;;
    --data-dir) data_dir="${2:?}"; shift 2 ;;
    --command) mode=command; command_line="${2:?}"; shift 2 ;;
    --auto)
      mode=auto; provider="${2:?}"; model="${3:?}"; shift 3
      if [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; then endpoint="$1"; shift; fi ;;
    *) usage ;;
  esac
done
[ -n "$mode" ] || usage

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
data_dir="${data_dir:-${STORYTELLER_HOME:-$(mktemp -d)}}"
export STORYTELLER_HOME="$data_dir"
if [ -z "$free" ]; then
  free="$data_dir/dev-input.md"
  mkdir -p "$data_dir"
  printf '%s\n\n%s\n' \
    '静かな町で、失われた記録を探す。' \
    '境界を守る仕組みが、暮らしの選択を変えていく。' > "$free"
fi

new_args=(new --free "$free" --scale dev)
[ -z "$seed" ] || new_args+=(--seed "$seed")
run_id="$(uv run st "${new_args[@]}")"
export STORYTELLER_RUN_ID="$run_id"
echo "data dir: $data_dir"
echo "run: $run_id"

start=$SECONDS
if [ "$mode" = auto ]; then
  auto_args=(auto --provider "$provider" --model "$model" --run "$run_id" --until-empty)
  [ -z "$endpoint" ] || auto_args+=(--endpoint "$endpoint")
  uv run st "${auto_args[@]}" || echo "実行者が異常終了しました" >&2
else
  sh -c "$command_line" || echo "実行者が異常終了しました" >&2
fi
elapsed=$((SECONDS - start))

echo "elapsed: $((elapsed / 60))m$((elapsed % 60))s"
uv run st status --run "$run_id" --json | uv run python -c '
import json, sys
payload = json.load(sys.stdin)
skipped = [t for t in payload["tasks"] if t["state"] == "skipped"]
print("status:", payload["status"])
print("skipped:", len(skipped))
for task in skipped:
    print("  -", task["task_id"], task["error"] or "")
print("warnings:", len(payload["warnings"]))
for warning in payload["warnings"]:
    print("  -", warning)
sys.exit(0 if payload["status"] == "completed" else 1)
'
