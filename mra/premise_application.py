"""Explicit Lean application diagnostics for complete research targets, never proof receipts."""

from copy import deepcopy
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from . import analysis_foundation as runtime
from .domain import canonical, digest
from .formalizer import exact_object, strict_json
from .integrity import file_hash, file_record
from .premise_metadata import PremiseMetadata, checker_slot, process_lock
from .premise_search import PremiseIndex
from .research_flow import atomic, identifier, lease
from .search_evaluation import _envelope
from .tasks import WorkerBusy, _identifier, _now

SCHEMA = "lean-premise-application-v1"
TEMPLATE = "lean_workspace/premise-application.lean.in"
FILES = ("mra/premise_application.py", TEMPLATE)
FINAL = {"NO_OPEN_GOALS", "OPEN_OBLIGATIONS", "NO_MATCH"}
ERRORS = (OSError, ValueError, KeyError, TypeError, RuntimeError)


def specification(store, row, data):
    """Only repository-compiled complete statements and explicitly selected cached theorems."""
    from .research_studio import backend_store

    attempt = store.round(row, data["round_id"])
    proposal = attempt["proposal"]
    citation = next(
        (x for x in row.get("citations", []) if x["citation_id"] == data["citation_id"]), None
    )
    if (
        citation is None
        or citation["withdrawn"]
        or citation["plan_fingerprint"] != proposal["plan_fingerprint"]
        or citation["node_id"] != proposal["node_id"]
    ):
        raise ValueError("Select an active theorem reference from this exact research step")
    item = citation["item"]
    if PremiseIndex(store.root).lookup(item["item_id"]) != item:
        raise ValueError("The referenced source changed")
    metadata = PremiseMetadata(store.root).status(item)
    if metadata["state"] not in ("READY", "AMBIGUOUS"):
        raise ValueError("Read the Lean declaration type before previewing an application")
    if not any(
        x["name"] == data["declaration"] and x["kind"] == "theorem"
        for x in metadata["report"]["matches"]
    ):
        raise ValueError("Explicitly select an exact theorem name from the cached Lean report")
    formal = proposal["formalization"]
    if formal["backend"] not in ("calculus", "pde"):
        raise ValueError("Application preview currently supports calculus and PDE candidates")
    plan = backend_store(store.root, formal["backend"]).preview(
        {"project_id": row["project_id"], "branch_id": "main", **formal["request"]}
    )
    compilation = plan.get("graph_compilation", plan)
    target = compilation["node_plans"][plan["problem_id"]]
    return {
        "study_id": row["study_id"],
        "source_fingerprint": row["source_fingerprint"],
        "plan_fingerprint": proposal["plan_fingerprint"],
        "node_id": proposal["node_id"],
        "round_id": data["round_id"],
        "round_fingerprint": digest(proposal),
        "citation_id": citation["citation_id"],
        "item": deepcopy(item),
        "declaration": data["declaration"],
        "metadata_identity": metadata["identity"],
        "metadata_report_fingerprint": digest(metadata["report"]),
        "backend": formal["backend"],
        "graph_preview_fingerprint": plan["plan_fingerprint"],
        "formal_statement": target["formal_statement"],
        "variables": target["variables"],
        "conditions": target["conditions"],
    }


def render(root, spec):
    module = spec["item"]["module"]
    if not isinstance(module, str) or not re.fullmatch(
        r"Mathlib(?:\.[A-Za-z_][A-Za-z_0-9]*)+", module
    ):
        raise ValueError("Invalid fixed Mathlib module")
    source = (Path(root) / TEMPLATE).read_text(encoding="utf-8")
    for marker, value in (
        ("__MRA_MODULE__", module),
        ("__MRA_STATEMENT__", spec["formal_statement"]),
    ):
        if source.count(marker) != 1:
            raise ValueError("Application template boundary changed")
        source = source.replace(marker, value)
    return source


