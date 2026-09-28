from __future__ import annotations

from pathlib import Path

import pytest

from storyteller.scale import ScaleError, derive_scale


ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    "preset, expected_events",
    [
        ("vignette", (3, 5)),
        ("short", (8, 12)),
        ("novella", (18, 24)),
        ("novel", (36, 48)),
        ("saga", (36, 48)),
    ],
)
def test_all_presets_derive_events_inside_the_table_range(
    preset: str, expected_events: tuple[int, int]
) -> None:
    result = derive_scale(preset, seed=7, repository_root=ROOT)

    events = result.value["derived"]["events"]
    assert expected_events[0] <= events <= expected_events[1]
    assert result.value["derived"]["world_sections"]
    assert set(result.value["derived"]["pool_need"]) == {
        "want", "ability", "duty", "taboo", "place", "era", "object",
        "age", "gender", "species",
    }


def test_axis_override_can_raise_the_event_range_and_records_warning() -> None:
    result = derive_scale(
        "short",
        overrides={"space": "world"},
        seed=7,
        repository_root=ROOT,
    )

    assert result.value["axes"]["space"] == "world"
    assert result.value["derived"]["events"] >= 36
    assert result.warnings


def test_subthread_event_reserve_uses_six_events_per_maximum_subthread() -> None:
    result = derive_scale(
        "vignette",
        overrides={"threads": "main+1-2"},
        seed=7,
        repository_root=ROOT,
    )

    assert result.value["derived"]["events"] >= 15
    assert result.warnings


def test_scale_derivation_is_deterministic_for_the_same_seed() -> None:
    first = derive_scale("short", seed=123, repository_root=ROOT)
    second = derive_scale("short", seed=123, repository_root=ROOT)

    assert first == second


def test_parts_are_required_only_for_parts_scale() -> None:
    result = derive_scale("saga", seed=123, parts=5, repository_root=ROOT)
    assert result.value["derived"]["parts"] == 5
    assert result.value["derived"]["threads"] == 5

    with pytest.raises(ScaleError):
        derive_scale("short", seed=123, parts=3, repository_root=ROOT)
