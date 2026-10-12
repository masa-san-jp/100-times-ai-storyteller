# 100 TIMES AI STORYTELLER

外部のAIエージェント（コーディングエージェント、ローカルLLM、クラウドのチャットモデル）が参照して実行するだけで、物語が自律的に生成・出力される**エージェントハーネス**です。

- リポジトリの中にAIはいません。実行者は、分解されたタスクを1つずつ、決められた入力・手順・出力形式に従って実行します。
- 物語生成ハーネスが、「いつ・どこで・誰が・何のために・何をして・どうなった」を記述した正本を作ります。
- 様式化ハーネスが、正本を小説・映画シナリオ・プレゼンテーションなど、ユーザーが選んだ様式に整形します。

> 開発中です。コマンドラインから物語の生成（正本の出力）までを動かせます。実装の状況は [docs/plan/ROADMAP.md](docs/plan/ROADMAP.md) を参照してください。

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

## 物語を作る

流れは、次の3段階です。どのコマンドも、データディレクトリ（入力と生成物を置く場所。既定はリポジトリの `private/`、環境変数 `STORYTELLER_HOME` で変更）を対象にします。

1. `st new`：入力と規模から run（1本の物語の作業単位）を作り、run ID を表示します。
2. `st auto`（ローカルLLM）、または実行者による `st next` と `st submit` の繰り返し：run のタスクを最後まで実行し、正本を出力します。
3. `st format`：完成した正本を、小説などの様式に整形します。

```bash
export STORYTELLER_HOME=/absolute/path/to/storyteller-data   # 省略するとリポジトリの private/
mkdir -p "$STORYTELLER_HOME/inputs/free"
cat > "$STORYTELLER_HOME/inputs/free/idea.md" <<'EOF'
雨の港町で、古い灯台守が最後の夜を迎える。

彼のもとに、見知らぬ少女が舟で訪れる。
EOF

uv run st new --free "$STORYTELLER_HOME/inputs/free/idea.md" --scale short
# 標準出力に run ID が出る（例：20261012-020357-f34c7e）
```

- 入力は `--free`（自由な Markdown・テキスト）か `--narrative`（13項目の JSON）のどちらか一方です。形式は [docs/spec/input.md](docs/spec/input.md) にあります。メールアドレス・電話番号・URL・住所などの個人情報を検出すると、取り込みを拒否します（終了コード 4）。事前に `st check-input PATH` で調べられます。
- `--scale` は、物語の規模を `vignette`（掌編）・`short`（短編）・`novella`（中編）・`novel`（長編）・`saga`（大河）から選びます。文字数ではなく規模を指定し、出来事数や人物数は規模から決まります。`--axis NAME=VALUE`（軸の上書き）、`--parts N`（大河の部数）、`--seed N`（再現用）、`--plot-type ID`、`--count N`（N 本をまとめて作るバッチ）も使えます。詳細は [docs/spec/scale.md](docs/spec/scale.md) と [docs/spec/cli.md](docs/spec/cli.md)。
- 進み具合は `uv run st status`（run の一覧）と `uv run st status --run RUN_ID`（タスクごと）で確かめます。

### 実行者ごとの手順

以降の `RUN` は、`st new` が表示した run ID です。実行者は「いま取れるタスクを1つ受け取り、出力を返す」ことを繰り返します。リポジトリの中にAIはいないので、どの実行者を使うかを選びます。

#### ローカル Ollama

