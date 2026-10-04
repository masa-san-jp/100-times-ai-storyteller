# ドキュメント索引と運用規則

このディレクトリは、100 TIMES AI STORYTELLER の**仕様・契約・設計判断の原本（SSOT）**である。
仕様・契約・設計判断がコード・テスト・会話・Issue の記述と食い違った場合は、ここを正とし、食い違いを Issue として起票する。開発計画・依存関係・進捗の管理場所は §2.2 に従う。

## 1. 読む順番

| 目的 | 読む文書 |
|---|---|
| 何を作るのかを知る | [VISION.md](VISION.md) → [GLOSSARY.md](GLOSSARY.md) |
| 先行リポジトリとの関係を知る | [LINEAGE.md](LINEAGE.md) |
| 実装・改修する | [spec/architecture.md](spec/architecture.md) → 担当箇所の spec → §2.2で管理場所を確認 → 作業Issue、または未移行の [plan/ROADMAP.md](plan/ROADMAP.md) と担当フェーズの計画書 |
| なぜそう決めたのかを知る | [adr/](adr/) |
| 文書・コードの変更手順を知る | [../CONTRIBUTING.md](../CONTRIBUTING.md)、[process/implementation.md](process/implementation.md) |

## 2. 原本の所有範囲

1つの事柄は、1つの文書にだけ書く。他の文書は、その文書へのリンクで参照し、内容を複写しない。

まだ存在しない文書は、バッククォートで書き「（Px-yy で作成）」と付ける。文書を作成した作業項目で、その行をリンクに書き換える。

| 事柄 | 原本 |
|---|---|
| 目的・設計原則（P1〜P8） | [VISION.md](VISION.md) |
| 用語の定義 | [GLOSSARY.md](GLOSSARY.md) |
| 先行3リポジトリとの関係・重複の統合 | [LINEAGE.md](LINEAGE.md) |
| 先行リポジトリの要素ごとの取り込み先 | [lineage-inventory.md](lineage-inventory.md) |
| 構成要素・実行モデル・実行者の隔離 | [spec/architecture.md](spec/architecture.md) |
| タスク定義・タスクカード・DAG・claim/lease・検証・再試行・キャッシュキー | [spec/task-model.md](spec/task-model.md) |
| ディレクトリ構成・manifest・run とキャッシュの配置・書き込み規則 | [spec/data-layout.md](spec/data-layout.md) |
| CLI のコマンド・引数・終了コード | [spec/cli.md](spec/cli.md) |
| 入力（ナラティブ13項目・自由入力）と個人情報の検出規則 | [spec/input.md](spec/input.md) |
| ナラティブの記入例（ユーザー向け。項目と規則は input.md に従う） | `guides/narrative.md`（P2-02 で作成） |
| 物語の規模（プリセット・軸・派生値） | [spec/scale.md](spec/scale.md) |
| 物語生成ハーネス S0〜S9 と正本スキーマ | [spec/story-pipeline.md](spec/story-pipeline.md) |
| 様式化ハーネス F0〜F4 と様式プロファイル | [spec/format-pipeline.md](spec/format-pipeline.md) |
| 実行者が従うプロトコル | [spec/executor-protocol.md](spec/executor-protocol.md) |
| バッチの傾向分析 | [spec/report.md](spec/report.md) |
| 無人運転の運用手順（ユーザー向け） | `guides/operations.md`（P4-06 で作成） |
| 既存フェーズの計画・完了条件・状態（未移行範囲） | [plan/ROADMAP.md](plan/ROADMAP.md) |
| 既存フェーズの作業項目（未移行範囲） | `plan/phase-N.md` |
| #53の開発計画・依存関係・進捗 | [親Issue #53](https://github.com/masa-san-jp/100-times-ai-storyteller/issues/53) と各作業Issue（§2.2） |
| 設計判断とその理由 | `adr/NNNN-*.md` |
| 実装の役割・受け渡し・レビューの基準 | [process/implementation.md](process/implementation.md) |

### 2.1 データが原本になる事柄

実装が進むと、次の事柄は**機械可読なファイルが原本**になる。spec は、そのファイルの形式と意味だけを定義し、値は複写しない。

