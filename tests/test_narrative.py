from __future__ import annotations

import json
from pathlib import Path

import pytest

from storyteller.cli import main
from storyteller.input_data import InputError
from storyteller.narrative import NARRATIVE_ITEMS, create_narrative_run, read_narrative

ROOT = Path(__file__).parents[1]


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def _narrative(filled: int = 3) -> dict[str, str]:
    return {key: ("傾向の記述" if index < filled else "") for index, key in enumerate(NARRATIVE_ITEMS)}


def test_valid_narrative_keeps_empty_items_empty(tmp_path: Path) -> None:
    result = read_narrative(_write(tmp_path / "n.json", _narrative(3)))
    assert list(result.items) == list(NARRATIVE_ITEMS)
    assert [key for key, text in result.items.items() if not text] == list(NARRATIVE_ITEMS)[3:]
    assert [p["id"] for p in result.paragraphs] == list(NARRATIVE_ITEMS)[:3]


def test_whitespace_only_item_is_empty(tmp_path: Path) -> None:
    value = _narrative(3)
    value["desire"] = "  \n "
    result = read_narrative(_write(tmp_path / "n.json", value))
    assert result.items["desire"] == ""


def test_fewer_than_three_filled_items_rejected(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="3つ以上"):
        read_narrative(_write(tmp_path / "n.json", _narrative(2)))
    value = _narrative(2)
    value["taboo"] = "   "
    with pytest.raises(InputError, match="3つ以上"):
        read_narrative(_write(tmp_path / "n2.json", value))


def test_missing_item_rejected_without_echoing_text(tmp_path: Path) -> None:
    value = _narrative(5)
    del value["taboo"]
    with pytest.raises(InputError, match="taboo") as info:
        read_narrative(_write(tmp_path / "n.json", value))
    assert "傾向の記述" not in str(info.value)


def test_unknown_item_and_non_string_rejected(tmp_path: Path) -> None:
    value = _narrative(5)
    value["extra"] = "x"
    with pytest.raises(InputError):
        read_narrative(_write(tmp_path / "a.json", value))
    value = _narrative(5)
    value["loss"] = 3
    with pytest.raises(InputError, match="loss"):
        read_narrative(_write(tmp_path / "b.json", value))


def test_length_limit_is_300_characters(tmp_path: Path) -> None:
    value = _narrative(5)
    value["author"] = "あ" * 300
    assert read_narrative(_write(tmp_path / "ok.json", value)).items["author"] == "あ" * 300
    value["author"] = "あ" * 301
    with pytest.raises(InputError, match="author.*300字") as info:
        read_narrative(_write(tmp_path / "ng.json", value))
    assert "あああ" not in str(info.value)


@pytest.mark.parametrize("content", [b"\xff\xfe", b"{", b"[]"])
def test_unreadable_files_rejected(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "n.json"
    path.write_bytes(content)
    with pytest.raises(InputError):
        read_narrative(path)
    with pytest.raises(InputError):
        read_narrative(tmp_path / "missing.json")


def test_create_narrative_run_makes_s1_task_per_filled_item(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    run_id = create_narrative_run(
        data_dir, _write(tmp_path / "n.json", _narrative(3)),
        preset="vignette", axis_overrides={}, seed=1, parts=None,
        plot_type=None, repository_root=ROOT,
    )
    run_dir = data_dir / "runs" / run_id
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    document = json.loads((run_dir / "input.json").read_text(encoding="utf-8"))
    assert document["kind"] == "narrative"
    assert manifest["input"]["type"] == "narrative"
    keys = list(NARRATIVE_ITEMS)[:3]
    assert set(manifest["tasks"]) == {f"S1.extract-{k}-{n}" for k in keys for n in range(1, 6)} | {"S2.plan"}


def test_cli_new_narrative(tmp_path: Path, monkeypatch, capsys) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("STORYTELLER_HOME", str(data_dir))
    good = _write(tmp_path / "good.json", _narrative(4))
    assert main(["new", "--narrative", str(good), "--scale", "vignette", "--seed", "7"]) == 0
    run_id = capsys.readouterr().out.strip()
    assert (data_dir / "runs" / run_id / "input.json").is_file()
    bad = _write(tmp_path / "bad.json", _narrative(1))
    assert main(["new", "--narrative", str(bad), "--scale", "vignette"]) == 1
