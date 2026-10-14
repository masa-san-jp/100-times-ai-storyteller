from __future__ import annotations

import errno
import json
from pathlib import Path

import pytest

import storyteller.orchestrator as module
from storyteller.cache import cache_key, save_cache
from storyteller.orchestrator import Orchestrator, TaskSpec
from storyteller.seed import derive_task_seed
from storyteller.storage import atomic_write_json
from tests.test_cache import llm_definition


TASK_IDS = ["D2.echo-a", "D2.echo-b", "D2.echo-c"]


def setup_run(tmp_path: Path, *, chain: bool = False):
    definitions = {
        "D1.make": {"id": "D1.make", "version": 1, "kind": "code", "handler": "make"},
        "D2.echo": llm_definition("D2.echo", inputs={
            "given": {"label": "依存", "from": "D1.make", "select": "data"},
            "provided": {"label": "入力", "from": "input", "select": "input.data"},
        }),
    }
    specs = [TaskSpec("D1.make", "D1.make")]
    specs += [TaskSpec(task, "D2.echo", deps=("D1.make",), index=(task[-1],))
              for task in TASK_IDS]
    if chain:
        definitions["D3.tail"] = llm_definition("D3.tail", inputs={
            "prior": {"label": "前段", "from": "D2.echo", "select": "b.text"},
        })
        specs.append(TaskSpec("D3.tail", "D3.tail", deps=("D2.echo-b",)))
    orchestrator = Orchestrator(tmp_path, definitions, {"make": lambda context: {"data": ["code"]}})
    run = orchestrator.create_run(seed=123, input_data={"data": ["run"]}, tasks=specs)
    return orchestrator, run


def key_for(task_id: str, given=None, provided=None):
    return cache_key("D2.echo", 1, {
        "given": ["code"] if given is None else given,
        "provided": ["run"] if provided is None else provided,
    }, seed=derive_task_seed(123, task_id, 0))


def test_shared_reads_keep_cache_completion_chain_and_claim_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator, run = setup_run(tmp_path, chain=True)
    save_cache(tmp_path, key_for("D2.echo-b"), {"text": "cached-b"})
    tail_key = cache_key("D3.tail", 1, {"prior": "cached-b"},
                         seed=derive_task_seed(123, "D3.tail", 0))
    save_cache(tmp_path, tail_key, {"text": "cached-tail"})
    original_input, original_output = module._read_run_input, module.read_task_output
    input_reads, dependency_reads = [], []

    def read_input(path):
        input_reads.append(path)
        return original_input(path)

    def read_output(path, task_type):
        if path.name == "D1.make":
            dependency_reads.append(path)
        return original_output(path, task_type)

    monkeypatch.setattr(module, "_read_run_input", read_input)
    monkeypatch.setattr(module, "read_task_output", read_output)
    claimed = orchestrator.claim_next(run, executor_id="worker")
    assert claimed is not None
    manifest = orchestrator.load_run(run)
    assert manifest["tasks"]["D2.echo-a"]["state"] == "claimed"
    assert manifest["tasks"]["D2.echo-c"]["state"] == "ready"
    assert manifest["tasks"]["D2.echo-b"]["state"] == "done"
    assert manifest["tasks"]["D3.tail"]["state"] == "done"
    for task in TASK_IDS:
        assert manifest["tasks"][task]["cache_key"] == key_for(task)
    assert manifest["tasks"]["D3.tail"]["cache_key"] == tail_key
    assert not (orchestrator.task_dir(run, "D2.echo-b") / "claim.json").exists()
    assert json.loads((orchestrator.task_dir(run, "D3.tail") / "input.json").read_text(encoding="utf-8")) == {"prior": "cached-b"}
    # One read for the handler, one for the complete locked scan, and one
    # fresh read for the real claimed task; not one per ready/cache-hit task.
    assert len(input_reads) == 3
    assert len(dependency_reads) == 2
    assert orchestrator.submit(claimed["ticket"], '{"text":"accepted"}').accepted
    second = orchestrator.claim_next(run, executor_id="worker")
    assert second is not None
    assert orchestrator.validate_claim(second["ticket"])["task_id"] == "D2.echo-c"


