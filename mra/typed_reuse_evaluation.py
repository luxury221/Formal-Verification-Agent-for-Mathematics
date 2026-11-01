"""Predeclared paired mechanism costs, not model or independent research scores."""

from pathlib import Path
import re
import sqlite3
import time
import uuid

from .domain import digest
from .formalizer import exact_object
from .integrity import file_hash
from .typed_definitions import manifest as typed_manifest
from .library_composition import manifest as import_manifest
from .mathlib_lab import now
from .number_theory_terms import text
from .research_flow import atomic, lease
from .typed_evaluation_checks import DirectChecks, ImportChecks
from .typed_evaluation_terms import SCHEMA, compile_case
from .search_evaluation import _envelope, _json
from .tasks import TaskStore, WorkerBusy

DATASET = "evals/typed-proof-reuse-v1/cases.json"
CASE_IDS = (
    "rat-polynomial-repeat",
    "mixed-lift",
    "rational-cancel",
    "missing-nonzero",
    "source-unavailable",
    "quantified-parameter",
)
FILES = (
    "mra/typed_reuse_evaluation.py",
    "mra/typed_evaluation_terms.py",
    "mra/typed_evaluation_checks.py",
    "mra/typed_composition_terms.py",
    "mra/typed_reuse.py",
    "mra/reuse_evaluation_checks.py",
    "mra/search_evaluation.py",
)
ERRORS = (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error)


def context(root):
    return {
        "dataset_sha256": file_hash(root / DATASET),
        "importer": import_manifest(root),
        "typed_provider": typed_manifest(root),
        "files": {n: file_hash(root / n) for n in FILES},
    }


def directory(root, key=None):
    root = Path(root).resolve()
    base = root / "artifacts/typed-proof-reuse-evaluations"
    if base.resolve() != base:
        raise ValueError("Evaluation storage cannot be redirected")
    if key is None:
        return base
    if not isinstance(key, str) or not re.fullmatch(r"typed-reuse-eval-[a-f0-9]{32}", key):
        raise ValueError("Invalid proof reuse evaluation identifier")
    result = base / key
    if result.resolve() != result:
        raise ValueError("Evaluation directory cannot be redirected")
    return result


def definition(root):
    root = Path(root).resolve()
    raw = _json(root / DATASET, 196608)
    exact_object(raw, {"schema", "authorship", "training_use", "cases"}, "Paired dataset")
    if (
        raw["schema"] != SCHEMA
        or raw["training_use"] is not False
        or not isinstance(raw["cases"], list)
        or tuple(c["id"] for c in raw["cases"]) != CASE_IDS
    ):
        raise ValueError("Use the complete fixed six-case diagnostic protocol")
    text(raw["authorship"], 2000, "Authorship and exposure")
    cases = [compile_case(c) for c in raw["cases"]]
    for case in cases:
        text(case["note"], 2000, "Diagnostic note")
    return {
        **raw,
        "cases": cases,
        "context": context(root),
        "budget": {
            "per_check_worker_seconds": 30,
            "per_case_per_arm_max_checks": 2,
            "per_case_per_arm_worker_seconds": 60,
            "per_arm_worker_seconds": 360,
            "combined_worker_seconds": 480,
            "maximum_checks": 16,
            "automatic_retries": 0,
            "integrity_time_separate": True,
        },
        "model_calls": 0,
        "model_solve_rate": None,
        "independent_blind_evaluation": False,
        "statistical_inference": False,
    }


def prepare(root):
    started = time.monotonic()
    root = Path(root).resolve()
    plan = definition(root)
    base = directory(root)
    base.mkdir(parents=True, exist_ok=True)
    if len(list(base.iterdir())) >= 50:
        raise ValueError("At most 50 local mechanism evaluations")
    key = "typed-reuse-eval-" + uuid.uuid4().hex
    plan.update(
        evaluation_id=key, created_at=now(), preparation_wall_seconds=time.monotonic() - started
    )
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
    return report(root, key)


def load_plan(root, area):
    plan = _envelope(area / "plan.json")
    plain = {
        k: v
        for k, v in plan.items()
        if k not in ("evaluation_id", "created_at", "preparation_wall_seconds", "plan_fingerprint")
    }
    if (
        plain != definition(root)
        or plan["evaluation_id"] != area.name
        or plan["plan_fingerprint"]
        != digest({k: v for k, v in plan.items() if k != "plan_fingerprint"})
    ):
        raise ValueError("Dataset, complete programs, budgets, identity or implementation changed")
    return plan


def steps(plan):
    for i, case in enumerate(plan["cases"]):
        for kind in ("source", *(("import", "inline") if i % 2 == 0 else ("inline", "import"))):
            yield case, kind


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
        "model_solve_rate": None,
        "independent_blind_evaluation": False,
        "fresh_attestation": False,
    }


def block_status(case, kind, source):
    if kind == "source" and not case["source_available"]:
        return "SOURCE_UNAVAILABLE"
    if kind == "inline" and case["inline_blocked"]:
        return "STRUCTURE_BLOCKED"
    if kind == "import" and (source is None or source["math_verdict"] != "PROVED"):
        return "SOURCE_UNAVAILABLE"
    return None


