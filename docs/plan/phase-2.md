# Phase 2 計画：ナラティブと多様性

- 所有範囲：Phase 2 の作業項目、依存関係、受け入れ条件
- 目的・完了条件：[ROADMAP.md](ROADMAP.md) Phase 2

## 共通の受け入れ条件

- 根拠の spec と実装が一致している。実装中に spec の変更が必要になった場合は、同じプルリクエストで spec を先に更新している。
- 作業でデータファイル（テーブル・タスク定義・スキーマ）を作成した場合、spec に書かれていた同じ値を削除し、そのファイルへのリンクに置き換えている（[docs/README.md](../README.md) §2.1）。
- `uv run pytest` と `uv run python tools/check_docs.py` が通り、CI が3つの OS で成功している。

## 作業項目

| ID | 作業 | 依存 | 根拠 | 受け入れ条件 |
|---|---|---|---|---|
| P2-01 | ナラティブの取り込み：`schemas/narrative.schema.json`、空でない項目が3未満の拒否、`st new --narrative` | P1-15 | input §2 | 項目の欠け・字数超過・空項目の扱いのテスト |
| P2-02 | 記入ガイド `docs/guides/narrative.md`（項目ごとの良い例・書きすぎの例） | P2-01 | input §2.2 | 13項目すべてに例がある。例が個人情報の検出に通る |
| P2-03 | 個人情報の検出のナラティブへの適用（日付を含む）と `st check-input`（検出文字列を出力しない） | P2-01 | input §4 | 検出対象ごとの検出例・非検出例のテスト。ログに検出文字列が残らない |
| P2-04 | S2 の run 間共有と、対極要素の増補テーブルへの追記（`tables.lock`、`table_snapshot` による再現） | P2-01 | story-pipeline S2, S3, data-layout §2, task-model §9 | 2本目の run で S1・S2 が実行者に渡らない。追記後も既存 run の割当が再現できる |
| P2-05 | バッチ：`st new --count`、重複除外（指紋・近似重複）、S9 による補充と作成数の上限、`batch.lock` | P2-04 | story-pipeline §7, data-layout §2, §3 | 重複した run が `duplicate` になり、補充の run が作られる。上限で補充が止まる |
| P2-06 | S7 の候補生成（`candidates: 3`、`least_similar`） | P2-05 | story-pipeline S7, task-model §7 | バッチ内で最も類似度の低い候補が選ばれる。バッチ外では random になる |
| P2-07 | 規模 `novella` / `novel` / `saga`、軸の上書き、部数、副筋 | P2-01 | scale, story-pipeline §4.2, §4.3 | 各規模で、出来事数・筋・部・テンプレートが仕様どおりに展開される |
| P2-08 | 専用の構造テンプレートと、残り7型のプロット型 | P2-07 | story-pipeline §4 | 7型それぞれで正本が出力される |
| P2-09 | end-to-end：同じナラティブから10本、全規模 × 全プロット型の網羅（`--plot-type` で指定） | P2-03, P2-06, P2-08 | ROADMAP Phase 2 | 完了条件 1〜4 |
