# Phase 0 計画：基盤

- 所有範囲：Phase 0 の作業項目、依存関係、受け入れ条件
- 目的・完了条件：[ROADMAP.md](ROADMAP.md) Phase 0

1項目 = 1つのプルリクエストを目安とする。各項目は、受け入れ条件をテストで確認できる状態で完了とする。

## 共通の受け入れ条件

- 根拠の spec と実装が一致している。実装中に spec の変更が必要になった場合は、同じプルリクエストで spec を先に更新している。
- 作業でデータファイル（テーブル・タスク定義・スキーマ）を作成した場合、spec に書かれていた同じ値を削除し、そのファイルへのリンクに置き換えている（[docs/README.md](../README.md) §2.1）。
- `uv run pytest` と `uv run python tools/check_docs.py` が通り、CI が3つの OS で成功している。

## 作業項目

| ID | 作業 | 依存 | 根拠 | 受け入れ条件 |
|---|---|---|---|---|
| P0-01 | 骨格：`pyproject.toml`（`st` の登録、PyYAML・jsonschema・pytest）、`uv.lock`、`.python-version`、`src/storyteller/`、`tests/`、`tools/`、`.gitignore` | — | ADR-0002, data-layout §1 | `uv sync` → `uv run st --help` → `uv run pytest` が通る。`pip install -e .` と `uv tool install --editable .` でも導入できる |
| P0-02 | CI：GitHub Actions で macOS・Linux・Windows × Python 3.11 / 3.13 のテスト | P0-01 | ADR-0002 | 3 OS で CI が成功する |
| P0-03 | 文書の検査 `tools/check_docs.py`：相対リンクの解決、ADR の見出し（状態・日付）、docs/README の所有範囲の表にある文書の実在（「（Px-yy で作成）」と書かれた行は、その作業項目が完了するまで対象外）。CI に組み込む | P0-02 | docs/README | 意図的に壊したリンクを検出する |
| P0-04 | データディレクトリの解決（`.storyteller-workspace.yaml` → `STORYTELLER_HOME` → `private/`）、原子的な書き込み、UTF-8/LF、同期フォルダの警告、ロック（manifest・batch・tables） | P0-01 | cli 冒頭, data-layout §2, §5 | 書き込み途中の例外で既存ファイルが壊れない。同期フォルダ名を含むパスで警告が出る。ロックが10秒で打ち切られる |
| P0-05 | YAML の読み込みと JSON Schema 検証の共通処理、タスク定義のスキーマ `schemas/task-definition.schema.json` | P0-01 | ADR-0003, task-model §2 | 必須項目の欠け、`no` の真偽値化などの型誤りがエラーになる |
| P0-06 | タスクカードの生成：固定の見出し、ticket、入力の埋め込み、`truncate`、文字数予算、再試行時の理由の記載、selector の実装 | P0-05 | task-model §2, §3 | 予算超過・required の切り詰め不能がエラーになる。カードにタスクID・run・段階の情報が含まれない |
| P0-07 | manifest（`schemas/manifest.schema.json`）と seed の派生 | P0-04 | data-layout §3, §4 | 同じ seed から同じタスク seed が得られる |
| P0-08 | DAG、タスクの状態遷移（`skipped` を含む）、コードタスクの即時実行、動的なタスクの追加、run の完了判定 | P0-06, P0-07 | architecture §2, task-model §4 | ダミーDAGで blocked → ready → claimed → done と遷移し、done と skipped だけになった run が completed になる |
| P0-09 | claim / lease：`O_EXCL`、ticket、期限切れの改名と再 claim、無効な claim による提出の拒否、隔離の種類の記録 | P0-08 | task-model §5 | 2プロセス同時の `st next` で二重 claim が起きない。期限切れ後に別の実行者が claim できる |
| P0-10 | 検証：JSON の救済、jsonschema、チェック（`sources_exist`, `max_chars`, `min_chars`, `count`, `ids_subset`, `uses_given`, `ends_complete`, `no_new_proper_nouns`） | P0-05 | task-model §6 | 各チェックの合格例・不合格例のテスト |
| P0-11 | 再試行と `failed`・`stalled`、無効化（依存タスクの差し戻しと claim の取り消しを含む）、`st retry`（回数のリセット）、ハーネス変更の検出と `halted`・`st resume` | P0-09, P0-10 | task-model §4, §6, data-layout §3 | 不合格が理由つきで再試行され、上限で failed になる。無効化で依存タスクが blocked に戻る。ハーネスを変えると run が halted になり、resume で再開する |
| P0-12 | キャッシュ：キーの計算、入力の正規化、`share_across_runs` | P0-08 | task-model §9 | 同じキーのタスクが実行者に渡らずに done になる。無効化後はキャッシュを使わない |
| P0-13 | 長文の継続（`continuation`、継続タスクの生成、連結、予算に応じた末尾の長さ） | P0-11 | task-model §8 | 打ち切られた出力から継続タスクが作られ、2回で完結しなければ不合格になる |
| P0-14 | CLI：`next`（`--wait`, `--json`）/ `submit` / `status` / `retry` / `resume` / `dev new-dummy`、終了コード | P0-11, P0-12, P0-13 | cli | 各コマンドの終了コードのテスト |
| P0-15 | ダミー実行者と end-to-end テスト | P0-14 | ROADMAP Phase 0 | 完了条件 1〜6 がテストで確認できる |

