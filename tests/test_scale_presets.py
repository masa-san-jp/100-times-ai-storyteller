"""P2-07: novella / novel / saga expansion through S0 and S3."""

from __future__ import annotations

from pathlib import Path

import pytest

from storyteller.cli import main
from storyteller.tables import load_table
from storyteller.scale import ScaleError, derive_scale
from tests.test_story_s3 import _assignment, _rich_pools, _run

ROOT = Path(__file__).parents[1]


def _expand(tmp_path: Path, preset: str, seed: int, **kwargs):
    scale = derive_scale(preset, seed=seed, repository_root=ROOT, **kwargs).value
    orchestrator, run_id = _run(
        tmp_path / "data", seed, run_number=1, scale=scale, pools=_rich_pools()
    )
    return scale, _assignment(orchestrator, run_id)


_PLOT_STRUCTURE = {
    plot["id"]: plot["structure"]
    for plot in load_table("plot_types", repository_root=ROOT)["types"]
}


def _template(events: int, plot_type: str) -> str:
    if _PLOT_STRUCTURE[plot_type] != "standard":
        return _PLOT_STRUCTURE[plot_type]
    return "three-beat" if events <= 5 else "kishotenketsu" if events <= 11 else "heros-journey-12"


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize(
    "preset, thread_range",
    [("novella", (2, 3)), ("novel", (4, 6))],
)
def test_main_plus_subthreads_are_expanded(
    tmp_path: Path, preset: str, thread_range: tuple[int, int], seed: int
) -> None:
    scale, assignment = _expand(tmp_path, preset, seed)
    threads = assignment["threads"]

    assert thread_range[0] <= len(threads) <= thread_range[1]
    assert sum(t["events"] for t in threads) == scale["derived"]["events"]
    assert threads[0]["kind"] == "main"
    assert threads[0]["events"] >= 3
    assert threads[0]["structure"] == _template(threads[0]["events"], threads[0]["plot_type"])
    for side in threads[1:]:
        assert side["kind"] == "subthread"
        assert 4 <= side["events"] <= 6
        assert side["structure"] == "kishotenketsu"
    assert len(assignment["cast"]) == scale["derived"]["cast"]


@pytest.mark.parametrize("seed", [1, 2])
def test_saga_decides_events_per_part(tmp_path: Path, seed: int) -> None:
    scale, assignment = _expand(tmp_path, "saga", seed, parts=3)
    derived = scale["derived"]
    threads = assignment["threads"]

    assert derived["parts"] == 3 and len(derived["part_events"]) == 3
    assert all(36 <= n <= 48 for n in derived["part_events"])
    assert derived["events"] == sum(derived["part_events"])
    assert [t["part"] for t in threads] == [1, 2, 3]
    assert [t["events"] for t in threads] == derived["part_events"]
    assert all(t["kind"] == "main" for t in threads)
    assert all(t["structure"] == _template(t["events"], t["plot_type"]) for t in threads)


def test_saga_default_parts_come_from_the_seed_within_2_to_4() -> None:
    counts = {
        derive_scale("saga", seed=s, repository_root=ROOT).value["derived"]["parts"]
        for s in range(40)
    }
    assert counts <= {2, 3, 4} and len(counts) > 1


def test_axis_override_to_parts_requires_parts_and_raises_the_floor() -> None:
    result = derive_scale(
        "short", overrides={"threads": "parts"}, seed=5, parts=2, repository_root=ROOT
    )
    assert result.value["derived"]["parts"] == 2
    assert all(n >= 36 for n in result.value["derived"]["part_events"])
    assert result.warnings
    with pytest.raises(ScaleError):
        derive_scale("novel", seed=5, parts=3, repository_root=ROOT)
    with pytest.raises(ScaleError):
        derive_scale("saga", seed=5, parts=7, repository_root=ROOT)


@pytest.mark.parametrize("preset", ["novella", "novel", "saga"])
def test_cli_accepts_the_larger_presets(preset: str, tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("STORYTELLER_HOME", str(tmp_path / "data"))
    free = tmp_path / "in.txt"
    free.write_text("夜の港町で、灯台守の少女が古い手紙を見つける。" * 5, encoding="utf-8")
    code = main(["new", "--free", str(free), "--scale", preset, "--seed", "7"])
    assert code == 0
    assert capsys.readouterr().out.strip()
