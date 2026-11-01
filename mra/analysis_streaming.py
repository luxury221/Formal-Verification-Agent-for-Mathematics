"""Versioned analysis execution with full streaming byte scans; A1 syntax unchanged."""

from contextlib import contextmanager
from copy import deepcopy
import math
from pathlib import Path
import time

from . import analysis_foundation as foundation
from .analysis_language import MODE as INPUT_SCHEMA, catalog as language_catalog, compile_request
from .streaming_integrity import SCAN_POLICY, load

from .domain import canonical, digest
from .formalizer import strict_json
from .integrity import IntegrityError, file_hash, file_record
from .mathlib_lab import MathlibLabStore, audit, run_identifier
from .replay import extract_export, render_replay
from .tasks import TaskStore
from .verifier import render_source

MODE = "analysis-streaming-v1"


def catalog():
    return {
        **language_catalog(),
        "input_schema": INPUT_SCHEMA,
        "execution_profile": MODE,
        "scan_policy": dict(SCAN_POLICY),
    }


POLICY = {**deepcopy(foundation.POLICY), "mode": MODE}
TRUST_FILES = tuple(
    dict.fromkeys(
        (
            *foundation.FILES,
            foundation.SNAPSHOT,
            foundation.LOCK,
            "mra/analysis_language.py",
            "mra/analysis_lab.py",
            "mra/streaming_integrity.py",
            "mra/analysis_streaming.py",
        )
    )
)


def manifest(root):
    root = Path(root).resolve()
    files = {name: file_record(foundation._safe(root, name))["sha256"] for name in TRUST_FILES}
    lock = strict_json((root / foundation.LOCK).read_text())
    if lock != {"mode": foundation.MODE, "sha256": files[foundation.SNAPSHOT]}:
        raise IntegrityError("Analysis foundation lock changed")
    return {
        "mode": MODE,
        "files": files,
        "analysis_import": foundation.IMPORT,
        "scan_policy": dict(SCAN_POLICY),
        "policy": deepcopy(POLICY),
        "catalog": catalog(),
    }


def prepare(root, raw):
    compiled = compile_request(raw)
    value = {
        **compiled,
        "mode": MODE,
        "source_manifest": manifest(root),
        "budget": {
            "candidate_processes": 1,
            "replay_processes": 1,
            "shared_worker_seconds": raw["timeout_seconds"],
            "integrity_time_separate": True,
            "automatic_retries": 0,
        },
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "model_calls": 0,
        "limitations": [
            "Type 0 real inner-product spaces; complete declared context retained.",
            "Real division is total, including a/0=0; no nonzero condition is inserted.",
            "Hypothesis consistency and natural-language equivalence require human review.",
            "Same fixed Lean implementation in two independent processes.",
            "No arbitrary Lean/imports/axioms, model calls or automatic legacy source admission.",
        ],
    }
    value["plan_fingerprint"] = digest(value)
    return value


def render(root, plan, *, replay=False):
    if replay:
        source = render_replay(root, plan["formal_statement"])
    else:
        source = render_source(
            {"extra_declarations": "", "proof": plan["proof_source"]}, plan["formal_statement"]
        )
        marker = "LeanExport.dumpEnv env (some locals.toList)"
        if source.count(marker) != 1:
            raise ValueError("Trusted export adapter changed")
        source = source.replace(
            marker, (root / "lean_workspace/mathlib-export.lean.in").read_text().rstrip()
        )
    marker = "import Mathlib.Data.Nat.Basic\n"
    if source.count(marker) != 1:
        raise ValueError("Trusted import adapter changed")
    return source.replace(marker, "import " + foundation.IMPORT + "\n")


