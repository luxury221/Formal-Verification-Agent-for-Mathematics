"""Versioned classical PDE ledger with exact source imports and independent proof replay."""

from contextlib import contextmanager
from copy import deepcopy
import math
import re
import uuid
from pathlib import Path
import time

from . import pde_language_runtime as foundation
from . import pde_energy_watched as prior
from .pde_language import MODE, catalog, compile_request
from .analysis_language import text
from .domain import canonical, digest
from .formalizer import exact_object, strict_json
from .integrity import IntegrityError, file_hash, file_record
from .mathlib_lab import MathlibLabStore, audit, now
from .replay import extract_export, render_replay
from .tasks import TaskStore
from .verifier import render_source

IMPORTS = foundation.IMPORTS
POLICY = foundation.POLICY


def run_identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"pdelang-[a-f0-9]{32}", value):
        raise ValueError("Invalid PDE language run ID")
    return value


def import_audits(output, sources):
    rows = [
        strict_json(line.split("MRA_IMPORT:", 1)[1])
        for line in output.splitlines()
        if "MRA_IMPORT:" in line
    ]
    if len(rows) != len(sources):
        raise IntegrityError("Missing or repeated imported proof audit")
    by_slot = {}
    for row in rows:
        if (
            set(row)
            != {
                "slot",
                "type_matches",
                "candidate_module_imported",
                "replayed_declarations",
                "axioms",
            }
            or type(row["slot"]) is not int
            or row["slot"] in by_slot
            or row["type_matches"] is not True
            or row["candidate_module_imported"] is not False
            or type(row["replayed_declarations"]) is not int
            or row["replayed_declarations"] < 1
            or not isinstance(row["axioms"], list)
            or any(a not in POLICY["allowed_axioms"] for a in row["axioms"])
        ):
            raise IntegrityError("Imported source failed its exact-type kernel audit")
        by_slot[row["slot"]] = row
    if set(by_slot) != {s["slot"] for s in sources}:
        raise IntegrityError("Imported source slots differ")
    return [by_slot[s["slot"]] for s in sources]


def source_use(output, sources):
    values = [
        strict_json('{"uses":' + line.split("MRA_SOURCE_USE:", 1)[1] + "}")["uses"]
        for line in output.splitlines()
        if "MRA_SOURCE_USE:" in line
    ]
    if not sources:
        if values:
            raise IntegrityError("Unexpected source-use audit")
        return []
    if (
        len(values) != 1
        or not isinstance(values[0], list)
        or not all(isinstance(v, str) for v in values[0])
    ):
        raise IntegrityError("Missing source-use audit")
    if any("MraCandidate.Reused.s" + str(s["slot"]) + ".checked" not in values[0] for s in sources):
        raise IntegrityError("A claimed source is absent from the final proof")
    return values[0]


def manifest(root):
    data, hashes = foundation.identity(root)
    return {
        "mode": MODE,
        "runtime_fingerprint": digest(data),
        "seals": hashes,
        "files": {n: r["sha256"] for n, r in data["files"].items()},
        "imports": list(IMPORTS),
        "policy": deepcopy(POLICY),
        "catalog": catalog(),
    }


def prepare(root, raw, *, store, walk=None, _with_data=False):
    compiled = compile_request(raw)
    sources, source_data = store.resolve_sources(compiled["sources"], walk)
    compiled.update(resolved_sources=sources, source_authorization_checked=True)
    value = {
        **compiled,
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
            "Real-to-Real calculus with explicit measure and lexical interval/a.e. binders.",
            "Division, deriv and Bochner integral are total; no missing condition is inserted.",
            "Hypothesis consistency and natural-language equivalence require human review.",
            "Same fixed Lean implementation in two independent processes.",
            "No arbitrary Lean/imports/axioms or model calls; sources require exact current E4.",
        ],
    }
    value["plan_fingerprint"] = digest(value)
    return (value, source_data) if _with_data else value


