# 先行リポジトリの要素の取り込み表

- 所有範囲：先行3リポジトリが定める物語の構成要素の一覧と、それぞれの取り込み方

この表は docs/LINEAGE.md §3 の原本である。先行リポジトリの要素は、この表のいずれかの行に必ず載せ、取り込み先・取り込むフェーズ・外す場合の理由を書く。

## 読み方

- 略記：`H:` は本リポジトリ。先行リポジトリは **HR** = `100-times-ai-heroes`、**HJ** = `100-times-ai-heros-journey`、**WB** = `100-times-ai-world-building`（いずれも `/Users/masa/マイドライブ/Dev/` の下）。
- 出典は `リポジトリ:ファイル:行` で示す。Colab 時代などの旧資料は、現行コードにない要素を定める場合だけ載せ、`[legacy]` を付ける。
- 「状態」列は、この表を作った時点のハーネスとの比較である。
  - **取り込み済み**：ハーネスの表・仕様に、同じ意味で存在する。
  - **一部**：概念はあるが、下位項目・制約・件数・文言が欠けている（差分を記す）。
  - **未取り込み**：tables/・schemas/・調べた仕様のどこにも対応がない。
  - **除外**：仕様または LINEAGE で、意図して除外・置き換えている（引用を記す）。
  - **ハーネス独自**：先行リポジトリにはなく、ハーネスで加えた要素。
  - **計画あり**：（一部・未取り込みの補足）後のフェーズで扱うと計画にあるが、定義はまだない。
- 「ハーネスでの場所・差分」列は、比較したときの記述（英語）をそのまま残している。
- 「決定」列は、オーナーの決定を当てはめた取り込み方である。
  - `取り込む（<作業項目ID>：<取り込み先>）`：その作業項目で、取り込み先に加える。
  - `取り込み済み（<場所>）`：すでに取り込まれている。
  - `外す（<理由>）`：取り込まない。
  - `ハーネス独自（先行リポジトリの要素ではない）`：ハーネスで加えた要素の行。取り込みの対象ではない。
- ハーネス側の主な参照先：`H:docs/spec/input.md`（L25-40 ナラティブ項目）、`H:docs/spec/story-pipeline.md`（S1 L43-47、S2 L50-57、S3 L59-76、S4 L78-82、S5 L84-88、S6 L90-100、S7 L102-110、§3 L126-130、§4 L132-161、§5 L164-170）、`H:docs/spec/format-pipeline.md`（L62-85）、`H:docs/spec/report.md`、`H:docs/LINEAGE.md`（L22-55）、`H:tables/*.yaml`。


## 1. 入力項目（作者のナラティブ／利用者の文脈）

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| 入力 | Narrative 13-item schema `NarrativeInput` | HJ:src/narrative_analyzer.py:16-30; HJ:src/narrative_interview.py:31-110; HJ:docs/design-specification.md:188-202 | 取り込み済み | H:docs/spec/input.md:23-39 (13 keys kept; questions rewritten to "傾向" form) | 取り込む（P2-01：input.md §2 と story-pipeline の用途の対応） |
| 入力 | `author` 自己紹介 — Q「あなたはどんな人物ですか？」 hint「思いつくままに、自分らしさや興味…」 | HJ:src/narrative_interview.py:32-37 | 取り込み済み（言い換え） | input.md:27 「人となり／どのような傾向を持つ人ですか」; forbids 職業名・所属・年齢・居住地. Hint text not carried (guide P2-02 planned) | 取り込む（P2-01：input.md §2 と story-pipeline の用途の対応） |
| 入力 | `missing` 欠けているもの — 「私には○○が欠けている。それは○○を象徴する」形式 | HJ:narrative_interview.py:38-43; design-specification.md:191 | 取り込み済み | input.md:28 (symbol clause kept) | 取り込む（P2-01：input.md §2 と story-pipeline の用途の対応） |
| 入力 | `status` 現在の状態 — 成功している／かつては成功していた／まだ成功していない／成功とは何かわからない | HJ:narrative_interview.py:44-49; design-spec:192 | 一部 | input.md:29 reframes to 満たされた／かつては／まだ／わからない (the "success" axis is dropped; four-way option kept) | 取り込む（P2-01：input.md §2 と story-pipeline の用途の対応） |
| 入力 | `memories` 現在の状況に最も強い影響を与えた過去のできごと | HJ:narrative_interview.py:50-55 | 取り込み済み（言い換え） | input.md:30 「どのような種類の経験ですか」; concrete いつ・どこで・誰と excluded by P8 | 取り込む（P2-01：input.md §2 と story-pipeline の用途の対応） |
| 入力 | `mission` 欠けているものを手に入れるためにクリアすべき具体的なミッション | HJ:narrative_interview.py:56-61 | 取り込み済み | input.md:31 | 取り込む（P2-01：input.md §2 と story-pipeline の用途の対応） |
| 入力 | `success` 欠けているものがいつか手に入るとイメージできるか（見込み） | HJ:narrative_interview.py:62-67 | 一部 | input.md:32 changes meaning to 「満たされた状態とは」 (成功像); the *prospect/likelihood* dimension is lost | 取り込む（P2-01：input.md の問いを原典の意味に合わせる） |
| 入力 | `loss` 手放さなければいけないもの（代償） | HJ:narrative_interview.py:68-73 | 取り込み済み | input.md:33 | 取り込む（P2-01：input.md §2 と story-pipeline の用途の対応） |
| 入力 | `taboo` 決して破ってはいけないタブー | HJ:narrative_interview.py:74-79 | 取り込み済み | input.md:34; also element axis `taboo` (H:tables/element_axes.yaml:11-13) | 取り込む（P2-01：input.md §2 と story-pipeline の用途の対応） |
| 入力 | `inhibit` いつも邪魔するもの（阻害要因） | HJ:narrative_interview.py:80-85 | 取り込み済み | input.md:35 | 取り込む（P2-01：input.md §2 と story-pipeline の用途の対応） |
| 入力 | `daily` 日常生活を表すキーワード | HJ:narrative_interview.py:86-91 | 取り込み済み | input.md:36 | 取り込む（P2-01：input.md §2 と story-pipeline の用途の対応） |
| 入力 | `change` 日常に変化をもたらす存在 | HJ:narrative_interview.py:92-97 | 取り込み済み | input.md:37 | 取り込む（P2-01：input.md §2 と story-pipeline の用途の対応） |
| 入力 | `acceptance` 大切なものを脅かす存在と和解・許容できるか（用途: 敵対者との関係性） | HJ:narrative_interview.py:98-103; design-spec:201 | 一部 | input.md:38 generalizes to 受け入れられること/にくいこと; link "用途＝敵対者との関係性" not used anywhere in S3/S5 | 取り込む（P2-01：input.md §2 と story-pipeline の用途の対応） |
| 入力 | `desire` 誰にも話せない秘めた願望 | HJ:narrative_interview.py:104-109 | 取り込み済み | input.md:39 | 取り込む（P2-01：input.md §2 と story-pipeline の用途の対応） |
| 入力 | Per-item interview hints and labels (NarrativeQuestion.hint/label) | HJ:narrative_interview.py:21-27 | 一部（計画あり） | input.md:43 guide `docs/guides/narrative.md` (P2-02) not yet written | 取り込む（P2-02：記入ガイド） |
| 入力 | Per-item "用途" mapping (e.g. daily→日常世界の素材, change→変化の触媒, acceptance→敵対者との関係) | HJ:docs/design-specification.md:190-202 | 未取り込み | S1 treats items uniformly; no item→story-slot routing (e.g. `daily`→ordinary-world, `change`→call, `loss`→ordeal) | 取り込む（P2-01：input.md §2 と story-pipeline の用途の対応） |
| 入力 | Default/sample narrative values | HJ:design-specification.md:204-224; HJ:narrative.example.json | 除外 | input.md:41 「空欄の項目は、空文字列のまま扱う。サンプル値で補完しない」 | 外す（サンプル値で補完しない：input.md §2.2） |
| 入力 | User context structured fields: `theme` | WB:config/prompts/expansion.yaml:27 | 未取り込み | Free input (input.md:45-49) is split into paragraphs only; no theme field. Legacy WB also passes theme as 「根底のテーマとなる重要な文脈」 into plot (WB v1.2 nb:1033-1034) | 取り込む（P1-03：S1 の素材の種類） |
| 入力 | `mood` 作品の雰囲気 | WB:expansion.yaml:28 | 未取り込み | No tone/mood parameter anywhere in S0-S9 or format rules | 取り込む（P1-03：S1 の素材の種類） |
| 入力 | `setting` 時代・場所・環境 | WB:expansion.yaml:29 | 一部 | Only indirectly via element axes `place`/`era` (drawn from pools, not from user-declared setting) | 取り込む（P1-03：S1 の素材の種類 image として扱う） |
| 入力 | `key_elements` 重要な要素 | WB:expansion.yaml:30 | 一部 | Materials from S1 (story-pipeline.md:46) play this role; no guarantee a user key element survives the r-ratio sampling (S3 L64) | 取り込む（P1-03：S1 の素材の種類） |
| 入力 | `protagonist_idea` 主人公の着想 | WB:expansion.yaml:31 | 未取り込み | S3 assigns protagonist elements randomly; no way to pin a user protagonist concept | 取り込む（P1-03：S1 の素材の種類） |
| 入力 | `image_observations` / image input (vision) | WB:expansion.yaml:21,32; WB:src/pipeline.py:717-770 | 未取り込み | Harness inputs are narrative JSON or text/Markdown only (input.md:7-10) | 外す（Phase 1〜4 はテキスト入力のみ。Phase 5 で再検討） |
| 入力 | Phase 0 context-extraction ("意図を保ち、情報を勝手に確定しすぎない", unknown→空) | WB:expansion.yaml:18-22 | 一部 | S1 per-item extraction (story-pipeline.md:43-47); "don't over-determine" rule not stated | 取り込む（P1-03：S1 のカードの規則） |
| 入力 | Colab-era external GPT interview for context (text + image) | WB:DESIGN_SPEC.md:244-256 `[legacy]` | 未取り込み | No interview/elicitation flow in harness (HJ CLI interview also not carried; st new reads files) | 取り込む（P2-02：記入ガイドの問いかけ） |

## 2. 分析の観点

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| 分析 | 願望分析 `desire` (切望・渇望, 400字) | HJ:src/narrative_analyzer.py:157-168; design-spec:358-365 | 一部 | S1 material kind `desire` (story-pipeline.md:47). Whole-narrative report replaced by 5 short phrases per item | 取り込む（P1-03：S1 の素材の種類に suppression を加える） |
| 分析 | 抑圧分析 `suppression` (抑圧している根源的な感情) | HJ:narrative_analyzer.py:170-181; design-spec:367-374 | 一部 | No `suppression` kind; closest is `fear` (story-pipeline.md:47). LINEAGE L25 claims 抑圧 imported but S1 kind enum lacks it | 取り込む（P1-03：S1 の素材の種類に suppression を加える） |
| 分析 | 葛藤分析 `conflict` (自己矛盾と葛藤) | HJ:narrative_analyzer.py:183-194; design-spec:376-383 | 一部 | S1 kind `conflict`; but S1 sees one item at a time (L44-45) so *cross-item* contradictions (e.g. desire vs taboo) cannot be detected | 取り込む（P1-03：S1 の素材の種類に suppression を加える） |
| 分析 | Cross-item holistic analysis (all 13 items read together) | HJ:narrative_analyzer.py:87-119; legacy nb cell 6 (HJ legacy:~20400) | 除外／変更 | LINEAGE L46 「S1 に統合する。ナラティブ1項目ずつの小タスクにする」 — fidelity risk: author-level synthesis is lost | 取り込む（P2-01：項目の組ごとの対比タスク S1.contrast） |
| 分析 | ナラティブ要素 10個 (抽象化・分類) `narrative` | HJ:narrative_analyzer.py:196-223; design-spec:385-392 | 一部 | S1 materials (5 per item, 10-30字, abstracted) story-pipeline.md:46 | 取り込む（P1-03：S1 の素材） |
| 分析 | 「抑圧されている自己像」 = narrative element fed to each character | HJ:character_generator.py:175; legacy nb cell 10 (HJ legacy:20767) | 未取り込み | S5 profile input = name/role/assigned elements only (story-pipeline.md:87); materials never directly attach to a character | 取り込む（P1-05：S3 で素材を人物と主題に割り当てる） |
| 分析 | 「解決すべきテーマ」 = random narrative element fed to plot | HJ legacy nb cell 10/18 (HJ legacy:20851, 21904); design-spec:529 | 未取り込み | No theme slot in S3 assignment or S7 card | 取り込む（P1-05：S3 で素材を人物と主題に割り当てる） |
| 分析 | 非断定・非診断原則 (DESIGN_PRINCIPLE) | HJ:src/batch_analyzer.py:29-32 | 取り込み済み | story-pipeline.md:46 「断定や診断をしない」; report.md:9 | 取り込み済み（story-pipeline.md:46、report.md:9） |
| 分析 | 深層心理学の専門家 persona for analysis | HJ:narrative_analyzer.py:166 | 除外（暗黙） | Harness cards have no persona role framing; S1 forbids diagnosis | 外す（カードに人格を持たせず作業内容だけを書く：VISION P1） |
| 分析 | Material kind taxonomy `image`, `value`, `fear` | (new in harness) | ハーネス独自 | story-pipeline.md:47 — harness-only | ハーネス独自（先行リポジトリの要素ではない） |

