from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from storyteller.adapter import (
    AdapterError,
    AdapterRequestError,
    AdapterResponse,
    AutoRunner,
    ModelConfig,
    OllamaAdapter,
    load_model_config,
)
from storyteller.dev.dummy import create_dummy_orchestrator
from storyteller.new_run import create_story_orchestrator
from storyteller.orchestrator import TaskSpec
from storyteller.validation import load_yaml, validate_document, validate_output


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
    assert "think" not in payload
    assert payload["options"] == {
        "temperature": 0.2,
        "num_predict": 123,
        "num_ctx": 456,
    }


def _collect_keys(value: Any, keys: set[str]) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys:
                found.add(key)
            found |= _collect_keys(item, keys)
    elif isinstance(value, list):
        for item in value:
            found |= _collect_keys(item, keys)
    return found


def test_ollama_payload_strips_length_constraints_including_nested(
    ollama_server: str,
) -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["intro", "sources"],
        "properties": {
            "intro": {"type": "string", "minLength": 1, "maxLength": 50},
            "sources": {"$ref": "#/$defs/sources"},
            "variant": {
                "anyOf": [
                    {"type": "string", "minLength": 2, "maxLength": 10},
                    {"type": "null"},
                ]
            },
        },
        "$defs": {
            "sources": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "minLength": 1, "maxLength": 20},
            }
        },
    }

    adapter = OllamaAdapter(_model(ollama_server))
    adapter.complete("次のJSONだけを出力すること。", schema=schema)

    sent_format = _OllamaHandler.requests[-1]["format"]
    assert _collect_keys(sent_format, {"minLength", "maxLength"}) == set()
    # Other constraints survive the copy.
    assert sent_format["$defs"]["sources"]["minItems"] == 1
    assert sent_format["properties"]["intro"]["type"] == "string"

    # The caller's schema object (the one used for validating the
    # submission) must be left untouched.
    assert schema["properties"]["intro"]["maxLength"] == 50
    assert schema["$defs"]["sources"]["items"]["maxLength"] == 20


def test_submission_validation_enforces_max_length_with_task_checks() -> None:
    root = Path(__file__).resolve().parents[1]
    schema_path = root / "schemas/tasks/S5.intro.schema.json"
    definition = load_yaml(root / "harness/story/tasks/S5.intro.yaml")
    inputs = {"character": {"id": "c1"}}
    too_long = {"intro": "あ" * 50 + "。", "sources": ["c1"]}

    assert validate_document(too_long, schema_path) == too_long
    rejected = validate_output(definition, json.dumps(too_long), inputs=inputs)
    assert not rejected.passed
    assert any("max_chars" in error for error in rejected.errors)

    ok = {"intro": "あ" * 49 + "。", "sources": ["c1"]}
    assert validate_output(definition, json.dumps(ok), inputs=inputs).passed


def test_non_local_endpoint_is_rejected() -> None:
    with pytest.raises(AdapterError):
        OllamaAdapter(_model("https://example.test:11434"))


def test_model_config_reads_repository_defaults() -> None:
    config = load_model_config("gpt-oss:20b", provider="ollama")
    assert config.endpoint == "http://127.0.0.1:11434"
    assert config.json_mode == "schema"
    assert config.think == "low"
    assert config.max_tokens == 8192
    assert config.context_length == 16384
    assert config.timeout_seconds == 600


def test_ollama_payload_includes_think_when_configured(ollama_server: str) -> None:
    adapter = OllamaAdapter(
        ModelConfig(
            name="thinking-model",
            provider="ollama",
            endpoint=ollama_server,
            temperature=0.2,
            max_tokens=123,
            context_length=456,
            json_mode="schema",
            think="low",
            timeout_seconds=600,
        )
    )
    adapter.complete("カード")

    assert _OllamaHandler.requests[0]["think"] == "low"


def test_ollama_adapter_uses_model_timeout_by_default(ollama_server: str) -> None:
    adapter = OllamaAdapter(_model(ollama_server))
    assert adapter.timeout == 600


