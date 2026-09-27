# spec: データ配置

- 所有範囲：リポジトリのディレクトリ構成、データディレクトリの構成、manifest、seed の派生、書き込み規則、キャッシュの削除、共有の制約

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

- `run_id` は `<作成日時 YYYYMMDD-HHMMSS>-<seed の先頭6桁の16進>` とする。
- `task_id` は `<タスク種別ID>-<添字>` とする。添字はスロット・人物・候補番号・継続番号を `-` でつなぐ（例：`S7.event-e005-c2`）。
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
| `tasks` | タスクID → 状態・試行回数・無効化の回数・キャッシュキー・実行者ID・隔離の種類・完了時刻 |
| `warnings` | 固有名詞の候補、出来事数の引き上げ、JSON の出させ方の切り替えなど |

- run の途中でハーネスのファイルの sha256 が変わったことを `st` が検出した場合、その run を `halted` にする。`halted` の run のタスクは `st next` の対象から外し、標準エラー出力に警告を出す。`st resume --accept-harness-change RUN_ID` で続行を明示した場合だけ `active` に戻す。
- バッチの manifest は、バッチの seed・要求件数・run の一覧・完了数・重複で除外した数・作成数の上限を持つ。更新は `batch.lock` を取ってから行う。

## 4. seed の派生

- バッチの seed から run の seed：`int(sha256(f"{batch_seed}:{index}").hexdigest()[:8], 16)`
- run の seed からタスクの seed：`int(sha256(f"{run_seed}:{task_id}:{attempt}").hexdigest()[:8], 16)`
- コードタスクの乱数は、必ずそのタスクの seed で初期化した `random.Random` を使う。グローバルの `random` を使わない。

## 5. 書き込み規則と共有の制約

- ファイルは、同じディレクトリの一時ファイルに書いてから `os.replace` で置き換える。
- manifest の更新は、run ごとのロックファイル（`manifest.lock`、`O_EXCL` で作成）を取ってから行う。バッチの manifest は `batch.lock`、増補テーブルへの追記は `tables.lock` を取ってから行う。どのロックも10秒で待ちを打ち切り、エラーにする。
- テキストファイルは UTF-8・改行 LF で書き出す。
- 並行実行は、同じマシン上の同じファイルシステムに限って保証する。データディレクトリをファイル同期サービス（Google Drive、Dropbox、iCloud 等）の配下に置くと、排他制御が機能しない場合がある。`st` は、データディレクトリのパスにこれらのサービスの既定のフォルダ名が含まれる場合、警告を出す。

## 6. キャッシュの削除

- キャッシュは自動では削除しない。
- `st cache prune --older-than DAYS` で、指定日数より前に作られ、かつ `active` / `stalled` の run から参照されていないものを削除する。
