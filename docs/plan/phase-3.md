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
| P3-09 | 意味検査を対象1件に分解し、判断不能や助言で本文を破棄しない | P3-08 | format-pipeline §3 F4 | 構造化プロファイル・F1v3・F4.reviewで対象別入力、引用照合、3値とassessmentを実装。捏造引用/情報不足/助言で再生成しない、真の違反は既存上限で処理、旧版/並列/予算/cacheを回帰。対照モデル検証は固定4要求を各1回、同案の再抽選なし |
| P3-10 | 正本の動機を F1 から F2 へ渡す（#153） | P3-09 | format-pipeline §3 F1・F2 | F1 v4 の部分に登場人物の motive を写し、F2 v3 の入力に切り詰めずに渡す。予算不足はプロフィールから縮め、収まらなければ予算超過。旧 F1 出力・旧 cache を維持。小説の出力で各人物の動機が行動に現れることを読んで確認する |
| P3-11 | 小説の F2 で同じ長い文の反復を提出時に拒否する | P3-09 | format-pipeline §4.5、task-model 検証 | profile の `submit_checks` を実装し、novel だけに `no_repeated_sentence`（台詞を除く）を指定。脚本・スライドは不変。台詞のリフレイン・小数は合格、地の文の反復は不合格→修正で受理 |
