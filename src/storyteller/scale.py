"""Deterministic scale derivation for S0."""

from __future__ import annotations

import math
import random
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .tables import load_table


SCALE_AXES = ("time", "space", "cast", "threads", "change")
_CAST_RANGE = re.compile(r"^(\d+)-(\d+)$")


class ScaleError(ValueError):
    """A scale preset or axis override is invalid."""


@dataclass(frozen=True)
class ScaleResult:
    """The manifest-ready representation of a derived scale."""

    value: dict[str, Any]
    warnings: tuple[str, ...]


def derive_scale(
    preset: str,
    *,
    overrides: Mapping[str, str] | None = None,
    seed: int,
    parts: int | None = None,
    repository_root: str | Path | None = None,
) -> ScaleResult:
    """Calculate all scale values using a run-local deterministic RNG."""

    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ScaleError("seed は整数で指定してください")
    scales = load_table("scales", repository_root=repository_root)
    presets = scales["presets"]
    if preset not in presets:
        raise ScaleError(f"未知の規模プリセットです: {preset}")
    preset_data = presets[preset]
    provided_overrides = dict(overrides or {})
    axes = dict(preset_data["axes"])
    for axis, value in provided_overrides.items():
        if axis not in SCALE_AXES:
            raise ScaleError(f"未知の規模の軸です: {axis}")
        values = {entry["value"] for entry in scales["axes"][axis]["values"]}
        if value not in values:
            raise ScaleError(f"{axis} の値が不正です: {value}")
        axes[axis] = value

    levels = {
        axis: _axis_level(scales["axes"][axis], axes[axis]) for axis in SCALE_AXES
    }
    rng = random.Random(seed)
    lower_bound = max(
        max(scales["level_min_events"][level] for level in levels.values()),
        3 + 6 * _max_subthreads(axes["threads"]),
    )
    original_min, original_max = preset_data["event_range"]
    warnings: list[str] = []
    if original_min < lower_bound:
        event_min = lower_bound
        event_max = math.ceil(lower_bound * 1.33)
        warnings.append(
            f"出来事数の範囲を下限 {lower_bound} に合わせて引き上げました"
        )
    else:
        event_min, event_max = original_min, original_max

    threads = _choose_threads(axes["threads"], rng)
    cast = _choose_cast(axes["cast"], rng)
    resolved_parts: int | None = None
    if axes["threads"] == "parts":
        minimum = scales["parts"]["min"]
        maximum = scales["parts"]["max"]
        if parts is None:
            default_min, default_max = scales["parts"]["default"]
            resolved_parts = rng.randint(default_min, default_max)
        elif isinstance(parts, bool) or not isinstance(parts, int) or not minimum <= parts <= maximum:
            raise ScaleError(f"部数は{minimum}〜{maximum}の整数で指定してください")
        else:
            resolved_parts = parts
        threads = resolved_parts
    elif parts is not None:
        raise ScaleError("--parts は threads=parts の規模でだけ指定できます")

    max_world_level = max(levels["space"], levels["change"])
    world_sections: list[str] = []
    for section_ids in scales["world_sections_by_level"][: max_world_level + 1]:
        for section_id in section_ids:
            if section_id not in world_sections:
                world_sections.append(section_id)

    element_axes = tuple(
        axis["key"]
        for axis in load_table("element_axes", repository_root=repository_root)["axes"]
    )
    pool_need = _pool_needs(cast, int(scales["candidate_multiplier"]), element_axes)
    return ScaleResult(
        value={
            "preset": preset,
            "axes": axes,
            "overrides": provided_overrides,
            "derived": {
                "events": rng.randint(event_min, event_max),
                "threads": threads,
                "cast": cast,
                "parts": resolved_parts,
                "world_sections": world_sections,
                "pool_need": pool_need,
            },
        },
        warnings=tuple(warnings),
    )


def _axis_level(axis: Mapping[str, Any], value: str) -> int:
    for entry in axis["values"]:
        if entry["value"] == value:
            return int(entry["level"])
    raise ScaleError(f"規模の軸の値が見つかりません: {value}")


def _max_subthreads(value: str) -> int:
    if value == "main+1-2":
        return 2
    if value == "main+3-5":
        return 5
    return 0


def _choose_threads(value: str, rng: random.Random) -> int:
    if value == "single":
        return 1
    if value == "main+1-2":
        return rng.randint(2, 3)
    if value == "main+3-5":
        return rng.randint(4, 6)
    if value == "parts":
        return 0
    raise ScaleError(f"筋の値を処理できません: {value}")


def _choose_cast(value: str, rng: random.Random) -> int:
    match = _CAST_RANGE.fullmatch(value)
    if match:
        return rng.randint(int(match.group(1)), int(match.group(2)))
    if value.endswith("+") and value[:-1].isdigit():
        return int(value[:-1])
    raise ScaleError(f"人物数の値を処理できません: {value}")


def _pool_needs(cast: int, candidate_multiplier: int, axes: tuple[str, ...]) -> dict[str, int]:
    """Reserve three candidates for each S3 allocation.

    Character axes are allocated once per character in S3.  The world/event
    axes have one allocation at this stage; later stages expand their use.
    """

    character_axes = {"want", "ability", "duty", "age", "gender", "species"}
    return {
        axis: candidate_multiplier * (cast if axis in character_axes else 1)
        for axis in axes
    }
