"""Check element contracts in the story and developer dummy harnesses."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from collections.abc import Mapping
from typing import Any

# Also support direct invocation from a checkout before package installation.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from storyteller.elements import element_contract_errors
from storyteller.validation import load_and_validate_yaml


# Only these existing multi-element tasks remain until their assigned migrations.
MIGRATION_EXCEPTIONS = {
    "S4.facts": "P1-32",
    "S5.facts": "P1-33",
    "S7.event": "P1-34",
}


def _has_sources(value: Any) -> bool:
    if isinstance(value, Mapping):
        return "sources" in value or any(_has_sources(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_sources(item) for item in value)
    return False


def check_harness(root: Path = ROOT) -> list[str]:
    """Validate definitions, schemas, source-free examples and element shapes."""
    errors = []
    definition_schema = root / "schemas/task-definition.schema.json"
    harnesses = [(root, root / "harness/story/tasks"),
                 (root / "src/storyteller/dev/dummy", root / "src/storyteller/dev/dummy/tasks")]
    for schema_root, directory in harnesses:
        for path in sorted(directory.glob("*.yaml")):
            try:
                definition = load_and_validate_yaml(path, definition_schema)
                if definition["kind"] != "llm":
                    continue
                schema_ref = definition.get("validate", {}).get("schema")
                schema = json.loads((schema_root / schema_ref).read_text(encoding="utf-8")) if schema_ref else None
                if _has_sources(schema):
                    errors.append(f"{path}: 出力スキーマに sources があります")
                card = definition["card"]
                if "sources" in str(card):
                    errors.append(f"{path}: カードに sources があります")
                if definition["id"] not in MIGRATION_EXCEPTIONS:
                    errors.extend(f"{path}: {error}" for error in element_contract_errors(definition, schema))
            except (ValueError, OSError, KeyError) as error:
                errors.append(f"{path}: {error}")
    return errors


def main() -> int:
    errors = check_harness()
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("ハーネスの要素とスキーマの検査に合格しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
