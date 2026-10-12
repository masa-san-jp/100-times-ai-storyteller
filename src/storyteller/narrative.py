"""S0 narrative import: schema validation and normalization (input.md section 2)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator

from .input_data import InputError, PersonalInformationError, normalize_text
from .new_run import create_run_from_paragraphs
from .pii_check import detect_narrative_pii

MIN_FILLED_ITEMS = 3
MAX_ITEM_CHARS = 300
_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "narrative.schema.json"

# key -> (item name, question), in input.md section 2.2 order.
NARRATIVE_ITEMS: dict[str, tuple[str, str]] = {
    "author": ("人となり", "あなたは、どのような傾向を持つ人ですか"),
    "missing": ("欠落", "自分に欠けていると感じがちなものは何ですか。それは何を象徴していますか"),
    "status": ("現在地", "その欠落に対して、今どのような状態にあると感じていますか"),
    "memories": ("形成", "今の傾向をつくったのは、どのような種類の経験ですか"),
    "mission": ("使命", "欠落を埋めるために、何をしなければならないと感じていますか"),
    "success": ("成功像", "満たされた状態とは、どのようなものだと思いますか"),
    "loss": ("代償", "そのためなら失ってもよい、または失うのが怖いものは何ですか"),
    "taboo": ("禁忌", "越えたくない一線は何ですか"),
    "inhibit": ("抑制", "自分を抑えているものは何ですか"),
    "daily": ("日常", "普段の日常は、どのような質感のものですか"),
    "change": ("変化の契機", "どのようなことがあると、自分は変わると思いますか"),
    "acceptance": ("受容", "受け入れられること、受け入れにくいことは何ですか"),
    "desire": ("秘めた願望", "誰にも話していない願望は何ですか"),
}


@dataclass(frozen=True)
class NarrativeInput:
    """A validated narrative; empty items stay empty strings."""

    source_sha256: str
    items: dict[str, str]

    @property
    def paragraphs(self) -> tuple[dict[str, str], ...]:
        """One S1 unit per non-empty item, identified by its item key."""

        return tuple(
            {"id": key, "item": NARRATIVE_ITEMS[key][0],
             "question": NARRATIVE_ITEMS[key][1], "text": text}
            for key, text in self.items.items()
            if text
        )

    def as_document(self) -> dict[str, object]:
        return {
            "kind": "narrative",
            "source_sha256": self.source_sha256,
            "items": dict(self.items),
            "paragraphs": [dict(paragraph) for paragraph in self.paragraphs],
        }


def read_narrative(path: str | Path) -> NarrativeInput:
    """Read, validate, and normalize a narrative JSON file."""

    source_path = Path(path)
    try:
        source_bytes = source_path.read_bytes()
    except OSError as error:
        raise InputError(f"ナラティブを読み込めません: {source_path}") from error
    try:
        value = json.loads(source_bytes.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise InputError(f"ナラティブは UTF-8 でなければなりません: {source_path}") from error
    except json.JSONDecodeError as error:
        raise InputError(f"ナラティブが JSON として読めません: {error.msg}（{error.lineno}行 {error.colno}桁）") from error

    if not isinstance(value, dict):
        raise InputError("ナラティブは13項目の JSON オブジェクトでなければなりません")
    # Normalize before measuring so the 300-character limit counts NFC characters.
    items = {
        key: normalize_text(text) if isinstance(text, str) else text
        for key, text in value.items()
    }
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(items),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    if errors:
        # Report item keys and the rule only; never echo the user's text.
        raise InputError("ナラティブの形式が不正です: " + "、".join(_describe(error) for error in errors))

    findings = detect_narrative_pii(items)
    if findings:
        raise PersonalInformationError(findings)

    filled = sum(1 for text in items.values() if text)
    if filled < MIN_FILLED_ITEMS:
        raise InputError(
            f"ナラティブは空でない項目が{MIN_FILLED_ITEMS}つ以上必要です（現在 {filled} 項目）"
        )
    return NarrativeInput(
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        items={key: items[key] for key in NARRATIVE_ITEMS},
    )


def _describe(error) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    if error.validator == "required":
        return f"項目 {error.message.split(chr(39))[1]} がありません"
    if error.validator == "additionalProperties":
        return "未知の項目があります"
    if error.validator == "maxLength":
        return f"項目 {path} が{MAX_ITEM_CHARS}字を超えています"
    if error.validator == "type":
        return f"項目 {path} は文字列でなければなりません"
    return f"項目 {path} が不正です"


def create_narrative_run(data_dir, input_path, **options) -> str:
    """Validate a narrative, derive S0, and create the initial S1 DAG."""

    narrative = read_narrative(input_path)
    return create_run_from_paragraphs(
        data_dir,
        narrative.as_document(),
        narrative.paragraphs,
        input_type="narrative",
        **options,
    )
