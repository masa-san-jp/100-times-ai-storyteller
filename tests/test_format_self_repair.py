"""#224: F4/F3 repair what the code can fix instead of leaving （未定）."""

from __future__ import annotations

from copy import deepcopy
import json

import yaml

from storyteller.format_scene import fountain_errors, separate_character_cues, strip_body_headings
from tests.test_formatting import PROFILE, _body, _drive, _start, dummy_format  # noqa: F401


def _part(h, run, task_id: str) -> dict:
    plan = json.loads((h.run_dir(run) / "tasks" / "F1.split-dummy" / "output.json").read_text(encoding="utf-8"))
    return next(item for item in plan["parts"] if f"-{item['id']}-" in task_id)


def _responder(h, run, write):
    def respond(task_id: str, card: str) -> str:
        if task_id.startswith("F4.judge"):
            return '{"answer": "no", "reason": "問題はない。"}'
        return write(task_id)
    return respond


def _assembled(h, run) -> str:
    return (h.run_dir(run) / "formats" / "dummy" / "dummy.md").read_text(encoding="utf-8")


def _writes(manifest) -> dict:
    return {name: task for name, task in manifest["tasks"].items() if task["type"].startswith("F2.")}


def test_strip_body_headings_removes_headings_sections_and_synopses_only() -> None:
    body = ".灯台の水面。 - 夜明け前\n\n# 第1幕\n= 要約\n\n\n\n波が寄せる。\n\n@カピオ\n……来た。\n"
    repaired, removed = strip_body_headings(body)
    assert removed == [".灯台の水面。 - 夜明け前", "# 第1幕", "= 要約"]
    assert repaired == "波が寄せる。\n\n@カピオ\n……来た。"
    assert fountain_errors(repaired) == []
    assert strip_body_headings("波が寄せる。\n") == ("波が寄せる。\n", [])
    # An ellipsis is not a forced scene heading.
    assert strip_body_headings("...と言った。") == ("...と言った。", [])


def test_separate_character_cues_adds_the_missing_blank_lines() -> None:
    body = "鐘が鳴る。\n@ダクバン\n歌え。\n@リオ\n(小声で)\nまだだ。\n\n@ダクバン\nもう一度。"
    repaired, added = separate_character_cues(body)
    assert added == 2
    assert repaired == "鐘が鳴る。\n\n@ダクバン\n歌え。\n\n@リオ\n(小声で)\nまだだ。\n\n@ダクバン\nもう一度。"
    assert fountain_errors(body) and fountain_errors(repaired) == []
    assert separate_character_cues("鐘が鳴る。") == ("鐘が鳴る。", 0)


def _scene_profile(dummy_format) -> None:
    profile = deepcopy(PROFILE)
    profile["checks"]["code"].append("fountain_syntax")
    (dummy_format / "dummy" / "profile.yaml").write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")


def test_cues_without_a_blank_line_are_repaired_without_invalidation(tmp_path, dummy_format) -> None:
    # The shape gpt-oss:20b wrote on 2026-10-28: a heading, then cues right after lines.
    _scene_profile(dummy_format)
    _, run, h = _start(tmp_path, dummy_format, events=3)
    _drive(h, run, _responder(h, run, lambda task_id: ".夕暮れの鐘楼\n" + _body(task_id, h, run)[:-1]
                              + "\n@ダクバン\n歌え。"))
    manifest = h.load_run(run)
    assert manifest["status"] == "completed"
    assert all(task["state"] == "done" and task["invalidations"] == 0 for task in _writes(manifest).values())
    assert any("人物名の前の空行をコードで補った：1か所" in warning for warning in manifest["warnings"])
    assert "あ\n\n@ダクバン\n歌え。" in _assembled(h, run)


def test_scene_headings_are_removed_by_code_without_invalidation(tmp_path, dummy_format) -> None:
    _scene_profile(dummy_format)
    _, run, h = _start(tmp_path, dummy_format, events=3)
    _drive(h, run, _responder(h, run, lambda task_id: ".灯台の水面\n\n" + _body(task_id, h, run)))
    manifest = h.load_run(run)
    assert manifest["status"] == "completed"
    assert all(task["state"] == "done" and task["invalidations"] == 0 for task in _writes(manifest).values())
    assert any("本文の見出し・節・要約の行をコードで除去：.灯台の水面" in warning for warning in manifest["warnings"])
    text = _assembled(h, run)
    assert ".灯台の水面" not in text and "（未定）" not in text
    # The saved F2 output keeps what the executor wrote.
    write = next(iter(_writes(manifest)))
    assert (h.run_dir(run) / "tasks" / write / "output.md").read_text(encoding="utf-8").startswith(".灯台の水面")


def test_a_body_short_by_less_than_half_is_adopted_at_the_invalidation_limit(tmp_path, dummy_format) -> None:
    _, run, h = _start(tmp_path, dummy_format, events=3)

    def write(task_id: str) -> str:
        half = (_part(h, run, task_id)["min_chars"] + 1) // 2
        return "あ" * (half - 1) + "。"

    log = _drive(h, run, _responder(h, run, write))
    manifest = h.load_run(run)
    assert manifest["status"] == "completed"
    for name, task in _writes(manifest).items():
        assert task["state"] == "done" and task["invalidations"] == 2, name
        # The limit is reached after two repairs that asked to write more.
        assert sum(task_id == name for task_id, _, _ in log) == 3
    assert any("字数の下限（半分以上）の不合格が無効化上限に達したため、最後の本文を採用" in warning
               for warning in manifest["warnings"])
    assert "（未定）" not in _assembled(h, run)


def test_a_body_shorter_than_half_is_still_skipped(tmp_path, dummy_format) -> None:
    _, run, h = _start(tmp_path, dummy_format, events=3)

    def write(task_id: str) -> str:
        half = (_part(h, run, task_id)["min_chars"] + 1) // 2
        return "あ" * (half - 2) + "。"

    _drive(h, run, _responder(h, run, write))
    manifest = h.load_run(run)
    assert manifest["status"] == "completed"
    assert all(task["state"] == "skipped" for task in _writes(manifest).values())
    assert "（未定）" in _assembled(h, run)


def test_other_code_failures_with_a_short_body_are_not_adopted(tmp_path, dummy_format) -> None:
    _, run, h = _start(tmp_path, dummy_format, events=3)

    def write(task_id: str) -> str:
        # Long enough for half, but it does not end with a complete sentence.
        half = (_part(h, run, task_id)["min_chars"] + 1) // 2
        return "あ" * half

    _drive(h, run, _responder(h, run, write))
    manifest = h.load_run(run)
    assert all(task["state"] == "skipped" for task in _writes(manifest).values())
