"""Batch trend analysis: ``st report --batch BATCH_ID`` (docs/spec/report.md).

The report is a run-shaped job in ``batches/<batch_id>/report/``.  Code does
everything that can be decided by code (R1 aggregation, R3 grouping, R5
assembly); the LLM only writes one short element per task from an excerpt the
code cut out within the card's character budget (P1, P5):

* R1 ``R1.aggregate`` (code): tallies the batch's ``completed`` runs, cuts each
  run's main-thread events down to ``EVENTS_BUDGET`` characters, and adds the
  R2 tasks.
* R2 ``R2.loss`` / ``R2.ending`` / ``R2.motif`` (LLM): one element per task per
  run; motifs are asked one at a time (at most ``MOTIF_SLOTS``) and the card
  shows the motifs already given.
* R3 ``R3.group`` (code): groups the motifs by character 3-gram similarity and
  adds the R4 tasks for the groups that appeared in the most runs.
* R4 ``R4.observation`` / ``R4.question`` (LLM): one element per task per group.
* R5 ``R5.assemble`` (code): writes ``report.json`` and ``report.md``.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .batch_status import _BATCH_ID_PATTERN, _batch_run_manifests
from .manifest import load_manifest
from .orchestrator import CodeTaskContext, CodeTaskResult, Orchestrator, TaskSpec
from .report_layout import report_dir
from .storage import atomic_write_json, atomic_write_text
from .validation import load_and_validate_yaml, validate_document


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REPORT_SCHEMA = REPOSITORY_ROOT / "schemas" / "report.schema.json"
_TASK_DEFINITION_SCHEMA = REPOSITORY_ROOT / "schemas" / "task-definition.schema.json"

# The events excerpt of one run.  The R2 tasks have max_input_chars 4000; the
# rest of the "入力" section (headings, desire and adversary clipped to
# FIELD_CHARS) stays well under the difference.
EVENTS_BUDGET = 2400
FIELD_CHARS = 120
OMISSION_MARKER = "（中略）"
MOTIF_SLOTS = 5
MAX_GROUPS = 7
GROUP_SIMILARITY = 0.4
GROUP_SUMMARIES = 3
MAX_VARIANTS = 5
SUMMARY_FIELD_CHARS = 200

_ROLE_ORDER = ("protagonist", "messenger", "supporter", "adversary", "bystander")


# --- starting a report -------------------------------------------------------

def load_report_task_definitions(
    repository_root: str | Path = REPOSITORY_ROOT,
) -> dict[str, dict[str, Any]]:
    """Load ``harness/report/tasks/*.yaml``."""
    root = Path(repository_root)
    definitions: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "harness" / "report" / "tasks").glob("*.yaml")):
        definition = load_and_validate_yaml(path, root / "schemas" / "task-definition.schema.json")
        definitions[definition["id"]] = definition
    return definitions


def make_handlers() -> dict[str, Any]:
    return {
        "report_aggregate": report_aggregate,
        "report_group": report_group,
        "report_assemble": report_assemble,
    }


def create_report_orchestrator(data_dir: str | Path) -> Orchestrator:
    """Return the orchestrator of the report runs (``harness_kind: report``)."""
    return Orchestrator(
        data_dir,
        load_report_task_definitions(),
        make_handlers(),
        harness_root=REPOSITORY_ROOT,
        repository_root=REPOSITORY_ROOT,
        harness_kind="report",
    )


def start_report(data_dir: str | Path, batch_id: str) -> str:
    """Create the report run of a batch and return its run ID.

    The batch is identified like ``st status --batch`` does: by the
    ``batch_id`` of the runs' manifests.  Only ``completed`` runs are analyzed.
    """
    data_dir = Path(data_dir)
    if not isinstance(batch_id, str) or not _BATCH_ID_PATTERN.fullmatch(batch_id):
        raise ValueError(f"invalid batch_id: {batch_id!r}")
    manifests = _batch_run_manifests(data_dir, batch_id)
    if not manifests and not (data_dir / "batches" / batch_id / "manifest.json").is_file():
        raise ValueError(f"バッチが見つかりません: {batch_id}")
    if not any(manifest["status"] == "completed" for manifest in manifests):
        raise ValueError(f"傾向分析の対象になる completed の run がありません: {batch_id}")
    directory = report_dir(data_dir, batch_id)
    if directory.exists():
        raise ValueError(
            f"傾向分析がすでにあります: {directory}（作り直すには、このディレクトリを削除してください）"
        )
    orchestrator = create_report_orchestrator(data_dir)
    run_id = orchestrator.create_run(
        task_specs=[TaskSpec("R1.aggregate", "R1.aggregate")],
        batch_id=batch_id,
        directory=directory,
    )
    # Run R1 now so that the R2 tasks are listed before the first `st next`.
    orchestrator.advance(run_id)
    return run_id


def register_cli_commands(subparsers: Any) -> None:
    """Register ``st report`` without putting its logic in cli.py."""
    parser = subparsers.add_parser("report", help="start the trend analysis of a batch")
    parser.add_argument("--batch", dest="batch_id", required=True)
    parser.set_defaults(command_handler=_run_report_command)


def _run_report_command(args: Any) -> int:
    from .storage import resolve_data_dir

    print(start_report(resolve_data_dir(), args.batch_id))
    return 0


# --- text helpers -------------------------------------------------------------

def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _one_line(value: Any) -> str:
    return " ".join(_nfc(str(value)).split())


def _clip(value: Any, limit: int) -> str:
    text = _one_line(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def trim_middle(lines: Sequence[str], budget: int) -> str:
    """Join *lines*; over *budget*, keep the first and last lines and omit the middle."""
    full = "\n".join(lines)
    if len(full) <= budget:
        return full
    used = len(OMISSION_MARKER) + 2
    head: list[str] = []
    tail: list[str] = []
    low, high = 0, len(lines) - 1
    from_head = True
    while low <= high:
        line = lines[low] if from_head else lines[high]
        if used + len(line) + 1 > budget:
            break
        used += len(line) + 1
        if from_head:
            head.append(line)
            low += 1
        else:
            tail.append(line)
            high -= 1
        from_head = not from_head
    if not head and not tail:
        return lines[0][: max(0, budget - len(OMISSION_MARKER) - 1)] + OMISSION_MARKER
    return "\n".join([*head, OMISSION_MARKER, *reversed(tail)])


# --- R1 -----------------------------------------------------------------------

def _read_json(path: Path, what: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(f"{what}を読めません: {path}") from error


def _main_events(story: Mapping[str, Any]) -> list[str]:
    main_threads = {
        thread["id"] for thread in story.get("threads", []) if thread.get("kind") == "main"
    }
    lines = []
    for event in story.get("events", []):
        if event.get("thread") in main_threads:
            lines.append(
                f"{len(lines) + 1}. {_one_line(event['what'])} → {_one_line(event['result'])}"
            )
    return lines


def _cast_member(assignment: Mapping[str, Any], role: str) -> Mapping[str, Any] | None:
    return next((person for person in assignment["cast"] if person.get("role") == role), None)


def _element_text(person: Mapping[str, Any] | None, axis: str) -> str | None:
    if person is None:
        return None
    element = person.get("elements", {}).get(axis)
    return _clip(element["text"], FIELD_CHARS) if isinstance(element, Mapping) else None


def _run_facts(data_dir: Path, run_id: str) -> dict[str, Any]:
    run_dir = data_dir / "runs" / run_id
    assignment = _read_json(run_dir / "tasks" / "S3.assign" / "output.json", "割り当て")
    story = _read_json(run_dir / "story" / "story.json", "正本")
    return {"run_id": run_id, "assignment": assignment, "story": story}


def report_aggregate(context: CodeTaskContext) -> CodeTaskResult:
    """R1: tally the batch's completed runs and add the R2 tasks."""
    manifest = load_manifest(context.run_dir / "manifest.json")
    batch_id = manifest["batch_id"]
    completed = [
        item["run_id"]
        for item in _batch_run_manifests(context.data_dir, batch_id)
        if item["status"] == "completed"
    ]
    if not completed:
        raise ValueError(f"completed の run がありません: {batch_id}")
    facts = [_run_facts(context.data_dir, run_id) for run_id in completed]

    runs = []
    for number, fact in enumerate(facts, start=1):
        assignment = fact["assignment"]
        record: dict[str, Any] = {"id": f"r{number}", "run_id": fact["run_id"]}
        desire = _element_text(_cast_member(assignment, "protagonist"), "want")
        adversary_role = _element_text(_cast_member(assignment, "adversary"), "duty")
        if desire is not None:
            record["desire"] = desire
        if adversary_role is not None:
            record["adversary_role"] = adversary_role
        record["events"] = trim_middle(_main_events(fact["story"]), EVENTS_BUDGET)
        runs.append(record)

    add_tasks: list[TaskSpec] = []
    r2_ids: list[str] = []
    for record in runs:
        label = record["id"]
        for task_type in ("R2.loss", "R2.ending"):
            task_id = f"{task_type}-{label}"
            add_tasks.append(TaskSpec(task_id, task_type, deps=(context.task_id,), index=(label,)))
            r2_ids.append(task_id)
        previous: list[str] = []
        for number in range(1, MOTIF_SLOTS + 1):
            task_id = f"R2.motif-{label}-{number}"
            add_tasks.append(
                TaskSpec(
                    task_id,
                    "R2.motif",
                    deps=(context.task_id, *previous),
                    index=(label, str(number)),
                )
            )
            previous.append(task_id)
            r2_ids.append(task_id)
    add_tasks.append(TaskSpec("R3.group", "R3.group", deps=(context.task_id, *r2_ids)))
    return CodeTaskResult(
        output={
            "batch_id": batch_id,
            "runs": runs,
            "aggregate": _tally(facts),
        },
        add_tasks=add_tasks,
    )


def _tally(facts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    plot_types: dict[str, dict[str, Any]] = {}
    elements: dict[str, dict[str, Any]] = {}
    roles: dict[str, dict[str, Any]] = {}
    ratios: list[dict[str, Any]] = []
    for fact in facts:
        run_id = fact["run_id"]
        assignment = fact["assignment"]
        for thread in assignment["threads"]:
            if thread["kind"] != "main":
                continue
            entry = plot_types.setdefault(
                thread["plot_type"],
                {"id": thread["plot_type"], "name": thread["plot_type_name"], "count": 0, "run_ids": []},
            )
            _add_use(entry, run_id)
        world = assignment["world"]
        groups = [person["elements"] for person in assignment["cast"]]
        groups.append({axis: world[axis] for axis in ("place", "era", "object")})
        for group in groups:
            for element in group.values():
                if not isinstance(element, Mapping) or "id" not in element:
                    continue
                entry = elements.setdefault(
                    element["id"],
                    {
                        "id": element["id"],
                        "axis": element["id"].split(":", 1)[0],
                        "text": element["text"],
                        "count": 0,
                        "run_ids": [],
                    },
                )
                _add_use(entry, run_id)
        for person in assignment["cast"]:
            entry = roles.setdefault(person["role"], {"role": person["role"], "count": 0, "run_ids": []})
            _add_use(entry, run_id)
        ratios.append({"run_id": run_id, "value": float(assignment["r"])})
    values = [item["value"] for item in ratios]
    return {
        "plot_types": sorted(plot_types.values(), key=lambda item: (-item["count"], item["id"])),
        "input_ratio": {
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "mean": round(sum(values) / len(values), 4) if values else None,
            "per_run": ratios,
        },
        "elements": sorted(elements.values(), key=lambda item: (-item["count"], item["id"])),
        "roles": sorted(
            roles.values(),
            key=lambda item: (_ROLE_ORDER.index(item["role"]) if item["role"] in _ROLE_ORDER else 99),
        ),
    }


def _add_use(entry: dict[str, Any], run_id: str) -> None:
    entry["count"] += 1
    if run_id not in entry["run_ids"]:
        entry["run_ids"].append(run_id)


# --- R3 -----------------------------------------------------------------------

def normalize_motif(text: str) -> str:
    """NFKC, case-folded, with spaces and punctuation removed."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(char for char in folded if unicodedata.category(char)[0] in {"L", "N"})


def trigrams(normalized: str) -> set[str]:
    if len(normalized) < 3:
        return {normalized}
    return {normalized[index:index + 3] for index in range(len(normalized) - 2)}


def similarity(left: str, right: str) -> float:
    """Jaccard similarity of the character 3-gram sets of two normalized motifs."""
    first, second = trigrams(left), trigrams(right)
    union = first | second
    return len(first & second) / len(union) if union else 0.0


def group_motifs(
    items: Sequence[tuple[str, str]],
    *,
    threshold: float = GROUP_SIMILARITY,
    limit: int = MAX_GROUPS,
) -> list[dict[str, Any]]:
    """Group ``(label, motif)`` pairs and return the groups seen in the most runs.

    Motifs whose 3-gram similarity reaches *threshold* (directly or through a
    chain of similar motifs) share a group.  Groups are ordered by the number of
    runs, then the number of motifs, then first appearance.
    """
    entries = [(label, text, normalize_motif(text)) for label, text in items]
    entries = [entry for entry in entries if entry[2]]
    parent = list(range(len(entries)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for first in range(len(entries)):
        for second in range(first + 1, len(entries)):
            if find(first) != find(second) and (
                similarity(entries[first][2], entries[second][2]) >= threshold
            ):
                parent[find(second)] = find(first)

    members: dict[int, list[int]] = {}
    for index in range(len(entries)):
        members.setdefault(find(index), []).append(index)
    groups = []
    for order, indexes in enumerate(sorted(members.values(), key=min)):
        labels = list(dict.fromkeys(entries[index][0] for index in indexes))
        counts = Counter(entries[index][2] for index in indexes)
        first_seen: dict[str, str] = {}
        for index in indexes:
            first_seen.setdefault(entries[index][2], entries[index][1])
        ranked = sorted(
            counts, key=lambda key: (-counts[key], len(first_seen[key]), list(first_seen).index(key))
        )
        groups.append(
            {
                "order": order,
                "labels": labels,
                "size": len(indexes),
                "term": first_seen[ranked[0]],
                "variants": [first_seen[key] for key in ranked[1:1 + MAX_VARIANTS]],
            }
        )
    groups.sort(key=lambda group: (-len(group["labels"]), -group["size"], group["order"]))
    return groups[:limit]


def _text_output(outputs: Mapping[str, Any], task_id: str) -> str | None:
    value = outputs.get(task_id)
    if isinstance(value, str) and value.strip():
        return _one_line(value)
    return None


def _summary_line(run: Mapping[str, Any]) -> str:
    parts = []
    for caption, key in (
        ("願望", "desire"),
        ("敵対者の役割", "adversary_role"),
        ("喪失", "central_loss"),
        ("結末", "ending"),
    ):
        if run.get(key):
            parts.append(f"{caption}：{_clip(run[key], SUMMARY_FIELD_CHARS)}")
    return "／".join(parts)


def report_group(context: CodeTaskContext) -> CodeTaskResult:
    """R3: collect the R2 results, group the motifs and add the R4 tasks."""
    aggregate = context.dependency_outputs["R1.aggregate"]
    outputs = context.dependency_outputs
    runs = []
    pairs: list[tuple[str, str]] = []
    for record in aggregate["runs"]:
        label = record["id"]
        motifs = []
        for number in range(1, MOTIF_SLOTS + 1):
            motif = _text_output(outputs, f"R2.motif-{label}-{number}")
            if motif is not None and normalize_motif(motif) not in {normalize_motif(m) for m in motifs}:
                motifs.append(motif)
        runs.append(
            {
                "id": label,
                "run_id": record["run_id"],
                "desire": record.get("desire"),
                "adversary_role": record.get("adversary_role"),
                "central_loss": _text_output(outputs, f"R2.loss-{label}"),
                "ending": _text_output(outputs, f"R2.ending-{label}"),
                "motifs": motifs,
            }
        )
        pairs.extend((label, motif) for motif in motifs)
    by_label = {run["id"]: run for run in runs}

    groups = []
    add_tasks: list[TaskSpec] = []
    for number, group in enumerate(group_motifs(pairs), start=1):
        group_id = f"g{number}"
        labels = sorted(group["labels"], key=lambda label: int(label[1:]))
        groups.append(
            {
                "id": group_id,
                "term": group["term"],
                "variants": group["variants"],
                "labels": labels,
                "run_ids": [by_label[label]["run_id"] for label in labels],
                "summaries": [
                    line
                    for line in (_summary_line(by_label[label]) for label in labels[:GROUP_SUMMARIES])
                    if line
                ] or [group["term"]],
            }
        )
        add_tasks.append(
            TaskSpec(
                f"R4.observation-{group_id}", "R4.observation",
                deps=(context.task_id,), index=(group_id,),
            )
        )
        add_tasks.append(
            TaskSpec(
                f"R4.question-{group_id}", "R4.question",
                deps=(context.task_id, f"R4.observation-{group_id}"), index=(group_id,),
            )
        )
    r4_ids = tuple(spec.task_id for spec in add_tasks)
    add_tasks.append(
        TaskSpec("R5.assemble", "R5.assemble", deps=("R1.aggregate", context.task_id, *r4_ids))
    )
    return CodeTaskResult(output={"runs": runs, "groups": groups}, add_tasks=add_tasks)


# --- R5 -----------------------------------------------------------------------

def build_report(
    aggregate_output: Mapping[str, Any],
    group_output: Mapping[str, Any],
    outputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Assemble the ``report.json`` document (schemas/report.schema.json)."""
    runs = group_output["runs"]
    return {
        "schema_version": 1,
        "batch_id": aggregate_output["batch_id"],
        "run_count": len(runs),
        "run_ids": [run["run_id"] for run in runs],
        "aggregate": aggregate_output["aggregate"],
        "motif_groups": [
            {
                "id": group["id"],
                "term": group["term"],
                "variants": group["variants"],
                "run_ids": group["run_ids"],
                "observation": _text_output(outputs, f"R4.observation-{group['id']}"),
                "question": _text_output(outputs, f"R4.question-{group['id']}"),
            }
            for group in group_output["groups"]
        ],
        "runs": [
            {key: run[key] for key in ("run_id", "desire", "adversary_role", "central_loss", "ending", "motifs")}
            for run in runs
        ],
    }


def _cell(value: Any) -> str:
    return _one_line(value).replace("|", "\\|")


def _run_list(run_ids: Sequence[str]) -> str:
    return "、".join(run_ids)


def render_markdown(report: Mapping[str, Any]) -> str:
    """Render ``report.md``: the tally (R1), motif groups and questions (R3, R4), run summaries (R2)."""
    aggregate = report["aggregate"]
    lines = [
        f"# バッチの傾向分析：{report['batch_id']}",
        "",
        f"対象の run：{report['run_count']}件（completed のもの）。"
        "以下は正本に現れた傾向を示すもので、解釈を押し付けるものではない。",
        "",
        "## 集計",
        "",
        "### プロット型",
        "",
        "| プロット型 | 回数 | 根拠の run |",
        "|---|---|---|",
    ]
    for item in aggregate["plot_types"]:
        lines.append(f"| {_cell(item['name'])}（{item['id']}） | {item['count']} | {_run_list(item['run_ids'])} |")
    ratio = aggregate["input_ratio"]
    lines += ["", "### 入力由来の比率", ""]
    if ratio["per_run"]:
        lines.append(f"最小 {ratio['min']}、最大 {ratio['max']}、平均 {ratio['mean']}")
        lines.append("")
        lines += ["| run | 比率 |", "|---|---|"]
        lines += [f"| {item['run_id']} | {item['value']} |" for item in ratio["per_run"]]
    lines += ["", "### 割り当てた要素", "", "| 要素 | 軸 | 回数 | 根拠の run |", "|---|---|---|---|"]
    for item in aggregate["elements"]:
        lines.append(
            f"| [{item['id']}] {_cell(item['text'])} | {item['axis']} | {item['count']} | {_run_list(item['run_ids'])} |"
        )
    lines += ["", "### 役の出現回数", "", "| 役 | 回数 | 根拠の run |", "|---|---|---|"]
    for item in aggregate["roles"]:
        lines.append(f"| {item['role']} | {item['count']} | {_run_list(item['run_ids'])} |")

    lines += ["", "## モチーフ群と問い", ""]
    if not report["motif_groups"]:
        lines += ["モチーフは得られなかった。", ""]
    for group in report["motif_groups"]:
        lines.append(f"### {group['id']}：{group['term']}")
        lines.append("")
        if group["variants"]:
            lines.append(f"- 近い言い方：{'、'.join(group['variants'])}")
        lines.append(f"- 傾向：{group['observation'] or '（得られなかった）'}")
        lines.append(f"- 問い：{group['question'] or '（得られなかった）'}")
        lines.append(f"- 根拠の run：{_run_list(group['run_ids'])}")
        lines.append("")

    lines += ["## run ごとの要約", ""]
    for run in report["runs"]:
        lines.append(f"### {run['run_id']}")
        lines.append("")
        for caption, key in (
            ("主人公の願望", "desire"),
            ("敵対者の役割", "adversary_role"),
            ("中心的な喪失", "central_loss"),
            ("結末", "ending"),
        ):
            lines.append(f"- {caption}：{run[key] or '（なし）'}")
        lines.append(f"- モチーフ：{'、'.join(run['motifs']) or '（なし）'}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def report_assemble(context: CodeTaskContext) -> CodeTaskResult:
    """R5: write ``report.json`` and ``report.md`` into the report directory."""
    report = build_report(
        context.dependency_outputs["R1.aggregate"],
        context.dependency_outputs["R3.group"],
        context.dependency_outputs,
    )
    validate_document(report, REPORT_SCHEMA)
    atomic_write_json(context.run_dir / "report.json", report)
    atomic_write_text(context.run_dir / "report.md", render_markdown(report))
    return CodeTaskResult(output=report)
