"""Code-built cumulative context for the staged plot (ADR-0011, story-pipeline S6.plot).

Every function here is deterministic: the ledgers are made from accepted task
outputs without any inference.  A value that was left undecided (a skipped
task) is written as 「（未定）」 and no row is dropped on its own; rows are only
cut by the card's per-slot truncation rules (task-model §3.2).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .tables import load_table

UNDECIDED = "（未定）"

ANALYSIS_KINDS = ("desire", "suppression", "conflict")
ANALYSIS_LABELS = {"desire": "切望", "suppression": "抑圧", "conflict": "葛藤"}
# The aspect shown in an S1.analysis card (its instruction sentence is chosen by it).
ANALYSIS_ASPECTS = ANALYSIS_LABELS

WHOLE_INPUT_LIMIT = 5000
WHOLE_INPUT_MIN_LINE = 20
WHOLE_INPUT_WARNING = "入力の全体を分析に渡しきれない"

SKELETON_LACK = "S6.skeleton_lack"
SKELETON_CLIMAX = "S6.skeleton_climax"
SKELETON_RESOLUTION = "S6.skeleton_resolution"
SKELETON_TURN = "S6.skeleton_turn"
SKELETON_TYPES = (SKELETON_LACK, SKELETON_CLIMAX, SKELETON_RESOLUTION, SKELETON_TURN)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

# S5 の項目を、人物の台帳に並べる順。単一の項目は (キー, 名前)。
_SINGLE_ITEMS = (
    ("intro", "紹介"), ("profile", "プロフィール"), ("motive", "動機"),
    ("appearance", "外見"), ("personality", "性格"), ("values", "価値観"),
)
_TAIL_ITEMS = (("voice", "口調"), ("inner_conflict", "内面の葛藤"), ("catchphrase", "決め台詞"))


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _oneline(value: Any) -> str:
    return " ".join(_nfc(str(value)).split())


# --- 入力の全体（S1.analysis の入力） -------------------------------------------------


def whole_input_text(run_input: Any) -> tuple[str, bool]:
    """Join the whole input into one string and fit it to 5000 characters.

    Returns ``(text, truncated_below_minimum)``; the flag is true only when the
    per-line allowance falls under 20 characters (more than 238 lines) and the
    input could not be passed completely (story-pipeline S6.plot §2).
    """

    paragraphs = run_input.get("paragraphs", []) if isinstance(run_input, Mapping) else []
    lines: list[str] = []
    for paragraph in paragraphs:
        if not isinstance(paragraph, Mapping):
            continue
        # 空白・改行は変えない（字数は NFC 正規化後の文字列で数える。S6.plot §2）。
        text = _nfc(str(paragraph.get("text", "")))
        question = paragraph.get("question")
        if isinstance(question, str) and question:
            text = f"{_nfc(question)}：{text}"
        lines.append(text)
    joined = "\n".join(lines)
    if len(joined) <= WHOLE_INPUT_LIMIT:
        return joined, False
    count = len(lines)
    allowance = (WHOLE_INPUT_LIMIT - count) // count
    if allowance >= WHOLE_INPUT_MIN_LINE:
        return "\n".join(line[:allowance] for line in lines), False
    kept: list[str] = []
    total = 0
    for line in lines:
        line = line[:WHOLE_INPUT_MIN_LINE]
        added = len(line) + (1 if kept else 0)
        if total + added > WHOLE_INPUT_LIMIT:
            break
        kept.append(line)
        total += added
    return "\n".join(kept), True


# --- 出力の収集 -----------------------------------------------------------------------


def done_output(run_dir: Path | None, manifest: Mapping[str, Any] | None, task_id: str) -> Any:
    """Return a done task's output read from disk, or ``None``."""

    if run_dir is None or manifest is None:
        return None
    record = manifest.get("tasks", {}).get(task_id)
    if not isinstance(record, Mapping) or record.get("state") != "done":
        return None
    from .task_outputs import read_task_output

    directory = Path(run_dir) / "tasks" / task_id
    if not (directory / "output.json").is_file() and not (directory / "output.md").is_file():
        return None
    return read_task_output(directory, record["type"])


