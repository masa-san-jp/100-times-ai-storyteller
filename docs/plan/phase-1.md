# Phase 1 計画：最小の物語生成（自由入力 × 掌編・短編）

- 所有範囲：Phase 1 の作業項目、依存関係、受け入れ条件
- 目的・完了条件：[ROADMAP.md](ROADMAP.md) Phase 1

## 共通の受け入れ条件

- 根拠の spec と実装が一致している。実装中に spec の変更が必要になった場合は、同じプルリクエストで spec を先に更新している。
- 作業でデータファイル（テーブル・タスク定義・スキーマ）を作成した場合、spec に書かれていた同じ値を削除し、そのファイルへのリンクに置き換えている（[docs/README.md](../README.md) §2.1）。
- `uv run pytest` と `uv run python tools/check_docs.py` が通り、CI が3つの OS で成功している。

## 作業項目

| ID | 作業 | 依存 | 根拠 | 受け入れ条件 |
|---|---|---|---|---|
| P1-01 | テーブルとそのスキーマ（[lineage-inventory.md](../lineage-inventory.md) で P1-01 に割り当てた行をすべて取り込む）：`scales`（最小出来事数・世界セクションの対応を含む）、`structures`（標準の3テンプレート。各段階に定義文・重み・要求する役・`absent_role_note`・`world_sections`・`object`）、`plot_types`（標準の14型。`core`・`required_events` を heros-journey の分類項目をもとに記述）、`world_sections`、`element_axes`、`elements/<axis>`（7軸。want・ability・duty は heroes の既定値から、他の4軸は新たに記述）、`name_sounds`、`common_words` | P0-15 | scale, story-pipeline §3〜§5, LINEAGE §3 | 全テーブルがスキーマ検証に通る |
| P1-02 | 規模の派生値の計算、S0（自由入力。個人情報の検出のうち、自由入力が対象とする項目を含む）、`st new --free`（`--scale`, `--axis`, `--seed`, `--plot-type`） | P1-01 | scale §4〜§6, input §3, §4, story-pipeline S0, cli `st new` | 各プリセット・軸の上書きで、出来事数の下限と引き上げが仕様どおりになる。同じ `--seed` で同じ派生値になる |
| P1-03 | S1（自由入力の段落分割を含む） | P1-02 | story-pipeline S1 | ダミー実行者で素材にIDが振られる |
| P1-04 | S2（run 内のプールとID付け。run 間の共有と増補テーブルへの追記は P2-04） | P1-03 | story-pipeline S2, §2.1 | 重複除去と、必要量到達後の `skipped` |
| P1-05 | S3（`table_snapshot`、入力由来の比率 r、筋への配分とテンプレートの選択、役と不在の役、要素と音の割当） | P1-04 | story-pipeline S3, §4.2, §4.3 | 同じ seed で同じ割当、別の seed で別の割当になる。人物が足りないとき不在の役が記録される |
| P1-06 | S4 | P1-05 | story-pipeline S4 | 規模に応じたセクションだけが生成される |
| P1-07 | S5（名前の響きと `uses_given`） | P1-05 | story-pipeline S5, §5 | 与えた音を使わない名前が不合格になる |
| P1-08 | S6（段階への配分、副筋の配置、必須の出来事、人物・不在の役・object・世界セクションの割当） | P1-06, P1-07 | story-pipeline S6 | 各段階最低1件のテスト。不在の役のスロットに注記が付く |
| P1-09 | S7（`candidates: 1`） | P1-08 | story-pipeline S7 | カードの入力が、直前の結果と世界の抜粋2件以内に限られている |
| P1-10 | S8（比較対象の選び方、S7 の無効化） | P1-09 | story-pipeline S8 | `yes` で S7 が無効化され、上限で failed になる |
| P1-11 | S9、`schemas/story.schema.json`、`story.md` のテンプレート | P1-10 | story-pipeline S9, §6 | 正本がスキーマに通る |
| P1-12 | `st workspace init`（`--data-dir`, `--agent`）、実行者プロトコルのテンプレートと一致検査、README の「実行者として動かす」節（`uv tool install` による導入手順）、Claude Code / Codex の読み取り禁止の権限設定。実機検証の結果を architecture §4 に追記する | P0-15 | architecture §4, executor-protocol, ADR-0004 | ワークスペース外の読み取りが両製品で拒否される。リポジトリ内のパスが拒否される |
| P1-13 | LLMアダプタ（`ollama`）、`st auto`、`config/models.yaml`、`json_mode` の段階的な切り替えと `adapters/state.json`、localhost 以外の拒否 | P0-15 | architecture §5, task-model §6.5 | 空応答が2回続くと json_mode が切り替わり、別プロセスの `st auto` にも反映される |
| P1-14 | 固有名詞の検出（P0-10 で実装済み。全タスクを `warn` で運用）の誤検出率を、Phase 1 の end-to-end の出力で測り、`tables/common_words.yaml` を整備し、5% 未満のタスクを `fail` に切り替える | P1-11 | task-model §6.4 | 測定結果と、`tables/common_words.yaml`・タスク定義の更新がプルリクエストに記録されている |
| P1-15 | end-to-end：ローカルモデルとコーディングエージェントでの短編の完走。開発者が書いた自由入力による生成例を `examples/` に置く | P1-11, P1-12, P1-13 | ROADMAP Phase 1 | 完了条件 1〜4 |

