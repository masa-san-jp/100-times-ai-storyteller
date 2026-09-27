from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from tools.check_docs import check_docs  # noqa: E402


def write_document(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_current_documents_pass_the_documentation_check() -> None:
    assert check_docs(ROOT) == []


def test_missing_relative_file_and_image_are_reported(tmp_path: Path) -> None:
    write_document(
        tmp_path / "README.md",
        "[missing](docs/missing.md)\n![missing](images/missing.png)\n",
    )

    violations = check_docs(tmp_path)

    assert len(violations) == 2
    assert "docs/missing.md" in violations[0].message
    assert "images/missing.png" in violations[1].message


def test_external_and_anchor_links_do_not_require_local_files(tmp_path: Path) -> None:
    write_document(
        tmp_path / "README.md",
        "[external](https://example.com/no-file)\n"
        "[anchor](#section)\n"
        "[empty]()\n"
        "[network](//example.com/no-file)\n"
        "[absolute](/no-file)\n",
    )

    assert check_docs(tmp_path) == []


def test_links_in_fenced_code_are_ignored(tmp_path: Path) -> None:
    write_document(
        tmp_path / "README.md",
        "```markdown\n[example](not-a-real-file.md)\n```\n",
    )

    assert check_docs(tmp_path) == []


def test_adr_requires_state_and_date_but_template_is_excluded(tmp_path: Path) -> None:
    write_document(
        tmp_path / "docs" / "adr" / "0001-valid.md",
        "# ADR\n- 状態：Proposed - 検討中\n- 日付：2026-09-27\n",
    )
    write_document(
        tmp_path / "docs" / "adr" / "0002-invalid.md",
        "# ADR\n- 状態：Draft\n- 日付：2026/09/27\n",
    )
    write_document(
        tmp_path / "docs" / "adr" / "TEMPLATE.md",
        "# ADR\n- 状態：Draft\n- 日付：not-a-date\n",
    )

    violations = check_docs(tmp_path)

    assert len(violations) == 2
    assert "有効な「- 状態：」の行がありません" in violations[0].message
    assert "「- 日付：YYYY-MM-DD」の行がありません" in violations[1].message


def test_markdown_under_excluded_directories_is_not_checked(tmp_path: Path) -> None:
    for directory in (".git", ".venv", "private"):
        write_document(
            tmp_path / directory / "ignored.md",
            "[missing](not-a-real-file.md)\n",
        )

    assert check_docs(tmp_path) == []