[Ollama](https://ollama.com/) を起動し、モデルを取得してから、`st auto` で実行します。

```bash
ollama pull gpt-oss:20b
uv run st auto --provider ollama --model gpt-oss:20b --run RUN --until-empty
```

- `--model` は、[config/models.yaml](config/models.yaml) の `models` に書かれたモデルの名前です。別のモデルを使うときは、同じ形式で項目を足します。
- `--endpoint` で接続先を変えられますが、localhost（`localhost`、`127.0.0.1`、`::1`）に限ります。`--workers N` で N 並列にできます。
- `--until-empty` を付けると、実行できるタスクがなくなった時点で終了します。省略した場合の動きは `uv run st auto --help` と [docs/spec/cli.md](docs/spec/cli.md) を参照してください。

#### OpenAI 互換エンドポイント（LM Studio など）

LM Studio や llama.cpp のサーバーなど、OpenAI 互換のサーバーを起動し、`--provider openai-compatible` を指定します。

```bash
uv run st auto --provider openai-compatible --model openai/gpt-oss-20b --run RUN --until-empty
```

- 既定の接続先は `config/models.yaml` の `openai/gpt-oss-20b`（`http://127.0.0.1:1234`）です。モデル名はサーバー側の識別子に合わせ、`config/models.yaml` に項目を足してください。ポートが違う場合は `--endpoint http://127.0.0.1:PORT` で指定します。
- 接続先は localhost に限ります。

#### Claude Code・Codex（実行者ワークスペース）

コーディングエージェントは、リポジトリの外に作る実行者ワークスペースで動かします。手順は下の「[実行者として動かす](#実行者として動かす)」のとおりで、`st workspace init` が作るワークスペースの `AGENTS.md` / `CLAUDE.md` に、`st next` → 出力の作成 → `st submit` の繰り返しが書かれています。

```bash
cd "$WORKSPACE"          # st workspace init で作ったワークスペース
st new --free /absolute/path/to/idea.md --scale short   # RUN を作る。データディレクトリは .storyteller-workspace.yaml の data_dir
claude -p "<指示>" --allowedTools "Bash(st next *)" "Bash(st submit *)" "Edit(./out.txt)"
# または
codex exec -C "$WORKSPACE" "<指示>"
```

- `st new` と `st status` は人間が実行します。実行者には `st next` と `st submit` だけを使わせます（`st status` はタスクIDを表示するため使わせません）。
- 実行者への指示は、手順を `AGENTS.md` に任せ、「`st next` から始めて、タスクがなくなるまで繰り返す」と伝えれば足ります。複数の実行者を同じデータディレクトリに向けて並行して動かすこともできます。
- 手動のチャットモデルも同じ手順で使えます（`st next --run RUN` でカードを取り、返答を `st submit TICKET PATH` で提出します。TICKET はカードの見出しにあります）。

### 様式化して読む

物語の生成が終わった（`st status` の run が `completed`）ら、`st format` で様式に整形します。

```bash
uv run st format --run RUN --as novel
uv run st auto --provider ollama --model gpt-oss:20b --run RUN --until-empty   # 様式化のタスクも同じ実行者で実行する
```

`st format` は様式化のタスクを run に追加するだけです。実行は、上の実行者と同じ手順で行います。`--as` に指定できる様式は、`formats/` にあるプロファイルの名前です：`novel`（小説）、`screenplay`（映画シナリオ）、`slides`（プレゼンテーション）、`visual-prompts`（画像プロンプト）、`reference-book`（資料集）。生成が終わっていない run を指定すると、エラーで拒否されます。

### 規模と動作確認

- 初めて試すときは、`vignette`（掌編）か `short`（短編）から始めてください。規模が大きいほど、タスクの数も実行時間も増えます。
- 開発用の最小規模 `dev` と、それで通し実行する `tools/dev_run.sh` は計画中で（[phase-1.md](docs/plan/phase-1.md) の P1-38）、現時点では使えません（`st new --scale dev` は拒否されます）。
- LLMを使わずに仕組みだけを確かめたい場合は、`st dev new-dummy` でダミーのハーネスの run を作れます（開発者向け）。

### 出力の場所

生成物はすべてデータディレクトリの中の `runs/RUN/` に出力されます（ユーザーの入力と生成物はコミットされません）。

| 場所 | 内容 |
|---|---|
| `runs/RUN/story/story.json` | 正本（「いつ・どこで・誰が・何のために・何をして・どうなった」の記述）。機械向け |
| `runs/RUN/story/story.md`、`characters.md`、`world.md` | 正本から作る、物語・人物・世界の読み物 |
| `runs/RUN/formats/NAME/` | `st format --as NAME` の出力（小説なら `formats/novel/novel.md`、プレゼンテーションなら `formats/slides/slides.md`） |
| `runs/RUN/manifest.json` | run の状態・規模・警告の記録 |

正本の形式は [docs/spec/story-pipeline.md](docs/spec/story-pipeline.md) §6 と `schemas/story.schema.json`、配置の詳細は [docs/spec/data-layout.md](docs/spec/data-layout.md)、様式化は [docs/spec/format-pipeline.md](docs/spec/format-pipeline.md) にあります。

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

仕様・計画・設計判断の原本は [docs/](docs/README.md) にあります。

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

