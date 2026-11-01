"""Isolated derivative/integral probes with full-target kernel checks and data replay."""

from copy import deepcopy
import math
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

MODE = "calculus-foundation-v1"
POLICY = {
    "mode": MODE,
    "allowed_axioms": ["propext", "Classical.choice", "Quot.sound"],
    "forbidden_axioms": ["sorryAx", "Lean.ofReduceBool"],
    "require_exact_target": True,
    "require_independent_replay": True,
}
BASE = ".runtime/" + MODE
IMPORTS = (
    "Mathlib.Analysis.Calculus.Deriv.Basic",
    "Mathlib.Analysis.Calculus.Deriv.Add",
    "Mathlib.MeasureTheory.Integral.IntervalIntegral.Basic",
)
SNAPSHOT = BASE + "/snapshot.json"
LOCK = BASE + "/lock.json"
TREES = (".runtime/lean-4.35.0-linux", BASE + "/mathlib")
FILES = (
    "mra/calculus_foundation.py",
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
PROBES = [
    {
        "id": "derivative-id",
        "original": "实恒等函数在任意实数x处有导数1；断言HasDerivAt。",
        "statement": "∀ x : ℝ, HasDerivAt (fun t : ℝ => t) 1 x",
        "proof": "by intro x; exact hasDerivAt_id x",
        "expected": "VERIFIED",
        "source": "Deriv.Basic hasDerivAt_id",
    },
    {
        "id": "derivative-constant",
        "original": "任意实常函数在任意实数x处有导数0。",
        "statement": "∀ c x : ℝ, HasDerivAt (fun _ : ℝ => c) 0 x",
        "proof": "by intro c x; exact hasDerivAt_const x c",
        "expected": "VERIFIED",
        "source": "Deriv.Basic hasDerivAt_const",
    },
    {
        "id": "derivative-add",
        "original": "f,g在同一点分别有明确导数df,dg时，和的导数为df+dg；两个原条件保留。",
        "statement": "∀ (f g : ℝ → ℝ) (df dg x : ℝ), HasDerivAt f df x → HasDerivAt g dg x → HasDerivAt (fun t => f t + g t) (df + dg) x",
        "proof": "by intro f g df dg x hf hg; exact hf.add hg",
        "expected": "VERIFIED",
        "source": "Deriv.Add HasDerivAt.add",
    },
    {
        "id": "interval-constant",
        "original": "Lebesgue体积下，有向区间常数积分等于(b-a)c；没有添加a≤b条件。",
        "statement": "∀ a b c : ℝ, (∫ _ in a..b, c) = (b - a) * c",
        "proof": "by intro a b c; simpa only [smul_eq_mul] using (intervalIntegral.integral_const (a := a) (b := b) c)",
        "expected": "VERIFIED",
        "source": "IntervalIntegral.Basic integral_const",
    },
    {
        "id": "interval-add",
        "original": "体积下两个实函数在无序端点间分别区间可积时，有向区间积分可加。",
        "statement": "∀ (f g : ℝ → ℝ) (a b : ℝ), IntervalIntegrable f volume a b → IntervalIntegrable g volume a b → (∫ t in a..b, f t + g t) = (∫ t in a..b, f t) + (∫ t in a..b, g t)",
        "proof": "by intro f g a b hf hg; exact intervalIntegral.integral_add hf hg",
        "expected": "VERIFIED",
        "source": "IntervalIntegral.Basic integral_add",
    },
    {
        "id": "interval-reversal",
        "original": "任意实函数及实端点，反向区间积分是原区间积分的相反数；算子按Mathlib总定义使用。",
        "statement": "∀ (f : ℝ → ℝ) (a b : ℝ), (∫ t in b..a, f t) = -(∫ t in a..b, f t)",
        "proof": "by intro f a b; exact intervalIntegral.integral_symm a b",
        "expected": "VERIFIED",
        "source": "IntervalIntegral.Basic integral_symm",
    },
    {
        "id": "zero-measure-constant",
        "original": "零测度下常函数的有向区间积分为0，不能套用Lebesgue长度公式。",
        "statement": "∀ a b c : ℝ, (∫ _ in a..b, c ∂(0 : Measure ℝ)) = 0",
        "proof": "by intro a b c; simp [intervalIntegral]",
        "expected": "VERIFIED",
        "source": "intervalIntegral definition and Bochner integral_zero_measure",
    },
    {
        "id": "general-measure-constant",
        "original": "一般测度的常数区间积分由两个Ioc测度实值之差给出；μ.real是toReal语义，不是自动假设有限。",
        "statement": "∀ (μ : Measure ℝ) (a b c : ℝ), (∫ _ in a..b, c ∂μ) = (μ.real (Set.Ioc a b) - μ.real (Set.Ioc b a)) * c",
        "proof": "by intro μ a b c; simpa only [smul_eq_mul] using (intervalIntegral.integral_const' (μ := μ) (a := a) (b := b) c)",
        "expected": "VERIFIED",
        "source": "IntervalIntegral.Basic integral_const'",
    },
    {
        "id": "ae-norm-bound",
        "original": "体积下仅假设无序半开区间上几乎处处范数≤C，得到区间积分范数界；不把a.e.提升为逐点，也不自动宣称可积。",
        "statement": "∀ (f : ℝ → ℝ) (a b C : ℝ), (∀ᵐ t ∂(volume : Measure ℝ), t ∈ Set.uIoc a b → ‖f t‖ ≤ C) → ‖∫ t in a..b, f t‖ ≤ C * |b - a|",
        "proof": "by intro f a b C h; exact intervalIntegral.norm_integral_le_of_norm_le_const_ae h",
        "expected": "VERIFIED",
        "source": "IntervalIntegral.Basic norm_integral_le_of_norm_le_const_ae",
    },
    {
        "id": "missing-derivative-conditions",
        "original": "失败对照：任意f,g及df,dg，遗漏两个HasDerivAt条件仍套用加法规则。",
        "statement": "∀ (f g : ℝ → ℝ) (df dg x : ℝ), HasDerivAt (fun t => f t + g t) (df + dg) x",
        "proof": "by intro f g df dg x; apply HasDerivAt.add",
        "expected": "REJECTED",
        "source": "derivative-add omission",
    },
    {
        "id": "missing-integrability",
        "original": "失败对照：去掉两个IntervalIntegrable条件仍套用积分加法。",
        "statement": "∀ (f g : ℝ → ℝ) (a b : ℝ), (∫ t in a..b, f t + g t) = (∫ t in a..b, f t) + (∫ t in a..b, g t)",
        "proof": "by intro f g a b; apply intervalIntegral.integral_add",
        "expected": "REJECTED",
        "source": "interval-add omission",
    },
    {
        "id": "wrong-orientation",
        "original": "失败对照：把常数积分的b-a错误替为a-b。",
        "statement": "∀ a b c : ℝ, (∫ _ in a..b, c) = (a - b) * c",
        "proof": "by intro a b c; simpa only [smul_eq_mul] using (intervalIntegral.integral_const (a := a) (b := b) c)",
        "expected": "REJECTED",
        "source": "interval-constant sign mutation",
    },
    {
        "id": "wrong-measure",
        "original": "失败对照：对任意测度μ错误使用Lebesgue长度公式。",
        "statement": "∀ (μ : Measure ℝ) (a b c : ℝ), (∫ _ in a..b, c ∂μ) = (b - a) * c",
        "proof": "by intro μ a b c; simpa only [smul_eq_mul] using (intervalIntegral.integral_const' (μ := μ) (a := a) (b := b) c)",
        "expected": "REJECTED",
        "source": "general-measure-constant mutation",
    },
    {
        "id": "ae-is-not-pointwise",
        "original": "失败对照：任意测度下，几乎处处函数为0不能直接提升为每点为0。",
        "statement": "∀ (f : ℝ → ℝ) (μ : Measure ℝ), (∀ᵐ t ∂μ, f t = 0) → ∀ t, f t = 0",
        "proof": "by intro f μ h; exact h",
        "expected": "REJECTED",
        "source": "a.e. versus pointwise mutation",
    },
    {
        "id": "unused-axiom",
        "original": "即使导数目标正确，也拒绝未使用的局部公理。",
        "statement": "∀ x : ℝ, HasDerivAt (fun t : ℝ => t) 1 x",
        "proof": "by intro x; exact hasDerivAt_id x",
        "expected": "POLICY_BLOCKED",
        "source": "local axiom control",
        "extra": "axiom unapproved : False",
    },
    {
        "id": "placeholder",
        "original": "导数目标中的sorry占位必须拒绝。",
        "statement": "∀ x : ℝ, HasDerivAt (fun t : ℝ => t) 1 x",
        "proof": "by sorry",
        "expected": "POLICY_BLOCKED",
        "source": "sorryAx control",
    },
]


def catalog():
    return {
        "mode": MODE,
        "imports": list(IMPORTS),
        "probes": deepcopy(PROBES),
        "policy": deepcopy(POLICY),
    }


def _safe(root, relative):
    path = root / relative
    if path.resolve() != path.absolute():
        raise IntegrityError("Calculus path was redirected: " + relative)
    return path


def seal(root):
    root = Path(root).resolve()
    if (root / LOCK).exists() or (root / SNAPSHOT).exists():
        raise IntegrityError("Calculus seal exists; no implicit replacement or migration")
    legacy = ContentSnapshot.load_runtime(root)
    legacy.verify()
    adoption = strict_json(_safe(root, BASE + "/adoption.json").read_text())
    acquired = strict_json(_safe(root, BASE + "/acquisition.json").read_text())
    if (
        adoption["lean"] != "4.35.0"
        or adoption["mathlib"] != "56a005855ea17e0b0958f73ec801ae140b0a1679"
        or adoption["import_scope"] != list(IMPORTS)
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
            for prior in (".runtime/mathlib", ".runtime/analysis-foundation-v1/mathlib"):
                old = root / prior / p.relative_to(mathlib)
                if old.is_file() and p.stat().st_ino == old.stat().st_ino:
                    raise IntegrityError("Calculus tree shares writable hardlinks with prior")
    for name in IMPORTS:
        module = mathlib / ".lake/build/lib/lean" / (name.replace(".", "/") + ".olean")
        if not module.is_file():
            raise IntegrityError("Calculus module cache missing: " + name)
    snapshot = ContentSnapshot.capture(
        root,
        TREES,
        FILES,
        tuple(legacy.data["host_files"]),
        {"mode": MODE, "import_scope": list(IMPORTS), "catalog": digest(catalog())},
    )
    raw = canonical(snapshot.data) + b"\n"
    with _safe(root, SNAPSHOT).open("xb") as outgoing:
        outgoing.write(raw)
    with _safe(root, LOCK).open("xb") as outgoing:
        outgoing.write(canonical({"mode": MODE, "sha256": file_hash(root / SNAPSHOT)}) + b"\n")
    legacy.verify()
    return {"mode": MODE, "fingerprint": snapshot.fingerprint, "legacy_preserved": True}


def _load(root):
    root = Path(root).resolve()
    lock = strict_json(_safe(root, LOCK).read_text())
    raw = _safe(root, SNAPSHOT).read_bytes()
    if lock != {"mode": MODE, "sha256": digest_bytes(raw)}:
        raise IntegrityError("Calculus snapshot does not match its lock")
    data = strict_json(raw.decode())
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("trees"), dict)
        or not isinstance(data.get("files"), dict)
        or not isinstance(data.get("host_files"), dict)
        or data.get("schema_version") != "1"
        or set(data["trees"]) != set(TREES)
        or set(data["files"]) != set(FILES)
        or not data["host_files"]
        or data["provenance"]
        != {"mode": MODE, "import_scope": list(IMPORTS), "catalog": digest(catalog())}
    ):
        raise IntegrityError("Incomplete analysis snapshot scope")
    snapshot = ContentSnapshot(root, data)
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
    if source.count("import Mathlib.Data.Nat.Basic\n") != 1:
        raise ValueError("Trusted import adapter changed")
    return source.replace(
        "import Mathlib.Data.Nat.Basic\n",
        "".join("import " + name + "\n" for name in IMPORTS)
        + "open MeasureTheory\nopen scoped Interval Filter\n",
    )


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


def run(root, probe_id, reviewer):
    root = Path(root).resolve()
    probe = next((deepcopy(p) for p in PROBES if p["id"] == probe_id), None)
    if probe is None or not isinstance(reviewer, str) or not 1 <= len(reviewer.strip()) <= 200:
        raise ValueError("Known probe and named local alignment reviewer required")
    with TaskStore(root).worker_lock() as fd:
        started = time.monotonic()
        snapshot = load(root)
        directory = _safe(root, "artifacts/calculus-foundation/runs/calculus-" + uuid.uuid4().hex)
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

    if not isinstance(run_id, str) or not re.fullmatch(r"calculus-[a-f0-9]{32}", run_id):
        raise ValueError("Invalid analysis run ID")
    directory = _safe(root, "artifacts/calculus-foundation/runs/" + run_id)
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
            raise IntegrityError("Calculus request or provider changed")
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
