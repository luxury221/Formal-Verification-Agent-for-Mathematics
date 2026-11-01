"""Structured formalization intake. Candidates and reviews grant no Lean evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import json
from typing import Protocol
import time
import uuid

from .domain import EvidenceLevel, RunStatus, Verdict, canonical, digest
from .model_service import (
    ModelBudget,
    ModelIdentity,
    ModelRequest,
    ModelResponse,
    ModelService,
    TokenUsage,
    integer_field,
    text_field,
)


def exact_object(value: object, fields: set[str], name: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{name} has missing or unexpected fields")
    return value


def strings(value: object, name: str, maximum: int = 32) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{name} must be an array of at most {maximum} strings")
    return tuple(text_field(item, name) for item in value)


def strict_json(raw: str) -> dict:
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise ValueError(f"Duplicate JSON field: {key}")
            obj[key] = value
        return obj

    def invalid_constant(value):
        raise ValueError(f"Nonstandard JSON constant: {value}")

    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object")
    return result


@dataclass(frozen=True)
class SourceClaim:
    id: str
    version: int
    statement: str
    context: tuple[str, ...] = ()
    schema_version: str = "1"

    def __post_init__(self) -> None:
        text_field(self.id, "source id", 128)
        integer_field(self.version, "source version", 1, 1000000)
        text_field(self.statement, "statement")
        if type(self.context) is not tuple or len(self.context) > 32:
            raise ValueError("Context must be an immutable tuple of at most 32 statements")
        for item in self.context:
            text_field(item, "context")
        if self.schema_version != "1":
            raise ValueError("Unsupported source schema")

    @classmethod
    def from_dict(cls, data: dict) -> SourceClaim:
        exact_object(data, {"id", "version", "statement", "context", "schema_version"}, "source")
        return cls(
            data["id"],
            data["version"],
            data["statement"],
            strings(data["context"], "context"),
            data["schema_version"],
        )

    @property
    def fingerprint(self) -> str:
        return digest(asdict(self))

    def revise(self, statement: str, context: tuple[str, ...]) -> SourceClaim:
        if (statement, context) == (self.statement, self.context):
            raise ValueError("A revision must change the statement or context")
        return SourceClaim(self.id, self.version + 1, statement, context)


@dataclass(frozen=True)
class Variable:
    name: str
    domain: str
    quantifier: str

    def __post_init__(self) -> None:
        text_field(self.name, "variable", 128)
        text_field(self.domain, "domain", 256)
        if self.quantifier not in ("forall", "exists", "parameter"):
            raise ValueError("Unknown quantifier")


@dataclass(frozen=True)
class FormalizationCandidate:
    source_fingerprint: str
    label: str
    lean_statement: str
    variables: tuple[Variable, ...]
    assumptions: tuple[str, ...]
    interpretation: str
    producer: ModelIdentity

    def __post_init__(self) -> None:
        for name in ("source_fingerprint", "label", "lean_statement", "interpretation"):
            text_field(getattr(self, name), name)
        if type(self.variables) is not tuple or not all(
            isinstance(v, Variable) for v in self.variables
        ):
            raise ValueError("Variables must be immutable typed records")
        if type(self.assumptions) is not tuple or not isinstance(self.producer, ModelIdentity):
            raise ValueError("Candidate needs immutable assumptions and adapter-owned identity")
        for assumption in self.assumptions:
            text_field(assumption, "assumption")

    @property
    def fingerprint(self) -> str:
        return digest(asdict(self))


class DraftStatus(str, Enum):
    CANDIDATES_READY = "CANDIDATES_READY"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    UNSUPPORTED = "UNSUPPORTED"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"


@dataclass(frozen=True)
class FormalizationDraft:
    source: SourceClaim
    request: ModelRequest
    status: DraftStatus
    run_status: RunStatus
    candidates: tuple[FormalizationCandidate, ...]
    questions: tuple[str, ...]
    producer: ModelIdentity | None
    usage: TokenUsage | None
    raw_response: str
    reason: str

    @property
    def request_fingerprint(self) -> str:
        return self.request.fingerprint

    @property
    def fingerprint(self) -> str:
        return digest(asdict(self))

    @property
    def math_verdict(self) -> Verdict:
        return (
            Verdict.NEEDS_CLARIFICATION
            if self.status == DraftStatus.NEEDS_CLARIFICATION
            else Verdict.UNKNOWN
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": "1",
            **asdict(self),
            "draft_fingerprint": self.fingerprint,
            "request_fingerprint": self.request_fingerprint,
            "candidate_fingerprints": [c.fingerprint for c in self.candidates],
            "math_verdict": self.math_verdict,
            "evidence_level": EvidenceLevel.E0,
            "can_execute_lean": False,
        }


class Formalizer(Protocol):
    def formalize_claim(
        self, source: SourceClaim, budget: ModelBudget = ModelBudget()
    ) -> FormalizationDraft: ...
    def formalize_step(
        self, source: SourceClaim, budget: ModelBudget = ModelBudget()
    ) -> FormalizationDraft: ...
    def suggest_clarifications(self, draft: FormalizationDraft) -> tuple[str, ...]: ...


def parse_output(
    raw: str, source: SourceClaim, producer: ModelIdentity
) -> tuple[DraftStatus, tuple[FormalizationCandidate, ...], tuple[str, ...]]:
    data = strict_json(raw)
    exact_object(
        data,
        {"schema_version", "source_fingerprint", "status", "candidates", "questions"},
        "model output",
    )
    if data["schema_version"] != "1" or data["source_fingerprint"] != source.fingerprint:
        raise ValueError("Model output is for another source/version or schema")
    status = DraftStatus(data["status"])
    if status not in (
        DraftStatus.CANDIDATES_READY,
        DraftStatus.NEEDS_CLARIFICATION,
        DraftStatus.UNSUPPORTED,
    ):
        raise ValueError("Model output may not set checker/transport status")
    if not isinstance(data["candidates"], list) or len(data["candidates"]) > 3:
        raise ValueError("Expected at most three candidates")
    candidates = []
    for entry in data["candidates"]:
        exact_object(
            entry,
            {"label", "lean_statement", "variables", "assumptions", "interpretation"},
            "candidate",
        )
        if not isinstance(entry["variables"], list) or len(entry["variables"]) > 32:
            raise ValueError("Expected at most 32 explicitly quantified variables")
        variables = []
        for variable in entry["variables"]:
            exact_object(variable, {"name", "domain", "quantifier"}, "variable")
            variables.append(Variable(**variable))
        if len({v.name for v in variables}) != len(variables):
            raise ValueError("Duplicate variable name")
        candidates.append(
            FormalizationCandidate(
                source.fingerprint,
                text_field(entry["label"], "label", 256),
                text_field(entry["lean_statement"], "Lean statement"),
                tuple(variables),
                strings(entry["assumptions"], "assumptions"),
                text_field(entry["interpretation"], "interpretation"),
                producer,
            )
        )
    questions = strings(data["questions"], "questions", 3)
    if status == DraftStatus.CANDIDATES_READY and (not candidates or questions):
        raise ValueError("Ready candidates require no unresolved questions")
    if status == DraftStatus.NEEDS_CLARIFICATION and not questions:
        raise ValueError("Ambiguous input requires explicit clarification questions")
    if status == DraftStatus.UNSUPPORTED and (candidates or questions):
        raise ValueError("Unsupported input may not include a ready candidate or questions")
    if len({c.fingerprint for c in candidates}) != len(candidates):
        raise ValueError("Duplicate candidate")
    return status, tuple(candidates), questions


class StructuredFormalizer:
    def __init__(self, service: ModelService) -> None:
        self.service = service

    def formalize_claim(
        self, source: SourceClaim, budget: ModelBudget = ModelBudget()
    ) -> FormalizationDraft:
        return self._formalize(source, "formalize_claim", budget)

    def formalize_step(
        self, source: SourceClaim, budget: ModelBudget = ModelBudget()
    ) -> FormalizationDraft:
        return self._formalize(source, "formalize_step", budget)

    def suggest_clarifications(self, draft: FormalizationDraft) -> tuple[str, ...]:
        return draft.questions

    def _formalize(self, source: SourceClaim, task: str, budget: ModelBudget) -> FormalizationDraft:
        input_json = canonical(
            {"source": asdict(source), "source_fingerprint": source.fingerprint}
        ).decode("utf-8")
        request = ModelRequest(uuid.uuid4().hex, task, input_json, budget)
        producer, usage, raw = None, None, ""

        def failed(status: DraftStatus, run_status: RunStatus, reason: str) -> FormalizationDraft:
            return FormalizationDraft(
                source, request, status, run_status, (), (), producer, usage, raw, reason
            )

        if len(input_json) > budget.max_input_chars:
            return failed(
                DraftStatus.ERROR,
                RunStatus.POLICY_BLOCKED,
                "Input exceeds the model request budget",
            )
        started = time.monotonic()
        try:
            response = self.service.generate(request)
        except TimeoutError:
            return failed(
                DraftStatus.TIMEOUT, RunStatus.TIMEOUT, "Model transport exceeded its deadline"
            )
        except OSError:
            return failed(
                DraftStatus.ERROR,
                RunStatus.EXECUTION_ERROR,
                "Model transport failed; no mathematical conclusion",
            )
        except ValueError:
            return failed(
                DraftStatus.INVALID_RESPONSE,
                RunStatus.POLICY_BLOCKED,
                "Model adapter returned an invalid structured response",
            )
        if time.monotonic() - started > budget.timeout_seconds:
            return failed(
                DraftStatus.TIMEOUT,
                RunStatus.TIMEOUT,
                "Model response arrived after the request deadline",
            )
        try:
            if (
                not isinstance(response, ModelResponse)
                or response.request_fingerprint != request.fingerprint
            ):
                raise ValueError("Response is not bound to this model request")
            producer, usage = response.identity, response.usage
            if not isinstance(producer, ModelIdentity) or not isinstance(usage, TokenUsage):
                raise ValueError("Missing adapter-owned model identity or token usage")
            if not isinstance(response.output_json, str):
                raise ValueError("Model output must be JSON text")
            if (
                len(response.output_json.encode("utf-8")) > budget.max_output_bytes
                or usage.output_tokens > budget.max_output_tokens
            ):
                raise ValueError("Model response exceeds the output budget")
            raw = response.output_json
            status, candidates, questions = parse_output(raw, source, producer)
        except (ValueError, TypeError, RecursionError) as error:
            return failed(DraftStatus.INVALID_RESPONSE, RunStatus.POLICY_BLOCKED, str(error))
        return FormalizationDraft(
            source,
            request,
            status,
            RunStatus.COMPLETED,
            candidates,
            questions,
            producer,
            usage,
            raw,
            "Candidate text requires independent semantic review and Lean checking",
        )


@dataclass(frozen=True)
class CandidateReview:
    draft_fingerprint: str
    candidate_fingerprint: str
    reviewer: str
    decision: str
    note: str

    def __post_init__(self) -> None:
        for name in ("draft_fingerprint", "candidate_fingerprint", "reviewer", "note"):
            text_field(getattr(self, name), name)
        if self.decision not in ("accept", "reject"):
            raise ValueError("Review decision must be accept or reject")


def review_candidate(draft: FormalizationDraft, review: CandidateReview) -> dict:
    if draft.status != DraftStatus.CANDIDATES_READY or draft.run_status != RunStatus.COMPLETED:
        raise ValueError(
            "Unresolved or failed formalization cannot be accepted or rejected as a ready candidate"
        )
    if draft.fingerprint != review.draft_fingerprint:
        raise ValueError("Review belongs to another draft/source version")
    matches = [c for c in draft.candidates if c.fingerprint == review.candidate_fingerprint]
    if len(matches) != 1:
        raise ValueError("Review belongs to another candidate/version")
    return {
        "schema_version": "1",
        "review": asdict(review),
        "source": asdict(draft.source),
        "candidate": asdict(matches[0]),
        "review_status": "ACCEPTED" if review.decision == "accept" else "REJECTED",
        "math_verdict": Verdict.UNKNOWN,
        "evidence_level": EvidenceLevel.E0,
        "can_execute_lean": False,
    }
