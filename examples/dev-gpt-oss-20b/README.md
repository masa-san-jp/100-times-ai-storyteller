# dev 規模の作例（gpt-oss:20b、ローカルの Ollama）

README の手順のとおり、`st new --free input.md --scale dev` → `st auto --provider ollama --model gpt-oss:20b --workers 4` → `st format --as novel / screenplay / slides` → `st auto` を実行した結果です（v0.0.1、main 639254f）。

- 所要時間：約139分（3様式を含む）
- story/：正本（story.md、world.md、characters.md、world_facts.md、glossary.md、story.json）
- formats/：小説（novel.md）、映画シナリオ（screenplay.fountain）、スライド（slides.md）

小型のローカルモデルでは、一部の本文が字数や書式の検査に通らず「（未定）」になります。この作例では、小説の第1章と映画シナリオの全場面がそうなりました。高性能なモデルを使うと、この欠けは出にくくなります。
