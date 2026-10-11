"""OpenAI-compatible LLM adapter (LM Studio, llama.cpp server, vLLM, ...).

Speaks the non-streaming ``/v1/chat/completions`` API with urllib only
(architecture.md §5).  The connection target is limited to localhost.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .adapter import (
    _JSON_MODES,
    AdapterResponse,
    ModelConfig,
    _card_requests_json,
    _validate_local_endpoint,
    _without_string_constraints,
)
from .adapter_retry import (
    AdapterError, RequestRejected, TransportError, retry_transport,
)

_CHAT_PATH = "/chat/completions"


def _chat_url(endpoint: str) -> str:
    base = endpoint.rstrip("/")
    if base.endswith(_CHAT_PATH):
        return base
    if base.endswith("/v1"):
        return base + _CHAT_PATH
    return base + "/v1" + _CHAT_PATH


class OpenAICompatibleAdapter:
    """Small urllib-only client for an OpenAI-compatible chat API."""

    def __init__(self, model: ModelConfig, *, timeout: float | None = None) -> None:
        if model.provider != "openai-compatible":
            raise AdapterError("OpenAICompatibleAdapter は provider=openai-compatible に限ります")
        _validate_local_endpoint(model.endpoint)
        self.model = model
        self.timeout = model.timeout_seconds if timeout is None else timeout

    def complete(
        self,
        card: str,
        *,
        schema: Mapping[str, Any] | None = None,
        json_mode: str | None = None,
        retry_budget: float = 1800.0,
    ) -> AdapterResponse:
        mode = json_mode or self.model.json_mode
        if mode not in _JSON_MODES:
            raise AdapterError(f"json_mode が不正です: {mode}")
        payload: dict[str, Any] = {
            "model": self.model.name,
            "messages": [{"role": "user", "content": card}],
            "stream": False,
            "temperature": self.model.temperature,
            "max_tokens": self.model.max_tokens,
        }
        if schema is not None or _card_requests_json(card):
            if mode == "schema":
                if schema is None:
                    raise AdapterError("schema モードにはタスクの JSON Schema が必要です")
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "output",
                        "strict": True,
                        "schema": _without_string_constraints(schema),
                    },
                }
            elif mode == "json":
                payload["response_format"] = {"type": "json_object"}
        request = Request(
            _chat_url(self.model.endpoint),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        def send(timeout: float) -> AdapterResponse:
            try:
                with urlopen(request, timeout=timeout) as response:
                    status = getattr(response, "status", 200)
                    if status < 200 or status >= 300:
                        raise HTTPError(request.full_url, status, "HTTP error", {}, response)
                    decoded = json.loads(response.read().decode("utf-8"))
                return self._decode_response(decoded)
            except HTTPError as error:
                try:
                    with error:
                        body = error.read(2048).decode("utf-8", errors="replace")[:500]
                except OSError:
                    body = ""
                detail = f": {body}" if body else ""
                category = RequestRejected if 400 <= error.code < 500 and error.code != 429 else TransportError
                raise category(f"OpenAI互換API HTTP status {error.code}{detail}") from error
            except (URLError, TimeoutError, OSError, HTTPException) as error:
                raise TransportError(f"OpenAI互換API への接続に失敗しました: {error}") from error
            except (json.JSONDecodeError, UnicodeError) as error:
                raise AdapterError(f"OpenAI互換API の応答を解析できません: {error}") from error

        return retry_transport(send, timeout=self.timeout, budget=retry_budget)

    @staticmethod
    def _decode_response(document: Any) -> AdapterResponse:
        if not isinstance(document, Mapping):
            raise AdapterError("OpenAI互換API の応答がJSONオブジェクトではありません")
        choices = document.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else None
        message = choice.get("message") if isinstance(choice, Mapping) else None
        if not isinstance(message, Mapping):
            raise AdapterError("OpenAI互換API の応答に choices[0].message がありません")
        content = message.get("content")
        if content is None:
            content = ""  # reasoning-only or empty reply: handled as an empty response
        if not isinstance(content, str):
            raise AdapterError("OpenAI互換API の message.content が文字列ではありません")
        finish_reason = choice.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise AdapterError("OpenAI互換API の finish_reason が不正です")
        # "length" is the same signal as Ollama's done_reason (task-model §8).
        return AdapterResponse(content, finish_reason)
