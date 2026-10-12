"""P4-05: batch trend analysis (`st report`, docs/spec/report.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from storyteller.cards import input_char_count
from storyteller.cli import main
from storyteller.manifest import load_manifest
from storyteller.report import (
    EVENTS_BUDGET,
    REPORT_SCHEMA,
    create_report_orchestrator,
    group_motifs,
    normalize_motif,
    start_report,
    trim_middle,
)
from storyteller.report_layout import report_dir


def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(REPORT_SCHEMA.read_text(encoding="utf-8")))


def _element(axis: str, number: int, text: str) -> dict:
    return {"id": f"{axis}:i{number:02d}", "text": text}


def _assignment(index: int, *, adversary: bool = True) -> dict:
    cast = [
        {
            "id": "c1",
            "role": "protagonist",
            "elements": {
                "want": _element("want", index, f"願望{index}を叶える"),
                "duty": _element("duty", 1, "村を守る"),
            },
        }
    ]
    if adversary:
        cast.append(
            {
                "id": "c2",
                "role": "adversary",
                "elements": {"want": _element("want", 50, "支配する"), "duty": _element("duty", 2, "関所の番人")},
            }
        )
    return {
        "r": 0.25 * index,
        "threads": [
            {"id": "t001", "kind": "main", "part": None, "plot_type": "quest", "plot_type_name": "探索",
             "events": 3, "structure": "standard"},
            {"id": "t002", "kind": "subthread", "part": None, "plot_type": "rivalry", "plot_type_name": "対立",
             "events": 3, "structure": "standard"},
        ],
        "cast": cast,
        "world": {
            "place": _element("place", 1, "港町"),
            "era": _element("era", 1, "黎明"),
            "object": _element("object", 1, "古い地図"),
        },
    }


def _story(count: int, *, width: int = 20) -> dict:
    events = []
    for number in range(1, count + 1):
        events.append(
            {
                "id": f"e{number:03d}",
                "thread": "t001" if number % 5 else "t002",
                "what": f"出来事{number}" + "あ" * width,
                "result": f"結果{number}" + "い" * width,
            }
        )
    return {"threads": [{"id": "t001", "kind": "main"}, {"id": "t002", "kind": "subthread"}], "events": events}


def _make_batch(
    data_dir: Path, monkeypatch, capsys, count: int = 3, *, events: int = 12, width: int = 20,
    batch_id: str = "b1",
) -> list[str]:
    monkeypatch.setenv("STORYTELLER_HOME", str(data_dir))
    run_ids = []
    for number in range(1, count + 1):
        seed = (number + (0 if batch_id == "b1" else 100)) << 20
        assert main(["dev", "new-dummy", "--seed", str(seed)]) == 0
        run_id = capsys.readouterr().out.strip()
        run_dir = data_dir / "runs" / run_id
        path = run_dir / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest.update({"batch_id": batch_id, "status": "completed"})
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        (run_dir / "tasks" / "S3.assign").mkdir(parents=True, exist_ok=True)
        (run_dir / "tasks" / "S3.assign" / "output.json").write_text(
            json.dumps(_assignment(number, adversary=number != 2), ensure_ascii=False), encoding="utf-8"
        )
        (run_dir / "story").mkdir(exist_ok=True)
        (run_dir / "story" / "story.json").write_text(
            json.dumps(_story(events, width=width), ensure_ascii=False), encoding="utf-8"
        )
        run_ids.append(run_id)
    return sorted(run_ids)


MOTIFS = ["古い指輪", "指輪の記憶", "雨の港", "雨の港町", "折れた櫂"]


def _answer(card: str, state: dict) -> str:
    if "失うもの" in card:
        return "約束を果たす機会"
    if "どのような結末" in card:
        return "旅の終わりに故郷へ帰る"
    if "象徴的なモチーフ" in card:
        number = state["motif"] = state.get("motif", 0) + 1
        return MOTIFS[number % len(MOTIFS)]
    if "傾向を1文で述べる" in card:
        return "喪失と帰還に結びつく傾向が見られる"
    if "問いを1文で書く" in card:
        return "最初の入力にある喪失の感覚と重なるでしょうか。"
    raise AssertionError(card)


def _drive(data_dir: Path, tmp_path: Path, capsys) -> list[str]:
    """Act as an executor with only `st next` and `st submit`; return the cards."""
    cards: list[str] = []
    state: dict = {}
    for _ in range(400):
        code = main(["next", "--json"])
        out = capsys.readouterr().out
        if code == 2:
            return cards
        assert code == 0
        claim = json.loads(out)
        cards.append(claim["card"])
        answer = tmp_path / "answer.txt"
        answer.write_text(_answer(claim["card"], state), encoding="utf-8")
        assert main(["submit", claim["ticket"], str(answer)]) == 0
        capsys.readouterr()
    raise AssertionError("did not finish")


def test_report_end_to_end_output_passes_schema(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    run_ids = _make_batch(data, monkeypatch, capsys)
    # An unrelated batch and a non-completed run must not be analyzed.
    other = _make_batch(data, monkeypatch, capsys, count=1, batch_id="b2")

    assert main(["report", "--batch", "b1"]) == 0
    report_run_id = capsys.readouterr().out.strip()
    directory = report_dir(data, "b1")
    manifest = load_manifest(directory / "manifest.json")
    assert manifest["run_id"] == report_run_id
    assert manifest["harness_kind"] == "report"
    assert manifest["batch_id"] == "b1"
    assert (directory / "tasks" / "R1.aggregate" / "output.json").is_file()
    # 3 runs x (loss, ending, 5 motifs) + R1 + R3 are listed before the first `st next`.
    assert sum(1 for t in manifest["tasks"].values() if t["type"].startswith("R2.")) == 21

    cards = _drive(data, tmp_path, capsys)

    manifest = load_manifest(directory / "manifest.json")
    assert manifest["status"] == "completed"
    assert all(task["state"] in {"done", "skipped"} for task in manifest["tasks"].values())
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    assert list(_validator().iter_errors(report)) == []
    assert report["run_ids"] == run_ids
    assert other[0] not in report["run_ids"]
    assert [run["run_id"] for run in report["runs"]] == run_ids
    assert all(run["central_loss"] == "約束を果たす機会" for run in report["runs"])
    plot = report["aggregate"]["plot_types"]
    assert plot == [{"id": "quest", "name": "探索", "count": 3, "run_ids": run_ids}]
    ratio = report["aggregate"]["input_ratio"]
    assert (ratio["min"], ratio["max"], ratio["mean"]) == (0.25, 0.75, 0.5)
    roles = {item["role"]: item for item in report["aggregate"]["roles"]}
    assert roles["protagonist"]["count"] == 3
    assert roles["adversary"]["count"] == 2  # the second run has no adversary
    assert roles["adversary"]["run_ids"] == [run_ids[0], run_ids[2]]
    elements = {item["id"]: item for item in report["aggregate"]["elements"]}
    assert elements["place:i01"]["count"] == 3
    # Desire and adversary role come from the assignment (R2 [C]).
    assert report["runs"][0]["desire"] == "願望1を叶える"
    assert report["runs"][0]["adversary_role"] == "関所の番人"
    assert report["runs"][1]["adversary_role"] is None
    # Motif groups cite the runs they came from, and carry the LLM's sentences.
    assert 1 <= len(report["motif_groups"]) <= 7
    for group in report["motif_groups"]:
        assert group["observation"] and group["question"]
        assert set(group["run_ids"]) <= set(run_ids)
    markdown = (directory / "report.md").read_text(encoding="utf-8")
    assert markdown.index("## 集計") < markdown.index("## モチーフ群と問い") < markdown.index("## run ごとの要約")
    for run_id in run_ids:
        assert run_id in markdown
    # Each motif card shows the motifs given before it (R2).
    heading = "### すでに挙げたモチーフ（重ねない）\n"
    motif_cards = [card for card in cards if "象徴的なモチーフ" in card]
    later = [card for card in motif_cards if heading in card]
    assert len(motif_cards) == 15 and len(later) == 12  # the first of each run has no earlier motif
    assert all(card.split(heading)[1].startswith("- ") for card in later)
    # Cards never name the task type, the run, or the batch.
    for card in cards:
        assert "R2." not in card and "R4." not in card
        assert all(run_id not in card for run_id in run_ids)


def test_r2_cards_stay_within_the_input_budget(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    _make_batch(data, monkeypatch, capsys, count=1, events=400, width=120)
    start_report(data, "b1")
    orchestrator = create_report_orchestrator(data)
    directory = report_dir(data, "b1")
    report_run = load_manifest(directory / "manifest.json")["run_id"]

    output = json.loads((directory / "tasks" / "R1.aggregate" / "output.json").read_text(encoding="utf-8"))
    events = output["runs"][0]["events"]
    assert len(events) <= EVENTS_BUDGET
    assert "（中略）" in events
    lines = events.split("\n")
    assert lines[0].startswith("1. ") and lines[-1].startswith("320. ")  # first and last main events

    seen = set()
    for _ in range(3):  # loss, ending and the first motif are ready; later motifs wait
        claim = orchestrator.claim_next(report_run, executor_id="test")
        assert claim is not None
        card = claim["card"]
        task = next(
            task for task in load_manifest(directory / "manifest.json")["tasks"].values()
            if task["claim"] and task["claim"]["ticket"] == claim["ticket"]
        )
        definition = orchestrator.task_definitions[task["type"]]
        seen.add(task["type"])
        task_id = f"{task['type']}-" + "-".join(task["index"])
        inputs = json.loads((directory / "tasks" / task_id / "input.json").read_text(encoding="utf-8"))
        assert input_char_count(definition, inputs) <= definition["max_input_chars"]
        body = card.split("## 入力\n", 1)[1].split("\n## 手順", 1)[0]
        assert len(body) <= definition["max_input_chars"]
        assert "（中略）" in body
    assert seen == {"R2.loss", "R2.ending", "R2.motif"}


def test_trim_middle_keeps_head_and_tail_and_respects_budget():
    lines = [f"{n}. " + "あ" * 30 for n in range(1, 101)]
    text = trim_middle(lines, 500)
    assert len(text) <= 500
    parts = text.split("\n")
    assert parts[0] == lines[0] and parts[-1] == lines[-1]
    assert "（中略）" in parts
    assert trim_middle(lines[:3], 500) == "\n".join(lines[:3])
    assert len(trim_middle(["あ" * 1000], 100)) <= 100


def test_group_motifs_by_trigram_similarity_ranks_by_run_count():
    pairs = [
        ("r1", "古い指輪"), ("r2", "古い指輪の記憶"), ("r3", "指輪"),
        ("r1", "雨の港"), ("r2", "雨の港町"),
        ("r3", "折れた櫂"), ("r3", "折れた櫂"),
    ]
    groups = group_motifs(pairs)
    assert [(g["term"], g["labels"]) for g in groups] == [
        ("古い指輪", ["r1", "r2"]),  # shares 3-grams with 古い指輪の記憶 (a chain of similar motifs)
        ("雨の港", ["r1", "r2"]),
        ("折れた櫂", ["r3"]),  # the same motif twice in one run is still one run
        ("指輪", ["r3"]),  # shorter than 3 characters: no shared 3-gram
    ]
    assert groups[0]["variants"] == ["古い指輪の記憶"]
    assert len(group_motifs([(f"r{n}", f"独立した語{n}{n}{n}{n}") for n in range(20)], limit=7)) == 7
    assert normalize_motif(" 雨の、港！ ") == "雨の港"


def test_report_errors(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    monkeypatch.setenv("STORYTELLER_HOME", str(data))
    data.mkdir()
    assert main(["report", "--batch", "nope"]) == 1
    assert main(["report", "--batch", "../x"]) == 1
    capsys.readouterr()
    _make_batch(data, monkeypatch, capsys, count=1)
    # A batch without completed runs cannot be analyzed.
    path = next((data / "runs").glob("*/manifest.json"))
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["status"] = "active"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert main(["report", "--batch", "b1"]) == 1
    manifest["status"] = "completed"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert main(["report", "--batch", "b1"]) == 0
    # The report is not overwritten.
    assert main(["report", "--batch", "b1"]) == 1
    assert "すでに" in capsys.readouterr().err


def test_report_schema_rejects_malformed_documents():
    validator = _validator()
    assert list(validator.iter_errors({})) != []
    assert list(validator.iter_errors({"schema_version": 1, "batch_id": "b", "run_count": 1})) != []


def test_report_tasks_are_not_mixed_into_status_runs(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    run_ids = _make_batch(data, monkeypatch, capsys, count=2)
    assert main(["report", "--batch", "b1"]) == 0
    capsys.readouterr()
    assert main(["status", "--batch", "b1", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [run["run_id"] for run in payload["runs"]] == run_ids


def test_failed_report_task_is_shown_and_retried(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    run_ids = _make_batch(data, monkeypatch, capsys, count=1)
    story = data / "runs" / run_ids[0] / "story" / "story.json"
    saved = story.read_text(encoding="utf-8")
    story.unlink()
    assert main(["report", "--batch", "b1"]) == 0
    report_run_id = capsys.readouterr().out.strip()
    directory = report_dir(data, "b1")
    assert load_manifest(directory / "manifest.json")["tasks"]["R1.aggregate"]["state"] == "failed"

    assert main(["status", "--run", report_run_id, "--json"]) == 0
    tasks = {task["task_id"]: task for task in json.loads(capsys.readouterr().out)["tasks"]}
    assert tasks["R1.aggregate"]["failure_report"] == "tasks/R1.aggregate/failure.md"

    story.write_text(saved, encoding="utf-8")
    assert main(["retry", "R1.aggregate"]) == 0
    capsys.readouterr()
    assert load_manifest(directory / "manifest.json")["tasks"]["R1.aggregate"]["state"] == "ready"
    assert main(["next", "--json"]) == 0
    capsys.readouterr()
    manifest = load_manifest(directory / "manifest.json")
    assert manifest["tasks"]["R1.aggregate"]["state"] == "done"
    assert any(task["type"] == "R2.loss" for task in manifest["tasks"].values())