## 順序

```
P1-01 → P1-02 → P1-03 → P1-04 → P1-05 → (P1-06 ∥ P1-07) → P1-08 → P1-09 → P1-10 → P1-11 → P1-14
P1-12, P1-13 は P0-15 の後、S 系列と並行して進められる
P1-11 + P1-12 + P1-13 → P1-15
```

## 素材の書き手とレビュー

- テーブルの行（プロット型の中身、要素、名前の音など）と、タスク定義のカードの文面（`card.role`・`card.steps`）は、物語の素材そのものである。これらは、各作業項目の中で実装者が、LINEAGE.md の出典と spec の原則（P1〜P5）に従って書く。
- 書いたファイルが原本になる（[docs/README.md](../README.md) §2.1）。オーケストレータがレビューし、テーブル（P1-01）は所有者も一度確認する。
- 各テーブル・各タスクの出力の JSON Schema は、spec に列挙した項目をそのまま形式にしたものとし、実装者が書き、オーケストレータがレビューする。spec にない項目を加えない。

## Phase 1 の DAG

自由入力の run は、次のタスクで構成する。`[C]` はコードタスク、`[L]` は LLM タスク。

| タスク | kind | 依存 | 作るもの |
|---|---|---|---|
| `S1.extract-p<3桁>` | L | — | 段落ごとの素材 |
| `S2.plan` | C | S1 すべて | S2.expand と S2.merge を追加 |
| `S2.expand-m<3桁>-<axis>` | L | S2.plan | 要素5件と対極要素 |
| `S2.merge` | C | S2.expand すべて | 入力由来プール（ID つき） |
| `S3.assign` | C | S2.merge | assignment。S4・S5・S6 を追加 |
| `S4.section-<section id>` | L | S3.assign、前提セクションのタスク | 世界セクション（`single`） |
| `S4.item-<section id>-<3桁>` | L | S3.assign、前提セクションのタスク | 一覧型セクションの項目（`list`） |
| `S5.name-c<n>` | L | S3.assign | 名前 |
| `S5.profile-c<n>` | L | S5.name-c<n> | プロフィール |
| `S5.intro-c<n>`・`S5.appearance-c<n>` | L | S5.profile-c<n> | 短い紹介・外見 |
| `S5.motive-c<n>` | L | S5.profile-c<n>、S5.name すべて、主人公の S5.intro（主人公以外の場合） | 動機 |
| `S5.catchphrase-c<n>` | L | S5.motive-c<n> | 決め台詞 |
| `S6.expand` | C | S4 すべて、S5 すべて | slots。S7・S8.plan・S9 を追加 |
| `S7.event-e<3桁>` | L | S6.expand、同じ筋の直前のスロットの S8.judge | 出来事 |
| `S8.plan-e<3桁>` | C | そのスロットと、時系列でそれより前のすべてのスロットの S7 | 比較相手を決め、S8.compare と S8.judge を追加 |
| `S8.compare-e<3桁>-k<n>` | L | S8.plan-e<3桁> | 矛盾の有無 |
| `S8.judge-e<3桁>` | C | そのスロットの S8.compare すべて（比較相手がなければ S8.plan） | 必要なら S7 を無効化 |
| `S9.assemble` | C | S8.judge すべて | story.json、story.md |

