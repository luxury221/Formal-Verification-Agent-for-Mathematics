"""Versioned smooth one-dimensional Poisson identities with checked proof-data reuse."""

from copy import deepcopy
import math
from pathlib import Path
import time
import os
import uuid

from .domain import canonical, digest
from .formalizer import strict_json
from .integrity import IntegrityError, file_hash, file_record
from . import pde_energy as prior
from .streaming_integrity_v2 import WatchedSnapshot, SCAN_POLICY, host_dependencies
from .mathlib_lab import audit
from .replay import extract_export, render_replay
from .tasks import TaskStore
from .verifier import NativeLeanBackend, render_source

MODE = "pde-energy-watched-v2"
POLICY = {
    "mode": MODE,
    "allowed_axioms": ["propext", "Classical.choice", "Quot.sound"],
    "forbidden_axioms": ["sorryAx", "Lean.ofReduceBool"],
    "require_exact_target": True,
    "require_independent_replay": True,
}
BASE = ".runtime/" + MODE
IMPORTS = ("Mathlib.MeasureTheory.Integral.IntervalIntegral.IntegrationByParts",)
SNAPSHOT = BASE + "/snapshot.json"
LOCK = BASE + "/lock.json"
TREES = (".runtime/lean-4.35.0-linux", ".runtime/pde-energy-v1/mathlib")
FILES = (
    "mra/pde_energy_watched.py",
    "mra/streaming_integrity_v2.py",
    "mra/pde_energy.py",
    ".runtime/pde-energy-v1/adoption.json",
    ".runtime/pde-energy-v1/acquisition.json",
    ".runtime/pde-energy-v1/snapshot.json",
    ".runtime/pde-energy-v1/lock.json",
    "evals/pde-energy-v1/probes.json",
    "lean_workspace/lemma-import.lean.in",
    "mra/verifier.py",
    "mra/mathlib_lab.py",
    "mra/replay.py",
    "mra/integrity.py",
    "mra/domain.py",
    "mra/formalizer.py",
    "mra/tasks.py",
    "ops/scripts/sandbox_worker.py",
    "lean_workspace/mathlib-export.lean.in",
    "lean_workspace/replay-checker.lean.in",
    "lean_workspace/environment.json",
    BASE + "/adoption.json",
    BASE + "/acquisition.json",
)
PROBES = strict_json(
    (Path(__file__).resolve().parents[1] / "evals/pde-energy-v1/probes.json").read_text()
)["probes"]


def catalog():
    return {
        "mode": MODE,
        "imports": list(IMPORTS),
        "probes": deepcopy(PROBES),
        "policy": deepcopy(POLICY),
        "scan_policy": deepcopy(SCAN_POLICY),
        "inherited_mathematics": prior.MODE,
    }


def _safe(root, relative):
    path = root / relative
    if path.resolve() != path.absolute():
        raise IntegrityError("Calculus path was redirected: " + relative)
    return path


def _context(root):
    import platform

    adoption = strict_json(_safe(root, BASE + "/adoption.json").read_text())
    acquired = strict_json(_safe(root, BASE + "/acquisition.json").read_text())
    inherited_raw = _safe(root, prior.SNAPSHOT).read_bytes()
    inherited_data = strict_json(inherited_raw.decode())
    inherited_lock = strict_json(_safe(root, prior.LOCK).read_text())
    if (
        adoption.get("mode") != MODE
        or adoption.get("lean") != "4.35.0"
        or adoption.get("mathlib") != "56a005855ea17e0b0958f73ec801ae140b0a1679"
        or adoption.get("kernel") != platform.release()
        or adoption.get("import_scope") != list(IMPORTS)
        or adoption.get("scan_policy") != SCAN_POLICY
        or adoption.get("native_host_files") != list(host_dependencies())
        or adoption.get("inherited_fingerprint") != digest(inherited_data)
        or inherited_lock != {"mode": prior.MODE, "sha256": digest_bytes(inherited_raw)}
        or acquired.get("mode") != MODE
        or acquired.get("new_downloads") != 0
        or acquired.get("new_installs") != 0
        or acquired.get("immutable_mathlib_tree") != TREES[1]
        or acquired.get("inherited_snapshot_sha256") != digest_bytes(inherited_raw)
    ):
        raise IntegrityError("Incomplete or incompatible watched runtime adoption")
    return {
        "mode": MODE,
        "import_scope": list(IMPORTS),
        "catalog": digest(catalog()),
        "scan_policy": digest(SCAN_POLICY),
        "inherited_runtime": adoption["inherited_fingerprint"],
    }, tuple(sorted(set(inherited_data["host_files"]) | set(host_dependencies())))


