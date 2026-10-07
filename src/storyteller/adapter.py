"""Local LLM adapters and the ``st auto`` executor.

The adapter deliberately talks to the same public claim/submit seam as an
external executor.  This keeps the local-model path subject to the same lease,
validation, and retry rules as every other executor.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import yaml

from .manifest import load_manifest, write_manifest
from .orchestrator import Orchestrator
from .storage import acquire_lock, atomic_write_json, manifest_lock
from .validation import YamlValidationError, parse_json_object, validate_document

from .adapter_retry import (
    AdapterError, AdapterRequestError, RequestRejected, TransportError,
    TransportExhausted, retry_transport,
)


@dataclass(frozen=True)
class ModelConfig:
    """A validated model configuration from ``config/models.yaml``."""

    name: str
    provider: str
    endpoint: str
    temperature: float
    max_tokens: int
    context_length: int
    json_mode: str
    think: bool | str | None = None
    timeout_seconds: float = 600.0


@dataclass(frozen=True)
class AdapterResponse:
    """The part of an Ollama response needed by the submission boundary."""

    content: str
    done_reason: str | None = None


_JSON_MODES = ("schema", "json", "off")
_THINK_VALUES = (False, "low", "medium", "high")
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "models.yaml"
_MODEL_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "models.schema.json"


def register_auto_command(subparsers: Any) -> None:
    """Register only the CLI surface for ``st auto``.

    The command implementation stays in this module so ``cli.py`` does not
    become a second home for adapter behavior while other CLI work is merged.
    """

    parser = subparsers.add_parser(
        "auto", help="run ready tasks through a local LLM adapter"
    )
    parser.add_argument("--provider", choices=("ollama", "openai-compatible"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--endpoint")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--run", dest="run_id")
    parser.add_argument("--until-empty", action="store_true")
    parser.set_defaults(_command_handler=run_auto_command)


def run_auto_command(args: argparse.Namespace) -> int:
    """CLI callback for ``st auto``."""

    if args.workers < 1:
        raise AdapterError("--workers は1以上の整数で指定してください")
    data_dir = _resolve_cli_data_dir()
    model = load_model_config(
        args.model,
        provider=args.provider,
        endpoint=args.endpoint,
    )
    if model.provider != "ollama":
        raise AdapterError(
            f"provider {model.provider!r} は Phase 1 では未対応です（ollama のみ）"
        )
    _validate_local_endpoint(model.endpoint)
    from .cli import _orchestrator

    orchestrator = _orchestrator(data_dir, args.run_id) if args.run_id else None
    runner = AutoRunner(
        data_dir,
        model,
        orchestrator,
        orchestrator_factory=lambda run_id: _orchestrator(data_dir, run_id),
    )
    runner.run(
        run_id=args.run_id,
        workers=args.workers,
        until_empty=args.until_empty,
    )
    return 0


def _resolve_cli_data_dir() -> Path:
    """Resolve the data directory without importing CLI internals."""

    from .storage import resolve_data_dir

    return resolve_data_dir()


def load_model_config(
    model: str,
    *,
    provider: str | None = None,
    endpoint: str | None = None,
    config_path: str | Path | None = None,
) -> ModelConfig:
    """Load and validate one model from the repository configuration."""

    if not isinstance(model, str) or not model:
        raise AdapterError("モデル名が必要です")
    path = Path(config_path) if config_path is not None else _DEFAULT_CONFIG_PATH
    try:
        with path.open("r", encoding="utf-8", newline=None) as stream:
            document = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as error:
        raise AdapterError(f"モデル設定を読み込めません: {path}") from error
    if not isinstance(document, Mapping) or not isinstance(document.get("models"), Mapping):
        raise AdapterError("config/models.yaml の models が不正です")
    try:
        validate_document(document, _MODEL_SCHEMA_PATH, source_path=path)
    except (YamlValidationError, OSError) as error:
        raise AdapterError(f"モデル設定の形式が不正です: {path}") from error
    raw = document["models"].get(model)
    if not isinstance(raw, Mapping):
        raise AdapterError(f"モデル設定がありません: {model}")

    resolved_provider = raw.get("provider")
    if not isinstance(resolved_provider, str) or not resolved_provider:
        raise AdapterError(f"モデル設定の provider が不正です: {model}")
    if provider is not None and provider != resolved_provider:
        raise AdapterError(
            f"指定した provider {provider!r} とモデル設定 {resolved_provider!r} が一致しません"
        )
    resolved_endpoint = endpoint if endpoint is not None else raw.get("endpoint")
    if not isinstance(resolved_endpoint, str) or not resolved_endpoint:
        raise AdapterError(f"モデル設定の endpoint が不正です: {model}")
    temperature = raw.get("temperature")
    max_tokens = raw.get("max_tokens")
    context_length = raw.get("context_length")
    json_mode = raw.get("json_mode", "schema")
    think = raw.get("think") if "think" in raw else None
    timeout_seconds = raw.get("timeout_seconds", 600)
    if (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or temperature < 0
        or isinstance(max_tokens, bool)
        or not isinstance(max_tokens, int)
        or max_tokens < 1
        or isinstance(context_length, bool)
        or not isinstance(context_length, int)
        or context_length < 1
        or json_mode not in _JSON_MODES
        or (think is not None and think not in _THINK_VALUES)
        or isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
    ):
        raise AdapterError(
            f"モデル設定の数値、json_mode、think または timeout_seconds が不正です: {model}"
        )
    return ModelConfig(
        name=model,
        provider=resolved_provider,
        endpoint=resolved_endpoint,
        temperature=float(temperature),
        max_tokens=max_tokens,
        context_length=context_length,
        json_mode=json_mode,
        think=think,
        timeout_seconds=float(timeout_seconds),
    )


def _validate_local_endpoint(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AdapterError("endpoint は http(s) の localhost URL で指定してください")
    hostname = parsed.hostname
    is_local_ip = False
    try:
        is_local_ip = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        pass
    if hostname.casefold() != "localhost" and not is_local_ip:
        raise AdapterError("endpoint は localhost、127.0.0.1、または ::1 に限ります")
    if parsed.username is not None or parsed.password is not None:
        raise AdapterError("endpoint に認証情報を含めることはできません")


def _chat_url(endpoint: str) -> str:
    return endpoint.rstrip("/") if endpoint.rstrip("/").endswith("/api/chat") else endpoint.rstrip("/") + "/api/chat"


def _without_string_constraints(value: Any) -> Any:
    """Copy a server schema without string length and pattern keywords (§6.5).

    Visit only schema locations so property/definition names and literal data
    remain intact. Submission validation still uses the caller's original.
    """

    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if key in {"minLength", "maxLength", "pattern"}:
                continue
            if key in {
                "properties", "patternProperties", "$defs", "definitions",
                "dependentSchemas", "dependencies",
            } and isinstance(item, Mapping):
                result[key] = {
                    name: _without_string_constraints(schema)
                    for name, schema in item.items()
                }
            elif key in {
                "additionalProperties", "unevaluatedProperties", "propertyNames",
                "items", "additionalItems", "unevaluatedItems", "contains",
                "not", "if", "then", "else", "contentSchema",
                "allOf", "anyOf", "oneOf", "prefixItems",
            }:
                result[key] = _without_string_constraints(item)
            else:
                result[key] = deepcopy(item)
        return result
    if isinstance(value, list):
        return [_without_string_constraints(item) for item in value]
    return value


class OllamaAdapter:
    """Small urllib-only client for Ollama's non-streaming chat API."""

    def __init__(self, model: ModelConfig, *, timeout: float | None = None) -> None:
        if model.provider != "ollama":
            raise AdapterError("OllamaAdapter は provider=ollama に限ります")
        _validate_local_endpoint(model.endpoint)
        self.model = model
        self.timeout = model.timeout_seconds if timeout is None else timeout

    def complete(
        self,
        card: str,
        *,
        schema: Mapping[str, Any] | None = None,
        json_mode: str | None = None,
    ) -> AdapterResponse:
        mode = json_mode or self.model.json_mode
        if mode not in _JSON_MODES:
            raise AdapterError(f"json_mode が不正です: {mode}")
        payload: dict[str, Any] = {
            "model": self.model.name,
            "messages": [{"role": "user", "content": card}],
            "stream": False,
            "options": {
                "temperature": self.model.temperature,
                "num_predict": self.model.max_tokens,
                "num_ctx": self.model.context_length,
            },
        }
        if self.model.think is not None:
            payload["think"] = self.model.think
        if schema is not None or _card_requests_json(card):
            if mode == "schema":
                if schema is None:
                    raise AdapterError("schema モードにはタスクの JSON Schema が必要です")
                payload["format"] = _without_string_constraints(schema)
            elif mode == "json":
                payload["format"] = "json"
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
                raise category(f"Ollama HTTP status {error.code}{detail}") from error
            except (URLError, TimeoutError, OSError, HTTPException) as error:
                raise TransportError(f"Ollama への接続に失敗しました: {error}") from error
            except (json.JSONDecodeError, UnicodeError) as error:
                raise AdapterError(f"Ollama の応答を解析できません: {error}") from error

        return retry_transport(send, timeout=self.timeout)

    @staticmethod
    def _decode_response(document: Any) -> AdapterResponse:
        if not isinstance(document, Mapping):
            raise AdapterError("Ollama の応答がJSONオブジェクトではありません")
        message = document.get("message")
        if not isinstance(message, Mapping) or not isinstance(message.get("content"), str):
            raise AdapterError("Ollama の応答に message.content がありません")
        done_reason = document.get("done_reason")
        if done_reason is not None and not isinstance(done_reason, str):
            raise AdapterError("Ollama の done_reason が不正です")
        return AdapterResponse(message["content"], done_reason)


