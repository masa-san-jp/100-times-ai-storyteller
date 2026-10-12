"""Formatting harness common parts: F0 select, F1 split, F3 assemble, F4 verify.

See docs/spec/format-pipeline.md.  The canonical story (``story/story.json``)
is only read.  Every choice here (units, parts, ranges, pass/fail) is made by
deterministic code; the LLM tasks (F2 writing, F4 yes/no questions) only
produce one text or one judgement each (P1, P2).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .format_units import UNIT_MAPPERS
from .orchestrator import CodeTaskContext, CodeTaskResult, Orchestrator, TaskSpec
from .storage import atomic_write_text
from .tables import load_table
from .validation import load_and_validate_yaml, validate_document, validate_output


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_SCHEMA = "schemas/format-profile.schema.json"
STORY_SCHEMA = "schemas/story.schema.json"
FORMAT_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
BUILTIN_CODE_CHECKS = ("min_chars", "max_chars", "ends_complete")
PREVIOUS_TAIL_CHARS = 600

# Task types whose single dependency is read directly, not as an
# index -> output object (the format name is the only varying index).
FORMAT_SINGLE_SOURCES = frozenset({"F0.select", "F1.split", "F2.write", "F2.write_once"})

# Syntax checks (Fountain, Marp ...) for ``checks.code``.  A check takes the
# text of one F2 task and its part record, and returns error messages.
SyntaxCheck = Callable[[str, Mapping[str, Any]], list[str]]
SYNTAX_CHECKS: dict[str, SyntaxCheck] = {}


def default_formats_root() -> Path:
    """Return the directory holding ``<name>/profile.yaml`` for each format."""

    return REPOSITORY_ROOT / "formats"


# --- task IDs ---------------------------------------------------------------

def f0_id(name: str) -> str:
    return f"F0.select-{name}"


def f1_id(name: str) -> str:
    return f"F1.split-{name}"


def f2_id(part_id: str, name: str, continuation: bool = True) -> str:
    return f"{_f2_type(continuation)}-{part_id}-{name}"


def judge_id(part_id: str, number: int, name: str) -> str:
    return f"F4.judge-{part_id}-b{number}-{name}"


def verify_id(part_id: str, name: str) -> str:
    return f"F4.verify-{part_id}-{name}"


def f3_id(name: str) -> str:
    return f"F3.assemble-{name}"


def _f2_type(continuation: bool) -> str:
    return "F2.write" if continuation else "F2.write_once"


# --- profile ----------------------------------------------------------------

def load_profile(
    name: str,
    *,
    formats_root: str | Path | None = None,
    repository_root: str | Path = REPOSITORY_ROOT,
) -> dict[str, Any]:
    """Load ``formats/<name>/profile.yaml`` and check it beyond the schema."""

    if not isinstance(name, str) or not FORMAT_NAME.fullmatch(name):
        raise ValueError(f"様式名が不正です: {name!r}")
    root = Path(formats_root) if formats_root is not None else default_formats_root()
    path = root / name / "profile.yaml"
    if not path.is_file():
        raise ValueError(f"様式プロファイルがありません: {path}")
    profile = load_and_validate_yaml(path, Path(repository_root) / PROFILE_SCHEMA)
    if profile["name"] != name:
        raise ValueError(f"プロファイルの name がディレクトリ名と一致しません: {name}")
    low, high = profile["chars_per_event"]
    if low > high:
        raise ValueError("chars_per_event の下限が上限を超えています")
    if profile["unit_mapping"] not in UNIT_MAPPERS:
        raise ValueError(f"unit_mapping が未対応です: {profile['unit_mapping']}")
    unknown = [
        check for check in profile["checks"]["code"]
        if check not in BUILTIN_CODE_CHECKS and check not in SYNTAX_CHECKS
    ]
    if unknown:
        raise ValueError(f"checks.code が未対応です: {', '.join(unknown)}")
    if not (path.parent / profile["template"]).is_file():
        raise ValueError(f"テンプレートがありません: {path.parent / profile['template']}")
    return profile


def read_story(run_dir: Path, repository_root: str | Path = REPOSITORY_ROOT) -> dict[str, Any]:
    """Read and validate the canonical story of a run."""

    path = run_dir / "story" / "story.json"
    try:
        story = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"正本を読み込めません: {path}") from error
    validate_document(story, Path(repository_root) / STORY_SCHEMA)
    return story


# --- text of inputs ---------------------------------------------------------

def _role_names(repository_root: Path) -> dict[str, str]:
    return {
        role["id"]: role["name"]
        for role in load_table("roles", repository_root=repository_root)["roles"]
    }


def event_text(event: Mapping[str, Any], names: Mapping[str, str]) -> str:
    """Render one event as one line for the F2 and F4 cards."""

    who = "、".join(names[person_id] for person_id in event["who"])
    fields = [
        ("いつ", event["when"]), ("どこ", event["where"]), ("誰が", who),
        ("なぜ", event["why"]), ("意思", event["intent"]), ("行動", event["what"]),
        ("結果", event["result"]), ("感情", event["emotion"]),
    ]
    if event["foreshadowing"]:
        fields.append(("伏線", event["foreshadowing"]))
    return "／".join(f"{label}：{value}" for label, value in fields)


def character_text(person: Mapping[str, Any], roles: Mapping[str, str]) -> str:
    return (
        f"{person['name']}（{person['reading']}）／"
        f"{roles.get(person['role'], person['role'])}／{person['profile']}"
    )


def split_counts(count: int, per_part: int) -> list[int]:
    """Split *count* events into near-equal parts of at most *per_part*."""

    parts = math.ceil(count / per_part)
    base, extra = divmod(count, parts)
    return [base + 1 if position < extra else base for position in range(parts)]


# --- code tasks -------------------------------------------------------------

def make_handlers(
    repository_root: str | Path = REPOSITORY_ROOT,
    formats_root: str | Path | None = None,
) -> dict[str, Callable[[CodeTaskContext], Any]]:
    """Return the code-task handlers, bound to the roots they read from."""

    root = Path(repository_root)

    def select(context: CodeTaskContext) -> CodeTaskResult:
        """F0: read the profile, compute the total range, and add F1."""

        name = context.task["index"][0]
        profile = load_profile(name, formats_root=formats_root, repository_root=root)
        story = read_story(context.run_dir, root)
        low, high = profile["chars_per_event"]
        count = len(story["events"])
        directory = Path(formats_root) if formats_root is not None else default_formats_root()
        template_text = (directory / name / profile["template"]).read_text(encoding="utf-8")
        return CodeTaskResult(
            output={
                "profile": profile,
                "template_text": template_text,
                "event_count": count,
                "total_chars": {"min": count * low, "max": count * high},
            },
            add_tasks=[TaskSpec(f1_id(name), "F1.split", deps=(context.task_id,), index=(name,))],
        )

    def split(context: CodeTaskContext) -> CodeTaskResult:
        """F1: map events to units, split by size, and add F2, F4 and F3."""

        name = context.task["index"][0]
        profile = context.inputs["selection"]["profile"]
        story = read_story(context.run_dir, root)
        plan = build_plan(story, profile, root)
        questions = plan["questions"]
        continuation = profile["continuation"]
        tasks: list[TaskSpec] = []
        previous: str | None = None
        verifies: list[str] = []
        writes: list[str] = []
        for part in plan["parts"]:
            part_id = part["id"]
            write = f2_id(part_id, name, continuation)
            deps = (context.task_id,) if previous is None else (context.task_id, previous)
            tasks.append(TaskSpec(write, _f2_type(continuation), deps=deps, index=(part_id, name)))
            judges = []
            for number in range(1, len(questions) + 1):
                judge = judge_id(part_id, number, name)
                judges.append(judge)
                tasks.append(TaskSpec(
                    judge, "F4.judge", deps=(context.task_id, write),
                    index=(part_id, f"b{number}", name),
                ))
            verify = verify_id(part_id, name)
            tasks.append(TaskSpec(
                verify, "F4.verify", deps=(context.task_id, write, *judges),
                index=(part_id, name),
            ))
            writes.append(write)
            verifies.append(verify)
            previous = write
        tasks.append(TaskSpec(
            f3_id(name), "F3.assemble",
            deps=(f0_id(name), context.task_id, *writes, *verifies), index=(name,),
        ))
        return CodeTaskResult(output=plan, add_tasks=tasks)

    def verify(context: CodeTaskContext) -> CodeTaskResult:
        """F4 (code side): code checks and the yes/no answers of one part."""

        part_id, name = context.task["index"][0], context.task["index"][-1]
        plan = context.inputs["plan"]
        part = next(item for item in plan["parts"] if item["id"] == part_id)
        write = f2_id(part_id, name, plan["continuation"])
        body = context.dependency_outputs[write]
        failures = check_body(body, part, plan["code_checks"], root)
        warnings: list[str] = []
        for number, question in enumerate(plan["questions"], start=1):
            judgement = context.dependency_outputs.get(judge_id(part_id, number, name))
            if judgement is None:
                warnings.append(f"{verify_id(part_id, name)}: 質問{number}の判定がないため確認できていません")
            elif judgement["answer"] == "yes":
                failures.append(f"質問「{question}」に該当：{judgement['reason']}")
        passed = not failures
        if not passed:
            context.invalidate_task(write, "；".join(failures))
        return CodeTaskResult(
            output={"part": part_id, "passed": passed, "failures": failures},
            manifest_updates={"warnings": warnings} if warnings else {},
        )

    def assemble(context: CodeTaskContext) -> dict[str, Any]:
        """F3: pour the unit texts into the template and write the file."""

        name = context.task["index"][0]
        selection, plan = context.inputs["selection"], context.inputs["plan"]
        story = read_story(context.run_dir, root)
        bodies: dict[str, list[str]] = {unit["id"]: [] for unit in plan["units"]}
        for part in plan["parts"]:
            text = context.dependency_outputs.get(f2_id(part["id"], name, plan["continuation"]))
            if not isinstance(text, str):
                raise ValueError(f"本文がありません: {part['id']}")
            bodies[part["unit"]].append(text.strip())
        units = [
            {**unit, "body": "\n\n".join(bodies[unit["id"]])} for unit in plan["units"]
        ]
        text = render_template(selection["template_text"], story, units, name)
        suffix = Path(selection["profile"]["template"]).suffix or ".txt"
        relative = f"formats/{name}/{name}{suffix}"
        atomic_write_text(context.run_dir / relative, text, encoding="utf-8")
        return {"path": relative, "chars": len("".join(text.split()))}

    return {
        "format_f0_select": select,
        "format_f1_split": split,
        "format_f3_assemble": assemble,
        "format_f4_verify": verify,
    }


def build_plan(
    story: Mapping[str, Any], profile: Mapping[str, Any], repository_root: Path
) -> dict[str, Any]:
    """F1: units, parts, and the per-part range of characters."""

    low, high = profile["chars_per_event"]
    events = {event["id"]: event for event in story["events"]}
    order = {event_id: position for position, event_id in enumerate(events)}
    units = UNIT_MAPPERS[profile["unit_mapping"]](story, profile, repository_root)
    if any(not unit.event_ids for unit in units):
        raise ValueError("出来事のない単位があります")
    assigned = [event_id for unit in units for event_id in unit.event_ids]
    if sorted(assigned) != sorted(events) or len(set(assigned)) != len(assigned):
        raise ValueError("unit_mapping がすべての出来事をちょうど1回ずつ割り付けていません")
    names = {person["id"]: person["name"] for person in story["cast"]}
    people = {person["id"]: person for person in story["cast"]}
    roles = _role_names(repository_root)
    per_part = max(1, profile["max_chars_per_task"] // high)

    unit_records: list[dict[str, Any]] = []
    parts: list[dict[str, Any]] = []
    for unit in units:
        ordered = sorted(unit.event_ids, key=order.__getitem__)
        part_ids: list[str] = []
        start = 0
        for number, size in enumerate(split_counts(len(ordered), per_part), start=1):
            chunk = ordered[start:start + size]
            start += size
            part_id = f"{unit.id}p{number}"
            who = sorted(
                {person for event_id in chunk for person in events[event_id]["who"]},
                key=list(people).index,
            )
            part_ids.append(part_id)
            parts.append({
                "id": part_id,
                "unit": unit.id,
                "event_ids": chunk,
                "events": [{"id": event_id, "text": event_text(events[event_id], names)} for event_id in chunk],
                "characters": [{"id": person, "text": character_text(people[person], roles)} for person in who],
                "min_chars": low * len(chunk),
                "max_chars": high * len(chunk),
                "range": f"{low * len(chunk)}〜{high * len(chunk)}字",
            })
        unit_records.append({
            "id": unit.id, "title": unit.title, "part": unit.part,
            "event_ids": ordered, "part_ids": part_ids,
        })
    return {
        "format": profile["name"],
        "unit": profile["unit"],
        "continuation": profile["continuation"],
        "rules": list(profile["rules"]),
        "code_checks": list(profile["checks"]["code"]),
        "questions": list(profile["checks"].get("llm", [])),
        "units": unit_records,
        "parts": parts,
    }


def check_body(
    body: str, part: Mapping[str, Any], code_checks: Sequence[str], repository_root: Path
) -> list[str]:
    """F4 code checks of one part.  Returns failure messages (empty = pass)."""

    builtin: list[Any] = []
    for check in code_checks:
        if check == "min_chars":
            builtin.append({"min_chars": {"n": part["min_chars"]}})
        elif check == "max_chars":
            builtin.append({"max_chars": {"n": part["max_chars"]}})
        elif check == "ends_complete":
            builtin.append("ends_complete")
    failures: list[str] = []
    if builtin:
        result = validate_output(
            {"output": "text", "validate": {"checks": builtin}}, body, {},
            harness_root=repository_root,
        )
        failures.extend(result.errors)
    for check in code_checks:
        if check in SYNTAX_CHECKS:
            failures.extend(SYNTAX_CHECKS[check](body, part))
    return failures


# --- template ---------------------------------------------------------------

_BLOCK = re.compile(r"\{\{#units\}\}(.*?)\{\{/units\}\}", re.DOTALL)
_PART_START = re.compile(r"\{\{#part_start\}\}(.*?)\{\{/part_start\}\}", re.DOTALL)
_VARIABLE = re.compile(r"\{\{\s*([a-z_]+(?:\.[a-z_]+)?)\s*\}\}")


def render_template(
    template: str, story: Mapping[str, Any], units: Sequence[Mapping[str, Any]], name: str
) -> str:
    """Pour the unit texts into the template (one ``{{#units}}`` block).

    Inside the block, ``{{unit.chapter}}`` counts units anew in each part
    (it equals ``{{unit.number}}`` without parts), and a
    ``{{#part_start}}…{{/part_start}}`` section is kept only for the first
    unit of each part (for the part heading of a saga).
    """

    blocks = _BLOCK.findall(template)
    if len(blocks) != 1 or len(_BLOCK.split(template)) != 3:
        raise ValueError("テンプレートに {{#units}}…{{/units}} が1つだけ必要です")
    protagonist = next(person for person in story["cast"] if person["role"] == "protagonist")
    plot_type = story["meta"]["plot_types"][0]["name"]
    values = {
        "title": f"{plot_type}：{protagonist['name']}",
        "protagonist": protagonist["name"],
        "plot_type": plot_type,
        "run_id": story["meta"]["run_id"],
        "format": name,
        "unit_count": str(len(units)),
    }

    def fill(text: str, scope: Mapping[str, str]) -> str:
        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in scope:
                raise ValueError(f"テンプレートに未知の変数があります: {key}")
            return scope[key]
        return _VARIABLE.sub(replace, text)

    rendered_units = []
    chapter = 0
    previous_part: Any = object()
    for number, unit in enumerate(units, start=1):
        starts_part = unit["part"] is not None and unit["part"] != previous_part
        chapter = 1 if unit["part"] != previous_part else chapter + 1
        previous_part = unit["part"]
        block = _PART_START.sub(lambda match: match.group(1) if starts_part else "", blocks[0])
        scope = {
            **values,
            "unit.number": str(number),
            "unit.chapter": str(chapter),
            "unit.id": unit["id"],
            "unit.title": unit["title"],
            "unit.part": "" if unit["part"] is None else str(unit["part"]),
            "unit.body": unit["body"],
        }
        rendered_units.append(fill(block, scope))
    before, _, after = _BLOCK.split(template)
    return fill(before, values) + "".join(rendered_units) + fill(after, values)


# --- orchestrator integration ----------------------------------------------

def format_source_value(slot_name: str, value: Any) -> Any:
    """The card source for a single format dependency (see FORMAT_SINGLE_SOURCES)."""

    if slot_name == "previous_unit" and isinstance(value, str):
        return value[-PREVIOUS_TAIL_CHARS:]
    return value


def load_format_task_definitions(
    repository_root: str | Path = REPOSITORY_ROOT,
) -> dict[str, dict[str, Any]]:
    """Load the formatting task definitions from ``harness/format/tasks``."""

    root = Path(repository_root)
    schema_path = root / "schemas" / "task-definition.schema.json"
    definitions: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "harness" / "format" / "tasks").glob("*.yaml")):
        definition = load_and_validate_yaml(path, schema_path)
        definitions[definition["id"]] = definition
    return definitions


def start_formatting(
    orchestrator: Orchestrator,
    run_id: str,
    name: str,
    *,
    formats_root: str | Path | None = None,
) -> None:
    """``st format``: add the F0 task to a completed story run (run -> active)."""

    manifest = orchestrator.load_run(run_id)
    if manifest["harness_kind"] != "story":
        raise ValueError("様式化できるのは物語の run だけです")
    if manifest["status"] not in {"completed", "active"}:
        raise ValueError(
            f"様式化できるのは completed または active の run だけです（現在: {manifest['status']}）"
        )
    unfinished = [
        task_id for task_id, task in manifest["tasks"].items()
        if not task["type"].startswith("F") and task["state"] not in {"done", "skipped"}
    ]
    if unfinished:
        raise ValueError("物語の生成が終わっていない run は様式化できません")
    load_profile(
        name, formats_root=formats_root, repository_root=orchestrator.repository_root
    )
    read_story(orchestrator.run_dir(run_id), orchestrator.repository_root)
    if f0_id(name) in manifest["tasks"]:
        raise ValueError(f"この run はすでに様式 {name} を作成済みです")
    orchestrator.add_tasks_to_completed_run(
        run_id, [TaskSpec(f0_id(name), "F0.select", index=(name,))]
    )
