from __future__ import annotations

import json
import hashlib
import contextlib
import io
from io import BytesIO
from datetime import datetime, timezone
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest
import yaml
from jsonschema import Draft202012Validator

from storyteller.cards import input_char_count
from storyteller.adapter import AutoRunner, ModelConfig
from storyteller.story_s9 import _validate_references
from storyteller.validation import validate_document
from storyteller.cli import main as cli_main
from storyteller.new_run import create_story_orchestrator
from storyteller.orchestrator import Orchestrator
from storyteller.world_facts import specialize_fact_definition
from storyteller.story_quality import body_texts


ROOT = Path(__file__).parents[1]
RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")


@pytest.fixture
def memoized_manifest_tasks(monkeypatch):
    """Reuse only successful checks of identical records in the large DAG.

    The real CLI and harness still run every transition and validation call.
    Changed records are checked by the original validator, as are all other
    schema references. The final manifest is checked again without this cache.
    """
    original = Draft202012Validator.VALIDATORS["$ref"]
    accepted = set()

    def check_reference(validator, reference, instance, schema):
        if reference != "#/$defs/task":
            yield from original(validator, reference, instance, schema)
            return
        key = json.dumps(instance, ensure_ascii=False, sort_keys=True)
        if key in accepted:
            return
        errors = tuple(original(validator, reference, instance, schema))
        if not errors:
            accepted.add(key)
        yield from errors

    monkeypatch.setitem(Draft202012Validator.VALIDATORS, "$ref", check_reference)

    def validate_without_cache(document, schema):
        with monkeypatch.context() as context:
            context.setitem(Draft202012Validator.VALIDATORS, "$ref", original)
            return validate_document(document, schema)

    return validate_without_cache


def test_manifest_task_validation_cache_rechecks_changed_and_invalid_records(memoized_manifest_tasks):
    from storyteller.validation import SchemaValidationError

    schema = json.loads((ROOT / "schemas/manifest.schema.json").read_text(encoding="utf-8"))
    task_schema = {"$ref": "#/$defs/task", "$defs": schema["$defs"]}
    record = {"type": "S5.fact", "kind": "llm", "state": "ready", "deps": [], "index": ["c1-age"],
              "attempt": 0, "tries": 0, "invalidations": 0, "continuation_step": 0,
              "cache_key": None, "claim": None, "history": [], "error": None}
    for _ in range(2):
        assert validate_document(record, task_schema) == record
    record["state"] = "invalid"
    for _ in range(2):
        with pytest.raises(SchemaValidationError):
            validate_document(record, task_schema)
    with pytest.raises(SchemaValidationError):
        memoized_manifest_tasks(record, task_schema)
    record["state"] = "done"
    assert validate_document(record, task_schema) == memoized_manifest_tasks(record, task_schema)


