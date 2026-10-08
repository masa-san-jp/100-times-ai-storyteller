from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from storyteller.cards import generate_task_card
from storyteller.glossary import (
    build_glossary, glossary_id, registration_outputs, related_glossary,
    registered_names, render_glossary_markdown,
)
from storyteller.validation import validate_output


ROOT = Path(__file__).parents[1]


def _outputs():
    return {
        "S3.assign": {
            "cast": [{"id": "c1", "role": "protagonist"}, {"id": "c2", "role": "adversary"}],
            "world_sections": [{"id": "groups", "name": "社会集団"}, {"id": "place", "name": "場の描写"}],
            "world_tasks": [{"id": "groups-001", "section_id": "groups"}],
            "world_fact_tasks": [{"id": f"place.name{n}", "section_id": "place",
                "section": {"name": "場の描写"}, "element": "name", "label": label}
                for n, label in [(1, "地名"), (2, "用語"), (0, "地名")]],
        },
        "S5.name-c1": {"name": "カナリ", "reading": "カナリ", "sources": ["sound-01"]},
        "S5.name-c2": {"name": "トセラ", "reading": "トセラ", "sources": ["sound-01"]},
        "S4.item_name-groups-001": {"name": "ルミナ", "reading": "ルミナ"},
        "S4.fact-place.name1": {"name": "セトナ", "reading": "セトナ"},
        "S4.fact-place.name2": {"name": "リオナ", "reading": "リオナ"},
    }


def test_registration_metadata_and_ids_are_stable():
    outputs = _outputs()
    glossary = build_glossary(outputs)
    assert len(glossary) == 5
    protagonist = next(entry for entry in glossary if entry["name"] == "カナリ")
    assert protagonist == {
        "id": "g" + hashlib.sha256(b"S5.name-c1:0").hexdigest()[:6],
        "name": "カナリ", "reading": "カナリ", "kind": "人物",
        "definition": "主人公", "registered_task": "S5.name-c1",
    }
    item = next(entry for entry in glossary if entry["name"] == "ルミナ")
    assert (item["kind"], item["definition"]) == ("社会集団", "社会集団の項目")
    facts = [entry for entry in glossary if entry["registered_task"].startswith("S4.fact-")]
    assert [entry["id"] for entry in facts] == [glossary_id(f"S4.fact-place.name{i}", 0) for i in (1, 2)]
    assert [entry["definition"] for entry in facts] == ["場の描写の地名", "場の描写の用語"]
    outputs["S4.fact-place.name0"] = {"name": "ナカセ", "reading": "ナカセ"}
    assert all(entry in build_glossary(outputs) for entry in glossary)
    assert build_glossary(dict(reversed(list(outputs.items())))) == build_glossary(outputs)


def test_only_done_registration_outputs_are_used(tmp_path):
    outputs = _outputs()
    manifest = {"tasks": {}}
    for task_id, output in outputs.items():
        task_dir = tmp_path / "tasks" / task_id
        task_dir.mkdir(parents=True)
        (task_dir / "output.json").write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
        manifest["tasks"][task_id] = {"type": task_id.split("-", 1)[0], "state": "done"}
    baseline = build_glossary(registration_outputs(tmp_path, manifest))
    for state in ("ready", "blocked", "claimed", "failed", "skipped"):
        manifest["tasks"]["S5.name-c2"]["state"] = state
        current = build_glossary(registration_outputs(tmp_path, manifest))
        assert all(entry["name"] != "トセラ" for entry in current)
        assert all(entry in baseline for entry in current)
    manifest["tasks"]["S5.name-c2"]["state"] = "done"
    assert build_glossary(registration_outputs(tmp_path, manifest)) == baseline
    assert not (tmp_path / "glossary.json").exists()


def test_registration_does_not_read_scalar_fact_files(tmp_path):
    outputs = _outputs()
    outputs["S3.assign"]["world_fact_tasks"].append({"id": "place.height", "element": "number"})
    manifest = {"tasks": {"S4.fact-place.height": {"type": "S4.fact", "state": "done"}}}
    for task_id, output in outputs.items():
        directory = tmp_path / "tasks" / task_id
        directory.mkdir(parents=True)
        (directory / "output.json").write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
        manifest["tasks"][task_id] = {"type": task_id.split("-", 1)[0], "state": "done"}
    registrations = registration_outputs(tmp_path, manifest)
    assert "S4.fact-place.height" not in registrations
    assert len(build_glossary(registrations)) == 5


def test_collision_is_an_error(monkeypatch):
    monkeypatch.setattr("storyteller.glossary.glossary_id", lambda *_: "g000000")
    with pytest.raises(ValueError, match="ID が衝突"):
        build_glossary(_outputs())


