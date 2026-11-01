"""Only the evidence gate may grant E3/E4 or a definite mathematical verdict."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from .domain import (
    Alignment,
    CheckReceipt,
    Claim,
    EnvironmentPin,
    Evidence,
    EvidenceLevel,
    Formalization,
    RunStatus,
    TaskResult,
    Verdict,
    digest,
)
from .integrity import IntegrityError, file_hash
from .verifier import LeanExecutionBackend
from .replay import BASE_FILES, REPLAY_FILES, MODE
from .nat_ideas import (
    MODE as NAT_MODE,
    INT_MODE,
    STRATEGIES,
    parse_bundle,
    bundle_file,
    bundle_digest_key,
    target_mode,
)
from .certificates import (
    CERTIFICATE_FILE,
    MODE as CERTIFICATE_MODE,
    REFERENCE_MODE,
    parse_certificate,
    read_certificate,
)


class EvidenceGate:
    def __init__(self, root: Path, backend: LeanExecutionBackend, policy: dict) -> None:
        self.root = root.resolve()
        self.backend = backend
        self.policy = policy

    def assess(
        self,
        claim: Claim,
        formalization: Formalization,
        receipt: CheckReceipt,
        alignment: Alignment | None = None,
        environment: EnvironmentPin | None = None,
    ) -> tuple[TaskResult, Evidence | None]:
        payload = receipt.payload
        directory = payload.get("artifact_directory", "")

        def reject(
            reason: str, status: RunStatus = RunStatus.POLICY_BLOCKED
        ) -> tuple[TaskResult, None]:
            return TaskResult(
                claim.id, status, Verdict.UNKNOWN, EvidenceLevel.E2, reason, directory
            ), None

        if not self.backend.authentic(receipt):
            return reject("Receipt is not authenticated by the internal checker")
        if payload.get("schema_version") != "1":
            return reject("Unsupported evidence schema")
        expected_statement = (
            claim.formal_statement
            if formalization.direction == "prove"
            else f"¬ ({claim.formal_statement})"
        )
        if (
            formalization.claim_fingerprint != claim.fingerprint
            or formalization.checked_statement != expected_statement
        ):
            return reject("Formalization changes the claim or the refutation target")
        if payload["claim_fingerprint"] != claim.fingerprint or payload[
            "formalization_fingerprint"
        ] != digest(asdict(formalization)):
            return reject("Claim or formalization version changed")
        current_environment = environment or self.backend.environment
        if payload["environment_fingerprint"] != current_environment.fingerprint:
            return reject("Lean/mathlib environment changed")
        if payload["policy_digest"] != digest(self.policy):
            return reject("Evidence policy changed; re-check required")
        try:
            self.backend.verify_environment()
        except IntegrityError as error:
            return reject(f"Runtime integrity check failed: {error}")
        artifact_path = Path(directory).resolve()
        if not artifact_path.is_relative_to(self.root / "artifacts/runs"):
            return reject("Artifact path escapes the project")
        replay = payload.get("replay")
        input_mode = payload.get("input_mode")
        if input_mode not in (REFERENCE_MODE, CERTIFICATE_MODE, NAT_MODE, INT_MODE):
            return reject("Unknown checker input mode")
        certificate_files = (
            {CERTIFICATE_FILE}
            if input_mode == CERTIFICATE_MODE
            else {bundle_file(input_mode)}
            if input_mode in (NAT_MODE, INT_MODE)
            else set()
        )
        if set(payload["files"]) != BASE_FILES | certificate_files | (
            REPLAY_FILES if replay is not None else set()
        ):
            return reject("Incomplete artifact bundle")
        for name, expected_hash in payload["files"].items():
            path = artifact_path / name
            if (
                not path.resolve().is_relative_to(artifact_path)
                or not path.is_file()
                or file_hash(path) != expected_hash
            ):
                return reject("Verification artifact was changed or removed")
        if input_mode in (NAT_MODE, INT_MODE):
            try:
                raw = (artifact_path / bundle_file(input_mode)).read_bytes()
                target, plan = parse_bundle(raw)
            except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
                return reject(f"Invalid natural arithmetic target binding: {error}")
            prefix = "int" if input_mode == INT_MODE else "nat"
            if (
                target_mode(target) != input_mode
                or self.policy.get(prefix + "_search_mode") != input_mode
                or self.policy.get(prefix + "_formula_schema") != target.TARGET_SCHEMA
                or self.policy.get(prefix + "_strategies") != list(STRATEGIES)
                or (claim, formalization) != target.contract(plan["direction"])
                or payload.get(bundle_digest_key(input_mode))
                != digest({"mode": input_mode, "target": target.to_dict(), "plan": plan})
                or payload.get("certificate_digest") is not None
            ):
                return reject("Natural arithmetic target, plan or policy changed")
        elif input_mode == CERTIFICATE_MODE:
            try:
                certificate = parse_certificate(
                    read_certificate(artifact_path / CERTIFICATE_FILE), claim, formalization
                )
            except ValueError as error:
                return reject(f"Certificate binding is invalid: {error}")
            if (
                self.policy.get("certificate_mode") != CERTIFICATE_MODE
                or claim.id not in self.policy.get("certificate_targets", ())
                or payload.get("certificate_digest") != certificate.fingerprint
            ):
                return reject("Certificate mode, target or digest is not approved")
        elif payload.get("certificate_digest") is not None:
            return reject("Reference input cannot carry a certificate digest")
        if payload["run_status"] != "COMPLETED":
            return reject(
                payload.get("integrity_error")
                or payload.get("replay_error")
                or "Checker did not finish a valid proof check",
                RunStatus(payload["run_status"]),
            )
        if payload["returncode"] != 0:
            return reject(
                "Lean rejected the proof candidate; this does not refute the claim",
                RunStatus.COMPLETED,
            )
        audit = payload["audit"]
        if input_mode in (NAT_MODE, INT_MODE) and (
            not isinstance(audit, dict)
            or audit.get("input_mode") != input_mode
            or audit.get("candidate_source_executed") is not False
            or audit.get("source_origin") != "host-owned-formula-and-bounded-strategy"
        ):
            return reject("Missing host-owned natural arithmetic strategy audit")
        if input_mode == CERTIFICATE_MODE and (
            not isinstance(audit, dict)
            or audit.get("input_mode") != CERTIFICATE_MODE
            or audit.get("candidate_source_executed") is not False
        ):
            return reject("Missing trusted certificate-construction audit")
        if (
            not audit
            or audit.get("declaration_kind") != "theorem"
            or audit.get("type_matches") is not True
            or audit.get("target") != "MraCandidate.result"
        ):
            return reject("Missing semantic declaration/type audit")
        independent = replay.get("audit") if isinstance(replay, dict) else None
        if (
            self.policy.get("require_independent_replay") is not True
            or self.policy.get("replay_mode") != MODE
            or not isinstance(independent, dict)
            or replay.get("run_status") != "COMPLETED"
            or replay.get("returncode") != 0
            or type(payload.get("candidate_pid")) is not int
            or payload["candidate_pid"] < 1
            or type(replay.get("pid")) is not int
            or replay["pid"] < 1
            or replay.get("pid") == payload.get("candidate_pid")
            or independent.get("mode") != MODE
            or independent.get("candidate_module_imported") is not False
            or independent.get("target") != "MraCandidate.result"
            or independent.get("declaration_kind") != "theorem"
            or independent.get("type_matches") is not True
            or type(independent.get("replayed_declarations")) is not int
            or independent["replayed_declarations"] < 1
            or set(independent.get("axioms", ())) != set(audit.get("axioms", ()))
            or set(independent.get("local_dependency_axioms", ()))
            != set(audit.get("local_dependency_axioms", ()))
        ):
            return reject("Missing, inconsistent or failed independent kernel replay")
        if independent.get("local_axioms"):
            return reject("Independent replay reported local axioms")
        axioms = tuple(audit["axioms"])
        all_axioms = set(axioms) | set(audit.get("local_dependency_axioms", []))
        if (
            audit.get("local_axioms")
            or all_axioms - set(self.policy["allowed_axioms"])
            or all_axioms & set(self.policy["forbidden_axioms"])
        ):
            return reject(
                "Unapproved local or transitive axiom dependency: "
                + ", ".join(sorted(all_axioms | set(audit.get("local_axioms", []))))
            )
        level = EvidenceLevel.E3
        verdict = Verdict.UNKNOWN
        reason = "Exact formal proposition checked; natural-language alignment awaits review"
        if alignment is not None:
            if (
                alignment.schema_version != "1"
                or alignment.claim_fingerprint != claim.fingerprint
                or alignment.formalization_fingerprint != digest(asdict(formalization))
                or not alignment.reviewer.strip()
                or not alignment.note.strip()
            ):
                return reject("Alignment record is missing, stale, or for another statement")
            level = EvidenceLevel.E4
            verdict = Verdict.PROVED if formalization.direction == "prove" else Verdict.REFUTED
            reason = "Lean proof and version-bound alignment record satisfy the evidence policy"
        evidence = Evidence(
            claim.fingerprint,
            digest(asdict(formalization)),
            current_environment.fingerprint,
            level,
            axioms,
            directory,
            digest(asdict(receipt)),
            digest(self.policy),
        )
        return TaskResult(
            claim.id, RunStatus.COMPLETED, verdict, level, reason, directory
        ), evidence