def parse_report(output, spec):
    lines = [
        x.split("MRA_APPLICATION:", 1)[1] for x in output.splitlines() if "MRA_APPLICATION:" in x
    ]
    if len(lines) != 1 or len(lines[0]) > 100000:
        raise ValueError("Missing, duplicate or oversized Lean application report")
    report = strict_json(lines[0])
    exact_object(
        report,
        {
            "schema",
            "declaration",
            "full_type",
            "context",
            "target",
            "state",
            "application",
            "bindings",
            "discharged",
            "obligations",
            "unresolved_metavariables",
            "error",
        },
        "Lean application report",
    )
    if (report["schema"], report["declaration"]) != (SCHEMA, spec["declaration"]):
        raise ValueError("Lean application identity changed")
    if report["state"] not in FINAL or type(report["unresolved_metavariables"]) is not bool:
        raise ValueError("Invalid application diagnostic state")
    for name in ("full_type", "target"):
        if not isinstance(report[name], str) or not report[name]:
            raise ValueError("Missing complete application target")
    for name, fields in (
        ("context", {"name", "type"}),
        ("bindings", {"parameter", "kind", "type", "value"}),
        ("obligations", {"type", "context"}),
    ):
        if not isinstance(report[name], list) or len(report[name]) > 256:
            raise ValueError("Application diagnostic budget exceeded")
        for value in report[name]:
            exact_object(value, fields, "Lean application entry")
            if not all(isinstance(x, str) for x in value.values()):
                raise ValueError("Invalid application diagnostic entry")
    if (
        not isinstance(report["discharged"], list)
        or len(report["discharged"]) > 256
        or not all(isinstance(x, str) for x in report["discharged"])
    ):
        raise ValueError("Invalid discharged hypothesis list")
    if report["state"] == "NO_MATCH":
        if not isinstance(report["error"], str) or report["application"] is not None:
            raise ValueError("Missing application mismatch diagnostic")
    elif not isinstance(report["application"], str) or report["error"] is not None:
        raise ValueError("Invalid inferred application")
    if report["state"] == "NO_OPEN_GOALS" and (
        report["obligations"] or report["unresolved_metavariables"]
    ):
        raise ValueError("Unresolved obligations cannot disappear from diagnostics")
    if report["state"] == "OPEN_OBLIGATIONS" and not (
        report["obligations"] or report["unresolved_metavariables"]
    ):
        raise ValueError("Open-obligation state requires an unresolved condition or parameter")
    return report


