"""Read real Lean goals at fixed tactic checkpoints; no external tactic source."""

from pathlib import Path

from .domain import canonical, digest
from .formalizer import exact_object, strict_json
from .integrity import file_hash
from .mathlib_lab import MathlibLabStore, source_manifest
from .number_theory_search import TARGET_FIELDS
from .number_theory_terms import MODE as TARGET_MODE, compile_request

MODE = "mathlib-nat-goal-trace-v1"
PROGRAM = "gcd-split-v1"
LABELS = ("before", "introduced", "split", "closed")


def checkpoint(label):
    return f'''  run_tac do
    let goals ← Lean.Elab.Tactic.getGoals
    let rendered ← goals.mapM fun goal => do
      let pretty ← Lean.Meta.ppGoal goal
      pure (Lean.Json.mkObj [("mvar", Lean.Json.str goal.name.toString), ("pretty", Lean.Json.str pretty.pretty)])
    let payload := Lean.Json.mkObj [("label", Lean.Json.str "{label}"), ("goals", Lean.Json.arr rendered.toArray)]
    Lean.logInfo m!"MRA_GOALS:{{payload.compress}}"'''


PROOF = "\n".join(
    (
        "by",
        checkpoint("before"),
        "  intros",
        checkpoint("introduced"),
        "  apply Nat.dvd_gcd",
        checkpoint("split"),
        "  all_goals first | assumption | (apply Nat.dvd_add <;> assumption)",
        checkpoint("closed"),
    )
)


def manifest(root):
    return {
        "mode": MODE,
        "executor": source_manifest(root),
        "extension_files": {
            name: file_hash(root / name)
            for name in ("mra/lean_goal_trace.py", "mra/number_theory_terms.py")
        },
        "program": PROGRAM,
        "proof_source": PROOF,
    }


def prepare(root, raw):
    if len(canonical(raw)) > 196608:
        raise ValueError("Goal trace request exceeds 192 KiB")
    raw = strict_json(canonical(raw).decode())
    exact_object(raw, TARGET_FIELDS | {"program"}, "Fixed Lean goal trace request")
    if raw["schema"] != MODE or raw["program"] != PROGRAM:
        raise ValueError("Only the versioned gcd split program is available")
    value = {k: v for k, v in raw.items() if k != "program"}
    value["schema"] = TARGET_MODE
    compiled = compile_request(
        {**value, "proof": {"ref": "Nat.dvd_refl", "terms": [{"nat": 0}], "proofs": []}}
    )
    plan = {
        "request": {**value, "schema": MODE, "program": PROGRAM},
        "formal_statement": compiled["formal_statement"],
        "variable_mapping": compiled["variable_mapping"],
        "assumptions": compiled["assumptions"],
        "proof_source": PROOF,
        "source_manifest": manifest(Path(root).resolve()),
        "budget": {
            "candidate_processes": 1,
            "replay_processes": 1,
            "shared_worker_seconds": value["timeout_seconds"],
            "max_heartbeats": 200000,
            "automatic_retries": 0,
            "integrity_time_separate": True,
        },
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "model_calls": 0,
        "limitations": [
            "Actual Lean tactic goals at four fixed checkpoints, not arbitrary interactive steps.",
            "Pretty goal text is diagnostic; only the final audited theorem and replay are proof evidence.",
            "No live session restoration, branching, arbitrary tactic input or model planning.",
            "All original premises are preserved; their consistency is not certified.",
        ],
    }
    plan["plan_fingerprint"] = digest(plan)
    return plan


class GoalTraceChecks(MathlibLabStore):
    def preview(self, request):
        return prepare(self.root, request)

    def gate(self, row, check_files=True):
        if row["plan"]["source_manifest"] != manifest(self.root):
            return "Goal trace program, compiler, executor or runtime changed"
        view = {
            **row,
            "plan": {**row["plan"], "source_manifest": row["plan"]["source_manifest"]["executor"]},
        }
        return super().gate(view, check_files)


def read_trace(output):
    records = []
    for line in output.splitlines():
        # Lean's unused-tactic warning quotes the instrumentation source too.
        # Only standalone diagnostic records are data emitted by this program.
        if not line.startswith("MRA_GOALS:"):
            continue
        record = strict_json(line.split("MRA_GOALS:", 1)[1])
        exact_object(record, {"label", "goals"}, "Lean checkpoint")
        if not isinstance(record["goals"], list) or len(record["goals"]) > 8:
            raise ValueError("Invalid Lean goal list")
        for goal in record["goals"]:
            exact_object(goal, {"mvar", "pretty"}, "Actual Lean goal")
            if any(not isinstance(goal[k], str) or not 0 < len(goal[k]) <= 12000 for k in goal):
                raise ValueError("Missing or oversized actual Lean goal")
        records.append(record)
    if tuple(r["label"] for r in records) != LABELS:
        raise ValueError("Incomplete or duplicate actual Lean checkpoints")
    if [len(r["goals"]) for r in records] != [1, 1, 2, 0]:
        raise ValueError("The fixed gcd trace did not produce the expected actual goal states")
    return {
        "mode": MODE,
        "program": PROGRAM,
        "checkpoints": records,
        "source": "candidate/stdout.log",
    }
