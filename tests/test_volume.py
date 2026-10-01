from __future__ import annotations

from pathlib import Path

import pytest

from storyteller.scale import derive_scale
from storyteller.volume import (
    VolumeError,
    apply_volume_update,
    compute_character_volume,
    compute_initial_volume,
    multiplier_for_preset,
)


ROOT = Path(__file__).parents[1]
_PRESETS = ("vignette", "short", "novella", "novel", "saga")
_OTHER_ROLES = ("messenger", "supporter", "adversary", "bystander")


@pytest.mark.parametrize(
    "preset, multiplier",
    [("vignette", 1), ("short", 1), ("novella", 2), ("novel", 4), ("saga", 8)],
)
def test_multiplier_for_preset_matches_the_table(preset: str, multiplier: int) -> None:
    assert multiplier_for_preset(preset, repository_root=ROOT) == multiplier


def test_multiplier_for_unknown_preset_is_rejected() -> None:
    with pytest.raises(VolumeError):
        multiplier_for_preset("not-a-preset", repository_root=ROOT)


@pytest.mark.parametrize("preset", _PRESETS)
def test_initial_volume_meets_the_world_and_story_floor_for_every_scale(preset: str) -> None:
    scale = derive_scale(preset, seed=7, repository_root=ROOT).value
    multiplier = multiplier_for_preset(preset, repository_root=ROOT)

    volume = compute_initial_volume(scale, repository_root=ROOT)

    assert volume["multiplier"] == multiplier
    assert volume["world"]["total_chars"] >= 100_000 * multiplier
    assert volume["story"]["total_chars"] >= 100_000 * multiplier
    assert len(volume["story"]["scene_counts"]) == scale["derived"]["events"]
    assert all(count >= 1 for count in volume["story"]["scene_counts"])
    for section in volume["world"]["sections"]:
        if section["kind"] == "single":
            assert all(viewpoint["facet_count"] >= 1 for viewpoint in section["viewpoints"])
        else:
            assert section["item_count"] >= 1


@pytest.mark.parametrize("preset", _PRESETS)
def test_initial_volume_is_deterministic_given_the_same_scale(preset: str) -> None:
    scale = derive_scale(preset, seed=42, repository_root=ROOT).value

    first = compute_initial_volume(scale, repository_root=ROOT)
    second = compute_initial_volume(scale, repository_root=ROOT)

    assert first == second


def test_initial_volume_rejects_a_scale_with_no_single_world_section() -> None:
    scale = {
        "preset": "short",
        "axes": {"space": "town", "change": "relation"},
        "derived": {"events": 5, "world_sections": ["people"]},
    }

    with pytest.raises(VolumeError):
        compute_initial_volume(scale, repository_root=ROOT)


@pytest.mark.parametrize("preset", _PRESETS)
def test_character_volume_meets_the_characters_floor_for_every_scale(preset: str) -> None:
    scale = derive_scale(preset, seed=11, repository_root=ROOT).value
    multiplier = multiplier_for_preset(preset, repository_root=ROOT)
    cast_count = scale["derived"]["cast"]
    characters = [{"id": "c1", "role": "protagonist"}] + [
        {"id": f"c{index + 2}", "role": _OTHER_ROLES[index % len(_OTHER_ROLES)]}
        for index in range(cast_count - 1)
    ]

    result = compute_character_volume(scale, characters, repository_root=ROOT)

    assert len(result["characters"]) == cast_count
    total = sum(character["target_chars"] for character in result["characters"])
    assert total >= 10_000 * multiplier

    protagonist = next(c for c in result["characters"] if c["role"] == "protagonist")
    others = [c for c in result["characters"] if c["role"] != "protagonist"]
    assert protagonist["weight"] == 3
    if others:
        assert others[0]["weight"] == 1
        assert protagonist["target_chars"] >= others[0]["target_chars"]
    for character in result["characters"]:
        assert character["item_counts"]["backstory"] >= 2
        assert character["item_counts"]["relationship"] == cast_count - 1


def test_character_volume_handles_a_solo_protagonist_cast() -> None:
    scale = {"preset": "vignette"}
    characters = [{"id": "c1", "role": "protagonist"}]

    result = compute_character_volume(scale, characters, repository_root=ROOT)

    assert len(result["characters"]) == 1
    assert result["characters"][0]["item_counts"]["relationship"] == 0
    assert sum(c["target_chars"] for c in result["characters"]) >= 10_000


def test_character_volume_requires_at_least_one_character() -> None:
    with pytest.raises(VolumeError):
        compute_character_volume({"preset": "vignette"}, [], repository_root=ROOT)


def test_apply_volume_update_merges_without_dropping_existing_keys() -> None:
    manifest = {
        "scale": {
            "preset": "short",
            "derived": {"events": 10, "volume": {"world": {"target_chars": 100_000}}},
        }
    }
    update = {"characters": [{"id": "c1", "target_chars": 5000}]}

    updated = apply_volume_update(manifest, update)

    volume = updated["scale"]["derived"]["volume"]
    assert volume["world"] == {"target_chars": 100_000}
    assert volume["characters"] == [{"id": "c1", "target_chars": 5000}]
    # the original manifest is untouched
    assert "characters" not in manifest["scale"]["derived"]["volume"]
