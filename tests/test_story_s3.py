from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from storyteller.orchestrator import CodeTaskResult, Orchestrator, TaskSpec
from storyteller.story_s3 import (
    _assign_roles,
    _choose_element,
    _structure_for,
    story_s3_assign,
)
from storyteller.story_s5 import story_s5_relationship_context
from storyteller.tables import load_table
from storyteller.validation import validate_document
from storyteller.volume import multiplier_for_preset


ROOT = Path(__file__).parents[1]
ALL_S5_TYPES = (
    "S5.name",
    "S5.profile",
    "S5.intro",
    "S5.appearance",
    "S5.motive",
    "S5.personality",
    "S5.values",
    "S5.voice",
    "S5.inner_conflict",
    "S5.backstory",
    "S5.relationship",
    "S5.catchphrase",
)


def _code_definition(task_id: str, handler: str) -> dict[str, object]:
    return {"id": task_id, "version": 1, "kind": "code", "handler": handler}


def _llm_definition(task_id: str) -> dict[str, object]:
    return {
        "id": task_id,
        "version": 1,
        "kind": "llm",
        "output": "json",
        "card": {
            "role": "入力を確認する。",
            "steps": ["JSONを出力する。"],
            "output_example": '{"value": "..."}',
        },
    }


def _definitions(material_kind: str = "suppression") -> dict[str, dict[str, object]]:
    definitions: dict[str, dict[str, object]] = {
        "S1.extract": _code_definition("S1.extract", "extract"),
        "S2.merge": _code_definition("S2.merge", "merge"),
        "S3.assign": {
            "id": "S3.assign",
            "version": 1,
            "kind": "code",
            "handler": "assign",
            "inputs": {
                "pools": {
                    "label": "入力由来プール",
                    "from": "S2.merge",
                    "select": "pools",
                    "required": True,
                }
            },
        },
        "S4.section": _llm_definition("S4.section"),
        "S4.item": _llm_definition("S4.item"),
        "S4.item_name": _llm_definition("S4.item_name"),
        "S4.diversity": _code_definition("S4.diversity", "diversity"),
        "S5.relationship_context": _code_definition(
            "S5.relationship_context", "relationship_context"
        ),
        "S6.expand": _code_definition("S6.expand", "s6"),
    }
    definitions.update({task_id: _llm_definition(task_id) for task_id in ALL_S5_TYPES})
    return definitions


def _pools() -> dict[str, list[dict[str, str]]]:
    return {
        "want": [],
        "ability": [],
        "duty": [],
        "taboo": [],
        "place": [],
        "era": [],
        "object": [],
        "age": [],
        "gender": [],
        "species": [],
    }


def _make_orchestrator(
    data_dir: Path,
    *,
    material_kind: str = "suppression",
    pools: dict[str, list[dict[str, str]]] | None = None,
) -> Orchestrator:
    definitions = _definitions(material_kind)
    source_pools = pools or _pools()

    def extract(_context):
        return {
            "materials": [
                {"id": "m001", "text": "言葉にされない自己像の抑圧", "kind": material_kind},
            ]
        }

    def merge(context):
        return CodeTaskResult(
            output={"pools": source_pools},
            add_tasks=[TaskSpec("S3.assign", "S3.assign", deps=(context.task_id,))],
        )

    return Orchestrator(
        data_dir,
        definitions,
        {
            "extract": extract,
            "merge": merge,
            "assign": story_s3_assign,
            "diversity": lambda _context: {"invalidated": []},
            "relationship_context": story_s5_relationship_context,
            "s6": lambda _context: {"ok": True},
        },
        harness_root=ROOT,
        repository_root=ROOT,
    )


