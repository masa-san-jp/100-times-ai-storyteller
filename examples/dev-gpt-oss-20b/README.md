# 作例：手元のパソコンの無料モデルで作った物語

このツールで実際に作った物語の見本です。どんなものができるかを、ここで確かめられます。

## 何を入力したか
[input.md](input.md) の2行だけです。

> 静かな町で、失われた記録を探す。
> 境界を守る仕組みが、暮らしの選択を変えていく。

## 何ができたか
- **物語のもと（正本）**：[story/](story/)
  - [story.md](story/story.md)：物語の筋
  - [world.md](story/world.md)：世界の設定
  - [characters.md](story/characters.md)：登場人物
  - [world_facts.md](story/world_facts.md)：暦・年表などの事実
  - [glossary.md](story/glossary.md)：用語集
- **読める形にしたもの**：[formats/](formats/)
  - [小説](formats/novel/novel.md)
  - [映画シナリオ](formats/screenplay/screenplay.fountain)：Fountain 形式。脚本用のアプリで開けます。
  - [スライド](formats/slides/slides.md)：Marp 形式。Marp でスライドとして表示できます。

## どうやって作ったか
- 使ったモデル：gpt-oss:20b。Ollama で手元のパソコンに入れた無料のモデルです。
- 物語の大きさ：いちばん小さい試し用の大きさです。数時間の出来事を1〜2人の人物で描く、ごく短い物語になります。
- かかった時間：約2時間20分（3つの形まで）

## 見てわかる限界
小説の第1章と映画シナリオの全場面は、本文が「（未定）」になっています。小さなモデルの書いた本文が、長さや書き方の決まりを満たせなかったためです。Claude などの性能の高いモデルを使うと、こうした抜けは出にくくなります。

## 自分で作るには
リポジトリの [README](../../README.md) の「物語を作る」に、手順があります。
