from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from storyteller.storage import (
    DataDirectoryError,
    LockTimeoutError,
    acquire_lock,
    atomic_write_json,
    atomic_write_text,
    batch_lock_path,
    manifest_lock_path,
    resolve_data_dir,
    sync_folder_marker,
    tables_lock_path,
    warn_if_sync_folder,
)


def _workspace_config(path: Path, data_dir: Path) -> None:
    path.write_text(
        "\n".join(
            [
                f"data_dir: {data_dir}",
                "executor_id: test-executor",
                "agent: generic",
                "isolation: placement",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_resolve_data_dir_prefers_workspace_config(tmp_path: Path) -> None:
    configured = tmp_path / "configured"
    _workspace_config(tmp_path / ".storyteller-workspace.yaml", configured)

    resolved = resolve_data_dir(
        cwd=tmp_path,
        environ={"STORYTELLER_HOME": str(tmp_path / "environment")},
        package_file=tmp_path / "not" / "a" / "package.py",
    )

    assert resolved == configured.resolve()


def test_resolve_data_dir_uses_environment_then_editable_private(tmp_path: Path) -> None:
    environment_dir = tmp_path / "environment"
    assert (
        resolve_data_dir(
            cwd=tmp_path,
            environ={"STORYTELLER_HOME": str(environment_dir)},
            package_file=tmp_path / "not" / "a" / "package.py",
        )
        == environment_dir.resolve()
    )

    repository = tmp_path / "repo"
    package_file = repository / "src" / "storyteller" / "storage.py"
    (repository / "src" / "storyteller").mkdir(parents=True)
    (repository / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    assert (
        resolve_data_dir(
            cwd=tmp_path,
            environ={},
            package_file=package_file,
        )
        == (repository / "private").resolve()
    )


def test_resolve_data_dir_rejects_invalid_or_unavailable_configuration(
    tmp_path: Path,
) -> None:
    config = tmp_path / ".storyteller-workspace.yaml"
    config.write_text("data_dir: relative\n", encoding="utf-8")
    with pytest.raises(DataDirectoryError):
        resolve_data_dir(cwd=tmp_path, environ={}, package_file=tmp_path / "x.py")

    config.unlink()
    with pytest.raises(DataDirectoryError):
        resolve_data_dir(cwd=tmp_path, environ={}, package_file=tmp_path / "x.py")


def test_sync_folder_warning_is_case_insensitive_and_emitted_to_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sync_dir = tmp_path / "My Drive" / "storyteller"
    assert sync_folder_marker(sync_dir) == "My Drive"
    assert warn_if_sync_folder(sync_dir)
    assert "警告" in capsys.readouterr().err
    assert not warn_if_sync_folder(tmp_path / "local")


def test_atomic_text_write_normalises_lf_and_preserves_original_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "nested" / "state.txt"
    atomic_write_text(destination, "あ\r\nb\rc\n")
    assert destination.read_bytes() == "あ\nb\nc\n".encode("utf-8")

    destination.write_text("old", encoding="utf-8")

    def fail_replace(*args: object) -> None:
        raise OSError("injected failure")

    monkeypatch.setattr("storyteller.storage.os.replace", fail_replace)
    with pytest.raises(OSError, match="injected failure"):
        atomic_write_text(destination, "new")
    assert destination.read_text(encoding="utf-8") == "old"
    assert list(destination.parent.glob(f".{destination.name}.*.tmp")) == []


def test_atomic_json_write_is_utf8_json_with_lf(tmp_path: Path) -> None:
    destination = tmp_path / "state.json"
    atomic_write_json(destination, {"text": "日本語", "items": [1, 2]})
    assert json.loads(destination.read_text(encoding="utf-8")) == {
        "text": "日本語",
        "items": [1, 2],
    }
    assert b"\r" not in destination.read_bytes()


def test_lock_timeout_and_release(tmp_path: Path) -> None:
    lock_path = tmp_path / "manifest.lock"
    with acquire_lock(lock_path):
        assert lock_path.is_file()
        with pytest.raises(LockTimeoutError):
            with acquire_lock(lock_path, timeout=0.02, retry_interval=0.005):
                pass
    assert not lock_path.exists()


def test_stale_lock_is_removed_and_reclaimed(tmp_path: Path) -> None:
    lock_path = tmp_path / "tables.lock"
    lock_path.write_text("stale", encoding="utf-8")
    old = time.time() - 31
    os.utime(lock_path, (old, old))

    with acquire_lock(lock_path, timeout=0.1):
        assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid()

    assert not lock_path.exists()


def test_lock_paths_follow_data_layout(tmp_path: Path) -> None:
    assert manifest_lock_path(tmp_path / "run") == tmp_path / "run" / "manifest.lock"
    assert batch_lock_path(tmp_path / "batch") == tmp_path / "batch" / "batch.lock"
    assert tables_lock_path(tmp_path) == tmp_path / "tables.lock"
