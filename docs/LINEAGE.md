# LINEAGE：先行リポジトリとの関係

- 所有範囲：先行3リポジトリとの関係、取り込む定義、重複工程の統合

## 1. 関連リポジトリ

| リポジトリ | 扱う工程 |
|---|---|
| [100-times-ai-heroes](https://github.com/masa-san-jp/100-times-ai-heroes) | 属性の組み合わせから、キャラクター設定と全身画像を生成する |
| [100-times-ai-heros-journey](https://github.com/masa-san-jp/100-times-ai-heros-journey) | 作家の自己ナラティブから、ヒーローズ・ジャーニー形式の物語を生成する |
| [100-times-ai-world-building](https://github.com/masa-san-jp/100-times-ai-world-building) | 入力を100倍に拡張し、世界設定・プロット・小説・資料集を生成する |
| [100-times-ai-manga-drawing](https://github.com/masa-san-jp/100-times-ai-manga-drawing) | 生成AIを使ったマンガ作画の実験（本リポジトリは取り込まない） |

## 2. 方針

先行リポジトリは**関連リポジトリ**であり、本リポジトリは実行時にそれらに依存しない。必要な定義と知見を取り込み、独立したハーネスとして動かす。判断の理由は [ADR-0001](adr/0001-independent-harness.md) に記録する。

取り込むのはコードではなく、**定義と知見**（項目・構造・テーブル・プロンプトの型・運用の仕組み）である。

## 3. 取り込む定義

先行3リポジトリが定める物語の構成要素は、[lineage-inventory.md](lineage-inventory.md)（取り込み表）にすべて載せ、要素ごとに取り込み先・取り込むフェーズ・外す場合の理由を書く。取り込み表がこの節の原本である。

- 所有者が先行リポジトリで整えた素材（heroes の `config/seeds/*.csv` など）は、LLM が新たに書いた素材で置き換えない。全件を取り込み、翻訳する場合は原文を残す。
- 先行リポジトリで LLM が行っていた処理（プロット型の選択など）のうち、コードで行えるものはコードに置き換える（VISION P2）。取り込み表では、その行を「外す（理由）」とする。

## 4. 重複工程の統合

先行3作の間には重複する工程がある。本リポジトリでは、それぞれを1か所にまとめ、LLMの呼び出しを減らす。

| 重複している工程 | 出典 | 本リポジトリでの扱い |
|---|---|---|
| 願望・能力・役割のプール生成 | 3作すべて | S2 に統合する。固定の100件ではなく、規模から求めた必要量だけ生成し、同じ入力の run 間で共有する |
| 入力の分析・抽出 | heros-journey 分析 / world-building Phase 0 | S1 に統合する。ナラティブ1項目ずつの小タスクにする |
| 4役の人物生成 | heros-journey / world-building Phase 2 | S5 に統合する。要素の割当は S3（コード）で行う |
| プロット型の選択 | heros-journey（LLMが21型から3候補）/ world-building（LLMが10型を生成して1つ選ぶ） | 21型を固定テーブルにし、選択は S3（コード）で行う。LLMの呼び出しをなくす |
| 世界設定 | heros-journey 4セクション / world-building 10ステップ | 1つのセクションカタログに統合し、規模が要求するセクションだけを S4 で生成する |
| プロット | heros-journey 骨子A〜E＋12段階＋アウトライン / world-building 10章プロット | S6（構造展開・コード）と S7（出来事1件ずつ）に置き換える |
| 章の本文生成と continuation | heros-journey / world-building Phase 5 | 様式化ハーネスの小説様式（F2）に統合する |
| 章の切り出し・参照検索 | world-building Phase 4（LLMが実行） | コードで行う（F1、S7 の入力組み立て） |
| 画像プロンプト | heroes / heros-journey | 任意の様式 `visual-prompts` として Phase 5 で追加する |
| 資料集 | world-building Phase 6 | 任意の様式 `reference-book` として Phase 5 で追加する |
| manifest・seed・チェックポイント・再開・重複除外 | world-building / heros-journey | オーケストレータに1実装だけ置く |