def test_story_harness_rejection_status_and_retry(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    free_input = tmp_path / "free.md"
    secret = "静かな町で、失われた記録を探す。"
    free_input.write_text(secret, encoding="utf-8")
    created = _cli(data_dir, "new", "--free", str(free_input), "--scale", "short", "--seed", "35")
    assert created.returncode == 0, created.stderr
    run_id = created.stdout.strip()
    harness = create_story_orchestrator(data_dir)
    schema_path = ROOT / "schemas" / "status.schema.json"

    def status() -> dict[str, Any]:
        result = _cli(data_dir, "status", "--run", run_id, "--json")
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        validate_document(payload, schema_path)
        assert [task["task_id"] for task in payload["tasks"]] == sorted(harness.load_run(run_id)["tasks"])
        return payload

    assert all(task["failure_report"] is None for task in status()["tasks"])
    calls = []

    def reject(request, *, timeout):
        calls.append(request)
        raise HTTPError(request.full_url, 400, "rejected", {}, BytesIO(b"invalid request"))

    monkeypatch.setattr("storyteller.adapter.urlopen", reject)
    model = ModelConfig("test:model", "ollama", "http://127.0.0.1:11434", 0.2, 50, 1000, "off")
    AutoRunner(data_dir, model, harness).run(run_id=run_id, workers=1, until_empty=True)
    failed = [task for task in status()["tasks"] if task["state"] == "failed"]
    assert len(calls) == len(failed) == 1
    task = failed[0]
    assert task["tries"] == 1 and "HTTP status 400: invalid request" in task["error"]
    report_path = data_dir / "runs" / run_id / task["failure_report"]
    assert task["failure_report"] == f"tasks/{task['task_id']}/failure.md"
    report = report_path.read_text(encoding="utf-8")
    assert secret not in report
    assert "### 段落" in report and "HTTP status 400" in report
    assert "ollama.test-model" in report
    human = _cli(data_dir, "status", "--run", run_id)
    assert human.returncode == 0, human.stderr
    assert task["failure_report"] in human.stdout

    listed = _cli(data_dir, "status", "--json")
    assert listed.returncode == 0, listed.stderr
    listing = json.loads(listed.stdout)
    validate_document(listing, schema_path)
    assert set(listing["runs"][0]) == {"run_id", "status", "counts"}

    retried = _cli(data_dir, "retry", task["task_id"])
    assert retried.returncode == 0, retried.stderr
    assert report_path.is_file()
    assert all(task["failure_report"] is None for task in status()["tasks"])
    claim = harness.claim_next(run_id, executor_id="e2e")
    assert claim is not None
    assert all(task["failure_report"] is None for task in status()["tasks"])
    inputs = _task_input(data_dir, run_id, task["task_id"])
    record = harness.load_run(run_id)["tasks"][task["task_id"]]
    output = _fake_output(task["task_id"], record, inputs, send_comparison_yes=False)
    submitted = _cli(data_dir, "submit", claim["ticket"], input_text=json.dumps(output, ensure_ascii=False))
    assert submitted.returncode == 0, submitted.stderr
    assert all(task["failure_report"] is None for task in status()["tasks"])

    # Older runs may contain failed tasks without a saved draft.
    claim = harness.claim_next(run_id, executor_id="e2e")
    assert claim is not None
    failed_result = harness.record_executor_failure(claim["ticket"], "要求の拒否", fatal=True)
    missing_report = harness.task_dir(run_id, failed_result.task_id) / "failure.md"
    missing_report.unlink()
    payload = status()
    assert any(task["state"] == "failed" for task in payload["tasks"])
    assert all(task["failure_report"] is None for task in payload["tasks"])


def _cli(data_dir: Path, *arguments: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    previous_home = os.environ.get("STORYTELLER_HOME")
    os.environ["STORYTELLER_HOME"] = str(data_dir)
    stdout = io.StringIO()
    stderr = io.StringIO()
    stdin = io.StringIO(input_text or "")
    previous_stdin = sys.stdin
    try:
        sys.stdin = stdin
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            returncode = cli_main(arguments)
    finally:
        sys.stdin = previous_stdin
        if previous_home is None:
            os.environ.pop("STORYTELLER_HOME", None)
        else:
            os.environ["STORYTELLER_HOME"] = previous_home
    return subprocess.CompletedProcess(
        [sys.executable, "-m", "storyteller.cli", *arguments],
        returncode,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
    )


def _manifest(data_dir: Path, run_id: str) -> dict[str, Any]:
    path = data_dir / "runs" / run_id / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _claimed_task(
    data_dir: Path,
    run_id: str,
    ticket: str,
) -> tuple[str, dict[str, Any]]:
    manifest = _manifest(data_dir, run_id)
    for task_id, task in manifest["tasks"].items():
        if (task.get("claim") or {}).get("ticket") == ticket:
            return task_id, task
    raise AssertionError(f"claim が manifest にありません: {ticket}")


def _task_input(data_dir: Path, run_id: str, task_id: str) -> dict[str, Any]:
    path = data_dir / "runs" / run_id / "tasks" / task_id / "input.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _first_id(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("id", "set_id"):
            identifier = value.get(key)
            if isinstance(identifier, str):
                return identifier
        for item in value.values():
            try:
                return _first_id(item)
            except AssertionError:
                continue
    elif isinstance(value, list):
        for item in value:
            try:
                return _first_id(item)
            except AssertionError:
                continue
    raise AssertionError("入力に ID がありません")


def _filler_text(person_id: str, field: str, length: int) -> str:
    """Build placeholder text of exactly ``length`` characters.

    Used for the S5 items whose validation checks a min/max character range
    (task-model.md §6.2 min_chars/max_chars); a short fixed sentence like the
    other fake S5 outputs would fail those checks.
    """

    unit = f"{person_id}の{field}に関する具体的な記述。"
    repeated = unit * (length // len(unit) + 2)
    return repeated[:length - 1] + "。"


def _fake_output(
    task_id: str,
    task: dict[str, Any],
    inputs: dict[str, Any],
    *,
    send_comparison_yes: bool,
) -> dict[str, Any] | str:
    task_type = task["type"]
    index = task.get("index", [])

    if task_type == "S1.extract":
        paragraph_number = int(index[0][1:])
        number = int(index[1])
        kinds = ("theme", "suppression", "conflict", "desire", "value")
        return {"text": f"段落{paragraph_number}から見える素材{number}", "kind": kinds[number - 1]}

    if task_type in {"S2.expand", "S2.counter"}:
        marker = "-".join(index)
        return f"{marker}から生まれる具体的な要素"

    if task_type in {"S4.item_name", "S4.calendar_name"}:
        reading = "".join(inputs["name_sound"]["sounds"][:2])
        return {"name": reading, "reading": reading}

    if task_type == "S4.calendar_epoch":
        return "共同体が水路の通行を開始した。"

    if task_type == "S4.fact":
        if inputs["shape"] == "name":
            reading = "".join(inputs["name_sound"]["sounds"][:2])
            return {"name": reading, "reading": reading}
        if inputs["shape"] in {"number", "integer", "year"}:
            bounds = inputs.get("bounds", {})
            return str(max(bounds.get("min", 0), min(20, bounds.get("max", 20))))
        return "共有の水路を点検する"

    if task_type in {"S4.section", "S4.item"}:
        body = _filler_text(task_id, "世界の本文", 900 if task_type == "S4.item" else 1150)
        # Deterministic, distinct synthetic openings keep the shared fixture
        # from accidentally exercising the diversity retry limit everywhere.
        opening = "".join(chr(0x4E00 + byte) for byte in hashlib.shake_256(task_id.encode("utf-8")).digest(80))
        return opening + body[80:]

    if task_type == "S5.name":
        sound = inputs["name_sound"]
        reading = "".join(sound["sounds"][:2])
        return {"name": reading, "reading": reading}

    if task_type == "S5.fact":
        shape = inputs["shape"]
        if shape == "name":
            reading = "".join(inputs["name_sound"]["sounds"][:2])
            return {"name": reading, "reading": reading}
        if shape == "choice":
            return inputs["choices"][0]["id"]
        if shape in {"number", "integer"}:
            bounds = inputs.get("bounds", {})
            value = 170 if inputs["label"] == "身長" else 30
            return str(max(bounds.get("min", value), min(value, bounds.get("max", value))))
        return "母" if inputs["label"] == "続柄" else "水路を点検する"

    if task_type.startswith("S5."):
        field = task_type.removeprefix("S5.")
        person_id = inputs["character"]["id"]
        short_values = {
            "intro": f"{person_id}の短い紹介。",
            "motive": f"{person_id}が行動する動機。",
            "catchphrase": f"私は{person_id}として進む。",
        }
        if field in short_values:
            return short_values[field]
        # story-pipeline.md §8 / ADR-0007: the new S5 items each enforce a
        # min_chars/max_chars range (tables/volume.yaml), so the placeholder
        # text must actually land inside it, not just be non-empty.
        volume_ranges = {
            "profile": (800, 1500),
            "appearance": (400, 800),
            "personality": (500, 1000),
            "values": (400, 800),
            "voice": (400, 800),
            "inner_conflict": (500, 1000),
            "backstory": (1000, 2000),
            "relationship": (300, 600),
        }
        minimum, maximum = volume_ranges[field]
        target_length = (minimum + maximum) // 2
        return _filler_text(task_id, field, target_length)

    if task_type == "S7.event":
        return {
            "when": "町に変化の兆しが現れたとき。",
            "where": "町の境界で。",
            "why": "状況を確かめるため。",
            "intent": "小さな選択をする。",
            "what": "人物は境界の仕組みを動かす。",
            "result": f"新しい道筋が見える（試行{task.get('attempt', 0)}）。",
            "emotion": "静かな決意を抱く。",
            "foreshadowing": "遠くで次の変化が始まる。",
        }[task["index"][1]]

    if task_type == "S7.detail":
        # Minimum-length scenes must still reach the short-scale floor (§10).
        sentence = "人物は場面の状況を確認し、行動の結果を受け止めた。"
        return sentence + _filler_text(task_id, "場面の記述", 1500 - len(sentence))

    if task_type == "S8.compare":
        event_a = inputs["event_a"]["id"]
        event_b = inputs["event_b"]["id"]
        answer = "yes" if send_comparison_yes else "no"
        reason = "比較対象と結果が矛盾する。" if answer == "yes" else "二つの記述は整合している。"
        return {"answer": answer, "reason": reason}

    raise AssertionError(f"未知の LLM タスクです: {task_type}")


@pytest.mark.parametrize("long_inputs", [False, True], ids=["normal", "long-inputs"])
def test_story_harness_cli_runs_to_s9_with_schema_outputs_and_regeneration(
    tmp_path: Path,
    long_inputs: bool,
    memoized_manifest_tasks,
) -> None:
    data_dir = tmp_path / "data"
    free_input = tmp_path / "free.md"
    free_input.write_text(
        "静かな町で、失われた記録を探す。\n\n"
        "境界を守る仕組みが、暮らしの選択を変えていく。\n\n"
        "誰かの沈黙を手がかりに、明日の道を考える。\n",
        encoding="utf-8",
    )

    created = _cli(
        data_dir,
        "new",
        "--free",
        str(free_input),
        "--scale",
        "short",
        "--seed",
        "1515",
    )
    assert created.returncode == 0, created.stderr
    run_id = created.stdout.strip()
    assert RUN_ID.fullmatch(run_id)
    harness = create_story_orchestrator(data_dir)
    model = yaml.safe_load((ROOT / "config/models.yaml").read_text(encoding="utf-8"))["models"]["gpt-oss:20b"]
    # This test covers generation, not lease expiry: use the production
    # orchestrator's clock injection so host load cannot expire a valid claim.
    now = datetime.now(timezone.utc)
    harness = Orchestrator(data_dir, harness.task_definitions, harness.code_handlers,
                           clock=lambda: now, harness_root=harness.definition_root,
                           repository_root=harness.repository_root, harness_kind=harness.harness_kind)

    sent_comparison_yes = False
    comparison_answers: list[str] = []
    invalidated_s7 = False
    trimmed_tasks: set[str] = set()
    trim_counts: dict[str, int] = {}
    extended_types: set[str] = set()
    continuation_outputs: dict[str, str] = {}
    extended_tasks: set[str] = set()
    similar_facets: list[str] = []
    rewritten_s4 = False
    quality_rejections: set[str] = set()
    rejected_tasks: set[str] = set()
    noun_warning_task: str | None = None
    for _ in range(2000):
        claimed = harness.claim_next(run_id, executor_id="e2e", isolation="none")
        if claimed is None:
            break
        task_id, task = _claimed_task(data_dir, run_id, claimed["ticket"])
        inputs = _task_input(data_dir, run_id, task_id)
        is_comparison = task["type"] == "S8.compare"
        output = _fake_output(
            task_id,
            task,
            inputs,
            send_comparison_yes=is_comparison and not sent_comparison_yes,
        )
        definition = specialize_fact_definition(harness.task_definitions[task["type"]], inputs)
        assert input_char_count(definition, inputs) <= definition.get("max_input_chars", 3000)
        # Conservative Japanese estimate: 2 tokens per card character, plus
        # the model's full output allowance. Includes retries and scene tails.
        assert 2 * len(claimed["card"]) + model["max_tokens"] <= model["context_length"], task_id
        assert "### 物語の素材" in claimed["card"] or "### 作り方の指示" in claimed["card"]
        assert "作り方の指示は本文に書かず" in claimed["card"]
        if task["type"] == "S5.intro" and noun_warning_task is None:
            terms = [*inputs["glossary"], *inputs["facts"]["glossary"]]
            assert terms
            assert all("registered_task" not in entry for entry in terms)
            output = "ゼラフィナを訪ねる人物。"
            noun_warning_task = task_id
        if task["type"] == "S5.profile" and "meta" not in quality_rejections:
            rejected = harness.submit(claimed["ticket"], "名前の響き" + output)
            assert not rejected.accepted and any("avoid_listed" in error for error in rejected.errors)
            quality_rejections.add("meta")
            rejected_tasks.add(task_id)
            continue
        input_body = next((text for text in body_texts(inputs) if len(text) >= 60), None)
        if task["type"] == "S7.detail" and input_body and "copy" not in quality_rejections:
            rejected = harness.submit(claimed["ticket"], input_body[:60] + output)
            assert not rejected.accepted and any("no_copy_from_inputs" in error for error in rejected.errors)
            quality_rejections.add("copy")
            rejected_tasks.add(task_id)
            continue
        if long_inputs:
            if task["type"] in {"S4.section", "S4.item"}:
                output = output[:80] + _filler_text(task_id, "世界の本文", 1120)
            elif task["type"] == "S5.profile":
                output = _filler_text(task_id, "profile", 1500)
            elif task["type"] == "S5.voice":
                output = _filler_text(task_id, "voice", 800)
            elif task["type"] == "S7.event":
                output = _filler_text(task_id, task["type"], 120)
        if task["type"] == "S4.section":
            if not similar_facets:
                similar_facets = sorted(
                    name for name in _manifest(data_dir, run_id)["tasks"]
                    if name.startswith("S4.section-place-")
                )[-2:]
                assert len(similar_facets) == 2
            if task_id in similar_facets and task["invalidations"] == 0:
                opening = "丘の頂上に立つと、風が皮膚に触れ、草の揺れが足元の道筋を知らせる。" * 3
                output = opening[:80] + output[80:]
            if task["invalidations"]:
                assert task_id == similar_facets[1]
                assert "この書き出しと似ないように書き始める" in claimed["card"]
                assert "丘の頂上に立つと" in claimed["card"]
                rewritten_s4 = True
        # Mix normal outputs with overlong outputs for each long task type.
        # Two section facets reproduce the observed f1/f2 failures.
        if trim_counts.get(task["type"], 0) < (2 if task["type"] == "S4.section" else 1):
            definition = specialize_fact_definition(harness.task_definitions[task["type"]], inputs)
            for check in definition.get("validate", {}).get("checks", []):
                arguments = check.get("max_chars", {}) if isinstance(check, dict) else {}
                if arguments.get("fix") != "trim":
                    continue
                trim_arguments = arguments
                maximum = arguments["n"]
                original_text = output[arguments["field"]] if "field" in arguments else output
                expected_fixed_text = original_text if original_text.endswith("。") else original_text + "。"
                long_text = expected_fixed_text + "余" * (maximum + 10 - len(expected_fixed_text)) + "。"
                if "field" in arguments:
                    output[arguments["field"]] = long_text
                else:
                    output = long_text
                trimmed_tasks.add(task_id)
                trim_counts[task["type"]] = trim_counts.get(task["type"], 0) + 1
        definition = specialize_fact_definition(harness.task_definitions[task["type"]], inputs)
        if definition.get("extend_to_min"):
            if task_id in continuation_outputs:
                assert "既出の文章を繰り返さず、同じ内容をさらに具体的に書き足す" in claimed["card"]
                output = continuation_outputs.pop(task_id)
            elif task["type"] not in extended_types and task_id not in trimmed_tasks:
                assert isinstance(output, str)
                minimum = next(check["min_chars"]["n"] for check in definition["validate"]["checks"] if "min_chars" in check)
                prefix = _filler_text(task_id, "書き足す前の説明", minimum // 2) + "\n\n"
                # Blank lines do not contribute to min_chars; retain exactly
                # the original text length even for minimum-length scenes.
                continuation_outputs[task_id] = prefix + output[len(prefix.rstrip()):]
                output = prefix
                extended_types.add(task["type"])
                extended_tasks.add(task_id)
        if is_comparison:
            comparison_answers.append(output["answer"])
            if output["answer"] == "yes":
                sent_comparison_yes = True
        submitted_output = (
            output
            if definition["output"] == "text"
            else json.dumps(output, ensure_ascii=False)
        )
        submitted = harness.submit(claimed["ticket"], submitted_output)
        assert submitted.accepted, (task_id, submitted.errors)
        if task_id == noun_warning_task:
            assert any("未登録の固有名詞" in warning and "ゼラフィナ" in warning
                       for warning in submitted.warnings)
        if task_id in trimmed_tasks:
            assert f"{task_id}: 字数の上限で切り詰め" in submitted.warnings
            task_dir = data_dir / "runs" / run_id / "tasks" / task_id
            if definition["output"] == "text":
                stored = (task_dir / "output.md").read_text(encoding="utf-8")
                fixed_text = submitted.value
            else:
                stored = json.loads((task_dir / "output.json").read_text(encoding="utf-8"))
                fixed_text = submitted.value[trim_arguments["field"]]
            assert stored == submitted.value
            assert fixed_text == expected_fixed_text
        current = _manifest(data_dir, run_id)
        invalidated_s7 = invalidated_s7 or any(
            task_name.startswith("S7.event-")
            and record["invalidations"] == 1
            for task_name, record in current["tasks"].items()
        )
    else:
        raise AssertionError("CLI 実行が上限回数で完走しませんでした")

    final_manifest = _manifest(data_dir, run_id)
    assert final_manifest["status"] == "completed"
    fact_tasks = {name: task for name, task in final_manifest["tasks"].items()
                  if task["type"] == "S4.fact"}
    assert fact_tasks and all(task["state"] == "done" for task in fact_tasks.values())
    assert quality_rejections == {"meta", "copy"}
    assert noun_warning_task is not None
    assert final_manifest["tasks"][noun_warning_task]["tries"] == 0
    assert any(warning.startswith(f"{noun_warning_task}: no_new_proper_nouns:")
               and "ゼラフィナ" in warning for warning in final_manifest["warnings"])
    if long_inputs:
        assert len([name for name in final_manifest["tasks"] if name.startswith("S4.section-place-")]) >= 10
    assert sent_comparison_yes
    assert comparison_answers.count("yes") == 1
    assert comparison_answers and all(answer in {"yes", "no"} for answer in comparison_answers)
    assert invalidated_s7
    assert rewritten_s4
    assert final_manifest["tasks"][similar_facets[0]]["invalidations"] == 0
    assert final_manifest["tasks"][similar_facets[1]]["invalidations"] == 1
    diversity_ids = {
        name for name, record in final_manifest["tasks"].items()
        if record["type"] == "S4.diversity"
    }
    assert diversity_ids
    for name in ("S6.expand", "S9.assemble"):
        assert diversity_ids <= set(final_manifest["tasks"][name]["deps"])
    for diversity_id in diversity_ids:
        record = final_manifest["tasks"][diversity_id]
        section_id = record["index"][0]
        facet_ids = {
            name for name in final_manifest["tasks"]
            if name.startswith(f"S4.section-{section_id}-")
        }
        assert set(record["deps"]) == {"S3.assign", *facet_ids}
        assert record["state"] == "done"
    assert any(
        history["to"] == "blocked"
        for history in final_manifest["tasks"]["S4.diversity-place"]["history"]
    )
    assert not any("書き出しの類似が無効化上限" in warning for warning in final_manifest["warnings"])
    assert set(trim_counts) == {
        "S4.section", "S4.item", "S5.profile", "S5.appearance", "S5.personality", "S5.values", "S5.backstory",
        "S5.relationship", "S5.voice", "S5.inner_conflict", "S7.detail",
    }
    assert trim_counts["S4.section"] == 2
    assert extended_types == set(trim_counts)
    for task_id in extended_tasks:
        assert final_manifest["tasks"][task_id]["tries"] == int(task_id in rejected_tasks)
        assert final_manifest["tasks"][task_id]["continuation_step"] == 1
        assert any(warning.startswith(f"{task_id}: 継続の重複段落を除去") for warning in final_manifest["warnings"])
    assert not continuation_outputs
    for task_id in trimmed_tasks:
        assert final_manifest["tasks"][task_id]["tries"] == int(task_id in rejected_tasks)
        assert f"{task_id}: 字数の上限で切り詰め" in final_manifest["warnings"]

    run_dir = data_dir / "runs" / run_id
    memoized_manifest_tasks(final_manifest, ROOT / "schemas/manifest.schema.json")
    story_path = run_dir / "story" / "story.json"
    markdown_path = run_dir / "story" / "story.md"
    assert story_path.is_file()
    assert markdown_path.is_file()
    glossary_path = run_dir / "story" / "glossary.md"
    glossary_markdown = glossary_path.read_text(encoding="utf-8")
    assert "| ID | 名前 | 読み | 種類 | 定義 | 登録したタスク |" in glossary_markdown
    world_facts = (run_dir / "story" / "world_facts.md").read_text(encoding="utf-8")
    assignment = json.loads((run_dir / "tasks/S3.assign/output.json").read_text(encoding="utf-8"))
    calendar_name = json.loads((run_dir / "tasks/S4.calendar_name/output.json").read_text(encoding="utf-8"))["name"]
    current_year = assignment["calendar"]["current_year"]
    assert f"暦：{calendar_name}" in world_facts
    assert f"現在の年：{current_year}年" in world_facts
    assert "| 最高地点の標高 | 20.0 | m |" in world_facts
    for task_id, task in final_manifest["tasks"].items():
        if task["type"] in {"S4.section", "S4.item"}:
            card_input = _task_input(data_dir, run_id, task_id)
            assert len(card_input["facts"]) >= 2
            assert all(set(row) == {"id", "text"} and "：" in row["text"] for row in card_input["facts"])
            assert card_input["calendar"] == calendar_name
            assert card_input["current_year"] == current_year
    characters_markdown = (run_dir / "story" / "characters.md").read_text(encoding="utf-8")
    character_facts = {task_id: task for task_id, task in final_manifest["tasks"].items()
                       if task["type"] == "S5.fact"}
    assert character_facts and all(task["state"] == "done" for task in character_facts.values())
    assert characters_markdown.count("### 事実のシート") == len(assignment["cast"])
    assert characters_markdown.count("| 身長（cm） | 170.0 |") == len(assignment["cast"])
    for task_id, task in final_manifest["tasks"].items():
        if task["type"].startswith("S5.") and task["type"] not in {"S5.name", "S5.fact", "S5.fact_plan", "S5.relationship_context"}:
            card_input = _task_input(data_dir, run_id, task_id)
            assert card_input["facts"]["age"] == 30
            assert card_input["facts"]["height_cm"] == 170
            assert card_input["facts"]["calendar"] == calendar_name
            assert any(entry["id"] == card_input["facts"]["birthplace"]
                       for entry in card_input["facts"]["glossary"])
    story = json.loads(story_path.read_text(encoding="utf-8"))
    assert story["meta"]["volume"]["characters"]["chars"] == sum(
        not char.isspace() for char in characters_markdown
    )
    assert story["cast"][0]["sources"]
    for section in story["world"]["sections"]:
        assert section["sources"]
        for item in section["items"]:
            assert item["sources"]
    validate_document(story, ROOT / "schemas" / "story.schema.json")
    story_markdown = markdown_path.read_text(encoding="utf-8")
    assert "人物は場面の状況を確認し、行動の結果を受け止めた。" in story_markdown
    assert story["meta"]["volume"]["story"]["chars"] >= 100_000
    scene_counts = final_manifest["scale"]["derived"]["volume"]["story"]["scene_counts"]
    detail_tasks = [task for task in final_manifest["tasks"].values() if task["type"] == "S7.detail"]
    assert len(detail_tasks) == sum(scene_counts) == 67
    cast_names = {person["id"]: person["name"] for person in story["cast"]}
    for event in story["events"]:
        for label, field in (("いつ", "when"), ("どこで", "where"), ("目的", "why"), ("行動", "what"), ("結果", "result")):
            assert f"- {label}：{event[field]}\n" in story_markdown
        names = "、".join(cast_names[person_id] for person_id in event["who"])
        assert f"- 誰が：{names}\n" in story_markdown
    assert story["meta"]["volume"]["world"]["chars"] >= 100_000
    world_description = (run_dir / "story" / "world.md").read_text(encoding="utf-8")
    assert story["meta"]["volume"]["world"]["chars"] > sum(
        not char.isspace() for char in world_description
    )

    outputs: dict[str, Any] = {}
    for task_id, task in final_manifest["tasks"].items():
        if task["state"] != "done":
            continue
        output_path = run_dir / "tasks" / task_id / "output.json"
        if output_path.is_file():
            outputs[task_id] = json.loads(output_path.read_text(encoding="utf-8"))
    input_data = json.loads((run_dir / "input.json").read_text(encoding="utf-8"))
    _validate_references(story, input_data, outputs)