def metrics(entry):
    check = entry["check"]
    value = check["result"] if check else None
    value = value or {}
    worker = value.get("worker_seconds", 0)
    return {
        "checks": int(check is not None),
        "reserved_worker_seconds": 30 if check else 0,
        "worker_seconds": worker,
        "wall_seconds": entry["wall_seconds"],
        "nonworker_seconds": max(0, entry["wall_seconds"] - worker),
        "processes": sum(value.get(p) is not None for p in ("candidate", "replay")),
    }


def summarize(plan, results):
    by_key = {(r["case_id"], r["kind"]): r for r in results}
    summary = {"denominator": len(plan["cases"]), "arms": {}, "pairs": []}
    for arm in ("import", "inline"):
        rows = [by_key[c["id"], arm] for c in plan["cases"]]
        costs = rows + (
            [by_key[c["id"], "source"] for c in plan["cases"]] if arm == "import" else []
        )
        target_metrics = [metrics(r) for r in rows]
        summary["arms"][arm] = {
            "denominator": len(plan["cases"]),
            "proved": sum(r["math_verdict"] == "PROVED" for r in rows),
            "unknown": sum(r["math_verdict"] == "UNKNOWN" for r in rows),
            "statuses": {
                s: sum(r["status"] == s for r in rows) for s in sorted({r["status"] for r in rows})
            },
            "cold_cost": {k: sum(metrics(r)[k] for r in costs) for k in metrics(rows[0])},
            "target_cost": {k: sum(m[k] for m in target_metrics) for k in target_metrics[0]},
        }
    for case in plan["cases"]:
        imported, inline = (by_key[case["id"], arm] for arm in ("import", "inline"))
        source = by_key[case["id"], "source"]
        summary["pairs"].append(
            {
                "case_id": case["id"],
                "import": imported["status"],
                "inline": inline["status"],
                "import_minus_inline_cold_worker_seconds": metrics(imported)["worker_seconds"]
                + metrics(source)["worker_seconds"]
                - metrics(inline)["worker_seconds"],
                "archive_unavailable_case": not case["source_available"],
            }
        )
    return summary


def report(root, key):
    started = time.monotonic()
    root = Path(root).resolve()
    area = directory(root, key)
    if not area.is_dir():
        raise ValueError("Unknown proof reuse evaluation")
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
            "Evaluation execution",
        )
        if (
            row["evaluation_id"] != key
            or row["plan_sha256"] != digest(plan)
            or row["state"]
            not in ("PREPARED", "RUNNING", "COMPLETED", "INTERRUPTED", "EXECUTION_ERROR")
        ):
            raise ValueError("Evaluation execution identity or state changed")
        expected = list(steps(plan))
        if not isinstance(row["entries"], list) or len(row["entries"]) > len(expected):
            raise ValueError("Invalid evaluation coverage")
        if row["state"] == "PREPARED":
            if row["entries"] or row["inflight"] or row["reviewer"] is not None:
                raise ValueError("Unconfirmed evaluation contains execution data")
        else:
            text(row["reviewer"], 200, "Named alignment reviewer")
        state = row["state"]
        if state == "RUNNING":
            try:
                with lease(area):
                    state = "INTERRUPTED"
            except WorkerBusy:
                pass
        results, sources = [], {}
        direct = None
        for entry, (case, kind) in zip(row["entries"], expected):
            exact_object(
                entry,
                {"case_id", "kind", "status", "run_id", "wall_seconds"},
                "Paired execution entry",
            )
            if (
                (entry["case_id"], entry["kind"]) != (case["id"], kind)
                or type(entry["wall_seconds"]) not in (int, float)
                or not 0 <= entry["wall_seconds"] < float("inf")
            ):
                raise ValueError("Dropped, reordered, duplicated or invalid execution entry")
            source = sources.get(case["id"])
            blocked = block_status(case, kind, source)
            check = None
            if blocked:
                if entry["status"] != blocked or entry["run_id"] is not None:
                    raise ValueError("Blocked case acquired invented checking evidence")
            else:
                direct = direct or DirectChecks(root, area)
                lab = (
                    ImportChecks(root, area, direct, source["run_id"], plan["context"])
                    if kind == "import"
                    else direct
                )
                check = lab.report(entry["run_id"])
                request = (
                    case["source"]
                    if kind == "source"
                    else case["inline_request"]
                    if kind == "inline"
                    else case["import_request"]
                )
                expected_plan = lab.preview(request)
                if (
                    check["invalidated"]
                    or check["plan"] != expected_plan
                    or check["state"] != entry["status"]
                    or check["request_id"] != key + ":" + case["id"] + ":" + kind
                    or check["alignment"]["reviewer"] != row["reviewer"]
                ):
                    raise ValueError(
                        "Child program, source, confirmation, status or evidence changed"
                    )
                if check["state"] in ("QUEUED", "RUNNING") or (
                    check["state"] == "COMPLETED"
                    and (check["math_verdict"], check["evidence_level"]) != ("PROVED", "E4")
                ):
                    raise ValueError("Uncommitted or incomplete child was published")
                if check["state"] != "COMPLETED" and (
                    check["math_verdict"],
                    check["evidence_level"],
                ) != ("UNKNOWN", "E0"):
                    raise ValueError("Failed execution cannot publish a mathematical verdict")
            result = {
                **entry,
                "check": check,
                "math_verdict": check["math_verdict"] if check else "UNKNOWN",
                "evidence_level": check["evidence_level"] if check else "E0",
            }
            results.append(result)
            if kind == "source" and check:
                sources[case["id"]] = check
        if state == "COMPLETED" and (len(results) != len(expected) or row["inflight"] is not None):
            raise ValueError("Completed evaluation omitted cases or retained inflight work")
        return {
            **row,
            "schema": SCHEMA,
            "state": state,
            "plan": plan,
            "results": results,
            "summary": summarize(plan, results) if state == "COMPLETED" else None,
            "integrity_ok": True,
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
            "model_calls": 0,
            "model_solve_rate": None,
            "independent_blind_evaluation": False,
            "fresh_attestation": False,
            "report_audit_wall_seconds": time.monotonic() - started,
        }
    except WorkerBusy:
        raise
    except ERRORS as error:
        atomic(area / "invalidated.json", {"at": now(), "reason": str(error)[:2000]})
        return unavailable(key, str(error))


