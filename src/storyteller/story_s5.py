"""Small S5 code-task helpers (story-pipeline.md S5).

The only code task defined here is ``S5.relationship_context``: it resolves
one character's name, role, and short introduction so a
``S5.relationship-<subject>-<target>`` card can be given exactly what
story-pipeline.md S5 calls for ("相手の人物の名前・役・紹介だけ"), without
needing the LLM task's shared card to address two different characters at
once (the selector language's ``{slot}`` placeholder only ever resolves to
one task index, task-model.md §2.4).  This task's own index is the target
character's ID, so the three input slots resolve normally with ``{slot}``,
the same way every other single-character S5 item does.
"""

from __future__ import annotations

from .orchestrator import CodeTaskContext


def story_s5_relationship_context(context: CodeTaskContext) -> dict[str, str]:
    """Return ``{"id", "name", "role", "intro"}`` for this task's target character."""

    index = context.task.get("index")
    if not isinstance(index, (list, tuple)) or not index or not isinstance(index[0], str) or not index[0]:
        raise ValueError("S5.relationship_context の添字が不正です")
    target_id = index[0]

    name = context.inputs.get("name")
    role = context.inputs.get("role")
    intro = context.inputs.get("intro")
    if not isinstance(name, str) or not name:
        raise ValueError("相手の人物の名前が解決できません")
    if not isinstance(role, str) or not role:
        raise ValueError("相手の人物の役が解決できません")
    if not isinstance(intro, str) or not intro:
        raise ValueError("相手の人物の紹介が解決できません")

    return {"id": target_id, "name": name, "role": role, "intro": intro}


__all__ = ["story_s5_relationship_context"]
