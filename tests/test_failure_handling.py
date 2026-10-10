"""P1-35: failure classes, private Issue drafts, and range preflight."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
import jsonschema

from storyteller.adapter import AutoRunner, ModelConfig
from storyteller.adapter_retry import TransportError, TransportExhausted, retry_transport
from storyteller.cli import main
from storyteller.elements import parse_element_value
from storyteller.orchestrator import CodeTaskResult, Orchestrator, TaskSpec


def definition(**extra):
    return {"id": "D1.value", "version": 3, "kind": "llm", "element": "integer",
            "output": "text", "continuation": False,
            "inputs": {"material": {"label": "素材", "from": "input", "select": "input.material"}},
            "card": {"role": "値を書く。", "steps": ["値を1つだけ書く。"]}, **extra}


def model():
    return ModelConfig("test:model", "ollama", "http://127.0.0.1:11434", 0.2, 50, 1000, "off")


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def sleep(self, delay):
        self.waits.append(delay)
        self.now += delay

    def __call__(self):
        return self.now


def virtual_retry(monkeypatch, clock):
    def retry(request, *, timeout, budget):
        return retry_transport(request, timeout=timeout, clock=clock, sleep=clock.sleep, budget=budget)
    monkeypatch.setattr("storyteller.adapter.retry_transport", retry)


@pytest.mark.parametrize("failure", [URLError("offline"), TimeoutError("timeout"), 429, 500, 503])
def test_transport_retries_identical_request_without_attempt(tmp_path, monkeypatch, failure):
    harness = Orchestrator(tmp_path, {"D1.value": definition()})
    run = harness.create_run(seed=1, input_data={"material": {"id": "m001", "text": "入力"}})
    clock = Clock()
    virtual_retry(monkeypatch, clock)
    calls = []

    def send(request, *, timeout):
        task = harness.load_run(run)["tasks"]["D1.value"]
        assert task["state"] == "claimed" and task["tries"] == task["attempt"] == 0
        calls.append(request)
        if len(calls) <= 2:
            if isinstance(failure, int):
                raise HTTPError(request.full_url, failure, "bad", {}, BytesIO(b"busy"))
            raise failure
        return BytesIO(b'{"message":{"content":"42"}}')

    monkeypatch.setattr("storyteller.adapter.urlopen", send)
    AutoRunner(tmp_path, model(), harness).run(run_id=run, workers=1, until_empty=True)
    assert clock.waits == [30, 60]
    assert calls[0] is calls[1] is calls[2]
    task = harness.load_run(run)["tasks"]["D1.value"]
    assert task["state"] == "done" and task["tries"] == task["attempt"] == 0
    assert not (harness.task_dir(run, "D1.value") / "attempts").exists()


def lease_orchestrator(tmp_path, clock, definitions):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return Orchestrator(tmp_path, definitions, clock=lambda: base + timedelta(seconds=clock.now))


def test_retry_budget_is_lease_remaining_minus_five_minutes(tmp_path, monkeypatch):
    clock = Clock()
    harness = lease_orchestrator(tmp_path, clock, {"D1.value": definition()})
    run = harness.create_run(seed=1, input_data={"material": "素材"})
    virtual_retry(monkeypatch, clock)

    def send(request, *, timeout):
        assert 0 < timeout <= 600
        raise URLError("offline")

    monkeypatch.setattr("storyteller.adapter.urlopen", send)
    AutoRunner(tmp_path, model(), harness).run(run_id=run, workers=1, until_empty=True)
    assert clock.now == 1500
    assert clock.waits == [30, 60, 120, 240, 300, 300, 300, 150]
    task = harness.load_run(run)["tasks"]["D1.value"]
    assert task["state"] == "ready" and task["claim"] is None
    assert task["tries"] == task["attempt"] == 0


def test_no_retry_when_lease_has_five_minutes_or_less(tmp_path, monkeypatch):
    clock = Clock()
    harness = lease_orchestrator(tmp_path, clock, {"D1.value": definition(lease_minutes=5)})
    run = harness.create_run(seed=1, input_data={"material": "素材"})
    virtual_retry(monkeypatch, clock)
    calls = []

    def send(request, *, timeout):
        calls.append(timeout)
        raise URLError("offline")

    monkeypatch.setattr("storyteller.adapter.urlopen", send)
    AutoRunner(tmp_path, model(), harness).run(run_id=run, workers=1, until_empty=True)
    assert clock.waits == [] and len(calls) == 1
    task = harness.load_run(run)["tasks"]["D1.value"]
    assert task["state"] == "ready" and task["claim"] is None


def test_auto_continues_after_submission_rejected_by_expired_lease(tmp_path, monkeypatch, capsys):
    clock = Clock()
    harness = lease_orchestrator(tmp_path, clock, {
        "D1.a": definition(id="D1.a", lease_minutes=1),
        "D1.b": definition(id="D1.b", lease_minutes=1),
    })
    run = harness.create_run(seed=1, input_data={"material": "素材"})
    calls = []

    def send(request, *, timeout):
        calls.append(request)
        if len(calls) == 1:
            clock.now += 120  # inference outlives the lease
        return BytesIO(b'{"message":{"content":"42"}}')

    monkeypatch.setattr("storyteller.adapter.urlopen", send)
    AutoRunner(tmp_path, model(), harness).run(run_id=run, workers=1, until_empty=True)
    assert len(calls) >= 2
    assert "lease" in capsys.readouterr().err
    states = {t["state"] for t in harness.load_run(run)["tasks"].values()}
    assert "done" in states


def test_thirty_minutes_releases_claim_and_stops_auto(tmp_path, monkeypatch):
    clock = Clock()
    harness = lease_orchestrator(tmp_path, clock, {"D1.value": definition(lease_minutes=35)})
    run = harness.create_run(seed=1, input_data={"material": "素材"})
    virtual_retry(monkeypatch, clock)

    def send(request, *, timeout):
        assert 0 < timeout <= 600
        raise URLError("offline")

    monkeypatch.setattr("storyteller.adapter.urlopen", send)
    AutoRunner(tmp_path, model(), harness).run(run_id=run, workers=1, until_empty=True)
    assert clock.now == 1800
    assert clock.waits == [30, 60, 120, 240, 300, 300, 300, 300, 150]
    task = harness.load_run(run)["tasks"]["D1.value"]
    assert task["state"] == "ready" and task["claim"] is None
    assert task["tries"] == task["attempt"] == 0
    assert not (harness.task_dir(run, "D1.value") / "failure.md").exists()


def test_timeout_is_bounded_by_remaining_transport_budget():
    clock = Clock()
    timeouts = []

    def request(timeout):
        timeouts.append(timeout)
        clock.now += timeout
        raise TransportError("timeout")

    with pytest.raises(TransportExhausted):
        retry_transport(request, timeout=600, clock=clock, sleep=clock.sleep)
    assert clock.now == 1800
    assert timeouts == [600, 600, 510]


@pytest.mark.parametrize("status", [400, 401, 404, 422])
def test_request_rejection_is_fatal_even_with_skip_and_status_links_draft(
    tmp_path, monkeypatch, capsys, status,
):
    harness = Orchestrator(tmp_path, {"D1.value": definition(on_exhausted="skip")})
    secret = "公開してはいけない入力本文"
    run = harness.create_run(seed=1, input_data={"material": secret})
    calls = []

    def send(request, *, timeout):
        calls.append(request)
        raise HTTPError(request.full_url, status, "rejected", {}, BytesIO(("理由" + "あ" * 600).encode("utf-8")))

    monkeypatch.setattr("storyteller.adapter.urlopen", send)
    AutoRunner(tmp_path, model(), harness).run(run_id=run, workers=1, until_empty=True)
    task = harness.load_run(run)["tasks"]["D1.value"]
    assert len(calls) == task["tries"] == task["attempt"] == 1
    assert task["state"] == "failed" and task["claim"] is None
    draft = harness.task_dir(run, "D1.value") / "failure.md"
    report = draft.read_text(encoding="utf-8")
    assert secret not in report
    for expected in ["D1.value", "version: 3", "パッケージ:", "git:", "ollama.test-model", "試行 1", "HTTP status", "### 素材", f"{len(secret)}文字"]:
        assert expected in report
    monkeypatch.setattr("storyteller.cli._data_dir", lambda: tmp_path)
    capsys.readouterr()
    assert main(["status", "--run", run, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tasks"][0]["failure_report"] == "tasks/D1.value/failure.md"
    schema = json.loads((Path(__file__).parents[1] / "schemas/status.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(payload, schema)
    assert main(["status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload["runs"][0]) == {"run_id", "status", "counts"}
    jsonschema.validate(payload, schema)


def test_exhaustion_report_keeps_attempts_and_limits_output_preview(tmp_path):
    harness = Orchestrator(tmp_path, {"D1.value": definition(max_attempts=2)})
    run = harness.create_run(seed=1, input_data={"material": "入力を伏せる"})
    for raw in ["あ" * 300 + "先頭300字の外", "450〜500"]:
        claim = harness.claim_next(run, executor_id="worker")
        assert not harness.submit(claim["ticket"], raw).accepted
    report = (harness.task_dir(run, "D1.value") / "failure.md").read_text(encoding="utf-8")
    assert "試行 1" in report and "試行 2" in report
    assert "先頭300字の外" not in report
    assert "入力を伏せる" not in report
    assert "値を1つだけ書く" in report
    harness.retry_failed(run, "D1.value")
    claim = harness.claim_next(run, executor_id="worker")
    harness.record_executor_failure(claim["ticket"], "再度拒否", fatal=True)
    report = (harness.task_dir(run, "D1.value") / "failure.md").read_text(encoding="utf-8")
    assert "試行 3" in report and "再度拒否" in report


@pytest.mark.parametrize("limits", [{"min": 10, "max": 5}, {"min": 10, "max": "{current_year}"}])
def test_invalid_range_creates_no_task_or_run(tmp_path, limits):
    harness = Orchestrator(tmp_path, {"D1.value": definition(range=limits)})
    with pytest.raises(ValueError, match="min が max"):
        harness.create_run(seed=1, input_data={"current_year": 5})
    assert list((tmp_path / "runs").iterdir()) == []


@pytest.mark.parametrize("element,value", [("integer", 42), ("number", 42.5)])
def test_fixed_range_is_recorded_without_inference_with_sources(tmp_path, element, value):
    from storyteller.task_outputs import task_sources
    harness = Orchestrator(tmp_path, {"D1.value": definition(element=element, range={"min": value, "max": "{current_year}"})})
    run = harness.create_run(seed=1, input_data={"current_year": value, "material": {"id": "m001", "text": "素材"}})
    assert harness.claim_next(run) is None
    task = harness.load_run(run)["tasks"]["D1.value"]
    assert task["state"] == "done" and task["tries"] == task["attempt"] == 0
    directory = harness.task_dir(run, "D1.value")
    assert (directory / "output.md").read_text(encoding="utf-8") == str(value)
    assert task_sources(directory) == ["m001"]
    assert not (directory / "claim.json").exists()


def test_dynamic_invalid_range_creates_no_added_task(tmp_path):
    definitions = {"D1.plan": {"id": "D1.plan", "kind": "code", "version": 1, "handler": "plan"},
                   "D1.value": definition(range={"min": 9, "max": 3})}
    def plan(context):
        return CodeTaskResult({}, add_tasks=[TaskSpec("D1.value", "D1.value", deps=("D1.plan",))])
    harness = Orchestrator(tmp_path, definitions, code_handlers={"plan": plan})
    run = harness.create_run(seed=1, task_specs=[TaskSpec("D1.plan", "D1.plan")])
    manifest = harness.advance(run)
    assert manifest["tasks"]["D1.plan"]["state"] == "failed"
    assert "D1.value" not in manifest["tasks"]
    assert not harness.task_dir(run, "D1.value").exists()
    assert (harness.task_dir(run, "D1.plan") / "failure.md").exists()


@pytest.mark.parametrize("raw,element,unit,expected", [
    (" 約 １，２４８ 年。 ", "integer", "年", 1248),
    ("およそ450メートル。", "number", "メートル", 450),
    ("おおよそ 4.5", "number", "", 4.5),
    ("約 g123abc。", "choice", "", "g123abc"),
])
def test_parser_removes_only_specified_presentation(raw, element, unit, expected):
    assert parse_element_value(raw, element, unit=unit, choices=["g123abc"]) == expected


@pytest.mark.parametrize("raw", ["約450〜500年。", "450 500", "450/500", "450と500", "450年の説明"])
def test_parser_rejects_ranges_or_prose_with_single_value_reason(raw):
    with pytest.raises(ValueError, match="値を1つだけ書く"):
        parse_element_value(raw, "integer", unit="年")


def test_dynamic_range_uses_new_parent_output_before_task_creation(tmp_path):
    value = definition(range={"min": 42, "max": "{current_year}"}, inputs={
        "current_year": {"label": "現在の年", "from": "D1.plan", "select": "output.current_year", "required": True}
    })
    definitions = {"D1.plan": {"id": "D1.plan", "kind": "code", "version": 1, "handler": "plan"},
                   "D1.value": value}
    def plan(context):
        return CodeTaskResult({"current_year": 42}, add_tasks=[
            TaskSpec("D1.value", "D1.value", deps=("D1.plan",))
        ])
    harness = Orchestrator(tmp_path, definitions, code_handlers={"plan": plan})
    run = harness.create_run(seed=1, task_specs=[TaskSpec("D1.plan", "D1.plan")])
    assert harness.claim_next(run) is None
    task = harness.load_run(run)["tasks"]["D1.value"]
    assert task["state"] == "done" and task["tries"] == 0
    assert (harness.task_dir(run, "D1.value") / "output.md").read_text(encoding="utf-8") == "42"


def test_input_budget_failure_has_a_local_draft(tmp_path):
    harness = Orchestrator(tmp_path, {"D1.value": definition(max_input_chars=1)})
    run = harness.create_run(seed=1, input_data={"material": "秘密の長い入力本文"})
    assert harness.claim_next(run) is None
    task = harness.load_run(run)["tasks"]["D1.value"]
    assert task["state"] == "failed" and task["tries"] == 0
    draft = (harness.task_dir(run, "D1.value") / "failure.md").read_text(encoding="utf-8")
    assert "入力が予算を超える" in draft
    assert "秘密の長い入力本文" not in draft
    assert "### 素材" in draft and "9文字" in draft


@pytest.mark.parametrize("task_type", ["D1.value", "S1.value"])
def test_card_redaction_handles_material_containing_markdown_headings(tmp_path, task_type):
    value = definition(id=task_type, max_attempts=1)
    harness = Orchestrator(tmp_path, {task_type: value})
    secret = "秘密の素材\n## 手順\n漏らしてはいけない本文\n### 私的な見出し\n秘密の末尾"
    run = harness.create_run(seed=1, input_data={"material": secret})
    claim = harness.claim_next(run, executor_id="worker")
    harness.record_executor_failure(claim["ticket"], "空の応答")
    draft = (harness.task_dir(run, task_type) / "failure.md").read_text(encoding="utf-8")
    for fragment in ["秘密の素材", "漏らしてはいけない本文", "私的な見出し", "秘密の末尾"]:
        assert fragment not in draft
    assert "### 素材" in draft
    assert "値だけを書く" in draft


def test_transport_cleanup_does_not_revoke_a_replacement_claim(tmp_path):
    harness = Orchestrator(tmp_path, {"D1.value": definition()})
    run = harness.create_run(seed=1, input_data={"material": "素材"})
    original = harness.claim_next(run, executor_id="old")
    harness.revoke_claim(run, "D1.value")
    harness.revoke_claim(run, "D1.value", expected_ticket=original["ticket"])
    replacement = harness.claim_next(run, executor_id="new")
    harness.revoke_claim(run, "D1.value", expected_ticket=original["ticket"])
    assert harness.validate_claim(replacement["ticket"])["claim"]["executor_id"] == "new"
    assert harness.submit(replacement["ticket"], "42").accepted
