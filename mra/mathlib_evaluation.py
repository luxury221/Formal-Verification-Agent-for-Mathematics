"""Fixed-budget authored Mathlib diagnostics; never a model or research solve score."""

from __future__ import annotations

from collections import Counter
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
from .mathlib_lab import MathlibLabStore, now, prepare, source_manifest
from .mathlib_terms import compile_request, text
from .tasks import TaskStore

SCHEMA = "mathlib-authored-evaluation-v1"
DATASET = "evals/mathlib-diagnostics-v1/cases.json"
SPLITS = ("development", "regression", "all")
MAX_REPORT = 2 * 1024 * 1024


def _json(path, limit=MAX_REPORT):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError("Evaluation file is redirected or oversized")
    return strict_json(path.read_text(encoding="utf-8"))


def evaluation_plan(root, split="all"):
    root = Path(root).resolve()
    if split not in SPLITS:
        raise ValueError("Unknown Mathlib evaluation split")
    dataset = _json(root / DATASET, 131072)
    exact_object(
        dataset,
        {"schema", "authorship", "visibility", "training_use", "model_calls", "cases"},
        "Mathlib dataset",
    )
    if (
        dataset["schema"] != SCHEMA
        or dataset["training_use"] is not False
        or type(dataset["model_calls"]) is not int
        or dataset["model_calls"] != 0
        or not isinstance(dataset["cases"], list)
        or not 1 <= len(dataset["cases"]) <= 16
    ):
        raise ValueError("Invalid authored evaluation dataset")
    text(dataset["authorship"], 2000, "Authorship")
    text(dataset["visibility"], 2000, "Visibility")
    identifiers, families, cases = set(), set(), []
    for case in dataset["cases"]:
        exact_object(case, {"id", "split", "family", "request", "expected_run_status"}, "Case")
        for key in ("id", "family"):
            if not isinstance(case[key], str) or not re.fullmatch(
                r"[a-z][a-z0-9_-]{0,63}", case[key]
            ):
                raise ValueError("Invalid case or family identifier")
        if (
            case["id"] in identifiers
            or case["family"] in families
            or case["split"] not in SPLITS[:2]
            or case["expected_run_status"] not in ("COMPLETED", "REJECTED", "PRECHECK_REJECTED")
            or not isinstance(case["request"], dict)
            or type(case["request"].get("timeout_seconds")) not in (float, int)
            or case["request"]["timeout_seconds"] != 30
        ):
            raise ValueError("Duplicate family, invalid split or nonuniform budget")
        identifiers.add(case["id"])
        families.add(case["family"])
        request = case["request"]
        title = text(request.get("title"), 160, "Case title")
        original = text(request.get("original"), 12000, "Case original statement")
        try:
            compiled = compile_request(request)
            preview = {
                "supported": True,
                "formal_statement": compiled["formal_statement"],
                "reason": None,
            }
        except (ValueError, KeyError, TypeError, RecursionError) as error:
            preview = {"supported": False, "formal_statement": None, "reason": str(error)}
        if split == "all" or case["split"] == split:
            cases.append({**case, "title": title, "original": original, "preflight": preview})
    if not cases:
        raise ValueError("Evaluation split is empty")
    plan = {
        "schema": SCHEMA,
        "scope": "project-authored-structured-proof-input-diagnostics",
        "split": split,
        "dataset_sha256": file_hash(root / DATASET),
        "implementation_sha256": file_hash(root / "mra/mathlib_evaluation.py"),
        "source_manifest": source_manifest(root),
        "cases": cases,
        "budget": {
            "attempts_per_case": 1,
            "shared_candidate_replay_worker_seconds_per_case": 30,
            "maximum_worker_seconds": 30 * len(cases),
            "integrity_time_separate": True,
            "automatic_retries": 0,
        },
        "authorship": dataset["authorship"],
        "visibility": dataset["visibility"],
        "independent_blind_evaluation": False,
        "training_use": False,
        "model_calls": 0,
        "model_solve_rate": None,
        "alignment_accuracy": None,
    }
    return {**plan, "plan_fingerprint": digest(plan)}


