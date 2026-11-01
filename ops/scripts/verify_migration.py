from pathlib import Path
import hashlib
import json

target = Path(__file__).resolve().parents[2]
source = Path("/mnt/d/数学形式化验证Agent")
files = [source / "Math_Research_Agent_Development_Blueprint.md"] + list(
    (source / "docs/security/upstream").rglob("*")
)
records = []
for path in files:
    if not path.is_file():
        continue
    relative = path.relative_to(source)
    original = hashlib.sha256(path.read_bytes()).hexdigest()
    migrated = hashlib.sha256((target / relative).read_bytes()).hexdigest()
    assert original == migrated, str(relative)
    records.append({"file": str(relative), "sha256": original})
destination = target / "docs/development/migration.json"
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(
    json.dumps(
        {
            "date": "2026-10-19",
            "source": str(source),
            "target": str(target),
            "original_retained": True,
            "memory_directory": str(source / "memory"),
            "verified_original_documents": records,
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
print(f"Migration verified: {len(records)} original documents; active development: {target}")
