"""Finite Nat experiments and reviewed refutations, outside the frozen checker."""

from __future__ import annotations

from copy import deepcopy
from itertools import product
import math
from pathlib import Path
import re
import sqlite3
import time
import uuid

from .domain import digest
from .formalizer import exact_object
from .integrity import file_hash
from .mathlib_lab import MathlibLabStore, now, source_manifest
from .number_theory_search import TARGET_FIELDS
from .number_theory_terms import MODE, compile_request, text
from .search_evaluation import _atomic, _envelope, _lease
from .tasks import TaskStore

SCHEMA = "mra-nat-counterexample-v1"
SEARCH_SCHEMA = "mra-nat-counterexample-search-v1"
WALL_SECONDS = 0.5
MAX_BITS = 256
MAX_OPERATIONS = 100000
PLACEHOLDER = {"ref": "Nat.dvd_refl", "terms": [{"nat": 0}], "proofs": []}


def target(raw):
    exact_object(raw, TARGET_FIELDS, "Complete Nat target without proof")
    return {
        k: v
        for k, v in compile_request({**raw, "proof": PLACEHOLDER})["request"].items()
        if k != "proof"
    }


class ExperimentLimit(Exception):
    pass


class Evaluator:
    def __init__(self):
        self.operations = 0
        self.started = time.monotonic()

    def tick(self):
        if self.operations >= MAX_OPERATIONS:
            raise ExperimentLimit("operation_limit")
        if time.monotonic() - self.started >= WALL_SECONDS:
            raise ExperimentLimit("wall_time_limit")
        self.operations += 1

    def nat(self, value, assignment):
        self.tick()
        key, args = next(iter(value.items()))
        if key == "var":
            result = assignment[args]
        elif key == "nat":
            result = args
        else:
            a, b = (self.nat(v, assignment) for v in args)
            if key == "add":
                result = a + b
            elif key == "mul":
                result = a * b
            elif key == "sub":
                result = max(a - b, 0)
            elif key == "gcd":
                result = math.gcd(a, b)
            else:
                raise ValueError("Unsupported Nat operation")
        if result.bit_length() > MAX_BITS:
            raise OverflowError("Finite experiment value exceeds 256 bits")
        return result

    def formula(self, value, assignment):
        self.tick()
        key, args = next(iter(value.items()))
        if key in ("and", "or", "iff"):
            a, b = (self.formula(v, assignment) for v in args)
            return (a and b) if key == "and" else (a or b) if key == "or" else a == b
        a, b = (self.nat(v, assignment) for v in args)
        if key == "dvd":
            return b == 0 if a == 0 else b % a == 0
        if key == "coprime":
            return math.gcd(a, b) == 1
        if key == "eq":
            return a == b
        if key == "le":
            return a <= b
        raise ValueError("Unsupported Nat predicate")

    def instance(self, value, assignment):
        premises = [self.formula(h["formula"], assignment) for h in value["assumptions"]]
        goal = self.formula(value["goal"], assignment)
        return {"assumptions": premises, "goal": goal, "violates_claim": all(premises) and not goal}