def test_mutating_one_resolved_task_does_not_leak_to_other_tasks_or_handlers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator, run = setup_run(tmp_path)
    original = orchestrator._resolve_task_inputs
    changed = False

    def mutate_first(manifest, run_id, task_id, **kwargs):
        nonlocal changed
        resolved = original(manifest, run_id, task_id, **kwargs)
        if kwargs.get("reads") is not None and task_id == TASK_IDS[0] and not changed:
            changed = True
            resolved.dependency_outputs["D1.make"]["data"].append("mutated")
            resolved.run_input["data"].append("mutated")
        return resolved

    with monkeypatch.context() as patch:
        patch.setattr(orchestrator, "_resolve_task_inputs", mutate_first)
        manifest = orchestrator.advance(run)
    assert changed
    assert manifest["tasks"][TASK_IDS[0]]["cache_key"] == key_for(
        TASK_IDS[0], ["code", "mutated"], ["run", "mutated"])
    for task in TASK_IDS[1:]:
        assert manifest["tasks"][task]["cache_key"] == key_for(task)
    context = orchestrator._build_context(manifest, run, TASK_IDS[1])
    assert context.inputs == {"given": ["code"], "provided": ["run"]}
    context.outputs["D1.make"]["data"].append("handler-copy")
    context.task["history"].append({"from": "copy"})
    assert context.dependency_outputs["D1.make"]["data"] == ["code"]
    assert {"from": "copy"} not in manifest["tasks"][TASK_IDS[1]]["history"]
    assert json.loads((orchestrator.run_dir(run) / "input.json").read_text(encoding="utf-8")) == {"data": ["run"]}
    assert json.loads((orchestrator.task_dir(run, "D1.make") / "output.json").read_text(encoding="utf-8")) == {"data": ["code"]}


@pytest.mark.parametrize("changed_file,deleted", [("dependency", False), ("dependency", True), ("run-input", False), ("run-input", True)])
def test_same_scan_detects_replacement_and_deletion_before_reusing_a_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed_file: str, deleted: bool,
) -> None:
    orchestrator, run = setup_run(tmp_path)
    path = (orchestrator.task_dir(run, "D1.make") / "output.json"
            if changed_file == "dependency" else orchestrator.run_dir(run) / "input.json")
    original = orchestrator._resolve_task_inputs
    changed = False

    def replace_after_first(manifest, run_id, task_id, **kwargs):
        nonlocal changed
        resolved = original(manifest, run_id, task_id, **kwargs)
        if kwargs.get("reads") is not None and task_id == TASK_IDS[0] and not changed:
            changed = True
            if deleted:
                path.unlink()
            else:
                atomic_write_json(path, {"data": ["new"]})
        return resolved

    monkeypatch.setattr(orchestrator, "_resolve_task_inputs", replace_after_first)
    manifest = orchestrator.advance(run)
    assert changed
    assert manifest["tasks"][TASK_IDS[0]]["cache_key"] == key_for(TASK_IDS[0])
    for task in TASK_IDS[1:]:
        record = manifest["tasks"][task]
        if deleted:
            assert record["state"] == "failed"
            assert ("出力ファイルがありません" if changed_file == "dependency" else "入力を読み込めません") in record["error"]
        else:
            assert record["state"] == "ready"
            assert record["cache_key"] == key_for(
                task, ["new"] if changed_file == "dependency" else None,
                ["new"] if changed_file == "run-input" else None)


def test_unreadable_signature_never_allows_an_old_successful_json_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator, run = setup_run(tmp_path)
    path = orchestrator.task_dir(run, "D1.make") / "output.json"
    original_resolve, original_stat, original_read = orchestrator._resolve_task_inputs, module.os.stat, Path.read_text
    deny = False

    def resolve(manifest, run_id, task_id, **kwargs):
        nonlocal deny
        result = original_resolve(manifest, run_id, task_id, **kwargs)
        if kwargs.get("reads") is not None and task_id == TASK_IDS[0]:
            deny = True
        return result

    def stat(name, *args, **kwargs):
        if deny and Path(name) == path:
            raise PermissionError(errno.EACCES, "injected permission change", str(name))
        return original_stat(name, *args, **kwargs)

    def read(name, *args, **kwargs):
        if deny and name == path:
            raise PermissionError(errno.EACCES, "injected permission change", str(name))
        return original_read(name, *args, **kwargs)

    monkeypatch.setattr(orchestrator, "_resolve_task_inputs", resolve)
    monkeypatch.setattr(module.os, "stat", stat)
    monkeypatch.setattr(Path, "read_text", read)
    manifest = orchestrator.advance(run)
    for task in TASK_IDS[1:]:
        assert manifest["tasks"][task]["state"] == "failed"
        assert manifest["tasks"][task]["error"] == "タスク出力を読み込めません: D1.make"
