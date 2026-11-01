"""Isolated Lean checks of repository references and fixed-target kernel certificates."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import signal
import subprocess
import time
from typing import Callable, Protocol
import uuid

from .domain import CheckReceipt, Claim, EnvironmentPin, Formalization, canonical, digest
from .datasets import repository_cases
from .integrity import ContentSnapshot, IntegrityError, file_hash
from .replay import BASE_FILES, REPLAY_FILES, extract_export, render_replay
from .nat_ideas import (
    STRATEGIES,
    NatTarget,
    IntTarget,
    INT_MODE,
    bundle_file,
    bundle_digest_key,
    target_from_dict,
    target_mode,
    proof_source,
    search_bundle,
    render_checker,
)
from .certificates import (
    MODE as CERTIFICATE_MODE,
    REFERENCE_MODE,
    CERTIFICATE_FILE,
    parse_certificate,
    render_certificate_checker,
)


class LeanExecutionBackend(Protocol):
    environment: EnvironmentPin

    def check(self, case: dict, claim: Claim, formalization: Formalization) -> CheckReceipt: ...
    def authentic(self, receipt: CheckReceipt) -> bool: ...
    def verify_environment(self) -> None: ...


def render_source(case: dict, expected_statement: str) -> str:
    actual_statement = case.get("checked_statement_override", expected_statement)
    return f"""import Mathlib.Data.Nat.Basic
import Lean
import LeanExport.Basic

set_option autoImplicit false
set_option Elab.async false
set_option maxHeartbeats 200000

namespace MraCandidate
{case["extra_declarations"]}
theorem result : {actual_statement} := {case["proof"]}
end MraCandidate

