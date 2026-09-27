# spec: データ配置

- 所有範囲：リポジトリのディレクトリ構成、データディレクトリの構成、manifest、seed の派生、書き込み規則とロック、キャッシュの削除、共有の制約、ハーネスの変更検出、ファイル形式（ワークスペース設定・claim）

## 1. リポジトリのディレクトリ構成

```
README.md                 # 概要・セットアップ・関連リポジトリ
AGENTS.md                 # 開発者（人・エージェント）向けの入口
CLAUDE.md                 # AGENTS.md を読み込むだけ
CONTRIBUTING.md           # 変更手順
pyproject.toml, uv.lock   # 依存関係（ADR-0002）
.python-version           # uv が使う Python のバージョン
.github/workflows/        # CI（macOS・Linux・Windows）
docs/                     # 原本（SSOT）。docs/README.md が索引。docs/guides/ はユーザー向けの手引き
tools/                    # 開発用のスクリプト（check_docs.py など）
src/storyteller/          # オーケストレータ本体。入口は `st`（pyproject の scripts）
  workspace_template/     # 実行者ワークスペースのテンプレート
harness/
  story/tasks/*.yaml      # 物語生成ハーネスのタスク定義
  format/tasks/*.yaml     # 様式化ハーネスのタスク定義
tables/*.yaml             # 既定テーブル（要素の既定値は tables/elements/<axis>.yaml）
formats/<name>/           # 様式プロファイル（profile.yaml）とテンプレート
config/models.yaml        # LLMアダプタのモデル設定
schemas/                  # JSON Schema（YAML ファイルの種類ごと、タスク出力、正本、manifest）
tests/                    # テスト
examples/                 # 公開してよい生成例（ユーザーの入力を含まないもの）
private/                  # 既定のデータディレクトリ。.gitignore で除外する
```

## 2. データディレクトリ

データディレクトリは、ユーザーの入力と生成物を置く場所である。場所の決め方は [cli.md](cli.md) 冒頭に従う。

```
<STORYTELLER_HOME>/
  inputs/
    narratives/*.json             # ナラティブ
    free/*.md                     # 自由入力
  tables/<axis>.json              # 増補テーブル（S2 の対極要素の追記先。追記のみ）
  tables.lock                     # 増補テーブルへの追記のロック
  adapters/state.json             # LLMアダプタが実行中に変更したモデルごとの設定
  cache/<key[0:2]>/<key>.json     # タスク出力のキャッシュ（task-model.md §9）
  batches/<batch_id>/
    manifest.json
    batch.lock                    # バッチの manifest の更新のロック
    report/                       # 傾向分析（spec/report.md）。run と同じ構成の manifest.json と tasks/ を持つ
      report.md, report.json      # 分析の出力
  runs/<run_id>/
    manifest.json
    input.json                    # S0 で正規化した入力
    tasks/<task_id>/
      card.md                     # 実行者に渡したタスクカード
      input.json                  # カードに埋め込んだ入力
      claim.json                  # claim 中のみ存在
      output.json | output.md     # 合格した出力
      attempts/<n>.json           # 不合格の試行（出力・理由・実行者ID・時刻）
    story/story.json, story.md    # 正本
    formats/<name>/…              # 様式化の出力
```

- `run_id` は `<作成日時（UTC） YYYYMMDD-HHMMSS>-<seed を8桁の小文字16進にした先頭6桁>` とする。
- `task_id` は `<タスク種別ID>`、または `<タスク種別ID>-<添字>` とする。添字は、スロットや人物のID、候補番号（`c1`〜）を `-` でつなぐ（例：`S7.event-e005-c2`）。継続は同じタスクの中で扱い、task_id を分けない（[task-model.md](task-model.md) §8）。
- コードタスクもタスクディレクトリを持ち、出力を `output.json` に置く（`card.md` と `claim.json` は持たない）。spec 中の `assignment.json`・`slots.json` は、それぞれ S3・S6 のタスクの `output.json` を指す。
- ユーザーの入力と生成物を、データディレクトリ以外に書き出さない。

## 3. manifest

`runs/<run_id>/manifest.json` の項目（JSON Schema は `schemas/manifest.schema.json`）：

