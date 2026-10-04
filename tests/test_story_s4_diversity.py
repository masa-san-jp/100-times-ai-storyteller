from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from storyteller.manifest import load_manifest, write_manifest
from storyteller.new_run import create_story_orchestrator
from storyteller.orchestrator import TaskSpec
from storyteller.story_s4 import story_s4_diversity
from storyteller.tables import load_table
from storyteller.validation import SchemaValidationError, validate_document


ROOT = Path(__file__).parents[1]


def _context(tmp_path: Path, bodies: list[str], counts: list[int] | None = None):
    harness = create_story_orchestrator(tmp_path / "data")
    facet_ids = [f"S4.section-place-1-f{number}" for number in range(1, len(bodies) + 1)]
    run_id = harness.create_run(
        seed=22,
        task_specs=[
            TaskSpec("S2.merge", "S2.merge"),
            TaskSpec("S3.assign", "S3.assign", deps=("S2.merge",)),
        ] + [
            TaskSpec(task_id, "S4.section", deps=("S3.assign",), index=(task_id.removeprefix("S4.section-"),))
            for task_id in facet_ids
        ] + [TaskSpec("S4.diversity-place", "S4.diversity", deps=tuple(facet_ids), index=("place",))],
    )
    manifest = harness.load_run(run_id)
    for task_id, count in zip(facet_ids, counts or [0] * len(bodies)):
        manifest["tasks"][task_id]["invalidations"] = count
    write_manifest(harness.run_dir(run_id) / "manifest.json", manifest)
    # Reverse insertion order proves that task_id order, rather than mapping
    # or actual completion order, decides which facet is rewritten.
    outputs = dict(reversed(list(zip(facet_ids, ({"body": body} for body in bodies)))))
    return SimpleNamespace(
        run_dir=harness.run_dir(run_id), dependency_outputs=outputs,
        harness=harness, run_id=run_id,
    ), facet_ids


def test_similar_openings_invalidate_later_ids_once_with_all_reasons(tmp_path: Path):
    opening = "丘の頂上に立つと、風が皮膚に触れる。" * 6
    context, ids = _context(tmp_path, [opening, opening, opening])

    result = story_s4_diversity(context)

    assert [task_id for task_id, _ in result.invalidations] == ids[1:]
    assert result.manifest_updates == {}
    for task_id, reason in result.invalidations:
        assert opening[:80] in reason
        assert "この書き出しと似ないように書き始める" in reason
    assert ids[0] in result.invalidations[1][1]
    assert ids[1] in result.invalidations[1][1]


def test_distinct_openings_are_kept_even_if_the_remaining_body_matches(tmp_path: Path):
    tail = "同じ場所についての共通する本文。" * 100
    context, _ = _context(tmp_path, ["あ" * 80 + tail, "い" * 80 + tail])

    result = story_s4_diversity(context)

    assert result.invalidations == []
    assert result.manifest_updates == {}


@pytest.mark.parametrize("count, invalidated", [(0, True), (1, True), (2, False)])
def test_retry_limit_warns_and_keeps_last_output(tmp_path: Path, count: int, invalidated: bool):
    opening = "風が皮膚を撫でる丘。" * 10
    context, ids = _context(tmp_path, [opening, opening], [0, count])

    result = story_s4_diversity(context)

    assert bool(result.invalidations) is invalidated
    if invalidated:
        assert result.manifest_updates == {}
    else:
        warnings = result.manifest_updates["warnings"]
        assert len(warnings) == 1
        assert ids[1] in warnings[0]
        assert "無効化上限（2回）" in warnings[0]
        assert "最後の出力を採用" in warnings[0]
        # Rechecking an adopted opening must not duplicate its warning.
        manifest_path = context.run_dir / "manifest.json"
        manifest = load_manifest(manifest_path)
        manifest["warnings"].extend(warnings)
        write_manifest(manifest_path, manifest)
        assert story_s4_diversity(context).manifest_updates == {}


