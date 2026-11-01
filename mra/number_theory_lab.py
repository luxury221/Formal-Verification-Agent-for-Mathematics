"""Independent gcd experiment, reusing the frozen real candidate/replay executor."""

from contextlib import contextmanager
from pathlib import Path

from .domain import canonical, digest
from .formalizer import strict_json
from .integrity import file_hash
from .mathlib_lab import MathlibLabStore, source_manifest
from .number_theory_terms import MODE, REFERENCES, compile_request, examples
from .tasks import TaskStore


def manifest(root):
    return {
        "mode": MODE,
        "executor": source_manifest(root),
        "extension_files": {
            name: file_hash(root / name)
            for name in ("mra/number_theory_terms.py", "mra/number_theory_lab.py")
        },
        "references": {name: list(values) for name, values in REFERENCES.items()},
    }


class NumberTheoryStore(MathlibLabStore):
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/number-theory"
        self.base.mkdir(parents=True, exist_ok=True)
        self.path = self.base / "lab.sqlite3"
        self.tasks = TaskStore(self.root)
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL)"
            )

    @contextmanager
    def transaction(self, write=True):
        if self.base.resolve() != self.base or self.path.is_symlink():
            raise ValueError("Number theory storage must not be redirected")
        with super().transaction(write) as db:
            yield db

    def preview(self, request):
        if len(canonical(request)) > 196608:
            raise ValueError("Number theory input exceeds 192 KiB")
        compiled = compile_request(strict_json(canonical(request).decode("utf-8")))
        result = {
            **compiled,
            "source_manifest": manifest(self.root),
            "budget": {
                "candidate_processes": 1,
                "replay_processes": 1,
                "shared_worker_seconds": compiled["request"]["timeout_seconds"],
                "integrity_time_separate": True,
            },
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
            "model_calls": 0,
        }
        result["plan_fingerprint"] = digest(result)
        return result

    def gate(self, row, check_files=True):
        if row["invalidated"]:
            return "Previously invalidated; a new preview and run are required"
        if row["plan"]["source_manifest"] != manifest(self.root):
            return "Number theory compiler, definition catalog, executor or runtime seal changed"
        # Preserve the frozen executor's own trust and artifact checks. This view
        # is never persisted and never edits the original or extension manifest.
        view = {
            **row,
            "plan": {**row["plan"], "source_manifest": row["plan"]["source_manifest"]["executor"]},
        }
        return super().gate(view, check_files)

    def report(self, run_id):
        return {**super().report(run_id), "mode": MODE, "fresh_attestation": False}

    def catalog(self):
        result = super().catalog()
        result.update(
            mode=MODE,
            definitions=[
                {
                    "symbol": "Nat.gcd : Nat → Nat → Nat",
                    "meaning": "gcd(a,b) is the greatest common divisor; gcd(a,0)=a; gcd(0,0)=0",
                    "source": ".runtime/lean-4.35.0-linux/src/lean/Init/Data/Nat/Gcd.lean:35",
                },
                {
                    "symbol": "Nat.Coprime : Nat → Nat → Prop",
                    "meaning": "Nat.Coprime a b means Nat.gcd a b = 1; zero is coprime only to one",
                    "source": ".runtime/lean-4.35.0-linux/src/lean/Init/Data/Nat/Coprime.lean:25",
                },
            ],
            references=[
                {
                    "name": name,
                    "nat_arguments": values[0],
                    "proof_arguments": values[1],
                    "type": values[2],
                    "title": values[3],
                    "origin": values[4],
                    "source": (
                        ".runtime/lean-4.35.0-linux/src/lean/Init/Data/Nat/"
                        + ("Coprime.lean" if "oprime" in name else "Gcd.lean")
                    )
                    if "gcd" in name or "oprime" in name
                    else next(ref["source"] for ref in result["references"] if ref["name"] == name),
                }
                for name, values in REFERENCES.items()
            ],
            examples=examples(),
            scope="manual gcd/coprime structured inputs; independent checks; no legacy graph or archive admission",
        )
        return result