def run(root, key, *, plan_fingerprint, reviewer, confirmed=False):
    root = Path(root).resolve()
    reviewer = text(reviewer, 200, "Named alignment reviewer")
    current = report(root, key)
    if not current["integrity_ok"]:
        raise ValueError(current["reason"])
    plan = current["plan"]
    if confirmed is not True or plan_fingerprint != plan["plan_fingerprint"]:
        raise ValueError(
            "Review complete targets, both programs, source construction and budgets before confirming"
        )
    if current["state"] != "PREPARED":
        if reviewer != current["reviewer"]:
            raise ValueError("Evaluation belongs to another confirmation")
        return current
    area = directory(root, key)
    with TaskStore(root).worker_lock() as lock_fd, lease(area):
        row = _envelope(area / "record.json")
        if row["state"] != "PREPARED" or load_plan(root, area) != plan:
            raise ValueError("Evaluation changed before execution")
        row.update(state="RUNNING", reviewer=reviewer)
        atomic(area / "record.json", row)
        started = time.monotonic()
        direct, active, sources = None, None, {}
        try:
            direct = DirectChecks(root, area, create=True)
            import_created = False
            for case, kind in steps(plan):
                if context(root) != plan["context"]:
                    raise ValueError("Evaluation context changed during checking")
                source = sources.get(case["id"])
                status = block_status(case, kind, source)
                entry = {
                    "case_id": case["id"],
                    "kind": kind,
                    "status": status,
                    "run_id": None,
                    "wall_seconds": 0,
                }
                if status is None:
                    before = time.monotonic()
                    if kind == "import":
                        active = ImportChecks(
                            root,
                            area,
                            direct,
                            source["run_id"],
                            plan["context"],
                            create=not import_created,
                        )
                        import_created = True
                        request = case["import_request"]
                    else:
                        active = direct
                        request = case["source"] if kind == "source" else case["inline_request"]
                    child = active.submit(
                        {
                            "request_id": key + ":" + case["id"] + ":" + kind,
                            "request": request,
                            "plan_fingerprint": active.preview(request)["plan_fingerprint"],
                            "reviewer": reviewer,
                            "confirmed": True,
                        }
                    )
                    row["inflight"] = {"kind": kind, "run_id": child["run_id"]}
                    atomic(area / "record.json", row)
                    with active.transaction() as db:
                        child = active.read(child["run_id"], db)
                        child.update(state="RUNNING", stage="CHECKING")
                        active.event(child, "STARTED")
                        active.save(db, child)
                    checked = active.execute(child["run_id"], lock_fd)
                    entry.update(
                        run_id=child["run_id"],
                        status=checked["state"],
                        wall_seconds=time.monotonic() - before,
                    )
                    if checked["invalidated"]:
                        raise ValueError("Child evidence became unavailable before publication")
                    if kind == "source":
                        sources[case["id"]] = checked
                row["entries"].append(entry)
                row.update(inflight=None, elapsed_seconds=time.monotonic() - started)
                atomic(area / "record.json", row)
            row["state"] = "COMPLETED"
        except (KeyboardInterrupt, *ERRORS) as error:
            row.update(
                state="INTERRUPTED" if isinstance(error, KeyboardInterrupt) else "EXECUTION_ERROR",
                error=str(error) or "Interrupted; no retry or automatic resume",
            )
            if active is not None:
                if row["inflight"] is not None:
                    key_inflight = row["inflight"]["run_id"]
                    if active.read(key_inflight)["state"] == "QUEUED":
                        active.cancel(key_inflight)
                active.recover_locked()
        finally:
            row["elapsed_seconds"] = time.monotonic() - started
            atomic(area / "record.json", row)
    return report(root, key)
