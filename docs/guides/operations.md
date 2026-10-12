# 無人運転の運用手順

- 所有範囲：人間がいない間に、多数の物語を実行者に作らせ続けるための運用手順（始め方、見守り方、実行者の再起動、停止した run の扱い）。コマンドの引数と終了コードは [spec/cli.md](../spec/cli.md)、状態の遷移と lease は [spec/task-model.md](../spec/task-model.md)、実行者の種類と隔離は [spec/architecture.md](../spec/architecture.md)、実行者の手順は [spec/executor-protocol.md](../spec/executor-protocol.md) が原本であり、ここには複写しない。

## 1. 前提

- `st` は常駐しません（[spec/architecture.md](../spec/architecture.md) §2）。状態はすべてデータディレクトリのファイルにあるため、実行者や `st auto` がいつ落ちても、状態は失われません。無人運転とは、「落ちたら起動し直す」を人間の代わりに繰り返すことです。
- 並行して動かせるのは、同じマシン上の同じファイルシステムにある実行者だけです（[spec/data-layout.md](../spec/data-layout.md) §5）。データディレクトリを、ファイル同期サービスの配下に置かないでください。
- 運転中にハーネス（`harness/`・`tables/`・`schemas/`・`formats/`）のファイルを変更すると、run が `halted` になります（§6.2）。リポジトリの更新（`git pull` など）は、運転の前後に行ってください。
- 実行者は2種類から選べます（[spec/architecture.md](../spec/architecture.md) §3）。両方を同時に動かしてもかまいません。

| 実行者 | 向いている場合 |
|---|---|
| ローカルLLM（`st auto`） | 1台のマシンで完結させたい。プロセスの管理が単純 |
| コーディングエージェント（Claude Code・Codex など） | クラウドのモデルを使いたい。起動し直しの仕組みを自分で用意する |

## 2. 始め方

### 2.1 run を作る

1. データディレクトリを決めます。カレントディレクトリに `.storyteller-workspace.yaml`（実行者ワークスペース）があればその設定、なければ環境変数 `STORYTELLER_HOME`、それも設定しない場合はリポジトリの `private/` が使われます（[spec/cli.md](../spec/cli.md) 冒頭）。`st new`・`st status` などの人間用のコマンドは、実行者ワークスペースの外で実行してください。
2. ナラティブを書き（[narrative.md](narrative.md)）、取り込めるかを確かめます。

```
st check-input narrative.json
```

3. 作りたい本数だけ run を作ります。標準出力に run ID が出るので、控えておきます。

```
st new --narrative narrative.json --scale short
st new --narrative narrative.json --scale short --seed 2
```

- 規模は [spec/scale.md](../spec/scale.md) のプリセットから選びます。8時間で終わる量は、使うモデルの速度と規模によって変わります。最初は少なめに作り、§3.3 の見守りで進み具合を見てから増やしてください。
- バッチの ID が分かっている場合は、§3.3 の `st status --batch` で、まとめて確認できます。

### 2.2 実行者を動かす

#### ローカルLLM（`st auto`）

LLMサーバー（Ollama、LM Studio、llama.cpp server など）を先に起動しておきます。接続先は localhost に限ります（[spec/architecture.md](../spec/architecture.md) §5）。モデルごとの設定は `config/models.yaml` にあります。

```
st auto --provider ollama --model gpt-oss:20b --workers 2 --until-empty
st auto --provider openai-compatible --model openai/gpt-oss-20b --endpoint http://127.0.0.1:1234 --workers 2 --until-empty
```

- `--workers N` で N 並列にします。N はLLMサーバーが同時に処理できる数に合わせます。
- `--run RUN_ID` を付けると、その run だけを対象にします。
- `--until-empty` を付けると、実行できるタスクがなくなった時点で終了します。lease の切れた claim は片付けてから判断するため、落ちたワーカーの残したタスクも、終了前に処理されます。
- 長時間の運転では、実行するマシンのスリープを防ぎます。macOS では `caffeinate -ims` を前に付けます（[spec/task-model.md](../spec/task-model.md) §5）。

```
caffeinate -ims st auto --provider ollama --model gpt-oss:20b --workers 2 --until-empty
```

#### コーディングエージェント

1. 実行者のマシンに `st` を導入します（[spec/architecture.md](../spec/architecture.md) §4）。

```
uv tool install --editable <リポジトリのパス>
```

2. リポジトリの外に、実行者ワークスペースを作ります。

