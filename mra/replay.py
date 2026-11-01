"""Bounded proof-data transfer to a fresh, trusted Lean kernel replay process."""

from pathlib import Path

from .formalizer import strict_json

MODE = "data-only-kernel-replay-v1"
TEMPLATE = "lean_workspace/replay-checker.lean.in"
MAX_EXPORT_BYTES = 524288
MAX_EXPORT_ROWS = 12000
BASE_FILES = frozenset(
    (
        "input/Candidate.lean",
        "request.json",
        "stdout.log",
        "stderr.log",
        "runtime-snapshot.json",
    )
)
REPLAY_FILES = frozenset(
    (
        "replay/Candidate.lean",
        "replay/proof.ndjson",
        "replay/policy.json",
        "replay/stdout.log",
        "replay/stderr.log",
    )
)


def render_replay(root: Path, expected_statement: str) -> str:
    template = (root / TEMPLATE).read_text(encoding="utf-8")
    if template.count("__MRA_EXPECTED_STATEMENT__") != 1:
        raise ValueError("Invalid trusted replay template")
    return template.replace("__MRA_EXPECTED_STATEMENT__", expected_statement)


def extract_export(output: str) -> bytes:
    lines = output.splitlines()
    if lines.count("MRA_EXPORT_BEGIN") != 1 or lines.count("MRA_EXPORT_END") != 1:
        raise ValueError("Missing or repeated proof export boundaries")
    start, end = lines.index("MRA_EXPORT_BEGIN"), lines.index("MRA_EXPORT_END")
    rows = lines[start + 1 : end]
    raw = ("\n".join(rows) + "\n").encode("utf-8")
    if end <= start or not rows or len(rows) > MAX_EXPORT_ROWS or len(raw) > MAX_EXPORT_BYTES:
        raise ValueError("Empty or oversized proof export")
    objects = [strict_json(row) for row in rows]
    metadata = objects[0].get("meta") if isinstance(objects[0], dict) else None
    if (
        not all(isinstance(value, dict) and value for value in objects)
        or set(objects[0]) != {"meta"}
        or not isinstance(metadata, dict)
        or not isinstance(metadata.get("format"), dict)
        or not isinstance(metadata.get("lean"), dict)
        or metadata["format"].get("version") != "3.1.0"
        or metadata["lean"].get("version") != "4.35.0"
    ):
        raise ValueError("Unsupported proof export metadata")
    return raw