def collect_outputs(
    base: Mapping[str, Any], run_dir: Path | None, manifest: Mapping[str, Any] | None,
    prefixes: Sequence[str],
) -> dict[str, Any]:
    """Merge ``base`` (direct dependency outputs) with done outputs read from disk."""

    outputs = dict(base)
    if run_dir is None or manifest is None:
        return outputs
    for task_id, record in manifest.get("tasks", {}).items():
        if task_id in outputs or not isinstance(record, Mapping):
            continue
        if record.get("state") != "done" or not task_id.startswith(tuple(prefixes)):
            continue
        value = done_output(run_dir, manifest, task_id)
        if value is not None:
            outputs[task_id] = value
    return outputs


# --- 入力の台帳 -----------------------------------------------------------------------


def analysis_lines(texts: Mapping[str, Any]) -> list[str]:
    """One line per analysis; a skipped analysis is 「（未定）」."""

    lines = []
    for kind in ANALYSIS_KINDS:
        value = texts.get(kind)
        text = _oneline(value) if isinstance(value, str) and value.strip() else UNDECIDED
        lines.append(f"{ANALYSIS_LABELS[kind]}：{text}")
    return lines


def analysis_texts(outputs: Mapping[str, Any]) -> dict[str, Any]:
    return {kind: outputs.get(f"S1.analysis-{kind}") for kind in ANALYSIS_KINDS}


def material_lines(outputs: Mapping[str, Any]) -> list[str]:
    from .story_materials import materials_from_outputs

    found = materials_from_outputs(
        {task_id: value for task_id, value in outputs.items() if task_id.startswith("S1.extract-")}
    )
    return [f"[{item['id']}] {_oneline(item['text'])}（{item['kind']}）" for item in found]


def input_ledger(outputs: Mapping[str, Any]) -> list[str]:
    return [*analysis_lines(analysis_texts(outputs)), *material_lines(outputs)]


# --- 人物の台帳 -----------------------------------------------------------------------


def _role_names() -> dict[str, str]:
    return {
        role["id"]: role["name"]
        for role in load_table("roles", repository_root=_REPOSITORY_ROOT)["roles"]
    }


def _element_text(person: Mapping[str, Any], axis: str) -> str:
    element = person.get("elements", {}).get(axis)
    if isinstance(element, Mapping) and isinstance(element.get("text"), str):
        return _oneline(element["text"])
    return UNDECIDED


def _item_text(outputs: Mapping[str, Any], item: str, person_id: str) -> str:
    value = outputs.get(f"S5.{item}-{person_id}")
    if isinstance(value, Mapping) and isinstance(value.get(item), str) and value[item].strip():
        return _oneline(value[item])
    return UNDECIDED


def _sheet_text(
    assignment: Mapping[str, Any], outputs: Mapping[str, Any], person_id: str,
) -> str:
    """The person's fact sheet (S5.fact) as one 「；」-joined run of lines."""

    if "character_fact_tasks" not in assignment or "S4.calendar_name" not in outputs:
        return UNDECIDED
    from .character_facts import assemble_character_sheet
    from .glossary import build_glossary

    sheet = assemble_character_sheet(outputs, person_id)
    names = {entry["id"]: entry["name"] for entry in build_glossary(outputs)}

    def value(field: str) -> str:
        if field not in sheet or (sheet[field] is None and field != "affiliation"):
            return UNDECIDED
        raw = sheet[field]
        if field in {"birthplace", "residence", "affiliation"}:
            return "なし" if raw is None else str(names.get(raw, raw))
        return str(raw)

    parts = [
        f"年齢：{value('age')}歳", f"生年：{value('birth_year')}年（{sheet['calendar']}）",
        f"身長：{value('height_cm')}cm", f"体格：{value('build')}", f"職業：{value('occupation')}",
        f"出身地：{value('birthplace')}", f"居住地：{value('residence')}", f"所属：{value('affiliation')}",
    ]
    family = [f"{member['relationship']} {member['name']}" for member in sheet["family"]]
    parts.append("家族：" + ("、".join(family) if family else "なし"))
    timeline = [
        f"{row['year'] if row['year'] is not None else UNDECIDED}年 {row['event']}"
        for row in sheet["timeline"]
    ]
    parts.append("年表：" + "、".join(timeline))
    parts.append("技能：" + "、".join(str(skill) for skill in sheet["skills"]))
    return "；".join(_oneline(part) for part in parts)


