from __future__ import annotations

import ast
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = ("src", "tests", "tools")


class _EncodingVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.violations: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            self._check_open_call(node)
        elif isinstance(node.func, ast.Attribute):
            if node.func.attr in {"read_text", "write_text"}:
                self._check_text_method(node)
            elif node.func.attr == "open" and not self._is_os_open(node):
                self._check_open_call(node)
        self.generic_visit(node)

    def _check_text_method(self, node: ast.Call) -> None:
        if not self._has_encoding_keyword(node):
            self._record(node)

    def _check_open_call(self, node: ast.Call) -> None:
        if self._is_binary_mode(node) or self._has_encoding_keyword(node):
            return
        self._record(node)

    @staticmethod
    def _is_os_open(node: ast.Call) -> bool:
        return (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
            and node.func.attr in {"open", "fdopen"}
        )

    @staticmethod
    def _has_encoding_keyword(node: ast.Call) -> bool:
        return any(keyword.arg == "encoding" for keyword in node.keywords)

    @staticmethod
    def _is_binary_mode(node: ast.Call) -> bool:
        mode: ast.expr | None = None
        mode_index = 0 if isinstance(node.func, ast.Attribute) else 1
        if len(node.args) > mode_index:
            mode = node.args[mode_index]
        for keyword in node.keywords:
            if keyword.arg == "mode":
                mode = keyword.value
                break
        return (
            isinstance(mode, ast.Constant)
            and isinstance(mode.value, str)
            and "b" in mode.value
        )

    def _record(self, node: ast.Call) -> None:
        self.violations.append(
            f"{self.path.relative_to(REPOSITORY_ROOT)}:{node.lineno}: "
            "file text I/O must specify an encoding keyword"
        )


def _find_encoding_violations() -> list[str]:
    violations: list[str] = []
    for source_root in SOURCE_ROOTS:
        for path in sorted((REPOSITORY_ROOT / source_root).rglob("*.py")):
            tree = ast.parse(path.read_bytes(), filename=str(path))
            visitor = _EncodingVisitor(path)
            visitor.visit(tree)
            violations.extend(visitor.violations)
    return violations


def test_text_file_io_always_specifies_encoding() -> None:
    violations = _find_encoding_violations()

    assert not violations, "\n".join(violations)
