"""One model proposal and one real Lean attempt per authored formal target."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from pathlib import Path
import time
import uuid

from .chat_model import ChatModelService, ModelConfig, PROOF_PROMPT
from .datasets import load_dataset
from .domain import Alignment, canonical, digest
from .evaluation import evaluation_plan
from .evaluation_targets import reference_target
from .evidence import EvidenceGate
from .formalizer import exact_object, strict_json
from .integrity import file_hash
from .model_service import ModelIdentity, ModelRequest, ModelResponse, TokenUsage
from .nat_ideas import STRATEGIES, validate_plan
from .tasks import TaskStore, _now
from .verifier import NativeLeanBackend

SCOPE = "authored-formal-target-proof-plan-only"


def model_evaluation_plan(root, split="regression", *, include_reserved=False):
    dataset = load_dataset(root)
    parent = evaluation_plan(dataset, split, include_reserved=include_reserved)
    cases = dataset.select(split, include_reserved=include_reserved)
    if not cases:
        raise ValueError("Empty training split cannot be evaluated")
    protocol = {
        "schema_version": "1",
        "kind": "model-proof-plan-evaluation-plan",
        "scope": SCOPE,
        "reference_protocol_fingerprint": parent["protocol_fingerprint"],
        "dataset_fingerprint": dataset.fingerprint,
        "budget": asdict(dataset.budget),
        "split": split,
        "selected_case_ids": [case["id"] for case in cases],
        "targets": [reference_target(case).to_dict() for case in cases],
        "strategies": list(STRATEGIES),
        "proof_prompt_fingerprint": digest(PROOF_PROMPT),
        "implementation_sha256": {
            name: file_hash(root / name)
            for name in ("mra/model_evaluation.py", "mra/evaluation_targets.py")
        },
    }
    return {
        **protocol,
        "protocol_fingerprint": digest(protocol),
        "reserved_selected": parent["reserved_selected"],
        "reserved_status": parent["reserved_status"],
        "license": parent["license"],
        "consent": parent["consent"],
        "training_use": False,
        "model_calls": 0,
        "alignment_accuracy": None,
        "limitations": [
            "Gold formal targets are provided; this does not measure natural-language formalization",
            "Same numerical budget as references, but restricted strategies differ from authored proofs",
            "Repository-visible authored examples; not an independently blind benchmark",
            "Alignment is a repository-author assertion; not an independent expert review",
            "No repair, implicit retry, or reference proof/label/witness in model requests",
        ],
    }


def proof_request(target, budget, request_id):
    return ModelRequest(
        request_id,
        "nat_prove",
        canonical(
            {
                "target_fingerprint": target.fingerprint,
                "target": target.to_dict(),
                "formal_statement": target.contract("prove")[0].formal_statement,
                "attempt": 1,
                "maximum_attempts": 1,
                "feedback": [],
            }
        ).decode(),
        budget,
        "nat-proof-output-v1",
    )


def parse_proposal(response, request, target, *, simulation=False):
    if (
        not isinstance(response, ModelResponse)
        or response.request_fingerprint != request.fingerprint
        or not isinstance(response.identity, ModelIdentity)
        or response.identity.kind != ("fixture" if simulation else "model")
        or not isinstance(response.usage, TokenUsage)
        or not isinstance(response.output_json, str)
    ):
        raise ValueError("Model response identity, usage or request binding is invalid")
    if (
        response.usage.output_tokens > request.budget.max_output_tokens
        or len(response.output_json.encode()) > request.budget.max_output_bytes
    ):
        raise ValueError("Model proposal exceeds its output budget")
    proposal = strict_json(response.output_json)
    exact_object(proposal, {"target_fingerprint", "plan"}, "Evaluation proof proposal")
    if proposal["target_fingerprint"] != target.fingerprint:
        raise ValueError("Model proposal changes the benchmark target")
    return None if proposal["plan"] is None else validate_plan(proposal["plan"], target)


def _write(path, value):
    with path.open("xb") as output:
        output.write(canonical(value) + b"\n")


def _save_report(directory, report):
    pending = directory / ".report.pending"
    _write(pending, report)
    pending.replace(directory / "report.json")


def _new_entry(case):
    return {
        "id": case["id"],
        "state": "PENDING",
        "run_status": None,
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0_CANDIDATE",
        "solved": False,
        "generation_attempts": 0,
        "lean_checks": 0,
        "lean_worker_count": 0,
        "usage": None,
        "identity": None,
        "files": {},
        "lean_files": {},
        "reason": None,
    }


def _summarize(report):
    entries = report["results"]
    complete = report["state"] == "COMPLETED"
    simulation = report["simulation"]
    solved = sum(entry["solved"] for entry in entries)
    usage = [entry["usage"] for entry in entries if entry["usage"] is not None]
    requests = sum(entry["generation_attempts"] for entry in entries)
    report.update(
        total=len(entries),
        processed=sum(entry["state"] not in ("PENDING", "RUNNING") for entry in entries),
        solved=solved,
        model_solve_rate=solved / len(entries) if complete and not simulation else None,
        simulation_checked_rate=solved / len(entries) if complete and simulation else None,
        alignment_accuracy=None,
        end_to_end_solve_rate=None,
        model_request_attempts=0 if simulation else requests,
        simulation_request_attempts=requests if simulation else 0,
        model_calls=0 if simulation else sum(entry["identity"] is not None for entry in entries),
        simulation_calls=sum(entry["identity"] is not None for entry in entries)
        if simulation
        else 0,
        usage_complete=len(usage) == requests,
        known_input_tokens=sum(value["input_tokens"] for value in usage),
        known_output_tokens=sum(value["output_tokens"] for value in usage),
        cost=None,
        lean_checks=sum(entry["lean_checks"] for entry in entries),
        lean_worker_count=sum(entry["lean_worker_count"] for entry in entries),
        run_status_counts=dict(Counter(entry["run_status"] or "NOT_STARTED" for entry in entries)),
    )


def run_model_evaluation(
    root,
    split="regression",
    *,
    include_reserved=False,
    simulation_service=None,
    cancel_requested=None,
):
    root = Path(root).resolve()
    plan = model_evaluation_plan(root, split, include_reserved=include_reserved)
    dataset = load_dataset(root)
    cases = dataset.select(split, include_reserved=include_reserved)
    simulation = simulation_service is not None
    config = None if simulation else ModelConfig.load(root)
    if not simulation and config is None:
        raise ValueError("MODEL_NOT_CONFIGURED: inspect --plan, then configure model.local.json")
    cancelled = cancel_requested or (lambda: False)
    # A whole evaluation holds the same lock as the browser/CLI task workers.
    # Its child processes inherit the descriptor if the supervisor dies.
    with TaskStore(root).worker_lock() as lock_fd:
        started = time.monotonic()
        backend = NativeLeanBackend(root, dataset.budget.lean_timeout_seconds)
        initialization = time.monotonic() - started
        gate = EvidenceGate(root, backend, backend.policy)
        base = root / "artifacts/model-evaluations"
        if not base.resolve().is_relative_to(root):
            raise ValueError("Model evaluation artifacts redirect outside project")
        directory = base / ("nat-plans-" + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        _write(directory / "plan.json", plan)
        service = simulation_service or ChatModelService(
            root, config, cancel_requested=cancelled, lifecycle_lock_fd=lock_fd
        )
        report = {
            "schema_version": "1",
            "kind": "fixture-plan-integration"
            if simulation
            else "real-model-proof-plan-evaluation",
            "scope": SCOPE,
            "state": "RUNNING",
            "simulation": simulation,
            "recorded_at": _now(),
            "plan": plan,
            "environment": asdict(backend.environment),
            "configured_model": config.public() if config else None,
            "initialization_seconds": initialization,
            "results": [_new_entry(case) for case in cases],
            "files": {"plan.json": file_hash(directory / "plan.json")},
            "artifact_directory": str(directory),
            "historical_record": True,
            "fresh_attestation": False,
            "limitations": plan["limitations"],
        }
        _summarize(report)
        _save_report(directory, report)
        try:
            for case, entry in zip(cases, report["results"]):
                if cancelled():
                    raise InterruptedError("Evaluation cancelled before next request")
                if config and ModelConfig.load(root) != config:
                    raise RuntimeError("Model configuration changed during evaluation")
                case_started = time.monotonic()
                target = reference_target(case)
                request = proof_request(target, dataset.budget.model, uuid.uuid4().hex)
                case_dir = directory / case["id"]
                case_dir.mkdir()
                _write(case_dir / "request.json", asdict(request))
                entry.update(state="RUNNING", generation_attempts=1)
                entry["files"]["request.json"] = file_hash(case_dir / "request.json")
                _summarize(report)
                _save_report(directory, report)
                try:
                    generated_at = time.monotonic()
                    response = service.generate(request)
                    entry["model_seconds"] = time.monotonic() - generated_at
                    if entry["model_seconds"] > request.budget.timeout_seconds:
                        raise TimeoutError("Model returned after the generation deadline")
                    # Account for valid transport envelopes even if the proposal is rejected.
                    if (
                        isinstance(response, ModelResponse)
                        and response.request_fingerprint == request.fingerprint
                        and isinstance(response.identity, ModelIdentity)
                        and response.identity.kind == ("fixture" if simulation else "model")
                        and isinstance(response.usage, TokenUsage)
                    ):
                        entry.update(
                            identity=asdict(response.identity), usage=asdict(response.usage)
                        )
                    if (
                        entry["identity"] is not None
                        and isinstance(response.output_json, str)
                        and len(response.output_json.encode()) <= request.budget.max_output_bytes
                    ):
                        _write(case_dir / "response.json", asdict(response))
                        entry["files"]["response.json"] = file_hash(case_dir / "response.json")
                    proposal = parse_proposal(response, request, target, simulation=simulation)
                    if cancelled():
                        raise InterruptedError("Evaluation cancelled after model response")
                    if proposal is None:
                        entry.update(state="ABSTAINED", run_status="COMPLETED")
                    else:
                        entry["lean_checks"] = 1
                        receipt = backend.check_nat_plan(
                            target, proposal, cancel_requested=cancelled, lifecycle_lock_fd=lock_fd
                        )
                        claim, formalization = target.contract(proposal["direction"])
                        alignment = Alignment(
                            claim.fingerprint,
                            digest(asdict(formalization)),
                            "repository-nat-arithmetic-v1-structured-target-author",
                            target.interpretation,
                        )
                        result, evidence = gate.assess(claim, formalization, receipt, alignment)
                        check = {
                            "target": target.to_dict(),
                            "plan": proposal,
                            "claim": asdict(claim),
                            "formalization": asdict(formalization),
                            "alignment": asdict(alignment),
                            "task": asdict(result),
                            "evidence": asdict(evidence) if evidence else None,
                        }
                        lean_dir = Path(result.artifact_directory)
                        _write(lean_dir / "report.json", check)
                        entry["lean_directory"] = str(lean_dir.relative_to(root))
                        entry["lean_files"] = {
                            name: file_hash(lean_dir / name)
                            for name in (*receipt.payload["files"], "receipt.json", "report.json")
                        }
                        entry["lean_worker_count"] = sum(
                            type(pid) is int and pid > 0
                            for pid in (
                                receipt.payload.get("candidate_pid"),
                                (receipt.payload.get("replay") or {}).get("pid"),
                            )
                        )
                        entry.update(
                            state="CHECKED",
                            run_status=result.run_status,
                            math_verdict=result.math_verdict,
                            evidence_level=result.evidence_level,
                            reason=result.reason,
                            solved=evidence is not None
                            and result.math_verdict == case["expected_verdict"]
                            and result.evidence_level == "E4_ALIGNED_AND_VERIFIED",
                        )
                        if cancelled():
                            raise InterruptedError("Evaluation cancelled before result publication")
                except (InterruptedError, KeyboardInterrupt):
                    entry.update(
                        state="CANCELLED",
                        run_status="CANCELLED",
                        math_verdict="UNKNOWN",
                        evidence_level="E0_CANDIDATE",
                        solved=False,
                    )
                    raise
                except RuntimeError:
                    entry.update(
                        state="FAILED",
                        run_status="POLICY_BLOCKED",
                        math_verdict="UNKNOWN",
                        evidence_level="E0_CANDIDATE",
                        solved=False,
                        reason="Runtime verification failed; evaluation stopped",
                    )
                    raise
                except (OSError, ValueError, TypeError, KeyError) as error:
                    entry.update(
                        state="FAILED",
                        run_status="TIMEOUT"
                        if isinstance(error, TimeoutError)
                        else "EXECUTION_ERROR",
                        reason=type(error).__name__ + ": proposal/transport/check failed",
                        math_verdict="UNKNOWN",
                        evidence_level="E0_CANDIDATE",
                        solved=False,
                    )
                finally:
                    entry["wall_seconds"] = time.monotonic() - case_started
                    _write(case_dir / "result.json", entry)
                    report["files"][case["id"] + "/result.json"] = file_hash(
                        case_dir / "result.json"
                    )
                    _summarize(report)
                    _save_report(directory, report)
            report["state"] = "COMPLETED"
        except (InterruptedError, KeyboardInterrupt):
            report["state"] = "CANCELLED"
        except (OSError, ValueError, RuntimeError):
            report["state"] = "INTERRUPTED"
            report["reason"] = "Evaluation stopped; no automatic retry or score for a partial run"
        report["total_wall_seconds"] = time.monotonic() - started
        _summarize(report)
        _save_report(directory, report)
        return report


def read_model_evaluation(root, directory):
    """Read historical artifacts with file checks; never authenticate or resume a run."""
    root = Path(root).resolve()
    directory = Path(directory).resolve()
    base = root / "artifacts/model-evaluations"
    if not directory.is_relative_to(base) or directory == base:
        raise ValueError("Report must be inside this project's model-evaluations directory")
    try:
        report = strict_json((directory / "report.json").read_text(encoding="utf-8"))
        if report["artifact_directory"] != str(directory):
            raise ValueError("Report path changed")
        if report["state"] not in ("RUNNING", "COMPLETED", "CANCELLED", "INTERRUPTED"):
            raise ValueError("Unknown evaluation state")
        if (
            not report["results"]
            or [entry["id"] for entry in report["results"]] != report["plan"]["selected_case_ids"]
        ):
            raise ValueError("Case identities differ from the evaluation plan")
        if type(report["simulation"]) is not bool or report["kind"] != (
            "fixture-plan-integration"
            if report["simulation"]
            else "real-model-proof-plan-evaluation"
        ):
            raise ValueError("Report producer classification changed")
        if report["plan"] != strict_json((directory / "plan.json").read_text(encoding="utf-8")):
            raise ValueError("Stored plan changed")
        for name, expected in report["files"].items():
            path = directory / name
            if not path.resolve().is_relative_to(directory) or file_hash(path) != expected:
                raise ValueError("Evaluation file changed")
        for entry in report["results"]:
            if entry["identity"] is not None and entry["identity"]["kind"] != (
                "fixture" if report["simulation"] else "model"
            ):
                raise ValueError("Model identity differs from report producer classification")
            result_path = directory / entry["id"] / "result.json"
            if entry["state"] not in ("PENDING", "RUNNING"):
                if entry != strict_json(result_path.read_text(encoding="utf-8")):
                    raise ValueError("Stored case result changed")
            elif report["state"] == "COMPLETED":
                raise ValueError("Incomplete cases cannot yield a completed score")
            for name, expected in entry["files"].items():
                path = directory / entry["id"] / name
                if not path.resolve().is_relative_to(directory) or file_hash(path) != expected:
                    raise ValueError("Model trace changed")
            for name, expected in entry["lean_files"].items():
                path = root / entry["lean_directory"] / name
                if (
                    not path.resolve().is_relative_to(root / "artifacts/runs")
                    or file_hash(path) != expected
                ):
                    raise ValueError("Lean evidence changed")
        saved = dict(report)
        _summarize(report)
        if report != saved:
            raise ValueError("Aggregate scores differ from recorded results")
        report.update(integrity_ok=True, historical_record=True, fresh_attestation=False)
        return report
    except (OSError, ValueError, KeyError, TypeError):
        return {
            "integrity_ok": False,
            "state": "ARTIFACTS_CHANGED",
            "model_solve_rate": None,
            "simulation_checked_rate": None,
            "historical_record": True,
            "fresh_attestation": False,
            "reason": "Stored report or referenced artifacts changed; rerun for new evidence",
        }