def cast_ledger(assignment: Mapping[str, Any], outputs: Mapping[str, Any]) -> list[str]:
    roles = _role_names()
    people = [person for person in assignment.get("cast", []) if isinstance(person, Mapping)]
    result: list[str] = []
    for person in people:
        person_id = person["id"]
        name_output = outputs.get(f"S5.name-{person_id}")
        name = name_output.get("name") if isinstance(name_output, Mapping) else None
        name = name if isinstance(name, str) and name else person_id
        role = roles.get(person.get("role"), str(person.get("role")))
        head = (
            f"{name}（{role}、{_element_text(person, 'age')}・{_element_text(person, 'gender')}"
            f"・{_element_text(person, 'species')}）"
            f"願望：{_element_text(person, 'want')} 能力：{_element_text(person, 'ability')}"
            f" 課題：{_element_text(person, 'duty')}"
        )
        parts = [head]
        if person.get("role") == "protagonist":
            taboo = f"禁忌：{_element_text(person, 'taboo')}"
            suppressed = person.get("suppressed_self_image")
            if isinstance(suppressed, Mapping) and isinstance(suppressed.get("text"), str):
                taboo += f" 抑圧されている自己像：{_oneline(suppressed['text'])}"
            parts.append(taboo)
        for item, label in _SINGLE_ITEMS:
            parts.append(f"{label}：{_item_text(outputs, item, person_id)}")
        backstories = sorted(
            (int(task_id.rsplit("-p", 1)[1]), value)
            for task_id, value in outputs.items()
            if task_id.startswith(f"S5.backstory-{person_id}-p") and isinstance(value, Mapping)
        )
        if backstories:
            for ordinal, value in backstories:
                text = value.get("backstory")
                parts.append(f"経歴{ordinal}：{_oneline(text) if isinstance(text, str) else UNDECIDED}")
        else:
            parts.append(f"経歴：{UNDECIDED}")
        for other in people:
            if other["id"] == person_id:
                continue
            value = outputs.get(f"S5.relationship-{person_id}-{other['id']}")
            other_output = outputs.get(f"S5.name-{other['id']}")
            other_name = other_output.get("name") if isinstance(other_output, Mapping) else other["id"]
            text = value.get("relationship") if isinstance(value, Mapping) else None
            parts.append(
                f"関係（{other_name}）：{_oneline(text) if isinstance(text, str) and text.strip() else UNDECIDED}"
            )
        for item, label in _TAIL_ITEMS:
            parts.append(f"{label}：{_item_text(outputs, item, person_id)}")
        parts.append(f"事実：{_sheet_text(assignment, outputs, person_id)}")
        result.append(" ".join(parts))
    return result


# --- 世界の台帳 -----------------------------------------------------------------------


def world_ledger(assignment: Mapping[str, Any], outputs: Mapping[str, Any]) -> list[str]:
    from .world_facts import fact_lines

    entries = assignment.get("world_fact_tasks", [])
    tasks = [task for task in assignment.get("world_tasks", []) if isinstance(task, Mapping)]
    lines: list[str] = []

    def facts(selected: Sequence[Mapping[str, Any]]) -> str:
        if not selected or "S4.calendar_name" not in outputs:
            return UNDECIDED
        rows = fact_lines(assignment, outputs, selected, include_items=True)
        return "；".join(_oneline(row["text"]) for row in rows) or UNDECIDED

    for section in assignment.get("world_sections", []):
        if not isinstance(section, Mapping):
            continue
        section_id = section["id"]
        section_tasks = [task for task in tasks if task.get("section_id") == section_id]
        if section.get("kind") == "single":
            bodies = []
            for task in section_tasks:
                output = outputs.get(f"S4.section-{task['id']}")
                if isinstance(output, Mapping) and isinstance(output.get("body"), str):
                    bodies.append(_oneline(output["body"]))
            own = [entry for entry in entries
                   if entry.get("section_id") == section_id and not entry.get("item_id")]
            lines.append(
                f"{section['name']}：事実：{facts(own)} 描写：{' '.join(bodies) or UNDECIDED}"
            )
            continue
        for task in section_tasks:
            output = outputs.get(f"S4.item-{task['id']}")
            name_output = outputs.get(f"S4.item_name-{task['id']}")
            name = None
            if isinstance(output, Mapping) and isinstance(output.get("name"), str):
                name = output["name"]
            elif isinstance(name_output, Mapping) and isinstance(name_output.get("name"), str):
                name = name_output["name"]
            body = output.get("body") if isinstance(output, Mapping) else None
            own = [entry for entry in entries if entry.get("item_id") == task["id"]]
            lines.append(
                f"{_oneline(name) if name else section['name']}：事実：{facts(own)}"
                f" 描写：{_oneline(body) if isinstance(body, str) and body.strip() else UNDECIDED}"
            )
    return lines