def search(raw, root):
    exact_object(raw, {"schema", "target", "budget"}, "Counterexample experiment")
    if raw["schema"] != SEARCH_SCHEMA:
        raise ValueError("Unsupported counterexample search schema")
    value = target(raw["target"])
    exact_object(raw["budget"], {"max_value", "max_assignments"}, "Finite experiment budget")
    for key, low, high in (("max_value", 0, 32), ("max_assignments", 1, 5000)):
        if type(raw["budget"][key]) is not int or not low <= raw["budget"][key] <= high:
            raise ValueError(f"{key} requires an integer from {low} to {high}")
    evaluator = Evaluator()
    assignments = eligible = overflows = 0
    witness = instance = None
    reason = "finite_domain_exhausted"
    try:
        for values in product(
            range(raw["budget"]["max_value"] + 1), repeat=len(value["variables"])
        ):
            if assignments >= raw["budget"]["max_assignments"]:
                reason = "assignment_limit"
                break
            evaluator.tick()
            assignments += 1
            assignment = dict(zip(value["variables"], values))
            try:
                observed = evaluator.instance(value, assignment)
            except OverflowError:
                overflows += 1
                continue
            eligible += all(observed["assumptions"])
            if observed["violates_claim"]:
                witness, instance, reason = assignment, observed, "candidate_found"
                break
    except ExperimentLimit as error:
        reason = str(error)
    request = {**value, "schema": SCHEMA, "witness": witness} if witness is not None else None
    result = {
        "schema": SEARCH_SCHEMA,
        "target": value,
        "status": "CANDIDATE_FOUND" if request else "NO_CANDIDATE",
        "stop_reason": reason,
        "witness": witness,
        "instance": instance,
        "candidate": request,
        "formal_statement": compile_request({**value, "proof": PLACEHOLDER})["formal_statement"],
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "lean_calls": 0,
        "model_calls": 0,
        "budget": {
            **raw["budget"],
            "wall_seconds": WALL_SECONDS,
            "max_value_bits": MAX_BITS,
            "max_operations": MAX_OPERATIONS,
        },
        "stats": {
            "assignments": assignments,
            "satisfying_premise_assignments": eligible,
            "overflow_assignments": overflows,
            "operations": evaluator.operations,
            "elapsed_seconds": round(time.monotonic() - evaluator.started, 6),
        },
        "provenance": {
            "input_sha256": digest(raw),
            "source_sha256": {
                n: file_hash(Path(root) / n)
                for n in ("mra/counterexamples.py", "mra/number_theory_terms.py")
            },
        },
        "limitations": [
            "Finite numerical exploration is not a proof or verified refutation.",
            "No witness does not prove the universal claim, even if the finite domain is exhausted.",
            "All premises must hold; Nat subtraction truncates and 0 divides only 0.",
            "Candidates require exact negation review, real Lean and independent replay.",
        ],
    }
    result["record_sha256"] = digest(result)
    return result


def _manifest(root):
    return {
        "executor": source_manifest(root),
        "extension_files": {
            name: file_hash(root / name)
            for name in (
                "mra/counterexamples.py",
                "mra/number_theory_terms.py",
                "mra/search_evaluation.py",
            )
        },
    }


def preview(root, raw):
    exact_object(raw, TARGET_FIELDS | {"witness"}, "Counterexample candidate")
    if raw["schema"] != SCHEMA:
        raise ValueError("Unsupported counterexample candidate schema")
    value = target({**{k: v for k, v in raw.items() if k != "witness"}, "schema": MODE})
    exact_object(raw["witness"], set(value["variables"]), "Complete Nat assignment")
    if any(type(n) is not int or not 0 <= n <= 32 for n in raw["witness"].values()):
        raise ValueError("Every witness value must be a Nat integer from 0 to 32")
    try:
        observed = Evaluator().instance(value, raw["witness"])
    except (ExperimentLimit, OverflowError) as error:
        raise ValueError("Witness exceeds finite experiment limits") from error
    if not observed["violates_claim"]:
        raise ValueError(
            "Witness must satisfy every original premise and violate the original conclusion"
        )

    def instantiate(tree):
        key, args = next(iter(tree.items()))
        if key == "var":
            return {"nat": raw["witness"][args]}
        return {key: args} if key == "nat" else {key: [instantiate(v) for v in args]}

    original = compile_request({**value, "proof": PLACEHOLDER})
    grounded = {
        **value,
        "variables": [],
        "goal": instantiate(value["goal"]),
        "assumptions": [{**h, "formula": instantiate(h["formula"])} for h in value["assumptions"]],
    }
    closed = compile_request({**grounded, "proof": PLACEHOLDER})
    ground_goal = compile_request({**grounded, "assumptions": [], "proof": PLACEHOLDER})[
        "formal_statement"
    ]
    arguments = [f"({raw['witness'][v]} : Nat)" for v in value["variables"]]
    arguments += ["(by decide)" for _ in value["assumptions"]]
    proof = (
        "by\n  intro mra_claim\n  have mra_bad : "
        + ground_goal
        + " := mra_claim "
        + " ".join(arguments)
        + "\n  exact (by decide : ¬ ("
        + ground_goal
        + ")) mra_bad"
    )
    result = {
        "request": {**value, "schema": SCHEMA, "witness": deepcopy(raw["witness"])},
        "direction": "refute",
        "original_formal_statement": original["formal_statement"],
        "formal_statement": "¬ (" + original["formal_statement"] + ")",
        "ground_statement": closed["formal_statement"],
        "ground_goal": ground_goal,
        "ground_assumptions": closed["assumptions"],
        "observations": observed,
        "proof_source": proof,
        "source_manifest": _manifest(Path(root).resolve()),
        "budget": {
            "candidate_processes": 1,
            "replay_processes": 1,
            "shared_worker_seconds": value["timeout_seconds"],
            "integrity_time_separate": True,
        },
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "model_calls": 0,
    }
    result["plan_fingerprint"] = digest(result)
    return result


