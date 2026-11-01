"""Visible authored search comparison; review generated plans before real execution."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid

from .domain import canonical, digest
from .formalizer import exact_object, strict_json
from .integrity import file_hash
from .mathlib_lab import now, run_identifier
from .number_theory_lab import NumberTheoryStore, manifest
from .number_theory_search import SCHEMA as SEARCH_SCHEMA, TARGET_FIELDS, search
from .number_theory_terms import compile_request, text
from .tasks import TaskStore

SCHEMA = "number-theory-search-evaluation-v1"
DATASET = "evals/number-theory-search-v1/cases.json"
VARIANTS = {
    "direct": {"max_depth": 1, "max_expansions": 3000},
    "composed": {"max_depth": 6, "max_expansions": 3000},
}
LIMIT = 2 * 1024 * 1024
SPLITS = ("all", "development", "regression")


def _json(path, limit=LIMIT):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError("Evaluation file is redirected, nonregular or oversized")
    return strict_json(path.read_text(encoding="utf-8"))


def _atomic(path, value):
    if path.is_symlink():
        raise ValueError("Evaluation output cannot be redirected")
    data = canonical({"payload": value, "sha256": digest(value)}) + b"\n"
    if len(data) > LIMIT:
        raise ValueError("Evaluation output exceeds 2 MiB")
    pending = path.with_name(".pending-" + uuid.uuid4().hex)
    try:
        with pending.open("xb") as stream:
            stream.write(data)
        pending.replace(path)
    finally:
        if pending.exists():
            pending.unlink()


def _envelope(path):
    value = _json(path)
    exact_object(value, {"payload", "sha256"}, "Evaluation envelope")
    if not isinstance(value["payload"], dict) or digest(value["payload"]) != value["sha256"]:
        raise ValueError("Evaluation digest mismatch")
    return value["payload"]


def _directory(root, evaluation_id=None, create=False):
    root = Path(root).resolve()
    base = root / "artifacts/number-theory-search-evaluations"
    if (root / "artifacts").is_symlink() or base.is_symlink():
        raise ValueError("Evaluation storage cannot be redirected")
    if create:
        base.mkdir(parents=True, exist_ok=True)
    if evaluation_id is None:
        return base
    if not isinstance(evaluation_id, str) or not re.fullmatch(
        r"ns-eval-[a-f0-9]{32}", evaluation_id
    ):
        raise ValueError("Invalid search evaluation identifier")
    path = base / evaluation_id
    if path.is_symlink():
        raise ValueError("Evaluation directory cannot be redirected")
    return path


def _context(root):
    return {
        "dataset_sha256": file_hash(root / DATASET),
        "verifier_manifest": manifest(root),
        "source_sha256": {
            n: file_hash(root / n)
            for n in ("mra/search_evaluation.py", "mra/number_theory_search.py")
        },
    }


def definition(root, split="all"):
    root = Path(root).resolve()
    if split not in SPLITS:
        raise ValueError("Unsupported search evaluation split")
    dataset = _json(root / DATASET, 131072)
    exact_object(
        dataset, {"schema", "authorship", "visibility", "training_use", "cases"}, "Search dataset"
    )
    if (
        dataset["schema"] != SCHEMA
        or dataset["training_use"] is not False
        or not isinstance(dataset["cases"], list)
        or not 1 <= len(dataset["cases"]) <= 16
    ):
        raise ValueError("Invalid visible authored search dataset")
    text(dataset["authorship"], 2000, "Authorship")
    text(dataset["visibility"], 2000, "Visibility")
    identifiers, families, targets, cases = set(), set(), set(), []
    for case in dataset["cases"]:
        exact_object(case, {"id", "family", "split", "target", "note"}, "Search case")
        for key in ("id", "family"):
            if not isinstance(case[key], str) or not re.fullmatch(
                r"[a-z][a-z0-9_-]{0,63}", case[key]
            ):
                raise ValueError("Invalid case identifier")
        exact_object(case["target"], TARGET_FIELDS, "Complete proof-free target")
        compiled = compile_request(
            {
                **case["target"],
                "proof": {"ref": "Nat.dvd_refl", "terms": [{"nat": 0}], "proofs": []},
            }
        )
        identity = digest(
            {k: case["target"][k] for k in ("schema", "variables", "assumptions", "goal")}
        )
        if (
            case["id"] in identifiers
            or case["family"] in families
            or identity in targets
            or case["split"] not in SPLITS[1:]
            or type(case["target"]["timeout_seconds"]) not in (int, float)
            or case["target"]["timeout_seconds"] != 30
        ):
            raise ValueError(
                "Duplicate case/family/target, invalid split or nonuniform Lean budget"
            )
        text(case["note"], 2000, "Exposure and diagnostic note")
        identifiers.add(case["id"])
        families.add(case["family"])
        targets.add(identity)
        if split == "all" or split == case["split"]:
            cases.append(
                {
                    **case,
                    "target": {k: v for k, v in compiled["request"].items() if k != "proof"},
                    "formal_statement": compiled["formal_statement"],
                }
            )
    if not cases:
        raise ValueError("Search evaluation split is empty")
    return {
        "schema": SCHEMA,
        "split": split,
        "cases": cases,
        "variants": VARIANTS,
        "authorship": dataset["authorship"],
        "visibility": dataset["visibility"],
        "independent_blind_evaluation": False,
        "training_use": False,
        "model_calls": 0,
        "limits": {
            "candidate_attempts_per_variant_case": 1,
            "automatic_retries": 0,
            "maximum_lean_worker_seconds": len(cases) * len(VARIANTS) * 30,
            "synthesis_wall_seconds_per_search": 0.75,
            "max_terms": 32,
            "integrity_time_separate": True,
        },
        "context": _context(root),
    }


@contextmanager
def _lease(directory):
    path = directory / "execution.lock"
    if path.is_symlink():
        raise ValueError("Evaluation execution lease cannot be redirected")
    with path.open("a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class EvaluationChecks(NumberTheoryStore):
    """Same frozen checker and gate; only the per-evaluation storage location differs."""

    def __init__(self, root, directory, create=False):
        self.root = Path(root).resolve()
        self.base = directory / "checks"
        self.path = self.base / "lab.sqlite3"
        if self.base.is_symlink() or self.path.is_symlink():
            raise ValueError("Evaluation check storage cannot be redirected")
        if create:
            self.base.mkdir()
            self.tasks = TaskStore(self.root)
            with self.transaction() as db:
                db.execute(
                    "CREATE TABLE runs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL)"
                )
        elif not self.path.is_file():
            raise ValueError("Evaluation check ledger is unavailable")


def prepare_evaluation(root, split="all"):
    root = Path(root).resolve()
    value = definition(root, split)
    # The proof-free dataset never supplies a reference proof to either search.
    lab = NumberTheoryStore(root)
    cases = []
    for case in value["cases"]:
        proposals, plans = {}, {}
        for variant, budget in VARIANTS.items():
            proposal = search(
                {"schema": SEARCH_SCHEMA, "target": case["target"], "budget": budget}, root
            )
            proposals[variant] = proposal
            plans[variant] = lab.preview(proposal["candidate"]) if proposal["candidate"] else None
        cases.append({**case, "searches": proposals, "verification_plans": plans})
    if definition(root, split) != value:
        raise ValueError("Search protocol changed during preparation")
    base = _directory(root, create=True)
    if len(list(base.iterdir())) >= 50:
        raise ValueError("At most 50 local search evaluations")
    evaluation_id = "ns-eval-" + uuid.uuid4().hex
    plan = {**value, "cases": cases, "evaluation_id": evaluation_id, "created_at": now()}
    plan["plan_fingerprint"] = digest(plan)
    directory = _directory(root, evaluation_id)
    directory.mkdir()
    _atomic(directory / "plan.json", plan)
    _atomic(
        directory / "report.json",
        {
            "schema": SCHEMA,
            "evaluation_id": evaluation_id,
            "plan_sha256": digest(plan),
            "state": "PREPARED",
            "created_at": now(),
            "updated_at": now(),
            "reviewer": None,
            "results": [],
            "inflight": None,
            "elapsed_seconds": 0,
            "error": None,
        },
    )
    return read_evaluation(root, evaluation_id)


def _load_plan(root, directory):
    plan = _envelope(directory / "plan.json")
    expected_fields = set(definition(root)) | {"evaluation_id", "created_at", "plan_fingerprint"}
    exact_object(plan, expected_fields, "Generated evaluation plan")
    if plan["evaluation_id"] != directory.name or plan["plan_fingerprint"] != digest(
        {k: v for k, v in plan.items() if k != "plan_fingerprint"}
    ):
        raise ValueError("Generated evaluation identity or fingerprint mismatch")
    current = definition(root, plan["split"])
    if {
        k: v
        for k, v in plan.items()
        if k not in ("cases", "evaluation_id", "created_at", "plan_fingerprint")
    } != {k: v for k, v in current.items() if k != "cases"}:
        raise ValueError(
            "Search dataset, generator, evaluation adapter or verifier protocol changed"
        )
    if not isinstance(plan["cases"], list) or len(plan["cases"]) != len(current["cases"]):
        raise ValueError("Generated case coverage mismatch")
    lab = NumberTheoryStore(root)
    for case, original in zip(plan["cases"], current["cases"]):
        exact_object(case, set(original) | {"searches", "verification_plans"}, "Generated case")
        if {
            k: v for k, v in case.items() if k not in ("searches", "verification_plans")
        } != original:
            raise ValueError("Original target or premises changed")
        exact_object(case["searches"], set(VARIANTS), "Search variants")
        exact_object(case["verification_plans"], set(VARIANTS), "Verification variants")
        for variant, budget in VARIANTS.items():
            proposal = case["searches"][variant]
            if (
                proposal["record_sha256"]
                != digest({k: v for k, v in proposal.items() if k != "record_sha256"})
                or proposal["target"] != original["target"]
                or proposal["math_verdict"] != "UNKNOWN"
                or proposal["evidence_level"] != "E0"
                or proposal["lean_calls"] != 0
                or proposal["model_calls"] != 0
            ):
                raise ValueError("Invalid unverified search provenance")
            if proposal["budget"] != {
                **budget,
                "max_terms": 32,
                "synthesis_wall_seconds": 0.75,
            } or proposal["provenance"]["input_sha256"] != digest(
                {"schema": SEARCH_SCHEMA, "target": original["target"], "budget": budget}
            ):
                raise ValueError("Search input or budget changed")
            candidate = proposal["candidate"]
            if candidate is None:
                if (
                    proposal["status"] != "NO_CANDIDATE"
                    or case["verification_plans"][variant] is not None
                ):
                    raise ValueError("Invalid empty search result")
            elif (
                proposal["status"] != "CANDIDATE_FOUND"
                or {k: v for k, v in candidate.items() if k != "proof"} != original["target"]
                or proposal["provenance"]["candidate_sha256"] != digest(candidate)
                or case["verification_plans"][variant] != lab.preview(candidate)
            ):
                raise ValueError("Candidate differs from the complete confirmed target or plan")
    return plan


def _unavailable(evaluation_id, reason):
    return {
        "schema": SCHEMA,
        "evaluation_id": evaluation_id,
        "state": "UNAVAILABLE",
        "integrity_ok": False,
        "reason": reason,
        "summary": None,
        "results": [],
        "plan": None,
        "model_solve_rate": None,
        "model_calls": 0,
        "independent_blind_evaluation": False,
        "fresh_attestation": False,
    }


def _invalidate(directory, reason):
    path = directory / "invalidated.json"
    if not path.exists():
        _atomic(path, {"at": now(), "reason": reason})


def _entries(plan):
    return [(case, variant) for case in plan["cases"] for variant in VARIANTS]


def _stop_inflight(lab, report):
    if lab is not None:
        if report["inflight"] and lab.read(report["inflight"])["state"] == "QUEUED":
            lab.cancel(report["inflight"])
        lab.recover_locked()


def read_evaluation(root, evaluation_id):
    root = Path(root).resolve()
    directory = _directory(root, evaluation_id)
    if not directory.is_dir():
        raise ValueError("Unknown search evaluation")
    if (directory / "invalidated.json").exists() or (directory / "invalidated.json").is_symlink():
        return _unavailable(
            evaluation_id, "Previously invalidated; prepare and review a new evaluation"
        )
    try:
        plan = _load_plan(root, directory)
        report = _envelope(directory / "report.json")
        exact_object(
            report,
            {
                "schema",
                "evaluation_id",
                "plan_sha256",
                "state",
                "created_at",
                "updated_at",
                "reviewer",
                "results",
                "inflight",
                "elapsed_seconds",
                "error",
            },
            "Execution report",
        )
        if (
            report["schema"] != SCHEMA
            or report["evaluation_id"] != evaluation_id
            or report["plan_sha256"] != digest(plan)
            or report["state"]
            not in ("PREPARED", "RUNNING", "COMPLETED", "INTERRUPTED", "EXECUTION_ERROR")
        ):
            raise ValueError("Execution identity, plan or state mismatch")
        items = _entries(plan)
        if not isinstance(report["results"], list) or len(report["results"]) > len(items):
            raise ValueError("Invalid execution coverage")
        if report["state"] == "PREPARED" and (
            report["results"] or report["inflight"] or report["reviewer"] is not None
        ):
            raise ValueError("Unconfirmed plan contains execution data")
        if report["state"] != "PREPARED":
            text(report["reviewer"], 200, "Named alignment reviewer")
        alive = False
        if report["state"] == "RUNNING":
            try:
                with _lease(directory):
                    pass
            except BlockingIOError:
                alive = True
        state = "INTERRUPTED" if report["state"] == "RUNNING" and not alive else report["state"]
        results = []
        lab = None
        for entry, (case, variant) in zip(report["results"], items):
            exact_object(entry, {"case_id", "variant", "run_id", "run_status"}, "Search result")
            if (entry["case_id"], entry["variant"]) != (case["id"], variant):
                raise ValueError("Skipped, duplicated or reordered evaluation result")
            proposal = case["searches"][variant]
            checked = None
            if proposal["candidate"] is None:
                if entry["run_id"] is not None or entry["run_status"] != "NO_CANDIDATE":
                    raise ValueError("Absent candidate was incorrectly checked")
            else:
                run_identifier(entry["run_id"])
                lab = lab or EvaluationChecks(root, directory)
                checked = lab.report(entry["run_id"])
                if (
                    checked["invalidated"]
                    or checked["plan"] != case["verification_plans"][variant]
                    or checked["state"] != entry["run_status"]
                    or checked["request_id"] != evaluation_id + ":" + case["id"] + ":" + variant
                    or checked["alignment"]["reviewer"] != report["reviewer"]
                ):
                    raise ValueError("Child proof, alignment or execution changed")
            results.append(
                {
                    **entry,
                    "search_status": proposal["status"],
                    "stop_reason": proposal["stop_reason"],
                    "search_stats": proposal["stats"],
                    "math_verdict": checked["math_verdict"] if checked else "UNKNOWN",
                    "evidence_level": checked["evidence_level"] if checked else "E0",
                    "check": checked,
                }
            )
        if state == "COMPLETED" and (len(results) != len(items) or report["inflight"] is not None):
            raise ValueError("Completed evaluation dropped results or retained an active check")
        summary = None
        if state == "COMPLETED":
            summary = {}
            for variant in VARIANTS:
                rows = [r for r in results if r["variant"] == variant]
                summary[variant] = {
                    "denominator": len(plan["cases"]),
                    "candidates": sum(r["search_status"] == "CANDIDATE_FOUND" for r in rows),
                    "proved": sum(r["math_verdict"] == "PROVED" for r in rows),
                    "unknown": sum(r["math_verdict"] == "UNKNOWN" for r in rows),
                    "rejected_candidates": sum(r["run_status"] == "REJECTED" for r in rows),
                    "search_expansions": sum(r["search_stats"]["expansions"] for r in rows),
                    "worker_seconds": sum(
                        (r["check"]["result"] or {}).get("worker_seconds", 0)
                        for r in rows
                        if r["check"]
                    ),
                }
            by_key = {(r["case_id"], r["variant"]): r for r in results}
            summary["composed_only_proved"] = [
                c["id"]
                for c in plan["cases"]
                if by_key[c["id"], "composed"]["math_verdict"] == "PROVED"
                and by_key[c["id"], "direct"]["math_verdict"] != "PROVED"
            ]
        return {
            **report,
            "state": state,
            "plan": plan,
            "results": results,
            "summary": summary,
            "integrity_ok": True,
            "fresh_attestation": False,
            "model_calls": 0,
            "model_solve_rate": None,
            "independent_blind_evaluation": False,
        }
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        _invalidate(directory, str(error))
        return _unavailable(evaluation_id, str(error))


def run_evaluation(root, evaluation_id, *, plan_fingerprint, reviewer, confirmed=False):
    root = Path(root).resolve()
    reviewer = text(reviewer, 200, "Named evaluation reviewer")
    current = read_evaluation(root, evaluation_id)
    if not current["integrity_ok"]:
        raise ValueError(current["reason"])
    plan = current["plan"]
    if confirmed is not True or plan_fingerprint != plan["plan_fingerprint"]:
        raise ValueError(
            "Review all complete targets, generated proofs and budgets, then confirm this plan fingerprint"
        )
    if current["state"] != "PREPARED":
        if current["reviewer"] != reviewer:
            raise ValueError("Execution already belongs to a different named confirmation")
        return current  # Idempotent read, never a retry or budget reset.
    directory = _directory(root, evaluation_id)
    tasks = TaskStore(root)
    with tasks.worker_lock() as lock_fd, _lease(directory):
        report = _envelope(directory / "report.json")
        if report["state"] != "PREPARED":
            raise ValueError("Evaluation already started; inspect its current state")
        if _load_plan(root, directory) != plan:
            raise ValueError("Generated plan changed before execution")
        report.update(state="RUNNING", reviewer=reviewer, updated_at=now())
        _atomic(directory / "report.json", report)
        started = time.monotonic()
        lab = None
        try:
            lab = EvaluationChecks(root, directory, create=True)
            for case, variant in _entries(plan):
                if _context(root) != plan["context"]:
                    raise ValueError("Search evaluation context changed during execution")
                candidate = case["searches"][variant]["candidate"]
                entry = {
                    "case_id": case["id"],
                    "variant": variant,
                    "run_id": None,
                    "run_status": "NO_CANDIDATE",
                }
                if candidate is not None:
                    child = lab.submit(
                        {
                            "request_id": evaluation_id + ":" + case["id"] + ":" + variant,
                            "request": candidate,
                            "plan_fingerprint": case["verification_plans"][variant][
                                "plan_fingerprint"
                            ],
                            "reviewer": reviewer,
                            "confirmed": True,
                        }
                    )
                    report["inflight"] = child["run_id"]
                    _atomic(directory / "report.json", report)
                    with lab.transaction() as db:
                        child = lab.read(child["run_id"], db)
                        child.update(state="RUNNING", stage="CHECKING")
                        lab.event(child, "STARTED")
                        lab.save(db, child)
                    checked = lab.execute(child["run_id"], lock_fd)
                    entry.update(run_id=child["run_id"], run_status=checked["state"])
                report["results"].append(entry)
                report.update(
                    inflight=None, updated_at=now(), elapsed_seconds=time.monotonic() - started
                )
                _atomic(directory / "report.json", report)
            report["state"] = "COMPLETED"
        except KeyboardInterrupt:
            report.update(state="INTERRUPTED", error="Interrupted; no automatic resume or retry")
            _stop_inflight(lab, report)
        except (OSError, RuntimeError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
            report.update(state="EXECUTION_ERROR", error=str(error))
            _stop_inflight(lab, report)
        finally:
            report.update(updated_at=now(), elapsed_seconds=time.monotonic() - started)
            _atomic(directory / "report.json", report)
    return read_evaluation(root, evaluation_id)


def evaluation_catalog(root):
    base = _directory(root)
    runs = []
    if base.exists():
        paths = sorted(base.iterdir(), key=lambda p: p.name, reverse=True)
        if len(paths) > 50:
            raise ValueError("Search evaluation history exceeds local limit")
        for path in paths:
            if not re.fullmatch(r"ns-eval-[a-f0-9]{32}", path.name):
                continue
            row = read_evaluation(root, path.name)
            runs.append(
                {
                    k: row.get(k)
                    for k in (
                        "evaluation_id",
                        "state",
                        "created_at",
                        "summary",
                        "integrity_ok",
                        "reason",
                    )
                }
            )
    return {
        "schema": SCHEMA,
        "definition": definition(root),
        "runs": sorted(runs, key=lambda r: r["created_at"] or "", reverse=True),
    }
