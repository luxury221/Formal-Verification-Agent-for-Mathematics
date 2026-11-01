"""Public-source function dynamics diagnostics; immutable programs and complete denominators."""

from copy import deepcopy
from pathlib import Path
import re
import sqlite3
import time
import uuid

from .domain import digest
from .formalizer import exact_object
from .integrity import file_hash
from .mathlib_lab import now
from .number_theory_terms import text
from .research_flow import atomic, lease
from .search_evaluation import _envelope, _json
from .function_dynamics_checks import Checks, verified
from . import structure_strategies as legacy
from . import structure_strategies_v3 as current
from .structured_terms import FIELDS, compile_request
from .structured_proofs import compile_proof
from .tasks import TaskStore, WorkerBusy

SCHEMA = "function-dynamics-evaluation-v1"
DATASET = "evals/function-dynamics-v1/cases.json"
CASE_IDS = (
    "fixed-point",
    "invariant-observation",
    "semiconjugate-iteration",
    "binary-operation-preservation",
    "commuting-iteration",
    "missing-semiconjugacy",
    "iterate-addition",
    "iterate-commuting-composition",
)
ARMS = {"legacy": legacy, "current": current}
SOURCE = ".runtime/mathlib/Mathlib/Logic/Function/Iterate.lean"
LICENSE = ".runtime/mathlib/LICENSE"
FILES = (
    "mra/function_dynamics_evaluation.py",
    "mra/function_dynamics_checks.py",
    "mra/reuse_evaluation_checks.py",
    "mra/search_evaluation.py",
    "mra/structure_reuse.py",
)
SYNTHESIS = {"max_rewrite_steps": 4, "max_expansions": 800, "max_candidates": 2}
BUDGET = {
    "per_check_worker_seconds": 20,
    "per_case_per_arm_max_checks": 2,
    "per_case_per_arm_worker_seconds": 40,
    "per_arm_worker_seconds": 320,
    "combined_worker_seconds": 640,
    "maximum_checks": 32,
    "automatic_retries": 0,
    "integrity_time_separate": True,
}
ERRORS = (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error)


def context(root):
    root = Path(root).resolve()
    return {
        "dataset_sha256": file_hash(root / DATASET),
        "strategies": {arm: module.manifest(root) for arm, module in ARMS.items()},
        "source_sha256": file_hash(root / SOURCE),
        "license_sha256": file_hash(root / LICENSE),
        "files": {n: file_hash(root / n) for n in FILES},
    }


def directory(root, key=None):
    base = Path(root).resolve() / "artifacts/function-dynamics-evaluations"
    if base.resolve() != base:
        raise ValueError("Evaluation storage cannot be redirected")
    if key is None:
        return base
    if not isinstance(key, str) or not re.fullmatch(r"function-dynamics-eval-[a-f0-9]{32}", key):
        raise ValueError("Invalid strategy evaluation identifier")
    area = base / key
    if area.resolve() != area:
        raise ValueError("Evaluation storage cannot be redirected")
    return area