## 順序

```
P0-01 → P0-02 → P0-03
P0-01 → P0-04 → P0-07 ─┐
P0-01 → P0-05 → P0-06 ─┴→ P0-08 → P0-09 ─┐
        P0-05 → P0-10 ───────────────────┴→ P0-11 → P0-13 ─┐
                          P0-08 → P0-12 ────────────────────┴→ P0-14 → P0-15
```

## 作業項目ごとの決定事項

| 作業項目 | 決定事項 |
|---|---|
| P0-01 | 配布名 `100-times-ai-storyteller`、import 名 `storyteller`、バージョン `0.0.1`、ビルドバックエンド `hatchling`、`.python-version` は `3.13`、`requires-python = ">=3.11"`。`st --help` は、その時点で登録したコマンドの一覧を表示する。ディレクトリは、各作業項目が必要になった時点で作る（P0-01 が作るのは `src/storyteller/`・`tests/`・`tools/` だけ） |
| P0-02 | CI は macOS・Linux・Windows × Python 3.11・3.13 の6ジョブすべてで `uv run pytest` を実行する。`check_docs.py` は P0-03 で CI に加える |
| P0-03 | 対象は、リポジトリ内のすべての `*.md`（`.venv/`・`private/`・`.git/` を除く）。検査 (1) Markdown のインラインリンクと画像 `[..](..)` / `![..](..)` のうち、スキームを持たない相対パスについて、`#` 以降を除いたファイルまたはディレクトリが実在すること。(2) `docs/adr/[0-9][0-9][0-9][0-9]-*.md` の各ファイルに、`- 状態：` の行（値が `Proposed` で始まる、`Accepted`、`Superseded by ADR-NNNN` のいずれか）と `- 日付：YYYY-MM-DD` の行があること。`TEMPLATE.md` は対象外。(3) `docs/README.md` §2 の表の「原本」列にある Markdown リンクの実在（(1) で検査される）。バッククォートで書かれ「（Px-yy で作成）」と付いた行は検査しない。文書を作成した作業項目は、その行をリンクに書き換える。違反があれば一覧を出力して終了コード 1 |
| P0-05 | YAML の共通処理（読み込み→種類に対応するスキーマで検証）は、Phase 0 ではタスク定義と `.storyteller-workspace.yaml`（`schemas/workspace.schema.json`）に使う。以降の YAML の種類も同じ処理を使う |
| P0-09 | Phase 0 の `st next` が対象にするのは run のタスクだけとする。バッチの分析のタスクは P4-05 で対象に加える |
| P0-10 | `no_new_proper_nouns` は、`warn` と `fail` の両方の動作を単体テストで確認する。ダミーのハーネスでは使わない |
| P0-11 | 無効化は、コードタスクから呼び出す内部の処理として実装し、Phase 0 ではダミーのハーネスの検査タスク（D3）から呼ぶ |

## ダミーのハーネス

`st dev new-dummy` が使う、Phase 0 の検証用のハーネスである。パッケージの `src/storyteller/dev/dummy/` に、タスク定義とスキーマを置く。物語とは無関係な内容とし、本物のハーネス（`harness/`）には置かない。

| タスク | kind | 内容 | 依存 |
|---|---|---|---|
| `D1.items` | code | 固定の3件 `{"items": [{"id": "d1", "text": "alpha"}, {"id": "d2", "text": "beta"}, {"id": "d3", "text": "gamma"}]}` を出力する | — |
| `D2.echo` | llm（json） | 項目1件ごとに1タスク（添字 `d1`〜`d3`）。出力 `{"text": "...", "sources": ["d1"]}`。チェック：`sources_exist`、`max_chars`（text, 50） | D1 |
| `D3.check` | code | D2 の出力のうち、text に `INVALID` を含むものを無効化する | D2 すべて |
| `D4.story` | llm（text） | D2 の出力をまとめた本文。`continuation: true`、チェック：`ends_complete`、`min_chars`（10） | D3 |
| `D5.assemble` | code | D4 の本文を `tasks/D5.assemble/output.json` に `{"text": ...}` として書く | D4 |

- D2・D4 の `lease_minutes` は 0.05（3秒）とし、lease 切れのテストに使う。
- ダミー実行者は、テストのヘルパーとして `st next --json` と `st submit` をサブプロセスで呼ぶ。応答はテストごとに与え、不合格・`INVALID`・途中で切れた出力・claim 後の停止を注入できるようにする（注入の実装は実装者の裁量）。
- 同時 claim のテストは、2つのサブプロセスを、ファイルによる開始合図で同時に `st next` させ、claim できたのが1つだけであることを確認する。Windows でも同じテストを使う。