def seal(root):
    root = Path(root).resolve()
    if (root / LOCK).exists() or (root / SNAPSHOT).exists():
        raise IntegrityError("Watched seal exists; no implicit replacement or migration")
    inherited = prior.load(root)
    provenance, hosts = _context(root)
    snapshot = WatchedSnapshot.capture(root, TREES, FILES, hosts, provenance)
    snapshot.verify()
    raw = canonical(snapshot.data) + b"\n"
    with _safe(root, SNAPSHOT).open("xb") as outgoing:
        outgoing.write(raw)
    with _safe(root, LOCK).open("xb") as outgoing:
        outgoing.write(canonical({"mode": MODE, "sha256": digest_bytes(raw)}) + b"\n")
    snapshot._seal_hashes = {SNAPSHOT: digest_bytes(raw), LOCK: file_hash(root / LOCK)}
    snapshot.verify()
    inherited.verify()
    return {
        "mode": MODE,
        "fingerprint": snapshot.fingerprint,
        "legacy_preserved": True,
        "monitor": snapshot.last_scan,
    }


def _load(root):
    root = Path(root).resolve()
    lock = strict_json(_safe(root, LOCK).read_text())
    raw = _safe(root, SNAPSHOT).read_bytes()
    if lock != {"mode": MODE, "sha256": digest_bytes(raw)}:
        raise IntegrityError("Calculus snapshot does not match its lock")
    data = strict_json(raw.decode())
    provenance, hosts = _context(root)
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("trees"), dict)
        or not isinstance(data.get("files"), dict)
        or not isinstance(data.get("host_files"), dict)
        or data.get("schema_version") != "1"
        or set(data["trees"]) != set(TREES)
        or set(data["files"]) != set(FILES)
        or set(data["host_files"]) != set(hosts)
        or data["provenance"] != provenance
    ):
        raise IntegrityError("Incomplete analysis snapshot scope")
    snapshot = WatchedSnapshot(root, data)
    snapshot._seal_hashes = {SNAPSHOT: digest_bytes(raw), LOCK: file_hash(root / LOCK)}
    snapshot.verify()
    return snapshot


def load(root):
    try:
        return _load(root)
    except IntegrityError:
        raise
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        raise IntegrityError("Calculus snapshot unavailable or malformed") from error


def digest_bytes(raw):
    import hashlib

    return hashlib.sha256(raw).hexdigest()


def render(root, probe, *, replay=False):
    if probe not in PROBES:
        raise ValueError("Only unchanged repository analysis probes are accepted")
    if replay:
        source = render_replay(root, probe["statement"])
    else:
        source = render_source(
            {"extra_declarations": probe.get("extra", ""), "proof": probe["proof"]},
            probe["statement"],
        )
        marker = "LeanExport.dumpEnv env (some locals.toList)"
        if source.count(marker) != 1:
            raise ValueError("Trusted export adapter changed")
        fragment = (root / "lean_workspace/mathlib-export.lean.in").read_text().rstrip()
        source = source.replace(marker, fragment)
    if not replay and probe.get("source_probe"):
        parent = next(p for p in PROBES if p["id"] == probe["source_probe"])
        template = (root / "lean_workspace/lemma-import.lean.in").read_text()
        if template.count("__MRA_SOURCE_STATEMENT__") != 1 or "__MRA_SLOT__" not in template:
            raise ValueError("Trusted proof-data import adapter changed")
        fragment = template.replace("__MRA_SOURCE_STATEMENT__", parent["statement"]).replace(
            "__MRA_SLOT__", "0"
        )
        old = """  let locals := env.checked.get.constants.foldStage2
    (fun names name _ => names.push name) (#[] : Array Name)"""
        new = """  let mut locals : Array Name := #[]
  for (name, _) in env.constants do
    if (Name.str Name.anonymous "MraCandidate").isPrefixOf name then
      locals := locals.push name"""
        if source.count(old) != 1 or source.count("namespace MraCandidate\n") != 1:
            raise ValueError("Trusted proof-data export boundary changed")
        source = (
            source.replace(
                "import LeanExport.Basic",
                "import LeanExport.Basic\nimport LeanExport.Parse\nimport Lean.Replay",
            )
            .replace("namespace MraCandidate\n", fragment + "\nnamespace MraCandidate\n", 1)
            .replace(old, new)
        )
        use_marker = "  let report := Json.mkObj ["
        if source.count(use_marker) != 2:
            raise ValueError("Trusted direct-use audit boundary changed")
        before, after = source.rsplit(use_marker, 1)
        source = (
            before
            + (
                "  let used := info.getUsedConstantsAsSet.toArray.map Name.toString\n"
                '  unless used.contains "MraCandidate.Reused.s0.checked" do\n'
                '    throwError "MRA_SOURCE_NOT_USED"\n'
                '  logInfo m!"MRA_SOURCE_USE:{(toJson used).compress}"\n' + use_marker
            )
            + after
        )

    if source.count("import Mathlib.Data.Nat.Basic\n") != 1:
        raise ValueError("Trusted import adapter changed")
    source = source.replace(
        "import Mathlib.Data.Nat.Basic\n",
        "".join("import " + name + "\n" for name in IMPORTS),
    )
    marker = "set_option autoImplicit false"
    if source.count(marker) != 1:
        raise ValueError("Trusted command boundary changed")
    return source.replace(marker, "open MeasureTheory\nopen scoped Interval Filter\n\n" + marker)


