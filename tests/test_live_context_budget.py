"""P1-69 (9): the largest `saga` cards fit `context_length` on a real `gpt-oss:20b`.

Live check against Ollama.  Skipped unless ``STORYTELLER_LIVE_OLLAMA=1`` is set and the
server answers; the measured values are printed (``pytest -s``).  Each card is sent once
with ``num_ctx = context_length``; only the input size matters, so the reply is capped at
a few tokens (``num_predict``) and the check adds ``max_tokens`` (8192) to
``prompt_eval_count`` as the specification says.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from storyteller.adapter import load_model_config
from storyteller.cards import generate_task_card, prepare_task_inputs
from storyteller.event_fields import EVENT_FIELDS, EVENT_LABELS
from storyteller.selectors import resolve_inputs
from storyteller.story_ledger import augment_sources
from storyteller.tables import load_table

from tests.test_staged_plot import ROOT, _definition, _expand, _sources, _thread

ENDPOINT = os.environ.get("STORYTELLER_OLLAMA_ENDPOINT", "http://127.0.0.1:11434")
MODEL = "gpt-oss:20b"


def _reachable() -> bool:
    try:
        with urlopen(f"{ENDPOINT}/api/tags", timeout=3):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    os.environ.get("STORYTELLER_LIVE_OLLAMA") != "1" or not _reachable(),
    reason="set STORYTELLER_LIVE_OLLAMA=1 with Ollama (gpt-oss:20b) running",
)


def _saga_cards() -> dict[str, str]:
    plots = [t["id"] for t in load_table("plot_types", repository_root=ROOT)["types"]][:6]
    threads = [_thread(n, part=n, plot=plot, events=8) for n, plot in enumerate(plots, 1)]
    result = _expand(threads)
    long_row = "あ" * 300
    slots = result.output["slots"]
    structures = load_table("structures", repository_root=ROOT)["templates"]
    stage_definition = max((s["definition"] for t in structures.values() for s in t["stages"]), key=len)
    stage_guidance = max((s["guidance"] for t in structures.values() for s in t["stages"]), key=len)
    for slot in slots:
        slot["stage"]["definition"], slot["stage"]["guidance"] = stage_definition, stage_guidance
        slot["characters"] = [
            {"id": f"c{n}", "name": "名" * 8, "role": "supporter", "motive": "動" * 120,
             "intro": "紹" * 50, "voice": "声" * 800}
            for n in range(1, 5)
        ]
        slot["world_sections"] = [{"id": f"w{n}", "name": "場", "body": "世" * 900} for n in range(2)]
    ledgers = {name: [long_row] * 400 for name in ("input_ledger", "cast_ledger", "world_ledger")}
    output, outputs = _sources(result, slots[-1]["id"])
    output["ledgers"] = {**output["ledgers"], **ledgers}
    for element in output["skeleton"]["elements"]:
        outputs[element["task"]] = "骨" * 200
    for slot in slots:
        outputs[f"S7.assemble-{slot['id']}"] = {
            "who": ["c1"], **{key: "出" * 120 for key in (
                "when", "where", "why", "intent", "what", "result", "emotion", "foreshadowing")},
        }
    cards: dict[str, str] = {}

    def render(task_type: str, task_id: str, index: list[str], *, retry: str | None = None,
               extra: dict[str, object] | None = None) -> str:
        definition = _definition(task_type)
        task = {"type": task_type, "index": index, "deps": [
            e["task"] for e in output["skeleton"]["elements"] if e["task"] != task_id]}
        expand = {**output, "event_field": "何が起きたか"}
        sources = augment_sources(None, None, task_id, task, {"S6.expand": expand}, outputs)
        sources = {**sources, "S7.assemble": {
            slot["id"]: outputs[f"S7.assemble-{slot['id']}"] for slot in slots}}
        inputs = resolve_inputs(definition, sources, None, index=index)
        inputs.update(extra or {})
        fitted = prepare_task_inputs(definition, inputs=inputs)
        return generate_task_card(definition, "tk", inputs=fitted, retry_reason=retry)

    first = output["skeleton"]["elements"]
    first_turn = next(e for e in first if e["kind"] == "turn" and e["thread"] == "t001" and e["n"] == 1)
    cards["skeleton_turn"] = render(
        "S6.skeleton_turn", first_turn["task"], [first_turn["thread"], str(first_turn["n"])],
        extra={"absent_role_note": "不" * 80})
    last = slots[-1]["id"]
    # The last field (foreshadowing) has all seven other fields decided, each at 120 chars.
    event_extra = {"decided_fields": {
        EVENT_LABELS[field]: "文" * 120 for field in EVENT_FIELDS if field != "foreshadowing"}}
    cards["event"] = render(
        "S7.event", f"S7.event-{last}-foreshadowing", [last, "foreshadowing"], extra=event_extra)
    cards["detail"] = render(
        "S7.detail", f"S7.detail-{last}-b1", [last, "1"], extra={"previous_scene": "前" * 600})
    cards["repair"] = render(
        "S7.event", f"S7.event-{last}-foreshadowing", [last, "foreshadowing"], extra=event_extra,
        retry="前回の出力は次の理由で不合格でした。" + "理" * 300)
    return cards


def test_largest_saga_cards_fit_the_context_length() -> None:
    model = load_model_config(MODEL, config_path=Path(ROOT) / "config" / "models.yaml")
    assert model.context_length == 32768
    for name, card in _saga_cards().items():
        payload = {
            "model": MODEL, "stream": False, "think": model.think,
            "messages": [{"role": "user", "content": card}],
            "options": {"temperature": model.temperature, "num_predict": 8, "num_ctx": model.context_length},
        }
        request = Request(
            f"{ENDPOINT}/api/chat", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        started = time.monotonic()
        with urlopen(request, timeout=900) as response:
            document = json.loads(response.read().decode("utf-8"))
        elapsed = time.monotonic() - started
        evaluated = document["prompt_eval_count"]
        print(f"{name}: chars={len(card)} prompt_eval_count={evaluated} "
              f"+max_tokens={evaluated + model.max_tokens} seconds={elapsed:.1f}")
        assert evaluated + model.max_tokens <= model.context_length, name
