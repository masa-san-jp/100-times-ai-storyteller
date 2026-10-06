from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from storyteller.orchestrator import Orchestrator, TaskSpec
from storyteller.manifest import write_manifest
from storyteller.story_s9 import story_s9_assemble
from storyteller.validation import validate_document
from tests.test_character_facts import _output as _facts, _world_output


ROOT = Path(__file__).parents[1]


def _assignment() -> dict[str, object]:
    element = lambda axis: {"id": f"{axis}:t1", "text": f"{axis}の要素"}
    return {
        "r": 0.7,
        "threads": [{"id": "t001", "kind": "main", "part": None, "plot_type": "quest", "plot_type_name": "旅", "events": 3, "structure": "three-beat"}],
        "cast": [{
            "id": "c1", "role": "protagonist", "elements": {"want": element("want"), "ability": element("ability"), "duty": element("duty"), "age": element("age"), "gender": element("gender"), "species": element("species"), "taboo": element("taboo")},
            "name_sound": {"set_id": "sound-01", "description": "短い響き", "sounds": ["カ", "ナ", "リ", "オ", "セ", "ト"]},
        }],
        "absent_roles": ["messenger", "supporter", "adversary"],
        "world": {"place": element("place"), "era": element("era"), "object": element("object"), "theme": None},
        "world_sections": [{"id": "place", "name": "場の描写", "definition": "場所", "viewpoints": ["場所"], "level": 0, "kind": "single", "max_chars": 400, "prerequisites": []}],
    }


def _context(tmp_path: Path, outputs: dict[str, object], run_input: object) -> SimpleNamespace:
    definition = {"id": "S9.assemble", "version": 1, "kind": "code", "handler": "story_s9_assemble"}
    orchestrator = Orchestrator(tmp_path, {"S9.assemble": definition})
    run_id = orchestrator.create_run(task_specs=[TaskSpec("S9.assemble", "S9.assemble")], seed=7, input_data=run_input, scale={"preset": "short"})
    manifest = orchestrator.load_run(run_id)
    manifest["input_ratio"] = 0.7
    write_manifest(orchestrator.run_dir(run_id) / "manifest.json", manifest)
    return SimpleNamespace(run_id=run_id, task_id="S9.assemble", task={}, definition=definition, inputs={}, run_input=run_input, outputs=outputs, dependency_outputs=outputs, data_dir=tmp_path, run_dir=orchestrator.run_dir(run_id))


def _base_outputs(events: dict[str, dict[str, object]]) -> dict[str, object]:
    assignment = _assignment()
    slots = {
        "slots": [
            {"id": event_id, "thread": "t001", "stage": {"id": "start", "name": "始まり"}}
            for event_id in events
        ]
    }
    outputs: dict[str, object] = {
        "S3.assign": assignment,
        "S5.facts-c1": _facts(),
        "S4.facts-place-1": _world_output(),
        "S6.expand": slots,
        "S4.section-place": {"body": "水路の町の描写", "sources": ["place:t1"]},
    }
    for event_id, event in events.items():
        outputs[f"S7.event-{event_id}"] = event
    for field, value in {
        "name": "カナ",
        "profile": "旅を続ける人物。",
        "intro": "道を探す旅人。",
        "appearance": "短い髪と外套。",
        "motive": "道を確かめたい。",
        "personality": "物静かで、観察してから動く気質。",
        "values": "交わした約束を何より重んじる。",
        "voice": "ゆっくりと言葉を選んで話す。「急がなくていい」。",
        "inner_conflict": "先へ進みたい気持ちと、何かを置き去りにする不安がせめぎ合う。",
        "catchphrase": "私は進む。",
    }.items():
        outputs[f"S5.{field}-c1"] = {field: value, "sources": ["c1"]}
    outputs["S5.name-c1"] = {"name": "カナ", "reading": "カナ", "sources": ["sound-01"]}
    outputs["S5.backstory-c1-p1"] = {
        "backstory": "幼い頃、水路の近くの町で育った。",
        "sources": ["c1"],
    }
    outputs["S5.backstory-c1-p2"] = {
        "backstory": "大人になってから、町を出て旅をする決意をした。",
        "sources": ["c1"],
    }
    return outputs


