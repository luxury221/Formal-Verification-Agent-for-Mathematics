"""Dependency-free source/JSON sanity gate, not a substitute for semantic tests."""

import ast
import json
from pathlib import Path

root = Path(__file__).resolve().parents[2]
files = []
for directory in ("mra", "ops/scripts", "tests", "evals", "lean_workspace"):
    for path in (root / directory).rglob("*"):
        if path.suffix not in (".py", ".json"):
            continue
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            ast.parse(text, filename=str(path))
        else:
            json.loads(text)
        assert text.endswith("\n"), f"Missing final newline: {path}"
        assert all(line == line.rstrip() for line in text.splitlines()), (
            f"Trailing whitespace: {path}"
        )
        files.append(path)
print(f"Source gate passed: {len(files)} Python/JSON files")
