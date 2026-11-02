"""Kernel-only candidate checks. Never fabricates a human semantic confirmation."""

from copy import deepcopy
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .domain import canonical, digest
from .formalizer import exact_object, strict_json
from .integrity import ContentSnapshot, file_hash
from .mathlib_lab import audit, render_candidate
from .paper_protocol import complete_statement
from .paper_sandbox import run as run_sandbox
from .replay import extract_export, render_replay
from .document_intake import read_json
from .research_flow import atomic
from .streaming_integrity_v2 import WatchedSnapshot
from .tasks import TaskStore
from .verifier import NativeLeanBackend


def languages():
    from .typed_definitions import catalog as typed_catalog
    from .calculus_language import catalog as calculus_catalog
    from .calculus_research import examples as calculus_examples
    from .proof_logic import catalog as proof_catalog
    from .proof_logic_examples import examples as proof_examples

    return {
        "typed": {"language": typed_catalog(), "example": typed_catalog()["examples"][0]},
        "calculus": {
            "language": calculus_catalog(),
            "example": calculus_examples()[0]["nodes"][-1]["target"],
        },
        "proof2": {
            "language": proof_catalog(),
            "example": proof_examples()[0]["nodes"][-1]["target"],
        },
    }


def compile_candidate(profile, raw, content, timeout=30):
    if profile == "typed":
        from .typed_terms import compile_request
        from .typed_definitions import STRATEGIES
    elif profile == "calculus":
        from .calculus_language import compile_request
    elif profile == "proof2":
        from .proof_logic import compile_request
    else:
        raise ValueError("Unknown whole-paper kernel profile")
    if not isinstance(raw, dict) or set(raw) & {"original", "title", "timeout_seconds"}:
        raise ValueError("Only the host assigns the complete original statement and budget")
    request = {
        **deepcopy(raw),
        "original": complete_statement(content),
        "title": content["title"][:160],
        "timeout_seconds": timeout,
    }
    compiled = compile_request(request)
    if profile == "typed":
        strategy = request["strategy"]
        if strategy not in STRATEGIES:
            raise ValueError("Only fixed typed proof strategies are supported")
        compiled["proof_source"] = STRATEGIES[strategy]["proof_source"]
    return {
        "profile": profile,
        **compiled,
        "candidate_fingerprint": digest({"profile": profile, "request": request}),
    }


