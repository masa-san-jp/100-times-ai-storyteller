# Phase 1 計画：最小の物語生成（自由入力 × 掌編・短編）

- 所有範囲：Phase 1 の作業項目、依存関係、受け入れ条件
- 目的・完了条件：[ROADMAP.md](ROADMAP.md) Phase 1

## 共通の受け入れ条件

- 根拠の spec と実装が一致している。実装中に spec の変更が必要になった場合は、同じプルリクエストで spec を先に更新している。
- 作業でデータファイル（テーブル・タスク定義・スキーマ）を作成した場合、spec に書かれていた同じ値を削除し、そのファイルへのリンクに置き換えている（[docs/README.md](../README.md) §2.1）。
- `uv run pytest` と `uv run python tools/check_docs.py` が通り、CI が3つの OS で成功している。

## 作業項目

| ID | 作業 | 依存 | 根拠 | 受け入れ条件 |
|---|---|---|---|---|
| P1-01 | テーブルとそのスキーマ：`scales`（最小出来事数・世界セクションの対応を含む）、`structures`（標準の3テンプレート。各段階に定義文・重み・要求する役・`absent_role_note`・`world_sections`・`object`）、`plot_types`（標準の14型。`core`・`required_events` を heros-journey の分類項目をもとに記述）、`world_sections`、`element_axes`、`elements/<axis>`（7軸。want・ability・duty は heroes の既定値から、他の4軸は新たに記述）、`name_sounds`、`common_words` | P0-15 | scale, story-pipeline §3〜§5, LINEAGE §3 | 全テーブルがスキーマ検証に通る |
| P1-02 | 規模の派生値の計算、S0（自由入力。個人情報の検出のうち、自由入力が対象とする項目を含む）、`st new --free`（`--scale`, `--axis`, `--seed`, `--plot-type`） | P1-01 | scale §4〜§6, input §3, §4, story-pipeline S0, cli `st new` | 各プリセット・軸の上書きで、出来事数の下限と引き上げが仕様どおりになる。同じ `--seed` で同じ派生値になる |
| P1-03 | S1（自由入力の段落分割を含む） | P1-02 | story-pipeline S1 | ダミー実行者で素材にIDが振られる |
| P1-04 | S2（run 内のプールとID付け。run 間の共有と増補テーブルへの追記は P2-04） | P1-03 | story-pipeline S2, §2.1 | 重複除去と、必要量到達後の `skipped` |
| P1-05 | S3（`table_snapshot`、入力由来の比率 r、筋への配分とテンプレートの選択、役と不在の役、要素と音の割当） | P1-04 | story-pipeline S3, §4.2, §4.3 | 同じ seed で同じ割当、別の seed で別の割当になる。人物が足りないとき不在の役が記録される |
| P1-06 | S4 | P1-05 | story-pipeline S4 | 規模に応じたセクションだけが生成される |
| P1-07 | S5（名前の響きと `uses_given`） | P1-05 | story-pipeline S5, §5 | 与えた音を使わない名前が不合格になる |
| P1-08 | S6（段階への配分、副筋の配置、必須の出来事、人物・不在の役・object・世界セクションの割当） | P1-06, P1-07 | story-pipeline S6 | 各段階最低1件のテスト。不在の役のスロットに注記が付く |
| P1-09 | S7（`candidates: 1`） | P1-08 | story-pipeline S7 | カードの入力が、直前の結果と世界の抜粋2件以内に限られている |
| P1-10 | S8（比較対象の選び方、S7 の無効化） | P1-09 | story-pipeline S8 | `yes` で S7 が無効化され、上限で failed になる |
| P1-11 | S9、`schemas/story.schema.json`、`story.md` のテンプレート | P1-10 | story-pipeline S9, §6 | 正本がスキーマに通る |
| P1-12 | `st workspace init`（`--data-dir`, `--agent`）、実行者プロトコルのテンプレートと一致検査、README の「実行者として動かす」節（`uv tool install` による導入手順）、Claude Code / Codex の読み取り禁止の権限設定。実機検証の結果を architecture §4 に追記する | P0-15 | architecture §4, executor-protocol, ADR-0004 | ワークスペース外の読み取りが両製品で拒否される。リポジトリ内のパスが拒否される |
| P1-13 | LLMアダプタ（`ollama`）、`st auto`、`config/models.yaml`、`json_mode` の段階的な切り替えと `adapters/state.json`、localhost 以外の拒否 | P0-15 | architecture §5, task-model §6.5 | 空応答が2回続くと json_mode が切り替わり、別プロセスの `st auto` にも反映される |
| P1-14 | 固有名詞の検出（P0-10 で実装済み。全タスクを `warn` で運用）の誤検出率を、Phase 1 の end-to-end の出力で測り、`tables/common_words.yaml` を整備し、5% 未満のタスクを `fail` に切り替える | P1-11 | task-model §6.4 | 測定結果と、`tables/common_words.yaml`・タスク定義の更新がプルリクエストに記録されている |
| P1-15 | end-to-end：ローカルモデルとコーディングエージェントでの短編の完走。開発者が書いた自由入力による生成例を `examples/` に置く | P1-11, P1-12, P1-13 | ROADMAP Phase 1 | 完了条件 1〜4 |

## 順序

```
P1-01 → P1-02 → P1-03 → P1-04 → P1-05 → (P1-06 ∥ P1-07) → P1-08 → P1-09 → P1-10 → P1-11 → P1-14
P1-12, P1-13 は P0-15 の後、S 系列と並行して進められる
P1-11 + P1-12 + P1-13 → P1-15
```
