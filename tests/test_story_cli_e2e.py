from __future__ import annotations

import json
import hashlib
import contextlib
import io
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from storyteller.cards import input_char_count
from storyteller.story_s9 import _validate_references
from storyteller.validation import validate_document
from storyteller.cli import main as cli_main
from storyteller.new_run import create_story_orchestrator


ROOT = Path(__file__).parents[1]
RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")


def _cli(data_dir: Path, *arguments: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    previous_home = os.environ.get("STORYTELLER_HOME")
    os.environ["STORYTELLER_HOME"] = str(data_dir)
    stdout = io.StringIO()
    stderr = io.StringIO()
    stdin = io.StringIO(input_text or "")
    previous_stdin = sys.stdin
    try:
        sys.stdin = stdin
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            returncode = cli_main(arguments)
    finally:
        sys.stdin = previous_stdin
        if previous_home is None:
            os.environ.pop("STORYTELLER_HOME", None)
        else:
            os.environ["STORYTELLER_HOME"] = previous_home
    return subprocess.CompletedProcess(
        [sys.executable, "-m", "storyteller.cli", *arguments],
        returncode,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
    )


def _manifest(data_dir: Path, run_id: str) -> dict[str, Any]:
    path = data_dir / "runs" / run_id / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _claimed_task(
    data_dir: Path,
    run_id: str,
    ticket: str,
) -> tuple[str, dict[str, Any]]:
    manifest = _manifest(data_dir, run_id)
    for task_id, task in manifest["tasks"].items():
        if (task.get("claim") or {}).get("ticket") == ticket:
            return task_id, task
    raise AssertionError(f"claim が manifest にありません: {ticket}")


def _task_input(data_dir: Path, run_id: str, task_id: str) -> dict[str, Any]:
    path = data_dir / "runs" / run_id / "tasks" / task_id / "input.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _first_id(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("id", "set_id"):
            identifier = value.get(key)
            if isinstance(identifier, str):
                return identifier
        for item in value.values():
            try:
                return _first_id(item)
            except AssertionError:
                continue
    elif isinstance(value, list):
        for item in value:
            try:
                return _first_id(item)
            except AssertionError:
                continue
    raise AssertionError("入力に ID がありません")


def _filler_text(person_id: str, field: str, length: int) -> str:
    """Build placeholder text of exactly ``length`` characters.

    Used for the S5 items whose validation checks a min/max character range
    (task-model.md §6.2 min_chars/max_chars); a short fixed sentence like the
    other fake S5 outputs would fail those checks.
    """

    unit = f"{person_id}の{field}に関する具体的な記述。"
    repeated = unit * (length // len(unit) + 2)
    return repeated[:length - 1] + "。"


def _fake_output(
    task_id: str,
    task: dict[str, Any],
    inputs: dict[str, Any],
    *,
    send_comparison_yes: bool,
) -> dict[str, Any] | str:
    task_type = task["type"]
    index = task.get("index", [])

    if task_type == "S1.extract":
        paragraph_id = index[0]
        paragraph_number = int(paragraph_id[1:])
        kinds = ("theme", "suppression", "conflict", "desire", "value")
        materials = [
            {
                "id": f"m{(paragraph_number - 1) * 5 + offset:03d}",
                "text": f"段落{paragraph_number}から見える素材{offset}",
                "kind": kind,
            }
            for offset, kind in enumerate(kinds, start=1)
        ]
        return {"materials": materials, "sources": [paragraph_id]}

    if task_type == "S2.expand":
        source = inputs["material"]["id"]
        marker = "-".join(index)
        return {
            "items": [f"{marker}から生まれる具体的な要素{number}" for number in range(1, 6)],
            "counterpart": f"{marker}から生まれる対極の要素",
            "sources": [source],
        }

    if task_type == "S4.item_name":
        reading = "".join(inputs["name_sound"]["sounds"][:2])
        return {"name": reading, "reading": reading}

    if task_type in {"S4.section", "S4.item"}:
        body = _filler_text("世界", "本文", 900 if task_type == "S4.item" else 1150)
        # Deterministic, distinct synthetic openings keep the shared fixture
        # from accidentally exercising the diversity retry limit everywhere.
        opening = "".join(chr(0x4E00 + byte) for byte in hashlib.shake_256(task_id.encode("utf-8")).digest(80))
        return opening + body[80:]

    if task_type == "S5.name":
        sound = inputs["name_sound"]
        reading = "".join(sound["sounds"][:2])
        return {"name": reading, "reading": reading, "sources": [sound["set_id"]]}

    if task_type.startswith("S5."):
        field = task_type.removeprefix("S5.")
        person_id = inputs["character"]["id"]
        short_values = {
            "intro": f"{person_id}の短い紹介。",
            "motive": f"{person_id}が行動する動機。",
            "catchphrase": f"私は{person_id}として進む。",
        }
        if field in short_values:
            return {field: short_values[field], "sources": [person_id]}
        # story-pipeline.md §8 / ADR-0007: the new S5 items each enforce a
        # min_chars/max_chars range (tables/volume.yaml), so the placeholder
        # text must actually land inside it, not just be non-empty.
        volume_ranges = {
            "profile": (800, 1500),
            "appearance": (400, 800),
            "personality": (500, 1000),
            "values": (400, 800),
            "voice": (400, 800),
            "inner_conflict": (500, 1000),
            "backstory": (1000, 2000),
            "relationship": (300, 600),
        }
        minimum, maximum = volume_ranges[field]
        target_length = (minimum + maximum) // 2
        return _filler_text("人物", "項目", target_length)

    if task_type == "S7.event":
        characters = inputs["characters"]
        character_ids = [character["id"] for character in characters]
        return {
            "when": "町に変化の兆しが現れたとき。",
            "where": "町の境界で。",
            "who": character_ids,
            "why": "状況を確かめるため。",
            "intent": "小さな選択をする。",
            "what": "人物は境界の仕組みを動かす。",
            "result": f"新しい道筋が見える（試行{task.get('attempt', 0)}）。",
            "emotion": "静かな決意を抱く。",
            "foreshadowing": "遠くで次の変化が始まる。",
            "sources": [character_ids[0]],
        }

    if task_type == "S7.detail":
        # Keep the harness fixture inside tables/volume.yaml's story_beat
        # range so S9 can verify the short-scale floor using real text output.
        sentence = "人物は場面の状況を確認し、行動の結果を受け止めた。"
        return sentence * 80

    if task_type == "S8.compare":
        event_a = inputs["event_a"]["id"]
        event_b = inputs["event_b"]["id"]
        answer = "yes" if send_comparison_yes else "no"
        reason = "比較対象と結果が矛盾する。" if answer == "yes" else "二つの記述は整合している。"
        return {"answer": answer, "reason": reason, "sources": [event_a, event_b]}

    raise AssertionError(f"未知の LLM タスクです: {task_type}")


@pytest.mark.parametrize("long_inputs", [False, True], ids=["normal", "long-inputs"])
def test_story_harness_cli_runs_to_s9_with_schema_outputs_and_regeneration(
    tmp_path: Path,
    long_inputs: bool,
) -> None:
    data_dir = tmp_path / "data"
    free_input = tmp_path / "free.md"
    free_input.write_text(
        "静かな町で、失われた記録を探す。\n\n"
        "境界を守る仕組みが、暮らしの選択を変えていく。\n\n"
        "誰かの沈黙を手がかりに、明日の道を考える。\n",
        encoding="utf-8",
    )

    created = _cli(
        data_dir,
        "new",
        "--free",
        str(free_input),
        "--scale",
        "short",
        "--seed",
        "1515",
    )
    assert created.returncode == 0, created.stderr
    run_id = created.stdout.strip()
    assert RUN_ID.fullmatch(run_id)
    harness = create_story_orchestrator(data_dir)
    model = yaml.safe_load((ROOT / "config/models.yaml").read_text(encoding="utf-8"))["models"]["gpt-oss:20b"]

    sent_comparison_yes = False
    comparison_answers: list[str] = []
    invalidated_s7 = False
    trimmed_tasks: set[str] = set()
    trim_counts: dict[str, int] = {}
    extended_types: set[str] = set()
    continuation_outputs: dict[str, str] = {}
    extended_tasks: set[str] = set()
    similar_facets: list[str] = []
    rewritten_s4 = False
    for _ in range(500):
        claimed = harness.claim_next(run_id, executor_id="e2e", isolation="none")
        if claimed is None:
            break
        task_id, task = _claimed_task(data_dir, run_id, claimed["ticket"])
        inputs = _task_input(data_dir, run_id, task_id)
        is_comparison = task["type"] == "S8.compare"
        output = _fake_output(
            task_id,
            task,
            inputs,
            send_comparison_yes=is_comparison and not sent_comparison_yes,
        )
        definition = harness.task_definitions[task["type"]]
        assert input_char_count(definition, inputs) <= definition.get("max_input_chars", 3000)
        # Conservative Japanese estimate: 2 tokens per card character, plus
        # the model's full output allowance. Includes retries and scene tails.
        assert 2 * len(claimed["card"]) + model["max_tokens"] <= model["context_length"], task_id
        if long_inputs:
            if task["type"] in {"S4.section", "S4.item"}:
                output = output[:80] + _filler_text("世界", "本文", 1120)
            elif task["type"] == "S5.profile":
                output = _filler_text("人物", "項目", 1500)
            elif task["type"] == "S5.voice":
                output = _filler_text("人物", "口調", 800)
            elif task["type"] == "S7.event":
                output.update({field: "具体的な出来事の説明。" * 10 for field in output if isinstance(output[field], str)})
        if task["type"] == "S4.section":
            if not similar_facets:
                similar_facets = sorted(
                    name for name in _manifest(data_dir, run_id)["tasks"]
                    if name.startswith("S4.section-place-")
                )[-2:]
                assert len(similar_facets) == 2
            if task_id in similar_facets and task["invalidations"] == 0:
                opening = "丘の頂上に立つと、風が皮膚に触れ、草の揺れが足元の道筋を知らせる。" * 3
                output = opening[:80] + output[80:]
            if task["invalidations"]:
                assert task_id == similar_facets[1]
                assert "この書き出しと似ないように書き始める" in claimed["card"]
                assert "丘の頂上に立つと" in claimed["card"]
                rewritten_s4 = True
        # Mix normal outputs with overlong outputs for each long task type.
        # Two section facets reproduce the observed f1/f2 failures.
        if trim_counts.get(task["type"], 0) < (2 if task["type"] == "S4.section" else 1):
            definition = harness.task_definitions[task["type"]]
            for check in definition.get("validate", {}).get("checks", []):
                arguments = check.get("max_chars", {}) if isinstance(check, dict) else {}
                if arguments.get("fix") != "trim":
                    continue
                trim_arguments = arguments
                maximum = arguments["n"]
                original_text = output[arguments["field"]] if "field" in arguments else output
                expected_fixed_text = original_text if original_text.endswith("。") else original_text + "。"
                long_text = expected_fixed_text + "余" * (maximum + 10 - len(expected_fixed_text)) + "。"
                if "field" in arguments:
                    output[arguments["field"]] = long_text
                else:
                    output = long_text
                trimmed_tasks.add(task_id)
                trim_counts[task["type"]] = trim_counts.get(task["type"], 0) + 1
        definition = harness.task_definitions[task["type"]]
        if definition.get("extend_to_min"):
            if task_id in continuation_outputs:
                assert "既出の文章を繰り返さず、同じ内容をさらに具体的に書き足す" in claimed["card"]
                output = continuation_outputs.pop(task_id)
            elif task["type"] not in extended_types and task_id not in trimmed_tasks:
                assert isinstance(output, str)
                minimum = next(check["min_chars"]["n"] for check in definition["validate"]["checks"] if "min_chars" in check)
                prefix = "短い説明。" * (minimum // 12)
                continuation_outputs[task_id] = output[len(prefix):]
                output = prefix
                extended_types.add(task["type"])
                extended_tasks.add(task_id)
        if is_comparison:
            comparison_answers.append(output["answer"])
            if output["answer"] == "yes":
                sent_comparison_yes = True
        submitted_output = (
            output
            if definition["output"] == "text"
            else json.dumps(output, ensure_ascii=False)
        )
        submitted = harness.submit(claimed["ticket"], submitted_output)
        assert submitted.accepted, (task_id, submitted.errors)
        if task_id in trimmed_tasks:
            assert f"{task_id}: 字数の上限で切り詰め" in submitted.warnings
            task_dir = data_dir / "runs" / run_id / "tasks" / task_id
            if definition["output"] == "text":
                stored = (task_dir / "output.md").read_text(encoding="utf-8")
                fixed_text = submitted.value
            else:
                stored = json.loads((task_dir / "output.json").read_text(encoding="utf-8"))
                fixed_text = submitted.value[trim_arguments["field"]]
            assert stored == submitted.value
            assert fixed_text == expected_fixed_text
        current = _manifest(data_dir, run_id)
        invalidated_s7 = invalidated_s7 or any(
            task_name.startswith("S7.event-")
            and record["invalidations"] == 1
            for task_name, record in current["tasks"].items()
        )
    else:
        raise AssertionError("CLI 実行が上限回数で完走しませんでした")

    final_manifest = _manifest(data_dir, run_id)
    assert final_manifest["status"] == "completed"
    if long_inputs:
        assert len([name for name in final_manifest["tasks"] if name.startswith("S4.section-place-")]) >= 10
    assert sent_comparison_yes
    assert comparison_answers.count("yes") == 1
    assert comparison_answers and all(answer in {"yes", "no"} for answer in comparison_answers)
    assert invalidated_s7
    assert rewritten_s4
    assert final_manifest["tasks"][similar_facets[0]]["invalidations"] == 0
    assert final_manifest["tasks"][similar_facets[1]]["invalidations"] == 1
    diversity_ids = {
        name for name, record in final_manifest["tasks"].items()
        if record["type"] == "S4.diversity"
    }
    assert diversity_ids
    for name in ("S6.expand", "S9.assemble"):
        assert diversity_ids <= set(final_manifest["tasks"][name]["deps"])
    for diversity_id in diversity_ids:
        record = final_manifest["tasks"][diversity_id]
        section_id = record["index"][0]
        facet_ids = {
            name for name in final_manifest["tasks"]
            if name.startswith(f"S4.section-{section_id}-")
        }
        assert set(record["deps"]) == {"S3.assign", *facet_ids}
        assert record["state"] == "done"
    assert any(
        history["to"] == "blocked"
        for history in final_manifest["tasks"]["S4.diversity-place"]["history"]
    )
    assert not any("書き出しの類似が無効化上限" in warning for warning in final_manifest["warnings"])
    assert set(trim_counts) == {
        "S4.section", "S4.item", "S5.profile", "S5.appearance", "S5.personality", "S5.values", "S5.backstory",
        "S5.relationship", "S5.voice", "S5.inner_conflict", "S7.detail",
    }
    assert trim_counts["S4.section"] == 2
    assert extended_types == set(trim_counts)
    for task_id in extended_tasks:
        assert final_manifest["tasks"][task_id]["tries"] == 0
        assert final_manifest["tasks"][task_id]["continuation_step"] == 1
    assert not continuation_outputs
    for task_id in trimmed_tasks:
        assert final_manifest["tasks"][task_id]["tries"] == 0
        assert f"{task_id}: 字数の上限で切り詰め" in final_manifest["warnings"]

    run_dir = data_dir / "runs" / run_id
    story_path = run_dir / "story" / "story.json"
    markdown_path = run_dir / "story" / "story.md"
    assert story_path.is_file()
    assert markdown_path.is_file()
    story = json.loads(story_path.read_text(encoding="utf-8"))
    assert story["cast"][0]["sources"]
    for section in story["world"]["sections"]:
        assert section["sources"]
        for item in section["items"]:
            assert item["sources"]
    validate_document(story, ROOT / "schemas" / "story.schema.json")
    story_markdown = markdown_path.read_text(encoding="utf-8")
    assert "人物は場面の状況を確認し、行動の結果を受け止めた。" in story_markdown
    assert story["meta"]["volume"]["story"]["chars"] >= 100_000
    assert story["meta"]["volume"]["world"]["chars"] >= 100_000

    outputs: dict[str, Any] = {}
    for task_id, task in final_manifest["tasks"].items():
        if task["state"] != "done":
            continue
        output_path = run_dir / "tasks" / task_id / "output.json"
        if output_path.is_file():
            outputs[task_id] = json.loads(output_path.read_text(encoding="utf-8"))
    input_data = json.loads((run_dir / "input.json").read_text(encoding="utf-8"))
    _validate_references(story, input_data, outputs)