def _run(
    data_dir: Path,
    seed: int,
    *,
    run_number: int,
    material_kind: str = "suppression",
    plot_type: str | None = None,
    scale: dict[str, object] | None = None,
    pools: dict[str, list[dict[str, str]]] | None = None,
) -> tuple[Orchestrator, str]:
    source_pools = pools or _pools()
    orchestrator = _make_orchestrator(
        data_dir,
        material_kind=material_kind,
        pools=source_pools,
    )
    scale_value = scale or {
        "preset": "vignette",
        "axes": {
            "time": "hours",
            "space": "spot",
            "cast": "1-2",
            "threads": "single",
            "change": "inner",
        },
        "overrides": {},
        "derived": {
            "events": 3,
            "threads": 1,
            "cast": 1,
            "parts": None,
            "world_sections": ["place"],
            "pool_need": {},
        },
    }
    run_id = orchestrator.create_run(
        seed=seed,
        run_id=f"20260927-0315{run_number:02d}-{seed:06x}",
        input_data={"kind": "free", "source_sha256": "a" * 64, "paragraphs": []},
        input_type="free",
        plot_type=plot_type,
        scale=scale_value,
        table_snapshot={axis: 0 for axis in source_pools},
        task_specs=[
            TaskSpec("S1.extract-p001", "S1.extract", index=("p001",)),
            TaskSpec("S2.merge", "S2.merge", deps=("S1.extract-p001",)),
        ],
    )
    manifest = orchestrator.advance(run_id)
    assert manifest["tasks"]["S3.assign"]["state"] == "done"
    return orchestrator, run_id