class AnalysisStore(MathlibLabStore):
    def __init__(self, root, *, base=None):
        self.root = Path(root).resolve()
        canonical_base = self.root / "artifacts/analysis-streaming"
        self.base = canonical_base if base is None else Path(base).absolute()
        if (
            self.base.resolve() != self.base
            or not self.base.is_relative_to(self.root / "artifacts")
            or (self.base != canonical_base and self.base.name != "analysis-streaming-private")
        ):
            raise ValueError(
                "Use the canonical analysis store or an isolated analysis-streaming-private area"
            )
        self.base.mkdir(parents=True, exist_ok=True)
        self.path = self.base / "lab.sqlite3"
        self.tasks = TaskStore(self.root)
        with self.transaction() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and "analysis_streaming_identity" not in tables:
                raise ValueError("Cannot adopt or migrate another ledger into analysis")
            db.execute(
                "CREATE TABLE IF NOT EXISTS analysis_streaming_identity (id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT NOT NULL)"
            )
            row = db.execute("SELECT mode FROM analysis_streaming_identity WHERE id=1").fetchone()
            if row is None:
                db.execute("INSERT INTO analysis_streaming_identity VALUES (1,?)", (MODE,))
            elif row[0] != MODE:
                raise ValueError("Wrong analysis ledger version")
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL)"
            )

    @contextmanager
    def transaction(self, write=True):
        if self.base.resolve() != self.base or self.path.is_symlink():
            raise ValueError("Analysis ledger was redirected")
        with super().transaction(write) as db:
            yield db

    def marker(self, run_id):
        return foundation._safe(
            self.root,
            str(
                (self.base / "invalidated-runs" / (run_identifier(run_id) + ".json")).relative_to(
                    self.root
                )
            ),
        )

    def invalidate(self, run_id, reason):
        marker = self.marker(run_id)
        marker.parent.mkdir(exist_ok=True)
        try:
            with marker.open("xb") as outgoing:
                outgoing.write(canonical({"permanent": True, "reason": str(reason)[:2000]}) + b"\n")
        except FileExistsError:
            pass

    def preview(self, raw):
        return prepare(self.root, raw)

    def catalog(self):
        return catalog()

    def gate(self, row, check_files=True):
        if row["invalidated"] or self.marker(row["run_id"]).exists():
            return "Previously invalidated analysis evidence; restoration cannot revive it"
        if row["plan"]["source_manifest"] != manifest(self.root):
            return "Analysis compiler, policy, imports or runtime identity changed"
        if self.preview(row["plan"]["request"]) != row["plan"]:
            return "Full analysis request differs from its confirmed compilation"
        alignment = row["alignment"]
        if (
            alignment["confirmed"] is not True
            or not isinstance(alignment["reviewer"], str)
            or not alignment["reviewer"].strip()
            or alignment["authentication"] != "local-assertion-only"
            or alignment["plan_fingerprint"] != row["plan"]["plan_fingerprint"]
            or row["submission_fingerprint"]
            != digest({"plan": row["plan"]["plan_fingerprint"], "reviewer": alignment["reviewer"]})
        ):
            return "Full statement and semantic alignment confirmation changed"
        if not check_files:
            return None
        directory = self.directory(row["run_id"])
        for name, expected in row["files"].items():
            path = foundation._safe(self.root, str((directory / name).relative_to(self.root)))
            if file_record(path)["sha256"] != expected:
                return "Analysis artifacts changed"
        if (
            row["state"] == "COMPLETED"
            or row["math_verdict"] == "PROVED"
            or row["evidence_level"] == "E4"
        ):
            if (row["state"], row["math_verdict"], row["evidence_level"]) != (
                "COMPLETED",
                "PROVED",
                "E4",
            ):
                return "Inconsistent analysis completion state"
            expected = {"plan.json", "runtime-snapshot.json"}
            expected |= {"candidate/" + n for n in ("Candidate.lean", "stdout.log", "stderr.log")}
            expected |= {
                "replay/" + n
                for n in (
                    "Candidate.lean",
                    "stdout.log",
                    "stderr.log",
                    "proof.ndjson",
                    "policy.json",
                )
            }
            if (
                set(row["files"]) != expected
                or strict_json((directory / "plan.json").read_text()) != row["plan"]
            ):
                return "Incomplete analysis proof artifact scope"
            snapshot = load(self.root)
            if (directory / "runtime-snapshot.json").read_bytes() != canonical(
                snapshot.data
            ) + b"\n":
                return "Recorded analysis runtime differs"
            elapsed = 0
            for phase, tag in (("candidate", "MRA_AUDIT:"), ("replay", "MRA_REPLAY:")):
                execution = row["result"][phase]
                seconds = execution["elapsed_seconds"]
                if (
                    execution["run_status"] != "COMPLETED"
                    or execution["returncode"] != 0
                    or type(execution["pid"]) is not int
                    or execution["pid"] <= 0
                    or not isinstance(seconds, (int, float))
                    or not math.isfinite(seconds)
                    or seconds < 0
                ):
                    return "Incomplete independent analysis process"
                elapsed += seconds
                work = directory / phase
                if (work / "Candidate.lean").read_text() != render(
                    self.root, row["plan"], replay=phase == "replay"
                ):
                    return "Analysis checker does not match the full target"
                if (
                    foundation._read_log(work / "stderr.log")
                    or audit(
                        foundation._read_log(work / "stdout.log"), tag, replay=phase == "replay"
                    )
                    != execution["audit"]
                ):
                    return "Analysis kernel audit differs"
            if row["result"]["candidate"]["pid"] == row["result"]["replay"]["pid"]:
                return "Independent replay process missing"
            if (
                abs(elapsed - row["result"]["worker_seconds"]) > 0.000001
                or elapsed > row["plan"]["request"]["timeout_seconds"] + 0.2
            ):
                return "Analysis worker accounting differs"
            if (directory / "replay/proof.ndjson").read_bytes() != extract_export(
                foundation._read_log(directory / "candidate/stdout.log")
            ):
                return "Analysis replay did not check the recorded proof data"
            if (directory / "replay/policy.json").read_bytes() != canonical(POLICY) + b"\n":
                return "Analysis replay policy differs"
        elif row["math_verdict"] != "UNKNOWN" or row["evidence_level"] != "E0":
            return "Incomplete checking cannot yield a mathematical judgment"
        return None

    def report(self, run_id):
        run_identifier(run_id)
        try:
            row = self.read(run_id)
            if row["run_id"] != run_id:
                raise ValueError("Analysis ledger identity differs")
        except (ValueError, KeyError, TypeError):
            self.invalidate(run_id, "Corrupt analysis ledger row")
            raise
        try:
            reason = self.gate(row)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            reason = str(error)
        if reason:
            self.invalidate(run_id, reason)
            with self.transaction() as db:
                row = self.read(run_id, db)
                if (
                    not row["invalidated"]
                    or row["math_verdict"] != "UNKNOWN"
                    or row["evidence_level"] != "E0"
                ):
                    row.update(invalidated=True, math_verdict="UNKNOWN", evidence_level="E0")
                    self.event(row, "ANALYSIS_EVIDENCE_INVALIDATED", reason=reason)
                    self.save(db, row)
        return {
            **row,
            "mode": MODE,
            "fresh_attestation": False,
            "artifact_status": "UNAVAILABLE"
            if reason
            else "MATCHES_RECORDED_HASHES"
            if row["files"]
            else "NOT_YET_RECORDED",
        }

    def execute(self, run_id, lock_fd):
        row = self.read(run_id)
        directory = self.directory(run_id)
        directory.mkdir(parents=True, exist_ok=False)
        result = {
            "candidate": None,
            "replay": None,
            "error": None,
            "worker_seconds": 0.0,
            "integrity_scans": [],
        }
        state, proved = "EXECUTION_ERROR", False

        def cancelled():
            current = self.read(run_id)
            return (
                current["cancel_requested"]
                or current["invalidated"]
                or self.marker(run_id).exists()
            )

        def checkpoint(phase, _):
            with self.transaction() as db:
                current = self.read(run_id, db)
                current["stage"] = phase
                self.event(current, phase)
                self.save(db, current)

        started = time.monotonic()
        try:
            reason = self.gate(row, False)
            if reason:
                raise ValueError(reason)
            plan = self.preview(row["plan"]["request"])
            (directory / "plan.json").write_bytes(canonical(plan) + b"\n")
            snapshot = load(self.root)
            result["integrity_scans"].append({"phase": "initial", **snapshot.last_scan})
            (directory / "runtime-snapshot.json").write_bytes(canonical(snapshot.data) + b"\n")
            runner = foundation._Runner(self.root)
            for phase, folder in (("LEAN_RUNNING", "candidate"), ("REPLAY_RUNNING", "replay")):
                if cancelled():
                    state = "CANCELLED"
                    break
                reason = self.gate(self.read(run_id), False)
                if reason:
                    raise ValueError(reason)
                snapshot.verify()
                result["integrity_scans"].append(
                    {"phase": "before-" + folder, **snapshot.last_scan}
                )
                work = directory / folder
                work.mkdir()
                if folder == "replay":
                    raw = extract_export(foundation._read_log(directory / "candidate/stdout.log"))
                    (work / "proof.ndjson").write_bytes(raw)
                    (work / "policy.json").write_bytes(canonical(POLICY) + b"\n")
                (work / "Candidate.lean").write_text(
                    render(self.root, plan, replay=folder == "replay"), encoding="utf-8"
                )
                outcome = runner._run_worker(
                    work,
                    directory / ("sandbox-" + folder),
                    work / "stdout.log",
                    work / "stderr.log",
                    plan["request"]["timeout_seconds"] - result["worker_seconds"],
                    phase=phase,
                    cancel_requested=cancelled,
                    checkpoint=checkpoint,
                    lifecycle_lock_fd=lock_fd,
                )
                result[folder] = outcome
                result["worker_seconds"] += outcome["elapsed_seconds"]
                state = outcome["run_status"]
                if state != "COMPLETED":
                    break
                output, stderr = (
                    foundation._read_log(work / "stdout.log"),
                    foundation._read_log(work / "stderr.log"),
                )
                if stderr or outcome["returncode"] != 0:
                    missing = "object file" in output or "unknown module prefix" in output
                    state = (
                        "EXECUTION_ERROR"
                        if stderr
                        or missing
                        or outcome["returncode"] is None
                        or outcome["returncode"] < 0
                        else "REJECTED"
                        if folder == "candidate"
                        else "POLICY_BLOCKED"
                    )
                    result["error"] = (stderr or output.split("MRA_EXPORT_BEGIN", 1)[0])[-6000:]
                    break
                outcome["audit"] = audit(
                    output,
                    "MRA_AUDIT:" if folder == "candidate" else "MRA_REPLAY:",
                    replay=folder == "replay",
                )
                if folder == "replay":
                    snapshot.verify()
                    result["integrity_scans"].append(
                        {"phase": "after-replay", **snapshot.last_scan}
                    )
                    proved = True
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            state, result["error"] = "POLICY_BLOCKED", str(error)
        result["elapsed_with_integrity_seconds"] = time.monotonic() - started
        files = {
            str(p.relative_to(directory)): file_hash(p)
            for p in directory.rglob("*")
            if p.is_file()
            and not any(x.startswith("sandbox-") for x in p.relative_to(directory).parts)
        }
        with self.transaction() as db:
            current = self.read(run_id, db)
            if current["cancel_requested"]:
                state, proved = "CANCELLED", False
            try:
                reason = self.gate(current, False)
            except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
                reason = str(error)
            if reason:
                self.invalidate(run_id, reason)
                current["invalidated"] = True
                state, proved, result["error"] = "POLICY_BLOCKED", False, reason
            current.update(
                state=state,
                stage=state,
                files=files,
                result=result,
                math_verdict="PROVED" if proved and state == "COMPLETED" else "UNKNOWN",
                evidence_level="E4" if proved and state == "COMPLETED" else "E0",
            )
            self.event(current, state)
            self.save(db, current)
        return self.report(run_id)
