"""Volume allocation for ADR-0007 and story-pipeline.md §8.

This module turns the per-task character-count targets in
``tables/volume.yaml`` into concrete allocations:

- how many facets to write per world-section viewpoint, and how many items
  the list-kind world sections already carry (``compute_initial_volume``);
- how many scenes (beats) to write per event (``compute_initial_volume``);
- how many items (and of what length, including the number of backstory
  periods and relationship entries) to write per character
  (``compute_character_volume``).

The world and story allocations only need values the scale derivation
already computes at ``st new`` time (S0): the selected world sections and
the event count. The per-character allocation needs each character's role,
which S3 decides, so it is a separate function that can be called again once
S3 has produced the cast. Both write to the same manifest location,
``scale.derived.volume``; :func:`apply_volume_update` merges a partial
result into it without disturbing what is already there.

Wiring these functions into S0/S3 is out of scope for this module (see
``docs/plan/phase-1.md`` P1-17 through P1-19); this module only provides the
calculation and its tests.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from .tables import load_table


class VolumeError(ValueError):
    """A volume allocation input does not match story-pipeline.md §8."""


def multiplier_for_preset(
    preset: str,
    *,
    repository_root: str | Path | None = None,
) -> int:
    """Return the scale's volume multiplier (``tables/scales.yaml``)."""

    scales = load_table("scales", repository_root=repository_root)
    multipliers = scales["volume_multiplier"]
    if preset not in multipliers:
        raise VolumeError(f"未知の規模プリセットです: {preset}")
    return int(multipliers[preset])


def compute_initial_volume(
    scale: Mapping[str, Any],
    *,
    repository_root: str | Path | None = None,
) -> dict[str, Any]:
    """Compute the world and story allocation at ``st new`` time (S0).

    ``scale`` is the value a run's manifest carries at ``manifest["scale"]``
    (the output of :func:`storyteller.scale.derive_scale`). The result is
    meant to be stored at ``manifest["scale"]["derived"]["volume"]``; the
    per-character allocation is added later by
    :func:`compute_character_volume`, once S3 has assigned roles.
    """

    preset = scale.get("preset")
    if not isinstance(preset, str) or not preset:
        raise VolumeError("scale.preset が不正です")
    derived = scale.get("derived")
    if not isinstance(derived, Mapping):
        raise VolumeError("scale.derived が不正です")
    event_count = derived.get("events")
    if isinstance(event_count, bool) or not isinstance(event_count, int) or event_count < 1:
        raise VolumeError("scale.derived.events が不正です")
    selected_ids = derived.get("world_sections")
    if not isinstance(selected_ids, list) or not selected_ids:
        raise VolumeError("scale.derived.world_sections が不正です")

    multiplier = multiplier_for_preset(preset, repository_root=repository_root)
    volume_table = load_table("volume", repository_root=repository_root)
    world_sections = _resolve_world_sections(
        scale, selected_ids, repository_root=repository_root
    )

    return {
        "multiplier": multiplier,
        "world": _allocate_world(multiplier, world_sections, volume_table),
        "story": _allocate_story(multiplier, event_count, volume_table),
    }


def compute_character_volume(
    scale: Mapping[str, Any],
    characters: Sequence[Mapping[str, Any]],
    *,
    repository_root: str | Path | None = None,
) -> dict[str, Any]:
    """Compute the per-character allocation once S3 has assigned roles.

    ``characters`` is a sequence of ``{"id": ..., "role": ...}`` entries (S3's
    ``assignment.json`` cast). The result is meant to update
    ``manifest["scale"]["derived"]["volume"]["characters"]``, for example
    through :func:`apply_volume_update`.
    """

    preset = scale.get("preset")
    if not isinstance(preset, str) or not preset:
        raise VolumeError("scale.preset が不正です")
    if not characters:
        raise VolumeError("人物が1人もいません")

    multiplier = multiplier_for_preset(preset, repository_root=repository_root)
    volume_table = load_table("volume", repository_root=repository_root)
    return {"characters": _allocate_characters(multiplier, characters, volume_table)}