def _directory(root, evaluation_id=None, *, create=False):
    root = Path(root).resolve()
    base = root / "artifacts/mathlib-evaluations"
    if (root / "artifacts").is_symlink() or base.is_symlink():
        raise ValueError("Evaluation storage cannot be redirected")
    if create:
        base.mkdir(parents=True, exist_ok=True)
    if evaluation_id is None:
        return base
    if (
        not isinstance(evaluation_id, str)
        or re.fullmatch(r"ml-bench-[a-f0-9]{32}", evaluation_id) is None
    ):
        raise ValueError("Invalid evaluation identifier")
    directory = base / evaluation_id
    if directory.is_symlink():
        raise ValueError("Evaluation directory cannot be redirected")
    return directory


def _write_report(directory, report):
    pending = directory / ".report.pending"
    if pending.exists() or pending.is_symlink():
        raise ValueError("Unexpected pending evaluation report")
    with pending.open("xb") as output:
        output.write(canonical(report) + b"\n")
    pending.replace(directory / "report.json")
    (directory / "report.sha256").write_text(file_hash(directory / "report.json") + "\n")


class EvaluationLab(MathlibLabStore):
    """Original checker and ledger contract in a separate per-evaluation directory."""

    def __init__(self, root, directory, *, create=False):
        self.root = Path(root).resolve()
        self.base = directory / "checks"
        self.path = self.base / "lab.sqlite3"
        if self.base.is_symlink():
            raise ValueError("Evaluation checks cannot be redirected")
        if create:
            self.base.mkdir()
            self.tasks = TaskStore(self.root)
            with self.transaction() as db:
                db.execute(
                    "CREATE TABLE runs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL)"
                )
        elif not self.path.is_file() or self.path.is_symlink():
            raise ValueError("Evaluation check ledger is unavailable")


