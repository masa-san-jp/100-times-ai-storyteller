"""P1-37: failures do not stop a run (task-model §6.7, ADR-0009)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from storyteller.cli import main
from storyteller.orchestrator import CodeTaskResult, Orchestrator

ROOT = Path(__file__).parents[1]
MATERIAL = {"material": {"id": "m001", "text": "静かな町で水路を守る人々の話。"}}
CHOICES = [{"id": "x1", "name": "赤"}, {"id": "x2", "name": "青"}]


def slot(label: str, select: str) -> dict[str, Any]:
    return {"label": label, "from": "input", "select": select, "required": True}


def llm(task_id: str, element: str, checks: list[Any], role: str = "素材から書く。", **extra: Any) -> dict[str, Any]:
    return {
        "id": task_id, "version": 1, "kind": "llm", "element": element, "output": "text",
        "continuation": False, "inputs": {"material": slot("素材", "input.material")},
        "card": {"role": role, "steps": ["書く。"]},
        "validate": {"checks": checks}, **extra,
    }


def definitions() -> dict[str, dict[str, Any]]:
    choice = llm("D2.pick", "choice", [], "選ぶ。", choice_max=1)
    choice["inputs"]["choices"] = slot("選択肢", "input.choices")
    follow = llm("D4.follow", "text", [])
    follow["inputs"] = {"noun": {"label": "固有名詞の案", "from": "D3.noun", "select": "output",
                                 "required": True},
                        # A glossary slot takes the separate glossary path of input building.
                        "glossary": {"label": "関係する用語集", "from": "S3.assign",
                                     "select": "glossary", "required": False}}
    return {
        "D1.short": llm("D1.short", "text", [{"max_chars": {"n": 12}}, "ends_complete"], "短く書く。"),
        "D2.pick": choice,
        "D3.noun": llm("D3.noun", "text", [{"no_new_proper_nouns": {"mode": "fail"}}], "名前を考える。"),
        "D4.follow": follow,
        "D5.assemble": {"id": "D5.assemble", "version": 1, "kind": "code", "handler": "assemble"},
    }


def assemble(context) -> CodeTaskResult:
    parts = context.dependency_outputs
    return CodeTaskResult(output={"parts": {key: parts.get(key, "（未定）") for key in
                                            ("D1.short", "D2.pick", "D3.noun", "D4.follow")}})


def new_run(tmp_path: Path, **kwargs: Any) -> tuple[Orchestrator, str]:
    harness = Orchestrator(tmp_path, definitions(), {"assemble": assemble})
    run_id = harness.create_run(
        seed=7, input_data={**MATERIAL, "choices": CHOICES},
        tasks={
            "D1.short": {"type": "D1.short"}, "D2.pick": {"type": "D2.pick"},
            "D3.noun": {"type": "D3.noun"},
            "D4.follow": {"type": "D4.follow", "deps": ["D3.noun"]},
            "D5.assemble": {"type": "D5.assemble",
                            "deps": ["D1.short", "D2.pick", "D3.noun", "D4.follow"]},
        },
        **kwargs,
    )
    return harness, run_id


def drive(harness: Orchestrator, run_id: str, respond, executor_id: str = "exec") -> list[str]:
    cards = []
    for _ in range(100):
        claim = harness.claim_next(run_id, executor_id=executor_id)
        if claim is None:
            break
        cards.append(claim["card"])
        harness.submit(claim["ticket"], respond(claim["card"]))
    return cards


REPAIR = "前回の出力を、元の内容を保ったまま"
LONG = "水路の町を静かに守り続ける物語です。"
NOUNS = "ゼラフィナとセレスティアが水路の町を訪れる。"


def respond(card: str) -> str:
    repair = REPAIR in card
    if "固有名詞の案" in card:
        return "未定でも続ける。"
    if "選ぶ。" in card or "### 選択肢" in card:
        return "x1" if repair else "選ぶのは「赤」あるいは青です"
    if repair:
        return "水路を守る。"
    return LONG if "短く書く。" in card else NOUNS


def test_run_completes_despite_overlong_choice_wobble_and_proper_noun_responses(tmp_path):
    harness, run_id = new_run(tmp_path)
    cards = drive(harness, run_id, respond)
    manifest = harness.load_run(run_id)
    assert manifest["status"] == "completed", manifest["tasks"]
    tasks = manifest["tasks"]
    assert tasks["D1.short"]["state"] == "done" and tasks["D1.short"]["tries"] == 1
    assert tasks["D2.pick"]["state"] == "done" and tasks["D2.pick"]["tries"] == 1
    assert tasks["D3.noun"]["state"] == "skipped" and tasks["D3.noun"]["tries"] == 5
    assert tasks["D4.follow"]["state"] == "done"
    assert any("D3.noun: 試行の上限に達したため省略" in w for w in manifest["warnings"])
    follow_card = next(card for card in cards if "固有名詞の案" in card)
    assert "### 固有名詞の案\n未定" in follow_card
    assert (harness.task_dir(run_id, "D3.noun") / "failure.md").is_file()
    output = json.loads((harness.task_dir(run_id, "D5.assemble") / "output.json").read_text(encoding="utf-8"))
    assert output["parts"]["D3.noun"] == "（未定）"
    assert output["parts"]["D2.pick"] == "x1"
    repair_cards = [card for card in cards if REPAIR in card]
    assert len(repair_cards) == 2
    for card in repair_cards:
        assert "### 元の出力" in card and "### 不合格の理由" in card and "### 満たすべき条件" in card
        assert "静かな町で水路を守る人々" not in card  # no whole picture (P1)
    assert any(LONG in card and "12字以内" in card for card in repair_cards)


def test_status_reports_skipped_tasks(tmp_path, monkeypatch, capsys):
    harness, run_id = new_run(tmp_path)
    drive(harness, run_id, respond)
    monkeypatch.setattr("storyteller.cli._data_dir", lambda: tmp_path)
    capsys.readouterr()
    assert main(["status", "--run", run_id, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    jsonschema.validate(payload, json.loads((ROOT / "schemas/status.schema.json").read_text(encoding="utf-8")))
    skipped = [task for task in payload["tasks"] if task["state"] == "skipped"]
    assert skipped and all(task["failure_report"] and task["error"] for task in skipped)
    assert main(["status", "--run", run_id]) == 0
    assert "skipped\t" in capsys.readouterr().out


def test_escalation_hands_exhausted_task_to_next_executor(tmp_path):
    definition = llm("D1.value", "integer", [], max_attempts=2)
    harness = Orchestrator(tmp_path, {"D1.value": definition})
    run_id = harness.create_run(seed=1, input_data=MATERIAL, escalation=["small", "large"])
    schema = json.loads((ROOT / "schemas/manifest.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(harness.load_run(run_id), schema)

    for _ in range(2):
        claim = harness.claim_next(run_id, executor_id="small")
        assert not harness.submit(claim["ticket"], "ほぼ四十").accepted
    task = harness.load_run(run_id)["tasks"]["D1.value"]
    assert task["state"] == "ready" and task["tries"] == 0 and task["executor"] == "large"
    assert harness.claim_next(run_id, executor_id="small") is None
    claim = harness.claim_next(run_id, executor_id="large")
    assert claim is not None
    assert harness.submit(claim["ticket"], "40").accepted
    manifest = harness.load_run(run_id)
    assert manifest["status"] == "completed"
    assert any("格上げ" in warning for warning in manifest["warnings"])


def test_without_next_executor_the_task_is_skipped_not_failed(tmp_path):
    definition = llm("D1.value", "integer", [], max_attempts=2)
    harness = Orchestrator(tmp_path, {"D1.value": definition})
    run_id = harness.create_run(seed=1, input_data=MATERIAL, escalation=["small", "large"])
    for executor, count in (("small", 2), ("large", 2)):
        for _ in range(count):
            claim = harness.claim_next(run_id, executor_id=executor)
            assert not harness.submit(claim["ticket"], "ほぼ四十").accepted
    manifest = harness.load_run(run_id)
    assert manifest["tasks"]["D1.value"]["state"] == "skipped"
    assert manifest["status"] == "completed"


def test_explicit_fail_still_stalls_and_default_is_skip(tmp_path):
    failing = llm("D1.value", "integer", [], max_attempts=1, on_exhausted="fail")
    harness = Orchestrator(tmp_path, {"D1.value": failing})
    run_id = harness.create_run(seed=1, input_data=MATERIAL)
    claim = harness.claim_next(run_id, executor_id="x")
    harness.submit(claim["ticket"], "ほぼ四十")
    assert harness.load_run(run_id)["status"] == "stalled"
    schema = json.loads((ROOT / "schemas/task-definition.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["on_exhausted"]["default"] == "skip"


def test_attempt_records_whether_a_repair_can_clear_the_reason(tmp_path):
    checks = [{"max_chars": {"n": 5}}, {"no_new_proper_nouns": {"mode": "fail"}}]
    harness = Orchestrator(tmp_path, {"D1.value": llm("D1.value", "text", checks)})
    run_id = harness.create_run(seed=1, input_data=MATERIAL)
    claim = harness.claim_next(run_id, executor_id="e")
    harness.submit(claim["ticket"], "これは五文字をはるかに超える長い文章です。")
    claim = harness.claim_next(run_id, executor_id="e")
    assert REPAIR in claim["card"]
    harness.submit(claim["ticket"], "セレスティアが来た。")  # short enough, but a new proper noun
    attempts = harness.task_dir(run_id, "D1.value") / "attempts"
    flags = [json.loads((attempts / f"{n}.json").read_text(encoding="utf-8"))["repairable"] for n in (1, 2)]
    assert flags == [True, False]
