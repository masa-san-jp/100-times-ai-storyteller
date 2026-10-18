# 作例：Ollama で動かした gpt-oss:20b で作った物語

このツールで実際に作った物語の見本です。どんなものができるかを、ここで確かめられます。

## 使ったAIと環境
- **AIモデル**：gpt-oss:20b。OpenAI が公開している、誰でも無料でダウンロードして使えるモデルです。ファイルの大きさは約13GBです。
- **動かし方**：[Ollama](https://ollama.com/)（AIモデルを自分のパソコンで動かすための無料のアプリ）で動かしました。インターネット上の有料AIサービスは使っていないので、利用料はかかりません。
- **パソコン**：MacBook Pro（Apple M4 Max、メモリ128GB）、Ollama 0.30.11
- **かかった時間**：約2時間20分（物語のもとから、3つの形の書き出しまで）

## 何を入力したか
[input.md](input.md) の2行だけです。

> 静かな町で、失われた記録を探す。
> 境界を守る仕組みが、暮らしの選択を変えていく。

物語の大きさには、いちばん小さい試し用の大きさを選びました。数時間の出来事を1〜2人の人物で描く、ごく短い物語になります。

## 何ができたか
- **物語のもと（正本）**：[story/](story/)
  - [story.md](story/story.md)：物語の筋
  - [world.md](story/world.md)：世界の設定
  - [characters.md](story/characters.md)：登場人物
  - [world_facts.md](story/world_facts.md)：暦・年表などの事実
  - [glossary.md](story/glossary.md)：用語集
- **読める形にしたもの**：[formats/](formats/)
  - [小説](formats/novel/novel.md)
  - [映画シナリオ](formats/screenplay/screenplay.fountain)：Fountain 形式です。脚本用のアプリで開けます。
  - [スライド](formats/slides/slides.md)：Marp 形式です。Marp を使うと、スライドとして表示できます。

## 見てわかる限界
小説の第1章と、映画シナリオの全場面は、本文が「（未定）」になっています。gpt-oss:20b が書いた本文が、長さや書き方の決まりを満たせなかったためです。Claude（Anthropic）などの性能の高いAIを使うと、こうした抜けは出にくくなります。

## 自分で作るには
1. [Ollama](https://ollama.com/) を入れて、`ollama pull gpt-oss:20b` でモデルをダウンロードします。
2. リポジトリの [README](../../README.md) の「物語を作る」の手順に従います。

このモデルを動かすには、少なくとも16GBのメモリがあるパソコンが必要です。
