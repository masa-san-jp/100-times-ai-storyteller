from __future__ import annotations

import json
import random
import re
from pathlib import Path

import pytest

from storyteller.cards import generate_task_card, input_char_count, prepare_task_inputs
from storyteller.orchestrator import Orchestrator
from storyteller.story_quality import join_continuation, longest_common_substring
from storyteller.tables import load_table
from storyteller.validation import load_and_validate_yaml, validate_output


ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("length", [59, 60, 61])
@pytest.mark.parametrize("output_kind", ["text", "json"])
def test_copy_threshold_and_reported_substring(length: int, output_kind: str):
    copied = "".join(chr(0x4E00 + i) for i in range(length))
    definition = {"output": output_kind, "validate": {"checks": ["no_copy_from_inputs"]}}
    output = f"前置き{copied}結び。"
    if output_kind == "json":
        output = json.dumps({"body": output, "sources": ["p001"]}, ensure_ascii=False)
    result = validate_output(definition, output, {"prior": {"id": "p001", "body": copied}})
    assert result.passed == (length < 60)
    if not result.passed:
        assert copied in result.errors[0]


def test_copy_uses_prose_values_not_metadata_or_artificial_boundaries(tmp_path: Path):
    definition = {"output": "json", "validate": {"checks": ["no_copy_from_inputs"]}}
    copied = "あ" * 60
    assert validate_output(definition, json.dumps({"sources": [copied]}), {"text": copied}).passed
    assert validate_output(definition, json.dumps({"body": copied}), {"id": copied, "source": copied}).passed
    assert validate_output(definition, json.dumps({"body": copied}), {"parts": ["あ" * 30, "あ" * 30]}).passed
    assert not validate_output(definition, json.dumps({"body": copied}), {"role_definition": {"definition": copied}}).passed
    # NFC and the table's configured limit are applied to the actual prose.
    for relative in ("tables/dedup.yaml", "schemas/tables/dedup.schema.json"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((ROOT / relative).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "tables/dedup.yaml").write_text("opening_threshold: 0.35\nmax_copy_chars: 3\n", encoding="utf-8")
    result = validate_output(definition, json.dumps({"body": "ががが"}), {"text": "か\u3099" * 3}, harness_root=tmp_path)
    assert not result.passed


def test_longest_common_substring_against_exhaustive_reference():
    rng = random.Random(27)
    for _ in range(100):
        text = "".join(rng.choices("あいう　\t", k=15))
        others = ["".join(rng.choices("あいう　\t", k=12)) for _ in range(3)]
        expected = max((text[start:end] for start in range(len(text))
                        for end in range(start + 1, len(text) + 1)
                        if any(text[start:end] in other for other in others)),
                       key=lambda part: sum(not char.isspace() for char in part), default="")
        actual = longest_common_substring(text, others)
        assert sum(not char.isspace() for char in actual) == sum(not char.isspace() for char in expected)
        assert actual in text and any(actual in other for other in others)


@pytest.mark.parametrize("length", [59, 60])
def test_copy_counts_without_whitespace_but_matches_exact_substrings(length: int):
    definition = {"output": "text", "validate": {"checks": ["no_copy_from_inputs"]}}
    copied = "甲　\t\n" * length
    result = validate_output(definition, "前。" + copied + "後。", {"body": copied})
    assert result.passed == (length < 60)
    if not result.passed:
        assert "60字" in result.errors[0]
    # A physically longer match consisting mainly of whitespace cannot hide
    # a shorter match containing more prose characters.
    dense = "乙" * 60
    sparse = "甲" + " " * 100 + "丙"
    result = validate_output(definition, sparse + "｜" + dense, {"parts": [sparse, dense]})
    assert not result.passed and dense in result.errors[0]
    # Whitespace is excluded from the count, not from substring matching.
    assert validate_output(definition, "甲" * 60, {"body": "甲 " * 60}).passed


@pytest.mark.parametrize("term", load_table("meta_terms")["phrases"])
@pytest.mark.parametrize("output_kind", ["text", "json"])
def test_meta_terms_are_rejected(term: str, output_kind: str):
    definition = {"output": output_kind, "validate": {"checks": [
        {"avoid_listed": {"table": "tables/meta_terms.yaml"}},
    ]}}
    output = f"この{term}を説明する。"
    if output_kind == "json":
        output = json.dumps({"body": output}, ensure_ascii=False)
    result = validate_output(definition, output)
    assert not result.passed and term in result.errors[0]


def test_real_task_definitions_enable_quality_checks():
    for path in (ROOT / "harness/story/tasks").glob("*.yaml"):
        definition = load_and_validate_yaml(path, ROOT / "schemas/task-definition.schema.json")
        if definition["kind"] == "llm":
            assert "no_copy_from_inputs" in definition["validate"]["checks"]
            assert {"avoid_listed": {"table": "tables/meta_terms.yaml"}} in definition["validate"]["checks"]


def test_card_separates_nested_context_and_counts_both_groups():
    definition = load_and_validate_yaml(ROOT / "harness/story/tasks/S5.profile.yaml", ROOT / "schemas/task-definition.schema.json")
    inputs = {"character": {"id": "c1", "elements": {"want": {"id": "want:t1", "text": "橋を直したい"}},
                            "name_sound": {"set_id": "sound-01", "sounds": ["カ", "ナ"]},
                            "role_definition": {"id": "protagonist", "definition": "困難に挑む役"},
                            "plot_context": {"id": "quest", "name": "旅"}},
              "name": "カナ", "facts": {"age": 30}}
    inputs["role_definition"] = inputs["character"]["role_definition"]
    inputs["plot_requirements"] = inputs["character"]["plot_context"]
    original = json.dumps(inputs, ensure_ascii=False)
    card = generate_task_card(definition, "ticket", inputs=inputs)
    body = card.split("## 入力\n")[1].split("\n\n## 手順")[0]
    assert body.startswith("### 物語の素材")
    parts = re.split(r"### (物語の素材|作り方の指示)\n", body)
    material = "\n".join(parts[i + 1] for i in range(1, len(parts), 2) if parts[i] == "物語の素材")
    instruction = "\n".join(parts[i + 1] for i in range(1, len(parts), 2) if parts[i] == "作り方の指示")
    assert "橋を直したい" in material and "カナ" in material
    assert "name_sound" not in material and "role_definition" not in material
    assert "困難に挑む役" in instruction and "sound-01" in instruction
    assert "[quest]" not in card and "[protagonist]" not in card
    assert "作り方の指示は本文に書かず" in card
    assert input_char_count(definition, inputs) == len(body)
    labels = [slot["label"] for name, slot in definition["inputs"].items() if name in inputs]
    positions = [body.index(f"#### {label}\n") for label in labels]
    assert positions == sorted(positions)
    assert json.dumps(inputs, ensure_ascii=False) == original
    prepare_task_inputs(definition, inputs=inputs)


@pytest.mark.parametrize(("paragraph", "removed"), [
    ("abcabcabc", True), ("abcabcabca", True),  # identical 3-gram sets
    ("abcde", False),  # Jaccard 1 / 5
    ("abcdef", True),  # versus abcdefg: 4 / 5, inclusive boundary
    ("abcdeh", False),
    ("あ", True), ("い", False),  # short paragraphs have empty gram sets
])
def test_continuation_paragraph_similarity(paragraph: str, removed: bool):
    previous = "abcabcabc\n\nabcdefg\n\nあ\n\n"
    combined, warnings = join_continuation(previous, paragraph + "\n\n新しい説明。")
    assert combined == previous + ("" if removed else paragraph + "\n\n") + "新しい説明。"
    assert bool(warnings) == removed


def _continuation_definition(**extra):
    return {"id": "D1.story", "version": 1, "kind": "llm", "output": "text",
            "continuation": True, "max_input_chars": 3000,
            "card": {"role": "本文を書く。", "steps": ["本文を完結させる。"]},
            "validate": {"checks": ["ends_complete"]}, **extra}


@pytest.mark.parametrize("truncated_middle", [False, True])
def test_continuation_dedup_survives_restart_and_stores_warning(tmp_path: Path, truncated_middle: bool):
    definitions = {"D1.story": _continuation_definition()}
    harness = Orchestrator(tmp_path, definitions)
    run_id = harness.create_run(seed=27)
    paragraph = "水辺を歩く人物は、流れに沿って道を探した。"
    first = harness.claim_next(run_id, executor_id="worker")
    assert harness.submit(first["ticket"], paragraph + "\n\n", truncated=True).accepted
    harness = Orchestrator(tmp_path, definitions)
    second = harness.claim_next(run_id, executor_id="worker")
    result = harness.submit(second["ticket"], paragraph + "\n\n別の道を見つけた。", truncated=truncated_middle)
    assert result.accepted and any("重複段落を除去" in warning for warning in result.warnings)
    if truncated_middle:
        assert (harness.task_dir(run_id, "D1.story") / "partial.md").read_text(encoding="utf-8") == paragraph + "\n\n別の道を見つけた。"
        harness = Orchestrator(tmp_path, definitions)
        third = harness.claim_next(run_id, executor_id="worker")
        result = harness.submit(third["ticket"], "人物は町へ戻った。")
    expected = paragraph + "\n\n別の道を見つけた。" + ("人物は町へ戻った。" if truncated_middle else "")
    assert result.value == expected
    assert (harness.task_dir(run_id, "D1.story") / "output.md").read_text(encoding="utf-8") == expected
    manifest = harness.load_run(run_id)
    assert manifest["status"] == "completed" and any("重複段落を除去" in warning for warning in manifest["warnings"])
    assert manifest["tasks"]["D1.story"]["tries"] == 0


def test_extend_to_min_counts_after_removing_duplicate(tmp_path: Path):
    definition = _continuation_definition(extend_to_min=True, validate={"checks": [{"min_chars": {"n": 20}}]})
    harness = Orchestrator(tmp_path, {"D1.story": definition})
    run_id = harness.create_run(seed=27)
    for _ in range(2):
        claim = harness.claim_next(run_id, executor_id="worker")
        assert harness.submit(claim["ticket"], "短い文章。\n\n").accepted
        assert harness.load_run(run_id)["tasks"]["D1.story"]["state"] == "ready"
    claim = harness.claim_next(run_id, executor_id="worker")
    result = harness.submit(claim["ticket"], "短い文章。\n\n")
    assert not result.accepted and any("min_chars" in error for error in result.errors)
    assert not (harness.task_dir(run_id, "D1.story") / "partial.md").exists()
