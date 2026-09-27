# 変更の手順

## 1. 原本の扱い

- 仕様・計画・判断の原本は `docs/` にある。原本の所有範囲は [docs/README.md](docs/README.md) §2 に従う。
- コードと原本が食い違った場合は、原本を正とし、食い違いを Issue に起票する。

## 2. 変更の種類と手順

| 変更の種類 | 手順 |
|---|---|
| 誤字・表現の修正 | そのまま修正する |
| spec の振る舞いの変更 | spec を先に変更する。コードの変更は、同じプルリクエストか、その後のプルリクエストで行う |
| 設計原則に関わる判断 | ADR を `docs/adr/NNNN-<名前>.md` に書き（[TEMPLATE.md](docs/adr/TEMPLATE.md)）、関係する spec を更新する |
| 作業項目の追加・変更 | `docs/plan/phase-N.md` を更新する |

## 3. プルリクエスト

- 1つの作業項目（`P<フェーズ>-<番号>`）につき1つのプルリクエストを目安にする。
- 説明に、作業項目のIDと、根拠となる spec の節を書く。
- `uv run pytest` が通り、CI（macOS・Linux・Windows）が成功していること。
- `uv run python tools/check_docs.py`（文書のリンク切れ・ADR の見出し・所有範囲の表の文書の実在の検査）が通ること。
- 原則層（VISION.md・ADR）の変更は、リポジトリ所有者の決定を得てから行う（[docs/README.md](docs/README.md) §3）。
