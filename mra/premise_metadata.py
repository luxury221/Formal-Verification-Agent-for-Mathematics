"""Explicit, bounded reads of imported Lean declaration types; never proof receipts."""

from contextlib import ExitStack, contextmanager
import fcntl
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
from .premise_search import PremiseIndex
from .research_flow import atomic
from .search_evaluation import _envelope
from .tasks import TaskStore, WorkerBusy, _identifier, _now

SCHEMA = "lean-premise-metadata-v1"
TEMPLATE = "lean_workspace/premise-inspector.lean.in"
READER_FILES = ("mra/premise_metadata.py", TEMPLATE)
FINAL = {"READY", "AMBIGUOUS", "NOT_FOUND"}


def render(root, item):
    module = item["module"]
    if not isinstance(module, str) or not re.fullmatch(
        r"Mathlib(?:\.[A-Za-z_][A-Za-z_0-9]*)+", module
    ):
        raise ValueError("Only fixed Mathlib module identifiers may be imported")
    template = (Path(root) / TEMPLATE).read_text(encoding="utf-8")
    if template.count("__MRA_MODULE__") != 1:
        raise ValueError("Metadata reader template boundary changed")
    return template.replace("__MRA_MODULE__", module)


def parse_report(output, item):
    reports = [
        line.split("MRA_METADATA:", 1)[1] for line in output.splitlines() if "MRA_METADATA:" in line
    ]
    if len(reports) != 1 or len(reports[0]) > 100000:
        raise ValueError("Missing, duplicate or oversized Lean metadata report")
    report = strict_json(reports[0])
    exact_object(report, {"schema", "module", "written_name", "matches"}, "Lean metadata")
    if (report["schema"], report["module"], report["written_name"]) != (
        SCHEMA,
        item["module"],
        item["written_name"],
    ):
        raise ValueError("Lean metadata request identity changed")
    if not isinstance(report["matches"], list) or len(report["matches"]) > 8:
        raise ValueError("Lean metadata match budget exceeded")
    names = set()
    for match in report["matches"]:
        exact_object(
            match,
            {
                "name",
                "module",
                "kind",
                "universe_parameters",
                "type",
                "explicit_type",
                "binders",
                "conclusion",
            },
            "Lean declaration metadata",
        )
        name = match["name"]
        if (
            not isinstance(name, str)
            or name in names
            or match["module"] != item["module"]
            or not (name == item["written_name"] or name.endswith("." + item["written_name"]))
        ):
            raise ValueError("Lean declaration name or defining module changed")
        names.add(name)
        if match["kind"] not in {
            "theorem",
            "axiom",
            "definition",
            "opaque",
            "inductive",
            "constructor",
            "recursor",
            "quotient",
        }:
            raise ValueError("Unknown Lean declaration kind")
        if not isinstance(match["universe_parameters"], list) or not all(
            isinstance(x, str) for x in match["universe_parameters"]
        ):
            raise ValueError("Invalid universe parameters")
        if not all(
            isinstance(match[k], str) and match[k] for k in ("type", "explicit_type", "conclusion")
        ):
            raise ValueError("Missing complete type")
        if not isinstance(match["binders"], list) or len(match["binders"]) > 256:
            raise ValueError("Declaration parameter budget exceeded")
        for binder in match["binders"]:
            exact_object(binder, {"name", "kind", "type"}, "Lean binder")
            if binder["kind"] not in {
                "explicit",
                "implicit",
                "strict_implicit",
                "instance",
            } or not all(isinstance(binder[k], str) for k in ("name", "type")):
                raise ValueError("Invalid Lean parameter")
    report["matches"].sort(key=lambda x: x["name"])
    return report


