# 開発者向けガイド（人・エージェント共通）

このファイルは、このリポジトリを**実装・改修・保守する開発者**のためのものです。
物語を生成するタスクの**実行者**は、このファイルではなく、`st workspace init` で作ったワークスペースの手順に従ってください（[docs/spec/executor-protocol.md](docs/spec/executor-protocol.md)）。

## 作業を始める前に

1. [docs/README.md](docs/README.md) を読み、原本の所有範囲と規則を確認する。
2. [docs/VISION.md](docs/VISION.md) の設計原則 P1〜P8 を確認する。すべての変更はこれに従う。
3. [docs/plan/ROADMAP.md](docs/plan/ROADMAP.md) で現在のフェーズを確認し、そのフェーズの計画書（`docs/plan/phase-N.md`）から作業項目を選ぶ。
4. 作業項目の「根拠となる spec」を読んでから実装する。

## 守ること

- **spec が先、コードが後。** spec にない振る舞いを実装しない。spec を変える必要があれば、先に spec を変更する（[CONTRIBUTING.md](CONTRIBUTING.md)）。
- 1つの事柄は1つの文書にだけ書く。内容を他の文書に複写しない。
- 設計原則に反する変更、または原則の解釈が分かれる判断は、ADR（[docs/adr/](docs/adr/)）を書く。
- 実装は Python 3.11 以上と uv で行う。外部ライブラリの追加は [ADR-0002](docs/adr/0002-python-uv.md) の手順に従う。
- macOS・Linux・Windows で動くように、[ADR-0002](docs/adr/0002-python-uv.md)「影響」節の実装規則に従う。
- ユーザーの入力と生成物（`private/` など、データディレクトリの中身）をコミットしない。
- セットアップ：`uv sync`　テスト：`uv run pytest`
