from __future__ import annotations

import json
from pathlib import Path

import pytest

from storyteller.input_data import (
    PersonalInformationError,
    read_free_input,
    split_paragraphs,
)


def test_free_input_is_normalized_and_saved_as_numbered_paragraphs(tmp_path: Path) -> None:
    source = tmp_path / "free.md"
    source.write_bytes("Ａ\r\n\r\nＢ\n".encode("utf-8"))

    value = read_free_input(source)

    assert value.paragraphs == (
        {"id": "p001", "text": "Ａ"},
        {"id": "p002", "text": "Ｂ"},
    )
    assert value.as_document()["kind"] == "free"
    assert value.as_document()["source_sha256"]


def test_long_paragraph_splits_at_the_last_sentence_ending_before_limit() -> None:
    first = "あ" * 1_195 + "。"
    second = "い" * 20

    paragraphs = split_paragraphs(first + second)

    assert paragraphs == [first, second]
    assert all(len(paragraph) <= 1_200 for paragraph in paragraphs)


@pytest.mark.parametrize(
    "value, kind",
    [
        ("mail test@example.com", "メールアドレス"),
        ("電話 090-1234-5678", "電話番号"),
        ("郵便 〒123-4567", "郵便番号"),
        ("サイト https://example.com/a", "URL"),
        ("連絡 @story_user", "SNSハンドル"),
        ("東京都渋谷区", "住所"),
        ("番号 1234567", "7桁以上の連続した数字"),
    ],
)
def test_free_input_rejects_each_supported_pii_kind(
    tmp_path: Path, value: str, kind: str
) -> None:
    source = tmp_path / "free.md"
    source.write_text(value, encoding="utf-8")

    with pytest.raises(PersonalInformationError) as error:
        read_free_input(source)

    assert kind in str(error.value)
    assert value not in str(error.value)


def test_free_input_does_not_reject_concrete_dates(tmp_path: Path) -> None:
    source = tmp_path / "free.md"
    source.write_text("遠い未来の2040年9月1日。", encoding="utf-8")

    assert read_free_input(source).paragraphs[0]["text"] == "遠い未来の2040年9月1日。"
