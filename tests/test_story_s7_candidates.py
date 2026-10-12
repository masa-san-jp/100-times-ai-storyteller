from __future__ import annotations

import json
import random
from pathlib import Path

from storyteller.batch import load_batch_manifest, trigram_jaccard
from storyteller.orchestrator import CodeTaskContext
from storyteller.story_s7 import EVENT_FIELDS, event_tasks
from storyteller.story_s7_candidates import (
    candidate_settings,
    story_s7_select,
    what_task_ids,
)
from storyteller.story_s8 import story_s8_judge
from tests.test_batch import _batch
from tests.test_story_s7_elements import _harness

ROOT = Path(__file__).parents[1]
OTHER = "水門を開いて隠れた通路を見つけ出し奥へ進む"
SIMILAR = "水門を開いて隠れた通路を見つけ出し外へ出る"
DIFFERENT = "夜の市場で古い地図を売り払い旅費にする"


def _context(data_dir: Path, run_id: str, texts: list[str]) -> CodeTaskContext:
    run_dir = data_dir / "runs" / run_id
    return CodeTaskContext(
        run_id=run_id, task_id="S7.select-e001",
        task={"index": ["e001"]}, definition={}, run_input={},
        inputs={"candidates": {
            f"e001-what-c{n}": text for n, text in enumerate(texts, start=1)
        }},
        outputs={}, dependency_outputs={}, data_dir=data_dir, run_dir=run_dir,
        seed=1, random=random.Random(1),
    )


def _selected(data_dir: Path, run_id: str, text: str) -> None:
    path = data_dir / "runs" / run_id / "tasks" / "S7.select-e001" / "output.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"candidate": "c1", "text": text}), encoding="utf-8")


def test_definition_asks_for_three_least_similar_candidates() -> None:
    assert candidate_settings() == (3, "least_similar")
    ids = [t.task_id for t in event_tasks("S6.expand", "e001", ["S6.expand"], None)]
    assert ids[:4] == [
        "S7.event-e001-what-c1", "S7.event-e001-what-c2",
        "S7.event-e001-what-c3", "S7.select-e001",
    ]
    assert len(ids) == 3 + 1 + len(EVENT_FIELDS) - 1 + 1


def test_batch_run_selects_the_least_similar_candidate(tmp_path: Path) -> None:
    data_dir, batch_id = _batch(tmp_path, 2)
    first, second = [r["run_id"] for r in load_batch_manifest(data_dir, batch_id)["runs"]]
    _selected(data_dir, first, OTHER)
    result = story_s7_select(_context(data_dir, second, [SIMILAR, OTHER, DIFFERENT]))
    assert result.output["candidate"] == "c3"
    assert result.output["text"] == DIFFERENT
    assert result.output["method"] == "least_similar"
    assert result.output["similarity"] == trigram_jaccard(DIFFERENT, OTHER)
    # The choice does not depend on the order of the candidates.
    result = story_s7_select(_context(data_dir, second, [DIFFERENT, SIMILAR, OTHER]))
    assert result.output["text"] == DIFFERENT


def test_selection_uses_the_maximum_over_all_comparands(tmp_path: Path) -> None:
    data_dir, batch_id = _batch(tmp_path, 3)
    first, second, third = [r["run_id"] for r in load_batch_manifest(data_dir, batch_id)["runs"]]
    _selected(data_dir, first, OTHER)
    _selected(data_dir, second, DIFFERENT)
    result = story_s7_select(_context(data_dir, third, [DIFFERENT, SIMILAR]))
    scores = {
        text: max(trigram_jaccard(text, OTHER), trigram_jaccard(text, DIFFERENT))
        for text in (DIFFERENT, SIMILAR)
    }
    assert result.output["text"] == min(scores, key=scores.get)


def test_batch_without_comparands_selects_randomly(tmp_path: Path) -> None:
    data_dir, batch_id = _batch(tmp_path, 2)
    first, _ = [r["run_id"] for r in load_batch_manifest(data_dir, batch_id)["runs"]]
    texts = [OTHER, SIMILAR, DIFFERENT]
    result = story_s7_select(_context(data_dir, first, texts))
    assert result.output["method"] == "random"
    assert result.output["similarity"] is None
    assert result.output["text"] == texts[random.Random(1).randrange(3)]


def test_run_outside_a_batch_selects_randomly(tmp_path: Path) -> None:
    _, run_id = _harness(tmp_path)
    texts = [OTHER, SIMILAR, DIFFERENT]
    context = _context(tmp_path / "data", run_id, texts)
    result = story_s7_select(context)
    assert result.output["method"] == "random"
    assert result.output["text"] == texts[random.Random(1).randrange(3)]


def test_what_task_ids_lists_candidates_in_order() -> None:
    tasks = {f"S7.event-e002-what-c{n}": {} for n in (3, 1, 2)}
    tasks["S7.event-e002-where"] = {}
    assert what_task_ids(tasks, "e002") == [
        "S7.event-e002-what-c1", "S7.event-e002-what-c2", "S7.event-e002-what-c3"
    ]
    assert what_task_ids({}, "e002") == ["S7.event-e002-what"]


def test_judge_invalidates_every_candidate(tmp_path: Path) -> None:
    orchestrator, run_id = _harness(tmp_path)
    orchestrator.advance(run_id)
    context = CodeTaskContext(
        run_id=run_id, task_id="S8.judge-e001", task={"index": ["e001"]},
        definition={}, run_input={},
        inputs={"comparisons": {"a": {"answer": "yes", "reason": "食い違う。"}}},
        outputs={}, dependency_outputs={}, data_dir=tmp_path / "data",
        run_dir=orchestrator.run_dir(run_id), seed=1, random=random.Random(1),
    )
    result = story_s8_judge(context)
    assert [task_id for task_id, _ in result.invalidations] == [
        f"S7.event-e001-what-c{n}" for n in (1, 2, 3)
    ]
