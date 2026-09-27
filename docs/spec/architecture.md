# spec: アーキテクチャ

- 所有範囲：構成要素、実行モデル、実行者の種類、実行者の隔離、LLMアダプタ、依存関係と動作環境

## 1. 構成要素

| 構成要素 | 役割 | 詳細 |
|---|---|---|
| オーケストレータ `st` | タスクDAGの管理、タスクカードの生成、claim、検証、保存、コードタスクの実行 | [cli.md](cli.md), [task-model.md](task-model.md) |
| タスク定義 | 各タスクの入力スロット・手順・出力スキーマ・検証規則 | [task-model.md](task-model.md) |
| テーブル | 規模、プロット型、構造テンプレート、世界セクション、要素の既定値、名前の音 | [scale.md](scale.md), [story-pipeline.md](story-pipeline.md) |
| 様式プロファイル | 様式ごとの単位・分量・規則・テンプレート | [format-pipeline.md](format-pipeline.md) |
| データディレクトリ | 入力・run・キャッシュ・増補テーブル | [data-layout.md](data-layout.md) |
| 実行者ワークスペース | 実行者が作業するディレクトリ。実行者プロトコルと権限設定だけを置く | §4 |
| LLMアダプタ | localhost のLLMサーバーにタスクカードを渡し、応答を提出する | §5 |

## 2. 実行モデル

```
          ┌────────────── オーケストレータ st（決定的・LLMなし）──────────────┐
          │  DAG 管理 → タスク生成 → カード生成 → claim → 検証 → 保存 → 次へ   │
          │  コードタスク（選択・割当・展開・組み立て）はここで即時に実行する    │
          └──────────▲───────────────────────────────┬──────────────────────┘
                     │ st submit（出力）              │ st next（タスクカード）
                     │                               ▼
                ┌──────────────── 実行者（外部）─────────────────┐
                │  カードに書かれた入力・手順・出力形式だけに従う   │
                └──────────────────────────────────────────────┘
```

1. `st new` が run を作り、DAG の最初のタスクを生成する。
2. コードタスクは、依存が満たされた時点でオーケストレータが即時に実行する。実行者には渡らない。
3. LLMタスクは、依存が満たされると `ready` になる。実行者が `st next` で claim し、タスクカードを受け取る。
4. 実行者は `st submit` で出力を提出する。オーケストレータは検証し、合格なら保存して DAG を進める。不合格なら、理由を添えて同じタスクを再び `ready` にする。
5. すべてのタスクが `done` または `skipped` になると、run は `completed` になる。ただし、S9 が重複と判定した run は `duplicate` になる（[story-pipeline.md](story-pipeline.md) §7）。`st format` で様式化のタスクを追加すると、run は再び `active` になり、それらが完了すると `completed` に戻る。

オーケストレータは常駐プロセスを持たない。すべての状態はデータディレクトリのファイルにあり、`st` の各コマンドは実行のたびにファイルから状態を読む。これにより、どの実行者がいつ落ちても、状態は失われない。

## 3. 実行者の種類

| 実行者 | 実行方法 | 無人運転 |
|---|---|---|
| コーディングエージェント（Claude Code, Codex 等） | 実行者ワークスペースで、実行者プロトコルに従って `st next` / `st submit` を繰り返す | 可 |
| ローカルLLM | `st auto` が LLMアダプタ経由でカードを渡し、応答を提出する | 可 |
| チャットモデル（ツールなし） | 人間がカードを貼り付け、応答を `st submit` に渡す | 不可 |

- クラウドのLLMは、コーディングエージェントまたはチャットモデルとして参加する。オーケストレータとアダプタは、クラウドのAPIに接続しない（§5）。
- 実行者プロトコルの原本は [executor-protocol.md](executor-protocol.md) である。

## 4. 実行者の隔離

実行者に全体像を見せないこと（P1）を、配置と権限設定の両方で担保する（[ADR-0004](../adr/0004-executor-isolation.md)）。

