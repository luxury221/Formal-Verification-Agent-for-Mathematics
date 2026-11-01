"""Real Lean validation of authored references, deliberately separate from model evaluation."""

from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid

from .datasets import DATASET_ID, EvaluationDataset, load_dataset
from .domain import canonical, digest
from .harness import MinimalResearchEngine
from .replay import MODE


def evaluation_plan(dataset: EvaluationDataset, split="regression", *, include_reserved=False):
    cases = dataset.select(split, include_reserved=include_reserved)
    protocol = {
        "schema_version": "1",
        "dataset_id": DATASET_ID,
        "dataset_fingerprint": dataset.fingerprint,
        "selected_case_ids": [case["id"] for case in cases],
        "budget": asdict(dataset.budget),
        "split": split,
        "verification": {
            "mode": MODE,
            "max_lean_workers_per_attempt": 2,
            "lean_timeout_scope": "shared across candidate and replay workers per attempt",
        },
    }
    return {
        **protocol,
        "protocol_fingerprint": digest(protocol),
        "kind": "repository-reference-validation-plan",
        "split_counts": {name: len(ids) for name, ids in dataset.manifest["splits"].items()},
        "reserved_selected": any(
            case["id"] in dataset.manifest["splits"]["reserved"] for case in cases
        ),
        "reserved_status": dataset.manifest["reserved_status"],
        "license": dataset.manifest["license"],
        "consent": dataset.manifest["consent"],
        "provenance": dataset.manifest["provenance"],
        "cases_sha256": dataset.manifest["sha256"],
        "training_use": False,
        "public_inputs": dataset.public_inputs(split, include_reserved=include_reserved),
        "limitations": dataset.manifest["limitations"],
    }


def run_reference_evaluation(root: Path, split="regression", *, include_reserved=False):
    root = root.resolve()
    dataset = load_dataset(root)
    plan = evaluation_plan(dataset, split, include_reserved=include_reserved)
    cases = dataset.select(split, include_reserved=include_reserved)
    if not cases:
        raise ValueError("Empty split has no reference evaluation; training is not enabled")
    base = root / "artifacts/evaluations"
    if not base.resolve().is_relative_to(root):
        raise ValueError("Evaluation artifacts redirect outside the project")
    directory = base / (DATASET_ID + "-" + uuid.uuid4().hex)
    directory.mkdir(parents=True)
    (directory / "plan.json").write_bytes(canonical(plan) + b"\n")
    started = time.monotonic()
    engine = MinimalResearchEngine(root, dataset.budget.lean_timeout_seconds, suite=DATASET_ID)
    initialization_seconds = time.monotonic() - started
    results = []
    for case in cases:
        case_started = time.monotonic()
        result = engine.run_case(
            case["id"], reviewer="repository-nat-arithmetic-v1-reference-author"
        )
        passed = (
            result.run_status.value,
            result.math_verdict.value,
            result.evidence_level.value,
        ) == (
            case["expected_status"],
            case["expected_verdict"],
            case["expected_level"],
        )
        receipt = json.loads((Path(result.artifact_directory) / "receipt.json").read_text())[
            "payload"
        ]
        replay = receipt.get("replay") or {}
        entry = {
            "id": case["id"],
            "family": dataset.manifest["families"][case["id"]],
            "passed": passed,
            "wall_seconds": round(time.monotonic() - case_started, 4),
            "lean_checks": 1,
            "candidate_attempts": 1,
            "lean_worker_count": sum(
                type(pid) is int and pid > 0
                for pid in (receipt.get("candidate_pid"), replay.get("pid"))
            ),
            "lean_worker_seconds": receipt["elapsed_seconds"],
            "integrity_seconds": receipt["integrity_seconds"],
            **asdict(result),
        }
        (directory / (case["id"] + ".json")).write_bytes(canonical(entry) + b"\n")
        results.append(entry)
    count = sum(entry["passed"] for entry in results)
    report = {
        "schema_version": "1",
        "kind": "real-lean-authored-reference-validation",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "plan": plan,
        "environment": asdict(engine.backend.environment),
        "total": len(results),
        "passed": count,
        "all_passed": count == len(results),
        "reference_pass_rate": count / len(results),
        "results": results,
        "observed_verdicts": dict(Counter(entry["math_verdict"] for entry in results)),
        "run_status_counts": dict(Counter(entry["run_status"] for entry in results)),
        "initialization_seconds": round(initialization_seconds, 4),
        "total_wall_seconds": round(time.monotonic() - started, 4),
        "lean_timeout_scope": "shared candidate and replay worker wall clock per attempt; full integrity verification and initialization are measured separately",
        "lean_checks_scope": "one complete candidate verification attempt, with up to two Lean workers and no extra generation attempt",
        "lean_worker_count": sum(entry["lean_worker_count"] for entry in results),
        "model_calls": 0,
        "model_input_tokens": 0,
        "model_output_tokens": 0,
        "model_solve_rate": None,
        "alignment_accuracy": None,
        "alignment_scope": "repository author assertion; no independent reviewer or user attestation",
        "receipt_scope": "historical export; fresh attestation requires rerunning the real checker",
        "artifact_directory": str(directory),
    }
    (directory / "report.json").write_bytes(canonical(report) + b"\n")
    return report