class ApplicationPreview:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/premise-applications"
        self.base.mkdir(parents=True, exist_ok=True)
        self.safe(self.base)

    def safe(self, path):
        if path.resolve() != path or not path.is_relative_to(self.root / "artifacts"):
            raise ValueError("Application storage was redirected")
        return path

    def identity(self, spec):
        return {
            "schema": SCHEMA,
            "specification_fingerprint": digest(spec),
            "files": {name: file_hash(self.root / name) for name in FILES},
        }

    def directory(self, identity):
        return self.safe(self.base / ("application-" + digest(identity)))

    def attempt(self, directory, row):
        if not re.fullmatch(r"attempt-[a-f0-9]{32}", row["attempt_id"]):
            raise ValueError("Invalid application attempt identity")
        return self.safe(directory / row["attempt_id"])

    def current(self, spec):
        from .research_studio import StudioStore

        store = StudioStore(self.root)
        if specification(store, store.read(spec["study_id"]), spec) != spec:
            raise ValueError("Application source, candidate or declaration changed")

    def status(self, spec):
        identity = self.identity(spec)
        directory = self.directory(identity)
        if not (directory / "record.json").exists():
            return {"state": "NOT_STARTED", "math_verdict": "UNKNOWN", "evidence_level": "E0"}
        row = _envelope(self.safe(directory / "record.json"))
        if row["identity"] != identity or row["specification"] != spec:
            raise ValueError("Application cache identity changed")
        if (row["math_verdict"], row["evidence_level"]) != ("UNKNOWN", "E0"):
            raise ValueError("Application diagnostics cannot grant proof evidence")
        if row["state"] not in FINAL | {"RUNNING", "FAILED", "TIMEOUT", "CANCELLED"}:
            raise ValueError("Unknown application lifecycle state")
        if row["state"] == "RUNNING":
            try:
                with process_lock(directory):
                    return {
                        **row,
                        "state": "INTERRUPTED",
                        "error": "Reader exited; explicitly retry",
                    }
            except WorkerBusy:
                return row
        if row["state"] in FINAL:
            attempt = self.attempt(directory, row)
            expected = {"Candidate.lean", "request.json", "stdout.log", "stderr.log"}
            if set(row["artifact_hashes"]) != expected:
                raise ValueError("Application artifacts missing")
            for name, sha in row["artifact_hashes"].items():
                if file_hash(self.safe(attempt / name)) != sha:
                    raise ValueError("Application artifact changed")
            if (
                parse_report((attempt / "stdout.log").read_text(), spec) != row["report"]
                or (attempt / "Candidate.lean").read_text() != render(self.root, spec)
                or strict_json((attempt / "request.json").read_text())
                != {"declaration": spec["declaration"]}
                or row["execution"]["run_status"] != "COMPLETED"
                or row["execution"]["returncode"] != 0
                or (attempt / "stderr.log").stat().st_size
                or row["state"] != row["report"]["state"]
            ):
                raise ValueError("Application execution report changed")
        return row

    def start(self, spec, request_id):
        _identifier(request_id)
        self.current(spec)
        identity = self.identity(spec)
        directory = self.directory(identity)
        with lease(self.base):
            if not directory.exists() and len(list(self.base.glob("application-*"))) >= 64:
                raise ValueError("Export before exceeding 64 application previews")
            directory.mkdir(exist_ok=True)
        with process_lock(directory) as process_fd:
            old = self.status(spec)
            if old["state"] in FINAL or request_id in old.get("requests", []):
                return old
            requests = old.get("requests", [])
            if len(requests) >= 3:
                raise ValueError("Three explicit application attempts exhausted")
            with checker_slot(self.root) as worker_fd:
                row = {
                    "identity": identity,
                    "specification": spec,
                    "requests": [*requests, request_id],
                    "attempt_id": "attempt-" + uuid.uuid4().hex,
                    "state": "RUNNING",
                    "at": _now(),
                    "math_verdict": "UNKNOWN",
                    "evidence_level": "E0",
                    "report": None,
                    "error": None,
                    "execution": None,
                    "artifact_hashes": {},
                }
                attempt = self.attempt(directory, row)
                attempt.mkdir()
                atomic(directory / "record.json", row)
                atomic(attempt / "record.json", row)
                try:
                    with (attempt / "supervisor.log").open("wb") as log:
                        subprocess.Popen(
                            [
                                sys.executable,
                                "-m",
                                "mra.premise_application",
                                "--execute",
                                directory.name,
                                str(process_fd),
                                str(worker_fd),
                            ],
                            cwd=self.root,
                            stdin=subprocess.DEVNULL,
                            stdout=log,
                            stderr=log,
                            start_new_session=True,
                            pass_fds=(process_fd, worker_fd),
                            env={
                                "PATH": "/usr/bin:/bin",
                                "LANG": "C.UTF-8",
                                "PYTHONPATH": str(self.root),
                            },
                        )
                except OSError as error:
                    row.update(state="FAILED", error=str(error)[:500])
                    atomic(attempt / "record.json", row)
                    atomic(directory / "record.json", row)
                    raise
                return row

    def execute(self, key, process_fd, worker_fd):
        if not re.fullmatch(r"application-[a-f0-9]{64}", key):
            raise ValueError("Invalid application task identity")
        os.fstat(process_fd)
        os.fstat(worker_fd)
        directory = self.safe(self.base / key)
        row = _envelope(directory / "record.json")
        spec = row["specification"]
        attempt = self.attempt(directory, row)
        started = time.monotonic()
        try:
            if row["state"] != "RUNNING" or self.identity(spec) != row["identity"]:
                raise ValueError("Application reader changed before execution")
            self.current(spec)
            (attempt / "Candidate.lean").write_text(render(self.root, spec), encoding="utf-8")
            (attempt / "request.json").write_bytes(
                canonical({"declaration": spec["declaration"]}) + b"\n"
            )
            snapshot = runtime.load(self.root)
            source = self.root / runtime.TREES[1] / spec["item"]["path"]
            if file_record(source)["sha256"] != spec["item"]["source_sha256"]:
                raise ValueError("Executable Mathlib source differs from retrieved source")
            row["execution"] = runtime._Runner(self.root)._run_worker(
                attempt,
                directory / ("sandbox-" + row["attempt_id"]),
                attempt / "stdout.log",
                attempt / "stderr.log",
                40,
                phase="APPLICATION_PREVIEW_RUNNING",
                lifecycle_lock_fd=worker_fd,
            )
            snapshot.verify()
            self.current(spec)
            if self.identity(spec) != row["identity"]:
                raise ValueError("Application reader changed during execution")
            execution = row["execution"]
            if execution["run_status"] != "COMPLETED":
                row.update(
                    state=execution["run_status"],
                    error="Bounded Lean application worker did not finish",
                )
            elif execution["returncode"] != 0 or (attempt / "stderr.log").stat().st_size:
                row.update(
                    state="FAILED",
                    error=(
                        (attempt / "stderr.log").read_text() + (attempt / "stdout.log").read_text()
                    )[-4000:],
                )
            else:
                report = parse_report((attempt / "stdout.log").read_text(), spec)
                row.update(
                    state=report["state"],
                    report=report,
                    artifact_hashes={
                        name: file_hash(attempt / name)
                        for name in ("Candidate.lean", "request.json", "stdout.log", "stderr.log")
                    },
                )
        except Exception as error:
            row.update(state="FAILED", error=type(error).__name__ + ": " + str(error)[:2000])
        row.update(finished_at=_now(), elapsed_seconds=time.monotonic() - started)
        atomic(attempt / "record.json", row)
        atomic(directory / "record.json", row)