```
st workspace init ~/storyteller-executor --agent claude-code
```

- `--agent` は `claude-code`・`codex`・`generic` から選びます。`--data-dir` を省略した場合のデータディレクトリは、§2.1 と同じ規則（`STORYTELLER_HOME`、なければ `private/`）で決まります。別の場所を使うときは `--data-dir` を指定します。
- 複数のエージェントを並行して動かす場合は、エージェントごとにワークスペースを作ります。実行者IDが重ならないようにするため、`--executor-id` は省略するか、別々の値にします。
- `--agent` を省略すると `generic` になり、起動コマンドは表示されません（その場合は、使うエージェントに合わせて自分で起動コマンドを用意します）。`claude-code` と `codex` では、作成が終わると「起動コマンド: ...」の行が表示されます。これを控えます。以降、この文書では「起動コマンド」と呼びます。
- 表示される起動コマンドの `"<指示>"` は、書き換える場所です。ワークスペースの `AGENTS.md`（`CLAUDE.md`）の手順に従って作業を続けるよう、指示を書いて入れます。

3. 起動コマンドを実行します。エージェントは [spec/executor-protocol.md](../spec/executor-protocol.md) §2 の手順に従い、`st next` と `st submit` を繰り返します。

## 3. 運転中の見守り

### 3.1 実行者の終わり方を知っておく

- コーディングエージェントは、`st next --wait 600` で10分待ってもタスクがないとき、終了コード 2 を受け取って作業を終えます。また、想定外の終了コードを受け取っても終えます（[spec/executor-protocol.md](../spec/executor-protocol.md) §2）。どちらの場合も、エージェントのプロセスは終了します。
- `st auto` は、通信の失敗が続いて待ち時間の上限に達したとき、claim を解放して終了します（[spec/task-model.md](../spec/task-model.md) §6.3）。LLMサーバーが落ちていたことが、典型的な原因です。
- いずれの場合も、実行者が終了しただけでは、run は壊れません。再起動すれば続きから進みます（§5）。

### 3.2 夜間のあいだ起動し直し続ける

実行者が終了するたびに起動し直すループを、時間の上限つきで動かします。次は bash（macOS・Linux）の例です。`<起動コマンド>` には、`st auto ...` の行、または `st workspace init` が表示した起動コマンドを入れます。

```
end=$((SECONDS + 8 * 3600))
while [ "$SECONDS" -lt "$end" ]; do
  <起動コマンド>
  sleep 60
done
```

- 起動コマンドが終了すると60秒待って起動し直します。タスクがまだなければ、実行者はすぐ終了して、また起動し直されます。待ち時間を増やしたいときは `sleep` の秒数を変えます。
- ループは、すべての run が終わっても止まりません。上限の時間で止まります。朝、§3.3 で確認してください。
- 起動し直しの間に、落ちた実行者の claim は lease（既定30分）が切れた時点で無効になります。次の `st next`（`st auto` の中の取得を含む）が、その claim を片付けて、タスクを `ready` に戻します。人間が何かをする必要はありません。

### 3.3 進み具合を確かめる

`st status` は、タスクIDなど全体の構造を表示するため、人間が使い、実行者には使わせません（[spec/cli.md](../spec/cli.md) §1）。

```
st status
st status --run RUN_ID
st status --batch BATCH_ID
```

| 見たいこと | コマンド | 見るところ |
|---|---|---|
| run ごとの状態と、状態別のタスク数 | `st status` | `completed` が増えているか。`stalled`・`halted` がないか |
| 1本の run のタスクごとの状態 | `st status --run RUN_ID` | `failed` のタスクと、その失敗の報告のパス。warnings |
| バッチ全体の進み具合 | `st status --batch BATCH_ID` | 要求件数・完了数・重複数、`stalled`・`halted` の run、`failed` のタスクの理由 |

- 機械で扱うときは `--json` を付けます。形式は [spec/cli.md](../spec/cli.md) の `st status` にあります。
- 進み具合が止まっているように見えるときは、実行者が動いているかを先に確かめます（§5）。

## 4. 実行者が止まる原因と見分け方