def static_ledgers(
    assignment: Mapping[str, Any], base_outputs: Mapping[str, Any],
    run_dir: Path | None, manifest: Mapping[str, Any] | None,
) -> dict[str, list[str]]:
    """The three ledgers that stay the same for every card of a run."""

    outputs = collect_outputs(
        base_outputs, run_dir, manifest, ("S1.analysis-", "S1.extract-", "S4.", "S5."),
    )
    outputs.setdefault("S3.assign", assignment)
    return {
        "input_ledger": input_ledger(outputs),
        "cast_ledger": cast_ledger(assignment, outputs),
        "world_ledger": world_ledger(assignment, outputs),
    }


# --- 骨子 -----------------------------------------------------------------------------


def skeleton_row(element: Mapping[str, Any], text: Any) -> str:
    """One skeleton-ledger line: the element's name, its event ID, then the text."""

    # Shared with the formatting stage (P3-12): 「欠落」「決定的な瞬間（<出来事ID>）」
    # 「結末の後（<出来事ID>）」、転換は「転換（<筋>・<n>・<出来事ID>）」
    # （スロットがなければ出来事IDを省く）。
    parts = [element["thread"], str(element["n"])] if element.get("kind") == "turn" else []
    if element.get("slot"):
        parts.append(element["slot"])
    name = "転換" if element.get("kind") == "turn" else element["name"]
    head = f"{name}（{'・'.join(parts)}）" if parts else name
    body = _oneline(text) if isinstance(text, str) and text.strip() else UNDECIDED
    return f"{head}：{body}"


def skeleton_ledger(
    plan: Mapping[str, Any], texts: Mapping[str, Any], *, only: Sequence[str] | None = None,
) -> list[str]:
    """Rows in order: 欠落 → 決定的な瞬間 → 結末の後 → 転換（筋ごと、時系列順）."""

    selected = None if only is None else set(only)
    return [
        skeleton_row(element, texts.get(element["task"]))
        for element in plan.get("elements", [])
        if selected is None or element["task"] in selected
    ]


def skeleton_texts(
    plan: Mapping[str, Any], base: Mapping[str, Any],
    run_dir: Path | None, manifest: Mapping[str, Any] | None,
) -> dict[str, Any]:
    texts: dict[str, Any] = {}
    for element in plan.get("elements", []):
        task_id = element["task"]
        value = base.get(task_id)
        if value is None:
            value = done_output(run_dir, manifest, task_id)
        if isinstance(value, str):
            texts[task_id] = value
    return texts


def assigned_elements(plan: Mapping[str, Any], slot_id: str) -> list[Mapping[str, Any]]:
    """The elements assigned to a slot: 転換（主筋 → 副筋、n の小さい順）→ 決定的な瞬間 → 結末の後."""

    found = [element for element in plan.get("elements", []) if element.get("slot") == slot_id]
    turns = sorted(
        (element for element in found if element["kind"] == "turn"),
        key=lambda element: (plan["thread_order"].index(element["thread"]), element["n"]),
    )
    rest = [element for element in found if element["kind"] == "climax"]
    rest += [element for element in found if element["kind"] == "resolution"]
    return [*turns, *rest]