def apply_volume_update(
    manifest: Mapping[str, Any],
    update: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a copy of ``manifest`` with ``update`` merged into
    ``scale.derived.volume``.

    Both :func:`compute_initial_volume` (S0) and
    :func:`compute_character_volume` (S3) produce partial volume dicts meant
    for this one manifest location; this keeps the merge rule in one place
    for both call sites instead of duplicating it at each one.
    """

    result = deepcopy(dict(manifest))
    scale = dict(result.get("scale") or {})
    derived = dict(scale.get("derived") or {})
    volume = dict(derived.get("volume") or {})
    volume.update(update)
    derived["volume"] = volume
    scale["derived"] = derived
    result["scale"] = scale
    return result


# -- allocation ---------------------------------------------------------


def _median(bounds: Sequence[int]) -> int:
    """Round the midpoint of a ``[min, max]`` task-length range up.

    Rounding up (rather than to nearest) keeps the per-task estimate used
    for the floor check conservative: it never overstates how many
    characters a task's median output is expected to contribute.
    """

    return math.ceil((bounds[0] + bounds[1]) / 2)


def _resolve_world_sections(
    scale: Mapping[str, Any],
    selected_ids: Sequence[str],
    *,
    repository_root: str | Path | None,
) -> list[dict[str, Any]]:
    sections = load_table("world_sections", repository_root=repository_root)["sections"]
    section_by_id = {section["id"]: section for section in sections}
    scales = load_table("scales", repository_root=repository_root)
    level = _world_level(scale, scales)

    resolved: list[dict[str, Any]] = []
    for section_id in selected_ids:
        if not isinstance(section_id, str) or section_id not in section_by_id:
            raise VolumeError(f"未知の世界セクションです: {section_id}")
        section = section_by_id[section_id]
        if section["kind"] == "single":
            resolved.append(
                {
                    "id": section_id,
                    "kind": "single",
                    "viewpoints": list(section["viewpoints"]),
                }
            )
        else:
            counts = scales.get("world_counts", {}).get(section_id)
            if not isinstance(counts, list) or level >= len(counts):
                raise VolumeError(f"世界セクションの件数がありません: {section_id}")
            resolved.append(
                {"id": section_id, "kind": "list", "item_count": int(counts[level])}
            )
    return resolved


def _world_level(scale: Mapping[str, Any], scales: Mapping[str, Any]) -> int:
    axes = scale.get("axes")
    if not isinstance(axes, Mapping):
        raise VolumeError("scale.axes が不正です")
    levels: list[int] = []
    for axis_name in ("space", "change"):
        axis_value = axes.get(axis_name)
        for entry in scales["axes"][axis_name]["values"]:
            if entry["value"] == axis_value:
                levels.append(int(entry["level"]))
                break
        else:
            raise VolumeError(f"規模の軸の値が見つかりません: {axis_name}")
    return max(levels)


def _allocate_world(
    multiplier: int,
    world_sections: Sequence[Mapping[str, Any]],
    volume_table: Mapping[str, Any],
) -> dict[str, Any]:
    task_chars = volume_table["task_chars"]
    facet_median = _median(task_chars["world_facet"])
    list_median = _median(task_chars["world_list_item"])
    target_chars = volume_table["floor_chars"]["world"] * multiplier

    single_sections = [section for section in world_sections if section["kind"] == "single"]
    list_sections = [section for section in world_sections if section["kind"] == "list"]
    if not single_sections:
        raise VolumeError("面を割り当てる世界セクションがありません")
    total_viewpoints = sum(len(section["viewpoints"]) for section in single_sections)
    if total_viewpoints == 0:
        raise VolumeError("面を割り当てる観点がありません")

    list_total = sum(section["item_count"] * list_median for section in list_sections)
    remaining = max(0, target_chars - list_total)
    share = remaining / total_viewpoints

    sections_out: list[dict[str, Any]] = []
    total_chars = list_total
    for section in single_sections:
        viewpoints_out = []
        for viewpoint in section["viewpoints"]:
            facet_count = max(1, math.ceil(share / facet_median))
            viewpoint_chars = facet_count * facet_median
            total_chars += viewpoint_chars
            viewpoints_out.append(
                {
                    "viewpoint": viewpoint,
                    "facet_count": facet_count,
                    "facet_chars": facet_median,
                    "target_chars": viewpoint_chars,
                }
            )
        sections_out.append(
            {"id": section["id"], "kind": "single", "viewpoints": viewpoints_out}
        )
    for section in list_sections:
        sections_out.append(
            {
                "id": section["id"],
                "kind": "list",
                "item_count": section["item_count"],
                "item_chars": list_median,
                "target_chars": section["item_count"] * list_median,
            }
        )

    return {
        "target_chars": target_chars,
        "sections": sections_out,
        "total_chars": total_chars,
    }


def _allocate_story(
    multiplier: int,
    event_count: int,
    volume_table: Mapping[str, Any],
) -> dict[str, Any]:
    beat_median = _median(volume_table["task_chars"]["story_beat"])
    target_chars = volume_table["floor_chars"]["story"] * multiplier
    total_beats = max(event_count, math.ceil(target_chars / beat_median))
    base, extra = divmod(total_beats, event_count)
    scene_counts = [base + (1 if index < extra else 0) for index in range(event_count)]

    return {
        "target_chars": target_chars,
        "scene_chars": beat_median,
        "scene_counts": scene_counts,
        "total_chars": sum(scene_counts) * beat_median,
    }


def _allocate_characters(
    multiplier: int,
    characters: Sequence[Mapping[str, Any]],
    volume_table: Mapping[str, Any],
) -> list[dict[str, Any]]:
    item_ranges = volume_table["task_chars"]["character"]
    item_medians = {key: _median(bounds) for key, bounds in item_ranges.items()}
    fixed_items = sorted(key for key in item_ranges if key not in {"backstory", "relationship"})
    fixed_total = sum(item_medians[key] for key in fixed_items)

    weights_table = volume_table["character_weight"]
    protagonist_weight = weights_table["protagonist"]
    default_weight = weights_table["default"]

    weights: list[float] = []
    for character in characters:
        role = character.get("role")
        if not isinstance(role, str) or not role:
            raise VolumeError("人物の役が不正です")
        weights.append(protagonist_weight if role == "protagonist" else default_weight)
    weight_sum = sum(weights)
    if weight_sum <= 0:
        raise VolumeError("人物の重みの合計が0です")

    # Every character carries one relationship entry per other character
    # (story-pipeline.md S5): the count is fixed by cast size, not chosen to
    # help meet the floor, unlike the backstory period count below.
    relationship_count = max(0, len(characters) - 1)
    relationship_total = relationship_count * item_medians["relationship"]
    target_chars_total = volume_table["floor_chars"]["characters"] * multiplier

    allocations: list[dict[str, Any]] = []
    for character, weight in zip(characters, weights):
        person_id = character.get("id")
        if not isinstance(person_id, str) or not person_id:
            raise VolumeError("人物のIDが不正です")
        role = character["role"]
        person_target = target_chars_total * weight / weight_sum
        remaining = person_target - fixed_total - relationship_total
        # "backstory（時期ごとに複数）" requires more than one period; raise
        # the count further only to close the gap to this character's share
        # of the floor.
        backstory_count = (
            max(2, math.ceil(remaining / item_medians["backstory"])) if remaining > 0 else 2
        )

        item_counts = {key: 1 for key in fixed_items}
        item_counts["relationship"] = relationship_count
        item_counts["backstory"] = backstory_count
        item_chars = dict(item_medians)
        target_chars = sum(item_counts[key] * item_chars[key] for key in item_ranges)

        allocations.append(
            {
                "id": person_id,
                "role": role,
                "weight": weight,
                "target_chars": target_chars,
                "item_counts": item_counts,
                "item_chars": item_chars,
            }
        )
    return allocations


__all__ = [
    "VolumeError",
    "multiplier_for_preset",
    "compute_initial_volume",
    "compute_character_volume",
    "apply_volume_update",
]
