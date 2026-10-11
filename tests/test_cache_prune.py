from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from storyteller.cache import cache_path, save_cache
from storyteller.cli import main

DAY = 86400


def _key(n: int) -> str:
    return f"{n:02x}" + "a" * 62


def _entry(data_dir: Path, n: int, age_days: float) -> Path:
    path = save_cache(data_dir, _key(n), {"n": n})
    old = time.time() - age_days * DAY
    os.utime(path, (old, old))
    return path


def _new_run(data_dir: Path, monkeypatch, capsys) -> str:
    monkeypatch.setenv("STORYTELLER_HOME", str(data_dir))
    assert main(["dev", "new-dummy"]) == 0
    return capsys.readouterr().out.strip()


def _set_run(data_dir: Path, run_id: str, status: str, key: str | None) -> None:
    path = data_dir / "runs" / run_id / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["status"] = status
    first = next(iter(manifest["tasks"].values()))
    first["cache_key"] = key
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def test_prune_deletes_only_old_unreferenced_entries(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    run_id = _new_run(data, monkeypatch, capsys)
    old_free = _entry(data, 1, 40)
    old_ref = _entry(data, 2, 40)
    recent = _entry(data, 3, 1)
    _set_run(data, run_id, "active", _key(2))

    assert main(["cache", "prune", "--older-than", "30"]) == 0
    assert "deleted 1" in capsys.readouterr().out
    assert not old_free.exists()
    assert not old_free.parent.exists()
    assert old_ref.exists()
    assert recent.exists()


@pytest.mark.parametrize("status", ["active", "stalled"])
def test_referenced_by_live_run_is_kept_even_at_zero_days(
    tmp_path, monkeypatch, capsys, status
):
    data = tmp_path / "data"
    run_id = _new_run(data, monkeypatch, capsys)
    entry = _entry(data, 4, 400)
    _set_run(data, run_id, status, _key(4))
    assert main(["cache", "prune", "--older-than", "0"]) == 0
    assert entry.exists()


@pytest.mark.parametrize("status", ["completed", "halted", "duplicate"])
def test_reference_from_non_live_run_does_not_protect(
    tmp_path, monkeypatch, capsys, status
):
    data = tmp_path / "data"
    run_id = _new_run(data, monkeypatch, capsys)
    entry = _entry(data, 5, 400)
    _set_run(data, run_id, status, _key(5))
    assert main(["cache", "prune", "--older-than", "30"]) == 0
    assert not entry.exists()


def test_unreadable_manifest_aborts_without_deleting(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    run_id = _new_run(data, monkeypatch, capsys)
    entry = _entry(data, 6, 400)
    (data / "runs" / run_id / "manifest.json").write_text("{broken", encoding="utf-8")
    assert main(["cache", "prune", "--older-than", "30"]) == 10
    assert entry.exists()


def test_report_manifest_of_live_batch_protects_entries(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    monkeypatch.setenv("STORYTELLER_HOME", str(data))
    entry = _entry(data, 7, 400)
    report = data / "batches" / "b1" / "report"
    report.mkdir(parents=True)
    (report / "manifest.json").write_text(
        json.dumps({"status": "active", "tasks": {"R1": {"cache_key": _key(7)}}}),
        encoding="utf-8",
    )
    assert main(["cache", "prune", "--older-than", "30"]) == 0
    assert entry.exists()


def test_no_cache_directory_and_bad_days(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STORYTELLER_HOME", str(tmp_path / "data"))
    assert main(["cache", "prune", "--older-than", "1"]) == 0
    assert main(["cache", "prune", "--older-than", "-1"]) == 1
    assert main(["cache", "prune"]) == 1


def test_unrelated_files_in_cache_are_ignored(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    monkeypatch.setenv("STORYTELLER_HOME", str(data))
    stray = data / "cache" / "zz" / "note.json"
    stray.parent.mkdir(parents=True)
    stray.write_text("{}", encoding="utf-8")
    old = time.time() - 999 * DAY
    os.utime(stray, (old, old))
    assert cache_path(data, _key(8)).parent != stray.parent
    assert main(["cache", "prune", "--older-than", "1"]) == 0
    assert stray.exists()
