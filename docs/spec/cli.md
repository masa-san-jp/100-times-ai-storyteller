# spec: CLI

- 所有範囲：`st` のコマンド・引数・出力・終了コード

`st` は `pyproject.toml` の `[project.scripts]` に登録する。開発者はリポジトリ内で `uv run st <command>` として、実行者は `uv tool install --editable <リポジトリのパス>` で導入した `st <command>` として実行する（[architecture.md](architecture.md) §4）。すべてのコマンドは、データディレクトリ（[data-layout.md](data-layout.md) §2）を対象にする。`st` の標準入力・標準出力・標準エラー出力は、OS の既定の文字コードによらず常に UTF-8（改行 LF）とする。データディレクトリは、カレントディレクトリの `.storyteller-workspace.yaml` → 環境変数 `STORYTELLER_HOME` → リポジトリの `private/` の順に決める。

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
| `st table list/add/check` | 人間 | 要素テーブルの一覧・増補テーブルへの行の追加・検査 | 5 |

- 各フェーズでは、そのフェーズまでに導入するコマンドだけを登録する。未導入のコマンドは `st --help` に表示しない。
- 開発用に `st dev new-dummy`（§2）を置く。

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
- `--wait SECONDS`：`ready` のタスクがないとき、指定秒数まで5秒間隔で待つ（0以上の整数。0は待たない。負の値は終了コード 1）。待っても見つからなければ終了コード 2。
- `--run` で指定した run が存在しない場合は終了コード 1、`completed` / `stalled` / `duplicate` の場合は終了コード 2 とする。
- `--executor-id` を省略したときは、実行者ワークスペースの設定、なければホスト名とプロセスIDから作る。

### `st submit`

```
st submit TICKET [PATH | -] [--truncated]
```

- 出力を、PATH（カレントディレクトリからの相対パスまたは絶対パス）、または標準入力（PATH が `-` か省略）から、UTF-8 として受け取り、検証する（[task-model.md](task-model.md) §6）。
- ticket に対応する claim が無効（lease 切れ等）の場合は終了コード 3、タスクの run が `halted` の場合は終了コード 6 を返す。
- `--truncated` は、LLMアダプタが長さによる打ち切りを報告するために使う。
- 標準出力：合格なら `accepted`、不合格なら `rejected` と理由、長文の途中として受け付けた場合は `continued`（[task-model.md](task-model.md) §8。終了コード 0。続きは次の `st next` で同じタスクの継続のカードとして渡される）。

### `st status`

```
st status [--run RUN_ID | --batch BATCH_ID] [--json]
```

- 人間向けの出力は表形式とし、形式は固定しない。`--json` の出力は次の形式とし、`schemas/status.schema.json` で検証する。
  - 引数なし：`{"runs": [{"run_id": 文字列, "status": run の状態, "counts": {6つの状態すべて: 整数（0を含む）}}]}`。runs は作成順。
  - `--run`：`{"run_id": 文字列, "status": run の状態, "warnings": [文字列], "tasks": [{"task_id": 文字列, "type": 文字列, "state": 状態, "tries": 整数, "invalidations": 整数, "executor_id": 文字列または null, "isolation": 文字列または null, "error": 文字列または null, "failure_report": 文字列または null}]}`。tasks は task_id の辞書順。`failure_report` は、`failed` または `skipped` のタスクの失敗の報告（[task-model.md](task-model.md) §6.3 の `failure.md`）の、run のディレクトリからの相対パス（`/` 区切り。例：`tasks/S4.fact-place.area/failure.md`）。それ以外、または報告がない場合は null。
  - すべての項目は必須で、表にない項目を出力しない。
  - `--batch`：`{"batch_id": 文字列, "requested": 整数または null, "completed": 整数, "duplicates": 整数, "created": 整数, "runs": [{"run_id", "status", "counts"}（引数なしの runs と同じ形式。作成順）], "problem_runs": [{"run_id": 文字列, "status": "stalled" または "halted", "warnings": [文字列]}], "failed_tasks": [{"run_id": 文字列, "task_id": 文字列, "reason": 文字列または null}]}`。`batch_id` が一致する run の manifest から作る（バッチの manifest は `requested` を読むだけ）。`completed` は `completed` の run の数、`duplicates` は `duplicate` の run の数、`created` は run の数。`requested` はバッチの manifest の `requested`（整数）で、ファイルや値がなければ null。`failed_tasks` の `reason` は manifest の `error`。run もバッチの manifest もなければ終了コード 1。
- 引数なし：run ごとの状態と、状態別のタスク数。
- `--run`：タスクごとの状態・試行回数・無効化の回数・実行者ID・隔離の種類、manifest の warnings。`failed` のタスクには失敗の報告のパスを示す。引数なしの一覧には報告のパスを示さない。
- `--batch`：バッチの要求件数・完了数・重複数・作成数、`stalled` / `halted` の run と、`failed` のタスクの理由。

### `st retry` / `st resume`

```
st retry TASK_ID        # タスクIDは st status --run で確認する
st resume --accept-harness-change RUN_ID
```

### `st dev new-dummy`

```
st dev new-dummy [--seed N]
```