def test_collision_is_recorded_as_a_failed_code_task(tmp_path, monkeypatch):
    from storyteller.orchestrator import Orchestrator, TaskSpec
    from storyteller.manifest import write_manifest
    from storyteller.story_s9 import story_s9_assemble
    from tests.test_story_s9 import _base_outputs, _event

    outputs = _base_outputs({"e001": _event()})
    outputs["S4.fact-place.location_name"] = {"name": "セトナ", "reading": "セトナ"}
    monkeypatch.setattr("storyteller.story_s9._all_outputs", lambda _: outputs)
    monkeypatch.setattr("storyteller.glossary.glossary_id", lambda *_: "g000000")
    definition = {"id": "S9.assemble", "version": 1, "kind": "code", "handler": "story_s9_assemble"}
    orchestrator = Orchestrator(tmp_path, {"S9.assemble": definition}, {"story_s9_assemble": story_s9_assemble})
    run_id = orchestrator.create_run(task_specs=[TaskSpec("S9.assemble", "S9.assemble")], seed=7, scale={"preset": "short"})
    manifest = orchestrator.load_run(run_id)
    manifest["input_ratio"] = 0.7
    write_manifest(orchestrator.run_dir(run_id) / "manifest.json", manifest)
    manifest = orchestrator.advance(run_id)
    assert manifest["status"] == "stalled"
    assert manifest["tasks"]["S9.assemble"]["state"] == "failed"
    assert "ID が衝突" in manifest["tasks"]["S9.assemble"]["error"]


def test_related_entries_do_not_expose_registration_tasks():
    outputs = _outputs()
    glossary = build_glossary(outputs)
    inputs = {"characters": [{"id": "c1"}], "world_sections": [{"id": "place"}]}
    related = related_glossary(glossary, outputs["S3.assign"], inputs, ["e001"])
    assert {entry["name"] for entry in related} == {"カナリ", "セトナ", "リオナ"}
    own_section = related_glossary(glossary, outputs["S3.assign"], {}, ["groups-001"])
    assert [entry["name"] for entry in own_section] == ["ルミナ"]
    direct = related_glossary(glossary, outputs["S3.assign"], {"origin": glossary[0]["id"]}, [])
    assert [entry["id"] for entry in direct] == [glossary[0]["id"]]
    indexed = related_glossary(glossary, outputs["S3.assign"], {"names": {"c2": {"name": "トセラ"}}}, [])
    assert [entry["name"] for entry in indexed] == ["トセラ"]
    definition = yaml.safe_load((ROOT / "harness/story/tasks/S7.detail.yaml").read_text(encoding="utf-8"))
    # Render just the optional glossary slot to check the isolation boundary.
    definition["inputs"] = {"glossary": definition["inputs"]["glossary"]}
    card = generate_task_card(definition, "ticket", inputs={"glossary": related})
    for entry in related:
        assert entry["name"] in card and entry["id"] in card
    assert "S4" not in card and "S5" not in card
    assert "トセラ" not in card and "ルミナ" not in card


@pytest.mark.parametrize("task_type", [
    "S4.section", "S4.item", "S5.profile", "S5.intro", "S5.appearance", "S5.motive",
    "S5.catchphrase", "S5.personality", "S5.values", "S5.backstory", "S5.relationship",
    "S5.voice", "S5.inner_conflict", "S7.event", "S7.detail",
])
def test_descriptive_tasks_reject_unregistered_names(task_type):
    definition = yaml.safe_load((ROOT / "harness/story/tasks" / f"{task_type}.yaml").read_text(encoding="utf-8"))
    check = next(check for check in definition["validate"]["checks"] if "no_new_proper_nouns" in check)
    assert check["no_new_proper_nouns"]["mode"] == "fail"
    # Isolate the proper noun check from unrelated length/schema constraints.
    definition = {"output": "text", "validate": {"checks": [check]}}
    glossary = build_glossary(_outputs())
    assert validate_output(definition, "「カナリ」とセトナを訪ねた。", registered_names=registered_names(glossary)).passed
    assert validate_output(definition, "『カナリ』を訪ねた。", registered_names=registered_names(glossary)).passed
    assert validate_output(definition, "ゼラフィナを訪ねた。", {"place": "ゼラフィナ"}).passed
    result = validate_output(definition, "ゼラフィナを訪ねた。", registered_names=registered_names(glossary))
    assert not result.passed and "未登録" in result.errors[0]
    assert validate_output(definition, "希望の水を汲む。", {"glossary": [{"name": "希望の水"}]}).passed
    assert not validate_output(definition, "「希望の水」を汲む。").passed


def test_glossary_export_escapes_table_cells():
    glossary = build_glossary(_outputs())
    glossary[0]["definition"] = "丘|海\nの名前。"
    markdown = render_glossary_markdown(glossary)
    assert "丘\\|海 の名前。" in markdown
    assert "登録したタスク" in markdown
    assert all(entry["id"] in markdown and entry["registered_task"] in markdown for entry in glossary)


def test_s9_exports_glossary_and_accepts_glossary_source_ids(tmp_path):
    from tests.test_story_s9 import _base_outputs, _context, _event
    from storyteller.story_s9 import story_s9_assemble

    outputs = _base_outputs({"e001": _event()})
    identifier = build_glossary(outputs)[0]["id"]
    outputs["S7.assemble-e001"]["sources"] = [identifier]
    context = _context(tmp_path, outputs, {"kind": "free", "paragraphs": []})
    story_s9_assemble(context)
    markdown = (context.run_dir / "story" / "glossary.md").read_text(encoding="utf-8")
    assert identifier in markdown and "| カナ | カナ | 人物 | 主人公 | S5.name-c1 |" in markdown