def _use_settings(tmp_path: Path, monkeypatch, *, threshold: float, limit: int):
    repository = tmp_path / "repository"
    for relative in (
        "schemas/tables/dedup.schema.json",
        "schemas/task-definition.schema.json",
        "harness/story/tasks/S4.section.yaml",
    ):
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((ROOT / relative).read_text(encoding="utf-8"), encoding="utf-8")
    (repository / "tables").mkdir()
    (repository / "tables/dedup.yaml").write_text(f"opening_threshold: {threshold}\nmax_copy_chars: 60\n", encoding="utf-8")
    definition_path = repository / "harness/story/tasks/S4.section.yaml"
    definition_path.write_text(
        definition_path.read_text(encoding="utf-8") + f"max_invalidations: {limit}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("storyteller.story_s4._REPOSITORY_ROOT", repository)


def test_jaccard_uses_sets_and_includes_threshold_boundary(tmp_path: Path, monkeypatch):
    # {abc, bca, cab} versus {abc, bcd, cde}: intersection 1 / union 5.
    context, ids = _context(tmp_path, ["abcabcabc", "abcde"])
    _use_settings(tmp_path, monkeypatch, threshold=0.2, limit=2)

    assert [task_id for task_id, _ in story_s4_diversity(context).invalidations] == ids[1:]
    settings = tmp_path / "repository/tables/dedup.yaml"
    settings.write_text("opening_threshold: 0.21\nmax_copy_chars: 60\n", encoding="utf-8")
    assert story_s4_diversity(context).invalidations == []


def test_configured_zero_invalidations_adopts_with_warning(tmp_path: Path, monkeypatch):
    context, _ = _context(tmp_path, ["同じ書き出し。", "同じ書き出し。"])
    _use_settings(tmp_path, monkeypatch, threshold=0.35, limit=0)

    result = story_s4_diversity(context)

    assert not result.invalidations
    assert "無効化上限（0回）" in result.manifest_updates["warnings"][0]


def test_dedup_table_has_valid_opening_threshold():
    assert load_table("dedup")["opening_threshold"] == 0.35
    for threshold in (-0.01, 1.01, "0.35", True):
        with pytest.raises(SchemaValidationError):
            validate_document({"opening_threshold": threshold, "max_copy_chars": 60}, ROOT / "schemas/tables/dedup.schema.json")


def test_real_harness_adopts_similar_output_at_limit_after_restarts(tmp_path: Path):
    body = "丘の頂上に立つと、風が皮膚に触れる。" * 50
    context, ids = _context(tmp_path, [body, body])
    harness = context.harness
    # Supply just the S3 inputs for this focused integration test; use the
    # real S4 cards, submit validation, handler, and invalidation machinery.
    harness._complete_task(context.run_id, "S2.merge", {"pools": {}})
    assignment = {
        "threads": [{"plot_type_name": "旅"}],
        "world": {
            "place": {"id": "place:t1", "text": "風が皮膚を撫でる丘"},
            "era": {"id": "era:t1", "text": "夜明けの時代"},
        },
        "world_tasks": [
            {
                "id": task_id.removeprefix("S4.section-"),
                "section": {"id": "place", "definition": "場の描写"},
                "viewpoint": "境界",
                "element": {"id": "object:t1", "text": "門の石"},
            }
            for task_id in ids
        ],
    }
    harness._complete_task(context.run_id, "S3.assign", assignment)
    submitted_ids: list[str] = []
    for _ in range(5):
        harness = create_story_orchestrator(tmp_path / "data")
        claim = harness.claim_next(context.run_id, executor_id="test")
        if claim is None:
            break
        manifest = harness.load_run(context.run_id)
        task_id = next(
            name for name, task in manifest["tasks"].items()
            if (task.get("claim") or {}).get("ticket") == claim["ticket"]
        )
        if manifest["tasks"][task_id]["invalidations"]:
            assert "この書き出しと似ないように書き始める" in claim["card"]
            assert body[:80] in claim["card"]
        assert harness.submit(claim["ticket"], body).accepted
        submitted_ids.append(task_id)
    else:
        pytest.fail("無効化上限で完走しませんでした")

    manifest = harness.load_run(context.run_id)
    assert submitted_ids == [ids[0], ids[1], ids[1], ids[1]]
    assert manifest["status"] == "completed"
    assert manifest["tasks"][ids[1]]["invalidations"] == 2
    assert manifest["tasks"][ids[1]]["state"] == "done"
    warnings = [warning for warning in manifest["warnings"] if "無効化上限" in warning]
    assert len(warnings) == 1
    assert "最後の出力を採用" in warnings[0]
    assert (harness.task_dir(context.run_id, ids[1]) / "output.md").read_text(encoding="utf-8") == body
