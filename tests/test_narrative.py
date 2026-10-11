from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from storyteller.input_data import InputError
from storyteller.narrative import NARRATIVE_ITEMS, read_narrative_input
from storyteller.new_run import create_narrative_run

ROOT = Path(__file__).parents[1]
KEYS = [key for key, _, _ in NARRATIVE_ITEMS]


def _write(tmp_path: Path, filled: dict[str, str] | None = None, **extra: object) -> Path:
    value: dict[str, object] = {key: "" for key in KEYS}
    value.update(filled or {"author": "慎重だが好奇心が強い", "missing": "安心", "desire": "静けさ"})
    value.update(extra)
    path = tmp_path / "narrative.json"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def test_thirteen_items_match_the_schema_keys() -> None:
    schema = json.loads((ROOT / "schemas" / "narrative.schema.json").read_text(encoding="utf-8"))
    assert KEYS == schema["required"] == list(schema["properties"])
    assert len(KEYS) == 13


def test_valid_narrative_keeps_empty_items_as_empty_strings(tmp_path: Path) -> None:
    narrative = read_narrative_input(_write(tmp_path))

    assert narrative.items["author"] == "慎重だが好奇心が強い"
    assert narrative.items["taboo"] == ""
    assert narrative.nonempty_keys == ("author", "missing", "desire")
    document = narrative.as_document()
    assert [p["id"] for p in document["paragraphs"]] == ["author", "missing", "desire"]
    assert "taboo" not in json.dumps(document["paragraphs"], ensure_ascii=False)


def test_whitespace_only_item_counts_as_empty(tmp_path: Path) -> None:
    path = _write(tmp_path, {"author": "a", "missing": "b", "desire": "  \n "})
    with pytest.raises(InputError, match="空でない項目が3つ以上"):
        read_narrative_input(path)


def test_fewer_than_three_nonempty_items_is_rejected_and_three_is_accepted(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="現在 2"):
        read_narrative_input(_write(tmp_path, {"author": "a", "missing": "b"}))
    assert read_narrative_input(_write(tmp_path, {"author": "a", "missing": "b", "loss": "c"}))


def test_missing_item_is_rejected_by_key(tmp_path: Path) -> None:
    path = _write(tmp_path)
    value = json.loads(path.read_text(encoding="utf-8"))
    del value["taboo"]
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(InputError, match="項目 taboo がありません"):
        read_narrative_input(path)


def test_unknown_item_and_non_string_item_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="未知の項目.*extra"):
        read_narrative_input(_write(tmp_path, extra="x"))
    with pytest.raises(InputError, match="項目 daily は文字列"):
        read_narrative_input(_write(tmp_path, daily=1))


def test_300_chars_is_accepted_and_301_is_rejected_without_echoing_text(tmp_path: Path) -> None:
    assert read_narrative_input(_write(tmp_path, {"author": "あ" * 300, "missing": "b", "loss": "c"}))
    with pytest.raises(InputError) as caught:
        read_narrative_input(_write(tmp_path, {"author": "あ" * 301, "missing": "b", "loss": "c"}))
    assert "項目 author が300字を超えています" in str(caught.value)
    assert "あああ" not in str(caught.value)


@pytest.mark.parametrize("content", [b"\xff\xfe", b"{", b"[]"])
def test_bad_encoding_or_json_is_rejected(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "n.json"
    path.write_bytes(content)
    with pytest.raises(InputError):
        read_narrative_input(path)


def test_create_narrative_run_makes_five_sequential_s1_tasks_per_nonempty_item(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    run_id = create_narrative_run(
        data_dir, _write(tmp_path), preset="vignette", axis_overrides={}, seed=7,
        parts=None, plot_type=None, repository_root=ROOT,
    )
    run_dir = data_dir / "runs" / run_id
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    input_value = json.loads((run_dir / "input.json").read_text(encoding="utf-8"))

    assert input_value["kind"] == "narrative"
    assert manifest["input"] == {
        "kind": "narrative", "source_sha256": input_value["source_sha256"], "plot_type": None,
    }
    s1 = {f"S1.extract-{k}-{n}" for k in ("author", "missing", "desire") for n in range(1, 6)}
    assert set(manifest["tasks"]) == s1 | {"S2.plan"}
    assert manifest["tasks"]["S1.extract-author-1"]["state"] == "ready"
    assert manifest["tasks"]["S1.extract-author-2"]["deps"] == ["S1.extract-author-1"]


def test_st_new_narrative_prints_run_id_and_rejects_sparse_narrative(tmp_path: Path) -> None:
    env = {**os.environ, "STORYTELLER_HOME": str(tmp_path / "home"), "PYTHONUTF8": "1"}

    def run(path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "storyteller.cli", "new", "--narrative", str(path), "--scale", "vignette"],
            env=env, capture_output=True, text=True, encoding="utf-8", cwd=tmp_path,
        )

    ok = run(_write(tmp_path))
    assert ok.returncode == 0, ok.stderr
    assert (tmp_path / "home" / "runs" / ok.stdout.strip() / "input.json").is_file()

    bad = run(_write(tmp_path, {"author": "a"}))
    assert bad.returncode == 1
    assert "空でない項目が3つ以上" in bad.stderr


def test_narrative_run_reaches_s2_plan_with_distinct_sequential_material_ids(tmp_path: Path) -> None:
    from storyteller.new_run import create_story_orchestrator

    keys = ("author", "taboo", "desire")  # non-empty items; item order, not position among 13
    path = _write(tmp_path, {
        "author": "慎重で好奇心が強い", "taboo": "他人を欺くこと", "desire": "誰にも邪魔されない静けさ",
    })
    data_dir = tmp_path / "data"
    run_id = create_narrative_run(
        data_dir, path, preset="vignette", axis_overrides={}, seed=7,
        parts=None, plot_type=None, repository_root=ROOT,
    )
    harness = create_story_orchestrator(data_dir)
    words = ["朝焼け", "古い灯台", "遠い汽笛", "冷たい水面", "乾いた砂丘"]
    for item, key in enumerate(keys):
        for number in range(1, 6):
            task_id = f"S1.extract-{key}-{number}"
            claim = harness.claim_task(run_id, task_id, executor_id="dummy")
            text = f"{words[number - 1]}にまつわる{'ABC'[item]}の感覚{number}"
            assert harness.submit(claim["ticket"], json.dumps(
                {"text": text, "kind": "image"}, ensure_ascii=False)).accepted
            task_dir = harness.task_dir(run_id, task_id)
            output = json.loads((task_dir / "output.json").read_text(encoding="utf-8"))
            assert output["id"] == f"m{item * 5 + number:03d}"
            inputs = json.loads((task_dir / "input.json").read_text(encoding="utf-8"))
            assert len(inputs["previous_materials"]) == number - 1
            assert inputs["paragraph"]["id"] == key
    manifest = harness.advance(run_id)
    assert manifest["tasks"]["S2.plan"]["state"] == "done"
    ids = []
    for key in keys:
        for number in range(1, 6):
            out = json.loads((harness.task_dir(run_id, f"S1.extract-{key}-{number}") / "output.json").read_text(encoding="utf-8"))
            ids.append(out["id"])
    assert ids == [f"m{n:03d}" for n in range(1, 16)]