def test_auto_records_adapter_failure_as_attempt_and_releases_claim(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    orchestrator = create_dummy_orchestrator(data_dir)
    run_id = orchestrator.create_run(
        task_specs=[{"task_id": "D1.items", "type": "D1.items"}],
        seed=1,
    )

    class FailingAdapter:
        def __init__(self, model: ModelConfig) -> None:
            pass

        def complete(self, *args: Any, **kwargs: Any) -> AdapterResponse:
            raise AdapterRequestError("Ollama への接続に3回失敗しました")

    runner = AutoRunner(
        data_dir,
        _model("http://127.0.0.1:11434"),
        orchestrator,
        adapter_factory=FailingAdapter,
    )
    runner.run(run_id=run_id, workers=1, until_empty=True)

    manifest = orchestrator.load_run(run_id)
    task = manifest["tasks"]["D2.echo-d1"]
    assert task["state"] == "failed"
    assert task["tries"] == 5
    assert task["attempt"] == 5
    assert task["claim"] is None
    attempt = json.loads(
        (
            data_dir
            / "runs"
            / run_id
            / "tasks"
            / "D2.echo-d1"
            / "attempts"
            / "5.json"
        ).read_text(encoding="utf-8")
    )
    assert "3回失敗" in attempt["reason"]


def test_auto_records_empty_response_as_attempt_and_releases_claim(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    orchestrator = create_dummy_orchestrator(data_dir)
    run_id = orchestrator.create_run(
        task_specs=[{"task_id": "D1.items", "type": "D1.items"}],
        seed=1,
    )

    class EmptyAdapter:
        def __init__(self, model: ModelConfig) -> None:
            pass

        def complete(self, *args: Any, **kwargs: Any) -> AdapterResponse:
            return AdapterResponse("   \u3000", "stop")

    runner = AutoRunner(
        data_dir,
        _model("http://127.0.0.1:11434"),
        orchestrator,
        adapter_factory=EmptyAdapter,
    )
    runner.run(run_id=run_id, workers=1, until_empty=True)

    task = orchestrator.load_run(run_id)["tasks"]["D2.echo-d1"]
    assert task["state"] == "failed"
    assert task["tries"] == 5
    assert task["attempt"] == 5
    assert task["claim"] is None
    assert "本文が空" in task["error"]


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


def test_auto_uses_the_manifest_harness_for_mixed_runs(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    story_orchestrator = create_story_orchestrator(data_dir)
    story_run_id = story_orchestrator.create_run(
        task_specs=[TaskSpec("S1.extract-p001", "S1.extract", index=("p001",))],
        seed=0x100001,
        input_data={
            "kind": "free",
            "paragraphs": [{"id": "p001", "text": "水路の町で門番が朝を迎えた。"}],
        },
    )
    dummy_orchestrator = create_dummy_orchestrator(data_dir)
    dummy_run_id = dummy_orchestrator.create_run(
        task_specs=[{"task_id": "D1.items", "type": "D1.items"}],
        seed=0x200002,
    )

    class FakeAdapter:
        def __init__(self, model: ModelConfig) -> None:
            pass

        def complete(self, card: str, **kwargs: Any) -> AdapterResponse:
            if "素材になる短い語句を5個" in card:
                return AdapterResponse(
                    json.dumps(
                        {
                            "materials": [
                                {"text": f"水路に残る記憶と{index}番目の影", "kind": "image"}
                                for index in range(5)
                            ],
                            "sources": ["p001"],
                        },
                        ensure_ascii=False,
                    ),
                    "stop",
                )
            if '"text": "..."' in card:
                item_id = card.split("id: '[", 1)[1].split("]'", 1)[0]
                return AdapterResponse(
                    json.dumps({"text": "確認できた項目です。", "sources": [item_id]}),
                    "stop",
                )
            return AdapterResponse("確認文をまとめた本文です。", "stop")

    def orchestrator_for(run_id: str):
        manifest = json.loads(
            (data_dir / "runs" / run_id / "manifest.json").read_text(encoding="utf-8")
        )
        return (
            story_orchestrator
            if manifest["harness_kind"] == "story"
            else dummy_orchestrator
        )

    runner = AutoRunner(
        data_dir,
        _model("http://127.0.0.1:11434"),
        None,
        orchestrator_factory=orchestrator_for,
        adapter_factory=FakeAdapter,
    )
    runner.run(run_id=None, workers=1, until_empty=True)

    assert story_orchestrator.load_run(story_run_id)["status"] == "completed"
    assert dummy_orchestrator.load_run(dummy_run_id)["status"] == "completed"
