import json
from pathlib import Path

import pytest

from storyteller.cards import generate_task_card
from storyteller.new_run import create_story_orchestrator
from storyteller.validation import validate_output

ROOT = Path(__file__).parents[1]
INPUTS = {"name_sound": {"sounds": ["ゴク", "オ", "ケ", "ヒ", "マ", "ネ"], "description": "静かな響き"}}


def test_name_card_explicitly_forbids_spaces(tmp_path):
    task = create_story_orchestrator(tmp_path / "data").task_definitions["S5.name"]
    card = generate_task_card(task, "ticket", inputs=INPUTS)
    assert "reading に空白・数字・ひらがなを入れない" in card
    assert "中黒「・」だけを使う" in card
    assert "省略や変形をせず" in card


@pytest.mark.parametrize("reading,passed", [("ゴク オ", False), ("ゴク・オ", True), ("ゴクオ", True)])
def test_existing_name_schema_keeps_space_readings_invalid(tmp_path, reading, passed):
    task = create_story_orchestrator(tmp_path / "data").task_definitions["S5.name"]
    result = validate_output(task, json.dumps({"name": "悟久尾", "reading": reading}, ensure_ascii=False), INPUTS,
                             harness_root=ROOT)
    assert result.passed is passed, result.errors
    if not passed:
        assert any("schema" in error and "reading" in error for error in result.errors), result.errors