def focus_skeleton(
    plan: Mapping[str, Any], slots: Sequence[Mapping[str, Any]], slot_index: int,
    texts: Mapping[str, Any],
) -> dict[str, list[str]]:
    """「この出来事で実現する骨子」または「次に向かう骨子」 (story-pipeline S7 v22)."""

    slot = slots[slot_index]
    own = assigned_elements(plan, slot["id"])
    if own:
        return {"realizing_skeleton": [skeleton_row(e, texts.get(e["task"])) for e in own]}

    def first_later(thread: str | None) -> list[Mapping[str, Any]]:
        for later in slots[slot_index + 1:]:
            if thread is not None and later["thread"] != thread:
                continue
            found = assigned_elements(plan, later["id"])
            if found:
                return found
        return []

    target = first_later(slot["thread"])
    if not target and slot["thread"] != plan.get("main_thread"):
        target = first_later(plan.get("main_thread"))
    if target:
        return {"next_skeleton": [skeleton_row(e, texts.get(e["task"])) for e in target]}
    return {}


# --- 出来事の台帳 ---------------------------------------------------------------------


def event_row(slot: Mapping[str, Any], output: Mapping[str, Any]) -> str:
    names = {person["id"]: person["name"] for person in slot.get("characters", [])}
    who = "、".join(names.get(person_id, person_id) for person_id in output.get("who", []))

    def field(key: str) -> str:
        value = output.get(key)
        return _oneline(value) if isinstance(value, str) and value.strip() else UNDECIDED

    return (
        f"{slot['id']}（{slot['thread']}・{slot['stage']['name']}）"
        f"いつ：{field('when')} どこで：{field('where')} 誰が：{who or UNDECIDED}"
        f" なぜ：{field('why')} 意思：{field('intent')} 何が起きたか：{field('what')}"
        f" 結果：{field('result')} 感情：{field('emotion')} 伏線：{field('foreshadowing')}"
    )


def event_ledger(
    slots: Sequence[Mapping[str, Any]], upto: int, base: Mapping[str, Any],
    run_dir: Path | None, manifest: Mapping[str, Any] | None,
) -> list[str]:
    """Confirmed events in chronological order, ``slots[:upto]`` (exclusive)."""

    rows = []
    for slot in slots[:upto]:
        task_id = f"S7.assemble-{slot['id']}"
        output = base.get(task_id)
        if output is None:
            output = done_output(run_dir, manifest, task_id)
        if isinstance(output, Mapping):
            rows.append(event_row(slot, output))
    return rows


# --- プロットの型の7項目 ----------------------------------------------------------------


def plot_items(plot: Mapping[str, Any]) -> list[str]:
    """中核構造・必須の転換点・人物の要件・時間の設計・葛藤の種類・クライマックスの条件・緩急の原則."""

    required = [
        f"転換{number}：{_oneline(event['description'])}"
        for number, event in enumerate(plot.get("required_events", []), start=1)
    ]
    return [
        f"中核構造：{_oneline(plot['core'])}",
        "必須の転換点：" + " ".join(required),
        f"人物の要件：{_oneline(plot['character_requirements'])}",
        f"時間の設計：{_oneline(plot['time_design'])}",
        f"葛藤の種類：{_oneline(plot['conflict'])}",
        f"クライマックスの条件：{_oneline(plot['climax'])}",
        f"緩急の原則：{_oneline(plot['pacing'])}",
    ]


__all__ = [
    "ANALYSIS_KINDS", "ANALYSIS_LABELS", "SKELETON_CLIMAX", "augment_sources", "SKELETON_LACK",
    "SKELETON_RESOLUTION", "SKELETON_TURN", "SKELETON_TYPES", "UNDECIDED",
    "WHOLE_INPUT_WARNING", "analysis_lines", "assigned_elements", "cast_ledger",
    "collect_outputs", "done_output", "event_ledger", "focus_skeleton", "input_ledger",
    "plot_items", "skeleton_ledger", "skeleton_row", "skeleton_texts", "static_ledgers",
    "whole_input_text", "world_ledger",
]