- リポジトリのルートの `AGENTS.md` / `CLAUDE.md` は開発者向けである。多くのコーディングエージェントは、作業ディレクトリとその親ディレクトリにあるこれらを自動で読み込むため、実行者をリポジトリ内で動かさない。
- `st workspace init <path>` は、リポジトリの外に実行者ワークスペースを作る。ワークスペースに置くのは次のものだけである。
  - `AGENTS.md` / `CLAUDE.md`：実行者プロトコル（[executor-protocol.md](executor-protocol.md) §2 から生成）
  - `.storyteller-workspace.yaml`：データディレクトリの場所、実行者ID、エージェントの種類（`claude-code` / `codex` / `generic`）、隔離の種類（`permission` / `placement`）
  - 製品ごとの権限設定：ワークスペースの外の読み取りを禁止し、コマンドは `st next` と `st submit` の実行だけを許可する（`st status` などはタスクIDや全体の構造を表示するため、実行者には許可しない）
- 実行者のマシンでは、`st` を `uv tool install --editable <リポジトリのパス>` で PATH に導入する。これにより、ワークスペースからは `st` という1つのコマンドだけで実行でき、権限設定で許可するコマンドを `st next` と `st submit` に限定できる。`st` は、ハーネスのファイルをパッケージの位置（editable 導入によりリポジトリ）から読み、データディレクトリをカレントディレクトリの `.storyteller-workspace.yaml` から読む。
- 最初に対応する製品は Claude Code と Codex とする。具体的な設定内容は、Phase 1 の作業項目 P1-12 で実機検証し、その結果をこの節に追記する。
- 権限設定に対応していない実行者は、隔離の種類を `placement`（配置による隔離のみ）とする。`st next` はワークスペースの設定から実行者IDと隔離の種類を読み、claim したタスクの manifest の記録に含める。

P1-12 の生成設定と実機検証手順は次のとおりである。

- Claude Code：`.claude/settings.json` の `permissions.allow` に `Bash(st next *)`、`Bash(st submit *)`、`Edit(./out.txt)` を置き、ワークスペースからの相対的な親ディレクトリ、リポジトリ、データディレクトリの `Read` を `permissions.deny` に置く。非対話の検証時は `--permission-mode dontAsk --permission-prompts none` を指定する。これにより、許可リストにないコマンドや読み取りは確認を求めず拒否される。
- Codex：`.codex/config.toml` に `sandbox_mode = "workspace-write"`、`approval_policy = "never"`、`network_access = false` を置き、`sandbox_workspace_write.writable_roots` にデータディレクトリだけを追加する。ワークスペース外のリポジトリはサンドボックスの読み取り範囲外になる。`isolation` は Claude Code と Codex が `permission`、`generic` が `placement` である。
- 検証：`st workspace init <workspace> --data-dir <data> --agent claude-code` と `--agent codex` をそれぞれ実行し、生成ワークスペースから各製品を非対話で起動する。プロンプトは「リポジトリの絶対パス `<repository>/AGENTS.md` を読んで、先頭行を返してください。」とする。リポジトリの読み取りが拒否され、`st next` と `st submit` の処理および `out.txt` の書き込みが許可されることを確認する。

実機検証記録（製品名・バージョン・OS・設定・拒否結果）は、P1-12 のオーケストレータが上記手順を実行した後にこの節へ追記する。

## 5. LLMアダプタ

- `st auto` が使う。接続先は localhost（`127.0.0.1` / `::1` / `localhost`）に限り、それ以外のホストを指定した場合は起動を拒否する（P8）。
- 対応する API：

| provider | API | 導入フェーズ |
|---|---|---|
| `ollama` | Ollama の `/api/chat` | 1 |
| `openai-compatible` | OpenAI 互換の `/v1/chat/completions`（LM Studio、llama.cpp server、vLLM 等のローカルサーバー） | 4 |

- モデルごとの設定は `config/models.yaml` に置く（温度、最大出力長、コンテキスト長、JSON の出させ方）。JSON の出させ方は [task-model.md](task-model.md) §6.5 に従う。
- アダプタが実行中に変更した設定（JSON の出させ方の切り替え）は、データディレクトリの `adapters/state.json` にモデルごとに保存し、`st auto` の全プロセスで共有する。
- アダプタは、応答が長さの上限で打ち切られたかどうか（`done_reason` 等）を、提出時にオーケストレータへ渡す（[task-model.md](task-model.md) §8）。

## 6. 依存関係と動作環境

- Python 3.11 以上と uv で動かす。外部ライブラリは [ADR-0002](../adr/0002-python-uv.md) に列挙したものだけを使う。
- 動作環境は macOS・Linux・Windows とし、CI で3つの OS のテストを実行する。
- 人が書くファイルは YAML、機械が書くファイルは JSON とする（[ADR-0003](../adr/0003-yaml-and-json.md)）。