def validate_references(row):
    references = row.get("applications", [])
    if not isinstance(references, list) or len(references) > 16:
        raise ValueError("Application preview history budget exceeded")
    seen = set()
    for reference in references:
        exact_object(
            reference,
            {"application_id", "request_id", "specification", "fingerprint"},
            "Application reference",
        )
        identifier(reference["application_id"], "application")
        _identifier(reference["request_id"])
        if (
            reference["application_id"] in seen
            or digest(reference["specification"]) != reference["fingerprint"]
        ):
            raise ValueError("Application reference identity changed")
        seen.add(reference["application_id"])
        if reference["specification"]["study_id"] != row["study_id"]:
            raise ValueError("Application belongs to another notebook")


def states(store, row):
    values = []
    for reference in row.get("applications", []):
        spec = reference["specification"]
        value = {
            "application_id": reference["application_id"],
            "round_id": spec["round_id"],
            "declaration": spec["declaration"],
            "variables": spec["variables"],
            "conditions": spec["conditions"],
            "formal_statement": spec["formal_statement"],
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
        }
        try:
            if specification(store, row, spec) != spec:
                raise ValueError("The candidate or source changed; retained as history")
            value.update(ApplicationPreview(store.root).status(spec))
        except ERRORS as error:
            value.update(state="UNAVAILABLE", error=str(error)[:500])
        # Return the bounded diagnostic, not all repeated source/cache identity fields.
        value.pop("specification", None)
        value.pop("identity", None)
        values.append(value)
    return values


def start(store, key, data):
    exact_object(
        data,
        {"request_id", "expected_revision", "round_id", "citation_id", "declaration"},
        "Preview a theorem application",
    )
    with lease(store.directory(key)):
        row = store.read(key)
        new = store.remember(row, data)
        if any(x["state"] == "RUNNING" for x in row["operations"]):
            raise ValueError("Finish the model operation before starting Lean application preview")
        references = row.setdefault("applications", [])
        if new:
            if len(references) >= 16:
                raise ValueError("Sixteen application previews per notebook")
            spec = specification(store, row, data)
            reference = {
                "application_id": "application-" + uuid.uuid4().hex,
                "request_id": data["request_id"],
                "specification": spec,
                "fingerprint": digest(spec),
            }
            references.append(reference)
            # Reserve a visible request before spawning; a busy checker remains explicitly retryable.
            store.save(row, "THEOREM_APPLICATION_REQUESTED_WITHOUT_PROOF")
        else:
            reference = next(x for x in references if x["request_id"] == data["request_id"])
    ApplicationPreview(store.root).start(reference["specification"], data["request_id"])
    return store.report(key)


def context(store, row, node_id):
    selected = {
        x["application_id"]
        for x in row.get("applications", [])
        if x["specification"]["node_id"] == node_id
        and x["specification"]["plan_fingerprint"] == row["plans"][-1]["fingerprint"]
    }
    values = [x for x in states(store, row) if x["application_id"] in selected]
    return {
        "diagnostics": [x for x in values if x["state"] in FINAL][-4:],
        "unavailable": [
            {"application_id": x["application_id"], "error": x.get("error")}
            for x in values
            if x["state"] == "UNAVAILABLE"
        ],
        "scope": "Historical Lean elaboration diagnostics for exact candidates; no proof or semantic alignment",
    }


if __name__ == "__main__":
    if len(sys.argv) != 5 or sys.argv[1] != "--execute":
        raise SystemExit("Internal application worker expects --execute key lock_fd worker_fd")
    ApplicationPreview(Path.cwd()).execute(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