def _event(**overrides: object) -> dict[str, object]:
    event = {
        "when": "朝",
        "where": "水路",
        "who": ["c1"],
        "why": "道を確かめる",
        "intent": "進む",
        "what": "歩いた",
        "result": "道が開いた",
        "emotion": "安心",
        "foreshadowing": "次の兆し",
        "sources": ["c1"],
    }
    event.update(overrides)
    return event


def test_s9_assembles_three_canonical_files_and_clears_last_foreshadowing(tmp_path: Path) -> None:
    events = {f"e{n:03d}": _event() for n in range(1, 4)}
    outputs = _base_outputs(events)

    context = _context(tmp_path, outputs, {"kind": "free", "paragraphs": [{"id": "p001", "text": "旅の素材"}]})
    story_s9_assemble(context)
    story_dir = context.run_dir / "story"
    story = json.loads((story_dir / "story.json").read_text(encoding="utf-8"))
    validate_document(story, ROOT / "schemas/story.schema.json")
    assert story["events"][-1]["foreshadowing"] == ""

    story_md = (story_dir / "story.md").read_text(encoding="utf-8")
    characters_md = (story_dir / "characters.md").read_text(encoding="utf-8")
    world_md = (story_dir / "world.md").read_text(encoding="utf-8")

    # story.md は出来事だけを並べ、人物・世界の節は別ファイルに分かれる。
    assert "## 登場人物" not in story_md
    assert "## 世界" not in story_md
    assert "1. " in story_md
    assert "## カナ" in characters_md
    assert characters_md.index("### 事実のシート") < characters_md.index("### 描写")
    assert "| 身長（cm） | 170 |" in characters_md
    assert story["meta"]["volume"]["characters"]["chars"] == sum(not char.isspace() for char in characters_md)
    assert "## 場の描写" in world_md


def test_s9_translates_role_to_japanese(tmp_path: Path) -> None:
    events = {"e001": _event()}
    outputs = _base_outputs(events)
    context = _context(tmp_path, outputs, {"kind": "free", "paragraphs": []})
    story_s9_assemble(context)
    characters_md = (context.run_dir / "story" / "characters.md").read_text(encoding="utf-8")
    assert "主人公" in characters_md
    assert "protagonist" not in characters_md


def test_s9_includes_present_s5_items_in_characters_markdown(tmp_path: Path) -> None:
    events = {"e001": _event()}
    outputs = _base_outputs(events)
    context = _context(tmp_path, outputs, {"kind": "free", "paragraphs": []})
    story_s9_assemble(context)
    characters_md = (context.run_dir / "story" / "characters.md").read_text(encoding="utf-8")
    assert "プロフィール：旅を続ける人物。" in characters_md
    assert "動機：道を確かめたい。" in characters_md
    assert "紹介：道を探す旅人。" in characters_md
    assert "外見：短い髪と外套。" in characters_md
    assert "決め台詞：私は進む。" in characters_md
    assert "性格：物静かで、観察してから動く気質。" in characters_md
    assert "価値観：交わした約束を何より重んじる。" in characters_md
    assert "口調：ゆっくりと言葉を選んで話す。「急がなくていい」。" in characters_md
    assert "内的葛藤：先へ進みたい気持ちと、何かを置き去りにする不安がせめぎ合う。" in characters_md
    assert "来歴：" in characters_md
    assert "幼い頃、水路の近くの町で育った。" in characters_md
    assert "大人になってから、町を出て旅をする決意をした。" in characters_md
    # 人物が1人しかいないので人物関係は0件であり、見出しごと出ない。
    assert "人物関係" not in characters_md