def _identity(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _freeze(pid):
    """Wait for SIGSTOP to take effect before walking a process's children."""
    try:
        os.kill(pid, signal.SIGSTOP)
        end = time.monotonic() + 0.5
        while time.monotonic() < end:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
            if fields[0] in ("T", "t", "Z", "X"):
                return
            time.sleep(0.002)
    except (OSError, IndexError):
        pass


def terminate(process, directory):
    """Freeze the owned parent before enumerating its separate sandbox sessions."""
    root_pid = process.pid
    alive = process.poll() is None
    if alive:
        _freeze(root_pid)
    owned = []
    path = directory / "worker-pids.json"
    if path.exists() and not path.is_symlink():
        try:
            owned.extend(strict_json(path.read_text()))
        except (OSError, ValueError, TypeError):
            pass
    todo = [root_pid] if alive else []
    # The supervisor may already have exited. Recorded sandbox roots retain
    # both leases, and their own descendants must still be collected.
    for item in list(owned):
        if (
            isinstance(item, dict)
            and type(item.get("pid")) is int
            and item["pid"] > 1
            and isinstance(item.get("start"), str)
            and _identity(item["pid"]) == item["start"]
        ):
            _freeze(item["pid"])
            todo.append(item["pid"])
    seen = set()
    while todo:
        pid = todo.pop()
        if pid in seen:
            continue
        seen.add(pid)
        try:
            # A subprocess can fork from a non-main thread.
            children = [
                child
                for task in Path(f"/proc/{pid}/task").iterdir()
                for child in (task / "children").read_text().split()
            ]
            for child in children:
                child = int(child)
                stamp = _identity(child)
                if stamp is not None:
                    owned.append({"pid": child, "start": stamp})
                    _freeze(child)
                    todo.append(child)
        except (OSError, ValueError):
            pass
    for item in reversed(owned):
        if (
            not isinstance(item, dict)
            or type(item.get("pid")) is not int
            or item["pid"] <= 1
            or not isinstance(item.get("start"), str)
        ):
            continue
        pid = item["pid"]
        if _identity(pid) == item["start"]:
            try:
                if os.getpgid(pid) == pid:
                    os.killpg(pid, signal.SIGKILL)
                else:
                    os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    if alive:
        try:
            os.killpg(root_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()
    # SIGKILL delivery is asynchronous. A reaped group leader does not imply
    # its descendants have closed their inherited queue/checker descriptors.
    end = time.monotonic() + 3
    while True:
        live = []
        for item in owned:
            if (
                not isinstance(item, dict)
                or type(item.get("pid")) is not int
                or not isinstance(item.get("start"), str)
            ):
                continue
            if _identity(item["pid"]) == item["start"]:
                try:
                    state = (
                        Path(f"/proc/{item['pid']}/stat").read_text().rsplit(") ", 1)[1].split()[0]
                    )
                    if state not in ("Z", "X"):
                        live.append(item["pid"])
                except (OSError, IndexError):
                    pass
        if not live:
            break
        if time.monotonic() >= end:
            raise OSError("Owned kernel descendants have not finished exiting")
        time.sleep(0.01)


def execute(root, directory, candidate, *, cancel_requested, lifecycle_lock_fd, wall_seconds=600):
    root, directory = Path(root).resolve(), Path(directory).absolute()
    if directory.resolve() != directory or not directory.is_relative_to(root / "artifacts"):
        raise ValueError("Paper candidate checks must stay below the project's artifacts")
    directory.mkdir(parents=True, exist_ok=False)
    payload = {
        "root": str(root),
        "directory": str(directory),
        "candidate": candidate,
        "wall_seconds": wall_seconds,
        "lifecycle_fd": lifecycle_lock_fd,
    }
    process = subprocess.Popen(
        [sys.executable, "-I", str(Path(__file__).with_name("paper_kernel_worker.py"))],
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        pass_fds=(lifecycle_lock_fd,),
    )
    started, outgoing = time.monotonic(), canonical(payload)
    try:
        while True:
            if cancel_requested():
                raise InterruptedError("Whole-paper candidate checking cancelled")
            remaining = wall_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("Whole-paper kernel child deadline exceeded")
            try:
                raw, _ = process.communicate(input=outgoing, timeout=min(0.1, remaining))
                break
            except subprocess.TimeoutExpired:
                outgoing = None
        if process.returncode or len(raw) > 131072:
            raise OSError("Kernel-only child interrupted or returned an invalid result")
        result = strict_json(raw.decode())
        validate_receipt(directory, candidate, result)
        return result
    finally:
        try:
            terminate(process, directory)
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


def validate_receipt(directory, candidate, result):
    """Recheck recorded bytes and exact-target audits; historical, never a fresh proof."""
    if result["candidate_fingerprint"] != candidate["candidate_fingerprint"]:
        raise ValueError("Kernel child checked a different candidate")
    if (
        result["math_verdict"] != "UNKNOWN"
        or result["evidence_level"] != "E0"
        or result.get("human_confirmation") is not None
    ):
        raise ValueError("A kernel-only result cannot confer original-source authority")
    seconds = result["worker_seconds"]
    if type(seconds) not in (float, int) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError("Invalid kernel time accounting")
    for name, expected in result["files"].items():
        p = directory / name
        if p.resolve() != p or not p.is_relative_to(directory) or file_hash(p) != expected:
            raise ValueError("Kernel candidate artifact changed before collection")
    if result["kernel_status"] != "KERNEL_CHECKED":
        return
    phases = result["phases"]
    if (
        set(phases) != {"candidate", "replay"}
        or phases["candidate"]["pid"] == phases["replay"]["pid"]
    ):
        raise ValueError("Independent candidate/replay processes are required")
    plan = read_json(directory / "compiled-plan.json")
    if (
        plan["request"] != candidate["request"]
        or plan["formal_statement"] != candidate["formal_statement"]
        or plan["proof_source"] != candidate["proof_source"]
    ):
        raise ValueError("Recorded check plan differs from the fixed candidate")
    required = {
        "compiled-plan.json",
        "runtime-snapshot.json",
        "candidate/Candidate.lean",
        "replay/Candidate.lean",
        "replay/proof.ndjson",
        "replay/policy.json",
        "scan-candidate.json",
        "scan-replay.json",
        "scan-final.json",
    }
    for phase, tag in (("candidate", "MRA_AUDIT:"), ("replay", "MRA_REPLAY:")):
        required.update({phase + "/stdout.log", phase + "/stderr.log"})
        outcome = phases[phase]
        if (
            outcome["run_status"] != "COMPLETED"
            or outcome["returncode"] != 0
            or type(outcome["pid"]) is not int
            or outcome["pid"] <= 1
        ):
            raise ValueError("Both real kernel processes must finish successfully")
        if (directory / phase / "stderr.log").read_bytes():
            raise ValueError("Kernel check has unaccounted stderr")
        checked = audit(
            (directory / phase / "stdout.log").read_text(), tag, replay=phase == "replay"
        )
        if outcome.get("audit") != checked:
            raise ValueError("Recorded kernel audit changed")
    if not required <= set(result["files"]):
        raise ValueError("Kernel receipt omits required evidence files")
    if (directory / "replay/proof.ndjson").read_bytes() != extract_export(
        (directory / "candidate/stdout.log").read_text()
    ):
        raise ValueError("Replay data differs from candidate proof export")


def check(root, directory, candidate, lifecycle_fd):
    """Runs actual fixed compilers, watched byte checks, sandbox and data replay."""
    profile = candidate["profile"]
    if profile == "typed":
        from .typed_definitions import prepare
        from .mathlib_lab import POLICY

        plan = prepare(root, candidate["request"])
        snapshot = ContentSnapshot.load_runtime(root)
        runner = object.__new__(NativeLeanBackend)
        runner.root, runner.runtime, runner.mathlib = (
            root,
            root / ".runtime/lean-4.35.0-linux",
            root / ".runtime/mathlib",
        )
        config = strict_json((root / "lean_workspace/environment.json").read_text())
        release = strict_json((root / ".runtime/lean-release.json").read_text())
        if (
            release["digest"] != "sha256:" + config["lean_archive_sha256"]
            or (runner.mathlib / ".mra-source-pin").read_text().strip() != config["mathlib_commit"]
        ):
            raise ValueError("Runtime release or Mathlib source pin changed")

        def renderer(replay):
            return (
                render_replay(root, plan["formal_statement"])
                if replay
                else render_candidate(root, plan)
            )
    else:
        from . import calculus_foundation_v2 as foundation

        if profile == "calculus":
            from .calculus_lab import prepare, render, POLICY
        elif profile == "proof2":
            from .proof_logic_lab import prepare, render, POLICY
        else:
            raise ValueError("Unknown controlled kernel profile")
        plan = prepare(root, candidate["request"])
        snapshot, runner = foundation.load(root), foundation._Runner(root)

        def renderer(replay):
            return render(root, plan, replay=replay)

    if (
        plan["formal_statement"] != candidate["formal_statement"]
        or plan["proof_source"] != candidate["proof_source"]
    ):
        raise ValueError("Compiled candidate changed before checking")
    watched = WatchedSnapshot(root, snapshot.data)
    watched._seal_hashes = deepcopy(snapshot._seal_hashes)
    result = {
        "candidate_fingerprint": candidate["candidate_fingerprint"],
        "kernel_status": "UNKNOWN",
        "phases": {},
        "worker_seconds": 0,
        "diagnostic": None,
        "runtime_fingerprint": snapshot.fingerprint,
        "source_manifest": plan["source_manifest"],
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "semantic_review": "MODEL_AUDIT_ONLY",
        "human_confirmation": None,
    }
    atomic(directory / "compiled-plan.json", plan)
    atomic(
        directory / "runtime-snapshot.json",
        {
            "fingerprint": snapshot.fingerprint,
            "sealed_manifests": snapshot._seal_hashes,
            "runtime_trees": sorted(snapshot.data["trees"]),
            "scope": "Reference to full sealed manifest, not a copy or a reseal",
        },
    )
    pids = []

    def checkpoint(_phase, _path):
        children = Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children").read_text().split()
        for pid in children:
            stamp = _identity(int(pid))
            if stamp:
                pids.append({"pid": int(pid), "start": stamp})
        # A plain immutable-owned control file, not mathematical evidence.
        pending = directory / ".worker-pids.pending"
        pending.write_bytes(canonical(pids))
        pending.replace(directory / "worker-pids.json")

    with TaskStore(root).worker_lock() as checker_fd:
        for phase, tag in (("candidate", "MRA_AUDIT:"), ("replay", "MRA_REPLAY:")):
            watched.verify()
            atomic(directory / ("scan-" + phase + ".json"), watched.last_scan)
            work = directory / phase
            work.mkdir()
            if phase == "replay":
                (work / "proof.ndjson").write_bytes(
                    extract_export((directory / "candidate/stdout.log").read_text())
                )
                (work / "policy.json").write_bytes(canonical(POLICY) + b"\n")
            (work / "Candidate.lean").write_text(renderer(phase == "replay"))
            outcome = run_sandbox(
                runner,
                work,
                directory / ("sandbox-" + phase),
                work / "stdout.log",
                work / "stderr.log",
                candidate["request"]["timeout_seconds"] - result["worker_seconds"],
                lifecycle_fd,
                checker_fd,
                checkpoint,
            )
            result["phases"][phase] = outcome
            result["worker_seconds"] += outcome["elapsed_seconds"]
            atomic(directory / "partial.json", result)
            output, stderr = (work / "stdout.log").read_text(), (work / "stderr.log").read_text()
            if outcome["run_status"] != "COMPLETED" or outcome["returncode"] != 0 or stderr:
                result["diagnostic"] = (stderr or output.split("MRA_EXPORT_BEGIN", 1)[0])[-6000:]
                break
            outcome["audit"] = audit(output, tag, replay=phase == "replay")
            atomic(directory / "partial.json", result)
        else:
            watched.verify()
            atomic(directory / "scan-final.json", watched.last_scan)
            if result["phases"]["candidate"]["pid"] == result["phases"]["replay"]["pid"]:
                raise ValueError("Independent replay process missing")
            result["kernel_status"] = "KERNEL_CHECKED"
    result["files"] = {
        str(p.relative_to(directory)): file_hash(p)
        for p in directory.rglob("*")
        if p.is_file()
        and not any(part.startswith("sandbox-") for part in p.relative_to(directory).parts)
    }
    return result


def worker_main():
    try:
        raw = sys.stdin.buffer.read(1048577)
        if len(raw) > 1048576:
            raise ValueError("Oversized paper kernel request")
        data = strict_json(raw.decode())
        exact_object(
            data,
            {"root", "directory", "candidate", "wall_seconds", "lifecycle_fd"},
            "Fixed paper kernel request",
        )
        root, directory = Path(data["root"]).resolve(), Path(data["directory"]).absolute()
        if (
            directory.resolve() != directory
            or not directory.is_relative_to(root / "artifacts")
            or not 0 < data["wall_seconds"] <= 600
        ):
            raise ValueError("Invalid paper kernel location or deadline")
        signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
        signal.setitimer(signal.ITIMER_REAL, data["wall_seconds"])
        result = check(root, directory, data["candidate"], data["lifecycle_fd"])
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        result = {
            "candidate_fingerprint": data["candidate"]["candidate_fingerprint"]
            if "data" in locals()
            else None,
            "kernel_status": "UNAVAILABLE",
            "phases": {},
            "worker_seconds": 0,
            "diagnostic": type(error).__name__ + ": " + str(error)[:2000],
            "files": {},
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
            "human_confirmation": None,
        }
        if "directory" in locals() and (directory / "partial.json").is_file():
            partial = read_json(directory / "partial.json")
            result.update(phases=partial["phases"], worker_seconds=partial["worker_seconds"])
        # Costs are reserved by the supervisor before launch, including unknown crash costs.
    sys.stdout.buffer.write(canonical(result))