def _directory(root, identifier=None, create=False):
    base = Path(root).resolve() / "artifacts/nat-counterexamples"
    if base.parent.is_symlink() or base.is_symlink():
        raise ValueError("Counterexample storage cannot be redirected")
    if create:
        base.mkdir(parents=True, exist_ok=True)
    if identifier is None:
        return base
    if not isinstance(identifier, str) or not re.fullmatch(r"cex-[a-f0-9]{32}", identifier):
        raise ValueError("Invalid counterexample identifier")
    directory = base / identifier
    if directory.is_symlink():
        raise ValueError("Counterexample directory cannot be redirected")
    return directory


class CounterexampleChecks(MathlibLabStore):
    """Proves the negated statement with the unchanged candidate/replay executor."""

    def __init__(self, root, directory, create=False):
        self.root = Path(root).resolve()
        self.base = directory / "checks"
        self.path = self.base / "lab.sqlite3"
        if self.base.is_symlink() or self.path.is_symlink():
            raise ValueError("Counterexample child storage cannot be redirected")
        if create:
            self.base.mkdir()
            with self.transaction() as db:
                db.execute(
                    "CREATE TABLE runs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL)"
                )
        elif not self.path.is_file():
            raise ValueError("Counterexample child evidence is unavailable")

    def preview(self, request):
        return preview(self.root, request)

    def gate(self, row, check_files=True):
        if row["plan"]["source_manifest"] != _manifest(self.root):
            return "Counterexample compiler, executor or runtime changed"
        view = {
            **row,
            "plan": {**row["plan"], "source_manifest": row["plan"]["source_manifest"]["executor"]},
        }
        return super().gate(view, check_files)


def prepare(root, request):
    plan = preview(root, request)
    base = _directory(root, create=True)
    if len(list(base.iterdir())) >= 50:
        raise ValueError("At most 50 local counterexample checks")
    identifier = "cex-" + uuid.uuid4().hex
    directory = _directory(root, identifier)
    directory.mkdir()
    _atomic(directory / "plan.json", plan)
    _atomic(
        directory / "report.json",
        {
            "schema": SCHEMA,
            "counterexample_id": identifier,
            "state": "PREPARED",
            "plan_sha256": digest(plan),
            "reviewer": None,
            "run_id": None,
            "created_at": now(),
            "updated_at": now(),
            "error": None,
        },
    )
    return report(root, identifier)


def report(root, identifier):
    directory = _directory(root, identifier)
    if not directory.is_dir():
        raise ValueError("Unknown counterexample check")

    def unavailable(reason):
        return {
            "schema": SCHEMA,
            "counterexample_id": identifier,
            "state": "UNAVAILABLE",
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
            "integrity_ok": False,
            "reason": reason,
            "plan": None,
            "check": None,
            "model_calls": 0,
        }

    invalid = directory / "invalidated.json"
    if invalid.exists() or invalid.is_symlink():
        return unavailable("Previously invalidated; prepare and review a new check")
    try:
        plan = _envelope(directory / "plan.json")
        if plan != preview(root, plan["request"]):
            raise ValueError("Confirmed complete target, witness or checker changed")
        row = _envelope(directory / "report.json")
        exact_object(
            row,
            {
                "schema",
                "counterexample_id",
                "state",
                "plan_sha256",
                "reviewer",
                "run_id",
                "created_at",
                "updated_at",
                "error",
            },
            "Counterexample report",
        )
        if (
            row["schema"] != SCHEMA
            or row["counterexample_id"] != identifier
            or row["plan_sha256"] != digest(plan)
        ):
            raise ValueError("Counterexample report identity mismatch")
        if row["state"] not in (
            "PREPARED",
            "RUNNING",
            "COMPLETED",
            "INTERRUPTED",
            "EXECUTION_ERROR",
        ):
            raise ValueError("Invalid counterexample execution state")
        state, child = row["state"], None
        if state == "PREPARED":
            if row["reviewer"] is not None or row["run_id"] is not None:
                raise ValueError("Unconfirmed counterexample contains execution data")
        else:
            text(row["reviewer"], 200, "Named refutation reviewer")
        if state == "RUNNING":
            try:
                with _lease(directory):
                    state = "INTERRUPTED"
            except BlockingIOError:
                pass
        if row["run_id"]:
            child = CounterexampleChecks(root, directory).report(row["run_id"])
            if (
                child["invalidated"]
                or child["plan"] != plan
                or child["request_id"] != identifier
                or child["alignment"]["reviewer"] != row["reviewer"]
            ):
                raise ValueError("Refutation child evidence or alignment changed")
        if state == "COMPLETED" and (not child or child["state"] != "COMPLETED"):
            raise ValueError("Completed refutation lacks completed child evidence")
        refuted = (
            state == "COMPLETED"
            and child["math_verdict"] == "PROVED"
            and child["evidence_level"] == "E4"
        )
        return {
            **row,
            "state": state,
            "plan": plan,
            "check": child,
            "integrity_ok": True,
            "math_verdict": "REFUTED" if refuted else "UNKNOWN",
            "evidence_level": "E4" if refuted else "E0",
            "fresh_attestation": False,
            "model_calls": 0,
        }
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        _atomic(invalid, {"at": now(), "reason": str(error)})
        return unavailable(str(error))


