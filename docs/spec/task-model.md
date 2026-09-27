# spec: タスクモデル

- 所有範囲：タスク定義の形式、タスクカードの形式と入力の描画、DAG とタスクの状態遷移、claim/lease、検証と再試行、無効化、候補生成、長文の継続、キャッシュキー

## 1. タスクの種類

| 種類 | `kind` | 実行するもの | 例 |
|---|---|---|---|
| コードタスク | `code` | オーケストレータ（即時実行） | 組み合わせ（S3）、構造展開（S6）、組み立て（S9） |
| LLMタスク | `llm` | 実行者 | 素材抽出（S1）、出来事生成（S7） |

## 2. タスク定義

タスク定義は `harness/<pipeline>/tasks/<task_type>.yaml` に置く。1ファイル = 1つのタスク種別。形式は `schemas/task-definition.schema.json`（JSON Schema Draft 2020-12）で検証する。

### 2.1 項目

| 項目 | kind | 必須 | 型・値 | 既定値 | 意味 |
|---|---|---|---|---|---|
| `id` | 両方 | 必須 | 文字列 `^[A-Z][0-9]+\.[a-z][a-z0-9_]*$` | — | タスク種別ID（`<段階>.<名前>`） |
| `version` | 両方 | 必須 | 1以上の整数 | — | 手順・スキーマ・入力の組み立て方を変えたら上げる |
| `kind` | 両方 | 必須 | `code` / `llm` | — | |
| `handler` | code | 必須 | 文字列 | — | オーケストレータに登録したコードタスクの処理名 |
| `inputs` | 両方 | 任意 | 入力スロットの対応表（§2.2） | `{}` | |
| `output` | llm | 必須 | `json` / `text` | — | |
| `card` | llm | 必須 | §2.3 | — | |
| `validate` | llm | 任意 | §6 | `{}` | |
| `max_input_chars` | llm | 任意 | 1以上の整数 | 3000 | タスクカードの「入力」節の文字数予算（文字数の数え方は §3.3） |
| `candidates` | llm | 任意 | 1以上の整数 | 1 | §7 |
| `select_candidate` | llm | 任意 | `random` / `least_similar` | `random` | §7 |
| `max_attempts` | llm | 任意 | 1以上の整数 | 3 | §6.3 |
| `max_invalidations` | llm | 任意 | 0以上の整数 | 2 | §4.3 |
| `share_across_runs` | llm | 任意 | 真偽値 | `false` | §9 |
| `lease_minutes` | llm | 任意 | 0より大きい数 | 30 | §5（小数を許す。テスト用の短い lease に使う） |
| `continuation` | llm | 任意 | 真偽値 | `output: text` なら `true`、`json` なら `false` | §8 |

- 表にない項目はエラーにする（`additionalProperties: false`）。
- `kind: code` のタスク定義に llm の項目がある場合もエラーにする。

### 2.2 入力スロット

```yaml
inputs:
  character:                 # スロット名（英小文字・数字・_）
    label: 人物               # カードの「入力」節に表示する見出し（必須）
    from: S3.assignment      # 参照するタスク種別ID、または run の入力（input）
    select: "cast[{slot}]"   # 取り出す値（§2.4）
    required: true           # 既定 false
    truncate: none           # none | head | tail | drop。既定 none（§3.2）
```

### 2.3 カードの内容

```yaml
card:
  role: 与えられた属性だけを使って、人物のプロフィールを書く。   # 必須
  steps: [ ... ]                                                  # 必須。1件以上
  output_example: '{"profile": "...", "sources": ["want:i007"]}'  # output: json では必須、text では任意
```

`card` には、そのタスクに必要なことだけを書く。パイプライン全体、他のタスク、物語の最終形への言及を書かない（P1）。1タスクの出力は、1項目または小さなJSONに限る（P5）。長いリスト（20件を超える要素）は、1回20件を上限に分割したタスクとして定義する。

### 2.4 select の文法

```
式     := 参照 | 関数名 "(" 引数 ("," 引数)* ")"
参照   := "output" アクセス* | "input" アクセス* | 名前 アクセス*
アクセス := "." 名前 | "[" (整数 | "{slot}") "]"
引数   := 式 | "{slot}" | 整数 | 文字列リテラル
```

- `output` は参照先タスクの出力、`input` は run の入力（`input.json`）、それ以外の名前は参照先の出力の最上位のキーを指す。
- `{slot}` は、そのタスクの添字（[data-layout.md](data-layout.md) §2 の task_id の添字）の最初の要素に置き換える。
- 関数は `src/storyteller/selectors.py` に登録したものだけを使える。登録のない関数名、存在しない値の参照は、タスクを生成する時点でエラーにし、そのタスクを `failed` にする（理由を記録する）。
- 任意のコードは評価しない。

