from pathlib import Path

from storyteller.orchestrator import Orchestrator
from storyteller.task_outputs import read_task_output, text_task_sources
from storyteller.validation import input_source_ids


def test_text_sources_use_fitted_input_and_survive_cache_reuse(tmp_path: Path) -> None:
    definition = {
        "id": "S4.section", "version": 1, "kind": "llm", "output": "text",
        "inputs": {
            "cut": {"label": "切り口", "from": "input", "select": "input.cut", "required": True},
            "unused": {"label": "補足", "from": "input", "select": "input.unused", "truncate": "drop"},
        },
        "max_input_chars": 100,
        "card": {"role": "本文を書く。", "steps": ["本文だけを書く。"]},
    }
    harness = Orchestrator(tmp_path, {"S4.section": definition})
    run_input = {
        "cut": {"id": "place:t1", "text": "水路"},
        "unused": {"id": "hidden", "text": "あ" * 1000},
    }
    run_id = harness.create_run(seed=1, input_data=run_input)
    claim = harness.claim_next(run_id, executor_id="worker")
    assert claim is not None
    assert "hidden" not in claim["card"]
    assert "[place:t1]" in claim["card"]
    text = "水路を具体的に描く。"
    assert harness.submit(claim["ticket"], text).accepted
    task_dir = harness.task_dir(run_id, "S4.section")
    assert (task_dir / "output.md").read_text(encoding="utf-8") == text
    assert not (task_dir / "output.json").exists()
    assert text_task_sources(task_dir) == ["place:t1"]
    assert read_task_output(task_dir, "S4.section") == {"body": text, "sources": ["place:t1"]}

    other = Orchestrator(tmp_path, {"S4.section": definition})
    cached_id = other.create_run(seed=1, input_data=run_input, run_id="20261003-000000-000001")
    assert other.claim_next(cached_id, executor_id="worker") is None
    assert other.load_run(cached_id)["status"] == "completed"
    assert read_task_output(other.task_dir(cached_id, "S4.section"), "S4.section") == {
        "body": text, "sources": ["place:t1"],
    }


def test_text_sources_exclude_context_identifiers() -> None:
    assert input_source_ids({
        "character": {
            "id": "c1", "elements": {"want": {"id": "want:t1", "text": "探す"}},
            "role_definition": {"id": "protagonist"}, "plot_context": {"id": "quest"},
        },
        "role_definition": {"id": "protagonist"},
        "plot_requirements": {"id": "quest"},
        "name_sound": {"set_id": "sound-01"},
    }) == ["c1", "sound-01", "want:t1"]
