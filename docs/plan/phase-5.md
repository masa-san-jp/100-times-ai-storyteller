# Phase 5 計画：拡張

- 所有範囲：Phase 5 の作業項目、依存関係、受け入れ条件
- 目的：[ROADMAP.md](ROADMAP.md) Phase 5

共通の受け入れ条件は [phase-0.md](phase-0.md) と同じとする。

| ID | 作業 | 依存 | 根拠 | 受け入れ条件 |
|---|---|---|---|---|
| P5-01 | 様式 `visual-prompts`（heroes 系譜）：人物と場面ごとの画像生成用プロンプト。先に format-pipeline §4 に単位と規則を追記する | P4-07 | format-pipeline §4, LINEAGE §4 | 人物数 + 出来事数のプロンプトが出力される |
| P5-02 | 様式 `reference-book`（world-building 系譜）：世界セクションと人物の資料集。先に format-pipeline §4 に単位と規則を追記する | P4-07 | format-pipeline §4, LINEAGE §4 | 正本の world と cast がすべて資料集に含まれる |
| P5-03 | テーブルの編集支援：`st table list/add/check`（スキーマ検証、重複の検出、増補テーブルと既定テーブルの差分表示）。先に cli.md に追記する | P4-07 | data-layout §2 | 不正な行の追加が拒否される |
| P5-04 | 生成物の評価手法：評価の観点と手順を `docs/spec/evaluation.md` として定める（先行リポジトリの非診断原則に従う） | P4-07 | VISION, LINEAGE | 評価の spec が docs/README の所有範囲の表に登録される |
