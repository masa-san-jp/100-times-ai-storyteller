"""Appending S2 counter elements to the supplemental tables.

story-pipeline S2: a counter element is appended to ``tables/<axis>.json``
under ``tables.lock`` only when an executor's submission passes validation
(never when a task becomes ``done`` from the cache).  An element that is
already in the tables after normalization is not appended.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .table_edit import TableEditError, add_row


def append_counter_element(
    data_dir: Path, run_id: str, task_id: str,
    card_inputs: Mapping[str, Any], text: Any,
) -> tuple[str | None, str | None]:
    """Append a passed counter element and return ``(row_id, warning)``.

    ``row_id`` is None when nothing was appended.  ``warning`` is set only when
    the element could not be appended for a reason other than a duplicate.
    """

    axis = card_inputs.get("axis")
    axis_key = axis.get("key") if isinstance(axis, Mapping) else None
    if not isinstance(axis_key, str) or not isinstance(text, str):
        return None, f"{task_id}: 対極要素を増補テーブルに追記できません（入力が不正）"
    try:
        return add_row(Path(data_dir), axis_key, text, f"{run_id}/{task_id}"), None
    except TableEditError as error:
        if str(error).startswith("duplicates "):
            return None, None
        return None, f"{task_id}: 対極要素を増補テーブルに追記できません: {error}"
