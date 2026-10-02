from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from storyteller.story_s9 import _validate_references
from storyteller.validation import validate_document


ROOT = Path(__file__).parents[1]
RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")


def _cli(data_dir: Path, *arguments: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["STORYTELLER_HOME"] = str(data_dir)
    return subprocess.run(
        [sys.executable, "-m", "storyteller.cli", *arguments],
        cwd=ROOT,
        env=environment,
        input=input_text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        timeout=30,
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


def _fake_output(
    task_id: str,
    task: dict[str, Any],
    inputs: dict[str, Any],
    *,
    send_comparison_yes: bool,
) -> dict[str, Any]:
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

    if task_type in {"S4.section", "S4.item"}:
        source = _first_id(inputs)
        if task_type == "S4.item":
            return {"name": "一覧の項目", "body": "町の暮らしを支える小さな仕組み。", "sources": [source]}
        return {"body": "町の暮らしと場所の変化が重なる。", "sources": [source]}

    if task_type.startswith("S5."):
        field = task_type.removeprefix("S5.")
        if field == "name":
            sound = inputs["name_sound"]
            reading = "".join(sound["sounds"][:2])
            return {"name": reading, "reading": reading, "sources": [sound["set_id"]]}
        person_id = inputs["character"]["id"]
        values = {
            "profile": f"{person_id}の属性をまとめたプロフィール。",
            "intro": f"{person_id}の短い紹介。",
            "appearance": f"{person_id}の外見と魅力。",
            "motive": f"{person_id}が行動する動機。",
            "catchphrase": f"私は{person_id}として進む。",
        }
        return {field: values[field], "sources": [person_id]}

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


def test_story_harness_cli_runs_to_s9_with_schema_outputs_and_regeneration(
    tmp_path: Path,
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

    sent_comparison_yes = False
    comparison_answers: list[str] = []
    invalidated_s7 = False
    for _ in range(500):
        claimed = _cli(data_dir, "next", "--run", run_id, "--json")
        if claimed.returncode == 2:
            break
        assert claimed.returncode == 0, claimed.stderr
        claim = json.loads(claimed.stdout)
        task_id, task = _claimed_task(data_dir, run_id, claim["ticket"])
        inputs = _task_input(data_dir, run_id, task_id)
        is_comparison = task["type"] == "S8.compare"
        output = _fake_output(
            task_id,
            task,
            inputs,
            send_comparison_yes=is_comparison and not sent_comparison_yes,
        )
        if is_comparison:
            comparison_answers.append(output["answer"])
            if output["answer"] == "yes":
                sent_comparison_yes = True
        submitted_output = (
            output
            if task["type"] == "S7.detail"
            else json.dumps(output, ensure_ascii=False)
        )
        submitted = _cli(
            data_dir,
            "submit",
            claim["ticket"],
            input_text=submitted_output,
        )
        assert submitted.returncode == 0, (task_id, submitted.stderr, submitted.stdout)
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
    assert sent_comparison_yes
    assert comparison_answers.count("yes") == 1
    assert comparison_answers and all(answer in {"yes", "no"} for answer in comparison_answers)
    assert invalidated_s7

    run_dir = data_dir / "runs" / run_id
    story_path = run_dir / "story" / "story.json"
    markdown_path = run_dir / "story" / "story.md"
    assert story_path.is_file()
    assert markdown_path.is_file()
    story = json.loads(story_path.read_text(encoding="utf-8"))
    validate_document(story, ROOT / "schemas" / "story.schema.json")
    story_markdown = markdown_path.read_text(encoding="utf-8")
    assert "人物は場面の状況を確認し、行動の結果を受け止めた。" in story_markdown
    assert story["meta"]["volume"]["story"]["chars"] >= 100_000

    outputs: dict[str, Any] = {}
    for task_id, task in final_manifest["tasks"].items():
        if task["state"] != "done":
            continue
        output_path = run_dir / "tasks" / task_id / "output.json"
        if output_path.is_file():
            outputs[task_id] = json.loads(output_path.read_text(encoding="utf-8"))
    input_data = json.loads((run_dir / "input.json").read_text(encoding="utf-8"))
    _validate_references(story, input_data, outputs)