- S0 はタスクではなく、`st new` の中で同期的に行う（入力の検証・正規化・規模の計算・manifest と最初のタスクの作成）。
- Phase 1 では `candidates: 1` のため、候補を選ぶタスクはない。S2 の run 間共有と増補テーブルへの追記は行わない（P2-04）。

## ファイル形式

| ファイル | 形式 |
|---|---|
| `input.json`（自由入力） | `{"kind": "free", "source_sha256": 文字列, "paragraphs": [{"id": "p001", "text": 文字列}]}`。段落の分割は input §3。1200字を超える段落は、1200字以内で最後の文末記号（`。！？!?.`）の直後で分け、文末記号がなければ1200字で分ける |
| manifest の `scale` | `{"preset": 文字列, "axes": {軸: 値}, "overrides": {軸: 値}, "derived": {"events": 整数, "threads": 整数, "cast": 整数, "parts": 整数または null, "world_sections": [セクションID], "pool_need": {要素の軸: 整数}}}` |
| manifest の `input` | `{"kind": "free", "source_sha256": 文字列, "plot_type": `--plot-type` の値または null}` |
| manifest の `table_snapshot` | `{要素の軸: 増補テーブルの行数}`。Phase 1 は増補テーブルがないため、すべて0 |
| `tables/scales.yaml` | `axes`（規模の軸ごとの値の列）、`level_min_events`（`[3, 6, 12, 24, 36]`）、`presets`（ID ごとに名前・軸の値・出来事数の範囲）、`world_sections_by_level`（段階ごとのセクションID）、`parts`（`min: 2, max: 6, default: [2, 4]`）、`subthread_events`（`[4, 6]`）、`input_ratio`（`[0.5, 0.9]`）、`candidate_multiplier`（3）、`world_counts`（一覧型のセクションごと・規模ごとの件数） |
| `tables/structures.yaml` | `templates`：ID ごとに `stages`（`id`・`name`・`definition`・`guidance`・`act`・`weight`・`roles`・`absent_role_note`・`world_sections`・`object`・`climax`） |
| `tables/plot_types.yaml` | `types`：`id`・`name`・`core`・`structure`・`character_requirements`・`time_design`・`conflict`・`climax`・`pacing`・`typical_setting`・`required_events`（`description`・`stages`：標準の3テンプレートそれぞれで割り当てる段階 `{three-beat, kishotenketsu, heros-journey-12}`）。Phase 1 は `structure: standard` の14型だけを置き、P2-08 で7型を加える |
| `tables/world_sections.yaml` | `sections`：`id`・`name`・`definition`・`viewpoints`（heros-journey・world-building の全観点と小項目。world-building v1.2 の指標を含む）・`level`（規模の段階。`level` が n 以下のものを生成する）・`kind`（`single`／`list`）・`max_chars`・`prerequisites`（前提セクションのID、最大2つ） |
| `tables/element_axes.yaml` | `axes`：`key`・`name`・`definition`・`generation_rules`（S2 でその軸の要素を作るときの規則。heroes の生成規則と world-building の観点を軸ごとに書く） |
| `tables/elements/<axis>.yaml` | `items`：オブジェクトの配列 `{"text": 日本語, "source": 原文（先行リポジトリ由来の場合）}`。ID は `<axis>:t<1から始まる行番号>`。heroes 由来の軸は heroes の全件（want 100・ability 100・duty 99・age 16・gender 10・species 50）、それ以外の軸は各100件。heroes に由来しない軸は、表層から深層まで、また観点を散らして書き、似た要素を並べない |
| `tables/roles.yaml` | `roles`：`id`（protagonist・messenger・supporter・adversary・bystander）・`name`・`definition`（heros-journey・world-building の役の定義の文章） |
| `tables/cliches.yaml` | `phrases`：S2 で使わない、ありきたりな表現の一覧（heroes の禁止例を含む）。各項目は2文字以上の句とし、普通の語の一部に一致する1文字の語（「火」「闇」など）を置かない |
| `tables/name_sounds.yaml` | `sets`：`id`・`description`・`sounds`（カタカナの音節、12個以上） |
| `tables/common_words.yaml` | `words`：文字列の配列 |
| `config/models.yaml` | `models`：モデル名ごとに `provider`・`endpoint`（既定 `http://127.0.0.1:11434`）・`temperature`・`max_tokens`・`context_length`・`json_mode`・`think`（推論の深さ。Ollama の `think` にそのまま渡す：`false`・`low`・`medium`・`high`。省略時は送らない）・`timeout_seconds`（既定 600） |
| `adapters/state.json` | `{"models": {モデル名: {"json_mode": 値, "empty_streak": 整数}}}`。更新は `adapters.lock`（data-layout §5 のロック）を取ってから行う。`empty_streak` はプロセスをまたいでモデル単位で数え、空でない応答で0に戻す |

