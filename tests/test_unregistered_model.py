"""#227: ``st auto`` fills in a configuration for a model not in config/models.yaml."""

from __future__ import annotations

import argparse
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from storyteller import adapter
from storyteller.adapter import AdapterError, load_model_config, resolve_model_config


class _ShowHandler(BaseHTTPRequestHandler):
    status = 200
    body: dict = {}
    requests: list = []

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length", "0"))
        type(self).requests.append((self.path, json.loads(self.rfile.read(length))))
        data = json.dumps(type(self).body).encode("utf-8")
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args) -> None:
        pass


@pytest.fixture
def show_server():
    _ShowHandler.status = 200
    _ShowHandler.body = {}
    _ShowHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ShowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _closed_port() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{sock.getsockname()[1]}"


def _recorded(data_dir: Path, model: str) -> dict:
    document = json.loads((data_dir / "adapters" / "inferred-models.json").read_text(encoding="utf-8"))
    return document["models"][model]["settings"]


def test_registered_model_is_loaded_as_before_and_nothing_is_recorded(tmp_path: Path) -> None:
    config = resolve_model_config("gpt-oss:20b", provider="ollama", data_dir=tmp_path)
    assert config == load_model_config("gpt-oss:20b", provider="ollama")
    assert not (tmp_path / "adapters" / "inferred-models.json").exists()


def test_a_registered_model_with_another_provider_still_stops(tmp_path: Path) -> None:
    with pytest.raises(AdapterError, match="一致しません"):
        resolve_model_config("gpt-oss:20b", provider="openai-compatible", data_dir=tmp_path)


@pytest.mark.parametrize(("reported", "expected"), [(131072, 32768), (8192, 8192)])
def test_unregistered_ollama_model_takes_context_length_from_api_show(
    tmp_path: Path, show_server: str, capsys, reported: int, expected: int,
) -> None:
    _ShowHandler.body = {"model_info": {"general.architecture": "gptoss", "gptoss.context_length": reported}}
    config = resolve_model_config("gpt-oss:120b", provider="ollama", endpoint=show_server, data_dir=tmp_path)
    assert _ShowHandler.requests == [("/api/show", {"model": "gpt-oss:120b"})]
    assert (config.name, config.provider, config.endpoint) == ("gpt-oss:120b", "ollama", show_server)
    assert (config.temperature, config.max_tokens, config.json_mode, config.think) == (0.7, 8192, "schema", None)
    assert config.context_length == expected
    assert config.timeout_seconds is None and config.transport_retry_budget_seconds == 1800.0
    settings = _recorded(tmp_path, "gpt-oss:120b")
    assert settings["context_length"]["value"] == expected
    assert str(reported) in settings["context_length"]["source"]
    assert settings["endpoint"] == {"value": show_server, "source": "--endpoint"}
    assert settings["temperature"] == {"value": 0.7, "source": "default"}
    err = capsys.readouterr().err
    assert "config/models.yaml に gpt-oss:120b がないため" in err
    assert f"context_length = {expected}" in err


def test_unreachable_server_falls_back_to_the_default_context_length(tmp_path: Path) -> None:
    endpoint = _closed_port()
    config = resolve_model_config("other:7b", provider="ollama", endpoint=endpoint, data_dir=tmp_path)
    assert config.context_length == 32768
    assert _recorded(tmp_path, "other:7b")["context_length"] == {"value": 32768, "source": "default"}


def test_api_show_without_context_length_falls_back_to_the_default(tmp_path: Path, show_server: str) -> None:
    _ShowHandler.body = {"model_info": {"general.architecture": "x", "other.context_length": 4096}}
    config = resolve_model_config("other:7b", provider="ollama", endpoint=show_server, data_dir=tmp_path)
    assert config.context_length == 32768


def test_a_model_the_ollama_server_does_not_have_stops_before_running(tmp_path: Path, show_server: str) -> None:
    _ShowHandler.status = 404
    _ShowHandler.body = {"error": "model 'missing' not found"}
    with pytest.raises(AdapterError, match="サーバーにモデルがありません"):
        resolve_model_config("missing", provider="ollama", endpoint=show_server, data_dir=tmp_path)
    assert not (tmp_path / "adapters" / "inferred-models.json").exists()


def test_unregistered_openai_compatible_model_uses_the_default_endpoint(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(adapter, "urlopen", lambda *_a, **_k: pytest.fail("no request"))
    config = resolve_model_config("local/model", provider="openai-compatible", data_dir=tmp_path)
    assert (config.endpoint, config.context_length) == ("http://127.0.0.1:1234", 32768)
    assert _recorded(tmp_path, "local/model")["endpoint"] == {"value": "http://127.0.0.1:1234", "source": "default"}


def test_non_local_endpoint_is_still_rejected_for_an_unregistered_model(tmp_path: Path) -> None:
    with pytest.raises(AdapterError, match="localhost"):
        resolve_model_config("other:7b", provider="ollama", endpoint="http://example.com:11434", data_dir=tmp_path)


def test_records_of_several_models_are_kept(tmp_path: Path) -> None:
    for name in ("a/one", "b/two"):
        resolve_model_config(name, provider="openai-compatible", data_dir=tmp_path)
    document = json.loads((tmp_path / "adapters" / "inferred-models.json").read_text(encoding="utf-8"))
    assert set(document["models"]) == {"a/one", "b/two"}


def test_st_auto_starts_with_an_unregistered_model(tmp_path: Path, show_server: str, monkeypatch) -> None:
    _ShowHandler.body = {"model_info": {"general.architecture": "gptoss", "gptoss.context_length": 131072}}
    started = {}

    class FakeRunner:
        def __init__(self, data_dir, model, orchestrator, **_kwargs) -> None:
            started["model"] = model

        def run(self, **_kwargs) -> None:
            started["ran"] = True

    monkeypatch.setattr(adapter, "AutoRunner", FakeRunner)
    monkeypatch.setattr(adapter, "_resolve_cli_data_dir", lambda: tmp_path)
    args = argparse.Namespace(
        provider="ollama", model="gpt-oss:120b", endpoint=show_server, workers=1, run_id=None, until_empty=True,
    )
    assert adapter.run_auto_command(args) == 0
    assert started["ran"] and started["model"].name == "gpt-oss:120b"
    assert started["model"].context_length == 32768


def test_an_unreadable_record_is_moved_aside_not_overwritten(tmp_path: Path, capsys) -> None:
    (tmp_path / "adapters").mkdir()
    (tmp_path / "adapters" / "inferred-models.json").write_text("{broken", encoding="utf-8")
    resolve_model_config("local/model", provider="openai-compatible", data_dir=tmp_path)
    aside = list((tmp_path / "adapters").glob("inferred-models.json.broken-*"))
    assert len(aside) == 1 and aside[0].read_text(encoding="utf-8") == "{broken"
    assert set(json.loads((tmp_path / "adapters" / "inferred-models.json").read_text(encoding="utf-8"))["models"]) == {
        "local/model"}
    assert "に移しました" in capsys.readouterr().err


def test_a_record_that_cannot_be_written_stops_with_an_adapter_error(tmp_path: Path, monkeypatch) -> None:
    def refuse(*_args, **_kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(adapter, "atomic_write_json", refuse)
    with pytest.raises(AdapterError, match="補った設定を記録できません"):
        resolve_model_config("local/model", provider="openai-compatible", data_dir=tmp_path)