| 状況 | 見え方 | 扱い |
|---|---|---|
| 実行者のプロセスが落ちた | タスクが `claimed` のまま進まない | 何もしなくてよい。lease が切れると自動で `ready` に戻る（§3.2）。実行者が動いていなければ、§5 で起動し直す |
| LLMサーバーに接続できない | 送り直しを続けたあと（lease の残りから5分を引いた時間まで）、`st auto` が終了する | サーバーを起動し直してから、`st auto` を起動し直す（§5） |
| LLMサーバーが要求を拒否した（HTTP 4xx。429 を除く） | 試行の上限を待たずに、そのタスクが `failed` になる | §6.1 |
| 出力が検証で不合格になった | 同じタスクが、理由つきで再び渡される | 何もしなくてよい。試行の上限までは自動で再試行される |
| 試行の上限に達した | `skipped`（既定）または `failed` | §6 |
| 無効化の上限に達した | タスクが `failed` になる | §6.1 |
| ハーネスのファイルが変わった | run が `halted` | §6.2 |

## 5. 実行者の再起動

1. 実行者が動いているかを確かめます。動いていなければ、タスクが残っているかを `st status` で確かめます（`ready` や `claimed` が残っているか）。
2. 再起動は、最初と同じコマンドを、もう一度実行するだけです。
   - `st auto`：§2.2 のコマンドをそのまま実行する。
   - コーディングエージェント：`st workspace init` が表示した起動コマンドをそのまま実行する。ワークスペースを作り直す必要はない。
3. すぐ `st next` を実行しても、落ちた実行者の claim の lease が切れるまでは、そのタスクは取れません。lease の切れを待つだけで、手動での取り消しは不要です。
4. 再起動しても同じ場所で止まるときは、LLMサーバーの状態（落ちていないか、モデルが読み込まれているか）と、`st status --run RUN_ID` の `failed` を確かめます。

## 6. 停止した run の扱い

run が止まるのは、次の2つの場合です（[spec/task-model.md](../spec/task-model.md) §4.2）。`st status` で、どの run が止まっているかが分かります。

### 6.1 `stalled`（失敗したタスクがある）

`failed` のタスクが1つでもあると起きます。`failed` になるのは、`on_exhausted: fail` のタスクが試行の上限まで不合格だった場合、LLMサーバーに要求を拒否された場合（HTTP 4xx。429 を除く）、無効化の上限に達した場合、コードタスクが例外で終了した場合です。通常のタスクは試行の上限に達しても `skipped` になって先へ進むため、止まりません。

1. 失敗したタスクと、失敗の報告のパスを確かめます。

```
st status --run RUN_ID
```

2. 失敗の報告（`failure.md`）を読みます。試行ごとの理由と出力の先頭が書かれています。報告はどこにも送られません。Issue にするかは、人間が判断します（[spec/task-model.md](../spec/task-model.md) §6.3）。
3. 原因が実行環境（LLMサーバーの不調など）だったと考えられるときは、`failed` のタスクを `ready` に戻します。

```
st retry TASK_ID
```

- `st retry` は、試行の回数を0に戻して再開します。原因が直っていないと、また `failed` になります。
- 他の run は、`stalled` の run があっても、そのまま進みます。

### 6.2 `halted`（ハーネスが変わった）

運転中にハーネスのファイルが変わると、その run は `halted` になり、`st next` の対象から外れます。標準エラー出力に警告が出ます。

- 変更が意図したものなら、run を再開します。

```
st resume --accept-harness-change RUN_ID
```

- 意図しない変更なら、ファイルを元に戻してから再開します。
- 再開すると、run の記録が現在のハーネスの内容に更新されます。それ以前のタスクの出力は、変わりません。

### 6.3 その他の状態

| 状態 | 意味 | 扱い |
|---|---|---|
| `completed` | すべてのタスクが `done` または `skipped` | 完了。`skipped` のタスクは、`st status --run` の warnings に理由が残っている |
| `duplicate` | S9 が、他の物語との重複と判定した | 異常ではない。正本は残るが、完了数には数えない |

## 7. 運転が終わったあと

1. `st status`（バッチなら `st status --batch BATCH_ID`）で、すべての run の状態を確かめます。`stalled`・`halted` が残っていれば、§6 で扱います。
2. `completed` の run について、`skipped` のタスクの理由（`st status --run RUN_ID` の warnings）を読みます。
3. キャッシュは自動では削除されません。容量が気になるときは、古いものを削除します。`active`・`stalled` の run から参照されているキャッシュは、削除されません（[spec/data-layout.md](../spec/data-layout.md) §6）。

```
st cache prune --older-than 30
```

4. 実行者ワークスペースと `st` の導入は、不要になったら削除してかまいません。データディレクトリの中身（`private/` など）は、コミットしないでください。