def definition(root):
    root = Path(root).resolve()
    raw = _json(root / DATASET, 196608)
    exact_object(
        raw, {"schema", "domain", "authorship", "training_use", "source", "cases"}, "Domain dataset"
    )
    if (
        raw["schema"] != SCHEMA
        or raw["domain"] != "function-iteration-and-semiconjugacy"
        or raw["training_use"] is not False
        or not isinstance(raw["cases"], list)
        or tuple(c["id"] for c in raw["cases"]) != CASE_IDS
    ):
        raise ValueError("Use the complete eight-case public-source domain dataset")
    text(raw["authorship"], 2000, "Authorship and exposure")
    source = raw["source"]
    exact_object(
        source,
        {
            "repository",
            "commit",
            "path",
            "sha256",
            "license",
            "license_path",
            "license_sha256",
            "purpose",
            "limits",
        },
        "Public source",
    )
    environment = _json(root / "lean_workspace/environment.json")
    if (
        source["repository"] != "https://github.com/leanprover-community/mathlib4"
        or source["commit"] != environment["mathlib_commit"]
        or source["path"] != SOURCE
        or source["sha256"] != file_hash(root / SOURCE)
        or source["license"] != "Apache-2.0"
        or source["license_path"] != LICENSE
        or source["license_sha256"] != file_hash(root / LICENSE)
    ):
        raise ValueError("Public source version, content or license changed")
    for name in ("purpose", "limits"):
        text(source[name], 2000, "Source scope")
    lines = (root / SOURCE).read_text(encoding="utf-8").splitlines()
    for case in raw["cases"]:
        exact_object(case, {"id", "family", "provenance", "target"}, "Domain case")
        text(case["family"], 100, "Correlated family")
        p = case["provenance"]
        exact_object(
            p,
            {
                "kind",
                "declaration",
                "lines",
                "statement",
                "adaptation",
                "exposure",
                "generator_reference_proof_supplied",
            },
            "Statement provenance",
        )
        expected_kind = (
            "hypothesis-deletion-control"
            if case["id"] == "missing-semiconjugacy"
            else "public-statement-adaptation"
        )
        span = p["lines"]
        if (
            p["kind"] != expected_kind
            or p["exposure"] != "developer-visible-public-source-and-adjacent-proofs"
            or p["generator_reference_proof_supplied"] is not False
            or not isinstance(span, list)
            or len(span) != 2
            or any(type(n) is not int for n in span)
            or not 1 <= span[0] <= span[1] <= len(lines)
            or span[1] - span[0] > 4
            or p["statement"] != "\n".join(lines[span[0] - 1 : span[1]]).split(":=")[0].strip()
        ):
            raise ValueError("Source anchor, exposure or excluded proof changed")
        for name in ("declaration", "adaptation"):
            text(p[name], 2000, "Declared source adaptation")
        exact_object(case["target"], FIELDS - {"proof"}, "Complete target without reference proof")
        if (
            type(case["target"]["timeout_seconds"]) is not int
            or case["target"]["timeout_seconds"] != 20
        ):
            raise ValueError("Each candidate and replay share a 20 second ceiling")
        compile_request({**case["target"], "proof": {"method": "rfl"}})
    return {
        "dataset": raw,
        "context": context(root),
        "budget": dict(BUDGET),
        "synthesis_budget": dict(SYNTHESIS),
        "model_calls": 0,
        "model_solve_rate": None,
        "independent_blind_evaluation": False,
        "statistical_inference": False,
    }


def validate_cases(plan):
    """Validate frozen snapshots; report never reruns proposal search."""
    if len(plan["cases"]) != len(CASE_IDS):
        raise ValueError("Prepared cases omitted")
    for source, case in zip(plan["definition"]["dataset"]["cases"], plan["cases"]):
        exact_object(
            case, {"id", "target", "formal_statement", "proposals", "arms"}, "Prepared case"
        )
        target = source["target"]
        formal = compile_request({**target, "proof": {"method": "rfl"}})["formal_statement"]
        if (
            case["id"] != source["id"]
            or case["target"] != target
            or case["formal_statement"] != formal
        ):
            raise ValueError("Complete declared target changed")
        exact_object(case["proposals"], set(ARMS), "Frozen proposals")
        exact_object(case["arms"], set(ARMS), "Prepared arms")
        for arm, module in ARMS.items():
            p = case["proposals"][arm]
            if (
                p["schema"] != module.SCHEMA
                or p["target"] != target
                or p["formal_statement"] != formal
                or p["source_manifest"] != plan["definition"]["context"]["strategies"][arm]
                or p["lean_calls"] != 0
                or p["model_calls"] != 0
                or p["math_verdict"] != "UNKNOWN"
                or p["evidence_level"] != "E0"
                or p["reference_proof_used"] is not False
                or p["plan_fingerprint"]
                != digest({k: v for k, v in p.items() if k not in ("plan_fingerprint", "stats")})
                or any(p["budget"][k] != v for k, v in SYNTHESIS.items())
                or not 1 <= len(p["candidates"]) <= 2
            ):
                raise ValueError("Frozen generator identity, budget or provenance changed")
            if case["arms"][arm] != [c["request"] for c in p["candidates"]]:
                raise ValueError("Prepared program order changed")
            for candidate in p["candidates"]:
                request = candidate["request"]
                compiled = compile_request(request)
                if (
                    candidate["candidate_id"] != digest(request)
                    or candidate["math_verdict"] != "UNKNOWN"
                    or candidate["evidence_level"] != "E0"
                    or {k: v for k, v in request.items() if k != "proof"} != target
                    or compiled["formal_statement"] != formal
                    or compile_proof(request["proof"], compiled) != candidate["proof_source"]
                ):
                    raise ValueError("Program, authority or complete original type changed")