@contextmanager
def _lease(directory, *, create=False):
    path = directory / "execution.lock"
    if path.is_symlink():
        raise ValueError("Evaluation lease cannot be redirected")
    with path.open("xb" if create else "rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def run_evaluation(root, *, split="all", plan_fingerprint, reviewer, confirmed=False):
    root = Path(root).resolve()
    plan = evaluation_plan(root, split)
    reviewer = text(reviewer, 200, "Named evaluation reviewer")
    if confirmed is not True or plan_fingerprint != plan["plan_fingerprint"]:
        raise ValueError("Review and explicitly confirm the current complete evaluation plan")
    tasks = TaskStore(root)
    with tasks.worker_lock() as lock_fd:
        base = _directory(root, create=True)
        if len(list(base.iterdir())) >= 100:
            raise ValueError("At most 100 local Mathlib evaluations")
        evaluation_id = "ml-bench-" + uuid.uuid4().hex
        directory = _directory(root, evaluation_id)
        directory.mkdir()
        with _lease(directory, create=True):
            (directory / "plan.json").write_bytes(canonical(plan) + b"\n")
            lab = EvaluationLab(root, directory, create=True)
            report = {
                "schema": SCHEMA,
                "evaluation_id": evaluation_id,
                "state": "RUNNING",
                "created_at": now(),
                "updated_at": now(),
                "split": split,
                "reviewer": reviewer,
                "alignment_kind": "local-repository-author-assertion",
                "plan_sha256": file_hash(directory / "plan.json"),
                "results": [],
                "inflight_run_id": None,
                "elapsed_seconds": 0,
            }
            _write_report(directory, report)
            started = time.monotonic()
            try:
                for case in plan["cases"]:
                    if evaluation_plan(root, split)["plan_fingerprint"] != plan_fingerprint:
                        raise ValueError("Evaluation protocol or dataset changed during execution")
                    if not case["preflight"]["supported"]:
                        entry = {
                            "id": case["id"],
                            "run_id": None,
                            "run_status": "PRECHECK_REJECTED",
                        }
                    else:
                        prepared = prepare(root, case["request"])
                        child = lab.submit(
                            {
                                "request_id": evaluation_id + ":" + case["id"],
                                "request": case["request"],
                                "plan_fingerprint": prepared["plan_fingerprint"],
                                "reviewer": reviewer,
                                "confirmed": True,
                            }
                        )
                        report["inflight_run_id"] = child["run_id"]
                        _write_report(directory, report)
                        with lab.transaction() as db:
                            child = lab.read(child["run_id"], db)
                            child.update(state="RUNNING", stage="CHECKING")
                            lab.event(child, "STARTED")
                            lab.save(db, child)
                        checked = lab.execute(child["run_id"], lock_fd)
                        entry = {
                            "id": case["id"],
                            "run_id": child["run_id"],
                            "run_status": checked["state"],
                        }
                    report["results"].append(entry)
                    report["inflight_run_id"] = None
                    report["updated_at"] = now()
                    report["elapsed_seconds"] = time.monotonic() - started
                    _write_report(directory, report)
                report["state"] = "COMPLETED"
            except KeyboardInterrupt:
                report["state"] = "INTERRUPTED"
                lab.recover_locked()
            except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
                report.update(state="EXECUTION_ERROR", error=str(error))
                lab.recover_locked()
            finally:
                report["updated_at"] = now()
                report["elapsed_seconds"] = time.monotonic() - started
                _write_report(directory, report)
    return read_evaluation(root, evaluation_id)


def _unavailable(evaluation_id, reason):
    return {
        "schema": SCHEMA,
        "evaluation_id": evaluation_id,
        "state": "UNAVAILABLE",
        "integrity_ok": False,
        "reason": reason,
        "results": [],
        "summary": None,
        "authored_plan_pass_rate": None,
        "model_solve_rate": None,
        "alignment_accuracy": None,
        "model_calls": 0,
        "fresh_attestation": False,
    }


def read_evaluation(root, evaluation_id):
    root = Path(root).resolve()
    directory = _directory(root, evaluation_id)
    if not directory.is_dir():
        raise ValueError("Unknown Mathlib evaluation")
    try:
        report = _json(directory / "report.json")
        seal = directory / "report.sha256"
        if (
            seal.is_symlink()
            or seal.stat().st_size > 128
            or seal.read_text().strip() != file_hash(directory / "report.json")
        ):
            raise ValueError("Evaluation report digest mismatch")
        plan = _json(directory / "plan.json")
        if report["schema"] != SCHEMA or report["evaluation_id"] != evaluation_id:
            raise ValueError("Evaluation report identity mismatch")
        if report["plan_sha256"] != file_hash(directory / "plan.json"):
            raise ValueError("Evaluation plan changed")
        if plan != evaluation_plan(root, report["split"]):
            raise ValueError("Evaluation dataset, adapter or checked runtime protocol changed")
        alive = False
        try:
            with _lease(directory):
                pass
        except BlockingIOError:
            alive = True
        state = report["state"]
        if state == "RUNNING" and not alive:
            state = "INTERRUPTED"
        if state not in ("RUNNING", "INTERRUPTED", "EXECUTION_ERROR", "COMPLETED"):
            raise ValueError("Invalid execution state")
        if not isinstance(report["results"], list) or len(report["results"]) > len(plan["cases"]):
            raise ValueError("Invalid evaluation progress")
        lab = EvaluationLab(root, directory)
        results, integrity = [], True
        for index, saved in enumerate(report["results"]):
            exact_object(saved, {"id", "run_id", "run_status"}, "Saved evaluation entry")
            case = plan["cases"][index]
            if saved["id"] != case["id"]:
                raise ValueError("Evaluation order or case identity changed")
            row = {
                **case,
                **saved,
                "math_verdict": "UNKNOWN",
                "evidence_level": "E0",
                "worker_seconds": 0,
                "candidate_processes": 0,
                "replay_processes": 0,
            }
            if not case["preflight"]["supported"]:
                if saved["run_id"] is not None or saved["run_status"] != "PRECHECK_REJECTED":
                    raise ValueError("Unsupported input cannot claim a Lean result")
                row["reason"] = case["preflight"]["reason"]
            else:
                checked = lab.report(saved["run_id"])
                valid = (
                    checked["plan"]["request"] == compile_request(case["request"])["request"]
                    and not checked["invalidated"]
                    and checked["state"] == saved["run_status"]
                )
                integrity = integrity and valid
                outcome = checked.get("result") or {}
                candidate, replay = outcome.get("candidate") or {}, outcome.get("replay") or {}
                row.update(
                    run_status=checked["state"],
                    math_verdict=checked["math_verdict"] if valid else "UNKNOWN",
                    evidence_level=checked["evidence_level"] if valid else "E0",
                    worker_seconds=outcome.get("worker_seconds", 0),
                    candidate_processes=int(bool(candidate.get("pid"))),
                    replay_processes=int(bool(replay.get("pid"))),
                    reason=outcome.get("error"),
                    checked=checked,
                )
            row["expected_status_matches"] = row["run_status"] == case["expected_run_status"]
            results.append(row)
        if state == "COMPLETED" and (
            len(results) != len(plan["cases"]) or report["inflight_run_id"] is not None
        ):
            raise ValueError("Completed evaluation is missing cases")
        proved = sum(row["math_verdict"] == "PROVED" for row in results)
        complete = state == "COMPLETED" and integrity
        return {
            **report,
            "state": state,
            "integrity_ok": integrity,
            "plan": plan,
            "results": results,
            "summary": {
                "total": len(plan["cases"]),
                "processed": len(results),
                "proved": proved,
                "unknown": len(results) - proved,
                "pending": len(plan["cases"]) - len(results),
                "run_status_counts": dict(Counter(row["run_status"] for row in results)),
                "candidate_processes": sum(row["candidate_processes"] for row in results),
                "replay_processes": sum(row["replay_processes"] for row in results),
                "worker_seconds": sum(row["worker_seconds"] for row in results),
            },
            "authored_plan_pass_rate": proved / len(plan["cases"]) if complete else None,
            "model_solve_rate": None,
            "alignment_accuracy": None,
            "model_calls": 0,
            "fresh_attestation": False,
        }
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        return _unavailable(evaluation_id, str(error))


def evaluation_catalog(root):
    plan = evaluation_plan(root)
    base = _directory(root)
    runs = []
    if base.is_dir():
        for directory in sorted(base.iterdir(), reverse=True)[:100]:
            if (
                re.fullmatch(r"ml-bench-[a-f0-9]{32}", directory.name) is None
                or directory.is_symlink()
            ):
                continue
            try:
                report = _json(directory / "report.json")
                seal = directory / "report.sha256"
                if (
                    seal.is_symlink()
                    or seal.stat().st_size > 128
                    or seal.read_text().strip() != file_hash(directory / "report.json")
                    or report["evaluation_id"] != directory.name
                    or report["state"]
                    not in ("RUNNING", "COMPLETED", "INTERRUPTED", "EXECUTION_ERROR")
                    or report["split"] not in SPLITS
                ):
                    continue
                text(report["created_at"], 64, "Creation time")
                if report["state"] == "RUNNING":
                    try:
                        with _lease(directory):
                            report["state"] = "INTERRUPTED"
                    except BlockingIOError:
                        pass
                runs.append(
                    {key: report[key] for key in ("evaluation_id", "created_at", "state", "split")}
                )
            except (OSError, ValueError, KeyError, TypeError):
                continue
    runs.sort(key=lambda row: row["created_at"], reverse=True)
    return {"plan": plan, "runs": runs[:30], "fresh_attestation": False}