def test_s9_summary_lists_fields_without_joining_sentences(tmp_path: Path) -> None:
    events = {
        "e001": _event(
            when="祭りの夜。",
            where="水路の近く、",
            why="大切な人を守るため",
            what="橋を渡った。",
            result="道が開けた、",
        ),
        # 実機で「受け入れたくてために」「頂上でで」が生じた語尾。
        "e002": _event(where="丘の頂上で", why="自らの本質を受け入れたくて"),
        "e003": _event(why="約束を果たすために"),
        "e004": _event(why="事情を確かめたいから"),
    }
    outputs = _base_outputs(events)
    context = _context(tmp_path, outputs, {"kind": "free", "paragraphs": []})
    story_s9_assemble(context)
    story_md = (context.run_dir / "story" / "story.md").read_text(encoding="utf-8")
    for index, event in enumerate(events.values(), start=1):
        assert (
            f"## {index}. 始まり\n\n"
            f"- いつ：{event['when']}\n"
            f"- どこで：{event['where']}\n"
            "- 誰が：カナ\n"
            f"- 目的：{event['why']}\n"
            f"- 行動：{event['what']}\n"
            f"- 結果：{event['result']}\n"
        ) in story_md
    assert "頂上でで" not in story_md
    assert "受け入れたくてために" not in story_md
    assert "その結果、" not in story_md
    story = json.loads((context.run_dir / "story" / "story.json").read_text(encoding="utf-8"))
    for actual, expected in zip(story["events"], events.values()):
        for field in ("when", "where", "who", "why", "what", "result"):
            assert actual[field] == expected[field]


def test_s9_summary_resolves_multiple_names_and_keeps_detail_in_event_order(tmp_path: Path) -> None:
    outputs = _base_outputs({"e001": _event(who=["c1", "c2"]), "e002": _event()})
    assignment = outputs["S3.assign"]
    other = {**assignment["cast"][0], "id": "c2", "role": "supporter"}
    assignment["cast"].append(other)
    for task_id, value in list(outputs.items()):
        if task_id.startswith("S5."):
            outputs[task_id.replace("c1", "c2")] = {**value, "sources": ["c2"]}
    outputs["S5.name-c2"] = {"name": "リオ", "reading": "リオ", "sources": ["sound-01"]}
    outputs["S7.detail-e001-b2"] = "二番目の場面。"
    outputs["S7.detail-e002-b1"] = "次の出来事の場面。"
    outputs["S7.detail-e001-b1"] = "最初の場面。"
    context = _context(tmp_path, outputs, {"kind": "free", "paragraphs": []})
    story_s9_assemble(context)
    story_md = (context.run_dir / "story" / "story.md").read_text(encoding="utf-8")
    assert "- 誰が：カナ、リオ\n" in story_md
    assert "- 結果：道が開いた\n\n最初の場面。\n\n二番目の場面。\n\n## 2. 始まり" in story_md
    assert story_md.endswith("次の出来事の場面。\n")


def test_s9_aggregates_volume_and_records_shortfall_warnings(tmp_path: Path) -> None:
    events = {"e001": _event()}
    outputs = _base_outputs(events)
    context = _context(tmp_path, outputs, {"kind": "free", "paragraphs": []})
    result = story_s9_assemble(context)

    story = json.loads((context.run_dir / "story" / "story.json").read_text(encoding="utf-8"))
    volume = story["meta"]["volume"]
    for key, floor in (("characters", 10000), ("world", 100000), ("story", 100000)):
        assert volume[key]["floor_chars"] == floor
        assert 0 < volume[key]["chars"] < floor

    warnings = result.manifest_updates.get("warnings", [])
    assert any(warning.startswith("分量が最低ラインに満たない：人物 ") for warning in warnings)
    assert any(warning.startswith("分量が最低ラインに満たない：世界 ") for warning in warnings)
    assert any(warning.startswith("分量が最低ラインに満たない：物語 ") for warning in warnings)
    for key, label in (("characters", "人物"), ("world", "世界"), ("story", "物語")):
        expected = f"分量が最低ラインに満たない：{label} {volume[key]['chars']}/{volume[key]['floor_chars']}"
        assert expected in warnings


def test_s9_rejects_unknown_event_reference(tmp_path: Path) -> None:
    outputs = _base_outputs({"e001": _event(who=["c999"], sources=["c1"])})
    context = _context(tmp_path, outputs, {"kind": "free", "paragraphs": []})
    try:
        story_s9_assemble(context)
    except ValueError as error:
        assert "who" in str(error)
    else:
        raise AssertionError("未知の who を受け入れました")
