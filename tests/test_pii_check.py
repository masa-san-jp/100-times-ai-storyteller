from __future__ import annotations

import json
from pathlib import Path

import pytest

from storyteller.cli import main
from storyteller.narrative import NARRATIVE_ITEMS, read_narrative
from storyteller.input_data import PersonalInformationError
from storyteller.pii_check import detect_narrative_pii

DETECTED = [
    ("連絡は test@example.com まで", "メールアドレス"),
    ("電話 090-1234-5678", "電話番号"),
    ("電話 +81 90 1234 5678", "電話番号"),
    ("〒123-4567", "郵便番号"),
    ("https://example.com/a", "URL"),
    ("www.example.com", "URL"),
    ("@story_user", "SNSハンドル"),
    ("東京都渋谷区", "住所"),
    ("1234567", "7桁以上の連続した数字"),
    ("2024年3月5日", "具体的な年月日"),
    ("2024年3月", "具体的な年月日"),
    ("令和6年3月5日", "具体的な年月日"),
    ("2024-03-05", "具体的な年月日"),
    ("2024/3/5", "具体的な年月日"),
    ("3024年", "具体的な年月日"),
]
NOT_DETECTED = [
    "遠い未来のどこか",
    "三月の終わり",
    "123456",
    "12-34",
    "いつも何かを恐れている",
    "3月5日ごろ",
    "家族のメールを読む",
]


def _narrative(**overrides: str) -> dict[str, str]:
    value = {key: "傾向の記述" if i < 3 else "" for i, key in enumerate(NARRATIVE_ITEMS)}
    value.update(overrides)
    return value


@pytest.mark.parametrize("text, kind", DETECTED)
def test_narrative_detects_each_kind(text: str, kind: str) -> None:
    findings = detect_narrative_pii(_narrative(desire=text))
    assert [f.kind for f in findings] == [kind]
    assert findings[0].location.startswith("項目 desire")


@pytest.mark.parametrize("text", NOT_DETECTED)
def test_narrative_does_not_detect_ordinary_text(text: str) -> None:
    assert detect_narrative_pii(_narrative(desire=text)) == []


def test_read_narrative_rejects_pii_without_echo(tmp_path: Path) -> None:
    path = tmp_path / "n.json"
    path.write_text(json.dumps(_narrative(taboo="2024年3月5日に"), ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PersonalInformationError) as error:
        read_narrative(path)
    assert "2024" not in str(error.value)
    assert "taboo" in str(error.value)


def test_check_input_narrative_exit_4_and_no_echo(tmp_path: Path, capsys) -> None:
    path = tmp_path / "n.json"
    path.write_text(json.dumps(_narrative(daily="a@b.example 2024-03-05"), ensure_ascii=False), encoding="utf-8")
    assert main(["check-input", str(path)]) == 4
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "メールアドレス" in output and "具体的な年月日" in output and "daily" in output
    assert "a@b.example" not in output and "2024-03-05" not in output


def test_check_input_free_ignores_dates_but_detects_others(tmp_path: Path, capsys) -> None:
    clean = tmp_path / "ok.md"
    clean.write_text("遠い未来の2040年9月1日。", encoding="utf-8")
    assert main(["check-input", str(clean)]) == 0
    capsys.readouterr()
    bad = tmp_path / "bad.md"
    bad.write_text("一段落目\n\n090-1234-5678", encoding="utf-8")
    assert main(["check-input", str(bad)]) == 4
    captured = capsys.readouterr()
    assert "p002" in captured.err + captured.out
    assert "090-1234-5678" not in captured.err + captured.out


def test_check_input_clean_narrative_and_bad_file(tmp_path: Path) -> None:
    ok = tmp_path / "n.json"
    ok.write_text(json.dumps(_narrative(), ensure_ascii=False), encoding="utf-8")
    assert main(["check-input", str(ok)]) == 0
    assert main(["check-input", str(tmp_path / "missing.md")]) == 1