def run(root, identifier, *, plan_fingerprint, reviewer, confirmed=False):
    current = report(root, identifier)
    reviewer = text(reviewer, 200, "Named refutation reviewer")
    if not current["integrity_ok"]:
        raise ValueError(current["reason"])
    if confirmed is not True or plan_fingerprint != current["plan"]["plan_fingerprint"]:
        raise ValueError(
            "Review the original target, complete witness and exact negation, then confirm its fingerprint"
        )
    if current["state"] != "PREPARED":
        if reviewer != current["reviewer"]:
            raise ValueError("Check already belongs to another named confirmation")
        return current
    directory = _directory(root, identifier)
    with TaskStore(Path(root)).worker_lock() as lock_fd, _lease(directory):
        row = _envelope(directory / "report.json")
        if row["state"] != "PREPARED" or current["plan"] != preview(
            root, current["plan"]["request"]
        ):
            raise ValueError("Counterexample already started or its plan changed")
        row.update(state="RUNNING", reviewer=reviewer, updated_at=now())
        _atomic(directory / "report.json", row)
        lab = None
        try:
            lab = CounterexampleChecks(root, directory, create=True)
            child = lab.submit(
                {
                    "request_id": identifier,
                    "request": current["plan"]["request"],
                    "plan_fingerprint": plan_fingerprint,
                    "reviewer": reviewer,
                    "confirmed": True,
                }
            )
            row["run_id"] = child["run_id"]
            _atomic(directory / "report.json", row)
            with lab.transaction() as db:
                child = lab.read(child["run_id"], db)
                child.update(state="RUNNING", stage="CHECKING")
                lab.event(child, "STARTED")
                lab.save(db, child)
            checked = lab.execute(child["run_id"], lock_fd)
            row["state"] = "COMPLETED" if checked["state"] == "COMPLETED" else "EXECUTION_ERROR"
            row["error"] = checked["result"]["error"]
        except KeyboardInterrupt:
            row.update(state="INTERRUPTED", error="Interrupted; no automatic retry")
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            row.update(state="EXECUTION_ERROR", error=str(error))
        finally:
            if lab and row["state"] != "COMPLETED":
                if row["run_id"] and lab.read(row["run_id"])["state"] == "QUEUED":
                    lab.cancel(row["run_id"])
                lab.recover_locked()
            row["updated_at"] = now()
            _atomic(directory / "report.json", row)
    return report(root, identifier)


def catalog(root):
    base = _directory(root)
    rows = []
    if base.exists():
        paths = list(base.iterdir())
        if len(paths) > 50:
            raise ValueError("Counterexample history exceeds local limit")
        for path in paths:
            if re.fullmatch(r"cex-[a-f0-9]{32}", path.name):
                value = report(root, path.name)
                rows.append(
                    {
                        k: value.get(k)
                        for k in (
                            "counterexample_id",
                            "state",
                            "math_verdict",
                            "evidence_level",
                            "created_at",
                            "integrity_ok",
                        )
                    }
                )
    return {
        "schema": SCHEMA,
        "runs": sorted(rows, key=lambda r: r["created_at"] or "", reverse=True),
    }