class _Runner:
    # Reuse just the unchanged namespace process launcher, with an explicit tree.
    _run_worker = NativeLeanBackend._run_worker

    def __init__(self, root):
        self.root = root
        self.runtime = root / TREES[0]
        self.mathlib = root / TREES[1]


def _read_log(path):
    with path.open("rb") as incoming:
        raw = incoming.read(1048577)
    if len(raw) > 1048576:
        raise ValueError("Calculus log exceeds sandbox limit")
    return raw.decode("utf-8", errors="replace")


def _save_result(directory, value):
    temporary = directory / (".result-" + uuid.uuid4().hex + ".tmp")
    with temporary.open("xb") as outgoing:
        outgoing.write(canonical(value) + b"\n")
        outgoing.flush()
        os.fsync(outgoing.fileno())
    temporary.replace(directory / "result.json")


def _source(root, probe, source_run_id):
    """Bind an exact currently valid producer; replay its data, never its module."""
    wanted = probe.get("source_probe")
    if wanted is None:
        if source_run_id is not None:
            raise ValueError("This probe does not accept source evidence")
        return None, None
    import re

    if not isinstance(source_run_id, str) or not re.fullmatch(
        r"pdewatched-[a-f0-9]{32}", source_run_id
    ):
        raise ValueError("A checked producer run ID is required")
    directory = _safe(root, "artifacts/pde-energy-watched-v2/runs/" + source_run_id)
    request = strict_json(
        _safe(root, str((directory / "request.json").relative_to(root))).read_text()
    )
    parent = next(p for p in PROBES if p["id"] == wanted)
    # Validate the fixed decreasing catalog edge before recursively checking status.
    if request.get("probe") != parent:
        raise IntegrityError("Source has a different complete theorem")
    row = report(root, source_run_id)
    if row.get("state") != "VERIFIED" or row.get("math_verdict") != "PROVED":
        raise IntegrityError("Source evidence is not currently verified")
    name = "replay/proof.ndjson"
    data = _safe(root, str((directory / name).relative_to(root))).read_bytes()
    if not 0 < len(data) <= 524288 or digest_bytes(data) != row["files"][name]:
        raise IntegrityError("Source proof data changed or exceeds its bound")
    return {
        "run_id": source_run_id,
        "probe_id": wanted,
        "statement": parent["statement"],
        "runtime": request["runtime"],
        "request_sha256": file_hash(directory / "request.json"),
        "result_sha256": file_hash(directory / "result.json"),
        "data_sha256": digest_bytes(data),
        "data_bytes": len(data),
    }, data


def _import_audit(output):
    rows = [
        strict_json(line.split("MRA_IMPORT:", 1)[1])
        for line in output.splitlines()
        if "MRA_IMPORT:" in line
    ]
    if len(rows) != 1:
        raise ValueError("Missing or repeated proof-data import audit")
    row = rows[0]
    if (
        set(row)
        != {"slot", "type_matches", "candidate_module_imported", "replayed_declarations", "axioms"}
        or type(row["slot"]) is not int
        or row["slot"] != 0
        or row["type_matches"] is not True
        or row["candidate_module_imported"] is not False
        or type(row["replayed_declarations"]) is not int
        or row["replayed_declarations"] < 1
        or not isinstance(row["axioms"], list)
        or any(a not in POLICY["allowed_axioms"] for a in row["axioms"])
    ):
        raise ValueError("Imported proof data did not pass full-type kernel checking")
    return row


