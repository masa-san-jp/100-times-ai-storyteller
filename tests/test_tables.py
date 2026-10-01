from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from storyteller.tables import (
    TableNameError,
    element_id,
    element_rows,
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
        "roles",
        "cliches",
        "volume",
        "beats",
    }
    assert set(tables["elements"]) == {
        "want",
        "ability",
        "duty",
        "taboo",
        "place",
        "era",
        "object",
        "age",
        "gender",
        "species",
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
    assert all(
        {"character_requirements", "time_design", "conflict", "climax", "pacing", "typical_setting", "customization_notes"}
        <= set(plot)
        for plot in plot_types["types"]
    )
    assert all(
        {"guidance", "act", "climax"} <= set(stage)
        for template in structures["templates"].values()
        for stage in template["stages"]
    )


def test_roles_cliches_world_sections_and_world_counts_are_complete():
    roles = load_table("roles", repository_root=ROOT)["roles"]
    assert [role["id"] for role in roles] == [
        "protagonist", "messenger", "supporter", "adversary", "bystander",
    ]
    assert all(role["definition"] for role in roles)

    cliches = load_table("cliches", repository_root=ROOT)["phrases"]
    assert {
        "運命",
        "闇の力",
        "守るべきもの",
        "火を操る",
        "透明化",
        "怪力",
        "読心",
        "治癒",
    } <= set(cliches)
    assert all(len(phrase) >= 2 for phrase in cliches)

    sections = load_table("world_sections", repository_root=ROOT)["sections"]
    section_ids = {section["id"] for section in sections}
    assert {"past_events", "social_groups", "people", "future"} <= section_ids
    assert {section["id"] for section in sections if section["kind"] == "list"} == {
        "past_events", "social_groups", "people", "future",
    }
    assert all(len(section["viewpoints"]) >= 3 for section in sections)
    assert all(set(section["prerequisites"]) <= section_ids for section in sections)

    counts = load_table("scales", repository_root=ROOT)["world_counts"]
    assert set(counts) == {"past_events", "social_groups", "people", "future"}
    assert all(len(values) == 5 for values in counts.values())


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
    for axis in ("want", "ability", "duty", "age", "gender", "species"):
        table = load_table(f"elements/{axis}", repository_root=ROOT)
        assert all(
            not any(char.isascii() and char.isalpha() for char in text)
            for item in table["items"]
            for text in [item["text"]]
        )


def test_default_element_tables_have_expected_unique_items_and_normalized_uniqueness():
    axes = (
        "want", "ability", "duty", "taboo", "place", "era", "object",
        "age", "gender", "species",
    )
    normalized_items: set[str] = set()

    for axis in axes:
        items = load_table(f"elements/{axis}", repository_root=ROOT)["items"]
        expected = {"want": 100, "ability": 100, "duty": 99, "age": 16,
                    "gender": 10, "species": 50}.get(axis, 100)
        assert len(items) == expected
        texts = [item["text"] for item in items]
        assert len(set(texts)) == expected

        for item in items:
            if axis in {"want", "ability", "duty", "age", "gender", "species"}:
                assert item.get("source")
            else:
                assert set(item) == {"text"}

        for item in items:
            normalized = "".join(unicodedata.normalize("NFKC", item["text"]).split())
            assert normalized not in normalized_items
            normalized_items.add(normalized)


def test_heroes_sources_are_preserved_and_ids_are_stable():
    source_axes = {
        "want": (100, "I want to find the person who stole my shadow on my tenth birthday.", "I want to teach the world to see ghosts without fear."),
        "ability": (100, "Can reverse causality through rhythmic movement", "Can turn memories into edible sweets"),
        "duty": (99, "Nostalgic Experience Designer. Designs shared memories people can revisit like theme parks", "Mushroom Forest Postmaster. Delivers mail through underground fungal networks"),
        "age": (16, "Prepubescent", "Frozen at nineteen for two hundred years"),
        "gender": (10, "Male", "Two-spirit"),
        "species": (50, "Human", "Yokai living in a public bath"),
    }
    for axis, (count, first_source, last_source) in source_axes.items():
        rows = element_rows(axis, repository_root=ROOT)
        assert len(rows) == count
        assert [row["id"] for row in rows] == [f"{axis}:t{i}" for i in range(1, count + 1)]
        assert rows[0]["source"] == first_source
        assert rows[-1]["source"] == last_source
        assert all(row["text"] for row in rows)
        assert len({row["source"] for row in rows}) == count


def test_element_id_requires_one_based_row_number():
    assert element_id("want", 1) == "want:t1"
    with pytest.raises(ValueError):
        element_id("want", 0)


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


def test_volume_table_has_the_adr_0007_floor_and_the_spec_task_ranges():
    volume = load_table("volume", repository_root=ROOT)

    assert volume["floor_chars"] == {"characters": 10000, "world": 100000, "story": 100000}
    assert volume["task_chars"]["story_beat"] == [1500, 2500]
    assert volume["task_chars"]["world_facet"] == [800, 1500]
    assert volume["task_chars"]["world_list_item"] == [600, 1200]
    assert set(volume["task_chars"]["character"]) == {
        "profile", "backstory", "relationship", "appearance", "personality",
        "values", "voice", "inner_conflict", "motive", "intro", "catchphrase",
    }
    assert all(
        bounds[0] < bounds[1]
        for bounds in volume["task_chars"]["character"].values()
    )
    assert volume["character_weight"] == {"protagonist": 3, "default": 1}


def test_scale_volume_multiplier_covers_every_preset():
    scales = load_table("scales", repository_root=ROOT)

    assert set(scales["volume_multiplier"]) == set(scales["presets"])
    assert scales["volume_multiplier"] == {
        "vignette": 1, "short": 1, "novella": 2, "novel": 4, "saga": 8,
    }


def test_beats_table_sequences_only_reference_known_beats():
    beats_table = load_table("beats", repository_root=ROOT)
    beat_ids = {beat["id"] for beat in beats_table["beats"]}

    assert set(beats_table["sequence"]) <= beat_ids
    assert {beat["id"] for beat in beats_table["beats"] if beat["repeatable"]} == {"development"}
    for entry in beats_table["short_sequences"]:
        assert set(entry["beats"]) <= beat_ids
        assert len(entry["beats"]) == entry["scene_count"]


def test_table_schemas_are_valid_json_schema_documents():
    for schema_path in (ROOT / "schemas" / "tables").glob("*.schema.json"):
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
