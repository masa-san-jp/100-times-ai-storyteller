"""Narrative PII detection (input.md section 4) and ``st check-input``.

Findings carry only the location, character position, and kind. The matched
text is never stored, printed, or logged.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .input_data import (
    InputError,
    PersonalInformationError,
    PiiFinding,
    find_pii,
    normalize_text,
    split_paragraphs,
)

# Concrete dates are detected in narratives only (free input may contain fictional dates).
_ERA = r"(?:令和|平成|昭和|大正|明治)"
_DATE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "具体的な年月日",
        re.compile(
            r"(?<!\d)(?:\d{4}|" + _ERA + r"\s*(?:\d{1,2}|元))\s*年(?:\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?)?"
            r"|(?<!\d)\d{4}\s*[-/.／]\s*\d{1,2}\s*[-/.／]\s*\d{1,2}(?!\d)"
        ),
    ),
)


def detect_narrative_pii(items: dict[str, str]) -> list[PiiFinding]:
    """Detect PII (including dates) in each narrative item, in item order."""

    from .narrative import NARRATIVE_ITEMS

    findings: list[PiiFinding] = []
    for key, text in items.items():
        if not isinstance(text, str) or not text:
            continue
        name = NARRATIVE_ITEMS[key][0] if key in NARRATIVE_ITEMS else key
        findings.extend(find_pii(text, f"項目 {key}（{name}）", _DATE_PATTERNS))
    return findings


def check_input_file(path: str | Path) -> list[PiiFinding]:
    """Return PII findings for a narrative (``.json``) or free-input file."""

    source = Path(path)
    try:
        raw = source.read_bytes().decode("utf-8")
    except OSError as error:
        raise InputError(f"入力を読み込めません: {source}") from error
    except UnicodeDecodeError as error:
        raise InputError(f"入力は UTF-8 でなければなりません: {source}") from error
    if source.suffix.lower() == ".json":
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise InputError(f"ナラティブが JSON として読めません: {error.msg}") from error
        if not isinstance(value, dict):
            raise InputError("ナラティブは13項目の JSON オブジェクトでなければなりません")
        items = {
            key: normalize_text(text) for key, text in value.items() if isinstance(text, str)
        }
        return detect_narrative_pii(items)
    findings: list[PiiFinding] = []
    for index, paragraph in enumerate(split_paragraphs(normalize_text(raw)), start=1):
        findings.extend(find_pii(paragraph, f"段落 p{index:03d}"))
    return findings


def _run_check_input(args: Any) -> int:
    findings = check_input_file(args.path)
    if findings:
        raise PersonalInformationError(findings)
    print("個人情報は検出されませんでした")
    return 0


def register_cli_commands(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "check-input", help="detect personal information in an input file"
    )
    parser.add_argument("path")
    parser.set_defaults(command_handler=_run_check_input)