def prepare(root):
    root = Path(root).resolve()
    started = time.monotonic()
    declaration = definition(root)
    cases = []
    for source in declaration["dataset"]["cases"]:
        target = deepcopy(source["target"])
        proposals = {
            arm: module.propose(
                root, {"schema": module.SCHEMA, "target": target, "budget": SYNTHESIS}
            )
            for arm, module in ARMS.items()
        }
        cases.append(
            {
                "id": source["id"],
                "target": target,
                "formal_statement": compile_request({**target, "proof": {"method": "rfl"}})[
                    "formal_statement"
                ],
                "proposals": proposals,
                "arms": {
                    arm: [deepcopy(c["request"]) for c in p["candidates"]]
                    for arm, p in proposals.items()
                },
            }
        )
    plan = {
        "schema": SCHEMA,
        "definition": declaration,
        "cases": cases,
        "plan_construction_wall_seconds": time.monotonic() - started,
    }
    validate_cases(plan)
    base = directory(root)
    base.mkdir(parents=True, exist_ok=True)
    if len(list(base.iterdir())) >= 50:
        raise ValueError("At most 50 local strategy diagnostics")
    key = "function-dynamics-eval-" + uuid.uuid4().hex
    plan.update(evaluation_id=key, created_at=now())
    plan["plan_fingerprint"] = digest(plan)
    area = directory(root, key)
    area.mkdir()
    atomic(area / "plan.json", plan)
    atomic(
        area / "record.json",
        {
            "evaluation_id": key,
            "plan_sha256": digest(plan),
            "state": "PREPARED",
            "reviewer": None,
            "entries": [],
            "inflight": None,
            "elapsed_seconds": 0,
            "error": None,
        },
    )
    value = report(root, key)
    return {**value, "preparation_invocation_wall_seconds": time.monotonic() - started}


def load_plan(root, area):
    plan = _envelope(area / "plan.json")
    if (
        plan["schema"] != SCHEMA
        or plan["evaluation_id"] != area.name
        or plan["definition"] != definition(root)
        or plan["plan_fingerprint"]
        != digest({k: v for k, v in plan.items() if k != "plan_fingerprint"})
    ):
        raise ValueError("Dataset, implementation, budget or saved plan changed")
    validate_cases(plan)
    return plan


def steps(plan):
    for i, case in enumerate(plan["cases"]):
        for arm in ("legacy", "current") if i % 2 == 0 else ("current", "legacy"):
            for slot in range(2):
                yield case, arm, slot


def skipped(case, arm, slot, successes):
    if (case["id"], arm) in successes:
        return "SKIPPED_AFTER_SUCCESS"
    if slot >= len(case["arms"][arm]):
        return "NO_CANDIDATE"
    return None


def summary(plan, results):
    arms = {}
    for arm in ("legacy", "current"):
        relevant = [r for r in results if r["arm"] == arm]
        proved = {r["case_id"] for r in relevant if r["math_verdict"] == "PROVED"}
        actual = [r for r in relevant if r["check"] is not None]
        arms[arm] = {
            "proved": len(proved),
            "unknown": len(CASE_IDS) - len(proved),
            "denominator": len(CASE_IDS),
            "actual_checks": len(actual),
            "worker_reserved_seconds": 20 * len(actual),
            "worker_seconds": sum(
                r["check"]["result"]["worker_seconds"] for r in actual if r["check"].get("result")
            ),
            "checking_wall_seconds": sum(r["wall_seconds"] for r in relevant),
            "case_outcomes": {
                c["id"]: ("PROVED" if c["id"] in proved else "UNKNOWN") for c in plan["cases"]
            },
        }
    families = {}
    for case in plan["definition"]["dataset"]["cases"]:
        families.setdefault(case["family"], []).append(case["id"])
    return {
        "denominator": len(CASE_IDS),
        "correlated_families": families,
        "arms": arms,
        "preparation_seconds_by_arm": {
            arm: sum(
                c["proposals"][arm]["stats"]["preparation_wall_seconds"] for c in plan["cases"]
            )
            for arm in ARMS
        },
        "synthesis_seconds_by_arm": {
            arm: sum(
                c["proposals"][arm]["stats"]["synthesis_elapsed_seconds"] for c in plan["cases"]
            )
            for arm in ARMS
        },
        "plan_construction_wall_seconds": plan["plan_construction_wall_seconds"],
        "independent_blind_evaluation": False,
        "statistical_inference": False,
        "model_solve_rate": None,
    }


def unavailable(key, reason):
    return {
        "schema": SCHEMA,
        "evaluation_id": key,
        "state": "UNAVAILABLE",
        "integrity_ok": False,
        "reason": reason,
        "summary": None,
        "results": [],
        "plan": None,
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "model_calls": 0,
        "fresh_attestation": False,
    }


