from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from storyteller.adapter import (
    AdapterError,
    AdapterResponse,
    AutoRunner,
    ModelConfig,
    OllamaAdapter,
    load_model_config,
)
from storyteller.dev.dummy import create_dummy_orchestrator


class _OllamaHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []
    response: dict[str, Any] = {"message": {"content": "ok"}, "done_reason": "stop"}

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        length = int(self.headers["Content-Length"])
        self.__class__.requests.append(json.loads(self.rfile.read(length)))
        body = json.dumps(self.__class__.response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture
def ollama_server():
    _OllamaHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _model(endpoint: str, *, json_mode: str = "schema") -> ModelConfig:
    return ModelConfig(
        name="test-model",
        provider="ollama",
        endpoint=endpoint,
        temperature=0.2,
        max_tokens=123,
        context_length=456,
        json_mode=json_mode,
    )


def test_ollama_payload_contains_schema_and_options(ollama_server: str) -> None:
    adapter = OllamaAdapter(_model(ollama_server))
    response = adapter.complete(
        "次のJSONだけを出力すること。",
        schema={"type": "object"},
    )

    assert response == AdapterResponse("ok", "stop")
    payload = _OllamaHandler.requests[0]
    assert payload["model"] == "test-model"
    assert payload["messages"] == [{"role": "user", "content": "次のJSONだけを出力すること。"}]
    assert payload["stream"] is False
    assert payload["format"] == {"type": "object"}
    assert payload["options"] == {
        "temperature": 0.2,
        "num_predict": 123,
        "num_ctx": 456,
    }


def test_non_local_endpoint_is_rejected() -> None:
    with pytest.raises(AdapterError):
        OllamaAdapter(_model("https://example.test:11434"))


def test_model_config_reads_repository_defaults() -> None:
    config = load_model_config("gpt-oss:20b", provider="ollama")
    assert config.endpoint == "http://127.0.0.1:11434"
    assert config.json_mode == "schema"


def test_auto_downgrades_json_mode_and_persists_warning(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    orchestrator = create_dummy_orchestrator(data_dir)
    run_id = orchestrator.create_run(
        task_specs=[{"task_id": "D1.items", "type": "D1.items"}],
        seed=1,
    )
    responses = iter(
        [
            AdapterResponse("", "stop"),
            AdapterResponse("", "stop"),
            AdapterResponse('{"text":"ok","sources":["d1"]}', "stop"),
            AdapterResponse('{"text":"ok","sources":["d2"]}', "stop"),
            AdapterResponse('{"text":"ok","sources":["d3"]}', "stop"),
            AdapterResponse("確認文をまとめた本文です。", "stop"),
        ]
    )

    class FakeAdapter:
        def __init__(self, model: ModelConfig) -> None:
            pass

        def complete(self, *args: Any, **kwargs: Any) -> AdapterResponse:
            return next(responses)

    runner = AutoRunner(
        data_dir,
        _model("http://127.0.0.1:11434"),
        orchestrator,
        adapter_factory=FakeAdapter,
    )
    runner.run(run_id=run_id, workers=1, until_empty=True)

    state = json.loads(
        (data_dir / "adapters" / "state.json").read_text(encoding="utf-8")
    )
    assert state["models"]["test-model"] == {"json_mode": "json", "empty_streak": 0}
    manifest = orchestrator.load_run(run_id)
    assert any("json_mode を json に切り替えました" in warning for warning in manifest["warnings"])