def _source_use(output):
    rows = [
        strict_json('{"uses":' + line.split("MRA_SOURCE_USE:", 1)[1] + "}")["uses"]
        for line in output.splitlines()
        if "MRA_SOURCE_USE:" in line
    ]
    if (
        len(rows) != 1
        or not isinstance(rows[0], list)
        or not all(isinstance(n, str) for n in rows[0])
    ):
        raise ValueError("Missing or malformed direct proof-use audit")
    if "MraCandidate.Reused.s0.checked" not in rows[0]:
        raise ValueError("The imported checked theorem was not used by the result")
    return rows[0]


def run(root, probe_id, reviewer, *, source_run_id=None, cancel_requested=None):
    root = Path(root).resolve()
    probe = next((deepcopy(p) for p in PROBES if p["id"] == probe_id), None)
    if probe is None or not isinstance(reviewer, str) or not 1 <= len(reviewer.strip()) <= 200:
        raise ValueError("Known probe and named local alignment reviewer required")
    with TaskStore(root).worker_lock() as fd:
        started = time.monotonic()
        snapshot = load(root)
        source_record, source_data = _source(root, probe, source_run_id)
        directory = _safe(
            root, "artifacts/pde-energy-watched-v2/runs/pdewatched-" + uuid.uuid4().hex
        )
        directory.mkdir(parents=True)
        result = {
            "mode": MODE,
            "state": "RUNNING",
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
            "candidate": None,
            "replay": None,
            "error": None,
            "files": {},
        }
        _save_result(directory, result)
        request = {
            "mode": MODE,
            "run_id": directory.name,
            "probe": probe,
            "runtime": snapshot.fingerprint,
            "timeout_seconds": 40,
            "policy": POLICY,
            "source": source_record,
            "alignment": {
                "reviewer": reviewer,
                "authentication": "local-assertion-only",
                "original": probe["original"],
                "statement": probe["statement"],
            },
        }
        (directory / "request.json").write_bytes(canonical(request) + b"\n")
        (directory / "runtime-snapshot.json").write_bytes(canonical(snapshot.data) + b"\n")
        runner = _Runner(root)
        worker_seconds = 0.0
        try:
            for phase in ("candidate", "replay"):
                work = directory / phase
                work.mkdir()
                (work / "Candidate.lean").write_text(render(root, probe, replay=phase == "replay"))
                if phase == "candidate" and source_data is not None:
                    (work / "source-0.ndjson").write_bytes(source_data)
                if phase == "replay":
                    raw = extract_export(_read_log(directory / "candidate/stdout.log"))
                    (work / "proof.ndjson").write_bytes(raw)
                    (work / "policy.json").write_bytes(canonical(POLICY) + b"\n")
                snapshot.verify()
                if _source(root, probe, source_run_id) != (source_record, source_data):
                    raise IntegrityError("Source evidence changed during execution")
                execution = runner._run_worker(
                    work,
                    directory / ("sandbox-" + phase),
                    work / "stdout.log",
                    work / "stderr.log",
                    40 - worker_seconds,
                    phase="LEAN_RUNNING" if phase == "candidate" else "REPLAY_RUNNING",
                    lifecycle_lock_fd=fd,
                    cancel_requested=cancel_requested,
                )
                worker_seconds += execution["elapsed_seconds"]
                result[phase] = execution
                output = _read_log(work / "stdout.log")
                stderr = _read_log(work / "stderr.log")
                if execution["run_status"] != "COMPLETED":
                    result["state"] = execution["run_status"]
                    break
                if stderr or execution["returncode"] is None or execution["returncode"] < 0:
                    result["state"] = "EXECUTION_ERROR"
                    result["error"] = stderr[-4000:]
                    break
                if execution["returncode"] != 0:
                    missing = "object file" in output or "unknown module prefix" in output
                    result["state"] = (
                        "EXECUTION_ERROR"
                        if missing
                        else "REJECTED"
                        if phase == "candidate"
                        else "POLICY_BLOCKED"
                    )
                    result["error"] = output[-4000:]
                    break
                execution["audit"] = audit(
                    output,
                    "MRA_AUDIT:" if phase == "candidate" else "MRA_REPLAY:",
                    replay=phase == "replay",
                )
            else:
                snapshot.verify()
                if _source(root, probe, source_run_id) != (source_record, source_data):
                    raise IntegrityError("Source evidence changed before publication")
                if source_record is not None:
                    result["candidate"]["import_audit"] = _import_audit(
                        _read_log(directory / "candidate/stdout.log")
                    )
                    result["candidate"]["source_use"] = _source_use(
                        _read_log(directory / "candidate/stdout.log")
                    )
                if result["candidate"]["pid"] == result["replay"]["pid"]:
                    raise ValueError("Replay did not use a distinct process")
                if cancel_requested is not None and cancel_requested():
                    result.update(state="CANCELLED", math_verdict="UNKNOWN", evidence_level="E0")
                else:
                    result.update(
                        state="VERIFIED",
                        math_verdict="PROVED",
                        evidence_level="E4_ALIGNED_AND_VERIFIED",
                    )
        except (KeyboardInterrupt, SystemExit) as error:
            result.update(
                state="INTERRUPTED",
                math_verdict="UNKNOWN",
                evidence_level="E0",
                error=type(error).__name__,
            )
        except (IntegrityError, ValueError, OSError) as error:
            result.update(
                state="POLICY_BLOCKED",
                math_verdict="UNKNOWN",
                evidence_level="E0",
                error=str(error),
            )
        finally:
            if result["state"] == "RUNNING":
                result["state"] = "INTERRUPTED"
            result["worker_seconds"] = worker_seconds
            result["total_seconds"] = time.monotonic() - started
            result["files"] = {
                str(p.relative_to(directory)): file_hash(p)
                for p in directory.rglob("*")
                if p.is_file()
                and not any(x.startswith("sandbox-") for x in p.parts)
                and p.name != "result.json"
            }
            _save_result(directory, result)
        return {"directory": str(directory.relative_to(root)), **report(root, directory.name)}


