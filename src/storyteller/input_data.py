"""S0 input normalization and personal-information checks."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path


MAX_FREE_INPUT_CHARS = 20_000
MAX_PARAGRAPH_CHARS = 1_200
_SENTENCE_ENDINGS = frozenset("。！？!?.")


class InputError(ValueError):
    """The supplied input cannot be normalized for a run."""


class PersonalInformationError(InputError):
    """Personal information was found in input and the input was rejected."""

    def __init__(self, findings: list["PiiFinding"]) -> None:
        self.findings = tuple(findings)
        details = "、".join(
            f"{finding.location}、文字位置 {finding.position}、{finding.kind}"
            for finding in findings
        )
        super().__init__(f"個人情報を検出したため取り込みを拒否しました: {details}")


@dataclass(frozen=True)
class PiiFinding:
    """A non-sensitive description of one detected pattern."""

    location: str
    position: int
    kind: str


@dataclass(frozen=True)
class FreeInput:
    """The normalized free-input document and its source digest."""

    source_sha256: str
    paragraphs: tuple[dict[str, str], ...]

    def as_document(self) -> dict[str, object]:
        return {
            "kind": "free",
            "source_sha256": self.source_sha256,
            "paragraphs": [dict(paragraph) for paragraph in self.paragraphs],
        }


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("メールアドレス", re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")),
    ("URL", re.compile(r"(?:https?://|www\.)[^\s<>]+", re.IGNORECASE)),
    ("郵便番号", re.compile(r"(?:〒\s*\d{3}[-ー]?\d{4}|(?<!\d)\d{3}[-ー]\d{4}(?![-ー\d]))")),
    ("電話番号", re.compile(r"(?:\+81[-ー\s]?\d{1,4}[-ー\s]?\d{2,4}[-ー\s]?\d{3,4}|0\d{1,4}[-ー\s]?\d{2,4}[-ー\s]?\d{3,4})")),
    ("SNSハンドル", re.compile(r"(?<![A-Za-z0-9_])@[A-Za-z0-9_]+")),
    (
        "住所",
        re.compile(
            r"(?:北海道|東京都|(?:京都|大阪)府|[一-龯]{2,3}県)"
            r"[^\n、，。\s]{0,24}(?:市|区|町|村)"
            r"(?:[^\n、，。\s]{0,12}(?:丁目|番地|番|号))?"
        ),
    ),
    ("7桁以上の連続した数字", re.compile(r"(?<!\d)\d{7,}(?!\d)")),
)


def read_free_input(path: str | Path) -> FreeInput:
    """Read, validate, and normalize a free-input UTF-8 file."""

    source_path = Path(path)
    try:
        source_bytes = source_path.read_bytes()
    except OSError as error:
        raise InputError(f"自由入力を読み込めません: {source_path}") from error
    try:
        raw = source_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise InputError(f"自由入力は UTF-8 でなければなりません: {source_path}") from error

    normalized = normalize_text(raw)
    if len(normalized) > MAX_FREE_INPUT_CHARS:
        raise InputError("自由入力は20000字以内で指定してください")
    paragraphs = split_paragraphs(normalized)
    if not paragraphs:
        raise InputError("自由入力に空でない段落がありません")

    findings = detect_free_pii(paragraphs)
    if findings:
        raise PersonalInformationError(findings)

    return FreeInput(
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        paragraphs=tuple(
            {"id": f"p{index:03d}", "text": paragraph}
            for index, paragraph in enumerate(paragraphs, start=1)
        ),
    )


def normalize_text(value: str) -> str:
    """Normalize line endings and Unicode while preserving meaningful text."""

    if not isinstance(value, str):
        raise InputError("自由入力は文字列でなければなりません")
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n")).strip()


def split_paragraphs(value: str) -> list[str]:
    """Split blank-line-separated text and cap every paragraph at 1200 chars."""

    candidates = re.split(r"\n[ \t]*\n+", value)
    paragraphs: list[str] = []
    for candidate in candidates:
        paragraph = candidate.strip()
        if not paragraph:
            continue
        paragraphs.extend(_split_long_paragraph(paragraph))
    return paragraphs


def detect_free_pii(paragraphs: list[str] | tuple[dict[str, str], ...]) -> list[PiiFinding]:
    """Detect only the PII classes that the free-input contract covers."""

    findings: list[PiiFinding] = []
    for index, paragraph_value in enumerate(paragraphs, start=1):
        paragraph = paragraph_value if isinstance(paragraph_value, str) else paragraph_value["text"]
        location = f"段落 p{index:03d}"
        occupied: list[tuple[int, int]] = []
        matches: list[tuple[int, int, str]] = []
        for kind, pattern in _PATTERNS:
            for match in pattern.finditer(paragraph):
                start, end = match.span()
                if any(start < occupied_end and end > occupied_start for occupied_start, occupied_end in occupied):
                    continue
                matches.append((start, end, kind))
                occupied.append((start, end))
        for start, _end, kind in sorted(matches, key=lambda item: (item[0], item[1], item[2])):
            findings.append(PiiFinding(location, start + 1, kind))
    return findings


def _split_long_paragraph(paragraph: str) -> list[str]:
    result: list[str] = []
    remaining = paragraph
    while len(remaining) > MAX_PARAGRAPH_CHARS:
        boundary = max(
            (index + 1 for index, character in enumerate(remaining[:MAX_PARAGRAPH_CHARS]) if character in _SENTENCE_ENDINGS),
            default=MAX_PARAGRAPH_CHARS,
        )
        result.append(remaining[:boundary].strip())
        remaining = remaining[boundary:].strip()
    if remaining:
        result.append(remaining)
    return result
