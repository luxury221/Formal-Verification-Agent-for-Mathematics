"""A narrowly scoped core-data API smoke, never a Lean correctness claim."""

import json
from pathlib import Path
import sys
import time
import uuid

root = Path(__file__).resolve().parents[2]
source = root / ".runtime/candidates/Danus"
sys.path.insert(0, str(source))
from danus.core import FactGraph  # noqa: E402

started = time.monotonic()
# The user's memory storage rule also applies to this upstream graph's test files.
temporary = Path("/mnt/d/数学形式化验证Agent/.tmp/danus-spike") / uuid.uuid4().hex
graph = FactGraph(temporary)
fact_id = graph.add(
    problem_id="p0-smoke",
    author="api-smoke",
    statement="placeholder input, not verified mathematics",
    proof="not checked by Lean",
)
assert graph.exists(fact_id)
assert fact_id in graph.list()
assert (
    graph.add(
        problem_id="p0-smoke",
        author="api-smoke",
        statement="placeholder input, not verified mathematics",
        proof="not checked by Lean",
    )
    == fact_id
)
assert graph.search("placeholder")
record = json.loads((root / ".runtime/danus-source.json").read_text())
record.update(
    elapsed_seconds=round(time.monotonic() - started, 4),
    python=sys.version,
    result="core_api_smoke_passed",
    scope="FactGraph add/read/dedup/search only",
    lean_checked=False,
    observation="FactGraph.add itself accepts unchecked text; a future adapter must place MRA evidence gate before writes",
    test_storage=str(temporary),
)
destination = root / "artifacts/danus-spike.json"
destination.parent.mkdir(exist_ok=True)
destination.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(record, ensure_ascii=False, indent=2))