- Phase 0 の検証用に、パッケージに同梱したダミーのハーネス（`src/storyteller/dev/dummy/`）から run を作る。ダミーのハーネスの内容は [phase-0.md](../plan/phase-0.md) §「ダミーのハーネス」に従う。
- 標準出力：作成した run ID。

### `st auto`

```
st auto --provider ollama|openai-compatible --model MODEL [--endpoint URL]
        [--workers N] [--run RUN_ID] [--until-empty]
```

- `st next` → アダプタ呼び出し → `st submit` を繰り返す。`--workers N` で N 並列にする。
- `--endpoint` は localhost に限る（[architecture.md](architecture.md) §5）。
- `--until-empty` のとき、`ready` のタスクがなく、lease の切れていない `claimed` のタスクもなくなったら終了する。lease の切れた claim は、run の状態（`active`・`stalled`）によらず片付けて（[task-model.md](task-model.md) §5 の無効な claim の扱い）、待つ対象に数えない。run が `stalled` で取れるタスクがない場合も、他の実行者の claim の終わりを待たずに終了する。

### `st workspace init`

```
st workspace init PATH [--data-dir DIR] [--executor-id ID] [--agent claude-code|codex|generic]
```

- PATH に実行者ワークスペースを作る（[architecture.md](architecture.md) §4）。PATH がリポジトリの内側にある場合は拒否する。
- `--data-dir` を省略したときは、`STORYTELLER_HOME`、なければリポジトリの `private/` を、絶対パスで設定ファイルに書く。
- `--agent` の既定は `generic`。`claude-code` は権限設定を書き出し、隔離の種類を `permission` とする。`codex` は書き込み範囲だけを設定し、読み取りを制限できないため、隔離の種類を `placement` とする。`generic` は権限設定を書き出さず、隔離の種類を `placement` とする。
- `--executor-id` を省略したときは、ホスト名とランダムな文字列から作る。
- 作成完了時、`claude-code` または `codex` の起動コマンドを標準出力に表示する。

### `st table`

```
st table list [AXIS]
st table add AXIS TEXT [--source SOURCE]
st table check [AXIS]
```

- 対象は要素テーブル（[story-pipeline.md](story-pipeline.md) §3）に限る。AXIS は `tables/element_axes.yaml` の軸の key で、存在しない軸は終了コード 1。AXIS を省略した `list` と `check` は、すべての軸を対象にする。
- 既定テーブル（`tables/elements/<axis>.yaml`）は読み取り専用で、`st` は書き換えない。書き込み先は増補テーブル（`<データディレクトリ>/tables/<axis>.json`、[data-layout.md](data-layout.md) §2）だけである。
- 重複の判定は、NFKC 正規化・空白除去の後の `text` の一致とする（[story-pipeline.md](story-pipeline.md) S2 と同じ）。
- `list`：AXIS を省略したときは、軸ごとに既定テーブルの行数と増補テーブルの行数を表示する。AXIS を指定したときは、増補テーブルの行を ID（`<axis>:x<行番号>`）・`text` とともに表示し、既定テーブルの行と重複する行には「既定と重複」と付ける。末尾に、既定テーブルの行数、増補テーブルの行数、そのうち既定と重複する行数を表示する。出力の形式は固定しない。
- `add`：増補テーブルの末尾に1行（`text` と、あれば `source`）を追記し、標準出力に付与した ID を出力する。追記は `tables.lock` を取ってから行う。次の場合は、何も書かずに終了コード 1 とし、理由を標準エラー出力に出す：`text` が NFKC 正規化・空白除去の後に空、`source` が空文字、正規化後の `text` が既定テーブルまたは増補テーブルの既存の行と同じ、増補テーブルが `check` の検査に通らない。
- `check`：既定テーブルが `schemas/tables/elements.schema.json` に適合すること、増補テーブルが読めて各行が有効な行であること（増補テーブルは、行の配列、または `items` に行の配列を持つオブジェクトのどちらでもよい。行は、空でない文字列〔`text` として扱う〕、または `text` が空でない文字列で `source` が省略可の空でない文字列のオブジェクト。S3 は行の未知の項目を無視するが、`check` は他の項目を持つ行を厳格に不正とする）、増補テーブルの中で重複する行と既定テーブルと重複する行がないこと、を検査する。増補テーブルがない軸は、行がないものとして扱う。問題がなければ終了コード 0。問題があれば、軸・行番号・理由を標準エラー出力に出し、終了コード 1 とする。

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
| 1 | 引数・入力・設定ファイルの誤り、実行環境の恒久的なファイル権限拒否、存在しない run・タスク、状態が対象外（例：`failed` でないタスクへの `st retry`） |
| 2 | 実行可能なタスクがない（`st next`） |
| 3 | claim が無効（lease 切れ・他の実行者が claim し直した・存在しない ticket） |
| 4 | 個人情報を検出したため、取り込みを拒否した |
| 5 | 提出した出力が検証で不合格だった（`st submit`） |
| 6 | 対象の run が `halted`（ハーネスの変更で停止中）である（`st next --run`、`st submit`） |
| 10 | 内部エラー（ロックの取得の打ち切り、manifest の不整合を含む） |

不合格を 0 以外にするのは、実行者（エージェントやスクリプト）が終了コードだけで次の行動を決められるようにするためである。
