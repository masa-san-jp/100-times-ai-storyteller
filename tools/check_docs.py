"""Check repository Markdown documents for the P0-03 documentation rules."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_INLINE_LINK_RE = re.compile(r"!?\[[^\]\n]*\]\(")
_ADR_NAME_RE = re.compile(r"^[0-9]{4}-.+\.md$")
_ADR_STATE_RE = re.compile(
    r"^- 状態：\s*(?:Proposed.*|Accepted|Superseded by ADR-[0-9]{4})\s*$"
)
_ADR_DATE_RE = re.compile(r"^- 日付：[0-9]{4}-[0-9]{2}-[0-9]{2}\s*$")


@dataclass(frozen=True)
class Violation:
    """One documentation rule violation."""

    path: Path
    line: int | None
    message: str

    def format(self, root: Path) -> str:
        relative_path = self.path.relative_to(root).as_posix()
        location = f"{relative_path}:{self.line}" if self.line else relative_path
        return f"{location}: {self.message}"


def _is_excluded(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    return any(part in {".git", ".venv", "private"} for part in relative.parts)


def _markdown_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.md")
        if path.is_file() and not _is_excluded(path, root)
    )


def _read_lines(path: Path) -> list[str] | None:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        return [f"<could not read document: {error}>\n"]


def _destination_from_link(text: str, opening_parenthesis: int) -> str | None:
    """Extract an inline-link destination from the text after ``[...](``.

    Markdown permits an angle-bracket destination and permits balanced
    parentheses in an unbracketed destination.  A title after the destination
    is deliberately ignored because it is not part of the filesystem path.
    """

    index = opening_parenthesis + 1
    while index < len(text) and text[index].isspace():
        index += 1
    if index >= len(text):
        return None

    if text[index] == "<":
        end = index + 1
        while end < len(text):
            if text[end] == ">" and text[end - 1] != "\\":
                return text[index + 1 : end]
            end += 1
        return None

    start = index
    depth = 0
    while index < len(text):
        character = text[index]
        if character == "\\":
            index += 2
            continue
        if character == "(":
            depth += 1
        elif character == ")":
            if depth == 0:
                return text[start:index]
            depth -= 1
        elif character.isspace() and depth == 0:
            return text[start:index]
        index += 1
    return None


def _is_fence(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("```") or stripped.startswith("~~~")


def _check_links(root: Path, path: Path) -> list[Violation]:
    violations: list[Violation] = []
    lines = _read_lines(path)
    if lines is None:
        return violations

    fenced = False
    for line_number, line in enumerate(lines, start=1):
        if _is_fence(line):
            fenced = not fenced
            continue
        if fenced:
            continue

        for match in _INLINE_LINK_RE.finditer(line):
            destination = _destination_from_link(line, match.end() - 1)
            if destination is None:
                continue
            destination = destination.strip()
            if destination.startswith("<") and destination.endswith(">"):
                destination = destination[1:-1]
            if not destination or destination.startswith("//"):
                continue
            if _SCHEME_RE.match(destination):
                continue
            if Path(destination).is_absolute():
                continue

            destination = destination.split("#", 1)[0]
            if not destination:
                continue
            destination = unquote(destination)
            target = (path.parent / destination).resolve(strict=False)
            if not target.exists():
                violations.append(
                    Violation(
                        path,
                        line_number,
                        f"相対リンクの対象が存在しません: {destination}",
                    )
                )
    return violations


def _check_adr(root: Path, path: Path) -> list[Violation]:
    if not _ADR_NAME_RE.match(path.name):
        return []

    lines = _read_lines(path)
    if lines is None:
        return []
    violations: list[Violation] = []
    if not any(_ADR_STATE_RE.fullmatch(line.strip()) for line in lines):
        violations.append(
            Violation(path, None, "ADR に有効な「- 状態：」の行がありません")
        )
    if not any(_ADR_DATE_RE.fullmatch(line.strip()) for line in lines):
        violations.append(
            Violation(path, None, "ADR に「- 日付：YYYY-MM-DD」の行がありません")
        )
    return violations


def check_docs(root: str | Path = ROOT) -> list[Violation]:
    """Return all P0-03 documentation violations under *root*."""

    repository = Path(root).resolve(strict=False)
    violations: list[Violation] = []
    for path in _markdown_files(repository):
        violations.extend(_check_links(repository, path))
        if path.parent == repository / "docs" / "adr":
            violations.extend(_check_adr(repository, path))
    return violations


def main() -> int:
    """Run the documentation check for the repository containing this file."""

    violations = check_docs()
    if not violations:
        return 0

    print("文書の検査に失敗しました:", file=sys.stderr)
    for violation in violations:
        print(f"- {violation.format(ROOT)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
