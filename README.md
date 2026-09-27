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

コーディングエージェントを実行者にする場合は、`st` をコマンドとして導入し、リポジトリの外に実行者ワークスペースを作ります。手順の詳細は Phase 1（P1-12）で確定し、この節に書きます。

```bash
uv tool install --editable .
st workspace init ~/storyteller-executor --agent claude-code
```

## ドキュメント

仕様・計画・設計判断の原本は [docs/](docs/README.md) にあります。

## 関連リポジトリ

100 TIMES AI シリーズの関連リポジトリです。本リポジトリは実行時にこれらに依存しません（関係の詳細は [docs/LINEAGE.md](docs/LINEAGE.md)）。

- [100-times-ai-heroes](https://github.com/masa-san-jp/100-times-ai-heroes)：キャラクター設定と全身画像の生成
- [100-times-ai-heros-journey](https://github.com/masa-san-jp/100-times-ai-heros-journey)：自己ナラティブからヒーローズ・ジャーニー形式の物語を生成
- [100-times-ai-world-building](https://github.com/masa-san-jp/100-times-ai-world-building)：世界観構築ワークフロー
- [100-times-ai-manga-drawing](https://github.com/masa-san-jp/100-times-ai-manga-drawing)：マンガ作画ワークフロー
