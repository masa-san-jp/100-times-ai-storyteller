from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from storyteller.tables import (
    TableNameError,
    load_all_tables,
    load_table,
    table_names,
)
from storyteller.validation import SchemaValidationError, validate_document


ROOT = Path(__file__).parents[1]


def test_all_phase_one_tables_are_loaded_and_schema_validated():
    tables = load_all_tables(repository_root=ROOT)

    assert set(table_names()) == {
        "scales",
        "structures",
        "plot_types",
        "world_sections",
        "element_axes",
        "name_sounds",
        "common_words",
    }
    assert set(tables["elements"]) == {
        "want",
        "ability",
        "duty",
        "taboo",
        "place",
        "era",
        "object",
    }
    assert all(tables["elements"][axis]["items"] for axis in tables["elements"])


def test_scale_table_contains_all_presets_and_world_levels():
    scales = load_table("scales", repository_root=ROOT)

    assert set(scales["presets"]) == {
        "vignette",
        "short",
        "novella",
        "novel",
        "saga",
    }
    assert scales["level_min_events"] == [3, 6, 12, 24, 36]
    assert len(scales["world_sections_by_level"]) == 5


def test_world_sections_and_scale_levels_are_consistent():
    sections = load_table("world_sections", repository_root=ROOT)["sections"]
    scales = load_table("scales", repository_root=ROOT)
    levels = {section["id"]: section["level"] for section in sections}

    assert len(levels) == len(sections)
    assert {"events", "observation", "interpretation", "media"} <= set(levels)
    assert levels["interpretation"] == 2
    assert levels["events"] == levels["observation"] == levels["media"] == 3

    for level, section_ids in enumerate(scales["world_sections_by_level"]):
        assert set(section_ids) == {
            section_id for section_id, section_level in levels.items() if section_level <= level
        }
        assert len(section_ids) == len(set(section_ids))


def test_plot_and_structure_tables_have_phase_one_shape():
    plot_types = load_table("plot_types", repository_root=ROOT)
    structures = load_table("structures", repository_root=ROOT)

    assert len(plot_types["types"]) == 14
    assert {plot["name"] for plot in plot_types["types"]} == {
        "旅（クエスト）",
        "モンスターを倒す",
        "成り上がり",
        "再生",
        "ラブストーリー",
        "ミステリー／犯罪",
        "悲劇",
        "帰還",
        "コメディ",
        "サバイバル／ディストピア",
        "復讐",
        "陰謀・政治劇",
        "人間ドラマ",
        "哲学的／存在論的",
    }
    assert {plot["structure"] for plot in plot_types["types"]} == {"standard"}
    assert set(structures["templates"]) == {
        "three-beat",
        "kishotenketsu",
        "heros-journey-12",
    }
    assert len(structures["templates"]["heros-journey-12"]["stages"]) == 12


def test_required_events_assign_a_stage_in_each_standard_template():
    plot_types = load_table("plot_types", repository_root=ROOT)
    structures = load_table("structures", repository_root=ROOT)
    stage_ids = {
        template_id: {stage["id"] for stage in template["stages"]}
        for template_id, template in structures["templates"].items()
    }
    standard_template_ids = {"three-beat", "kishotenketsu", "heros-journey-12"}

    for plot in plot_types["types"]:
        for event in plot["required_events"]:
            assert set(event["stages"]) == standard_template_ids
            for template_id, stage_id in event["stages"].items():
                assert stage_id in stage_ids[template_id]


def test_name_sound_table_has_eight_sets_with_twelve_sounds_each():
    sounds = load_table("name_sounds", repository_root=ROOT)

    assert len(sounds["sets"]) >= 8
    assert all(len(sound_set["sounds"]) >= 12 for sound_set in sounds["sets"])


@pytest.mark.parametrize("invalid_sound", ["ャ", "ュ", "ョ", "ー", "ッ"])
def test_name_sound_schema_rejects_standalone_small_kana_and_long_mark(
    invalid_sound: str,
):
    sounds = load_table("name_sounds", repository_root=ROOT)
    sounds["sets"][0]["sounds"][0] = invalid_sound

    with pytest.raises(SchemaValidationError):
        validate_document(sounds, ROOT / "schemas/tables/name_sounds.schema.json")


def test_default_element_tables_are_japanese():
    for axis in ("want", "ability", "duty"):
        table = load_table(f"elements/{axis}", repository_root=ROOT)
        assert all(
            not any(char.isascii() and char.isalpha() for char in item)
            for item in table["items"]
        )


def test_default_element_tables_have_100_unique_items_and_normalized_uniqueness():
    axes = ("want", "ability", "duty", "taboo", "place", "era", "object")
    normalized_items: set[str] = set()

    for axis in axes:
        items = load_table(f"elements/{axis}", repository_root=ROOT)["items"]
        assert len(items) == 100
        assert len(set(items)) == 100

        start = 8 if axis in {"want", "ability", "duty"} else 0
        assert all(10 <= len(item) <= 40 for item in items[start:])

        for item in items:
            normalized = "".join(unicodedata.normalize("NFKC", item).split())
            assert normalized not in normalized_items
            normalized_items.add(normalized)


def test_unknown_table_name_is_rejected():
    with pytest.raises(TableNameError):
        load_table("not-a-table", repository_root=ROOT)


def test_table_loader_reports_schema_errors(tmp_path: Path):
    (tmp_path / "tables").mkdir()
    (tmp_path / "schemas" / "tables").mkdir(parents=True)
    (tmp_path / "tables" / "common_words.yaml").write_text(
        "words: [ok]\nextra: forbidden\n", encoding="utf-8"
    )
    (tmp_path / "schemas" / "tables" / "common_words.schema.json").write_text(
        (ROOT / "schemas/tables/common_words.schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    with pytest.raises(SchemaValidationError):
        load_table("common_words", repository_root=tmp_path)


def test_table_schemas_are_valid_json_schema_documents():
    for schema_path in (ROOT / "schemas" / "tables").glob("*.schema.json"):
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
