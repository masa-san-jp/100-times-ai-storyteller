# Phase 3 計画：様式化

- 所有範囲：Phase 3 の作業項目、依存関係、受け入れ条件
- 目的・完了条件：[ROADMAP.md](ROADMAP.md) Phase 3

共通の受け入れ条件は [phase-0.md](phase-0.md) と同じとする。

| ID | 作業 | 依存 | 根拠 | 受け入れ条件 |
|---|---|---|---|---|
| P3-01 | 様式化の共通部分：`schemas/format-profile.schema.json`、F0・F1（字数範囲の計算を含む）・F3・F4（F2 の無効化）、`st format`（run を active に戻す） | P2-09 | format-pipeline §1〜§3 | ダミー様式で F0〜F4 が通る。F4 の不合格で F2 が無効化される |
| P3-02 | 小説様式 `formats/novel/`（heros-journey-12 の10章割付を `tables/structures.yaml` に追加） | P3-01 | format-pipeline §4 | 章数と分量が範囲内 |
| P3-03 | 映画シナリオ様式 `formats/screenplay/`（`scene` の割付、`tables/time_jumps.yaml`、Fountain の構文検査） | P3-01 | format-pipeline §4.1 | シーン分割のテスト。Fountain の構文検査に通る |
| P3-04 | プレゼンテーション様式 `formats/slides/`（1出来事1枚、表紙と結びのテンプレート、Marp の構文検査） | P3-01 | format-pipeline §4.2 | 枚数 = 出来事数 + 2。Marp の構文検査に通る |
| P3-05 | end-to-end：1つの正本から3様式 | P3-02, P3-03, P3-04 | ROADMAP Phase 3 | 完了条件 1〜3 |
| P3-08 | 未確定の前部分を使った後続生成と巻き添え再生成を防ぐ | P3-01 | format-pipeline §3 F2 | F1v2で前部分F4.verify完了を次F2の依存へ追加。前部分の不合格中は次をclaimせず、修正完了後に確定本文を渡す。固定応答で旧グラフの巻き添えを再現し、新版の巻き添え0、旧版/様式間並列/skip/上限警告採用/素材/最終出力/cacheの契約を確認。実モデル追加なし |
