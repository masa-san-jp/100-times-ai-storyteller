# 100 TIMES AI STORYTELLER

外部のAIエージェント（コーディングエージェント、ローカルLLM、クラウドのチャットモデル）が参照して実行するだけで、物語が自律的に生成・出力される**エージェントハーネス**です。

- リポジトリの中にAIはいません。実行者は、分解されたタスクを1つずつ、決められた入力・手順・出力形式に従って実行します。
- 物語生成ハーネスが、「いつ・どこで・誰が・何のために・何をして・どうなった」を記述した正本を作ります。
- 様式化ハーネスが、正本を小説・映画シナリオ・プレゼンテーションなど、ユーザーが選んだ様式に整形します。

> 現在は設計段階です。実装の状況は [docs/plan/ROADMAP.md](docs/plan/ROADMAP.md) を参照してください。

## セットアップ

Python 3.11 以上と [uv](https://docs.astral.sh/uv/) を使います。uv は必要なバージョンの Python も自動で用意します。

```bash
# uv の導入（macOS / Linux）
curl -LsSf https://astral.sh/uv/install.sh | sh
# uv の導入（Windows PowerShell）
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

git clone https://github.com/masa-san-jp/100-times-ai-storyteller.git
cd 100-times-ai-storyteller
uv sync
uv run st --help
```

uv を使わない場合は、Python 3.11 以上の環境で `pip install -e .` でも導入できます。

## 実行者として動かす

コーディングエージェントを実行者にする場合は、`st` をコマンドとして導入し、リポジトリの外に実行者ワークスペースを作ります。`workspace init` は実行者プロトコル、データディレクトリの設定、製品別の隔離設定を生成します。

```bash
REPOSITORY=/absolute/path/to/100-times-ai-storyteller
DATA_DIR=/absolute/path/to/storyteller-data
WORKSPACE=/absolute/path/to/storyteller-executor

uv tool install --editable "$REPOSITORY"
st workspace init "$WORKSPACE" --data-dir "$DATA_DIR" --agent claude-code
```

`WORKSPACE` はリポジトリの外に置いてください。`--agent claude-code` は `.claude/settings.json` を、`--agent codex` は `.codex/config.toml` を生成します。Codex は読み取りを制限できないため、`.storyteller-workspace.yaml` と manifest では `placement` として扱います。`generic`（既定値）も権限設定を生成せず、配置による隔離として扱います。`workspace init` の完了時には、選択したエージェントの起動コマンドも表示されます。

生成したワークスペースから、次のようにエージェントを非対話で起動します。タスクがある間は `AGENTS.md` / `CLAUDE.md` の手順に従って `st next` と `st submit` を繰り返します。

```bash
(cd "$WORKSPACE" && claude -p "<指示>" --allowedTools "Bash(st next *)" "Bash(st submit *)" "Edit(./out.txt)")

codex exec -C "$WORKSPACE" "<指示>"
```

Claude Code は実機確認で、`permissions.deny` によりワークスペース外の `AGENTS.md` の読み取りと Bash による読み取りが拒否されました。ただし、新しいワークスペースは信頼済みでないため `permissions.allow` は無視されるので、`st next` と `st submit` を許可する `--allowedTools` を起動時に渡します。Codex は書き込みだけを制限し、読み取りは制限しないため、ワークスペースをリポジトリの外に置く `placement` として扱います。製品名・バージョン・実機確認の詳細は [アーキテクチャ仕様 §4](docs/spec/architecture.md) に記録しています。

## ドキュメント

仕様・契約・設計判断の原本は [docs/](docs/README.md) にあります。#53の開発計画・依存関係・進捗はIssue、既存フェーズの未移行項目は従来の計画書で管理します。管理場所と移行規則は [docs/README.md §2.2](docs/README.md#22-開発計画依存関係進捗の原本) を参照してください。

## 関連リポジトリ

100 TIMES AI シリーズの関連リポジトリです。本リポジトリは実行時にこれらに依存しません（関係の詳細は [docs/LINEAGE.md](docs/LINEAGE.md)）。

- [100-times-ai-heroes](https://github.com/masa-san-jp/100-times-ai-heroes)：キャラクター設定と全身画像の生成
- [100-times-ai-heros-journey](https://github.com/masa-san-jp/100-times-ai-heros-journey)：自己ナラティブからヒーローズ・ジャーニー形式の物語を生成
- [100-times-ai-world-building](https://github.com/masa-san-jp/100-times-ai-world-building)：世界観構築ワークフロー
- [100-times-ai-manga-drawing](https://github.com/masa-san-jp/100-times-ai-manga-drawing)：マンガ作画ワークフロー

## 用語を使った読み方

たとえば同じ物語の出来事を小説と映画シナリオで読みたい場合、出来事を記録する「正本」と、その表現を変える「様式化」を分けて考えます。これは目的を理解するための利用例で、各様式の実装完了を示すものではありません。用語の正式な定義は [GLOSSARY](docs/GLOSSARY.md)、実装の現在地は [ROADMAP](docs/plan/ROADMAP.md) を参照してください。

## 系譜をたどる入口

このハーネスの成立背景は、シリーズ先行3作の定義・知見を取り込み、重複工程を整理する方針にあります。コードの移植や実行時依存とは区別しており、取り込む対象と理由の原本は [LINEAGE](docs/LINEAGE.md)、独立化の判断は [ADR-0001](docs/adr/0001-independent-harness.md) にあります。

