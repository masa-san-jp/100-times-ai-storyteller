# 開発者向けガイド（人・エージェント共通）

このファイルは、このリポジトリを**実装・改修・保守する開発者**のためのものです。
物語を生成するタスクの**実行者**は、このファイルではなく、`st workspace init` で作ったワークスペースの手順に従ってください（[docs/spec/executor-protocol.md](docs/spec/executor-protocol.md)）。

## 作業を始める前に

1. [docs/README.md](docs/README.md) を読み、原本の所有範囲と規則を確認する。
2. [docs/process/implementation.md](docs/process/implementation.md) を読み、自分の役割（オーケストレータ／実装者）と、作業の受け渡し・報告の形式を確認する。
3. [docs/VISION.md](docs/VISION.md) の設計原則 P1〜P8 を確認する。すべての変更はこれに従う。
4. 依頼された作業項目の管理場所を [docs/README.md §2.2](docs/README.md#22-開発計画依存関係進捗の原本) で確認する。#53の開発は作業Issue、既存フェーズの未移行項目は `docs/plan/phase-N.md` を読み、参照する spec と適用する受け入れ条件を確認してから実装する。

## 実装者が守ること

- 依頼された作業項目1件だけを実装する。範囲外のファイルを変更しない。
- **仕様を変更しない。** spec にない振る舞いを実装しない。仕様の不足・矛盾・曖昧さを見つけたら、解釈を自分で決めずに作業を止め、報告の「仕様への質問」に書く。
- push、プルリクエストの作成、マージをしない。ローカルでのコミットまでを行う。
- 実装は Python 3.11 以上と uv で行う。外部ライブラリを追加しない（追加が必要なら「仕様への質問」として報告する）。
- macOS・Linux・Windows で動くように、[ADR-0002](docs/adr/0002-python-uv.md)「影響」節の実装規則に従う。
- テストのデータは pytest の `tmp_path` に作る。リポジトリ内に作らない。
- マシン全体に影響する操作（`uv tool install`、グローバルな設定の変更など）をしない。導入の確認が必要な場合は、`UV_TOOL_DIR` と `UV_TOOL_BIN_DIR` を一時ディレクトリに向けて行い、終わったら削除する。
- ユーザーの入力と生成物（`private/` など、データディレクトリの中身）をコミットしない。
- セットアップ：`uv sync`　テスト：`uv run pytest`
- 作業の最後に、[docs/process/implementation.md](docs/process/implementation.md) §3 の形式で報告する。