def report(root, key):
    audit_started = time.monotonic()
    root = Path(root).resolve()
    area = directory(root, key)
    if not area.is_dir():
        raise ValueError("Unknown strategy evaluation")
    if (area / "invalidated.json").exists() or (area / "invalidated.json").is_symlink():
        return unavailable(key, "Previously invalidated; prepare a new evaluation")
    try:
        plan, row = load_plan(root, area), _envelope(area / "record.json")
        exact_object(
            row,
            {
                "evaluation_id",
                "plan_sha256",
                "state",
                "reviewer",
                "entries",
                "inflight",
                "elapsed_seconds",
                "error",
            },
            "Strategy execution",
        )
        if (
            row["evaluation_id"] != key
            or row["plan_sha256"] != digest(plan)
            or row["state"]
            not in ("PREPARED", "RUNNING", "COMPLETED", "INTERRUPTED", "EXECUTION_ERROR")
        ):
            raise ValueError("Execution identity or state changed")
        expected = list(steps(plan))
        if not isinstance(row["entries"], list) or len(row["entries"]) > len(expected):
            raise ValueError("Invalid execution coverage")
        if row["state"] == "PREPARED":
            if row["entries"] or row["inflight"] or row["reviewer"] is not None:
                raise ValueError("Unconfirmed plan acquired execution data")
        else:
            text(row["reviewer"], 200, "Named alignment reviewer")
        state = row["state"]
        if state == "RUNNING":
            try:
                with lease(area):
                    state = "INTERRUPTED"
            except WorkerBusy:
                pass
        results = []
        successes = set()
        lab = None
        for entry, (case, arm, slot) in zip(row["entries"], expected):
            exact_object(
                entry,
                {"case_id", "arm", "slot", "status", "run_id", "wall_seconds"},
                "Strategy attempt entry",
            )
            if (
                (entry["case_id"], entry["arm"], entry["slot"]) != (case["id"], arm, slot)
                or type(entry["wall_seconds"]) not in (int, float)
                or not 0 <= entry["wall_seconds"] < float("inf")
            ):
                raise ValueError("Dropped, duplicated, reordered or invalid attempt entry")
            reason = skipped(case, arm, slot, successes)
            child = None
            if reason:
                if (
                    entry["status"] != reason
                    or entry["run_id"] is not None
                    or entry["wall_seconds"] != 0
                ):
                    raise ValueError("Unattempted slot acquired invented checking evidence")
            else:
                lab = lab or Checks(root, area)
                child = lab.report(entry["run_id"])
                if (
                    child["invalidated"]
                    or child["plan"] != lab.preview(case["arms"][arm][slot])
                    or child["state"] != entry["status"]
                    or child["request_id"] != key + ":" + case["id"] + ":" + arm + ":" + str(slot)
                    or child["alignment"]["reviewer"] != row["reviewer"]
                    or child["alignment"]["confirmed"] is not True
                ):
                    raise ValueError(
                        "Child program, alignment, budget, status or current evidence changed"
                    )
                if child["state"] in ("QUEUED", "RUNNING") or (
                    child["state"] == "COMPLETED"
                    and (child["math_verdict"], child["evidence_level"]) != ("PROVED", "E4")
                ):
                    raise ValueError("Uncommitted or incomplete checking evidence")
                if child["math_verdict"] == "PROVED":
                    verified(child)
                    successes.add((case["id"], arm))
                elif (child["math_verdict"], child["evidence_level"]) != ("UNKNOWN", "E0"):
                    raise ValueError("A failed program cannot refute the target")
            results.append(
                {
                    **entry,
                    "check": child,
                    "math_verdict": child["math_verdict"] if child else "UNKNOWN",
                    "evidence_level": child["evidence_level"] if child else "E0",
                }
            )
        if row["inflight"] is not None:
            pending = row["inflight"]
            exact_object(pending, {"case_id", "arm", "slot", "run_id"}, "Inflight attempt")
            if len(results) >= len(expected):
                raise ValueError("Unexpected inflight attempt after complete coverage")
            case, arm, slot = expected[len(results)]
            if (pending["case_id"], pending["arm"], pending["slot"]) != (
                case["id"],
                arm,
                slot,
            ) or skipped(case, arm, slot, successes):
                raise ValueError("Inflight identity does not match the next uncommitted slot")
            lab = lab or Checks(root, area)
            child = lab.report(pending["run_id"])
            if (
                child["invalidated"]
                or child["plan"] != lab.preview(case["arms"][arm][slot])
                or child["request_id"] != key + ":" + case["id"] + ":" + arm + ":" + str(slot)
                or child["alignment"]["reviewer"] != row["reviewer"]
            ):
                raise ValueError("Inflight program, identity or evidence changed")
        if state == "COMPLETED" and (
            len(results) != len(CASE_IDS) * 4 or row["inflight"] is not None
        ):
            raise ValueError("Completed evaluation omitted an attempt slot")
        return {
            **row,
            "schema": SCHEMA,
            "state": state,
            "plan": plan,
            "results": results,
            "summary": summary(plan, results) if state == "COMPLETED" else None,
            "integrity_ok": True,
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
            "model_calls": 0,
            "model_solve_rate": None,
            "independent_blind_evaluation": False,
            "fresh_attestation": False,
            "report_audit_wall_seconds": time.monotonic() - audit_started,
        }
    except WorkerBusy:
        raise
    except ERRORS as error:
        atomic(area / "invalidated.json", {"at": now(), "reason": str(error)[:2000]})
        return unavailable(key, str(error))