open Lean Elab Command in
run_cmd do
  let env ← getEnv
  let some info := env.find? `MraCandidate.result
    | throwError "MRA_MISSING_TARGET"
  unless (match info with | .thmInfo _ => true | _ => false) do
    throwError "MRA_NOT_A_THEOREM"
  let actualType ← liftTermElabM do
    let expected ← Lean.Elab.Term.elabType (← `(term| {expected_statement}))
    unless ← Lean.Meta.isDefEq info.type expected do
      throwError "MRA_TARGET_MISMATCH"
    return (← Lean.Meta.ppExpr info.type).pretty
  let axioms ← Lean.collectAxioms `MraCandidate.result
  let locals := env.checked.get.constants.foldStage2
    (fun names name _ => names.push name) (#[] : Array Name)
  let localAxioms := env.checked.get.constants.foldStage2
    (fun names name ci => match ci with
      | .axiomInfo _ => names.push name.toString
      | _ => names) (#[] : Array String)
  let mut allLocalAxioms : Array Name := #[]
  for name in locals do
    for ax in ← Lean.collectAxioms name do
      if !allLocalAxioms.contains ax then
        allLocalAxioms := allLocalAxioms.push ax
  let report := Json.mkObj [
    ("target", toJson "MraCandidate.result"),
    ("declaration_kind", toJson "theorem"),
    ("type_matches", toJson true),
    ("actual_type", toJson actualType),
    ("local_axioms", toJson localAxioms),
    ("local_dependency_axioms", toJson (allLocalAxioms.map Name.toString)),
    ("axioms", toJson (axioms.map Name.toString))]
  logInfo m!"MRA_AUDIT:{{report.compress}}"
  liftIO do
    IO.println "MRA_EXPORT_BEGIN"
    LeanExport.dumpEnv env (some locals.toList)
    IO.println "MRA_EXPORT_END"
"""


class NativeLeanBackend:
    def __init__(self, root: Path, timeout_seconds: float = 30.0) -> None:
        self.root = root.resolve()
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or timeout_seconds > 120:
            raise ValueError("P0 timeout must be in (0, 120] seconds")
        self.timeout_seconds = timeout_seconds
        self.runtime = self.root / ".runtime/lean-4.35.0-linux"
        self.mathlib = self.root / ".runtime/mathlib"
        self._key = secrets.token_bytes(32)
        # Validate actual files before executing even `lean --version`.
        self._snapshot = ContentSnapshot.load_runtime(self.root)
        self.verify_environment()
        self.policy = json.loads((self.root / "lean_workspace/evidence-policy.json").read_text())
        config = json.loads((self.root / "lean_workspace/environment.json").read_text())
        release = json.loads((self.root / ".runtime/lean-release.json").read_text())
        if release["digest"] != "sha256:" + config["lean_archive_sha256"]:
            raise RuntimeError("Runtime pin does not match official release receipt")
        version = subprocess.check_output(
            [str(self.runtime / "bin/lean"), "--version"],
            text=True,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            timeout=10,
        ).strip()
        if f"version {config['lean_version']}," not in version:
            raise RuntimeError("Wrong Lean version")
        revision = (self.mathlib / ".mra-source-pin").read_text().strip()
        if revision != config["mathlib_commit"]:
            raise RuntimeError("Wrong mathlib commit")
        if not (self.mathlib / ".lake/build/lib/lean/Mathlib/Data/Nat/Basic.olean").exists():
            raise RuntimeError("Mathlib cache missing; run ops/scripts/prepare_mathlib_cache.py")
        self.environment = EnvironmentPin(
            config["lean_version"],
            revision,
            self._snapshot.fingerprint,
            file_hash(self.mathlib / "lake-manifest.json"),
        )
        self._cases = repository_cases(self.root)
        self._worker_hash = file_hash(self.root / "ops/scripts/sandbox_worker.py")

    def verify_environment(self) -> None:
        self._snapshot.verify()

    def authentic(self, receipt: CheckReceipt) -> bool:
        expected = hmac.new(self._key, canonical(receipt.payload), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, receipt.signature)

    def _run_worker(
        self,
        work: Path,
        sandbox: Path,
        stdout_path: Path,
        stderr_path: Path,
        budget: float,
        *,
        phase: str,
        cancel_requested: Callable[[], bool] | None = None,
        checkpoint: Callable[[str, str], None] | None = None,
        lifecycle_lock_fd: int | None = None,
    ) -> dict:
        sandbox.mkdir()
        command = [
            "/usr/bin/unshare",
            "--user",
            "--map-root-user",
            "--mount",
            "--net",
            "--pid",
            "--fork",
            "--kill-child",
            "/usr/bin/python3",
            str(self.root / "ops/scripts/sandbox_worker.py"),
            str(self.runtime),
            str(self.mathlib),
            str(work),
            str(sandbox),
            str(max(1, math.ceil(budget))),
            "16384",
        ]
        started = time.monotonic()
        status, returncode, pid = "COMPLETED", None, None
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            if cancel_requested is not None and cancel_requested():
                status = "CANCELLED"
            elif budget <= 0:
                status = "TIMEOUT"
            else:
                process = subprocess.Popen(
                    command,
                    cwd=work,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                    env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                    pass_fds=() if lifecycle_lock_fd is None else (lifecycle_lock_fd,),
                )
                pid = process.pid
                try:
                    deadline = time.monotonic() + budget
                    if checkpoint is not None:
                        checkpoint(
                            phase,
                            str(stdout_path.parent if phase == "LEAN_RUNNING" else work.parent),
                        )
                    while True:
                        if cancel_requested is not None and cancel_requested():
                            status = "CANCELLED"
                            break
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            status = "TIMEOUT"
                            break
                        try:
                            returncode = process.wait(timeout=min(0.1, remaining))
                            break
                        except subprocess.TimeoutExpired:
                            continue
                finally:
                    if process.poll() is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait()
        return {
            "run_status": status,
            "returncode": returncode,
            "pid": pid,
            "elapsed_seconds": time.monotonic() - started,
        }

    def check(
        self,
        case: dict,
        claim: Claim,
        formalization: Formalization,
        *,
        cancel_requested: Callable[[], bool] | None = None,
        checkpoint: Callable[[str, str], None] | None = None,
        lifecycle_lock_fd: int | None = None,
    ) -> CheckReceipt:
        return self._check(
            case,
            claim,
            formalization,
            cancel_requested=cancel_requested,
            checkpoint=checkpoint,
            lifecycle_lock_fd=lifecycle_lock_fd,
        )

    def check_certificate(
        self,
        case: dict,
        claim: Claim,
        formalization: Formalization,
        raw: bytes,
        *,
        cancel_requested: Callable[[], bool] | None = None,
        checkpoint: Callable[[str, str], None] | None = None,
        lifecycle_lock_fd: int | None = None,
    ) -> CheckReceipt:
        if type(raw) is not bytes:
            raise ValueError("Certificate input must be bytes")
        return self._check(
            case,
            claim,
            formalization,
            certificate_raw=raw,
            cancel_requested=cancel_requested,
            checkpoint=checkpoint,
            lifecycle_lock_fd=lifecycle_lock_fd,
        )

    def check_nat_plan(self, target: NatTarget, plan: dict, **options) -> CheckReceipt:
        # Reconstruct the immutable data contract rather than trusting a caller's
        # Lean string, case dictionary or claimed target fingerprint.
        target = NatTarget.from_dict(target.to_dict())
        bundle = search_bundle(target, plan)
        claim, formalization = target.contract(bundle["plan"]["direction"])
        return self._check({}, claim, formalization, nat_bundle=bundle, **options)

    def check_int_plan(self, target: IntTarget, plan: dict, **options) -> CheckReceipt:
        target = IntTarget.from_dict(target.to_dict())
        bundle = search_bundle(target, plan)
        claim, formalization = target.contract(bundle["plan"]["direction"])
        return self._check({}, claim, formalization, nat_bundle=bundle, **options)

    def _check(
        self,
        case: dict,
        claim: Claim,
        formalization: Formalization,
        *,
        certificate_raw: bytes | None = None,
        nat_bundle: dict | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        checkpoint: Callable[[str, str], None] | None = None,
        lifecycle_lock_fd: int | None = None,
    ) -> CheckReceipt:
        if nat_bundle is not None:
            target = target_from_dict(nat_bundle["target"])
            plan = search_bundle(target, nat_bundle["plan"])["plan"]
            mode = target_mode(target)
            prefix = "int" if mode == INT_MODE else "nat"
            if (
                certificate_raw is not None
                or nat_bundle["mode"] != mode
                or self.policy.get(prefix + "_search_mode") != mode
                or self.policy.get(prefix + "_formula_schema") != target.TARGET_SCHEMA
                or self.policy.get(prefix + "_strategies") != list(STRATEGIES)
                or (claim, formalization) != target.contract(plan["direction"])
            ):
                raise ValueError(
                    "Natural arithmetic target/strategy is not approved by the sealed policy"
                )
            case = {
                "id": claim.id,
                "original_statement": claim.original_statement,
                "formal_statement": claim.formal_statement,
                "direction": plan["direction"],
                "extra_declarations": "",
                "proof": proof_source(plan, target),
            }
        elif case != self._cases.get(case.get("id")):
            raise ValueError(
                "P0 accepts only unchanged repository-owned fixtures; arbitrary Lean is not an input API"
            )
        expected_claim = (
            target.contract(plan["direction"])[0]
            if nat_bundle is not None
            else Claim(
                case["id"],
                1,
                case["original_statement"],
                case["formal_statement"],
                tuple(case.get("annotation", {}).get("assumptions", ())),
            )
        )
        expected_statement = (
            expected_claim.formal_statement
            if case["direction"] == "prove"
            else f"¬ ({expected_claim.formal_statement})"
        )
        expected_formalization = Formalization(
            expected_claim.fingerprint, expected_statement, case["direction"]
        )
        if claim != expected_claim or formalization != expected_formalization:
            raise ValueError("P0 claim and formalization must exactly match the repository fixture")
        certificate = (
            parse_certificate(certificate_raw, claim, formalization)
            if certificate_raw is not None
            else None
        )
        input_mode = (
            target_mode(target)
            if nat_bundle is not None
            else CERTIFICATE_MODE
            if certificate is not None
            else REFERENCE_MODE
        )
        if certificate is not None and (
            self.policy.get("certificate_mode") != CERTIFICATE_MODE
            or claim.id not in self.policy.get("certificate_targets", ())
        ):
            raise ValueError("Certificate mode or target is not approved by the sealed policy")
        check_started = time.monotonic()
        integrity_error = None
        try:
            self.verify_environment()
        except IntegrityError as error:
            integrity_error = str(error)
        integrity_seconds = time.monotonic() - check_started
        directory = self.root / "artifacts/runs" / (case["id"] + "-" + uuid.uuid4().hex)
        work = directory / "input"
        sandbox = directory / "sandbox"
        work.mkdir(parents=True)
        source = (
            render_checker(self.root, target, plan)
            if nat_bundle is not None
            else render_certificate_checker(self.root, formalization.checked_statement)
            if certificate is not None
            else render_source(case, formalization.checked_statement)
        )
        (work / "Candidate.lean").write_text(source, encoding="utf-8")
        if nat_bundle is not None:
            (directory / bundle_file(input_mode)).write_bytes(canonical(nat_bundle) + b"\n")
        if certificate is not None:
            (directory / CERTIFICATE_FILE).write_bytes(certificate.body)
        (directory / "runtime-snapshot.json").write_bytes(canonical(self._snapshot.data) + b"\n")
        (directory / "request.json").write_bytes(
            canonical(
                {
                    "claim": asdict(claim),
                    "formalization": asdict(formalization),
                    "environment": asdict(self.environment),
                    "policy": self.policy,
                    "fixture_id": case["id"],
                    "input_mode": input_mode,
                    "certificate_digest": certificate.fingerprint if certificate else None,
                    **(
                        {bundle_digest_key(input_mode): digest(nat_bundle)}
                        if nat_bundle is not None
                        else {}
                    ),
                    "worker_sha256": self._worker_hash,
                    "runtime_snapshot_digest": self._snapshot.fingerprint,
                }
            )
        )
        if checkpoint is not None:
            checkpoint("CHECKING", str(directory))
        if integrity_error is not None:
            (directory / "stdout.log").write_bytes(b"")
            (directory / "stderr.log").write_text(integrity_error + "\n", encoding="utf-8")
            candidate = {
                "run_status": "POLICY_BLOCKED",
                "returncode": None,
                "pid": None,
                "elapsed_seconds": 0.0,
            }
        else:
            candidate = self._run_worker(
                work,
                sandbox,
                directory / "stdout.log",
                directory / "stderr.log",
                self.timeout_seconds,
                phase="LEAN_RUNNING",
                cancel_requested=cancel_requested,
                checkpoint=checkpoint,
                lifecycle_lock_fd=lifecycle_lock_fd,
            )
        status, returncode = candidate["run_status"], candidate["returncode"]
        output = (directory / "stdout.log").read_text(encoding="utf-8", errors="replace")
        error = (directory / "stderr.log").read_text(encoding="utf-8", errors="replace")
        audit = None
        lines = [
            line.split("MRA_AUDIT:", 1)[1] for line in output.splitlines() if "MRA_AUDIT:" in line
        ]
        if status == "COMPLETED":
            if "MRA_TARGET_MISMATCH" in output or "MRA_CERTIFICATE_POLICY_" in output:
                status = "POLICY_BLOCKED"
            elif error or returncode is None or returncode < 0:
                status = "EXECUTION_ERROR"
            elif returncode == 0:
                if len(lines) != 1:
                    status = "EXECUTION_ERROR"
                else:
                    try:
                        audit = json.loads(lines[0])
                    except json.JSONDecodeError:
                        status = "EXECUTION_ERROR"
            elif "object file" in output or "unknown module prefix" in output:
                status = "EXECUTION_ERROR"
        replay = None
        replay_error = None
        if status == "COMPLETED" and returncode == 0:
            try:
                raw = extract_export(output)
                replay_work = directory / "replay"
                replay_work.mkdir()
                (replay_work / "proof.ndjson").write_bytes(raw)
                (replay_work / "Candidate.lean").write_text(
                    render_replay(self.root, formalization.checked_statement), encoding="utf-8"
                )
                (replay_work / "policy.json").write_bytes(canonical(self.policy) + b"\n")
                (replay_work / "stdout.log").touch()
                (replay_work / "stderr.log").touch()
                replay = {
                    "run_status": "POLICY_BLOCKED",
                    "returncode": None,
                    "pid": None,
                    "elapsed_seconds": 0.0,
                    "audit": None,
                }
                if checkpoint is not None:
                    checkpoint("REPLAY_PREPARING", str(directory))
                integrity_started = time.monotonic()
                try:
                    self.verify_environment()
                finally:
                    integrity_seconds += time.monotonic() - integrity_started
                replay = self._run_worker(
                    replay_work,
                    directory / "sandbox-replay",
                    replay_work / "stdout.log",
                    replay_work / "stderr.log",
                    self.timeout_seconds - candidate["elapsed_seconds"],
                    phase="REPLAY_RUNNING",
                    cancel_requested=cancel_requested,
                    checkpoint=checkpoint,
                    lifecycle_lock_fd=lifecycle_lock_fd,
                )
                replay_output = (replay_work / "stdout.log").read_text(
                    encoding="utf-8", errors="replace"
                )
                replay_stderr = (replay_work / "stderr.log").read_text(
                    encoding="utf-8", errors="replace"
                )
                replay_lines = [
                    line.split("MRA_REPLAY:", 1)[1]
                    for line in replay_output.splitlines()
                    if "MRA_REPLAY:" in line
                ]
                replay["audit"] = None
                status = replay["run_status"]
                if status == "COMPLETED":
                    if replay_stderr or replay["returncode"] is None or replay["returncode"] < 0:
                        status = "EXECUTION_ERROR"
                    elif replay["returncode"] != 0:
                        status = (
                            "EXECUTION_ERROR"
                            if "object file" in replay_output
                            or "unknown module prefix" in replay_output
                            else "POLICY_BLOCKED"
                        )
                        replay_error = replay_output[-4000:]
                    elif len(replay_lines) != 1:
                        status = "EXECUTION_ERROR"
                    else:
                        replay["audit"] = json.loads(replay_lines[0])
                replay["run_status"] = status
            except (IntegrityError, ValueError) as failure:
                status, replay_error = "POLICY_BLOCKED", str(failure)
                if replay is not None:
                    replay["run_status"] = status
                    (directory / "replay/stderr.log").write_text(
                        str(failure) + "\n", encoding="utf-8"
                    )
        payload = {
            "schema_version": "1",
            "claim_fingerprint": claim.fingerprint,
            "formalization_fingerprint": digest(asdict(formalization)),
            "environment_fingerprint": self.environment.fingerprint,
            "policy_digest": digest(self.policy),
            "input_mode": input_mode,
            "certificate_digest": certificate.fingerprint if certificate else None,
            **(
                {bundle_digest_key(input_mode): digest(nat_bundle)}
                if nat_bundle is not None
                else {}
            ),
            "integrity_error": integrity_error,
            "artifact_directory": str(directory),
            "returncode": returncode,
            "candidate_pid": candidate["pid"],
            "run_status": status,
            "elapsed_seconds": round(
                candidate["elapsed_seconds"] + (replay["elapsed_seconds"] if replay else 0), 4
            ),
            "integrity_seconds": round(integrity_seconds, 4),
            "total_elapsed_seconds": round(time.monotonic() - check_started, 4),
            "audit": audit,
            "replay": replay,
            "replay_error": replay_error,
            "files": {
                name: file_hash(directory / name)
                for name in sorted(
                    BASE_FILES
                    | (REPLAY_FILES if replay is not None else set())
                    | ({CERTIFICATE_FILE} if certificate is not None else set())
                    | ({bundle_file(input_mode)} if nat_bundle is not None else set())
                )
            },
        }
        signature = hmac.new(self._key, canonical(payload), hashlib.sha256).hexdigest()
        receipt = CheckReceipt(payload, signature)
        with (directory / "receipt.json").open("xb") as outgoing:
            outgoing.write(canonical(asdict(receipt)))
        return receipt
