"""Isolated, repository-owned analysis probes; no arbitrary Lean or legacy admission."""

from copy import deepcopy
from pathlib import Path
import time
import os
import uuid

from .domain import canonical, digest
from .formalizer import strict_json
from .integrity import ContentSnapshot, IntegrityError, file_hash, file_record
from .mathlib_lab import audit
from .replay import extract_export, render_replay
from .tasks import TaskStore
from .verifier import NativeLeanBackend, render_source

MODE = "analysis-foundation-v1"
POLICY = {
    "mode": MODE,
    "allowed_axioms": ["propext", "Classical.choice", "Quot.sound"],
    "forbidden_axioms": ["sorryAx", "Lean.ofReduceBool"],
    "require_exact_target": True,
    "require_independent_replay": True,
}
BASE = ".runtime/" + MODE
IMPORT = "Mathlib.Analysis.InnerProductSpace.Basic"
SNAPSHOT = BASE + "/snapshot.json"
LOCK = BASE + "/lock.json"
TREES = (".runtime/lean-4.35.0-linux", BASE + "/mathlib")
FILES = (
    "mra/analysis_foundation.py",
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
SPACE = "∀ (F : Type) [NormedAddCommGroup F] [InnerProductSpace ℝ F]"
PROBES = (
    {
        "id": "real-self",
        "original": "每个实数与自身的实内积非负。",
        "statement": "∀ x : ℝ, 0 ≤ inner ℝ x x",
        "proof": "by intro x; exact real_inner_self_nonneg",
        "expected": "VERIFIED",
        "source": "real_inner_self_nonneg",
    },
    {
        "id": "space-self",
        "original": "任意 Type 0 实内积空间中，每个向量与自身的内积非负。",
        "statement": SPACE + " (x : F), 0 ≤ inner ℝ x x",
        "proof": "by intro F _ _ x; exact real_inner_self_nonneg",
        "expected": "VERIFIED",
        "source": "real_inner_self_nonneg",
    },
    {
        "id": "cauchy-schwarz",
        "original": "任意 Type 0 实内积空间中，内积的范数不超过两向量范数之积。",
        "statement": SPACE + " (x y : F), ‖inner ℝ x y‖ ≤ ‖x‖ * ‖y‖",
        "proof": "by intro F _ _ x y; exact norm_inner_le_norm x y",
        "expected": "VERIFIED",
        "source": "norm_inner_le_norm",
    },
    {
        "id": "wrong-sign",
        "original": "错误符号对照：任意实数的自身内积非正。",
        "statement": "∀ x : ℝ, inner ℝ x x ≤ 0",
        "proof": "by intro x; exact real_inner_self_nonneg",
        "expected": "REJECTED",
        "source": "sign mutation of real-self; not a refutation attempt",
    },
    {
        "id": "missing-instance",
        "original": "遗漏内积空间实例的类型条件对照。",
        "statement": "∀ (F : Type) [NormedAddCommGroup F] (x : F), 0 ≤ inner ℝ x x",
        "proof": "by intro F _ x; exact real_inner_self_nonneg",
        "expected": "REJECTED",
        "source": "instance omission of space-self",
    },
    {
        "id": "mixed-types",
        "original": "把实数和复数混放在同一个实数内积的类型对照。",
        "statement": "∀ (x : ℝ) (z : ℂ), 0 ≤ inner ℝ x z",
        "proof": "by intro x z; exact real_inner_self_nonneg",
        "expected": "REJECTED",
        "source": "type mutation of real-self",
    },
    {
        "id": "unused-axiom",
        "original": "即使主目标正确，也必须拒绝未使用的局部公理。",
        "statement": "∀ x : ℝ, 0 ≤ inner ℝ x x",
        "proof": "by intro x; exact real_inner_self_nonneg",
        "extra": "axiom unapproved : False",
        "expected": "POLICY_BLOCKED",
        "source": "local-axiom security control",
    },
    {
        "id": "placeholder",
        "original": "实数非负目标中的 sorry 占位必须拒绝。",
        "statement": "∀ x : ℝ, 0 ≤ inner ℝ x x",
        "proof": "by sorry",
        "expected": "POLICY_BLOCKED",
        "source": "sorryAx security control",
    },
)


def catalog():
    return {"mode": MODE, "import": IMPORT, "probes": deepcopy(PROBES), "policy": deepcopy(POLICY)}


def _safe(root, relative):
    path = root / relative
    if path.resolve() != path.absolute():
        raise IntegrityError("Analysis path was redirected: " + relative)
    return path


def seal(root):
    root = Path(root).resolve()
    if (root / LOCK).exists() or (root / SNAPSHOT).exists():
        raise IntegrityError("Analysis seal exists; no implicit replacement or migration")
    legacy = ContentSnapshot.load_runtime(root)
    legacy.verify()
    adoption = strict_json(_safe(root, BASE + "/adoption.json").read_text())
    acquired = strict_json(_safe(root, BASE + "/acquisition.json").read_text())
    if (
        adoption["lean"] != "4.35.0"
        or adoption["mathlib"] != "56a005855ea17e0b0958f73ec801ae140b0a1679"
        or adoption["import_scope"] != [IMPORT]
        or acquired["exit_code"] != 0
        or acquired["new_target_olean"] is not True
        or not acquired["cache_files"]
    ):
        raise IntegrityError("Incomplete or incompatible analysis preparation")
    mathlib = _safe(root, TREES[1])
    if (mathlib / ".mra-source-pin").read_text().strip() != adoption["mathlib"]:
        raise IntegrityError("Wrong analysis Mathlib pin")
    for row in acquired["cache_files"]:
        name = row["file"]
        if Path(name).name != name or not name.endswith(".ltar"):
            raise IntegrityError("Invalid acquisition name")
        if file_hash(_safe(root, ".cache/" + MODE + "/mathlib/" + name)) != row["sha256"]:
            raise IntegrityError("Acquired analysis cache changed")
    for p in mathlib.rglob("*"):
        if p.is_file() and not p.is_symlink():
            old = root / ".runtime/mathlib" / p.relative_to(mathlib)
            if old.is_file() and p.stat().st_ino == old.stat().st_ino:
                raise IntegrityError("Analysis tree shares writable hardlinks with legacy")
    module = mathlib / ".lake/build/lib/lean" / (IMPORT.replace(".", "/") + ".olean")
    if not module.is_file():
        raise IntegrityError("Analysis module cache missing")
    snapshot = ContentSnapshot.capture(
        root,
        TREES,
        FILES,
        tuple(legacy.data["host_files"]),
        {"mode": MODE, "import_scope": [IMPORT], "catalog": digest(catalog())},
    )
    raw = canonical(snapshot.data) + b"\n"
    with _safe(root, SNAPSHOT).open("xb") as outgoing:
        outgoing.write(raw)
    with _safe(root, LOCK).open("xb") as outgoing:
        outgoing.write(canonical({"mode": MODE, "sha256": file_hash(root / SNAPSHOT)}) + b"\n")
    legacy.verify()
    return {"mode": MODE, "fingerprint": snapshot.fingerprint, "legacy_preserved": True}


def load(root):
    root = Path(root).resolve()
    lock = strict_json(_safe(root, LOCK).read_text())
    raw = _safe(root, SNAPSHOT).read_bytes()
    if lock != {"mode": MODE, "sha256": digest_bytes(raw)}:
        raise IntegrityError("Analysis snapshot does not match its lock")
    data = strict_json(raw.decode())
    if (
        data.get("schema_version") != "1"
        or set(data["trees"]) != set(TREES)
        or set(data["files"]) != set(FILES)
        or not data["host_files"]
        or data["provenance"]
        != {"mode": MODE, "import_scope": [IMPORT], "catalog": digest(catalog())}
    ):
        raise IntegrityError("Incomplete analysis snapshot scope")
    snapshot = ContentSnapshot(root, data)
    snapshot._seal_hashes = {SNAPSHOT: digest_bytes(raw), LOCK: file_hash(root / LOCK)}
    snapshot.verify()
    return snapshot


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
    if source.count("import Mathlib.Data.Nat.Basic\n") != 1:
        raise ValueError("Trusted import adapter changed")
    return source.replace("import Mathlib.Data.Nat.Basic\n", "import " + IMPORT + "\n")


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
        raise ValueError("Analysis log exceeds sandbox limit")
    return raw.decode("utf-8", errors="replace")


def _save_result(directory, value):
    temporary = directory / (".result-" + uuid.uuid4().hex + ".tmp")
    with temporary.open("xb") as outgoing:
        outgoing.write(canonical(value) + b"\n")
        outgoing.flush()
        os.fsync(outgoing.fileno())
    temporary.replace(directory / "result.json")


def run(root, probe_id, reviewer):
    root = Path(root).resolve()
    probe = next((deepcopy(p) for p in PROBES if p["id"] == probe_id), None)
    if probe is None or not isinstance(reviewer, str) or not 1 <= len(reviewer.strip()) <= 200:
        raise ValueError("Known probe and named local alignment reviewer required")
    with TaskStore(root).worker_lock() as fd:
        started = time.monotonic()
        snapshot = load(root)
        directory = _safe(root, "artifacts/analysis-foundation/runs/analysis-" + uuid.uuid4().hex)
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
                if phase == "replay":
                    raw = extract_export(_read_log(directory / "candidate/stdout.log"))
                    (work / "proof.ndjson").write_bytes(raw)
                    (work / "policy.json").write_bytes(canonical(POLICY) + b"\n")
                snapshot.verify()
                execution = runner._run_worker(
                    work,
                    directory / ("sandbox-" + phase),
                    work / "stdout.log",
                    work / "stderr.log",
                    40 - worker_seconds,
                    phase="LEAN_RUNNING" if phase == "candidate" else "REPLAY_RUNNING",
                    lifecycle_lock_fd=fd,
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
                if result["candidate"]["pid"] == result["replay"]["pid"]:
                    raise ValueError("Replay did not use a distinct process")
                result.update(
                    state="VERIFIED",
                    math_verdict="PROVED",
                    evidence_level="E4_ALIGNED_AND_VERIFIED",
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


def report(root, run_id):
    root = Path(root).resolve()
    import re

    if not isinstance(run_id, str) or not re.fullmatch(r"analysis-[a-f0-9]{32}", run_id):
        raise ValueError("Invalid analysis run ID")
    directory = _safe(root, "artifacts/analysis-foundation/runs/" + run_id)
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
            raise IntegrityError("Analysis request or provider changed")
        expected = {
            "request.json",
            "runtime-snapshot.json",
            "candidate/Candidate.lean",
            "candidate/stdout.log",
            "candidate/stderr.log",
        }
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
                raise IntegrityError("Analysis artifact changed: " + name)
        if (directory / "runtime-snapshot.json").read_bytes() != canonical(snapshot.data) + b"\n":
            raise IntegrityError("Recorded runtime changed")
        for phase in ("candidate", "replay"):
            if result[phase] is not None:
                if (directory / phase / "Candidate.lean").read_text() != render(
                    root, probe, replay=phase == "replay"
                ):
                    raise IntegrityError("Analysis checker changed")
        if result["mode"] != MODE:
            raise IntegrityError("Wrong analysis result mode")
        if result["state"] == "VERIFIED":
            if request["policy"] != POLICY or request["timeout_seconds"] != 40:
                raise IntegrityError("Analysis policy or budget changed")
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
                    or not execution["pid"]
                ):
                    raise IntegrityError("Incomplete independent kernel check")
                checked = audit(
                    _read_log(directory / phase / "stdout.log"), tag, replay=phase == "replay"
                )
                if checked != execution["audit"] or _read_log(directory / phase / "stderr.log"):
                    raise IntegrityError("Kernel audit changed")
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