| 事柄 | 原本になるファイル | 移行する時期 |
|---|---|---|
| 規模プリセットの値 | `tables/scales.yaml` | Phase 1 |
| プロット型・構造テンプレート | `tables/plot_types.yaml`, `tables/structures.yaml` | Phase 1 |
| 各タスクの手順・出力スキーマ・パラメータ（候補数・字数・件数・共有の有無） | `harness/**/tasks/*.yaml`, `schemas/` | Phase 1〜3 |
| 最小出来事数・世界セクションの対応 | `tables/scales.yaml` | Phase 1 |
| 要素の軸 | `tables/element_axes.yaml` | Phase 1 |
| 世界セクションのカタログ | `tables/world_sections.yaml` | Phase 1 |
| 近似重複の閾値 | `tables/dedup.yaml` | Phase 2 |
| 正本の形式 | `schemas/story.schema.json` | Phase 1 |

移行するときは、spec 側の値を削除し、ファイルへのリンクに置き換える。値が spec とファイルの両方に残っている状態を作らない。ファイルを作成する作業項目は、この置き換えを受け入れ条件に含む（各フェーズ計画書の「共通の受け入れ条件」）。

### 2.2 開発計画・依存関係・進捗の原本

[ハッカソン向け開発の親Issue #53](https://github.com/masa-san-jp/100-times-ai-storyteller/issues/53) と、そこから参照する作業Issueを、この開発の計画・依存関係・進捗の原本とする。親Issueは全体の順序と関連作業への入口、各作業Issueはその作業の範囲・依存・受け入れ条件・進捗を管理する。仕様・契約は本文へ複写せず、担当する spec・ADR・機械可読な原本へリンクする。

- 作業Issueには、対象範囲、対象外、参照仕様、依存する作業、検証できる完了条件、未決事項を記す。受け入れ条件は仕様への適合を検証するためのものであり、Issueだけで仕様を追加・変更しない。
- 未決事項は「決定待ち」とし、何を誰が決める必要があるか、どの後続作業が止まるかを記す。決定後は担当する原本へ反映し、そのリンクをIssueに残す。未決の契約を実装の根拠にしない。
- 既存の [plan/ROADMAP.md](plan/ROADMAP.md) と `plan/phase-N.md` は、未移行の既存フェーズの計画・進捗を引き続き管理する。#53を理由に既存フェーズの受け入れ条件や状態を変更しない。
- 既存の作業項目をIssueへ移す場合は、項目単位で移行先へのリンクと移行範囲を計画書に記す。移行した範囲の依存・進捗はIssueだけを更新し、計画書に同じ情報を重ねて管理しない。未移行の項目は従来の管理を続ける。

## 3. 変更の権限

原本は、変更の重さによって2層に分ける。目的は、実装に合わせて原本が少しずつ書き換えられ、原本として機能しなくなること（仕様の漂流）を防ぐことである。

| 層 | 対象 | 変更の条件 |
|---|---|---|
| 原則層 | [VISION.md](VISION.md)、[adr/](adr/) | リポジトリ所有者の決定が必要。変更は新しい ADR として記録する |
| 仕様層 | 上記以外のすべての文書 | オーケストレータ（[process/implementation.md](process/implementation.md) §1）が変更する。原則層に反しないこと、[CONTRIBUTING.md](../CONTRIBUTING.md) の手順（spec を先に変える、整合を検査する）に従うこと。実装者は仕様を変更せず、不明点を報告する |

- 原則層に反するかどうか判断が分かれる変更は、ADR を Proposed として起票し、所有者の決定を待つ。
- 置き換えた文書は、冒頭に `Superseded：<置き換え先へのリンク>` と書く。

## 4. 未決事項の扱い

- 原本には、未決のまま実装の根拠になる記述を残さない。仕様層の事柄は、開発者が決めて本文に書く。
- 実装や検証で決定を見直す必要が出た場合は、原本を更新する。決定の理由を残す必要がある場合は ADR を書く。
- 原則層に関わる未決事項は、ADR を Proposed として起票し、リポジトリ所有者の決定を待つ。