| 項目 | 内容 |
|---|---|
| `schema_version` | manifest 形式の版 |
| `run_id`, `batch_id` | 識別子 |
| `created_at`, `updated_at` | 時刻（ISO 8601、UTC） |
| `status` | `active` / `stalled` / `halted`（ハーネスの変更で停止中）/ `completed` / `duplicate` |
| `seed`, `seed_source` | run の seed と、その由来（`argument` / `batch` / `generated`） |
| `input` | 入力の種類（`narrative` / `free`）と、入力ファイルの sha256 |
| `scale` | 規模プリセット、上書きした軸、計算した派生値（[scale.md](scale.md) §6） |
| `harness` | リポジトリ内のタスク定義・既定テーブル・スキーマ・様式プロファイル各ファイルの sha256。データディレクトリの増補テーブルは含めない |
| `table_snapshot` | S3 の実行時点の、増補テーブルの軸ごとの行数。増補テーブルは追記のみなので、この行数までを読めば、同じ割当を再現できる |
| `tasks` | タスクID → タスクの記録（§3.1） |
| `warnings` | 固有名詞の候補、出来事数の引き上げ、JSON の出させ方の切り替えなど |

### 3.1 タスクの記録

| 項目 | 型 | 内容 |
|---|---|---|
| `type` | 文字列 | タスク種別ID |
| `kind` | `code` / `llm` | |
| `state` | 状態（[task-model.md](task-model.md) §4.2） | |
| `deps` | 文字列の配列 | 依存するタスクID |
| `index` | 文字列の配列 | task_id の添字の要素（`{slot}` の置き換えに使う） |
| `attempt` | 整数（初期値0） | seed とキャッシュキーの計算に使う。不合格・無効化のたびに1増え、戻らない |
| `tries` | 整数（初期値0） | 不合格の回数。`st retry` で0に戻る |
| `invalidations` | 整数（初期値0） | 無効化の回数。`st retry` で0に戻る |
| `continuation_step` | 整数（初期値0） | [task-model.md](task-model.md) §8 |
| `cache_key` | 文字列または null | |
| `claim` | オブジェクトまたは null | claim 中の `ticket`・`executor_id`・`isolation`・`lease_expires_at` |
| `history` | 配列 | 状態遷移の記録（時刻・遷移・理由・実行者ID） |
| `error` | 文字列または null | `failed` の理由 |

- run の manifest は、§3 の表の項目をすべて必須とする（値がないものは null）。型は、`schema_version`・`seed` が整数、`created_at`・`updated_at` が §7.2 と同じ時刻の形式、`input`・`scale`・`harness`・`table_snapshot`・`tasks` がオブジェクト、`warnings` が文字列の配列、その他が文字列とする。表にない項目はエラーとする（`additionalProperties: false`）。`scale` と `table_snapshot` の内部の形式は、それを使う作業項目（P1-02、P1-05）で定める。Phase 0 では `input`・`scale` は空のオブジェクトでよい。
- タスクの記録も、§3.1 の表の項目をすべて必須とし、表にない項目はエラーとする。

形式は `schemas/manifest.schema.json` で検証する。バッチの manifest の形式は Phase 2（P2-05）で `schemas/batch-manifest.schema.json` として定める。

### 3.2 ハーネスの変更検出

- run の作成時に、リポジトリの `harness/`・`tables/`・`schemas/`・`formats/` の配下にあるすべてのファイルについて、相対パスと sha256 の組を manifest の `harness` に記録する。`config/` とデータディレクトリの増補テーブルは含めない。
- `st next` と `st submit` は、対象の run について、記録と現在のファイルの組を比べる。ファイルの追加・削除・内容の変更は、すべて変更とみなす。
- run の途中でハーネスのファイルの sha256 が変わったことを `st` が検出した場合、その run を `halted` にする。`halted` の run のタスクは `st next` の対象から外し、標準エラー出力に警告を出す。`st resume --accept-harness-change RUN_ID` で続行を明示した場合だけ、manifest の `harness` を現在の値に更新し、run の状態を [task-model.md](task-model.md) §4.2 の優先順で決め直す。既存の claim は有効のまま残す。
- バッチの manifest は、バッチの seed・要求件数・run の一覧・完了数・重複で除外した数・作成数の上限を持つ。更新は `batch.lock` を取ってから行う。

