from __future__ import annotations

import json
from pathlib import Path

import pytest

from storyteller.tables import (
    TableNameError,
    load_all_tables,
    load_table,
    table_names,
)
from storyteller.validation import SchemaValidationError


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


def test_plot_and_structure_tables_have_phase_one_shape():
    plot_types = load_table("plot_types", repository_root=ROOT)
    structures = load_table("structures", repository_root=ROOT)

    assert len(plot_types["types"]) == 14
    assert {plot["structure"] for plot in plot_types["types"]} == {"standard"}
    assert set(structures["templates"]) == {
        "three-beat",
        "kishotenketsu",
        "heros-journey-12",
    }
    assert len(structures["templates"]["heros-journey-12"]["stages"]) == 12


def test_name_sound_table_has_eight_sets_with_twelve_sounds_each():
    sounds = load_table("name_sounds", repository_root=ROOT)

    assert len(sounds["sets"]) >= 8
    assert all(len(sound_set["sounds"]) >= 12 for sound_set in sounds["sets"])


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