def run(root, key, *, plan_fingerprint, reviewer, confirmed=False):
    invocation_started = time.monotonic()
    root = Path(root).resolve()
    reviewer = text(reviewer, 200, "Named alignment reviewer")
    current = report(root, key)
    if not current["integrity_ok"]:
        raise ValueError(current["reason"])
    plan = current["plan"]
    if confirmed is not True or plan_fingerprint != plan["plan_fingerprint"]:
        raise ValueError(
            "Review all complete targets, prepared programs and equal worker ceilings before confirming"
        )
    if current["state"] != "PREPARED":
        if reviewer != current["reviewer"]:
            raise ValueError("Evaluation belongs to another confirmation")
        return current
    area = directory(root, key)
    with TaskStore(root).worker_lock() as fd, lease(area):
        row = _envelope(area / "record.json")
        if row["state"] != "PREPARED" or load_plan(root, area) != plan:
            raise ValueError("Evaluation changed before execution")
        row.update(state="RUNNING", reviewer=reviewer)
        atomic(area / "record.json", row)
        started = time.monotonic()
        lab = None
        successes = set()
        try:
            lab = Checks(root, area, create=True)
            for case, arm, slot in steps(plan):
                if context(root) != plan["definition"]["context"]:
                    raise ValueError("Frozen context changed during evaluation")
                reason = skipped(case, arm, slot, successes)
                entry = {
                    "case_id": case["id"],
                    "arm": arm,
                    "slot": slot,
                    "status": reason,
                    "run_id": None,
                    "wall_seconds": 0,
                }
                if reason is None:
                    before = time.monotonic()
                    request = case["arms"][arm][slot]
                    child = lab.submit(
                        {
                            "request_id": key + ":" + case["id"] + ":" + arm + ":" + str(slot),
                            "request": request,
                            "plan_fingerprint": lab.preview(request)["plan_fingerprint"],
                            "reviewer": reviewer,
                            "confirmed": True,
                        }
                    )
                    row["inflight"] = {
                        "case_id": case["id"],
                        "arm": arm,
                        "slot": slot,
                        "run_id": child["run_id"],
                    }
                    atomic(area / "record.json", row)
                    with lab.transaction() as db:
                        child = lab.read(child["run_id"], db)
                        child.update(state="RUNNING", stage="CHECKING")
                        lab.event(child, "STARTED")
                        lab.save(db, child)
                    checked = lab.execute(child["run_id"], fd)
                    if checked["invalidated"]:
                        raise ValueError("Current child evidence unavailable before publication")
                    entry.update(
                        run_id=child["run_id"],
                        status=checked["state"],
                        wall_seconds=time.monotonic() - before,
                    )
                    if checked["math_verdict"] == "PROVED":
                        verified(checked)
                        successes.add((case["id"], arm))
                row["entries"].append(entry)
                row.update(inflight=None, elapsed_seconds=time.monotonic() - started)
                atomic(area / "record.json", row)
            row["state"] = "COMPLETED"
        except (KeyboardInterrupt, *ERRORS) as error:
            row.update(
                state="INTERRUPTED" if isinstance(error, KeyboardInterrupt) else "EXECUTION_ERROR",
                error=str(error) or "Interrupted; no automatic resume",
            )
            if lab is not None:
                if (
                    row["inflight"] is not None
                    and lab.read(row["inflight"]["run_id"])["state"] == "QUEUED"
                ):
                    lab.cancel(row["inflight"]["run_id"])
                lab.recover_locked()
        finally:
            row["elapsed_seconds"] = time.monotonic() - started
            atomic(area / "record.json", row)
    value = report(root, key)
    return {**value, "run_invocation_wall_seconds": time.monotonic() - invocation_started}