## 3. タスクカード

タスクカードは、タスク定義に入力を埋め込んで生成する Markdown である。

### 3.1 形式

```markdown
# タスク <ticket>

## あなたの役割
<card.role>

## 入力
### <スロットの label>
<スロットの値（§3.2）>

## 手順
1. <card.steps[0]>
2. ...

## 出力形式
<output: json の場合>
次のJSONだけを出力すること。前後に説明を書かないこと。
<card.output_example>
<output: text の場合>
本文だけを出力すること。前後に説明・見出し・注釈を書かないこと。
<card.output_example があれば、「例：」に続けて示す>

## 守ること
- 入力に書かれていないことを付け加えない。
- 出力形式以外の文章を書かない。

## 前回の不合格理由        ← 再試行・無効化のときだけ
<理由。500字を超える場合は500字で切り、末尾に「…」を付ける>

## これまでの出力の末尾    ← 継続のときだけ（§8）
<末尾>
```

- 見出しには、タスクIDではなく、claim ごとに発行する ticket を書く。
- カードに、タスクID・run ID・タスク種別ID・段階名（`S1`〜`S9`、`F0`〜`F4` の形の文字列）を含めない。P0-06 のテストでは、これらの文字列がカードに現れないことを確認する。
- 入力素材のIDは、素材を区別するための記号として見せてよい。IDの体系は [story-pipeline.md](story-pipeline.md) §2.1 に従う。

### 3.2 入力の描画と切り詰め

- スロットは、タスク定義に書かれた順に描画する。
- 値の描画：
  - 文字列：そのまま書く。
  - `id` と `text` を持つオブジェクトの配列：1件1行で `[<id>] <text>` と書く。
  - その他の配列：1件1行で `- <値>` と書く。
  - その他のオブジェクト：`yaml.safe_dump(allow_unicode=True, sort_keys=False)` の結果を書く。
- 「入力」節の文字数が `max_input_chars` を超える場合、次の順で切り詰める。
  1. `required: false` のスロットを、定義の逆順に処理する。
  2. 次に `required: true` のスロットを、定義の逆順に処理する。
  3. 各スロットの処理：`drop` はスロットごと削除する。`head` は先頭を残し、`tail` は末尾を残す。配列は要素単位、文字列は文字単位で削る。`none` は削らない。
  4. 予算に収まった時点で止める。すべて処理しても収まらない場合は、そのタスクを `failed` にし、理由を「入力が予算を超える」とする。
- 「前回の不合格理由」と「これまでの出力の末尾」は、`max_input_chars` の対象外とする（それぞれ上限を持つ）。

### 3.3 文字数の数え方

この文書群で「文字数」「字」というときは、NFC 正規化した文字列の Unicode コードポイント数（Python の `len(unicodedata.normalize("NFC", s))`）を指す。改行も1文字と数える。

## 4. DAG とタスクの状態

### 4.1 DAG

- run ごとに、タスクの依存関係を DAG として持つ。DAG は manifest の `tasks` に保存する（[data-layout.md](data-layout.md) §3）。
- DAG は、run の作成時に作る部分と、コードタスクの実行結果によって後から追加する部分（例：S6 がスロット数だけ S7 を追加する）からなる。コードタスクが追加できるのは、自分に依存するタスクだけとする。

### 4.2 状態と遷移

| 状態 | 意味 |
|---|---|
| `blocked` | 依存が未完了 |
| `ready` | 実行可能 |
| `claimed` | 実行者が claim 中（lease の期限つき） |
| `done` | 出力を保存済み |
| `failed` | 試行または無効化の上限に達した、または処理できない |
| `skipped` | 不要になった（例：要素プールが必要量に達した後の S2 タスク） |

| 遷移 | 契機 |
|---|---|
| `blocked` → `ready` | すべての依存が `done` または `skipped` になった |
| `ready` → `done` | コードタスクの実行が成功した、またはキャッシュに一致した（§9） |
| `ready` → `claimed` | `st next` で claim された |
| `claimed` → `done` | 提出が検証に合格した |
| `claimed` → `ready` | 提出が不合格で試行の上限に達していない、lease が切れた、claim が取り消された、継続が必要になった（§8） |
| `claimed` → `failed` | 提出が不合格で試行の上限に達した |
| `ready` → `failed` | コードタスクが例外で終了した、入力の生成に失敗した（§2.4, §3.2） |
| `blocked` / `ready` → `skipped` | コードタスクが不要と判断した |
| `done` → `ready` | 無効化された（§4.3） |
| `done` / `ready` / `claimed` → `blocked` | 依存先が無効化された（§4.3） |
| `failed` → `ready` | `st retry` |