## 3. シード／要素テーブルと値

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| シードテーブル | `ability` DEFAULT_SEEDS (8): Can manipulate fire at will / read minds / superhuman strength / turn invisible / heal others / control time briefly / perfect memory / communicate with animals | HR:ollama_hero_gen.py:452-461 | 取り込み済み | H:tables/elements/ability.yaml:2-9 (translated, first 8 rows) | 取り込み済み（tables/elements/ability.yaml:2-9） |
| シードテーブル | `wants` DEFAULT_SEEDS (8): find lost family / strongest warrior / truth about past / protect the innocent / immortality / true love / conquer the world / peace to all nations | HR:ollama_hero_gen.py:462-471 | 取り込み済み | H:tables/elements/want.yaml:2-9 | 取り込み済み（tables/elements/want.yaml:2-9） |
| シードテーブル | `role` DEFAULT_SEEDS (8): Warrior / Mage / Healer / Assassin / Scholar / Merchant / Noble / Wanderer (each "Name. description") | HR:ollama_hero_gen.py:472-481 | 取り込み済み | H:tables/elements/duty.yaml:2-9 (axis renamed `duty` 役割・課題) | 取り込み済み（tables/elements/duty.yaml:2-9） |
| シードテーブル | Curated `ability` table, 100 values (e.g. "Can reverse causality through rhythmic movement") — full list Appendix A | HR:config/seeds/seed_ability.csv:2-101 (= data/seed_ability.csv) | 未取り込み | 0 of these 100 appear in ability.yaml (verified: no 因果/タトゥー/写真 etc.). Harness rows 9-101 are newly written, more mundane | 取り込む（P1-01：tables/elements/<axis>.yaml、日本語訳と原文） |
| シードテーブル | Curated `wants` table, 100 values (e.g. "I want to find the person who stole my shadow on my tenth birthday.") — Appendix A | HR:config/seeds/seed_wants.csv:2-101 | 未取り込み | Not imported; harness want.yaml rows 10-101 are abstract ("…たい") without concrete 相手・場所・物・期限 | 取り込む（P1-01：tables/elements/<axis>.yaml、日本語訳と原文） |
| シードテーブル | Curated `role` table, 99 values (e.g. "Ghost Real Estate Agent. …") — Appendix A | HR:config/seeds/seed_role.csv:2-100 | 未取り込み | Not imported; harness duty.yaml rows 10-101 are family/community/work/secret "役目/職務" | 取り込む（P1-01：tables/elements/<axis>.yaml、日本語訳と原文） |
| シードテーブル | `age` table (DEFAULT 6: Child…Ageless; curated 16: Prepubescent…Frozen at nineteen for two hundred years) | HR:ollama_hero_gen.py:425-432; HR:config/seeds/seed_age.csv:2-17 | 除外 | LINEAGE.md:29 「要素テーブル既定値（能力・願望・役割。年齢・性別・種族は取り込まない）」; story-pipeline.md:130 | 取り込む（P1-01：tables/elements/<axis>.yaml、日本語訳と原文） |
| シードテーブル | `gender` table (DEFAULT 5; curated 10 incl. "Changes gender with the moon phase") | HR:ollama_hero_gen.py:433-439; HR:config/seeds/seed_gender.csv:2-11 | 除外 | same quote | 取り込む（P1-01：tables/elements/<axis>.yaml、日本語訳と原文） |
| シードテーブル | `species` table (DEFAULT 10: Human…Alien; curated 50 incl. Kitsune, "Ghost bound to a vending machine") | HR:ollama_hero_gen.py:440-451; HR:config/seeds/seed_species.csv:2-51 | 除外 | same quote. Note: removes the main non-human/fantastical diversity lever of HR | 取り込む（P1-01：tables/elements/<axis>.yaml、日本語訳と原文） |
| シードテーブル | Physical characteristics composite `physical = f"{age} {gender} {species}"` | HR:ollama_hero_gen.py:1090; HR legacy 20240916…py:53 | 除外 | story-pipeline.md:130 「人物の外形は、割り当て要素と名前の響きから S5 が書く範囲に留める」 | 取り込む（P1-07：S5 appearance の入力） |
| シードテーブル | Auto-grown seed rows from runs (backup lists incl. "Chrono Forger…", "Beacon of Light…") | HR:data/seed_backup_20260927/*.csv | 未取り込み（不要） | Run-generated data; harness has its own augmentation table (story-pipeline.md:54) | 取り込む（P2-04：増補テーブル） |
| 要素プール | HJ element pools `wants` (心の中に持っている願望) / `abilities` (秘めている特有の能力) / `roles` (抱えている課題や役割上の葛藤), 100 each | HJ:src/colab_features.py:94-150; design-spec:398-406 | 取り込み済み（統合） | S2 per-axis pools (story-pipeline.md:50-57); LINEAGE.md:45 「固定の100件ではなく、規模から求めた必要量だけ生成」 | 取り込み済み（S2 の軸ごとのプール：story-pipeline.md:50-57） |
| 要素プール | WB `desire_list` 100 (登場人物が秘めている願望) | WB:expansion.yaml:42-63; pipeline.py:811-865 | 取り込み済み（統合） | S2 axis `want` | 取り込み済み（S2 の軸 `want`） |
| 要素プール | WB `ability_list` 100 (v1.2: 「特別な能力**または平凡な得意なこと**」) | WB:expansion.yaml:71-92; WB v1.2 nb:243 `[legacy]` | 取り込み済み（統合） | S2 axis `ability`; ability.yaml includes mundane skills (rows 10-30) | 取り込み済み（S2 の軸 `ability`） |
| 要素プール | WB `role_list` 100 (v1.2: 「担わなければいけない役割、課されている使命、または職業や身分」) | WB:expansion.yaml:100-121; WB v1.2 nb:256 `[legacy]` | 一部 | Axis `duty` definition (element_axes.yaml:8-10) = 役割と責任・葛藤; 使命 and 職業や身分 aspects not named | 取り込む（P1-04：S2 duty の観点） |
| 要素の軸 | New axes `taboo`, `place`, `era`, `object` | (harness-only) | ハーネス独自 | element_axes.yaml:11-22 — not from predecessors (taboo loosely from HJ narrative `taboo`) | ハーネス独自（先行リポジトリの要素ではない） |
| 要素プール | Diversity requirement for desires: 表層的な願望から深層的な願望まで | WB:expansion.yaml:52 | 未取り込み | S2 card (story-pipeline.md:52-53) has no depth-spread requirement | 取り込む（P1-04：S2 のタスク定義の規則、tables/cliches.yaml） |
| 要素プール | Diversity categories for abilities: 身体的・精神的・社会的・特殊能力 | WB:expansion.yaml:81; story_generation.yaml:152 | 一部 | Default ability.yaml rows appear grouped (body/perception/intellect/social/special) but S2 prompt has no category spread rule | 取り込む（P1-04：S2 のタスク定義の規則、tables/cliches.yaml） |
| 要素プール | Diversity categories for roles: 社会的・物語的・象徴的役割 | WB:expansion.yaml:110; story_generation.yaml:172 | 未取り込み | Not in duty definition or S2 | 取り込む（P1-04：S2 のタスク定義の規則、tables/cliches.yaml） |
| 要素プール | Item length 10〜30文字程度 | WB:expansion.yaml:53,82,111 | 取り込み済み | story-pipeline.md:46,52 (10〜30字) | 取り込み済み（story-pipeline.md:46,52） |
| 要素プール | Batch generation 20 items/request | WB:config/ollama_config.yaml:98; pipeline.py:811-865 | 取り込み済み | task-model.md:61 「1回20件を上限に分割」; LINEAGE.md:34 | 取り込み済み（task-model.md:61） |
| 要素の選択 | Per-character random pick of want/ability/role (+narrative) | HJ:colab_pipeline.py:412-423; HJ legacy nb cell 10; WB:world_building.yaml:35 | 取り込み済み | S3 step 6 (story-pipeline.md:64-68), seeded, no duplicates, input-ratio r ∈ [0.5,0.9] | 取り込み済み（S3 の手順6：story-pipeline.md:64-68） |
| 要素の選択 | HJ protagonist element mapping: 内面の願望=want, 秘めた能力=ability, 個人的な課題=role | HJ:character_generator.py:171-183 | 取り込み済み | S3: each character gets want/ability/duty; protagonist adds taboo | 取り込み済み（S3 の手順6：story-pipeline.md:64-68） |
| 要素の選択 | HJ non-protagonist mapping: messenger←関連する課題(role), supporter←関連する能力(ability), adversary←主人公が向き合う課題(role) | HJ:character_generator.py:213,249,285 | 一部 | Harness gives each role its own want/ability/duty; the *relational* link (adversary embodies protagonist's 課題) is not modeled | 取り込む（P1-05：S3 で人物ごとに要素を割り当てる） |

## 4. 要素生成の規則（対の要素、抽象化）

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| 生成規則 | 「対になるキャラクター」 counterpart generation → append to seed tables (self-growth) | HR:ollama_hero_gen.py:859-911, 585-596, 1249-1253; HR legacy 20240916…py:117-148 | 取り込み済み | story-pipeline.md:53-54 (S2 `counterpart`, appended to augmentation table `tables/<axis>.json`); VISION P4 | 取り込み済み（S2 `counterpart`：story-pipeline.md:53-54） |
| 生成規則 | Counterpart ability constraint: avoid same ability & stock powers (火・透明化・怪力・読心・治癒); must have 具体的な条件や制約; 意外な能力 | HR:ollama_hero_gen.py:866-869 | 未取り込み | S2 counterpart = 「対極にある要素を1件」 only; no anti-stock / condition-and-constraint rule | 取り込む（P1-04：S2 のタスク定義の規則、tables/cliches.yaml） |
| 生成規則 | Counterpart wants constraint: "I want to..." one sentence; avoid 守りたい・平和・最強・真実の愛; must include 具体的な相手・場所・物・期限 | HR:ollama_hero_gen.py:884-887 | 未取り込み | Not in S2; harness want.yaml itself violates this (rows 2-9 are the stock examples) | 取り込む（P1-04：S2 のタスク定義の規則、tables/cliches.yaml） |
| 生成規則 | Counterpart role constraint: avoid 戦士・魔法使い・治癒師・暗殺者; 現代・近未来の職業×幻想 | HR:ollama_hero_gen.py:902-905 | 未取り込み | Not in S2 | 取り込む（P1-04：S2 のタスク定義の規則、tables/cliches.yaml） |
| 生成規則 | Counterpart examples (few-shot) e.g. "Ghost Real Estate Agent…", "walk into photographs…rainy days" | HR:ollama_hero_gen.py:871-874,889-891,907-909; HR legacy …py:121-141 | 未取り込み | Harness cards carry no few-shot exemplars | 取り込む（P1-04：S2 のタスク定義の規則、tables/cliches.yaml） |
| 生成規則 | Counterpart scope: relative to a whole integrated character concept | HR:ollama_hero_gen.py:859-861 | 一部 | Harness counterpart is relative to 5 items from one material × one axis (story-pipeline.md:52) | 取り込む（P1-04：S2 のタスク定義の規則、tables/cliches.yaml） |
| 生成規則 | 入力を直接引用せず抽象的に解釈・再構築 | WB:expansion.yaml:43,51,72,80,101,109; WB v1.2 nb:230-256, 381-383 | 取り込み済み | story-pipeline.md:46 「原文の表現をそのまま引用しない」; LINEAGE.md:33 | 取り込み済み（story-pipeline.md:46） |
| 生成規則 | 「抽象的に解釈して拡張」 applied also to every world element (events…future) | WB v1.2 nb:381, 409, 431, 460, 491, 526…970 `[legacy]` | 一部 | S4 has no abstraction instruction (input is only section def + place/era + plot type, story-pipeline.md:81) | 取り込む（P1-04：S2 のタスク定義の規則、tables/cliches.yaml） |
| 生成規則 | Stock-phrase ban in concept/profile: "hero", "destiny", "darkness", "protect the innocent" / 「運命」「闇」「守るべきもの」「強い意志」 | HR:ollama_hero_gen.py:795, 837 | 未取り込み | No banned-cliché list in any S5/S7/F2 rule (common_words.yaml is an allow-list for proper-noun detection, task-model.md:239) | 取り込む（P1-04：S2 のタスク定義の規則、tables/cliches.yaml） |
| 生成規則 | 「属性に含まれる固有の言葉や具体的な描写は省略せずに残す」「属性にない出来事・場所・過去・人間関係・心情は加えない」 | HR:ollama_hero_gen.py:792-794, 834-836 | 一部 | Provenance via `sources` (VISION P3) + `no_new_proper_nouns` check; "keep specific wording" rule absent | 取り込む（P1-07：S5 のカード） |
| 生成規則 | 1出力1項目 prompt format (名前のみ／1文のみ／1段落) | HR:ollama_hero_gen.py:812-814, 853-854 | 取り込み済み | task-model.md:61; LINEAGE.md:31 | 取り込み済み（task-model.md:61） |
| 生成規則 | Seeded randomness / reproducible seed | HJ:colab_pipeline.py:167; WB:pipeline.py:421-428 | 取り込み済み | S3 (story-pipeline.md:61), data-layout (run_manifest) | 取り込み済み（S3：story-pipeline.md:61、run_manifest） |

## 5. 人物と役割の定義

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| 役割 | 4 roles protagonist/messenger/supporter/adversary | HJ:character_generator.py:18-31; WB:world_building.yaml:29-33 | 取り込み済み | GLOSSARY.md:38; story-pipeline.md:66-69; structures.schema enum | 取り込み済み（GLOSSARY.md:38、story-pipeline.md:66-69） |
| 役割 | 主人公 definition: 物語の中心人物であり、冒険と試練を通じて成長し、変化する存在。内的および外的な葛藤を克服することで、自己実現や世界への貢献を果たす | HJ:design-spec:414-416; HJ legacy nb:20767; WB v1.2 nb:~329 | 一部 | Role names only (GLOSSARY.md:38); no definition text reaches S5/S7 cards. LINEAGE.md:28 claims "定義" imported into S5 but S5 (L84-88) has none | 取り込む（P1-01：tables/roles.yaml、P1-07：S5 のカード） |
| 役割 | 使者 definition: 主人公に知恵や助言を与え、冒険への一歩を踏み出すきっかけを作る存在。成長のための方向性や目的意識を提供する | HJ:character_generator.py:215; design-spec:428-430 | 一部 | same gap; only structure stage `roles: [messenger]` at call stages | 取り込む（P1-01：tables/roles.yaml、P1-07：S5 のカード） |
| 役割 | 援助者/支援者 definition: 主人公をサポートしたり試練を通じて成長を促す。時に助け、時に混乱をもたらしながら新たな視点や気づきを与える | HJ:character_generator.py:251; design-spec:440-442 | 一部 | same gap (the "時に混乱をもたらす" ambivalence is lost) | 取り込む（P1-01：tables/roles.yaml、P1-07：S5 のカード） |
| 役割 | 敵対者 definition: 主人公が克服すべき外的・内的な障害や敵対者。英雄の成長を試す存在であり、恐怖、誘惑、葛藤を象徴する | HJ:character_generator.py:287; design-spec:449-451 | 一部 | same gap; `absent_role_note` covers "内面の力" variant (structures.yaml) | 取り込む（P1-01：tables/roles.yaml、P1-07：S5 のカード） |
| 役割 | 傍観者 `bystander` | (harness-only) | ハーネス独自 | story-pipeline.md:69 | ハーネス独自（先行リポジトリの要素ではない） |
| 人物の項目 | `name` | HR:ollama_hero_gen.py:801-818; HJ:character_generator.py:180-183; WB:world_building.yaml:42 | 取り込み済み（方式を置き換え） | S5 `name` + `reading` from name_sounds (story-pipeline.md:86, §5) | 取り込み済み（S5 `name`・`reading`：story-pipeline.md:86） |
| 人物の項目 | HR name rules: 英語表記, 国籍・文化・架空言語の名前も可, examples "Kain Astralion / Yuichi Aihara" | HR:ollama_hero_gen.py:808-816; HR legacy …py:77-86 | 除外 | story-pipeline.md:166 「LLM に名前を自由に作らせると、内部知識の典型的な名前に偏る（P4）。名前は、コードが与えた音から組み立てさせる。」 | 取り込む（P1-07：S5 のカード） |
| 人物の項目 | `profile` (HR 3-4文 JP; HJ 主人公400字/他200字; WB description 200-300字) | HR:ollama_hero_gen.py:820-839; HJ:character_generator.py:171,209; WB:world_building.yaml:44 | 取り込み済み | S5 `profile` ≤300字 (story-pipeline.md:87) | 取り込み済み（S5 `profile`：story-pipeline.md:87） |
| 人物の項目 | HR profile rules: 性別不明・Theyは「彼は」; 書くのは年齢・性別・種族・役割・能力・願望; 固有の言葉を省略せず具体的に | HR:ollama_hero_gen.py:829-836 | 一部 | S5 profile has input list but no content-rule list | 取り込む（P1-07：S5 のカード） |
| 人物の項目 | WB `short_introduction` 短い紹介文（50文字程度） | WB:world_building.yaml:43; WB v1.2 nb:327 | 未取り込み | cast has profile + motive only (story-pipeline.md:173) | 取り込む（P1-07：S5 の項目） |
| 人物の項目 | WB `description` must include 外見・人物像・魅力 (v1.2: 重要な要素や外見的な特徴、人物の魅力) | WB:world_building.yaml:44 | 一部 | S5 profile has no appearance/charm requirement; appearance explicitly narrowed (story-pipeline.md:130) | 取り込む（P1-07：S5 の項目） |
| 人物の項目 | `assigned_desire/ability/role` stored per character | WB:world_building.yaml:45-47; HR character.json attributes HR:ollama_hero_gen.py:1241-1248 | 取り込み済み | cast 割り当て要素 in story.json (story-pipeline.md:173) | 取り込み済み（story.json の cast：story-pipeline.md:173） |
| 人物の項目 | `motive` | (harness-only) | ハーネス独自 | S5 motive ≤120字 (story-pipeline.md:88) | ハーネス独自（先行リポジトリの要素ではない） |
| 人物の項目 | HR `concept` — English 1 paragraph (3-4 sentences) integrating 身体的特徴/役割/能力/願望 | HR:ollama_hero_gen.py:781-799; HR legacy …py:59-66 | 一部 | S5 profile integrates elements, but no separate English concept (used downstream for image prompts) | 取り込む（P1-07：S5 profile の入力で要素を統合する） |
| 人物の項目 | HR `catchphrase` 決め台詞 (一人称から始める, 1文, キャラにふさわしい口調; legacy 「抽象的に解釈して」) | HR:ollama_hero_gen.py:841-857; HR legacy …py:99-111 | 未取り込み | No catchphrase/voice line in cast; no dialogue-voice field feeding F2 | 取り込む（P1-07：S5 の項目） |
| 人物の項目 | HR `height_cm` (from concept+age+species) | HR:ollama_hero_gen.py:913-927, 976-985 | 未取り込み | Not in cast (could belong to visual-prompts P5-01) | 取り込む（P1-07：S5 の項目） |
| 人物の項目 | HR character.json schema (schema_version, id, iteration, name, height_cm, profile, catchphrase, concept, attributes, new_seeds, images, generation) | HR:ollama_hero_gen.py:1230-1258 | 一部 | story.json cast (story-pipeline.md:173); catchphrase/height/concept/images absent | 取り込み済み（story.schema.json の cast、P1-11） |
| 人物の生成 | Non-protagonists generated with 主人公の人物像 as context (一貫性の担保) | HJ:character_generator.py:211,247,283; design-spec:474 | 一部 | S5 `motive` sees other names+roles only (story-pipeline.md:88); `profile` sees no other character | 取り込む（P1-07：他の人物の項目の入力に主人公の名前・役・紹介を含める） |
| 人物の生成 | Each role re-draws its own narrative/want/ability/role elements | HJ legacy nb cell 10 (HJ legacy:20767-20840); design-spec:456-469 | 取り込み済み | S3 step 6 | 取り込み済み（S3 の手順6：story-pipeline.md:64-68） |
| 人物の生成 | Plot type fed into character generation (物語の構造) | HJ:character_generator.py:174,212; WB:world_building.yaml:17-18 | 未取り込み | S5 cards exclude plot type (story-pipeline.md:86-88) | 取り込む（P1-07：character_requirements） |
| 人物の生成 | HR full-body / turnaround images, image label (name + height) | HR:ollama_hero_gen.py:769-779, 1143-1227; HR:image_labels.py:1 | 未取り込み | Harness produces no images; not stated as excluded (LINEAGE.md:53 only moves *prompts* to P5) | 外す（本リポジトリは物語の生成まで。画像生成は heroes の範囲） |

## 6. プロット型

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| プロット型 | 1 旅（クエスト） Quest | HJ:colab_features.py:243; design-spec:489 | 取り込み済み | H:tables/plot_types.yaml:2 `quest` | 取り込み済み（tables/plot_types.yaml:2） |
| プロット型 | 2 モンスターを倒す Overcoming The Monster | HJ:colab_features.py:244 | 取り込み済み | plot_types.yaml:13 | 取り込み済み（tables/plot_types.yaml:13） |
| プロット型 | 3 成り上がり Rags to Riches | :245 | 取り込み済み | plot_types.yaml:24 | 取り込み済み（tables/plot_types.yaml:24） |
| プロット型 | 4 再生 Rebirth | :246 | 取り込み済み | plot_types.yaml:35 | 取り込み済み（tables/plot_types.yaml:35） |
| プロット型 | 5 ラブストーリー Romance / Forbidden Love (legacy: 恋愛・禁断の愛など) | :247; HJ legacy nb:~605 | 取り込み済み | plot_types.yaml:46 (禁断の愛 nuance dropped) | 取り込み済み（tables/plot_types.yaml:46） |
| プロット型 | 6 ミステリー／犯罪 | :248 | 取り込み済み | plot_types.yaml:57 | 取り込み済み（tables/plot_types.yaml:57） |
| プロット型 | 7 悲劇 | :249 | 取り込み済み | plot_types.yaml:68 | 取り込み済み（tables/plot_types.yaml:68） |
| プロット型 | 8 帰還 Voyage and Return | :250 | 取り込み済み | plot_types.yaml:79 | 取り込み済み（tables/plot_types.yaml:79） |
| プロット型 | 9 コメディ | :251 | 取り込み済み | plot_types.yaml:90 | 取り込み済み（tables/plot_types.yaml:90） |
| プロット型 | 10 サバイバル／ディストピア | :252 | 取り込み済み | plot_types.yaml:101 | 取り込み済み（tables/plot_types.yaml:101） |
| プロット型 | 11 復讐 | :253 | 取り込み済み | plot_types.yaml:112 | 取り込み済み（tables/plot_types.yaml:112） |
| プロット型 | 12 タイムループ／時間改変 | :254 | 未取り込み（計画あり） | story-pipeline.md:135 「専用テンプレートを持つ7型は P2-08 で加える」 | 取り込む（P2-08：tables/plot_types.yaml） |
| プロット型 | 13 陰謀・政治劇 | :255 | 取り込み済み | plot_types.yaml:123 | 取り込み済み（tables/plot_types.yaml:123） |
| プロット型 | 14 人間ドラマ | :256 | 取り込み済み | plot_types.yaml:134 | 取り込み済み（tables/plot_types.yaml:134） |
| プロット型 | 15 シュルレアリスム／超現実 | :257 | 未取り込み（計画あり P2-08） | — | 取り込む（P2-08：tables/plot_types.yaml） |
| プロット型 | 16 断片的構成（フラグメント／コラージュ／パスティーシュ） | :258 | 未取り込み（計画あり P2-08） | — | 取り込む（P2-08：tables/plot_types.yaml） |
| プロット型 | 17 メタフィクション／ポストモダン | :259 | 未取り込み（計画あり P2-08） | — | 取り込む（P2-08：tables/plot_types.yaml） |
| プロット型 | 18 詩的・抽象的（リリカル） | :260 | 未取り込み（計画あり P2-08） | — | 取り込む（P2-08：tables/plot_types.yaml） |
| プロット型 | 19 哲学的／存在論的 | :261 | 取り込み済み | plot_types.yaml:145 | 取り込み済み（tables/plot_types.yaml:145） |
| プロット型 | 20 ドキュメンタリー／エッセイ | :262 | 未取り込み（計画あり P2-08） | — | 取り込む（P2-08：tables/plot_types.yaml） |
| プロット型 | 21 アンチクライマックス／無為 | :263 | 未取り込み（計画あり P2-08） | — | 取り込む（P2-08：tables/plot_types.yaml） |
| プロット型 | WB: LLM-generated 10 plot types from 神話・伝承・古今東西の物語 | WB:expansion.yaml:129-159; WB v1.2 nb:~268 | 除外 | LINEAGE.md:48 「21型を固定テーブルにし、選択は S3（コード）で行う。LLMの呼び出しをなくす」 | 外す（プロット型は固定テーブル、選択はコード：VISION P2） |
| プロット型の項目 | 中核構造 `core_structure` | HJ:colab_features.py:191; design-spec:515; WB:expansion.yaml:134 | 取り込み済み | plot_types.yaml `core` | 取り込み済み（tables/plot_types.yaml `core`） |
| プロット型の項目 | 必須イベント `required_events` (最低3つの転換点) | HJ:colab_features.py:192; design-spec:516; WB:expansion.yaml:135 | 取り込み済み | plot_types.yaml `required_events` (3 each, mapped per template) | 取り込み済み（tables/plot_types.yaml `required_events`） |
| プロット型の項目 | キャラクター要件 `character_requirements` (主要人物に必須の属性・関係性) | HJ:colab_features.py:193; design-spec:517; WB:expansion.yaml:136 | 未取り込み | Not in plot_types.schema; e.g. romance needs 2 leads, revenge needs a wrongdoer — S3 role assignment ignores plot type | 取り込む（P1-01：tables/plot_types.yaml、P1-05/P1-07/P1-09 で使う） |
| プロット型の項目 | 時間設計原理 `time_design` / `temporal_design_principles` | HJ:colab_features.py:194; design-spec:518; WB:expansion.yaml:137 | 未取り込み | Critical for timeloop/fragment types; only mentioned as future "専用テンプレート" | 取り込む（P1-01：tables/plot_types.yaml、P1-05/P1-07/P1-09 で使う） |
| プロット型の項目 | 葛藤の種類 `conflict` / `types_of_conflict` | HJ:colab_features.py:195; design-spec:519; WB:expansion.yaml:138 | 未取り込み | — | 取り込む（P1-01：tables/plot_types.yaml、P1-05/P1-07/P1-09 で使う） |
| プロット型の項目 | クライマックス条件 `climax` / `climax_conditions` | HJ:colab_features.py:196; design-spec:520; WB:expansion.yaml:139 | 未取り込み | — | 取り込む（P1-01：tables/plot_types.yaml、P1-05/P1-07/P1-09 で使う） |
| プロット型の項目 | 緩急の原則 `pacing` / `principles_of_temp` | HJ:colab_features.py:197; design-spec:521; WB:expansion.yaml:140 | 一部 | Stage `weight` (structures.yaml) gives event distribution, not tension rhythm | 取り込む（P1-01：tables/plot_types.yaml、P1-05/P1-07/P1-09 で使う） |
| プロット型の項目 | 典型的な物語設定 `typical_story_setting` (v1.2: 典型的な物語中の時代設定) | WB:expansion.yaml:141; WB v1.2 nb:280 | 未取り込み | Not in plot_types; S4 gets plot-type *name* only | 取り込む（P1-01：tables/plot_types.yaml、P1-05/P1-07/P1-09 で使う） |
| プロット型の項目 | `customization_notes` (adapt type to user context) | WB:expansion.yaml:189 | 除外 | LINEAGE.md:48 (no LLM selection/adaptation) | 取り込む（P1-01：tables/plot_types.yaml、P1-05/P1-07/P1-09 で使う） |
| プロット型の選択 | HJ LLM chooses top-3 fitting types from 21 then random pick | HJ:colab_features.py:283-317; colab_pipeline.py:425-435 | 除外 | LINEAGE.md:48; VISION.md:39 「LLMに「最も良いもの」を選ばせない」 | 外す（選択はコードで行う：VISION P2） |
| プロット型の選択 | WB LLM selects 1 optimal type | WB:expansion.yaml:167-191 | 除外 | same | 外す（選択はコードで行う：VISION P2） |
| プロット型の選択 | `--plot-type` fixed override | HJ:run_pipeline.py:71; colab_pipeline.py:430-434 | 取り込み済み | S3 step 3 (story-pipeline.md:61) | 取り込み済み（S3 の手順3：story-pipeline.md:61） |
| プロット型の選択 | Multiple plot types per story (main/sub/parts) | (harness-only) | ハーネス独自 | story-pipeline.md:61 | ハーネス独自（先行リポジトリの要素ではない） |

## 7. 構造の段階（ヒーローズ・ジャーニー）と段階ごとの指針

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| 構造 | 12-stage list JOURNEY_STAGES_12 | HJ:src/plot_generator.py:34-47 | 取り込み済み | H:tables/structures.yaml:62-159 `heros-journey-12` | 取り込み済み（tables/structures.yaml:62-159 `heros-journey-12`） |
| 構造 | 11-stage legacy list (no 最も危険な場所への接近) | HJ:plot_generator.py:19-31; design-spec:546-558 | 取り込み済み（上位集合） | 12-stage template contains all 11 | 外す（12段階に統合済み） |
| 構造 | 幕 (act) assignment 第一幕 1-4 / 第二幕 5-8 / 第三幕 9-12 | HJ:plot_generator.py:35-46; design-spec:701-704; HJ legacy nb cell 30 | 未取り込み | LINEAGE.md:27 says 「三幕への割付」 imported into structures.yaml, but no `act` field exists in structures.yaml / structures.schema.json | 取り込む（P1-01：tables/structures.yaml） |
| 構造 | 12→10 chapter grouping [(0),(1),(2),(3),(4),(5),(6,7),(8),(9,10),(11)] | HJ:src/story_generator.py:578-635 (groups 593-596) | 未取り込み（計画あり） | format-pipeline.md:76 「heros-journey の12段階→10章の割付を P3-02 で加える」 | 取り込む（P3-02：12段階→10章の割付） |
| 構造 | Generic stage→chapter grouping (divmod, remainder to later chapters) | HJ:story_generator.py:597-606 | 一部 | unit_mapping `structure` (format-pipeline.md:76) = one chapter per stage; no N-chapter compression | 取り込む（P3-01：F1 の割付） |
| 構造 | Stage 1 日常世界 — 目的: 普段の生活、環境、価値観、現状維持の姿勢; ポイント: 欠点や不満・未解決の課題を提示／読者が共感できる要素を強調／「変化前」の基準点 | HJ:design-spec:548,562-565; HJ legacy nb:22338+ | 一部 | structures.yaml:64-71 covers 生活・環境・価値観・現状維持・基準点; 欠点や不満 and 読者の共感 missing | 取り込む（P1-01：tables/structures.yaml） |
| 構造 | Stage 2 冒険への呼びかけ — 異常事態、事件、人物との出会いで現状を揺るがす／「選択」を迫る | HJ:design-spec:549,567-569 | 取り込み済み | structures.yaml:72-79 | 取り込み済み（tables/structures.yaml:72-79） |
| 構造 | Stage 3 拒否 — 冒険の重大さを強調; 恐れ、不安、責任感、無力感など内的・外的葛藤／拒否理由を納得できるものに | HJ:design-spec:550,571-573 | 一部 | structures.yaml:80-87 has 葛藤 list; 「重大さを強調」「理由を納得できるものに」 missing | 取り込む（P1-01：tables/structures.yaml） |
| 構造 | Stage 4 師との出会い — 勇気や知恵; メンターは精神的な支え／言葉や行動が内面に変化 | HJ:design-spec:551,575-577 | 取り込み済み | structures.yaml:88-95 (精神的な支え nuance partly) | 取り込み済み（tables/structures.yaml:88-95） |
| 構造 | Stage 5 第一関門の突破 — 「戻れない一線」／新たな環境、ルール、危険 | HJ:design-spec:552,579-581 | 取り込み済み | structures.yaml:96-103 | 取り込み済み（tables/structures.yaml:96-103） |
| 構造 | Stage 6 試練、仲間、敵 — 友情・信頼・裏切り／小さな成功と失敗で段階的成長 | HJ:design-spec:553,583-585 | 取り込み済み | structures.yaml:104-111 | 取り込み済み（tables/structures.yaml:104-111） |
| 構造 | Stage 7 最も危険な場所への接近 (12-stage only; no guidance text in HJ) | HJ:plot_generator.py:41 | 取り込み済み | structures.yaml:112-119 (definition authored in harness) | 取り込み済み（tables/structures.yaml:112-119） |
| 構造 | Stage 8 最大の試練 — 肉体的・精神的に最も厳しい挑戦／「死と再生」の象徴的体験 | HJ:design-spec:554,587-589 | 取り込み済み | structures.yaml:120-127 | 取り込み済み（tables/structures.yaml:120-127） |
| 構造 | Stage 9 報酬 — 物理的な宝、知恵、自己理解、仲間との絆／**達成感と同時に新たな課題や責任が見えてくる** | HJ:design-spec:555,591-593 | 一部 | structures.yaml:128-135 lacks 新たな課題や責任 | 取り込む（P1-01：tables/structures.yaml） |
| 構造 | Stage 10 帰路 — 帰還途中の新たな障害や葛藤／クライマックスに向けた緊張感の再構築 | HJ:design-spec:556,595-597 | 取り込み済み | structures.yaml:136-143 | 取り込み済み（tables/structures.yaml:136-143） |
| 構造 | Stage 11 復活 — 古い自分の死と新しい自分の誕生／新しい理解や価値観 | HJ:design-spec:557,599-601 | 取り込み済み | structures.yaml:144-151 | 取り込み済み（tables/structures.yaml:144-151） |
| 構造 | Stage 12 宝を持ち帰る — 宝は知恵・成長・人間関係など多様／変化が周囲の世界や他者に影響 | HJ:design-spec:558,603-605 | 取り込み済み | structures.yaml:152-159 | 取り込み済み（tables/structures.yaml:152-159） |
| 構造 | Per-stage 50字 description generation (LLM expands each stage for the story) | HJ:plot_generator.py:164-189 | 除外／置き換え | LINEAGE.md:50 「S6（構造展開・コード）と S7（出来事1件ずつ）に置き換える」 | 外す（出来事1件ずつの生成に置き換え：VISION P1） |
| 構造 | Plot outline 800字 (成長と変化を明確に／三幕構成を意識) | HJ:plot_generator.py:258-279; Plot.outline :68-71 | 除外／置き換え | LINEAGE.md:50. Note: no whole-story arc summary exists in canonical story (story.md is template-only) | 外す（出来事1件ずつの生成に置き換え：VISION P1） |
| 構造 | Integrated final plot from skeleton+characters+world+12 stages | HJ:design-spec:681-707; HJ legacy nb cell 30 | 除外／置き換え | LINEAGE.md:50 | 外す（出来事1件ずつの生成に置き換え：VISION P1） |
| 構造 | 起承転結 / 3-beat templates | (harness-only) | ハーネス独自 | structures.yaml:2-61 | ハーネス独自（先行リポジトリの要素ではない） |

## 8. プロットの骨子A〜E

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| 骨子 | Skeleton A–E as a generation step | HJ:colab_features.py:373-508; design-spec:629-677; HJ legacy nb cells 25-29 | 除外／置き換え | LINEAGE.md:50 「heros-journey 骨子A〜E＋12段階＋アウトライン … S6 … S7 に置き換える」. Sub-elements below checked for survival in stage definitions | 外す（出来事1件ずつの生成に置き換え：VISION P1） |
| 骨子A | 日常の世界 | HJ:colab_features.py:401; design-spec:637 | 取り込み済み | ordinary-world stage | 取り込み済み（tables/structures.yaml ordinary-world） |
| 骨子A | 非日常の世界 | HJ:colab_features.py:401; design-spec:638 | 一部 | threshold stage 「未知の環境、規則、危険」; no explicit "special world" setting object; HJ world gen was framed as 非日常世界 (colab_features.py:346) | 取り込む（P1-01：tables/structures.yaml の段階の指針） |
| 骨子A | 主人公が欠落させているもの | HJ:design-spec:639 | 一部 | narrative `missing` only enters as S1 material; no protagonist `lack` field / stage requirement | 取り込む（P1-01：tables/structures.yaml の段階の指針） |
| 骨子A | 主人公の前に現れる予兆 | HJ:design-spec:640 | 未取り込み | No omen/foreshadowing slot | 取り込む（P1-01：tables/structures.yaml の段階の指針） |
| 骨子A | 主人公の元に訪れる使者 | HJ:design-spec:641 | 取り込み済み | call-to-adventure `roles: [messenger]` | 取り込み済み（tables/structures.yaml call-to-adventure） |
| 骨子A | 召喚を拒絶させる要素 | HJ:design-spec:642 | 取り込み済み | refusal stage | 取り込み済み（tables/structures.yaml refusal） |
| 骨子A | 主人公が使者からもらうもの | HJ:design-spec:643 | 一部 | `object: true` only from mentor stage on; no "gift from messenger" requirement | 取り込む（P1-01：tables/structures.yaml の段階の指針） |
| 骨子B | 主人公のパーティーの構成 | HJ:design-spec:650 | 一部 | trials stage roles [supporter, adversary]; no party-formation concept | 取り込む（P1-01：tables/structures.yaml の段階の指針） |
| 骨子B | 主人公の挫折のエピソード | HJ:design-spec:651 | 未取り込み | No setback beat outside ordeal | 取り込む（P1-01：tables/structures.yaml の段階の指針） |
| 骨子B | 主人公の使者からもらうもの（得たもの） | HJ:design-spec:652 | 一部 | as above | 取り込む（P1-01：tables/structures.yaml の段階の指針） |
| 骨子C | 最も危険な場所 | HJ:design-spec:659 | 取り込み済み | approach stage | 取り込み済み（tables/structures.yaml approach） |
| 骨子C | 敵対者 | HJ:design-spec:660 | 取り込み済み | approach/ordeal roles | 取り込み済み（tables/structures.yaml approach・ordeal） |
| 骨子C | 最大の試練 (colab version) | HJ:colab_features.py:403 | 取り込み済み | ordeal stage | 取り込み済み（tables/structures.yaml ordeal） |
| 骨子D | 日常への帰還におけるイベント | HJ:design-spec:667 | 取り込み済み | road-back stage | 取り込み済み（tables/structures.yaml road-back） |
| 骨子D | 主人公が代償として失ったもの | HJ:design-spec:668 | 未取り込み | Narrative `loss` never mapped to a required "cost paid" beat | 取り込む（P1-01：tables/structures.yaml の段階の指針） |
| 骨子E | 新しい日常の世界 | HJ:design-spec:675 | 取り込み済み | return-with-elixir stage | 取り込み済み（tables/structures.yaml return-with-elixir） |
| 骨子E | 主人公が得たもの | HJ:design-spec:676 | 取り込み済み | reward / return-with-elixir | 取り込み済み（tables/structures.yaml reward・return-with-elixir） |
| 骨子E | 周囲への影響 | HJ:colab_features.py:405 | 取り込み済み | return-with-elixir 「周囲にも及ぶ」 | 取り込み済み（tables/structures.yaml return-with-elixir） |

## 9. プロット／章／出来事の項目

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| 章の項目 | どのような出来事が起きたのか (HJ) / `events` 発生した事件 (WB) | HJ legacy nb:21890-21896; design-spec:532; WB:plot_generation.yaml:174 | 取り込み済み | S7 `what` (story-pipeline.md:108) | 取り込み済み（S7 `what`：story-pipeline.md:108） |
| 章の項目 | `situation` 物語世界の状況 | WB:plot_generation.yaml:173; WB v1.2 nb:1026 | 一部 | S7 `when`/`where` + world excerpts; no world-state field per event | 取り込む（P1-09：when・where） |
| 章の項目 | 主人公がどんな感情を抱いたのか / `protagonist_emotions` 主人公の**受動的**感情 | HJ design-spec:533; HJ legacy nb:21894; WB:plot_generation.yaml:175 | 未取り込み | S7 schema has no emotion field (when/where/who/why/what/result). Duplicated in HJ+WB — high-value gap | 取り込む（P1-09：S7 の出力の項目） |
| 章の項目 | 主人公がどんな意思と行動を示したのか / `protagonist_actions` | HJ design-spec:534; WB:plot_generation.yaml:176 | 一部 | S7 `why` (intent) + `what` (action) but not bound to protagonist specifically | 取り込む（P1-09：S7 の出力の項目） |
| 章の項目 | それによって状況にどんな変化が起きたのか / `situation_change` | HJ design-spec:535; WB:plot_generation.yaml:177 | 取り込み済み | S7 `result` | 取り込み済み（S7 `result`：story-pipeline.md:108） |
| 章の項目 | `foreshadowing` 次章への伏線（第1〜9章） | WB:plot_generation.yaml:178; WB v1.2 nb:1031 | 未取り込み | S7 sees only previous `result` (story-pipeline.md:107); no forward setup/payoff mechanism | 取り込む（P1-09：S7 の出力の項目） |
| 章の項目 | `conclusion` 物語の締めくくり（第10章）「必ず物語を締めくくる」 | WB:plot_generation.yaml:179; WB v1.2 nb:1032 | 一部 | Final stages (resolution/ketsu/return-with-elixir) define closure; no explicit rule on last slot | 取り込む（P1-09：S7 の出力の項目） |
| 章の項目 | 章タイトル (文学的な表現に改変) | HJ:plot_generator.py:422,426; design-spec:726 | 未取り込み | F2/F3 novel rules (format-pipeline.md:28-31) have no chapter-title rule | 取り込む（P3-01：様式化の題のタスク） |
| 章の項目 | 10-chapter plot as unit | HJ legacy nb cell 18; WB:plot_generation.yaml:162-206 | 除外／置き換え | LINEAGE.md:50; chapter count derived from structure/scale | 取り込む（P3-02） |
| 章の項目 | `extract_chapter` (LLM splits plot into chapters) | WB:plot_generation.yaml:214-230 | 除外 | LINEAGE.md:52 「章の切り出し・参照検索 … コードで行う」 | 外す（コードによる割付とセクションの指定に置き換え：VISION P2） |
| 章の項目 | `extract_keywords` ≤10 per chapter, viewpoints: 重要な場所・舞台／アイテム・道具／概念・テーマ／出来事／感情 | WB:plot_generation.yaml:238-258; WB v1.2 nb:1086 | 除外 | LINEAGE.md:52; replaced by S6 step 7 world-section selection (story-pipeline.md:99) | 外す（コードによる割付とセクションの指定に置き換え：VISION P2） |
| 章の項目 | `search_references` ≤20, fields `source`/`content`/`relevance` | WB:plot_generation.yaml:266-289; WB v1.2 nb:1109 | 除外（置き換え） | LINEAGE.md:52; S7 gets ≤2 world sections chosen by code. Note: persona list & characters no longer searchable per chapter | 外す（コードによる割付とセクションの指定に置き換え：VISION P2） |
| 出来事の項目 | when / where / who / why / what / result | (harness-only) | ハーネス独自 | story-pipeline.md:108 | ハーネス独自（先行リポジトリの要素ではない） |

## 10. 世界設定のカテゴリと下位項目

### 10.1 世界の上位カテゴリ

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| 世界 | HJ 4-item 非日常世界 (社会構造・組織体・生活風習・人々), input = narrative + analysis | HJ:colab_features.py:346-360; design-spec:609-625; HJ legacy nb cell 24 | 取り込み済み（統合） | world_sections.yaml `social_structure`, `organizations`, `customs`, `people`; LINEAGE.md:49. Gap: S4 input excludes the narrative/analysis (story-pipeline.md:81) | 取り込み済み（tables/world_sections.yaml social_structure・organizations・customs・people） |
| 世界 | WB 10-step cumulative chain events→observation→interpretation→media→past→social_structure→living_env→social_groups→people_list→future | WB:src/pipeline.py:1043-1085; DESIGN_SPEC.md:317-345 | 除外（依存関係） | story-pipeline.md:81 「他のセクションの内容は渡さない。」 — sections are independent; world coherence across sections not enforced | 取り込む（P1-06：セクションの前提セクション（最大2件）の本文を入力に含める） |
| 世界 | Generation gating by scale (sections cumulative by level 0-4) | (harness-only) | ハーネス独自 | scales.yaml:142-147; only `place` at level 0 — events/observation/media/past_events need level 3, future level 4 | ハーネス独自（先行リポジトリの要素ではない） |
| 世界 | Input to world gen: WB events uses plot type only; HJ uses narrative | WB:world_building.yaml:73-74; HJ:colab_features.py:353-359 | 一部 | S4 input = section def + place + era + plot type name | 取り込み済み（story-pipeline S4 の入力） |
| 世界 | Output length (WB unbounded, v1.2 "詳細に"; harness 400字) | WB:world_building.yaml:76 | 一部 | S4 body ≤400字 (story-pipeline.md:82) — sub-field depth necessarily lost | 取り込む（P1-01：world_sections の max_chars） |
| 世界 | 場の描写 `place` section | (harness-only) | ハーネス独自 | world_sections.yaml:2-8 | ハーネス独自（先行リポジトリの要素ではない） |

### 10.2 HJ の世界の下位項目（4カテゴリ×項目）

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| 世界/HJ social_structure | 発展の指向性 | HJ:colab_features.py:348; design-spec:622 | 取り込み済み | world_sections.yaml:45 | 取り込み済み（tables/world_sections.yaml:45） |
| 世界/HJ social_structure | 構造維持の仕組み | same | 取り込み済み | world_sections.yaml:46 | 取り込み済み（tables/world_sections.yaml:46） |
| 世界/HJ social_structure | 大きな課題 | same | 取り込み済み | world_sections.yaml:47 | 取り込み済み（tables/world_sections.yaml:47） |
| 世界/HJ social_structure | 発展阻害要因 | same | 取り込み済み | world_sections.yaml:47 | 取り込み済み（tables/world_sections.yaml:47） |
| 世界/HJ organizations | 組織が指向する理想像 | HJ:colab_features.py:349; design-spec:623 | 取り込み済み | world_sections.yaml:37 | 取り込み済み（tables/world_sections.yaml:37） |
| 世界/HJ organizations | 構成要素 | same | 取り込み済み | :38 | 取り込み済み（tables/world_sections.yaml:38） |
| 世界/HJ organizations | 施設・設備 | same | 取り込み済み | :38 | 取り込み済み（tables/world_sections.yaml:38） |
| 世界/HJ organizations | 抱える課題 | same | 取り込み済み | :39 | 取り込み済み（tables/world_sections.yaml:39） |
| 世界/HJ customs | 公共貢献と考えられる行動 | HJ:colab_features.py:350; design-spec:624 | 取り込み済み | world_sections.yaml:21 | 取り込み済み（tables/world_sections.yaml:21） |
| 世界/HJ customs | 重要視される文化・思想 | same | 取り込み済み | :22 | 取り込み済み（tables/world_sections.yaml:22） |
| 世界/HJ customs | 軋轢・緊張 | same | 取り込み済み | :23 | 取り込み済み（tables/world_sections.yaml:23） |
| 世界/HJ people | 社会における役割 | HJ:colab_features.py:351; design-spec:625 | 取り込み済み | world_sections.yaml:29 | 取り込み済み（tables/world_sections.yaml:29） |
| 世界/HJ people | 重要な行動指針 | same | 取り込み済み | :30 | 取り込み済み（tables/world_sections.yaml:30） |
| 世界/HJ people | 個人的な問題 | same | 取り込み済み | :31 | 取り込み済み（tables/world_sections.yaml:31） |

### 10.3 WB `events`（物理現象 → ハーネス `events` 世界の法則）

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| 世界/events | cosmic.`spacetime` 時空間の構造 | WB:world_building.yaml:77,84; v1.2 nb:382 | 取り込み済み | world_sections.yaml:53 「時空・相互作用・エネルギーと秩序」 | 取り込み済み（tables/world_sections.yaml:53） |
| 世界/events | cosmic.`interactions` 相互作用 | WB:world_building.yaml:85 | 取り込み済み | :53 | 取り込み済み（tables/world_sections.yaml:53） |
| 世界/events | cosmic.`quantum_fields` 量子場 | WB:world_building.yaml:86 | 一部 | folded into 「エネルギー」; field concept not named | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/events | cosmic.`entropy` エントロピー (v1.2: 情報量) | WB:world_building.yaml:87; v1.2 nb:385 | 一部 | 「秩序」 only; information-quantity reading lost | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/events | terrestrial.`crustal_movement` 地殻変動 | WB:world_building.yaml:90 | 一部 | 「大地…の成り立ちと変動」 | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/events | terrestrial.`surface_structure` 地表構造 (v1.2: 地表の成分) | WB:world_building.yaml:91 | 一部 | 「大地」 | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/events | terrestrial.`ecosystem` 生態系 | WB:world_building.yaml:92 | 取り込み済み | :54 | 取り込み済み（tables/world_sections.yaml:54） |
| 世界/events | (harness adds 気候) | — | ハーネス独自 | :54 | ハーネス独自（先行リポジトリの要素ではない） |

### 10.4 WB `observation`

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| 世界/observation | `sensory_input` 感覚入力 | WB:world_building.yaml:110,118; v1.2 nb:409 | 取り込み済み | world_sections.yaml:60 | 取り込み済み（tables/world_sections.yaml:60） |
| 世界/observation | `measurement_devices` 測定装置 | WB:world_building.yaml:111,119 | 取り込み済み | :61 (道具が何を測り、何を見落とすか) | 取り込み済み（tables/world_sections.yaml:61） |
| 世界/observation | `predictive_models` 予測モデル | WB:world_building.yaml:112,120 | 取り込み済み | :62 | 取り込み済み（tables/world_sections.yaml:62） |
| 世界/observation | `explanatory_theories` 説明理論 | WB:world_building.yaml:113,121 | 取り込み済み | :62 | 取り込み済み（tables/world_sections.yaml:62） |

### 10.5 WB `interpretation`

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| 世界/interpretation | `mythological` 神話伝承 | WB:world_building.yaml:141,150; v1.2 nb:431 | 取り込み済み | world_sections.yaml:13 | 取り込み済み（tables/world_sections.yaml:13） |
| 世界/interpretation | `geometric_philosophical` 幾何的・哲学的解釈 | WB:world_building.yaml:142,151 | 一部 | :14 「哲学や経験則」 — geometric dropped, merged with empirical | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/interpretation | `empirical` 経験主義的解釈 (v1.2 "Pietistic") | WB:world_building.yaml:143,152 | 一部 | merged into :14 | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/interpretation | `relativistic_quantum` 相対論的・量子論的解釈 | WB:world_building.yaml:144,153 | 一部 | :15 「科学的説明」 generic | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/interpretation | `informational_computational` 情報・計算的解釈 | WB:world_building.yaml:145,154 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |

### 10.6 WB `media`（記録）

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| 世界/media | `biological` 生体媒体（記憶、口伝）; v1.2 traits: 不可視、超高密度、低速の読み書き、複製劣化あり | WB:world_building.yaml:177,186; v1.2 nb:460 | 取り込み済み（特性は未取り込み） | world_sections.yaml:68 | 取り込み済み（tables/world_sections.yaml:68） |
| 世界/media | `static_engraved` 静的刻記媒体（石碑、彫刻）; traits: 可視、長期保存、容量は面積依存 | WB:world_building.yaml:178,187; v1.2 nb:461 | 取り込み済み（統合） | :69 「刻印や文字」 | 取り込み済み（tables/world_sections.yaml:69） |
| 世界/media | `static_written` 静的筆記媒体（紙、羊皮紙）; traits same | WB:world_building.yaml:179,188; v1.2 nb:462 | 取り込み済み（統合） | :69 | 取り込み済み（tables/world_sections.yaml:69） |
| 世界/media | `analog_signal` アナログ信号媒体（音声、映像）; traits: 不可視、波形の忠実保存、高速、複製劣化あり | WB:world_building.yaml:180,189; v1.2 nb:463 | 一部 | :70 「音やその他の媒体」 (video/signal not named) | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/media | `digital` デジタル媒体; traits: 不可視、高密度、複製劣化なし、高速、機器依存 | WB:world_building.yaml:181,190; v1.2 nb:464 | 未取り込み | No digital medium viewpoint | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/media | Media trait axes (visibility, density, R/W speed, copy degradation, device dependence) | WB v1.2 nb:460-464 `[legacy]` | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |

### 10.7 WB `important_past_events`（→ ハーネス `past_events`）

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| 世界/past | Count: 有史以来の最重要イベント 10件 | WB:world_building.yaml:201; v1.2 nb:491 | 未取り込み | single 400字 body | 取り込む（P1-06：規模に応じた件数を1件1タスクで生成） |
| 世界/past | `event_name` イベント名 | WB:world_building.yaml:209,218 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/past | `approximate_date` おおよその時期 | WB:world_building.yaml:210,219 | 未取り込み | (also no world chronology anywhere) | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/past | `description` 詳細な説明（200〜300文字） | WB:world_building.yaml:211,220 | 一部 | body text | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/past | `impact` 世界への影響 | WB:world_building.yaml:212,221 | 取り込み済み | world_sections.yaml:76 「現在の状況を形づくった転機」 | 取り込み済み（tables/world_sections.yaml:76） |
| 世界/past | (harness adds 記録・語り継ぎ, 記憶の違いによる対立) | — | ハーネス独自 | :77-78 | ハーネス独自（先行リポジトリの要素ではない） |

### 10.8 WB `social_structure` — 12カテゴリ（現行コード）

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| 世界/social_structure | 1 政治体制 `political_system` | WB:world_building.yaml:243,259 | 未取り込み | Harness social_structure = 3 HJ viewpoints only (world_sections.yaml:44-48) | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/social_structure | 2 社会階層 `social_hierarchy` | :244,260 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/social_structure | 3 経済システム `economic_system` | :245,261 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/social_structure | 4 文化・宗教 `culture_religion` | :246,262 | 一部 | customs 「重要視される文化や思想」 (religion not named) | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/social_structure | 5 法律・治安 `law_security` | :247,263 | 一部 | 「構造や秩序を維持する仕組み」 | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/social_structure | 6 教育・技術 `education_technology` | :248,264 | 一部 | era axis 技術 (element_axes.yaml:17-19); no education | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/social_structure | 7 環境・生態系 `environment_ecology` | :249,265 | 一部 | events 生態系 / place | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/social_structure | 8 インフラ・交通 `infrastructure_transport` | :250,266 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/social_structure | 9 生活様式 `lifestyle` | :251,267 | 一部 | customs | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/social_structure | 10 コミュニケーション `communication` | :252,268 | 一部 | media (records only, not communication) | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/social_structure | 11 芸術・娯楽 `arts_entertainment` | :253,269 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/social_structure | 12 その他の特徴 `other_features` | :254,270 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |

### 10.9 WB `social_structure` の詳細指標 `[legacy]`（v1.2 ノートブック。現行コードにはない）

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| 世界/ss-legacy | 政治・社会構造 › 国家体制: 統治形態の種類／権力集中度や分権度／政策の安定性・変動性／国家間の連携・対立状況／国内の政治構造（議会、貴族院、軍事組織など） | WB v1.2 nb:525-531 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/ss-legacy | 政治・社会構造 › 社会階層: 階層の区分／社会流動性の度合い／経済・文化資源の分布と集中度／社会的信用・評価システム／階層間の交流および対立の様相 | WB v1.2 nb:532-537 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/ss-legacy | 経済システム › 通貨・交易: 通貨の種類・価値安定性・インフレ率／交易ルート（主要ルート、障害要因、季節変動）／貿易品目と供給チェーン／取引頻度と市場参加者数／貿易規制や税制の影響 | WB v1.2 nb:538-544 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/ss-legacy | 経済システム › 生産・消費: 各産業の生産能力（農業、工業、手工業）／資源分布と採取難易度／消費パターンと需要変動／労働力供給・賃金体系・雇用率／生産・流通コストと効率性 | WB v1.2 nb:545-550 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/ss-legacy | 文化と宗教 › 言語・習慣: 言語体系・方言・文字体系の多様性／習慣や伝統行事の頻度・形式／文化的アイデンティティとその変遷／文化資産の保存と普及度／教育機関やメディアの影響度 | WB v1.2 nb:551-557 | 一部 | customs covers 習慣 loosely | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/ss-legacy | 文化と宗教 › 宗教・信仰: 宗教体系（多神教、一神教、精霊信仰）／教義・儀式・祭礼／信者数・拡散率・布教／宗教組織の構造・権威・資金源／宗教的禁忌・倫理規範 | WB v1.2 nb:558-563 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/ss-legacy | 法律と秩序 › 法体系: 厳格性・柔軟性・適用範囲／犯罪種類と頻度／裁判制度の構造・透明性・公平性／罰則・再犯率・改正履歴／法遵守意識 | WB v1.2 nb:564-570 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/ss-legacy | 法律と秩序 › 治安維持: 警察・衛兵・民兵の規模と装備／治安予算／犯罪予防の成熟度／市民の安全意識・通報システム／治安指標 | WB v1.2 nb:571-576 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/ss-legacy | 教育と技術 › 教育機関: 種類（学校、図書館、師弟制度）と規模／カリキュラム・伝承方法・評価基準／学習者数・進学率／教育予算／制度の変遷 | WB v1.2 nb:577-583 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/ss-legacy | 教育と技術 › 技術レベル: 科学技術の進歩指標／技術普及率／技術革新の影響度・持続性／産業別技術適用／政策・国際競争力 | WB v1.2 nb:584-589 | 一部 | era axis | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/ss-legacy | 環境と生態系 › 自然環境: 地形の種類・分布・標高／気候パターン・気温・降水量／自然資源（鉱物、森林、淡水）／自然保全施策／災害頻度とリスク | WB v1.2 nb:590-596 | 一部 | place section 地形・気候・資源・危険 (world_sections.yaml:4-7) | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/ss-legacy | 環境と生態系 › 動植物: 生物種・固有種・絶滅危惧種／捕食・共生関係／季節変動／環境ストレス要因／生物多様性と保護 | WB v1.2 nb:597-602 | 一部 | events 生態系 | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/ss-legacy | インフラと交通 › 交通手段: 種類・頻度・速度・信頼性／交通網のカバレッジ／燃料効率・環境負荷／事故率・安全基準／運行コストと利用者数 | WB v1.2 nb:603-609 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/ss-legacy | インフラと交通 › インフラ整備: 施設の種類・規模・耐用年数／維持管理コスト・故障率／普及率・地域格差／建設技術・材料品質／投資額・補助金 | WB v1.2 nb:610-615 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/ss-legacy | 生活様式と職業 › 住居・衣食: 住居タイプ／建築様式・材料・設備／衣服のデザイン・素材・伝統衣装／食文化（主食、副菜、調理法、地域食材）／居住環境指標 | WB v1.2 nb:616-622 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/ss-legacy | 生活様式と職業 › 職業・労働: 職業の種類と分布・経済貢献／労働時間・賃金・労働条件／職業訓練・スキル習得／需給バランス・失業率／労働組合・労働法規 | WB v1.2 nb:623-628 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/ss-legacy | コミュニケーションと情報伝達 › メディア: 種類・普及率・リーチ／発信速度・正確性・検証／報道の多様性・客観性／市民のアクセス／法的・自己規制 | WB v1.2 nb:629-635 | 一部 | media section (records, not mass media) | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/ss-legacy | コミュニケーションと情報伝達 › 通信手段: 種類・普及率・伝達速度／コスト・インフラ・信頼性／セキュリティ・データ保護／カバレッジ・障害率／アップグレード周期・利用者数 | WB v1.2 nb:636-641 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/ss-legacy | 芸術と娯楽 › 音楽・演劇: 表現の種類・スタイル・ジャンル／文化イベントの頻度・規模／芸術家の育成・資金・評価／公共施設（劇場、ホール）／社会的評価・批評・文化政策 | WB v1.2 nb:642-648 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/ss-legacy | 芸術と娯楽 › スポーツ・ゲーム: 種類・ルール・競技システム／参加者数・観戦者数・経済効果／開催頻度・規模・運営／競技施設・安全基準／ルール改定・公正性 | WB v1.2 nb:649-654 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |

### 10.10 WB `living_environment` — 7つの側面（現行コード）→ ハーネス `customs`・`people`

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| 世界/living | 家族形態 `family_structure` | WB:world_building.yaml:291,302 | 未取り込み | Not a viewpoint in customs/people | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/living | 地域コミュニティ `community` | :292,303 | 一部 | customs 「公共への貢献」 | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/living | ライフステージ `life_stages` | :293,304 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/living | 社会的モラル `social_morality` | :294,305 | 一部 | customs 「重要視される文化や思想」; people 「行動指針」 | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/living | ジェンダー役割 `gender_roles` | :295,306 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/living | 健康・衛生 `health_hygiene` | :296,307 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/living | 日常行動パターン `daily_patterns` | :297,308 | 一部 | people 「社会における役割と日常の行動」 | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |

### 10.11 WB `living_environment` の詳細項目 `[legacy]`（v1.2 ノートブック）

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| 世界/living-legacy | 社会的集団・コミュニティ › 家族形態: 世帯構成の類型／血縁・婚姻関係と扶養責任の強度／家族内意思決定・家計管理／世代間交流・同居／近居率・介護分担／離婚・再婚・養子縁組の制度と受容 | WB v1.2 nb:694-700 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/living-legacy | › 地域コミュニティ: 自治組織形態／参加率と自治活動（清掃・防災・祭礼）／互助ネットワークと資源共有／リーダー選出・合意形成／地域アイデンティティと外部流入者への開放度 | WB v1.2 nb:701-706 | 一部 | customs (軋轢/公共貢献) | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/living-legacy | › 職業・所属集団: 雇用形態／組織文化・上下関係・福利厚生／労働組合・業界団体の加入率と交渉力／異業種交流／地位付与・表彰制度 | WB v1.2 nb:707-712 | 一部 | organizations | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/living-legacy | 趣味・関心: コミュニティの規模と活動頻度／行動規範・モデレーション・ハラスメント対策／情報共有様式／維持・運営の資金源 | WB v1.2 nb:713-717 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/living-legacy | ライフステージとライフイベント (38 items): 出産施設・助産制度・出生登録／命名儀式・祝賀慣行／出生率／産後サポート／保育・幼児教育と就園率／遊び文化／友人関係形成／成長儀礼／成人年齢・通過儀礼／進学・就職・技能習得の分岐点／恋愛・デート文化とジェンダー規範／自立支援／危険行動・リスク教育／キャリア形成・昇進儀礼・表彰／住宅取得制度／家族内役割再編／健康管理／地域での社会的貢献活動／婚姻制度と手続／婚礼儀式の形式・費用・地域差／親族間の贈与・儀礼／離婚・再婚と子の監護権／妊産婦医療／乳幼児養育文化／親族・コミュニティの子育て支援／教育投資／退職制度・年金・再就職／介護形態／健康寿命／高齢者コミュニティ／家族間の介護分担／看取りと死後事務／葬儀形式／埋葬形態／香典・弔電・供花／相続／家族・親族間のプライバシー／家族・親族間の金銭管理 | WB v1.2 nb:718-756 | 未取り込み | No life-cycle/rites viewpoint at all | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/living-legacy | 社会的モラル・マナー: 公共空間のマナー／公共での身体距離・プライバシー／敬語・挨拶・謝罪文化／贈答文化／会食・接待の作法／意見表明と沈黙・察しの文化 | WB v1.2 nb:757-763 | 一部 | customs 軋轢 only | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/living-legacy | 社会的役割と地位付与: 年功序列・長老制・高齢者敬重度／市民権・兵役・奉仕義務／引退儀礼・隠居文化／権威・資源の世代継承 | WB v1.2 nb:764-768 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/living-legacy | ジェンダー役割: 家事・育児・介護の分担／ジェンダー平等・法的保護／多様なジェンダーコミュニティの権利・認知／服飾・化粧・身体装飾の性差規範／差別事例・救済制度 | WB v1.2 nb:769-774 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/living-legacy | 職業・専門家ステータス: 資格・免許・学位の取得と難易度／専門職団体・ギルド・学会の権威／社会的信用・地位／功労章・表彰・叙勲／懲戒制度・職業倫理 | WB v1.2 nb:775-780 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/living-legacy | 宗教・文化的役割: 聖職者・巫女・行者の地位と任命儀式／文化財守人・家元・世襲芸能／年中祭礼の役割者／宗教階層の権限差／世襲・選挙・試験による地位付与 | WB v1.2 nb:781-786 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/living-legacy | 住環境・生活インフラ: 持家／賃貸比率・空き家率／上下水・エネルギー供給／外食・デリバリー文化 | WB v1.2 nb:787-790 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/living-legacy | 健康・衛生・安全: 医療アクセス／公衆衛生／生活安全 | WB v1.2 nb:791-794 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/living-legacy | 日常行動パターン: 平均労働時間・通勤時間／睡眠・家事・余暇時間／旅行・スポーツ参加／文化施設の利用／ボランティア・地域活動／消費・購買行動／決済方法／メディア接触頻度／遠隔地との交流 | WB v1.2 nb:795-804 | 一部 | people 「日常の行動」 | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |

### 10.12 WB `social_groups` → ハーネス `organizations`

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| 世界/groups | Count: 10個の社会的集団 | WB:plot_generation.yaml:12; v1.2 nb:~846 | 未取り込み | single 400字 body | 取り込む（P1-06：規模に応じた件数を1件1タスクで生成） |
| 世界/groups | `name` 集団名 | WB:plot_generation.yaml:23,34 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/groups | `members` 構成員（v1.2: 構成員の種類と規模） | :24,35; v1.2 nb:849 | 取り込み済み | organizations 「構成要素」 (world_sections.yaml:38) | 取り込み済み（tables/world_sections.yaml:38） |
| 世界/groups | `purpose` 目的 | :25,36 | 取り込み済み | 「組織が指向する理想像」 (:37) | 取り込み済み（tables/world_sections.yaml:37） |
| 世界/groups | `challenges` 課題 | :26,37 | 取り込み済み | 「課題と内部の緊張」 (:39) | 取り込み済み（tables/world_sections.yaml:39） |
| 世界/groups | `activities` 活動内容 | :27,38 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/groups | `characteristics` 特徴 | :28,39 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |

### 10.13 WB `people_list`（100人のペルソナ）→ ハーネス `people`

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| 世界/people | Count: 100人のペルソナ (split 20/request) | WB:plot_generation.yaml:52; pipeline.py:1107-1170 | 未取り込み | people section is a single ≤400字 descriptive body; no individual personas. Also removes a large pool of potential minor characters | 取り込む（P1-06：規模に応じた件数を1件1タスクで生成） |
| 世界/people | 1 `name` 氏名 | WB:plot_generation.yaml:64,83 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/people | 2 `age` 年齢 | :65,84 | 未取り込み（年齢テーブルの除外を参照） | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/people | 3 `gender` 性別 | :66,85 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/people | 4 `residence` 居住地 | :67,86 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/people | 5 `family` 家族構成 | :68,87 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/people | 6 `affiliation` 所属 | :69,88 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/people | 7 `role` 役割 | :70,89 | 取り込み済み | people 「社会における役割」 | 取り込み済み（tables/world_sections.yaml people） |
| 世界/people | 8 `income` 収入レベル | :71,90 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/people | 9 `lifestyle` ライフスタイル | :72,91 | 一部 | 「日常の行動」 | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/people | 10 `hobbies` 趣味 | :73,92 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/people | 11 `values` 価値観 | :74,93 | 取り込み済み | 「人々が重要視する行動指針」 | 取り込み済み（tables/world_sections.yaml people） |
| 世界/people | 12 `goals` 目標 | :75,94 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/people | 13 `concerns` 悩み | :76,95 | 取り込み済み | 「個人的な問題」 | 取り込み済み（tables/world_sections.yaml people） |
| 世界/people | 14 `relationships` 人間関係 | :77,96 | 取り込み済み | 「他者との関係」 | 取り込み済み（tables/world_sections.yaml people） |
| 世界/people | v1.2 extra: `休日の過ごし方` | WB v1.2 nb:910 `[legacy]` | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/people | v1.2 extra: `情報収集方法` | WB v1.2 nb:911 `[legacy]` | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |

### 10.14 WB `future_scenarios` → ハーネス `future`

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| 世界/future | Horizon: 50〜100年後 | WB:plot_generation.yaml:109; v1.2 nb:~966 | 未取り込み | future section has no time horizon | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/future | type `optimistic` 楽観的シナリオ | :122 | 取り込み済み | world_sections.yaml:84 「望ましい未来」 | 取り込み済み（tables/world_sections.yaml:84） |
| 世界/future | type `pessimistic` 悲観的シナリオ | :123 | 取り込み済み | :85 「失敗や停滞から生じる未来」 | 取り込み済み（tables/world_sections.yaml:85） |
| 世界/future | type `moderate` 中間シナリオ | :124; v1.2 nb:972 | 未取り込み | replaced by 「未来を変えるために必要な行動と代償」 (:86) | 取り込む（P1-06：規模に応じた件数を1件1タスクで生成） |
| 世界/future | `scenario_type` | :127,141 | 一部 | implicit | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/future | `timeline` 時間軸 | :128,142 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/future | `key_changes` 主要な変化 | :129,143 | 一部 | body | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/future | `social_impact` 社会への影響 | :130,144 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/future | `technological_developments` 技術的発展 | :131,145 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/future | `environmental_state` 環境状態 | :132,146 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/future | `quality_of_life` 生活の質 | :133,147 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/future | `conflicts` 紛争・対立 | :134,148 | 未取り込み | — | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |
| 世界/future | `resolutions` 解決策 | :135,149 | 一部 | 「必要な行動と代償」 | 取り込む（P1-01：tables/world_sections.yaml の観点、P1-06 で生成） |

## 11. 出力物

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| 出力物 | Work title: 3 candidates (JSON title+reason), 物語のテーマを表現, 日本語5〜15文字 | HJ:plot_generator.py:430-511; design-spec:745-751; HJ legacy nb cell 14 | 一部／除外 | Only slides title, from template: format-pipeline.md:85 「題名は、正本の主人公の名前とプロット型から、テンプレートで作る（LLM に作らせない）」. Novel/canonical story has no title field (story-pipeline.md:173) | 取り込む（P3-01：様式化の題のタスク） |
| 出力物 | Catchphrase 決め台詞 | HR:ollama_hero_gen.py:841-857 | 未取り込み | see §5 | 取り込む（P1-07：S5 の項目） |
| 出力物 | HJ visual prompts ×4 roles (EN, ≤150語/400字): 年齢、体格、顔立ち、髪、衣装、色、持ち物、雰囲気、照明 | HJ:colab_features.py:511-597 (fields 550, 579); design-spec:755-771 | 一部（計画あり） | format-pipeline.md:69 `visual-prompts` 「P5-01 で定義」; LINEAGE.md:53. Field list not defined | 取り込む（P5-01） |
| 出力物 | HR image prompt composition (legacy): Subject (full-length JRPG/fighting-game illustration) + Angle (waist-height, entire body) + Pose (upright, conveys role/personality/ability/culture) + Background (white) + Concept + Artstyle (hand-drawn lines, manga/anime, haute couture, edgy design) | HR legacy 20240916…py:69-74; HR:image_model_profiles.py:395-406 | 一部（計画あり） | same P5-01 | 取り込む（P5-01） |
| 出力物 | HR image prompt styles `natural` / `prose` / tag styles (`pony`, `noobai`, animagine/illustrious) with tags solo, full body, standing, looking at viewer, feet visible, centered composition, white background | HR:image_model_profiles.py:358-439 | 未取り込み | model-specific; not in P5-01 scope text | 取り込む（P5-01） |
| 出力物 | HR turnaround sheet prompt (front/side/back, T-pose, identical outfit, white bg) | HR:ollama_hero_gen.py:769-779 | 未取り込み | — | 取り込む（P5-01） |
| 出力物 | HR output CSV columns (name, profile, catchphrase, image_prompt, concept, age, gender, species, ability, wants, role, image_path, image_seed, turnaround_path, height_cm, character_dir) | HR:ollama_hero_gen.py:405-422 | 一部 | story.json cast (no image/catchphrase/height) | 取り込み済み（story.json） |
| 出力物 | HJ world.md / plot_skeleton.md / visual_prompts.md / metadata.json (title, fingerprint, plot_type, chapter_count, character_names, selected_elements) | HJ:story_generator.py:685-729 | 一部 | story.json meta (run ID, seed, scale, r, plot type, template, input type) story-pipeline.md:173; no title/skeleton/visual | 取り込み済み（data-layout §2 の run の構成） |
| 出力物 | HJ narrative_analysis.md (desire/suppression/conflict reports + elements) | HJ:story_generator.py:790-836 | 除外／変更 | S1 materials only (LINEAGE.md:46) | 取り込む（P4-05：傾向分析） |
| 出力物 | Novel chapters (HJ 800〜1200字/章; legacy modes 10章×4000字, 3章×2000字; WB 10章, min 2000字) | HJ:plot_generator.py:419; design-spec:738-743; WB:config/ollama_config.yaml:146 | 取り込み済み（パラメータを変更） | format `novel` chars_per_event (format-pipeline.md:23, 66) | 取り込み済み（format-pipeline.md:23,66） |
| 出力物 | Reference book: `characters.md` (外見的特徴を詳細に; 性格・背景・動機; キャラクター間の関係性) | WB:story_generation.yaml:42-54; v1.2 nb:1201 | 一部（計画あり） | `reference-book` P5-02 「世界セクション・人物」 (format-pipeline.md:70) | 取り込む（P5-02） |
| 出力物 | Reference `plot.md` (テーマ分析; 各章の成長アーク; モチーフと象徴; 伏線と回収の構造) | WB:story_generation.yaml:62-74; v1.2 nb:1206 | 未取り込み | P5-02 scope is world + cast only; report.md R2 motifs is batch-level | 取り込む（P5-02） |
| 出力物 | Reference `user_context.md` (根源的な感情; なぜこの物語が必要だったか; テーマの普遍性; 読者への影響) | WB:story_generation.yaml:82-94; v1.2 nb:1211 | 未取り込み | Also tension with non-diagnosis principle (report.md:9) — not addressed | 取り込む（P5-02） |
| 出力物 | Reference per world element (背景や意味の解釈; 他要素との関連性; 物語への影響) ×10 | WB:story_generation.yaml:102-116; pipeline.py:1620-1623 | 一部（計画あり） | P5-02 | 取り込む（P5-02） |
| 出力物 | Reference `desire_list.md` (表層的/中間的/深層的に分類; 物語への反映; 願望間の関連・対立; 普遍的欲求) | WB:story_generation.yaml:124-136 | 未取り込み | — | 取り込む（P5-02） |
| 出力物 | Reference `ability_list.md` (身体的/精神的/社会的/特殊に分類; 活用; 獲得方法や制限; 世界観との整合) | WB:story_generation.yaml:144-156 | 未取り込み | — | 取り込む（P5-02） |
| 出力物 | Reference `role_list.md` (社会的/物語的/象徴的に分類; 物語構造への寄与; 相互作用・対立; 変化・成長) | WB:story_generation.yaml:164-176 | 未取り込み | — | 取り込む（P5-02） |
| 出力物 | Reference `plottype_list.md` (起源・文化的背景; 選択の適切性; 整合性; 他型との比較) | WB:story_generation.yaml:184-199 | 未取り込み | — | 取り込む（P5-02） |
| 出力物 | 17-document reference set overall | WB:DESIGN_SPEC.md:562-582 | 一部（計画あり） | P5-02 covers ~11 (world+characters) | 取り込む（P5-02） |
| 出力物 | Batch analysis per-run summary: protagonist_desire / adversary_type / central_loss / ending (獲得、喪失、変容、帰還など) / key_motifs ≤5 | HJ:batch_analyzer.py:438-456 | 取り込み済み（計画あり P4-05） | report.md:18 R2 (desire & adversary role from assignment.json; central_loss, ending, motifs by LLM) | 取り込み済み（report.md:18） |
| 出力物 | Batch cross-run patterns ≤7 {pattern, evidence_runs, question (問いの形, 日本語の項目名)} | HJ:batch_analyzer.py:468-506, 573-617 | 取り込み済み（計画あり） | report.md:19-21 R3/R4 (observation + question) | 取り込み済み（report.md:19-21） |
| 出力物 | Batch frequency tables plot_type/want/ability/role/narrative | HJ:batch_analyzer.py:22, 213-239 | 取り込み済み（計画あり） | report.md:17 R1 | 取り込み済み（report.md:17） |

## 12. 文章と内容を形づくる生成規則

| 分類 | 要素 | 出典（リポジトリ:ファイル:行） | 状態 | ハーネスでの場所・差分 | 決定 |
|---|---|---|---|---|---|
| 文章の規則 | HJ chapter: 具体的な出来事、登場人物の感情や行動を詳細に描写 | HJ:plot_generator.py:420; design-spec:723 | 一部 | novel rule 「地の文と会話で書く。説明的な要約で済ませない。」 (format-pipeline.md:29); emotions not required | 取り込む（P3-02：formats/novel の規則） |
| 文章の規則 | HJ chapter: 読者が情景を思い浮かべられる文学的な表現 / ディティールの深い文学的表現 | HJ:plot_generator.py:421; design-spec:724-725 | 未取り込み | no literary-quality rule in profile | 取り込む（P3-02：formats/novel の規則） |
| 文章の規則 | HJ: 物語の全体構造を損なわないこと | HJ design-spec:722 | 除外 | VISION P1 (no global view in cards) | 取り込む（P3-02：formats/novel の規則） |
| 文章の規則 | HJ/WB: 最後は完結した文で終える／途中で止めない | HJ:plot_generator.py:423; WB:story_generation.yaml:27 | 取り込み済み | checks `ends_complete` (format-pipeline.md:34); continuation task-model.md §8 | 取り込み済み（task-model §8） |
| 文章の規則 | WB: 重厚長大な小説／出力可能な最大トークン数 | WB:story_generation.yaml:23-24 | 除外／変更 | ADR-0005 scale-not-length; chars_per_event bounds | 取り込み済み（task-model §8） |
| 文章の規則 | WB: 読者が実際に体験していると感じられる深みとディテール | WB:story_generation.yaml:25 | 未取り込み | — | 取り込む（P3-02：formats/novel の規則） |
| 文章の規則 | WB: 余計な説明文を排除し本文のみ | WB:story_generation.yaml:26 | 取り込み済み | output: text (format-pipeline.md:52) | 取り込む（P3-02：formats/novel の規則） |
| 文章の規則 | WB: 文学的な表現を用い、感情と情景を豊かに描写 | WB:story_generation.yaml:28 | 未取り込み | — | 取り込む（P3-02：formats/novel の規則） |
| 文章の規則 | Writer persona 「現代を代表する小説家」「優れた小説家」 | WB:story_generation.yaml:7; HJ:plot_generator.py:364 | 除外（暗黙） | cards carry no persona | 外す（カードに人格を持たせず作業内容だけを書く：VISION P1） |
| 文章の規則 | Chapter input includes all 4 character profiles + previous chapter summaries | HJ:plot_generator.py:388-392; design-spec:728-733 | 一部 | F2 gets only characters appearing in the unit + last 600字 (format-pipeline.md:51) | 取り込み済み（format-pipeline F2） |
| 文章の規則 | Chapter input includes world references (plot_reference_n) | WB:story_generation.yaml:19-20 | 一部 | F2 gets events only; world excerpts are in S7 not F2 | 取り込み済み（format-pipeline F2） |
| 続き書き | Continuation prompt (既出を繰り返さず末尾直後から; 章見出し付けない; 出来事と感情の決着まで; 完結文) max 2 | HJ:plot_generator.py:396-407; colab_pipeline.py:57 | 取り込み済み | task-model.md:265-270 (max 2) | 取り込み済み（task-model §8） |
| 重複排除 | story_fingerprint (NFKC, whitespace removed, casefold, sha256) | HJ:story_generator.py:61-79 | 取り込み済み | story-pipeline.md:178 | 取り込み済み（story-pipeline.md:178） |
| 重複排除 | Batch refill until N unique, max_attempts = max(3N, N+10) | HJ:colab_pipeline.py:213-216 | 取り込み済み | story-pipeline.md:181 | 取り込み済み（story-pipeline.md:181） |
| 重複排除 | Near-duplicate (char 3-gram Jaccard) | (harness-only) | ハーネス独自 | story-pipeline.md:179 | ハーネス独自（先行リポジトリの要素ではない） |

---

## 13. リポジトリ間の重複（1か所に統合する）

| 概念 | HR | HJ | WB | ハーネスでの単一の置き場所 |
|---|---|---|---|---|
| Want / 願望 pool | seed_wants + DEFAULT_SEEDS; counterpart new_wants | ElementPools.wants (100) | desire_list (100) | S2 axis `want` + want.yaml |
| Ability / 能力 pool | seed_ability; new_ability | abilities (100) | ability_list (100) | S2 axis `ability` + ability.yaml |
| Role / 役割・課題 pool | seed_role; new_role | roles (課題, 100) | role_list (100) | S2 axis `duty` + duty.yaml |
| Random per-character element pick | get_random_attribute | _select_elements / legacy cell 10 | characters prompt 「ランダムに割り当て」 | S3 step 6 |
| 4 roles + definition text | — | character_generator / design-spec §9.2 | world_building.yaml:29-33; v1.2 nb (identical wording to HJ) | S5 (definitions still missing) |
| Character name + profile | name/profile | 名前 + プロフィール (400/200字) | name/short_introduction/description | S5 name/profile/motive |
| Plot-type field schema (core, required events, character reqs, time design, conflict, climax, pacing) | — | PlotTypeSpec / plot_list 7 項目 | plottype_list 8-9 fields (+typical_story_setting) | plot_types.yaml (only core + required_events) |
| Plot type selection by LLM | — | top-3 classifier | select 1 | S3 code (both EXCLUDED) |
| Chapter plot fields: event / emotion / intention-action / situation change | — | legacy cell 10/18, design-spec §10.3 | plot.yaml situation/events/emotions/actions/change/foreshadowing | S7 event (emotion + foreshadowing MISSING) |
| Social structure / organizations / customs / people | — | 4-item world (colab_features:348-351) | social_structure (12), social_groups, living_environment, people_list | world_sections.yaml (HJ granularity only) |
| Visual prompts | concept→image prompt, turnaround | VisualPrompts ×4 | (reference characters 外見) | P5-01 visual-prompts |
| Chapter writing + continuation | — | generate_chapter + continuation | story_chapter + max_continuations 3 | F2 novel + task-model §8 |
| Abstraction / non-quotation rule | legacy catchphrase 「抽象的に解釈」 | narrative 要素 「抽象化」 | 「直接引用せず抽象的に解釈・再構築」 | S1 |
| Seed/run reproducibility, checkpoint, dedup | — | fingerprint, draft checkpoints | run_manifest, derived seeds | orchestrator |
| Appearance description | species/age/gender + image | visual prompt fields | description 外見 + reference characters 外見 | not in S5 (narrowed) |

---

## 付録A. HR の厳選シードテーブル（`config/seeds/*.csv`）

出典：`/Users/masa/マイドライブ/Dev/100-times-ai-heroes/config/seeds/`（先頭100行は `data/seed_*.csv` と同じ。`data/seed_*.csv` には、実行時に追記された行も含まれる）。

### seed_ability.csv（100件。決定：取り込む（P1-01：tables/elements/<axis>.yaml、日本語訳と原文））

- L2: Can reverse causality through rhythmic movement
- L3: Can absorb emotions as colors and animate tattoos
- L4: Can hear the last words spoken in any room
- L5: Can fold space like origami, but only along straight lines
- L6: Can borrow another person's skill for exactly one minute
- L7: Can turn written words into living insects
- L8: Can see the price of every decision as a floating number
- L9: Can pause the world while holding their breath
- L10: Can make anyone forget one sentence they just heard
- L11: Can grow plants from spoken promises
- L12: Can translate the language of machines and appliances
- L13: Can store sunlight in their hair and release it at night
- L14: Can swap the weight of two objects they touch
- L15: Can walk into photographs and change small details
- L16: Can taste lies as bitterness
- L17: Can summon rain by crying, and only by crying
- L18: Can rewind a single object by up to one hour
- L19: Can make shadows solid enough to stand on
- L20: Can see the thread connecting people who will meet again
- L21: Can speak with anyone through their dreams, but forgets it upon waking
- L22: Can turn sound into light and light into sound
- L23: Can make any door open onto a place they have been before
- L24: Can freeze a moment into a glass marble and replay it later
- L25: Can read the history of an object by holding it
- L26: Can make gravity point in any direction within arm's reach
- L27: Can copy a person's face but not their voice
- L28: Can command electricity as long as they keep singing
- L29: Can predict the next ten seconds, but only for others
- L30: Can heal wounds by taking them onto their own body
- L31: Can shrink to the size of a coin
- L32: Can talk to the dead through static on a radio
- L33: Can make objects lighter by telling them jokes
- L34: Can see through walls as if they were water
- L35: Can make time move faster for plants only
- L36: Can split into three smaller copies of themselves
- L37: Can turn anger into heat and sadness into cold
- L38: Can erase their presence from cameras and screens
- L39: Can sense lies told within one kilometer
- L40: Can draw anything and bring it to life until sunrise
- L41: Can breathe any gas and exhale it as flowers
- L42: Can make people tell the truth by offering them tea
- L43: Can manipulate magnetism through their fingertips
- L44: Can open portals, but only inside mirrors
- L45: Can age objects forward by centuries in seconds
- L46: Can hear the thoughts of animals as poetry
- L47: Can make a promise physically unbreakable
- L48: Can store memories inside seashells
- L49: Can create illusions only children can see
- L50: Can teleport by stepping into puddles
- L51: Can turn their body into sand and reform it
- L52: Can control the flow of rumors in a city
- L53: Can make anyone fall asleep by humming
- L54: Can walk on light beams
- L55: Can see ghosts only when wearing their grandmother's glasses
- L56: Can move one kilogram with their mind for every hour they did not sleep
- L57: Can bend water into shapes that remember
- L58: Can make any food taste like a forgotten memory
- L59: Can steal the color out of objects and use it as energy
- L60: Can undo the last word they spoke
- L61: Can make music that repairs broken machines
- L62: Can communicate with stars through Morse code blinks
- L63: Can make clocks stop in their vicinity
- L64: Can call lightning, but only while dancing
- L65: Can hide inside any book and read from within
- L66: Can make anyone feel the emotions of a place's past
- L67: Can change the season in a small garden
- L68: Can grow crystal armor that shatters when they lie
- L69: Can see the cracks where the world might break
- L70: Can pull objects out of television screens
- L71: Can make echoes answer questions
- L72: Can give inanimate objects one day of life
- L73: Can see a person's greatest regret as a shadow behind them
- L74: Can make paper airplanes fly across continents
- L75: Can turn their voice into a physical wall
- L76: Can make wounds bloom into flowers instead of scars
- L77: Can fuse two memories into one
- L78: Can locate anything that was lost, except their own things
- L79: Can speak every language, but only in the future tense
- L80: Can slow their heartbeat to become invisible to predators
- L81: Can change their size according to their confidence
- L82: Can turn lies into bubbles that float away
- L83: Can call birds to carry messages and small objects
- L84: Can see the lifespan of machines
- L85: Can make fire that does not burn living things
- L86: Can create a room outside of time once per day
- L87: Can exchange senses with another person
- L88: Can make any surface into a doorway to their home
- L89: Can calm storms by writing haiku
- L90: Can see a person's future self for one second
- L91: Can make coins multiply, but each copy steals a memory
- L92: Can harden air into glass
- L93: Can make wounds tell the story of how they happened
- L94: Can draw power from the moon's reflection in water
- L95: Can make anyone's footsteps silent
- L96: Can turn their tears into pearls that grant small wishes
- L97: Can reverse gravity for everyone who laughs
- L98: Can see the invisible threads of debt between people
- L99: Can transform into the last animal they touched
- L100: Can knit protective charms from moonlight
- L101: Can turn memories into edible sweets

### seed_wants.csv（100件。決定：取り込む（P1-01：tables/elements/<axis>.yaml、日本語訳と原文））

- L2: I want to find the person who stole my shadow on my tenth birthday.
- L3: I want to open a ramen shop that the dead and the living can both visit.
- L4: I want to learn why my mother erased me from her memories.
- L5: I want to design a memory that everyone in the city can share without fighting.
- L6: I want to repay a kindness I received from a stranger forty years ago.
- L7: I want to become ordinary for just one full day.
- L8: I want to prove that the extinct sky whales are still alive.
- L9: I want to finish the novel my late partner started.
- L10: I want to be forgiven by the town I accidentally flooded.
- L11: I want to find a place where my power does not hurt anyone.
- L12: I want to hear my own voice for the first time.
- L13: I want to win back my name from the demon I sold it to.
- L14: I want to build a school for monsters who want to become human.
- L15: I want to see the ocean before my body turns completely to stone.
- L16: I want to rescue my little brother from the dream he never woke up from.
- L17: I want to replace the god of my village, who has grown tired.
- L18: I want to deliver a letter that has been undeliverable for three hundred years.
- L19: I want to be remembered by at least one person after I disappear.
- L20: I want to find out which of my clones is the original.
- L21: I want to make my estranged father laugh once before he dies.
- L22: I want to buy back the island my ancestors lost in a card game.
- L23: I want to stop the festival that sacrifices one wish every year.
- L24: I want to become the first robot to be granted a funeral.
- L25: I want to cure the sadness that turned my hometown gray.
- L26: I want to find the song that ends all wars, which my grandmother hummed.
- L27: I want to escape the story I was written into.
- L28: I want to plant a forest on the moon for my late dog.
- L29: I want to know what I was before I was reincarnated.
- L30: I want to return the stars I accidentally knocked out of the sky.
- L31: I want to make the city's streetlights sing again.
- L32: I want to earn enough luck to save my sister's failing restaurant.
- L33: I want to find the one mirror that shows my real face.
- L34: I want to teach my rival how to lose without breaking.
- L35: I want to live long enough to see my apprentice surpass me.
- L36: I want to break the promise that binds me to the mountain.
- L37: I want to prove that monsters can be heroes on live television.
- L38: I want to win the cooking contest that my father rigged against me.
- L39: I want to recover the color of the sky that my people forgot.
- L40: I want to be loved without using my powers.
- L41: I want to finish repairing the clock that controls my village's time.
- L42: I want to reunite the band that broke up when the world ended.
- L43: I want to discover who has been leaving flowers at my future grave.
- L44: I want to find a cure for the curse that makes me forget every Sunday.
- L45: I want to become a real hero instead of a stunt double.
- L46: I want to bring my hometown back from the bottom of the lake.
- L47: I want to hear my late teacher's voice one more time.
- L48: I want to earn the right to leave the library I was born in.
- L49: I want to prove I am not the villain the prophecy described.
- L50: I want to marry the ghost who haunts my apartment.
- L51: I want to rebuild the bridge between the two feuding villages.
- L52: I want to retire and grow tomatoes in peace.
- L53: I want to find the dragon that promised to eat me when I turned thirty.
- L54: I want to become the mayor of the underground city.
- L55: I want to understand why the stars keep sending me messages.
- L56: I want to repair my friendship with the AI I betrayed.
- L57: I want to make the ugliest monster in the world feel beautiful.
- L58: I want to rescue the lighthouse keeper who vanished during the storm.
- L59: I want to trade my immortality for one ordinary lifetime.
- L60: I want to find the recipe that tastes like my first memory.
- L61: I want to collect every lost umbrella in the world.
- L62: I want to rewrite the ending of my favorite tragedy.
- L63: I want to become the kind of adult I needed as a child.
- L64: I want to make peace between the sea people and the fishermen.
- L65: I want to find out who replaced my family with perfect copies.
- L66: I want to earn a medal from the country that exiled me.
- L67: I want to stop the rumor that I am already dead.
- L68: I want to learn to sleep without dreaming of the war.
- L69: I want to get my stolen heart back from the museum.
- L70: I want to become a comedian in a world that forgot how to laugh.
- L71: I want to give the forest spirits a vote in the city council.
- L72: I want to find a new home for the last dragon egg.
- L73: I want to break my family's thousand-year-old habit of betrayal.
- L74: I want to beat the champion who stole my title with cheating.
- L75: I want to rebuild the robot that raised me.
- L76: I want to taste snow before I go back to the stars.
- L77: I want to open a detective agency for crimes nobody reports.
- L78: I want to find the other half of the map tattooed on my back.
- L79: I want to protect my town's last cinema from demolition.
- L80: I want to find the person who can hear my thoughts.
- L81: I want to make my mother proud without becoming like her.
- L82: I want to escape from the time loop that restarts every Tuesday.
- L83: I want to erase the one mistake the whole world remembers me for.
- L84: I want to find the melody that wakes the sleeping giant.
- L85: I want to buy freedom for the spirits trapped in the vending machines.
- L86: I want to learn how to cry.
- L87: I want to deliver my grandmother's last recipe to her first love.
- L88: I want to become a better villain than my famous father.
- L89: I want to stop aging backwards before I disappear.
- L90: I want to prove that my invention can end hunger.
- L91: I want to find where the missing hours of my life went.
- L92: I want to make the moon notice me.
- L93: I want to find a friend who is not afraid of my power.
- L94: I want to bring back the festival my town forgot.
- L95: I want to win the war without killing anyone.
- L96: I want to become human before the next cherry blossom season.
- L97: I want to recover the memories I sold to pay for my sister's surgery.
- L98: I want to see my creator one last time and ask why.
- L99: I want to go home, although I no longer remember where it is.
- L100: I want to finish the pilgrimage my ancestors abandoned.
- L101: I want to teach the world to see ghosts without fear.

### seed_role.csv（99件。決定：取り込む（P1-01：tables/elements/<axis>.yaml、日本語訳と原文））

- L2: Nostalgic Experience Designer. Designs shared memories people can revisit like theme parks
- L3: Biotechnology Tattoo Artist. Inks living tattoos that react to the wearer's feelings
- L4: Digital Nutrition Consultant. Prescribes balanced diets of information for overloaded minds
- L5: Dream Cartographer. Maps the recurring dreams of an entire city block
- L6: Ghost Real Estate Agent. Finds suitable haunts for homeless spirits
- L7: Memory Recycler. Collects unwanted memories and refurbishes them for resale
- L8: Silence Sommelier. Pairs rare kinds of silence with clients' moods
- L9: Algorithm Therapist. Counsels recommendation engines that have become obsessive
- L10: Weather Negotiator. Mediates disputes between farmers and local rain spirits
- L11: Lost-and-Found Keeper of the Afterlife. Returns items the dead left unfinished
- L12: Urban Beekeeper of Mechanical Bees. Maintains the city's robotic pollinators
- L13: Apology Ghostwriter. Writes sincere apologies for people who cannot find the words
- L14: Time-Zone Diplomat. Resolves conflicts between neighborhoods that live in different hours
- L15: Night Market Illusionist. Sells illusions by weight at a midnight bazaar
- L16: Retired Hero Caretaker. Runs a nursing home for aging superheroes
- L17: Planet-Scale Gardener. Terraforms abandoned moons one seed at a time
- L18: Language Midwife. Helps newly invented languages speak their first words
- L19: Vending Machine Oracle Technician. Repairs machines that dispense prophecies
- L20: Emotional Debt Collector. Recovers kindness that was borrowed and never returned
- L21: Archive Diver. Swims through flooded libraries to rescue drowned books
- L22: Sound Fossil Hunter. Excavates voices trapped in ancient stone
- L23: Street Food Alchemist. Cooks dishes that temporarily change the eater's personality
- L24: Cloud Shepherd. Herds wandering clouds back to drought-stricken villages
- L25: Avatar Tailor. Sews custom bodies for people living in virtual worlds
- L26: Rumor Exterminator. Hunts down lies before they grow into legends
- L27: Constellation Restorer. Repaints faded star patterns in the night sky
- L28: Mirror Customs Officer. Inspects everything that crosses between reflections
- L29: Shrine Influencer. Promotes forgotten gods on social media
- L30: Grief Florist. Grows flowers that bloom only when someone is truly mourned
- L31: Underground Idol for Deep-Sea Creatures. Performs concerts in the abyss
- L32: Mech Pilot Driving Instructor. Teaches teenagers to pilot giant robots safely
- L33: Border Guard of the Dream Country. Checks passports of sleepers entering dreams
- L34: Library Cat Mayor. Governs a city built inside a public library
- L35: Fortune Cookie Author. Writes prophecies that accidentally come true
- L36: Rebellion Accountant. Keeps the books balanced for a revolutionary army
- L37: Monster Hotel Concierge. Runs a luxury hotel for creatures of legend
- L38: Clockmaker of Personal Time. Builds clocks that measure one person's remaining luck
- L39: Rain Collector for Tears of the Sky. Bottles rain that fell during historic moments
- L40: Toy Soldier Commander. Leads an army of abandoned toys
- L41: Space Station Janitor. Cleans a station orbiting a dying star
- L42: Taste Translator. Converts flavors into music for people who cannot taste
- L43: Last Lighthouse Keeper. Guards the final lighthouse between the living and the dead
- L44: Unlicensed Wish Broker. Resells unused wishes on the black market
- L45: Stunt Double for Gods. Performs dangerous miracles in place of lazy deities
- L46: Smell Archivist. Preserves the scents of vanished places
- L47: Crossword Puzzle Detective. Solves crimes hidden in newspaper puzzles
- L48: Bicycle Courier Between Worlds. Delivers parcels across dimensional borders
- L49: Karaoke Exorcist. Banishes demons by singing them off stage
- L50: Ramen Stall Philosopher. Serves noodles and existential advice until dawn
- L51: Glacier Historian. Reads the history written in layers of ancient ice
- L52: Puppet Parliament Speaker. Chairs debates between marionettes with free will
- L53: Nightmare Recycling Engineer. Converts bad dreams into clean energy
- L54: Solar Sail Seamstress. Stitches sails for ships that travel on starlight
- L55: Graffiti Guardian. Paints protective sigils on city walls at night
- L56: Echo Mail Carrier. Delivers messages left in canyons decades ago
- L57: Chess Coach for Artificial Intelligences. Teaches machines to lose gracefully
- L58: Moon Pharmacist. Mixes medicines that only work under the full moon
- L59: Carnival Mechanic. Keeps a haunted amusement park's rides running
- L60: Wandering Bathhouse Owner. Runs a bathhouse built on the back of a giant turtle
- L61: Paper Crane Messenger. Folds cranes that fly letters to the dead
- L62: Deep Web Monk. Meditates in the unindexed corners of the internet
- L63: Heatwave Firefighter. Fights fires started by sunlight spirits
- L64: Customs Inspector of Memories. Checks what travelers remember at the border
- L65: Volcano Chef. Cooks banquets using lava as a stove
- L66: Shadow Puppet Historian. Retells lost history through shadow plays
- L67: Bridge Troll Toll Economist. Modernizes toll pricing for magical bridges
- L68: Coral Reef Architect. Designs cities for merfolk refugees
- L69: Insomnia Night Guide. Leads sleepless people on tours of the city at 3 a.m.
- L70: Snowflake Designer. Crafts unique snowflakes for each winter
- L71: Echo Location Surveyor. Maps caves by listening to their memories
- L72: Vintage Robot Restorer. Repairs robots from forgotten sci-fi eras
- L73: Sky Whale Veterinarian. Treats giant whales that swim through the clouds
- L74: Scarecrow Union Organizer. Fights for the rights of sentient scarecrows
- L75: Holographic Tour Guide. Guides tourists through ruins that no longer exist
- L76: Wish-Well Plumber. Unclogs wishing wells jammed with unfulfilled wishes
- L77: Candlelight Stenographer. Records confessions spoken only by candlelight
- L78: Parade Float Engineer. Builds floats for the festival of the dead
- L79: Fairy Tale Editor. Revises old fairy tales so they end differently
- L80: Subway Spirit Conductor. Drives the last train that ghosts ride home
- L81: Orbital Florist. Grows flowers in zero gravity for astronauts' funerals
- L82: Arcade Champion Turned Coach. Trains gamers whose scores keep the world stable
- L83: Tea Ceremony Hacker. Breaks into systems through ritualized etiquette
- L84: Storm Chaser Photographer. Photographs the faces hidden inside storms
- L85: Mask Carver. Carves masks that let wearers become someone else for a night
- L86: Seed Vault Guardian. Protects the last seeds of extinct plants
- L87: Bookbinder of Unwritten Books. Binds the books people meant to write
- L88: Kite Maker for Souls. Builds kites that carry spirits to the sky
- L89: Clock Tower Hermit. Lives inside a clock tower and keeps the city on time
- L90: Neighborhood Dragon Mediator. Settles noise complaints involving dragons
- L91: Street Magician Debt Collector. Pays off debts with tricks instead of money
- L92: Colorist of Black-and-White Films. Restores color to forgotten memories on film
- L93: Gravity Choreographer. Designs dances for places with shifting gravity
- L94: Pilgrimage App Developer. Builds navigation for journeys to sacred places
- L95: Traveling Cinema Projectionist. Shows films of possible futures in rural villages
- L96: Firework Composer. Writes symphonies performed in fireworks
- L97: Inkwell Monster Tamer. Keeps creatures that crawl out of old manuscripts
- L98: Frontier Radio DJ. Broadcasts to settlers on a distant planet
- L99: Tidal Clockkeeper. Adjusts the tides for coastal towns
- L100: Mushroom Forest Postmaster. Delivers mail through underground fungal networks

### seed_age.csv（16件。決定：取り込む（P1-01：tables/elements/<axis>.yaml、日本語訳と原文））

- L2: Prepubescent
- L3: Preteen
- L4: Early teens
- L5: Late teens
- L6: Early twenties
- L7: Late twenties
- L8: In their thirties
- L9: In their forties
- L10: In their fifties
- L11: In their sixties
- L12: In their seventies
- L13: Over ninety
- L14: Centuries old but looks ten
- L15: Newly created, three days old
- L16: Ages backwards, currently a young adult
- L17: Frozen at nineteen for two hundred years

### seed_gender.csv（10件。決定：取り込む（P1-01：tables/elements/<axis>.yaml、日本語訳と原文））

- L2: Male
- L3: Female
- L4: Non-binary
- L5: Genderfluid
- L6: Agender
- L7: Androgynous
- L8: Bigender
- L9: Genderless construct
- L10: Changes gender with the moon phase
- L11: Two-spirit

### seed_species.csv（50件。決定：取り込む（P1-01：tables/elements/<axis>.yaml、日本語訳と原文））

- L2: Human
- L3: Half-human half-aquatic
- L4: Demigod
- L5: Half-elf
- L6: Dwarf
- L7: Forest spirit
- L8: Fallen angel
- L9: Minor demon on probation
- L10: Dragon in human form
- L11: Kitsune
- L12: Tanuki
- L13: Lycanthrope
- L14: Vampire who cannot drink blood
- L15: Ghost bound to a vending machine
- L16: Zombie with perfect manners
- L17: Golem made of recycled phones
- L18: Android with a borrowed human memory
- L19: Cyborg octopus
- L20: Sentient houseplant
- L21: Living shadow
- L22: Mushroom colony in a human shape
- L23: Bird-headed human
- L24: Cat that became human by mistake
- L25: Snow woman (yuki-onna)
- L26: Kappa
- L27: Tengu
- L28: Oni
- L29: Moth-winged fae
- L30: Merfolk living in a bathtub
- L31: Crystal-skinned gemkin
- L32: Clockwork automaton
- L33: AI hologram
- L34: Digital ghost living in a smartphone
- L35: Clone number 7 of a famous scientist
- L36: Time-displaced human from the Edo period
- L37: Alien exchange student
- L38: Deep-sea anglerfolk
- L39: Half-giant
- L40: Sand elemental
- L41: Lightning elemental living in a power line
- L42: Paper doll brought to life
- L43: Stuffed toy with a soul
- L44: Chimera of three animals
- L45: Descendant of a sea serpent
- L46: Plant-human hybrid
- L47: Human with a parasitic twin spirit
- L48: Reincarnated emperor penguin
- L49: Star fragment in human form
- L50: Nine-tailed fox apprentice
- L51: Yokai living in a public bath

---

## 行数の集計（§1〜§12。§13 と付録を除く）

| 状態 | 行数 |
|---|---|
| 取り込み済み | 116 |
| 一部 | 83 |
| 未取り込み | 120 |
| 除外 | 25 |
| ハーネス独自 | 12 |

| 決定 | 行数 |
|---|---|
| 取り込む | 218 |
| 取り込み済み | 110 |
| 外す | 16 |
| ハーネス独自 | 12 |
| 合計 | 356 |