def selected_analysis_steps(definition: Mapping[str, Any], inputs: Mapping[str, Any]) -> Sequence[str]:
    """Keep only the sentence of the analysis aspect (like S7.condition's condition_kind)."""

    steps = definition.get("card", {}).get("steps")
    if definition.get("id") != "S1.analysis":
        return steps
    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)):
        raise ValueError("S1.analysis card.steps must be a sequence")
    scopes: dict[str, str] = {}
    for step in steps:
        if not isinstance(step, str):
            raise ValueError("S1.analysis card.steps must contain strings")
        key, separator, body = step.partition("：")
        if key in ANALYSIS_KINDS and separator:
            if key in scopes:
                raise ValueError(f"duplicate analysis instruction: {key}")
            if not body.strip():
                raise ValueError(f"empty analysis instruction: {key}")
            scopes[key] = body
    missing = set(ANALYSIS_KINDS) - scopes.keys()
    if missing:
        raise ValueError("missing analysis instruction: " + ", ".join(sorted(missing)))
    label = inputs.get("analysis_kind")
    kind = next((key for key, value in ANALYSIS_ASPECTS.items() if value == label), None)
    if kind is None:
        raise ValueError("unknown analysis_kind")
    result = []
    for step in steps:
        key, separator, body = step.partition("：")
        if key in ANALYSIS_KINDS and separator:
            if key == kind:
                result.append("作り方の指示：" + body)
        else:
            result.append(step)
    return result


# --- カードの入力への組み立て（オーケストレータから呼ぶ） ----------------------------------

_SKELETON_CARD_TYPES = frozenset(SKELETON_TYPES)


def augment_sources(
    run_dir: Path | None,
    manifest: Mapping[str, Any] | None,
    task_id: str,
    task: Mapping[str, Any],
    source_outputs: dict[str, Any],
    dependency_outputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Add the code-built ledgers to the S6.expand view of a card's sources.

    Only the cards of the staged graph (S6.expand v6 and later, whose output
    carries the skeleton plan) get these values; every other card is unchanged.
    """

    task_type = task.get("type")
    if task_type not in _SKELETON_CARD_TYPES and task_type not in {"S7.event", "S7.detail"}:
        return source_outputs
    expand = source_outputs.get("S6.expand")
    if not isinstance(expand, Mapping) or not isinstance(expand.get("skeleton"), Mapping):
        return source_outputs
    plan = expand["skeleton"]
    slots = expand["slots"]
    plots = {plot["id"]: plot for plot in load_table("plot_types", repository_root=_REPOSITORY_ROOT)["types"]}
    texts = skeleton_texts(plan, dependency_outputs, run_dir, manifest)
    extra: dict[str, Any] = {}
    if task_type in _SKELETON_CARD_TYPES:
        element = next(item for item in plan["elements"] if item["task"] == task_id)
        needed = [item["task"] for item in plan["elements"] if item["task"] in task.get("deps", ())]
        if needed:
            extra["skeleton_ledger"] = skeleton_ledger(plan, texts, only=needed)
        plot = plots[element["plot_type"]]
        extra["plot_items"] = plot_items(plot)
        theme = slots[0].get("theme") if slots else None
        if theme is not None:
            extra["theme"] = theme
        if element.get("slot"):
            slot = next(item for item in slots if item["id"] == element["slot"])
            roles = _role_names()
            extra["slot_characters"] = [
                f"{person['name']}（{roles.get(person['role'], person['role'])}）"
                for person in slot["characters"]
            ]
            extra["stage_definition"] = slot["stage"]["definition"]
            extra["stage_guidance"] = slot["stage"]["guidance"]
            if element["kind"] == "turn":
                extra["required_event"] = plot["required_events"][element["n"] - 1]["description"]
                if slot.get("absent_role_note"):
                    extra["absent_role_note"] = slot["absent_role_note"]
    else:
        slot_index = next(
            index for index, slot in enumerate(slots) if slot["id"] == task["index"][0]
        )
        extra["skeleton_ledger"] = skeleton_ledger(plan, texts)
        if task_type == "S7.event":
            extra.update(focus_skeleton(plan, slots, slot_index, texts))
            upto = slot_index
        else:
            upto = slot_index + 1
        ledger = event_ledger(slots, upto, dependency_outputs, run_dir, manifest)
        if ledger:
            extra["event_ledger"] = ledger
    return {**source_outputs, "S6.expand": {**expand, **extra}}