- コードタスクは再試行しない。例外で終了した場合は `failed` にし、例外の内容を manifest に記録する。
- run の状態は、次の優先順で決める：`halted`（ハーネスの変更）＞ `stalled`（`failed` のタスクがある）＞ `completed`（すべて `done` または `skipped`）＞ `active`。`duplicate` は S9 が設定する（[story-pipeline.md](story-pipeline.md) §7）。

### 4.3 無効化

- 後続の検査（S8、F4、Phase 0 のダミーDAGの検査タスク）が、`done` のタスクの出力を不合格とした場合、コードはそのタスクを無効化する。
- 無効化の処理：
  1. 状態を `ready` に戻し、`invalidations` と `attempt` を1ずつ増やす（`attempt` が変わるので、タスクの seed とキャッシュキーも変わる）。
  2. 次のカードの「前回の不合格理由」に、検査の理由を書く。
  3. 無効化されたタスクに直接・間接に依存するタスクを `blocked` に戻し、それらの出力を破棄する。`claimed` のものは claim を取り消す（`claim.json` を `claim.revoked.<n>.json` に改名し、その ticket による提出を終了コード 3 で拒否する）。
- `invalidations` が `max_invalidations` を超える無効化が起きた場合は、無効化せずに `failed` にする。

## 5. claim と lease

- `st next` は、`ready` のLLMタスクを1つ選んで claim する。対象は、`active` の run のタスクと、Phase 4 以降はバッチの分析（[report.md](report.md)）のタスクである。選ぶ順は、run の作成順 → DAG の深さ（依存の段数）の小さい順 → タスクIDの辞書順とする。
- claim は、タスクディレクトリに `claim.json` を排他的に作成する（`os.open(path, O_CREAT | O_EXCL | O_WRONLY)`）ことで行う。作成に成功した実行者だけが、そのタスクを実行できる。`claim.json` の形式は [data-layout.md](data-layout.md) §7。
- 時刻は UTC のシステム時計で扱う。現在時刻が `lease_expires_at` 以上になった claim は無効とする。
- 無効な claim を見つけた `st next` は、`claim.json` を `claim.expired.<n>.json`（n は1から始まる、未使用の最小の整数）に改名し、タスクを `ready` に戻してから claim し直す。無効な claim による提出は終了コード 3 で拒否する。
- 提出が合格・不合格のいずれかで処理された後、`claim.json` は削除する（claim の記録は manifest と `attempts/` に残る）。
- 並行実行を保証するのは、同じマシン上の同じファイルシステムに限る（[data-layout.md](data-layout.md) §5）。

## 6. 検証と再試行

### 6.1 検証の順序

1. **形式**：`output: json` の場合、JSON のオブジェクトとして解析できること。解析できない場合は、出力の中から最初の `{` と、JSON の文字列リテラル内の括弧を無視して数えた対応する `}` までを取り出し、再度解析する。配列は救済の対象にしない。
2. **スキーマ**：`validate.schema` の JSON Schema（Draft 2020-12）に適合すること。
3. **チェック**：`validate.checks` に書いた検査（§6.2）。

### 6.2 チェック

`validate.checks` は配列で、各要素はチェック名の文字列、またはチェック名を唯一のキーとし引数を値とするオブジェクトとする。`field` はドット区切りのパス（例：`profile`、`items`）で、`output: text` の出力全体は `field` を省略して表す。

| チェック | 引数 | 合格の条件 |
|---|---|---|
| `sources_exist` | なし | 出力の `sources` の各IDが、カードの「入力」節に列挙したIDに含まれる |
| `max_chars` | `field`（任意）, `n` | 文字数が n 以下 |
| `min_chars` | `field`（任意）, `n` | 文字数が n 以上 |
| `count` | `field`, `n` または `min`・`max` | 配列の件数が n、または min 以上 max 以下 |
| `ids_subset` | `field`, `slot` | 配列の各IDが、指定した入力スロットに含まれるIDの部分集合 |
| `uses_given` | `field`, `slot`, `n` | 文字列が、指定した入力スロットの要素のうち n 個以上を部分文字列として含む |
| `ends_complete` | `field`（任意） | 末尾の空白を除いた最後の文字が `。．.！!？?」』）)】…` のいずれか |
| `no_new_proper_nouns` | `fields`（任意）, `mode`（`warn` / `fail`） | §6.4 |

### 6.3 不合格の扱い

- 不合格の場合、出力と理由を `attempts/<n>.json` に保存し、`tries` と `attempt` を1ずつ増やす。
- `tries` が `max_attempts` に達していなければ `ready` に戻し、次のカードの「前回の不合格理由」に理由を書く。達していれば `failed` にする。
- `st retry` は、`failed` のタスクだけを対象とし（他の状態なら終了コード 1）、`tries` と `invalidations` を0に戻して `ready` にする。`attempt` は戻さない（同じ seed の再利用を避けるため）。依存先のタスクは変更しない。