def render(root, plan, *, replay=False):
    sources = plan["resolved_sources"]
    if replay:
        source = render_replay(root, plan["formal_statement"])
    else:
        source = render_source(
            {"extra_declarations": "", "proof": plan["proof_source"]}, plan["formal_statement"]
        )
        marker = "LeanExport.dumpEnv env (some locals.toList)"
        if source.count(marker) != 1:
            raise IntegrityError("Trusted export adapter changed")
        source = source.replace(
            marker, (root / "lean_workspace/mathlib-export.lean.in").read_text().rstrip()
        )
        if sources:
            template = (root / "lean_workspace/lemma-import.lean.in").read_text()
            if template.count("__MRA_SOURCE_STATEMENT__") != 1 or "__MRA_SLOT__" not in template:
                raise IntegrityError("Trusted source import adapter changed")
            fragments = [
                template.replace("__MRA_SOURCE_STATEMENT__", s["statement"]).replace(
                    "__MRA_SLOT__", str(s["slot"])
                )
                for s in sources
            ]
            old = """  let locals := env.checked.get.constants.foldStage2
    (fun names name _ => names.push name) (#[] : Array Name)"""
            new = """  let mut locals : Array Name := #[]
  for (name, _) in env.constants do
    if (Name.str Name.anonymous "MraCandidate").isPrefixOf name then
      locals := locals.push name"""
            if source.count(old) != 1 or source.count("namespace MraCandidate\n") != 1:
                raise IntegrityError("Trusted import/export boundary changed")
            source = source.replace(
                "import LeanExport.Basic",
                "import LeanExport.Basic\nimport LeanExport.Parse\nimport Lean.Replay",
            ).replace(
                "namespace MraCandidate\n", "\n".join(fragments) + "\nnamespace MraCandidate\n", 1
            )
            source = source.replace(old, new)
            marker = "  let report := Json.mkObj ["
            if source.count(marker) != len(sources) + 1:
                raise IntegrityError("Trusted source audit boundary changed")
            before, after = source.rsplit(marker, 1)
            use = "  let used := info.getUsedConstantsAsSet.toArray.map Name.toString\n"
            for s in sources:
                name = "MraCandidate.Reused.s" + str(s["slot"]) + ".checked"
                use += '  unless used.contains "' + name + '" do\n'
                use += '    throwError "MRA_SOURCE_NOT_USED"\n'
            use += '  logInfo m!"MRA_SOURCE_USE:{(toJson used).compress}"\n'
            source = before + use + marker + after
    marker = "import Mathlib.Data.Nat.Basic\n"
    if source.count(marker) != 1:
        raise IntegrityError("Trusted import adapter changed")
    source = source.replace(marker, "".join("import " + n + "\n" for n in IMPORTS))
    marker = "set_option autoImplicit false"
    if source.count(marker) != 1:
        raise IntegrityError("Trusted command boundary changed")
    return source.replace(marker, "open MeasureTheory\nopen scoped Interval Filter\n\n" + marker)


def diagnostic(output, stderr, phase):
    prefix = stderr or output.split("MRA_EXPORT_BEGIN", 1)[0]
    lines = [
        line for line in prefix.splitlines() if not line.startswith(("MRA_AUDIT:", "MRA_REPLAY:"))
    ]
    message = "\n".join(lines)[:6000]
    kind = (
        "TYPE_MISMATCH"
        if "type mismatch" in message.lower()
        else "UNSOLVED_GOALS"
        if "unsolved goals" in message
        else "LEAN_DIAGNOSTIC"
    )
    return {"phase": phase, "kind": kind, "text": message, "mathematical_counterexample": False}


