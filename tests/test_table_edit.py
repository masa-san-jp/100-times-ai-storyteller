from __future__ import annotations

import json
from pathlib import Path

import pytest

from storyteller.cli import main
from storyteller.tables import load_element_table


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("STORYTELLER_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _path(home: Path) -> Path:
    return home / "tables" / "place.json"


def test_add_appends_and_assigns_ids(home, capsys):
    assert main(["table", "add", "place", "雨の降らない港町", "--source", "memo"]) == 0
    assert capsys.readouterr().out.strip() == "place:x1"
    assert main(["table", "add", "place", "浮かぶ市場"]) == 0
    assert capsys.readouterr().out.strip() == "place:x2"
    data = json.loads(_path(home).read_text(encoding="utf-8"))
    assert data == {"items": [{"text": "雨の降らない港町", "source": "memo"}, {"text": "浮かぶ市場"}]}
    assert main(["table", "check"]) == 0


@pytest.mark.parametrize("text", ["", "   ", "　"])
def test_add_rejects_empty_text(home, text):
    assert main(["table", "add", "place", text]) == 1
    assert not _path(home).exists()


def test_add_rejects_empty_source_and_unknown_axis(home):
    assert main(["table", "add", "place", "x", "--source", ""]) == 1
    assert main(["table", "add", "nosuch", "x"]) == 1
    assert not _path(home).exists()


def test_add_rejects_duplicates(home, capsys):
    default = load_element_table("place")["items"][0]["text"]
    assert main(["table", "add", "place", default]) == 1
    assert main(["table", "add", "place", " ".join(default)]) == 1
    assert main(["table", "add", "place", "新しい場所"]) == 0
    assert main(["table", "add", "place", "新しい　場所"]) == 1
    assert len(json.loads(_path(home).read_text(encoding="utf-8"))["items"]) == 1


def test_add_refuses_invalid_supplement_and_keeps_it(home):
    (home / "tables").mkdir()
    _path(home).write_text(json.dumps({"items": [{"text": ""}]}), encoding="utf-8")
    before = _path(home).read_bytes()
    assert main(["table", "add", "place", "新しい場所"]) == 1
    assert _path(home).read_bytes() == before


def test_check_reports_invalid_rows_and_duplicates(home, capsys):
    default = load_element_table("place")["items"][0]["text"]
    (home / "tables").mkdir()
    rows = [{"text": "a"}, {"text": "a"}, {"text": default}, {"text": "b", "x": 1}, "str"]
    _path(home).write_text(json.dumps(rows), encoding="utf-8")
    assert main(["table", "check", "place"]) == 1
    err = capsys.readouterr().err
    for needle in ("place:x2", "place:x3", "place:x4", "place:x5"):
        assert needle in err
    assert main(["table", "check", "age"]) == 0


def test_check_rejects_broken_json(home):
    (home / "tables").mkdir()
    _path(home).write_text("{", encoding="utf-8")
    assert main(["table", "check", "place"]) == 1


def test_list(home, capsys):
    assert main(["table", "list"]) == 0
    out = capsys.readouterr().out
    assert "place\tdefault:" in out and "supplement: 0" in out
    default = load_element_table("place")["items"][0]["text"]
    (home / "tables").mkdir()
    _path(home).write_text(json.dumps({"items": [{"text": default}, {"text": "新規"}]}), encoding="utf-8")
    assert main(["table", "list", "place"]) == 0
    out = capsys.readouterr().out
    assert "place:x1" in out and "既定と重複" in out and "duplicates with default: 1" in out
    assert main(["table", "list", "nosuch"]) == 1