def _assignment(orchestrator: Orchestrator, run_id: str) -> dict[str, object]:
    path = orchestrator.run_dir(run_id) / "tasks" / "S3.assign" / "output.json"
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _allocation_signature(assignment: dict[str, object]) -> str:
    return json.dumps(
        {
            "plots": [thread["plot_type"] for thread in assignment["threads"]],
            "roles": [person["role"] for person in assignment["cast"]],
            "character_elements": [
                person["elements"] for person in assignment["cast"]
            ],
            "world_elements": assignment["world"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def test_s3_assignment_is_schema_valid_and_adds_the_phase1_dag(tmp_path: Path) -> None:
    orchestrator, run_id = _run(tmp_path / "data", 123, run_number=1)
    assignment = _assignment(orchestrator, run_id)
    validate_document(assignment, ROOT / "schemas/story/assignment.schema.json")
    manifest = orchestrator.load_run(run_id)

    assert manifest["table_snapshot"] == {axis: 0 for axis in _pools()}
    assert assignment["cast"][0]["role"] == "protagonist"
    assert assignment["cast"][0]["id"] == "c1"
    assert assignment["cast"][0]["suppressed_self_image"]["kind"] == "suppression"
    assert {"messenger", "supporter", "adversary"}.issubset(
        set(assignment["absent_roles"])
    )
    place_tasks = [
        task_id for task_id in manifest["tasks"] if task_id.startswith("S4.section-place-")
    ]
    assert place_tasks
    assert all(manifest["tasks"][task_id]["deps"] == ["S3.assign"] for task_id in place_tasks)
    assert manifest["tasks"]["S6.expand"]["deps"][0] == "S3.assign"
    assert len(manifest["tasks"]["S6.expand"]["deps"]) > 1


def test_s3_same_seed_repeats_and_ten_seeds_do_not_all_match(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    first_orchestrator, first_id = _run(data_dir, 456, run_number=1)
    second_orchestrator, second_id = _run(data_dir, 456, run_number=2)
    assert _assignment(first_orchestrator, first_id) == _assignment(second_orchestrator, second_id)

    assignments = []
    for number, seed in enumerate(range(100, 110), start=3):
        orchestrator, run_id = _run(data_dir, seed, run_number=number)
        assignments.append(_allocation_signature(_assignment(orchestrator, run_id)))
    assert len(set(assignments)) > 1


def test_s3_plot_type_override_is_read_from_manifest_input(tmp_path: Path) -> None:
    orchestrator, run_id = _run(
        tmp_path / "data",
        789,
        run_number=1,
        plot_type="quest",
    )
    assert _assignment(orchestrator, run_id)["threads"][0]["plot_type"] == "quest"


def _rich_pools() -> dict[str, list[dict[str, str]]]:
    return {
        axis: [
            {"id": f"{axis}:i{index:02d}", "text": f"入力由来の{axis}{index}"}
            for index in range(1, 21)
        ]
        for axis in _pools()
    }


def _multi_thread_scale() -> dict[str, object]:
    return {
        "preset": "novella",
        "axes": {
            "time": "months",
            "space": "region",
            "cast": "4-8",
            "threads": "main+1-2",
            "change": "community",
        },
        "overrides": {},
        "derived": {
            "events": 15,
            "threads": 3,
            "cast": 4,
            "parts": None,
            "world_sections": ["place", "customs", "people", "organizations", "social_structure", "social_groups", "interpretation"],
            "pool_need": {},
        },
    }


def test_s3_multi_cast_and_threads_keep_ids_sources_and_event_counts_unique(
    tmp_path: Path,
) -> None:
    orchestrator, run_id = _run(
        tmp_path / "data",
        321,
        run_number=1,
        scale=_multi_thread_scale(),
        pools=_rich_pools(),
    )
    assignment = _assignment(orchestrator, run_id)
    manifest = orchestrator.load_run(run_id)

    assert manifest["input_ratio"] == assignment["r"]
    assert 0.5 <= assignment["r"] <= 0.9
    assert len(assignment["cast"]) == 4
    assert len({thread["plot_type"] for thread in assignment["threads"]}) == 3
    assert all(
        4 <= thread["events"] <= 6
        for thread in assignment["threads"][1:]
    )
    assert sum(thread["events"] for thread in assignment["threads"]) == 15

    axis_ids: dict[str, list[str]] = {axis: [] for axis in _rich_pools()}
    for person in assignment["cast"]:
        for axis, element in person["elements"].items():
            axis_ids[axis].append(element["id"])
    for axis, element in assignment["world"].items():
        if axis in axis_ids and isinstance(element, dict):
            axis_ids[axis].append(element["id"])
    assert all(len(ids) == len(set(ids)) for ids in axis_ids.values())
    assert any(
        ":i" in element["id"]
        for person in assignment["cast"]
        for element in person["elements"].values()
    )


def test_s3_world_tasks_follow_volume_and_distinct_viewpoint_cuts(tmp_path: Path) -> None:
    scale = _multi_thread_scale()
    orchestrator, run_id = _run(
        tmp_path / "data",
        324,
        run_number=1,
        scale=scale,
        pools=_rich_pools(),
    )
    manifest = orchestrator.load_run(run_id)
    assignment = _assignment(orchestrator, run_id)
    world_volume = manifest["scale"]["derived"]["volume"]["world"]
    allocation = world_volume["sections"]
    floor = load_table("volume")["floor_chars"]["world"] * multiplier_for_preset(
        scale["preset"]
    )
    assert world_volume["total_chars"] >= floor

    for section in allocation:
        section_id = section["id"]
        expected = (
            sum(viewpoint["facet_count"] for viewpoint in section["viewpoints"])
            if section["kind"] == "single"
            else section["item_count"]
        )
        prefix = f"S4.{'section' if section['kind'] == 'single' else 'item'}-{section_id}-"
        task_ids = [task_id for task_id in manifest["tasks"] if task_id.startswith(prefix)]
        assert len(task_ids) == expected

    cuts_by_viewpoint: dict[tuple[str, str], list[str]] = {}
    for world_task in assignment["world_tasks"]:
        if world_task["kind"] == "single":
            key = (world_task["section_id"], world_task["viewpoint"])
            cuts_by_viewpoint.setdefault(key, []).append(world_task["element"]["id"])
    assert cuts_by_viewpoint
    assert all(len(ids) == len(set(ids)) for ids in cuts_by_viewpoint.values())


def test_s3_downstream_edges_match_the_phase_one_dag(tmp_path: Path) -> None:
    orchestrator, run_id = _run(
        tmp_path / "data",
        322,
        run_number=1,
        scale=_multi_thread_scale(),
        pools=_rich_pools(),
    )
    manifest = orchestrator.load_run(run_id)
    tasks = manifest["tasks"]
    assignment = _assignment(orchestrator, run_id)
    world_sections = {
        section["id"]: section
        for section in load_table("world_sections")["sections"]
    }
    task_section = {
        f"S4.{'section' if entry['kind'] == 'single' else 'item'}-{entry['id']}": entry["section_id"]
        for entry in assignment["world_tasks"]
    }

    s4_ids = [task_id for task_id in tasks if task_id.startswith(("S4.section-", "S4.item-"))]
    s4_by_section: dict[str, list[str]] = {}
    for task_id in s4_ids:
        section_id = task_section[task_id]
        s4_by_section.setdefault(section_id, []).append(task_id)
    for task_id in s4_ids:
        section_id = task_section[task_id]
        prerequisite_ids = [
            dependency
            for prerequisite in world_sections[section_id]["prerequisites"]
            for dependency in s4_by_section.get(prerequisite, [])
        ]
        expected_deps = ["S3.assign", *prerequisite_ids]
        if task_id.startswith("S4.item-"):
            name_id = task_id.replace("S4.item-", "S4.item_name-", 1)
            expected_deps.append(name_id)
            assert tasks[name_id]["type"] == "S4.item_name"
            assert tasks[name_id]["deps"] == ["S3.assign"]
            assert tasks[name_id]["index"] == tasks[task_id]["index"]
        assert tasks[task_id]["deps"] == expected_deps

    person_ids = [person["id"] for person in assignment["cast"]]
    counts_by_person = {
        entry["id"]: entry["item_counts"]
        for entry in manifest["scale"]["derived"]["volume"]["characters"]
    }
    name_ids = [f"S5.name-{person_id}" for person_id in person_ids]
    protagonist_name_id = name_ids[0]
    protagonist_intro_id = f"S5.intro-{person_ids[0]}"

    relationship_context_ids = {
        person_id: f"S5.relationship_context-{person_id}" for person_id in person_ids
    }
    for person_id in person_ids:
        context_id = relationship_context_ids[person_id]
        assert tasks[context_id]["deps"] == [
            "S3.assign",
            f"S5.name-{person_id}",
            f"S5.intro-{person_id}",
        ]

    for position, person_id in enumerate(person_ids):
        profile_id = f"S5.profile-{person_id}"
        motive_id = f"S5.motive-{person_id}"
        context_name_deps = [] if position == 0 else [protagonist_name_id]
        context_intro_deps = [] if position == 0 else [protagonist_intro_id]
        parallel_deps = ["S3.assign", profile_id, *context_name_deps]
        assert tasks[f"S5.name-{person_id}"]["deps"] == ["S3.assign"]
        assert tasks[profile_id]["deps"] == [
            "S3.assign",
            *context_name_deps,
            f"S5.name-{person_id}",
        ]
        for field in ("intro", "appearance", "personality", "values", "voice", "inner_conflict"):
            assert tasks[f"S5.{field}-{person_id}"]["deps"] == parallel_deps
        assert tasks[motive_id]["deps"] == [
            "S3.assign",
            profile_id,
            *name_ids,
            *context_intro_deps,
        ]

        counts = counts_by_person[person_id]
        backstory_ids = [
            f"S5.backstory-{person_id}-p{ordinal}"
            for ordinal in range(1, counts["backstory"] + 1)
        ]
        for backstory_id in backstory_ids:
            assert tasks[backstory_id]["deps"] == parallel_deps
        assert len(backstory_ids) >= 2

        relationship_targets = [other for other in person_ids if other != person_id]
        assert counts["relationship"] == len(relationship_targets)
        relationship_ids = [
            f"S5.relationship-{person_id}-{other_id}" for other_id in relationship_targets
        ]
        for relationship_id, other_id in zip(relationship_ids, relationship_targets):
            assert tasks[relationship_id]["deps"] == [
                *parallel_deps,
                relationship_context_ids[other_id],
            ]

        assert tasks[f"S5.catchphrase-{person_id}"]["deps"] == [
            "S3.assign",
            motive_id,
            f"S5.personality-{person_id}",
            f"S5.values-{person_id}",
            f"S5.voice-{person_id}",
            f"S5.inner_conflict-{person_id}",
            *backstory_ids,
            *relationship_ids,
            *context_name_deps,
            *context_intro_deps,
        ]

    s5_ids = [task_id for task_id in tasks if task_id.startswith("S5.")]
    s4_with_checks = [
        task_id for task_id in tasks
        if tasks[task_id]["type"] in {"S4.section", "S4.item", "S4.diversity"}
    ]
    assert tasks["S6.expand"]["deps"] == ["S3.assign", *s4_with_checks, *s5_ids]
    for section_id, facet_ids in s4_by_section.items():
        diversity_id = f"S4.diversity-{section_id}"
        if world_sections[section_id]["kind"] == "single":
            assert tasks[diversity_id]["deps"] == ["S3.assign", *facet_ids]
            assert tasks[diversity_id]["index"] == [section_id]
        else:
            assert diversity_id not in tasks


def test_s3_character_volume_reaches_the_floor_and_matches_created_task_counts(
    tmp_path: Path,
) -> None:
    scale = _multi_thread_scale()
    orchestrator, run_id = _run(
        tmp_path / "data",
        323,
        run_number=1,
        scale=scale,
        pools=_rich_pools(),
    )
    manifest = orchestrator.load_run(run_id)
    tasks = manifest["tasks"]
    assignment = _assignment(orchestrator, run_id)
    person_ids = [person["id"] for person in assignment["cast"]]
    characters_volume = manifest["scale"]["derived"]["volume"]["characters"]
    assert {entry["id"] for entry in characters_volume} == set(person_ids)

    multiplier = multiplier_for_preset(scale["preset"])
    floor = load_table("volume")["floor_chars"]["characters"] * multiplier
    assert sum(entry["target_chars"] for entry in characters_volume) >= floor

    for entry in characters_volume:
        person_id = entry["id"]
        counts = entry["item_counts"]
        for field in (
            "profile",
            "intro",
            "appearance",
            "motive",
            "personality",
            "values",
            "voice",
            "inner_conflict",
            "catchphrase",
        ):
            assert f"S5.{field}-{person_id}" in tasks
        backstory_ids = [
            task_id
            for task_id in tasks
            if task_id.startswith(f"S5.backstory-{person_id}-p")
        ]
        assert len(backstory_ids) == counts["backstory"]
        relationship_ids = [
            task_id
            for task_id in tasks
            if task_id.startswith(f"S5.relationship-{person_id}-")
        ]
        assert len(relationship_ids) == counts["relationship"] == len(person_ids) - 1


def test_s3_input_ratio_controls_input_or_table_source() -> None:
    input_pool = {"want": [{"id": "want:i01", "text": "入力"}]}
    table_pool = {"want": [{"id": "want:t1", "text": "テーブル"}]}

    input_context = SimpleNamespace(random=SimpleNamespace(random=lambda: 0.59, choice=lambda items: items[0]))
    table_context = SimpleNamespace(random=SimpleNamespace(random=lambda: 0.60, choice=lambda items: items[0]))

    assert _choose_element(input_context, "want", 0.6, input_pool, table_pool, set())["id"] == "want:i01"
    assert _choose_element(table_context, "want", 0.6, input_pool, table_pool, set())["id"] == "want:t1"


def test_s3_role_assignment_only_uses_required_roles_and_records_absence() -> None:
    structures = load_table("structures")["templates"]
    for template_id in ("kishotenketsu", "three-beat", "heros-journey-12"):
        structure = structures[template_id]
        required = Counter(
            role
            for stage in structure["stages"]
            for role in stage["roles"]
            if role in {"messenger", "supporter", "adversary"}
        )
        roles, absent = _assign_roles(
            SimpleNamespace(random=random.Random(7)),
            [{"structure": template_id}],
            structures,
            2,
        )
        ordered = sorted(
            required,
            key=lambda role: (
                -required[role],
                ("messenger", "supporter", "adversary").index(role),
            ),
        )
        assert roles == ["protagonist", ordered[0]]
        assert set(absent) == set(ordered[1:])

        roles, absent = _assign_roles(
            SimpleNamespace(random=random.Random(7)),
            [{"structure": template_id}],
            structures,
            4,
        )
        assert set(roles) >= {"protagonist", *required}
        assert absent == []

    only_messenger = {"only-messenger": {"stages": [{"roles": ["messenger"]}]}}
    roles, absent = _assign_roles(
        SimpleNamespace(random=random.Random(7)),
        [{"structure": "only-messenger"}],
        only_messenger,
        3,
    )
    assert roles[:2] == ["protagonist", "messenger"]
    assert absent == []

    random_roles = {
        _assign_roles(
            SimpleNamespace(random=random.Random(seed)),
            [{"structure": "kishotenketsu"}],
            structures,
            5,
        )[0][-1]
        for seed in range(20)
    }
    assert "bystander" in random_roles


def test_subthread_uses_kishotenketsu_even_for_a_dedicated_plot_template() -> None:
    custom_plot = {"structure": "dedicated-template"}
    assert _structure_for(custom_plot, 20, is_main=False) == "kishotenketsu"
    assert _structure_for(custom_plot, 20, is_main=True) == "dedicated-template"


def test_s3_table_snapshot_and_ratio_are_stable_after_reexecution(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    orchestrator, run_id = _run(data_dir, 654, run_number=1)
    first = orchestrator.load_run(run_id)
    first_snapshot = first["table_snapshot"]
    first_ratio = first["input_ratio"]

    (data_dir / "tables").mkdir(parents=True, exist_ok=True)
    (data_dir / "tables" / "want.json").write_text(
        json.dumps([{"text": "後から増えた要素"}], ensure_ascii=False),
        encoding="utf-8",
    )
    orchestrator.invalidate_task(run_id, "S3.assign", reason="再実行の検証")
    orchestrator.advance(run_id)
    second = orchestrator.load_run(run_id)

    assert second["table_snapshot"] == first_snapshot
    assert second["input_ratio"] == first_ratio
