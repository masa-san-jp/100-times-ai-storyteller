from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from storyteller.cache import (
    cache_key,
    cache_path,
    lookup_cache,
    normalize_input,
    save_cache,
)
from storyteller.manifest import write_manifest
from storyteller.orchestrator import Orchestrator
from storyteller.seed import derive_task_seed


def llm_definition(task_id: str, **extra: object) -> dict:
    definition = {
        "id": task_id,
        "version": 1,
        "kind": "llm", "element": "text",
        "output": "json",
        "card": {
            "role": "入力を確認する。",
            "steps": ["JSONを出力する。"],
            "output_example": '{"text": "..."}',
        },
    }
    definition.update(extra)
    return definition


def test_normalize_input_recurses_without_reordering_arrays() -> None:
    value = {"ｋｅｙ": ["ＡＢＣ", {"nested": "①"}], "number": 1}

    assert normalize_input(value) == {
        "key": ["ABC", {"nested": "1"}],
        "number": 1,
    }
    assert normalize_input({"items": ["a", "b"]}) != normalize_input(
        {"items": ["b", "a"]}
    )


def test_cache_key_matches_spec_canonical_json() -> None:
    input_data = {"text": "Ａ", "items": ["x", 2, None]}
    expected_payload = {
        "candidate": 0,
        "input": {"items": ["x", 2, None], "text": "A"},
        "seed": 17,
        "type": "D2.echo",
        "version": 3,
    }
    canonical = json.dumps(
        expected_payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    assert cache_key("D2.echo", 3, input_data, seed=17) == hashlib.sha256(
        canonical
    ).hexdigest()
    assert cache_key("D2.echo", 3, input_data, seed=17) != cache_key(
        "D2.echo", 3, input_data, seed=18
    )
    assert cache_key(
        "D2.echo", 3, input_data, attempt=0, share_across_runs=True
    ) == cache_key("D2.echo", 3, input_data, attempt=0, share_across_runs=True)
    assert cache_key(
        "D2.echo", 3, input_data, attempt=0, share_across_runs=True
    ) != cache_key("D2.echo", 3, input_data, attempt=1, share_across_runs=True)


def test_cache_uses_sharded_path_and_atomic_json_storage(tmp_path: Path) -> None:
    key = cache_key("D2.echo", 1, {}, seed=1)

    path = save_cache(tmp_path, key, {"text": "保存済み"})

    assert path == cache_path(tmp_path, key)
    assert path == tmp_path / "cache" / key[:2] / f"{key}.json"
    assert lookup_cache(tmp_path, key) == (True, {"text": "保存済み"})
    assert lookup_cache(tmp_path, "0" * 64) == (False, None)


def test_cache_reuse_completes_ready_llm_without_claiming(tmp_path: Path) -> None:
    definitions = {"D2.echo": llm_definition("D2.echo")}
    orchestrator = Orchestrator(tmp_path, definitions)
    run_id = orchestrator.create_run(seed=123)
    task_seed = derive_task_seed(123, "D2.echo", 0)
    key = cache_key("D2.echo", 1, {}, seed=task_seed)
    save_cache(tmp_path, key, {"text": "cached"})

    assert orchestrator.claim_next(run_id, executor_id="worker") is None
    manifest = orchestrator.load_run(run_id)
    task = manifest["tasks"]["D2.echo"]
    assert task["state"] == "done"
    assert task["cache_key"] == key
    assert task["claim"] is None
    assert json.loads(
        (tmp_path / "runs" / run_id / "tasks" / "D2.echo" / "output.json").read_text(
            encoding="utf-8"
        )
    ) == {"text": "cached"}


def test_cache_lookup_uses_the_truncated_card_input(tmp_path: Path) -> None:
    definitions = {
        "D2.echo": llm_definition(
            "D2.echo",
            inputs={
                "given": {
                    "label": "入力",
                    "from": "input",
                    "select": "input.given",
                    "required": True,
                    "truncate": "head",
                }
            },
            max_input_chars=14,
        )
    }
    orchestrator = Orchestrator(tmp_path, definitions)
    run_id = orchestrator.create_run(
        seed=123,
        input_data={"given": "abcdefghijk"},
    )
    card_inputs = {"given": "abcdefg"}
    task_seed = derive_task_seed(123, "D2.echo", 0)
    key = cache_key("D2.echo", 1, card_inputs, seed=task_seed)
    save_cache(tmp_path, key, {"text": "cached from card input"})

    assert orchestrator.claim_next(run_id, executor_id="worker") is None
    task = orchestrator.load_run(run_id)["tasks"]["D2.echo"]
    assert task["state"] == "done"
    assert task["cache_key"] == key


def test_cached_text_output_uses_the_text_output_file(tmp_path: Path) -> None:
    definitions = {
        "D2.echo": llm_definition("D2.echo", output="text")
    }
    orchestrator = Orchestrator(tmp_path, definitions)
    run_id = orchestrator.create_run(seed=123)
    task_seed = derive_task_seed(123, "D2.echo", 0)
    key = cache_key("D2.echo", 1, {}, seed=task_seed)
    save_cache(tmp_path, key, "cached text")

    assert orchestrator.claim_next(run_id, executor_id="worker") is None
    task_dir = tmp_path / "runs" / run_id / "tasks" / "D2.echo"
    assert (task_dir / "output.md").read_text(encoding="utf-8") == "cached text"
    assert not (task_dir / "output.json").exists()


def test_submit_saves_validated_output_using_card_input(tmp_path: Path) -> None:
    definitions = {
        "D2.echo": llm_definition(
            "D2.echo",
            inputs={
                "given": {
                    "label": "入力",
                    "from": "input",
                    "select": "input.given",
                    "required": True,
                    "truncate": "head",
                }
            },
            max_input_chars=14,
        )
    }
    orchestrator = Orchestrator(tmp_path, definitions)
    run_id = orchestrator.create_run(
        seed=123,
        input_data={"given": "abcdefghijk"},
    )
    claimed = orchestrator.claim_next(run_id, executor_id="worker")
    assert claimed is not None
    accepted = orchestrator.submit(claimed["ticket"], '{"text": "accepted"}')
    assert accepted.accepted

    task = orchestrator.load_run(run_id)["tasks"]["D2.echo"]
    card_inputs = {"given": "abcdefg"}
    task_seed = derive_task_seed(123, "D2.echo", 0)
    expected_key = cache_key("D2.echo", 1, card_inputs, seed=task_seed)
    assert task["cache_key"] == expected_key
    assert lookup_cache(tmp_path, expected_key) == (
        True,
        {"text": "accepted"},
    )


def test_invalidation_changes_the_key_used_after_a_cached_submission(
    tmp_path: Path,
) -> None:
    definitions = {"D2.echo": llm_definition("D2.echo")}
    orchestrator = Orchestrator(tmp_path, definitions)
    run_id = orchestrator.create_run(seed=123)
    claimed = orchestrator.claim_next(run_id, executor_id="worker")
    assert claimed is not None
    assert orchestrator.submit(claimed["ticket"], '{"text": "accepted"}').accepted
    old_key = orchestrator.load_run(run_id)["tasks"]["D2.echo"]["cache_key"]

    invalidated = orchestrator.invalidate_task(
        run_id,
        "D2.echo",
        reason="検査で差し戻し",
    )
    assert invalidated["tasks"]["D2.echo"]["attempt"] == 1
    assert invalidated["tasks"]["D2.echo"]["cache_key"] is None

    replacement = orchestrator.claim_next(run_id, executor_id="worker-2")
    assert replacement is not None
    current = orchestrator.load_run(run_id)["tasks"]["D2.echo"]
    assert current["cache_key"] != old_key


def test_candidate_number_is_taken_from_task_id(tmp_path: Path) -> None:
    definitions = {"D2.echo": llm_definition("D2.echo")}
    orchestrator = Orchestrator(tmp_path, definitions)
    run_id = orchestrator.create_run(
        seed=123,
        task_specs=[
            {
                "task_id": "D2.echo-c2",
                "type": "D2.echo",
                "index": ["c2"],
            }
        ],
    )
    task_seed = derive_task_seed(123, "D2.echo-c2", 0)
    key = cache_key("D2.echo", 1, {}, candidate=2, seed=task_seed)
    save_cache(tmp_path, key, {"text": "candidate two"})

    assert orchestrator.claim_next(run_id, executor_id="worker") is None
    task = orchestrator.load_run(run_id)["tasks"]["D2.echo-c2"]
    assert task["state"] == "done"
    assert task["cache_key"] == key


def test_share_across_runs_uses_attempt_instead_of_task_seed(tmp_path: Path) -> None:
    definitions = {
        "D2.echo": llm_definition("D2.echo", share_across_runs=True)
    }
    orchestrator = Orchestrator(tmp_path, definitions)
    first_run = orchestrator.create_run(seed=123)
    second_run = orchestrator.create_run(seed=456)
    shared_key = cache_key("D2.echo", 1, {}, attempt=0, share_across_runs=True)
    save_cache(tmp_path, shared_key, {"text": "shared"})

    assert orchestrator.claim_next(first_run, executor_id="worker-1") is None
    assert orchestrator.claim_next(second_run, executor_id="worker-2") is None
    assert orchestrator.load_run(second_run)["tasks"]["D2.echo"]["state"] == "done"


def test_invalidated_attempt_does_not_reuse_previous_cache(tmp_path: Path) -> None:
    definitions = {"D2.echo": llm_definition("D2.echo")}
    orchestrator = Orchestrator(tmp_path, definitions)
    run_id = orchestrator.create_run(seed=123)
    old_seed = derive_task_seed(123, "D2.echo", 0)
    old_key = cache_key("D2.echo", 1, {}, seed=old_seed)
    save_cache(tmp_path, old_key, {"text": "old"})

    manifest = orchestrator.load_run(run_id)
    manifest["tasks"]["D2.echo"]["attempt"] = 1
    manifest["tasks"]["D2.echo"]["invalidations"] = 1
    write_manifest(tmp_path / "runs" / run_id / "manifest.json", manifest)

    claimed = orchestrator.claim_next(run_id, executor_id="worker")

    assert claimed is not None
    current = orchestrator.load_run(run_id)["tasks"]["D2.echo"]
    assert current["state"] == "claimed"
    assert current["cache_key"] != old_key


def test_cache_key_requires_attempt_or_seed_for_selected_scope() -> None:
    with pytest.raises(ValueError):
        cache_key("D2.echo", 1, {})
    with pytest.raises(ValueError):
        cache_key("D2.echo", 1, {}, seed=1, share_across_runs=True)