def recover(root, run_id):
    """Explicitly close a supervisor interruption; never resume or certify."""
    import re

    root = Path(root).resolve()
    if not isinstance(run_id, str) or not re.fullmatch(r"pdewatched-[a-f0-9]{32}", run_id):
        raise ValueError("Invalid energy run ID")
    with TaskStore(root).worker_lock():
        directory = _safe(root, "artifacts/pde-energy-watched-v2/runs/" + run_id)
        value = strict_json(
            _safe(root, str((directory / "result.json").relative_to(root))).read_text()
        )
        if value.get("state") == "RUNNING":
            value.update(
                state="INTERRUPTED",
                math_verdict="UNKNOWN",
                evidence_level="E0",
                recovered=True,
                error="Explicit recovery after worker-lock acquisition; no automatic retry",
            )
            _save_result(directory, value)
        return report(root, run_id)


def report(root, run_id):
    root = Path(root).resolve()
    import re

    if not isinstance(run_id, str) or not re.fullmatch(r"pdewatched-[a-f0-9]{32}", run_id):
        raise ValueError("Invalid analysis run ID")
    directory = _safe(root, "artifacts/pde-energy-watched-v2/runs/" + run_id)
    marker = _safe(root, str((directory / "invalidated.json").relative_to(root)))
    try:
        result = strict_json(
            _safe(root, str((directory / "result.json").relative_to(root))).read_text()
        )
        if marker.exists():
            raise IntegrityError("Previously invalidated analysis evidence")
        if result.get("state") == "RUNNING":
            return {
                "mode": MODE,
                "state": "RUNNING",
                "math_verdict": "UNKNOWN",
                "evidence_level": "E0",
            }
        if result.get("state") == "INTERRUPTED" and result.get("recovered") is True:
            return {
                "mode": MODE,
                "state": "INTERRUPTED",
                "math_verdict": "UNKNOWN",
                "evidence_level": "E0",
                "error": result.get("error"),
            }
        snapshot = load(root)
        request = strict_json(
            _safe(root, str((directory / "request.json").relative_to(root))).read_text()
        )
        probe = request["probe"]
        if (
            probe not in PROBES
            or request["run_id"] != run_id
            or request["mode"] != MODE
            or request["runtime"] != snapshot.fingerprint
        ):
            raise IntegrityError("Calculus request or provider changed")
        source_record = request.get("source")
        source_id = source_record.get("run_id") if isinstance(source_record, dict) else None
        checked_source, source_data = _source(root, probe, source_id)
        if source_record != checked_source:
            raise IntegrityError("Recorded producer identity or current evidence changed")
        expected = {
            "request.json",
            "runtime-snapshot.json",
            "candidate/Candidate.lean",
            "candidate/stdout.log",
            "candidate/stderr.log",
        }
        if source_data is not None:
            expected.add("candidate/source-0.ndjson")
            if (directory / "candidate/source-0.ndjson").read_bytes() != source_data:
                raise IntegrityError("Imported bytes differ from the checked producer")
        if result["replay"] is not None:
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
        if set(result["files"]) != expected:
            raise IntegrityError("Incomplete analysis evidence scope")
        for name, value in result["files"].items():
            if (
                file_record(_safe(root, str((directory / name).relative_to(root))))["sha256"]
                != value
            ):
                raise IntegrityError("Calculus artifact changed: " + name)
        if (directory / "runtime-snapshot.json").read_bytes() != canonical(snapshot.data) + b"\n":
            raise IntegrityError("Recorded runtime changed")
        for phase in ("candidate", "replay"):
            if result[phase] is not None:
                if (directory / phase / "Candidate.lean").read_text() != render(
                    root, probe, replay=phase == "replay"
                ):
                    raise IntegrityError("Calculus checker changed")
        if result["mode"] != MODE:
            raise IntegrityError("Wrong analysis result mode")
        if result["state"] == "VERIFIED":
            if request["policy"] != POLICY or request["timeout_seconds"] != 40:
                raise IntegrityError("Calculus policy or budget changed")
            alignment = request["alignment"]
            if (
                alignment["original"] != probe["original"]
                or alignment["statement"] != probe["statement"]
                or alignment["authentication"] != "local-assertion-only"
                or not alignment["reviewer"].strip()
            ):
                raise IntegrityError("Incomplete local semantic alignment")
            for phase, tag in (("candidate", "MRA_AUDIT:"), ("replay", "MRA_REPLAY:")):
                execution = result[phase]
                if (
                    execution["run_status"] != "COMPLETED"
                    or execution["returncode"] != 0
                    or type(execution["pid"]) is not int
                    or execution["pid"] <= 0
                    or type(execution["elapsed_seconds"]) not in (int, float)
                    or not math.isfinite(execution["elapsed_seconds"])
                    or execution["elapsed_seconds"] < 0
                ):
                    raise IntegrityError("Incomplete independent kernel check")
                checked = audit(
                    _read_log(directory / phase / "stdout.log"), tag, replay=phase == "replay"
                )
                if checked != execution["audit"] or _read_log(directory / phase / "stderr.log"):
                    raise IntegrityError("Kernel audit changed")
            if checked_source is not None:
                imported = _import_audit(_read_log(directory / "candidate/stdout.log"))
                if _source_use(_read_log(directory / "candidate/stdout.log")) != result[
                    "candidate"
                ].get("source_use"):
                    raise IntegrityError("Direct proof-use audit changed")
                if imported != result["candidate"].get("import_audit"):
                    raise IntegrityError("Proof-data import audit changed")
            elapsed = sum(result[p]["elapsed_seconds"] for p in ("candidate", "replay"))
            if (
                type(result["worker_seconds"]) not in (int, float)
                or not math.isfinite(result["worker_seconds"])
                or abs(elapsed - result["worker_seconds"]) > 0.000001
                or elapsed > 40.2
            ):
                raise IntegrityError("Calculus worker accounting differs")
            if result["candidate"]["pid"] == result["replay"]["pid"]:
                raise IntegrityError("Shared candidate and replay process")
            if (directory / "replay/proof.ndjson").read_bytes() != extract_export(
                _read_log(directory / "candidate/stdout.log")
            ):
                raise IntegrityError("Replayed proof data differs from candidate export")
            if (directory / "replay/policy.json").read_bytes() != canonical(POLICY) + b"\n":
                raise IntegrityError("Replay policy changed")
            if (
                result["math_verdict"] != "PROVED"
                or result["evidence_level"] != "E4_ALIGNED_AND_VERIFIED"
            ):
                raise IntegrityError("Inconsistent verified state")
        elif result["math_verdict"] != "UNKNOWN" or result["evidence_level"] != "E0":
            raise IntegrityError("Failed execution cannot yield a mathematical judgment")
        return result
    except (IntegrityError, ValueError, KeyError, TypeError, OSError) as error:
        if directory.is_dir() and not marker.exists():
            marker.write_bytes(canonical({"reason": str(error), "permanent": True}) + b"\n")
        return {
            "mode": MODE,
            "state": "INVALIDATED",
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
            "error": str(error),
        }
