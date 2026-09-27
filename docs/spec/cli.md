# spec: CLI

- 所有範囲：`st` のコマンド・引数・出力・終了コード

`st` は `pyproject.toml` の `[project.scripts]` に登録する。開発者はリポジトリ内で `uv run st <command>` として、実行者は `uv tool install --editable <リポジトリのパス>` で導入した `st <command>` として実行する（[architecture.md](architecture.md) §4）。すべてのコマンドは、データディレクトリ（[data-layout.md](data-layout.md) §2）を対象にする。データディレクトリは、カレントディレクトリの `.storyteller-workspace.yaml` → 環境変数 `STORYTELLER_HOME` → リポジトリの `private/` の順に決める。

## 1. コマンド一覧

| コマンド | 使う人 | 内容 | 導入フェーズ |
|---|---|---|---|
| `st next` | 実行者 | LLMタスクを1つ claim し、タスクカードを出力する | 0 |
| `st submit` | 実行者 | 出力を提出する | 0 |
| `st status` | 人間 | run・バッチ・タスクの状態を表示する（タスクIDを表示するため、実行者には使わせない） | 0 |
| `st retry` | 人間 | `failed` のタスクを `ready` に戻す | 0 |
| `st resume` | 人間 | ハーネスの変更を受け入れて run を再開する | 0 |
| `st new` | 人間 | run（またはバッチ）を作る | 1 |
| `st auto` | 人間 | LLMアダプタで、タスクを無人で実行し続ける | 1 |
| `st workspace init` | 人間 | リポジトリ外に実行者ワークスペースを作る | 1 |
| `st check-input` | 人間 | 入力ファイルの個人情報検出だけを行う | 2 |
| `st format` | 人間 | 正本から様式化を開始する | 3 |
| `st cache prune` | 人間 | 古いキャッシュを削除する | 4 |
| `st report` | 人間 | バッチの傾向分析を開始する | 4 |

Phase 0 では、ダミーの DAG を作るための `st dev new-dummy` を開発用に置く。

## 2. 各コマンド

### `st new`

```
st new (--narrative PATH | --free PATH)
       --scale PRESET [--axis NAME=VALUE ...] [--parts N]
       [--count N] [--seed N] [--plot-type ID]
```

- `--narrative` と `--free` のどちらか一方が必須。入力の形式は [input.md](input.md)。
- `--scale`・`--axis`・`--parts` は [scale.md](scale.md)。
- `--count N` を指定するとバッチを作り、N 本の run を作る。
- `--plot-type` はプロット型を固定する。指定がなければ S3 がコードで選ぶ。
- 標準出力：作成した run ID（バッチの場合はバッチ ID）。

### `st next`

```
st next [--run RUN_ID] [--executor-id ID] [--wait SECONDS] [--json]
```

- `ready` のLLMタスクを1つ claim し、タスクカードを標準出力に出す。カードの見出しに ticket が書かれている。
- `--json` のときは `{"ticket", "card", "lease_expires_at"}` を出力する。タスクIDは出力しない（[task-model.md](task-model.md) §3）。
- `--run` で指定した run が `halted` の場合は、終了コード 6 を返す。
- `--wait SECONDS`：`ready` のタスクがないとき、指定秒数まで5秒間隔で待つ。
- `--executor-id` を省略したときは、実行者ワークスペースの設定、なければホスト名とプロセスIDから作る。

### `st submit`

```
st submit TICKET [PATH | -] [--truncated]
```

- 出力をファイルまたは標準入力から受け取り、検証する（[task-model.md](task-model.md) §6）。
- ticket に対応する claim が無効（lease 切れ等）の場合は終了コード 3、タスクの run が `halted` の場合は終了コード 6 を返す。
- `--truncated` は、LLMアダプタが長さによる打ち切りを報告するために使う。
- 標準出力：合格なら `accepted`、不合格なら `rejected` と理由。

### `st status`

```
st status [--run RUN_ID | --batch BATCH_ID] [--json]
```

- 引数なし：run ごとの状態と、状態別のタスク数。
- `--run`：タスクごとの状態・試行回数・無効化の回数・実行者ID・隔離の種類、manifest の warnings。
- `--batch`：バッチの要求件数・完了数・重複数・作成数、`stalled` / `halted` の run と、`failed` のタスクの理由。

### `st retry` / `st resume`

```
st retry TASK_ID        # タスクIDは st status --run で確認する
st resume --accept-harness-change RUN_ID
```

### `st auto`

```
st auto --provider ollama|openai-compatible --model MODEL [--endpoint URL]
        [--workers N] [--run RUN_ID] [--until-empty]
```

- `st next` → アダプタ呼び出し → `st submit` を繰り返す。`--workers N` で N 並列にする。
- `--endpoint` は localhost に限る（[architecture.md](architecture.md) §5）。
- `--until-empty` のとき、`ready` のタスクがなく、`claimed` のタスクもなくなったら終了する。

### `st workspace init`

```
st workspace init PATH [--data-dir DIR] [--executor-id ID] [--agent claude-code|codex|generic]
```

- PATH に実行者ワークスペースを作る（[architecture.md](architecture.md) §4）。PATH がリポジトリの内側にある場合は拒否する。
- `--data-dir` を省略したときは、`STORYTELLER_HOME`、なければリポジトリの `private/` を、絶対パスで設定ファイルに書く。
- `--agent` の既定は `generic`。`claude-code` と `codex` は権限設定を書き出し、隔離の種類を `permission` とする。`generic` は権限設定を書き出さず、隔離の種類を `placement` とする。
- `--executor-id` を省略したときは、ホスト名とランダムな文字列から作る。

### `st check-input` / `st format` / `st cache prune` / `st report`

```
st check-input PATH
st format --run RUN_ID --as FORMAT_NAME     # run を active に戻し、様式化のタスクを追加する
st cache prune --older-than DAYS
st report --batch BATCH_ID
```

## 3. 終了コード

| コード | 意味 |
|---|---|
| 0 | 成功（`st submit` では合格） |
| 1 | 引数・入力の誤り |
| 2 | 実行可能なタスクがない（`st next`） |
| 3 | claim が無効（lease 切れ・他の実行者が claim し直した・存在しない ticket） |
| 4 | 個人情報を検出したため、取り込みを拒否した |
| 5 | 提出した出力が検証で不合格だった（`st submit`） |
| 6 | 対象の run が `halted`（ハーネスの変更で停止中）である（`st next --run`、`st submit`） |
| 10 | 内部エラー |

不合格を 0 以外にするのは、実行者（エージェントやスクリプト）が終了コードだけで次の行動を決められるようにするためである。