## 4. seed の派生

- バッチの seed から run の seed：`int(sha256(f"{batch_seed}:{index}").hexdigest()[:8], 16)`
- run の seed からタスクの seed：`int(sha256(f"{run_seed}:{task_id}:{attempt}").hexdigest()[:8], 16)`（`attempt` は §3.1）
- `--seed` を指定しない場合、run の seed は `secrets.randbits(32)` で作る。
- コードタスクの乱数は、必ずそのタスクの seed で初期化した `random.Random` を使う。グローバルの `random` を使わない。

## 5. 書き込み規則と共有の制約

- ファイルは、同じディレクトリの一時ファイル（`.<ファイル名>.<ランダムな16進8桁>.tmp`）に書き、flush と `os.fsync` をしてから `os.replace` で置き換える。途中で例外が起きた場合は一時ファイルを削除し、元のファイルを変更しない。
- manifest の更新は、run ごとのロックファイル（`manifest.lock`、`O_EXCL` で作成）を取ってから行う。バッチの manifest は `batch.lock`、増補テーブルへの追記は `tables.lock` を取ってから行う。ロックファイルには、プロセスID・ホスト名・作成時刻を書く。取得できない場合は 0.1秒間隔で再試行し、10秒で打ち切って終了コード 10 とする。作成から30秒を超えたロックファイルは、異常終了したプロセスが残したものとみなして削除し、取得し直す（ロック中の処理は短いため）。処理が終わったらロックファイルを削除する。
- テキストファイルは UTF-8・改行 LF で書き出す。
- 並行実行は、同じマシン上の同じファイルシステムに限って保証する。データディレクトリをファイル同期サービス（Google Drive、Dropbox、iCloud 等）の配下に置くと、排他制御が機能しない場合がある。`st` は、データディレクトリの絶対パスの要素のいずれかが、大文字小文字を区別せずに次のいずれかを含む場合、コマンドごとに1回、標準エラー出力に警告を出す：`Google Drive`、`GoogleDrive`、`My Drive`、`マイドライブ`、`Dropbox`、`iCloud Drive`、`Mobile Documents`、`OneDrive`。

## 6. キャッシュの削除

- キャッシュは自動では削除しない。
- `st cache prune --older-than DAYS` で、指定日数より前に作られ、かつ `active` / `stalled` の run から参照されていないものを削除する。

## 7. ファイル形式

### 7.1 実行者ワークスペースの設定 `.storyteller-workspace.yaml`

| キー | 必須 | 型・値 |
|---|---|---|
| `data_dir` | 必須 | データディレクトリの絶対パス |
| `executor_id` | 必須 | `^[A-Za-z0-9._-]{1,64}$` |
| `agent` | 必須 | `claude-code` / `codex` / `generic` |
| `isolation` | 必須 | `permission` / `placement` |

形式は `schemas/workspace.schema.json` で検証する。ファイルが壊れている、必須のキーがない、`data_dir` が相対パスである場合は、終了コード 1 とする。

### 7.2 claim `claim.json`

| キー | 型・値 |
|---|---|
| `ticket` | 小文字16進32桁（`secrets.token_hex(16)`） |
| `task_id` | 文字列 |
| `executor_id` | `^[A-Za-z0-9._-]{1,64}$` |
| `isolation` | `permission` / `placement` / `adapter`（`st auto`）/ `none`（ワークスペース外からの実行） |
| `claimed_at`, `lease_expires_at` | UTC の ISO 8601、秒精度、末尾 `Z`（例：`2026-09-27T03:15:00Z`） |

### 7.3 データディレクトリの決め方

[cli.md](cli.md) 冒頭の順で決め、コマンドの開始時に絶対パスにする。リポジトリの `private/` を使うのは、`st` がリポジトリから editable で導入されている場合（パッケージの2つ上のディレクトリに `pyproject.toml` がある場合）に限る。それ以外で、設定ファイルも `STORYTELLER_HOME` もない場合は、終了コード 1 とする。