### 6.4 固有名詞の検出（`no_new_proper_nouns`）

形態素解析を使わない近似の検出である。

- 候補とする文字列：
  - 3文字以上のカタカナ（長音符・中点を含む）の連続
  - 「」『』で囲まれた文字列
  - 大文字で始まり、英字だけからなる2文字以上の語（文頭の語を含む）
  - 1文字以上の漢字の連続の直後に、接尾語（国・王国・帝国・町・村・市・島・山・川・教団・会・社・家・様・氏）が続くもの（接尾語を含めて1つの候補とする）
- 候補のうち、カードの「入力」節の全文に部分文字列として含まれるもの、または一般語の許可リスト `tables/common_words.yaml` に含まれるものは除外する。許可リストが存在しない場合は、入力だけを除外の対象とする。
- 残った候補があれば、`mode: warn` では合格とし候補を manifest の `warnings` に記録する。`mode: fail` では不合格とする。
- Phase 1 では、すべてのタスクを `warn` で運用して誤検出率を測る（P1-14）。

### 6.5 JSON の出させ方（LLMアダプタ）

`config/models.yaml` の `json_mode` で、モデルごとに指定する。

| 値 | 動作 |
|---|---|
| `schema`（既定） | サーバーの構造化出力機能に、タスクの JSON Schema を渡す |
| `json` | サーバーの JSON モードだけを使う |
| `off` | サーバーの機能を使わず、プロンプトの指示と §6.1 の救済だけに頼る |

`schema` または `json` で空の応答・解析不能な応答が2回続いた場合、アダプタはそのモデルについて1段階下の値に切り替え、データディレクトリの `adapters/state.json` に保存し、run の manifest の `warnings` に記録する（先行リポジトリで、一部のモデルが JSON モードで空応答を返した問題への対策）。

## 7. 候補生成

- `candidates: k`（k ≥ 2）のタスクは、同じカードで k 個のタスクを作る。タスクIDの添字に候補番号 `c1`〜`c<k>` を付け、カードの入力は同一にする。seed だけが異なる。
- k 件がそろったら、後続のコードタスクが1件を選ぶ。
  - `random`：選択タスクの seed による無作為選択
  - `least_similar`：比較対象との、文字3-gram の Jaccard 係数の最大値が最も小さいもの。比較対象は、同じバッチの他の run の、同じタスク種別の選ばれた出力とする。バッチに属さない run、またはバッチ内に比較対象がまだない場合は `random` で選ぶ。
- LLMに候補の優劣を判断させない（P4）。

## 8. 長文の継続

- `continuation: true` のタスクで、次のいずれかに当てはまる提出は「途中で切れた」とみなす。
  - `st submit --truncated` が指定された（アダプタが長さによる打ち切りを報告した）
  - 出力が 400字以上で、`ends_complete` の条件を満たさない
- 途中で切れた提出は、検証せずに `tasks/<task_id>/partial.md` に追記し、タスクを `ready` に戻して `continuation_step` を1増やす。次のカードは継続のカードとし、「これまでの出力の末尾」節に `partial.md` の末尾を入れ、手順に「既出の文章を繰り返さず、末尾の直後から続きを書いて完結させる」を加える。
- 末尾の長さ = `max_input_chars` − 「入力」節の文字数（上限 6000字）。この値が 1000字未満になる場合は、そのタスクを `failed` にし、理由を「継続の予算が足りない」とする。
- 継続は、最初の提出の後に2回まで行う（`continuation_step` は最大2）。2回目の継続の提出がなお途中で切れている場合は、`partial.md` を破棄し、1回の不合格として §6.3 に従う。
- 途中で切れていない提出を受け取ったら、`partial.md` と連結した全体を §6 で検証する。
- `continuation: false` のタスクで、`--truncated` が指定された提出は不合格とする。`st submit --truncated` を `output: json` のタスクに指定した場合は、終了コード 1 とする。

## 9. キャッシュキーと再利用

- LLMタスクの出力は、次のオブジェクトを正規化した JSON（`json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))` を UTF-8 にしたもの）の sha256 をキーとしてキャッシュに保存する。
  - 通常：`{"type": タスク種別ID, "version": version, "input": 正規化した入力, "candidate": 候補番号(候補なしは0), "seed": タスクの seed}`
  - `share_across_runs: true`：`seed` の代わりに `"attempt": attempt` を入れる。同じ入力から作る run の間で、出力が共有される。
- 入力の正規化：文字列を再帰的に NFKC 正規化する。配列の順序、数値、null はそのまま保つ。
- 合格した出力だけをキャッシュに保存する。
- 同じキーのタスクが `ready` になったら、実行者に渡さず、キャッシュの出力で `done` にする（P6）。無効化されたタスクは `attempt` が変わるため、以前のキャッシュには一致しない。
