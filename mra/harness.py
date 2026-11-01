"""Replaceable minimal research harness for the P0 verification experiment."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Callable, Protocol

from .domain import (
    Alignment,
    Claim,
    EvidenceLevel,
    Formalization,
    RunStatus,
    TaskResult,
    Verdict,
    canonical,
    digest,
)
from .datasets import DATASET_ID, load_dataset
from .evidence import EvidenceGate
from .verifier import NativeLeanBackend


class ResearchEngine(Protocol):
    def run_case(self, case_id: str, reviewer: str | None = None) -> TaskResult: ...


class MinimalResearchEngine:
    def __init__(self, root: Path, timeout_seconds: float = 30.0, suite: str = "golden") -> None:
        self.root = root.resolve()
        if suite not in ("golden", DATASET_ID):
            raise ValueError("Unknown repository reference suite")
        cases = (
            json.loads((root / "evals/golden_cases.json").read_text(encoding="utf-8"))
            if suite == "golden"
            else load_dataset(root).cases
        )
        self.cases = {c["id"]: c for c in cases}
        self.backend = NativeLeanBackend(root, timeout_seconds)
        self.gate = EvidenceGate(root, self.backend, self.backend.policy)

    def run_case(
        self,
        case_id: str,
        reviewer: str | None = None,
        *,
        cancel_requested: Callable[[], bool] | None = None,
        checkpoint: Callable[[str, str], None] | None = None,
        lifecycle_lock_fd: int | None = None,
        certificate_raw: bytes | None = None,
    ) -> TaskResult:
        case = self.cases[case_id]
        claim = Claim(
            case_id,
            1,
            case["original_statement"],
            case["formal_statement"],
            tuple(case.get("annotation", {}).get("assumptions", ())),
        )
        statement = (
            claim.formal_statement
            if case["direction"] == "prove"
            else f"¬ ({claim.formal_statement})"
        )
        formalization = Formalization(claim.fingerprint, statement, case["direction"])
        checker = self.backend.check if certificate_raw is None else self.backend.check_certificate
        receipt = checker(
            case,
            claim,
            formalization,
            **({"raw": certificate_raw} if certificate_raw is not None else {}),
            cancel_requested=cancel_requested,
            checkpoint=checkpoint,
            lifecycle_lock_fd=lifecycle_lock_fd,
        )
        alignment = None
        if reviewer:
            alignment = Alignment(
                claim.fingerprint,
                digest(asdict(formalization)),
                reviewer,
                case.get("annotation", {}).get(
                    "alignment_note",
                    "Reviewer explicitly attests to the displayed repository fixture's original/formal statement mapping; refutation checks its negation.",
                ),
            )
        if checkpoint is not None:
            checkpoint("ASSESSING", receipt.payload["artifact_directory"])
        if cancel_requested is not None and cancel_requested():
            result = TaskResult(
                case_id,
                RunStatus.CANCELLED,
                Verdict.UNKNOWN,
                EvidenceLevel.E0,
                "Task cancellation requested; no evidence promoted",
                receipt.payload["artifact_directory"],
            )
            evidence = None
        else:
            result, evidence = self.gate.assess(claim, formalization, receipt, alignment)
        directory = Path(result.artifact_directory)
        with (directory / "report.json").open("xb") as output:
            output.write(
                canonical(
                    {
                        "task": asdict(result),
                        "claim": asdict(claim),
                        "formalization": asdict(formalization),
                        "alignment": asdict(alignment) if alignment else None,
                        "evidence": asdict(evidence) if evidence else None,
                        "limitations": [
                            "Repository-fixed targets; proof input is an authored reference or a bounded kernel-expression certificate; no LLM integration",
                            "Alignment is an explicit named reviewer assertion, not a Lean theorem about natural language",
                            "Proof data is replayed in a separate process using the same sealed Lean kernel implementation; arbitrary Lean input remains unsupported",
                            "Receipts are authenticated within this Python backend instance; historical reports require a fresh check for new attestation",
                        ],
                    }
                )
            )
        with (directory / "events.jsonl").open("xb") as output:
            # Compatibility summary only. Durable task events are recorded in
            # real time by TaskStore; these lines are written after execution.
            for state in (
                "CREATED",
                "RUNNING",
                "CANCELLED"
                if result.run_status == RunStatus.CANCELLED
                else "COMPLETED"
                if result.run_status.value in ("COMPLETED", "POLICY_BLOCKED")
                else "FAILED",
            ):
                output.write(
                    canonical(
                        {
                            "task_id": case_id,
                            "state": state,
                            "run_status": result.run_status,
                            "math_verdict": result.math_verdict,
                            "kind": "post-run-summary",
                        }
                    )
                    + b"\n"
                )
        return result
