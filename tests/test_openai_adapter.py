from __future__ import annotations

import io
import json
import threading
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

import storyteller.adapter as adapter_module
from storyteller.adapter import AdapterError, ModelConfig, load_model_config
from storyteller.cli import main
from storyteller.openai_adapter import OpenAICompatibleAdapter, _chat_url
from tests.support.dummy_clock import dummy_clock


class _Handler(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []
    paths: list[str] = []
    reply: dict[str, Any] | None = None  # fixed reply; otherwise answer by card
    status = 200

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        cls = self.__class__
        cls.requests.append(payload)
        cls.paths.append(self.path)
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        if cls.reply is not None:
            body = cls.reply
        else:
            card = payload["messages"][0]["content"]
            body = {"choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": _answer(card)}}]}
        data = json.dumps(body).encode("utf-8")
        self.send_response(cls.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        return


def _answer(card: str) -> str:
    if "素材になる短い語句を1個" in card:
        return json.dumps({"text": "水路に残る記憶と静かな影", "kind": "image"}, ensure_ascii=False)
    if "### 項目\n" in card:
        return "確認できた項目です。"
    return "確認文をまとめた本文です。"


@pytest.fixture
def server():
    _Handler.requests, _Handler.paths, _Handler.reply, _Handler.status = [], [], None, 200
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def _model(endpoint: str, json_mode: str = "schema") -> ModelConfig:
    return ModelConfig(name="m", provider="openai-compatible", endpoint=endpoint,
                       temperature=0.2, max_tokens=64, context_length=1024,
                       json_mode=json_mode, timeout_seconds=5)


def test_chat_url_forms() -> None:
    for base in ("http://127.0.0.1:1234", "http://127.0.0.1:1234/", "http://127.0.0.1:1234/v1",
                 "http://127.0.0.1:1234/v1/chat/completions"):
        assert _chat_url(base) == "http://127.0.0.1:1234/v1/chat/completions"


def test_models_yaml_has_openai_compatible_entry() -> None:
    model = load_model_config("openai/gpt-oss-20b", provider="openai-compatible")
    assert model.provider == "openai-compatible"


def test_rejects_non_local_endpoint_and_wrong_provider() -> None:
    with pytest.raises(AdapterError):
        OpenAICompatibleAdapter(_model("http://example.com:1234"))
    with pytest.raises(AdapterError):
        OpenAICompatibleAdapter(ModelConfig("m", "ollama", "http://127.0.0.1:1", 0.1, 8, 8, "off"))


def test_request_shape_and_response(server: str) -> None:
    schema = {"type": "object", "properties": {"t": {"type": "string", "maxLength": 3}}}
    result = OpenAICompatibleAdapter(_model(server)).complete("次のJSONだけを出力すること。", schema=schema)
    payload = _Handler.requests[0]
    assert _Handler.paths == ["/v1/chat/completions"]
    assert payload["model"] == "m" and payload["stream"] is False
    assert payload["max_tokens"] == 64 and payload["temperature"] == 0.2
    fmt = payload["response_format"]
    assert fmt["type"] == "json_schema"
    assert "maxLength" not in json.dumps(fmt["json_schema"]["schema"])
    assert result.content and result.done_reason == "stop"

    OpenAICompatibleAdapter(_model(server, "json")).complete("次のJSONだけを出力すること。")
    assert _Handler.requests[1]["response_format"] == {"type": "json_object"}
    OpenAICompatibleAdapter(_model(server, "off")).complete("次のJSONだけを出力すること。")
    assert "response_format" not in _Handler.requests[2]
    OpenAICompatibleAdapter(_model(server)).complete("自由文", schema=None)
    assert "response_format" not in _Handler.requests[3]


def test_length_finish_reason_and_null_content(server: str) -> None:
    _Handler.reply = {"choices": [{"finish_reason": "length", "message": {"content": None}}]}
    result = OpenAICompatibleAdapter(_model(server)).complete("自由文")
    assert (result.content, result.done_reason) == ("", "length")


def test_malformed_response_and_client_error(server: str) -> None:
    _Handler.reply = {"choices": []}
    with pytest.raises(AdapterError):
        OpenAICompatibleAdapter(_model(server)).complete("x")
    _Handler.reply, _Handler.status = {"error": "bad"}, 400
    with pytest.raises(AdapterError, match="400"):
        OpenAICompatibleAdapter(_model(server)).complete("x")


def test_st_auto_completes_dummy_run_against_fake_server(
    server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "models.yaml"
    config.write_text(
        "models:\n  local-model:\n    provider: openai-compatible\n"
        f"    endpoint: {server}\n    temperature: 0.2\n    max_tokens: 256\n"
        "    context_length: 2048\n    json_mode: schema\n    timeout_seconds: 10\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(adapter_module, "_DEFAULT_CONFIG_PATH", config)
    data_dir = tmp_path / "data"
    monkeypatch.setenv("STORYTELLER_HOME", str(data_dir))
    with dummy_clock():
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            assert main(["dev", "new-dummy", "--seed", "7"]) == 0
            run_id = out.getvalue().strip().splitlines()[-1]
            assert main(["auto", "--provider", "openai-compatible", "--model", "local-model",
                         "--run", run_id, "--until-empty"]) == 0
    manifest = json.loads((data_dir / "runs" / run_id / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "completed", err.getvalue()
    assert len(_Handler.requests) >= 2
    assert set(_Handler.paths) == {"/v1/chat/completions"}
