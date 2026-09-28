from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from storyteller.orchestrator import Orchestrator, TaskSpec
from storyteller.manifest import write_manifest
from storyteller.story_s9 import story_s9_assemble
from storyteller.validation import validate_document


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


def test_s9_assembles_canonical_story_and_clears_last_foreshadowing(tmp_path: Path) -> None:
    assignment = _assignment()
    slots = {"slots": [{"id": f"e{n:03d}", "thread": "t001", "stage": {"id": "start", "name": "始まり"}} for n in range(1, 4)]}
    outputs: dict[str, object] = {
        "S3.assign": assignment,
        "S6.expand": slots,
        "S4.section-place": {"body": "水路の町の描写", "sources": ["place:t1"]},
    }
    for event_id in ("e001", "e002", "e003"):
        outputs[f"S7.event-{event_id}"] = {"when": "朝", "where": "水路", "who": ["c1"], "why": "道を確かめる", "intent": "進む", "what": "歩いた", "result": "道が開いた", "emotion": "安心", "foreshadowing": "次の兆し", "sources": ["c1"]}
    for field, value in {"name": "カナ", "profile": "旅を続ける人物。", "intro": "道を探す旅人。", "appearance": "短い髪と外套。", "motive": "道を確かめたい。", "catchphrase": "私は進む。"}.items():
        outputs[f"S5.{field}-c1"] = {field: value, "sources": ["c1"]}
    outputs["S5.name-c1"] = {"name": "カナ", "reading": "カナ", "sources": ["sound-01"]}

    context = _context(tmp_path, outputs, {"kind": "free", "paragraphs": [{"id": "p001", "text": "旅の素材"}]})
    story_s9_assemble(context)
    story_path = context.run_dir / "story" / "story.json"
    markdown_path = context.run_dir / "story" / "story.md"
    import json
    with story_path.open("r", encoding="utf-8") as stream:
        story = json.load(stream)
    validate_document(story, ROOT / "schemas/story.schema.json")
    assert story["events"][-1]["foreshadowing"] == ""
    assert markdown_path.read_text(encoding="utf-8").index("## 登場人物") < markdown_path.read_text(encoding="utf-8").index("## 世界") < markdown_path.read_text(encoding="utf-8").index("## 出来事")


def test_s9_rejects_unknown_event_reference(tmp_path: Path) -> None:
    assignment = _assignment()
    outputs: dict[str, object] = {
        "S3.assign": assignment,
        "S6.expand": {"slots": [{"id": "e001", "thread": "t001", "stage": {"id": "start", "name": "始まり"}}]},
        "S4.section-place": {"body": "水路の町の描写", "sources": ["place:t1"]},
        "S7.event-e001": {"when": "朝", "where": "水路", "who": ["c999"], "why": "進む", "intent": "進む", "what": "歩いた", "result": "変化", "emotion": "驚き", "foreshadowing": "", "sources": ["c1"]},
    }
    for field, value in {"name": "カナ", "profile": "旅を続ける人物。", "intro": "道を探す旅人。", "appearance": "短い髪と外套。", "motive": "道を確かめたい。", "catchphrase": "私は進む。"}.items():
        outputs[f"S5.{field}-c1"] = {field: value, "sources": ["c1"]}
    outputs["S5.name-c1"] = {"name": "カナ", "reading": "カナ", "sources": ["sound-01"]}
    context = _context(tmp_path, outputs, {"kind": "free", "paragraphs": []})
    try:
        story_s9_assemble(context)
    except ValueError as error:
        assert "who" in str(error)
    else:
        raise AssertionError("未知の who を受け入れました")