class PDELanguageStore(MathlibLabStore):
    def __init__(self, root, *, base=None):
        self.root = Path(root).resolve()
        canonical_base = self.root / "artifacts/pde-language"
        self.base = canonical_base if base is None else Path(base).absolute()
        if (
            self.base.resolve() != self.base
            or not self.base.is_relative_to(self.root / "artifacts")
            or (self.base != canonical_base and self.base.name != "pde-language-private")
        ):
            raise ValueError(
                "Use the canonical analysis store or an isolated pde-language-private area"
            )
        self.base.mkdir(parents=True, exist_ok=True)
        self.path = self.base / "lab.sqlite3"
        self.tasks = TaskStore(self.root)
        with self.transaction() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and "pde_language_identity" not in tables:
                raise ValueError("Cannot adopt or migrate another ledger into analysis")
            db.execute(
                "CREATE TABLE IF NOT EXISTS pde_language_identity (id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT NOT NULL)"
            )
            row = db.execute("SELECT mode FROM pde_language_identity WHERE id=1").fetchone()
            if row is None:
                db.execute("INSERT INTO pde_language_identity VALUES (1,?)", (MODE,))
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

    def read(self, run_id, db=None):
        run_identifier(run_id)
        if db is not None:
            return self.decode(
                db.execute("SELECT body, sha256 FROM runs WHERE id=?", (run_id,)).fetchone()
            )
        with self.transaction(False) as conn:
            return self.read(run_id, conn)

    def submit(self, data):
        exact_object(
            data,
            {"request_id", "request", "plan_fingerprint", "reviewer", "confirmed"},
            "Mathlib submission",
        )
        request_id = text(data["request_id"], 128, "Request ID")
        reviewer = text(data["reviewer"], 200, "Reviewer")
        if data["confirmed"] is not True:
            raise ValueError(
                "Explicit confirmation of original, full formula, library terms and budget is required"
            )
        plan = self.preview(data["request"])
        if data["plan_fingerprint"] != plan["plan_fingerprint"]:
            raise ValueError("Preview changed; review the current complete statement again")
        key = digest({"plan": plan["plan_fingerprint"], "reviewer": reviewer})
        with self.transaction() as db:
            previous = db.execute(
                "SELECT body, sha256 FROM runs WHERE request_id=?", (request_id,)
            ).fetchone()
            if previous:
                row = self.decode(previous)
                if row["submission_fingerprint"] != key:
                    raise ValueError("Request ID is already bound to a different confirmation")
                return row
            if db.execute("SELECT count(*) FROM runs").fetchone()[0] >= 200:
                raise ValueError("Mathlib experiment ledger is limited to 200 runs")
            run_id = "pdelang-" + uuid.uuid4().hex
            row = {
                "run_id": run_id,
                "request_id": request_id,
                "submission_fingerprint": key,
                "plan": plan,
                "alignment": {
                    "reviewer": reviewer,
                    "confirmed": True,
                    "at": now(),
                    "plan_fingerprint": plan["plan_fingerprint"],
                    "authentication": "local-assertion-only",
                },
                "state": "QUEUED",
                "stage": "QUEUED",
                "math_verdict": "UNKNOWN",
                "evidence_level": "E0",
                "created_at": now(),
                "updated_at": now(),
                "cancel_requested": False,
                "invalidated": False,
                "events": [],
                "files": {},
                "result": None,
                "model_calls": 0,
            }
            self.event(row, "CONFIRMED_AND_QUEUED", reviewer=reviewer)
            db.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?)",
                (run_id, request_id, canonical(row).decode(), digest(row)),
            )
        return row

    def directory(self, run_id):
        path = self.base / "runs" / run_identifier(run_id)
        if not path.resolve().is_relative_to(self.base.resolve()) or path.is_symlink():
            raise ValueError("Mathlib artifact path escapes its store")
        return path

    def marker(self, run_id):
        return foundation.safe(
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
        return prepare(self.root, raw, store=self)

    def catalog(self):
        return catalog()

    def describe_source(self, run_id):
        if isinstance(run_id, str) and re.fullmatch(r"pdewatched-[a-f0-9]{32}", run_id):
            row = prior.report(self.root, run_id)
            if row.get("math_verdict") != "PROVED":
                raise IntegrityError("Select a currently verified source")
            directory = foundation.safe(self.root, "artifacts/pde-energy-watched-v2/runs/" + run_id)
            statement = strict_json((directory / "request.json").read_text())["probe"]["statement"]
            provider = prior.MODE
        else:
            row = self.report(run_identifier(run_id))
            if row["math_verdict"] != "PROVED" or row["invalidated"]:
                raise IntegrityError("Select a currently verified source")
            statement, provider = row["plan"]["formal_statement"], MODE
        return {
            "run_id": run_id,
            "provider": provider,
            "formal_statement": statement,
            "type_fingerprint": digest(statement),
            "source_authorization_checked": True,
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
            "limits": "A current source descriptor is not a proof of a new target",
        }

    def resolve_sources(self, sources, walk=None):
        records, data = [], {}
        for source in sources:
            run_id = source["run_id"]
            if run_id.startswith("pdewatched-"):
                row = prior.report(self.root, run_id)
                if (row.get("state"), row.get("math_verdict"), row.get("evidence_level")) != (
                    "VERIFIED",
                    "PROVED",
                    "E4_ALIGNED_AND_VERIFIED",
                ):
                    raise IntegrityError("Watched source is not currently E4")
                directory = foundation.safe(
                    self.root, "artifacts/pde-energy-watched-v2/runs/" + run_id
                )
                request = strict_json((directory / "request.json").read_text())
                statement = request["probe"]["statement"]
                stamp = {
                    "request": file_hash(directory / "request.json"),
                    "result": file_hash(directory / "result.json"),
                }
                provider = prior.MODE
            else:
                row = self.report(run_id, _walk=walk)
                if (
                    row["state"],
                    row["math_verdict"],
                    row["evidence_level"],
                    row["invalidated"],
                ) != ("COMPLETED", "PROVED", "E4", False):
                    raise IntegrityError("PDE language source is not currently E4")
                directory = self.directory(run_id)
                statement = row["plan"]["formal_statement"]
                stamp = {"row": digest(self.read(run_id))}
                provider = MODE
            if digest(statement) != source["type_fingerprint"]:
                raise IntegrityError("Source complete type fingerprint differs")
            proof = foundation.safe(
                self.root, str((directory / "replay/proof.ndjson").relative_to(self.root))
            )
            raw = proof.read_bytes()
            if (
                not 0 < len(raw) <= 524288
                or file_hash(proof) != row["files"]["replay/proof.ndjson"]
            ):
                raise IntegrityError("Source proof data changed or exceeds its bound")
            records.append(
                {
                    **deepcopy(source),
                    "provider": provider,
                    "statement": statement,
                    "stamp": stamp,
                    "data_sha256": prior.digest_bytes(raw),
                    "data_bytes": len(raw),
                }
            )
            data[source["slot"]] = raw
        if sum(len(raw) for raw in data.values()) > 524288:
            raise IntegrityError("Combined imported proof data exceeds 512 KiB")
        return records, data

    def source_stamps_match(self, records, db):
        """Recheck source revocation and durable identities inside the publication transaction."""
        for source in records:
            if source["provider"] == MODE:
                if self.marker(source["run_id"]).exists():
                    return False
                row = self.read(source["run_id"], db)
                if (
                    row["invalidated"]
                    or row["state"] != "COMPLETED"
                    or row["math_verdict"] != "PROVED"
                    or row["evidence_level"] != "E4"
                    or digest(row) != source["stamp"]["row"]
                ):
                    return False
            else:
                directory = foundation.safe(
                    self.root, "artifacts/pde-energy-watched-v2/runs/" + source["run_id"]
                )
                if (directory / "invalidated.json").exists():
                    return False
                if any(
                    file_hash(
                        foundation.safe(
                            self.root, str((directory / (n + ".json")).relative_to(self.root))
                        )
                    )
                    != h
                    for n, h in source["stamp"].items()
                ):
                    return False
        return True

    def gate(self, row, check_files=True, walk=None):
        if row["invalidated"] or self.marker(row["run_id"]).exists():
            return "Previously invalidated analysis evidence; restoration cannot revive it"
        if row["plan"]["source_manifest"] != manifest(self.root):
            return "Analysis compiler, policy, imports or runtime identity changed"
        prepared, imported_data = prepare(
            self.root, row["plan"]["request"], store=self, walk=walk, _with_data=True
        )
        if prepared != row["plan"]:
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
            path = foundation.safe(self.root, str((directory / name).relative_to(self.root)))
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
            expected |= {
                "candidate/source-" + str(s["slot"]) + ".ndjson"
                for s in row["plan"]["resolved_sources"]
            }
            for slot, raw in imported_data.items():
                if (directory / ("candidate/source-" + str(slot) + ".ndjson")).read_bytes() != raw:
                    return "Imported source bytes differ"
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
            snapshot = foundation.load(self.root)
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
                    or type(seconds) not in (int, float)
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
                    foundation.read_log(work / "stderr.log")
                    or audit(
                        foundation.read_log(work / "stdout.log"), tag, replay=phase == "replay"
                    )
                    != execution["audit"]
                ):
                    return "Analysis kernel audit differs"
            output = foundation.read_log(directory / "candidate/stdout.log")
            if import_audits(output, row["plan"]["resolved_sources"]) != row["result"][
                "candidate"
            ].get("import_audits") or source_use(output, row["plan"]["resolved_sources"]) != row[
                "result"
            ]["candidate"].get("source_use"):
                return "Imported type or direct-use audits changed"
            if row["result"]["candidate"]["pid"] == row["result"]["replay"]["pid"]:
                return "Independent replay process missing"
            if (
                type(row["result"]["worker_seconds"]) not in (int, float)
                or not math.isfinite(row["result"]["worker_seconds"])
                or abs(elapsed - row["result"]["worker_seconds"]) > 0.000001
                or elapsed > row["plan"]["request"]["timeout_seconds"] + 0.2
            ):
                return "Analysis worker accounting differs"
            if (directory / "replay/proof.ndjson").read_bytes() != extract_export(
                foundation.read_log(directory / "candidate/stdout.log")
            ):
                return "Analysis replay did not check the recorded proof data"
            if (directory / "replay/policy.json").read_bytes() != canonical(POLICY) + b"\n":
                return "Analysis replay policy differs"
        elif row["math_verdict"] != "UNKNOWN" or row["evidence_level"] != "E0":
            return "Incomplete checking cannot yield a mathematical judgment"
        return None

    def report(self, run_id, *, _walk=None):
        run_identifier(run_id)
        try:
            row = self.read(run_id)
            if row["run_id"] != run_id:
                raise ValueError("Analysis ledger identity differs")
        except (ValueError, KeyError, TypeError):
            self.invalidate(run_id, "Corrupt analysis ledger row")
            raise
        try:
            walk = _walk if _walk is not None else {"stack": set(), "seen": set()}
            if run_id in walk["stack"] or len(walk["stack"]) >= 8:
                raise IntegrityError("Cyclic or excessive PDE source depth")
            walk["seen"].add(run_id)
            if len(walk["seen"]) > 16:
                raise IntegrityError("PDE source closure exceeds 16 typed runs")
            walk["stack"].add(run_id)
            try:
                reason = self.gate(row, walk=walk)
            finally:
                walk["stack"].remove(run_id)
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
        result = {"candidate": None, "replay": None, "error": None, "worker_seconds": 0.0}
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
            snapshot = foundation.load(self.root)
            (directory / "runtime-snapshot.json").write_bytes(canonical(snapshot.data) + b"\n")
            runner = foundation.Runner(self.root)
            for phase, folder in (("LEAN_RUNNING", "candidate"), ("REPLAY_RUNNING", "replay")):
                if cancelled():
                    state = "CANCELLED"
                    break
                reason = self.gate(self.read(run_id), False)
                if reason:
                    raise ValueError(reason)
                snapshot.verify()
                work = directory / folder
                work.mkdir()
                if folder == "candidate":
                    sources, source_data = self.resolve_sources(plan["sources"])
                    if sources != plan["resolved_sources"]:
                        raise IntegrityError("Source changed before proof-data import")
                    for slot, raw in source_data.items():
                        (work / ("source-" + str(slot) + ".ndjson")).write_bytes(raw)
                if folder == "replay":
                    raw = extract_export(foundation.read_log(directory / "candidate/stdout.log"))
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
                    foundation.read_log(work / "stdout.log"),
                    foundation.read_log(work / "stderr.log"),
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
                    result["diagnostic"] = diagnostic(output, stderr, folder)
                    result["error"] = result["diagnostic"]["text"]
                    break
                outcome["audit"] = audit(
                    output,
                    "MRA_AUDIT:" if folder == "candidate" else "MRA_REPLAY:",
                    replay=folder == "replay",
                )
                if folder == "candidate":
                    outcome["import_audits"] = import_audits(output, plan["resolved_sources"])
                    outcome["source_use"] = source_use(output, plan["resolved_sources"])
                if folder == "replay":
                    snapshot.verify()
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
        try:
            reason = self.gate(self.read(run_id), False)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            reason = str(error)
        with self.transaction() as db:
            current = self.read(run_id, db)
            if current["cancel_requested"]:
                state, proved = "CANCELLED", False
            if (
                current["plan"] != row["plan"]
                or current["alignment"] != row["alignment"]
                or current["submission_fingerprint"] != row["submission_fingerprint"]
                or current["state"] != "RUNNING"
            ):
                reason = reason or "Confirmed target or execution state changed before publication"
            try:
                current_sources = self.source_stamps_match(row["plan"]["resolved_sources"], db)
            except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
                current_sources, reason = False, str(error)
            if current["invalidated"] or self.marker(run_id).exists() or not current_sources:
                reason = reason or "Source or confirmation changed before publication"
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