## 作業項目ごとの決定事項

| 作業項目 | 決定事項 |
|---|---|
| P1-02 | 規模の派生値の計算は5つのプリセットすべてについて実装する。`st new` が受け付けるプリセットは Phase 1 では `vignette` と `short` だけとし、他は終了コード 1（「Phase 2 で対応」と表示） |
| P1-05 | 受け入れ条件の「別の seed で別の割当」は、10個の異なる seed の割当がすべて同一にはならないことで確認する |
| P1-09 | 受け入れ条件の「入力が限られている」は、S7 のカードに、story-pipeline S7 に列挙した入力以外（直前以外の出来事、割り当てていない人物、3件目以降の世界セクション）が含まれないことで確認する |
| P1-11 | 受け入れ条件に、正本のすべての ID 参照（`who`・`thread`・`sources`）が正本の中に実在することの検査を加える。`story.md` は、題（プロット型の名前と主人公の名前から作る）、登場人物の一覧、世界の一覧、出来事の順に並べ、出来事は「<when>、<where>で、<who の名前>が、<why>ために、<what>。その結果、<result>。」の文型で書く |
| P1-12 | Claude Code は、ワークスペースの `.claude/settings.json` の権限設定で、`st next`・`st submit` の実行とワークスペース内の `out.txt` の書き込みだけを許可し、それ以外の読み取りを拒否する。Codex は、読み取りを拒否する設定がない場合、隔離の種類を `placement` とする。検証は、各エージェントを非対話モードでワークスペースから起動し、リポジトリの `AGENTS.md` を読むよう指示して、拒否されることを確かめる。使った製品のバージョンと設定を architecture §4 に記録する |
| P1-13 | Ollama には `POST /api/chat` に `{"model", "messages": [{"role": "user", "content": カード}], "stream": false, "format": スキーマ・"json"・省略, "options": {"temperature", "num_predict", "num_ctx"}}` を送り、`message.content` を提出する。`done_reason` が `length` のときは `--truncated` を付ける。接続エラー・HTTP エラー・タイムアウトは3回まで再試行し、なお失敗した場合、または応答の本文が空の場合は、実行者の失敗として不合格と同じに扱う（task-model §6.3） |
| P1-14 | 誤検出率は、P1-15 の run（3本以上）の `warn` の記録について、オーケストレータが各候補を固有名詞か否か判定して求める。タスク種別ごとに「誤検出を1つ以上含む出力の数 ÷ 出力の数」とし、出力が20以上あり5%未満の種別を `fail` に切り替える。判定結果はプルリクエストに記録する |
| P1-15 | ローカルモデルは Ollama の `gpt-oss:20b`。入力は開発者が書いた自由入力 `examples/inputs/free-01.md`（3〜5段落、個人情報を含まない）。規模は `short`。完走の判定は、run が `completed` で、正本がスキーマと ID 参照の検査に通り、出来事数が派生値の範囲内であること。Claude Code と Codex の完走は、オーケストレータが実行者ワークスペースから非対話モードで起動して確かめる |
