"""S0 narrative intake: schema validation and normalization (input.md §2)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .input_data import InputError, normalize_text
from .validation import SchemaValidationError, validate_document

MAX_ITEM_CHARS = 300
MIN_NONEMPTY_ITEMS = 3
_SCHEMA = Path(__file__).resolve().parents[2] / "schemas" / "narrative.schema.json"

# Item key -> (item name, question). Order follows input.md §2.2.
NARRATIVE_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("author", "人となり", "あなたは、どのような傾向を持つ人ですか"),
    ("missing", "欠落", "自分に欠けていると感じがちなものは何ですか。それは何を象徴していますか"),
    ("status", "現在地", "その欠落に対して、今どのような状態にあると感じていますか（満たされた／かつては／まだ／わからない）"),
    ("memories", "形成", "今の傾向をつくったのは、どのような種類の経験ですか"),
    ("mission", "使命", "欠落を埋めるために、何をしなければならないと感じていますか"),
    ("success", "成功像", "満たされた状態とは、どのようなものだと思いますか"),
    ("loss", "代償", "そのためなら失ってもよい、または失うのが怖いものは何ですか"),
    ("taboo", "禁忌", "越えたくない一線は何ですか"),
    ("inhibit", "抑制", "自分を抑えているものは何ですか"),
    ("daily", "日常", "普段の日常は、どのような質感のものですか"),
    ("change", "変化の契機", "どのようなことがあると、自分は変わると思いますか"),
    ("acceptance", "受容", "受け入れられること、受け入れにくいことは何ですか"),
    ("desire", "秘めた願望", "誰にも話していない願望は何ですか"),
)
_NAMES = {key: name for key, name, _ in NARRATIVE_ITEMS}


@dataclass(frozen=True)
class NarrativeInput:
    """A validated narrative: normalized items and the source digest."""

    source_sha256: str
    items: dict[str, str]

    @property
    def nonempty_keys(self) -> tuple[str, ...]:
        return tuple(key for key, _, _ in NARRATIVE_ITEMS if self.items[key])

    def as_document(self) -> dict[str, object]:
        """The input.json form. ``paragraphs`` holds one entry per non-empty
        item, with the item key as id, so S1 takes the item (question and
        answer) as its only input."""

        return {
            "kind": "narrative",
            "source_sha256": self.source_sha256,
            "items": dict(self.items),
            "paragraphs": [
                {
                    "id": key,
                    "text": f"問い（{name}）：{question}\n回答：{self.items[key]}",
                }
                for key, name, question in NARRATIVE_ITEMS
                if self.items[key]
            ],
        }


def read_narrative_input(path: str | Path) -> NarrativeInput:
    """Read, validate, and normalize a narrative JSON file (UTF-8)."""

    source_path = Path(path)
    try:
        source_bytes = source_path.read_bytes()
    except OSError as error:
        raise InputError(f"ナラティブを読み込めません: {source_path}") from error
    try:
        raw = json.loads(source_bytes.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise InputError(f"ナラティブは UTF-8 でなければなりません: {source_path}") from error
    except json.JSONDecodeError as error:
        raise InputError(f"ナラティブが JSON として不正です: 行 {error.lineno}、列 {error.colno}") from error
    if not isinstance(raw, dict):
        raise InputError("ナラティブは13項目のJSONオブジェクトでなければなりません")

    # Normalize first, so the 300-char limit applies to the stored value.
    # Whitespace-only items become empty strings; non-strings are left to the schema.
    items = {
        key: normalize_text(value) if isinstance(value, str) else value
        for key, value in raw.items()
    }
    try:
        validate_document(items, _SCHEMA, source_path=source_path)
    except SchemaValidationError as error:
        raise InputError(_describe_schema_errors(error)) from error

    nonempty = sum(1 for value in items.values() if value)
    if nonempty < MIN_NONEMPTY_ITEMS:
        raise InputError(
            f"ナラティブは空でない項目が{MIN_NONEMPTY_ITEMS}つ以上必要です（現在 {nonempty}）"
        )
    return NarrativeInput(
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        items={key: items[key] for key, _, _ in NARRATIVE_ITEMS},
    )


def _describe_schema_errors(error: SchemaValidationError) -> str:
    """Describe violations by item key only; never echo input values."""

    problems: list[str] = []
    for item in getattr(error, "errors", ()):
        path = [str(part) for part in item.path]
        if item.validator == "required":
            missing = [key for key in item.validator_value if key not in item.instance]
            problems.extend(f"項目 {key} がありません" for key in missing)
        elif item.validator == "additionalProperties":
            extra = sorted(key for key in item.instance if key not in _NAMES)
            problems.append("未知の項目があります（" + "、".join(extra) + "）")
        elif item.validator == "maxLength":
            problems.append(f"項目 {path[0]} が{MAX_ITEM_CHARS}字を超えています")
        elif item.validator == "type" and path:
            problems.append(f"項目 {path[0]} は文字列でなければなりません")
        else:
            problems.append(f"項目 {'.'.join(path) or '(全体)'} が不正です")
    return "ナラティブが不正です: " + "、".join(problems or ["スキーマ違反"])