@contextmanager
def process_lock(directory):
    fd = os.open(directory / ".process.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise WorkerBusy("This Lean type read is still running") from error
        yield fd
    finally:
        os.close(fd)  # Inherited descriptors retain ownership after supervisor exits.


@contextmanager
def checker_slot(root):
    """Allow idle queue polling to release its lock; never retry Lean execution."""
    tasks = TaskStore(root)
    deadline = time.monotonic() + 2
    with ExitStack() as stack:
        while True:
            try:
                fd = stack.enter_context(tasks.worker_lock())
                break
            except WorkerBusy:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        yield fd


class PremiseMetadata:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/premise-metadata"
        self.base.mkdir(parents=True, exist_ok=True)
        self.safe(self.base)

    def safe(self, path):
        if path.resolve() != path or not path.is_relative_to(self.root / "artifacts"):
            raise ValueError("Lean metadata storage was redirected")
        return path

    def identity(self, item):
        index_identity, _ = PremiseIndex(self.root).identity()
        lock = strict_json(runtime._safe(self.root, runtime.LOCK).read_text())
        if lock != {
            "mode": runtime.MODE,
            "sha256": file_hash(runtime._safe(self.root, runtime.SNAPSHOT)),
        }:
            raise ValueError("Lean metadata runtime seal changed")
        return {
            "schema": SCHEMA,
            "item_fingerprint": digest(item),
            "index_identity": index_identity["identity"],
            "runtime_snapshot_sha256": lock["sha256"],
            "reader_files": {name: file_hash(self.root / name) for name in READER_FILES},
        }

    def location(self, identity):
        return self.safe(self.base / ("metadata-" + digest(identity)))

    def attempt(self, directory, row):
        if not isinstance(row["attempt_id"], str) or not re.fullmatch(
            r"attempt-[a-f0-9]{32}", row["attempt_id"]
        ):
            raise ValueError("Invalid Lean metadata attempt identity")
        return self.safe(directory / row["attempt_id"])

    def status(self, item):
        identity = self.identity(item)
        directory = self.location(identity)
        if not (directory / "record.json").exists():
            return {"state": "NOT_READ", "math_verdict": "UNKNOWN", "evidence_level": "E0"}
        row = _envelope(self.safe(directory / "record.json"))
        if row["identity"] != identity or row["item"] != item:
            raise ValueError("Lean metadata cache identity changed")
        if row["math_verdict"] != "UNKNOWN" or row["evidence_level"] != "E0":
            raise ValueError("Metadata cannot grant mathematical evidence")
        if row["state"] not in FINAL | {"RUNNING", "FAILED", "TIMEOUT", "CANCELLED"}:
            raise ValueError("Unknown metadata execution state")
        if row["state"] == "RUNNING":
            try:
                with process_lock(directory):
                    return {
                        **row,
                        "state": "INTERRUPTED",
                        "error": "Reader exited; explicitly request a new read",
                    }
            except WorkerBusy:
                return row
        if row["state"] in FINAL:
            attempt = self.attempt(directory, row)
            for name, expected in row["artifact_hashes"].items():
                if name not in {"Candidate.lean", "request.json", "stdout.log", "stderr.log"}:
                    raise ValueError("Unexpected Lean metadata artifact")
                if file_hash(self.safe(attempt / name)) != expected:
                    raise ValueError("Lean metadata execution artifact changed")
            if set(row["artifact_hashes"]) != {
                "Candidate.lean",
                "request.json",
                "stdout.log",
                "stderr.log",
            }:
                raise ValueError("Lean metadata execution artifacts missing")
            parsed = parse_report((attempt / "stdout.log").read_text(), item)
            if (
                parsed != row["report"]
                or row["execution"]["returncode"] != 0
                or row["execution"]["run_status"] != "COMPLETED"
                or (attempt / "stderr.log").stat().st_size
                or (attempt / "Candidate.lean").read_text() != render(self.root, item)
                or strict_json((attempt / "request.json").read_text())
                != {"module": item["module"], "written_name": item["written_name"]}
            ):
                raise ValueError("Lean metadata report changed")
            expected = (
                "NOT_FOUND"
                if not parsed["matches"]
                else "READY"
                if len(parsed["matches"]) == 1
                else "AMBIGUOUS"
            )
            if row["state"] != expected:
                raise ValueError("Lean metadata resolution state changed")
        return row

    def start(self, item, request_id):
        _identifier(request_id)
        if PremiseIndex(self.root).lookup(item["item_id"]) != item:
            raise ValueError("Source reference changed before metadata read")
        render(self.root, item)
        identity = self.identity(item)
        directory = self.location(identity)
        if not directory.exists() and len(list(self.base.glob("metadata-*"))) >= 64:
            raise ValueError("Export metadata before exceeding 64 source reads")
        directory.mkdir(exist_ok=True)
        with process_lock(directory) as process_fd:
            old = (
                _envelope(directory / "record.json")
                if (directory / "record.json").exists()
                else None
            )
            if old and (old["identity"] != identity or old["item"] != item):
                raise ValueError("Lean metadata cache identity changed")
            if old and (request_id in old["requests"] or old["state"] in FINAL):
                return self.status(item)
            requests = old["requests"] if old else []
            if len(requests) >= 3:
                raise ValueError(
                    "Three explicit metadata attempts exhausted; inspect retained diagnostics"
                )
            with checker_slot(self.root) as worker_fd:
                attempt_id = "attempt-" + uuid.uuid4().hex
                attempt = self.safe(directory / attempt_id)
                attempt.mkdir()
                row = {
                    "identity": identity,
                    "item": item,
                    "requests": [*requests, request_id],
                    "attempt_id": attempt_id,
                    "state": "RUNNING",
                    "at": _now(),
                    "math_verdict": "UNKNOWN",
                    "evidence_level": "E0",
                    "error": None,
                    "report": None,
                    "execution": None,
                    "artifact_hashes": {},
                }
                atomic(directory / "record.json", row)
                atomic(attempt / "record.json", row)
                try:
                    with (attempt / "supervisor.log").open("wb") as log:
                        subprocess.Popen(
                            [
                                sys.executable,
                                "-m",
                                "mra.premise_metadata",
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
        if not re.fullmatch(r"metadata-[a-f0-9]{64}", key):
            raise ValueError("Invalid metadata task identity")
        # Descriptor inheritance keeps both locks alive across service restarts.
        os.fstat(process_fd)
        os.fstat(worker_fd)
        directory = self.safe(self.base / key)
        row = _envelope(directory / "record.json")
        attempt = self.attempt(directory, row)
        started = time.monotonic()
        try:
            if row["state"] != "RUNNING" or self.identity(row["item"]) != row["identity"]:
                raise ValueError("Metadata request or reader changed before execution")
            (attempt / "Candidate.lean").write_text(
                render(self.root, row["item"]), encoding="utf-8"
            )
            (attempt / "request.json").write_bytes(
                canonical(
                    {"module": row["item"]["module"], "written_name": row["item"]["written_name"]}
                )
                + b"\n"
            )
            # Existing immutable analysis cache has more modules than the P0 cache.
            # This reads metadata only; its proof-probe import policy is not expanded.
            snapshot = runtime.load(self.root)
            source = self.root / runtime.TREES[1] / row["item"]["path"]
            if file_record(source)["sha256"] != row["item"]["source_sha256"]:
                raise ValueError("Executable Mathlib cache source differs from retrieved source")
            row["execution"] = runtime._Runner(self.root)._run_worker(
                attempt,
                directory / ("sandbox-" + row["attempt_id"]),
                attempt / "stdout.log",
                attempt / "stderr.log",
                40,
                phase="METADATA_RUNNING",
                lifecycle_lock_fd=worker_fd,
            )
            snapshot.verify()
            if self.identity(row["item"]) != row["identity"]:
                raise ValueError("Metadata reader changed during execution")
            execution = row["execution"]
            if execution["run_status"] != "COMPLETED":
                row.update(
                    state=execution["run_status"],
                    error="Bounded Lean metadata worker did not finish",
                )
            elif execution["returncode"] != 0 or (attempt / "stderr.log").stat().st_size:
                row.update(
                    state="FAILED",
                    error=(
                        (attempt / "stderr.log").read_text() + (attempt / "stdout.log").read_text()
                    )[-4000:],
                )
            else:
                report = parse_report((attempt / "stdout.log").read_text(), row["item"])
                count = len(report["matches"])
                row.update(
                    state="NOT_FOUND" if count == 0 else "READY" if count == 1 else "AMBIGUOUS",
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


def main():
    if len(sys.argv) != 5 or sys.argv[1] != "--execute":
        raise SystemExit("Internal metadata worker expects --execute key lock_fd worker_fd")
    PremiseMetadata(Path.cwd()).execute(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))


if __name__ == "__main__":
    main()