def _card_requests_json(card: str) -> bool:
    return "次のJSONだけを出力すること。" in card


class _StateStore:
    """Process-shared model mode and empty-response counters."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "adapters" / "state.json"
        self.lock_path = data_dir / "adapters.lock"
        self._thread_lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"models": {}}
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise AdapterError(f"アダプタ状態を読み込めません: {self.path}") from error
        if not isinstance(value, Mapping) or not isinstance(value.get("models"), Mapping):
            raise AdapterError(f"アダプタ状態が不正です: {self.path}")
        return {"models": dict(value["models"])}

    @staticmethod
    def _normalise_record(record: Any, default_mode: str) -> dict[str, Any]:
        if not isinstance(record, Mapping):
            return {"json_mode": default_mode, "empty_streak": 0}
        mode = record.get("json_mode", default_mode)
        streak = record.get("empty_streak", 0)
        if mode not in _JSON_MODES or isinstance(streak, bool) or not isinstance(streak, int) or streak < 0:
            return {"json_mode": default_mode, "empty_streak": 0}
        return {"json_mode": mode, "empty_streak": streak}

    def current(self, model: ModelConfig) -> dict[str, Any]:
        with self._thread_lock, acquire_lock(self.lock_path):
            state = self._read()
            return self._normalise_record(state["models"].get(model.name), model.json_mode)

    def record_response(self, model: ModelConfig, *, empty_or_invalid: bool) -> tuple[str, bool]:
        with self._thread_lock, acquire_lock(self.lock_path):
            state = self._read()
            record = self._normalise_record(state["models"].get(model.name), model.json_mode)
            previous_mode = record["json_mode"]
            if empty_or_invalid and record["json_mode"] in {"schema", "json"}:
                record["empty_streak"] += 1
                if record["empty_streak"] >= 2:
                    record["json_mode"] = "json" if previous_mode == "schema" else "off"
                    record["empty_streak"] = 0
            else:
                record["empty_streak"] = 0
            state["models"][model.name] = record
            atomic_write_json(self.path, state)
            return str(record["json_mode"]), record["json_mode"] != previous_mode


class AutoRunner:
    """Claim, call Ollama, and submit until the selected work is exhausted."""

    def __init__(
        self,
        data_dir: str | Path,
        model: ModelConfig,
        orchestrator: Orchestrator | None,
        *,
        orchestrator_factory: Callable[[str], Orchestrator] | None = None,
        adapter_factory: Callable[[ModelConfig], OllamaAdapter] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve(strict=False)
        self.model = model
        self.orchestrator = orchestrator
        self.orchestrator_factory = orchestrator_factory
        self.state = _StateStore(self.data_dir)
        self.adapter_factory = adapter_factory or OllamaAdapter
        self._stop = threading.Event()

    def run(self, *, run_id: str | None, workers: int, until_empty: bool) -> None:
        if workers < 1:
            raise AdapterError("workers は1以上でなければなりません")
        if workers == 1:
            self._worker(run_id=run_id, until_empty=until_empty)
            return
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(self._worker, run_id=run_id, until_empty=until_empty)
                for _ in range(workers)
            ]
            for future in futures:
                future.result()

    def _worker(self, *, run_id: str | None, until_empty: bool) -> None:
        adapter = self.adapter_factory(self.model)
        while not self._stop.is_set():
            claim, orchestrator = self._claim_next(run_id)
            if claim is None:
                if not until_empty:
                    time.sleep(0.2)
                    continue
                if not self._has_claimed_tasks(run_id):
                    return
                time.sleep(0.05)
                continue
            try:
                claim_info = orchestrator.validate_claim(claim["ticket"])
                definition = orchestrator.task_definitions[
                    orchestrator.load_run(claim_info["run_id"])["tasks"][claim_info["task_id"]]["type"]
                ]
                fact_inputs = (
                    json.loads((orchestrator.task_dir(claim_info["run_id"], claim_info["task_id"])
                                / "input.json").read_text(encoding="utf-8"))
                    if definition.get("id") == "S4.facts" else None
                )
                schema = self._load_schema(definition, orchestrator, inputs=fact_inputs)
                mode = self.state.current(self.model)["json_mode"]
                response = adapter.complete(claim["card"], schema=schema, json_mode=mode)
                empty_response = not response.content.strip()
                invalid = empty_response or (
                    _card_requests_json(claim["card"])
                    and _is_unparseable_json(response.content)
                )
                new_mode, switched = self.state.record_response(
                    self.model, empty_or_invalid=invalid
                )
                if switched:
                    self._record_warning(
                        orchestrator,
                        claim_info["run_id"],
                        f"モデル {self.model.name} の json_mode を {new_mode} に切り替えました",
                    )
                if empty_response:
                    orchestrator.record_executor_failure(
                        claim["ticket"],
                        "Ollama の応答本文が空です",
                    )
                    continue
                result = orchestrator.submit(
                    claim["ticket"],
                    response.content,
                    truncated=response.done_reason == "length",
                )
                if not result.accepted:
                    continue
            except TransportExhausted as error:
                self._stop.set()
                orchestrator.revoke_claim(
                    claim_info["run_id"], claim_info["task_id"], reason=str(error),
                    expected_ticket=claim["ticket"],
                )
                print(f"警告: {error}", file=sys.stderr)
                return
            except RequestRejected as error:
                try:
                    orchestrator.record_executor_failure(claim["ticket"], str(error), fatal=True)
                except (AdapterError, OSError, ValueError) as submit_error:
                    print(
                        f"警告: LLMタスクの失敗を記録できません: {submit_error}",
                        file=sys.stderr,
                    )
                else:
                    print(f"警告: {error}", file=sys.stderr)
            except (AdapterError, OSError, ValueError) as error:
                orchestrator.record_executor_failure(claim["ticket"], str(error))
                print(f"警告: LLMタスクを処理できません: {error}", file=sys.stderr)

    def _claim_next(
        self, run_id: str | None
    ) -> tuple[dict[str, str] | None, Orchestrator | None]:
        """Claim from one run using the orchestrator selected for that run."""
        if run_id is not None:
            orchestrator = self._orchestrator_for_run(run_id)
            return (
                orchestrator.claim_next(
                    run_id,
                    executor_id=re.sub(r"[^A-Za-z0-9._-]", "-", f"{self.model.provider}.{self.model.name}")[:64],
                    isolation="adapter",
                ),
                orchestrator,
            )
        for candidate_run_id in self._candidate_run_ids():
            orchestrator = self._orchestrator_for_run(candidate_run_id)
            claim = orchestrator.claim_next(
                candidate_run_id,
                executor_id=re.sub(r"[^A-Za-z0-9._-]", "-", f"{self.model.provider}.{self.model.name}")[:64],
                isolation="adapter",
            )
            if claim is not None:
                return claim, orchestrator
        return None, None

    def _orchestrator_for_run(self, run_id: str) -> Orchestrator:
        if self.orchestrator_factory is not None:
            return self.orchestrator_factory(run_id)
        if self.orchestrator is not None:
            return self.orchestrator
        raise AdapterError("run のオーケストレータが指定されていません")

    def _candidate_run_ids(self) -> list[str]:
        runs_dir = self.data_dir / "runs"
        if not runs_dir.is_dir():
            return []
        candidates: list[tuple[str, str]] = []
        for entry in runs_dir.iterdir():
            if not entry.is_dir():
                continue
            try:
                manifest = load_manifest(entry / "manifest.json")
            except (OSError, ValueError):
                continue
            if manifest["status"] in {"active", "stalled"}:
                candidates.append((manifest["created_at"], manifest["run_id"]))
        candidates.sort(key=lambda item: (item[0], item[1]))
        return [candidate[1] for candidate in candidates]

    def _load_schema(
        self,
        definition: Mapping[str, Any],
        orchestrator: Orchestrator,
        *,
        inputs: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any] | None:
        if definition.get("output") != "json" or self.state.current(self.model)["json_mode"] != "schema":
            return None
        if definition.get("id") == "S4.facts" and inputs is not None:
            return inputs["fact_schema"]
        schema_ref = (definition.get("validate") or {}).get("schema")
        if not isinstance(schema_ref, str):
            return None
        path = orchestrator.definition_root / schema_ref
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise AdapterError(f"JSON Schema を読み込めません: {path}") from error
        if not isinstance(value, Mapping):
            raise AdapterError(f"JSON Schema がオブジェクトではありません: {path}")
        return value

    def _record_warning(
        self, orchestrator: Orchestrator, run_id: str, warning: str
    ) -> None:
        run_dir = orchestrator.run_dir(run_id)
        with manifest_lock(run_dir):
            manifest = load_manifest(run_dir / "manifest.json")
            if warning not in manifest["warnings"]:
                manifest["warnings"].append(warning)
            now = datetime.now(timezone.utc).replace(microsecond=0)
            manifest["updated_at"] = now.isoformat().replace("+00:00", "Z")
            write_manifest(run_dir / "manifest.json", manifest)

    def _has_claimed_tasks(self, run_id: str | None) -> bool:
        runs_dir = self.data_dir / "runs"
        if not runs_dir.is_dir():
            return False
        run_dirs = [runs_dir / run_id] if run_id is not None else list(runs_dir.iterdir())
        for run_dir in run_dirs:
            if not run_dir.is_dir():
                continue
            try:
                manifest = load_manifest(run_dir / "manifest.json")
            except (OSError, ValueError):
                continue
            if any(task.get("state") == "claimed" for task in manifest["tasks"].values()):
                return True
        return False


def _is_unparseable_json(content: str) -> bool:
    try:
        parse_json_object(content)
    except ValueError:
        return True
    return False


__all__ = [
    "AdapterError",
    "AdapterRequestError",
    "AdapterResponse",
    "AutoRunner",
    "ModelConfig",
    "OllamaAdapter",
    "load_model_config",
    "register_auto_command",
    "run_auto_command",
]
