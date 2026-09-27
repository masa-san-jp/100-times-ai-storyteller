# Phase 4 計画：並列・無人運転

- 所有範囲：Phase 4 の作業項目、依存関係、受け入れ条件
- 目的・完了条件：[ROADMAP.md](ROADMAP.md) Phase 4

共通の受け入れ条件は [phase-0.md](phase-0.md) と同じとする。

| ID | 作業 | 依存 | 根拠 | 受け入れ条件 |
|---|---|---|---|---|
| P4-01 | `st auto --workers N`：ワーカーの異常終了からの復帰、`--until-empty` | P3-05 | cli `st auto`, task-model §5 | ワーカーを強制終了しても、lease 切れ後に他のワーカーが引き継ぐ |
| P4-02 | LLMアダプタ `openai-compatible` | P4-01 | architecture §5 | LM Studio または llama.cpp server で短編が完走する |
| P4-03 | バッチの進捗表示と、`stalled` の run・`failed` のタスクの理由つき一覧（`st status --batch`） | P4-01 | cli `st status` | 理由が manifest から表示される |
| P4-04 | `st cache prune` | P4-01 | data-layout §6 | 参照中のキャッシュが削除されない |
| P4-05 | バッチの傾向分析 `st report`（`batches/<id>/report/` のタスクと `st next` の対象化、`schemas/report.schema.json`） | P4-03 | [spec/report.md](../spec/report.md) | 分析の出力がスキーマに通る。R2 のカードが文字数予算内に収まる |
| P4-06 | 運用手順 `docs/guides/operations.md`（無人運転の始め方、実行者の再起動、停止した run の扱い） | P4-03 | architecture, executor-protocol | 手順どおりに8時間の無人運転ができる |
| P4-07 | end-to-end：8時間の無人運転 | P4-02, P4-05, P4-06 | ROADMAP Phase 4 | 完了条件 1〜2 |
