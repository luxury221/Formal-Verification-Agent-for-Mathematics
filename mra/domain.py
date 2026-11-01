"""Small immutable contracts; execution success never implies mathematical truth."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
from typing import Any


class Verdict(str, Enum):
    PROVED = "PROVED"
    REFUTED = "REFUTED"
    GAP_FOUND = "GAP_FOUND"
    UNKNOWN = "UNKNOWN"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"


class RunStatus(str, Enum):
    COMPLETED = "COMPLETED"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    TIMEOUT = "TIMEOUT"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    CANCELLED = "CANCELLED"


class EvidenceLevel(str, Enum):
    E0 = "E0_CANDIDATE"
    E2 = "E2_FORMALIZED"
    E3 = "E3_KERNEL_CHECKED"
    E4 = "E4_ALIGNED_AND_VERIFIED"


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


@dataclass(frozen=True)
class Claim:
    id: str
    version: int
    original_statement: str
    formal_statement: str
    assumptions: tuple[str, ...] = ()
    schema_version: str = "1"

    def __post_init__(self) -> None:
        if self.version < 1 or not all((self.id, self.original_statement, self.formal_statement)):
            raise ValueError("A claim needs an identity, positive version, and both statements")
        if self.schema_version != "1":
            raise ValueError("Unsupported claim schema")

    @property
    def fingerprint(self) -> str:
        return digest(asdict(self))


@dataclass(frozen=True)
class Formalization:
    claim_fingerprint: str
    checked_statement: str
    direction: str = "prove"
    schema_version: str = "1"

    def __post_init__(self) -> None:
        if self.direction not in ("prove", "refute") or self.schema_version != "1":
            raise ValueError("Unsupported formalization contract")


@dataclass(frozen=True)
class Alignment:
    claim_fingerprint: str
    formalization_fingerprint: str
    reviewer: str
    note: str
    schema_version: str = "1"


@dataclass(frozen=True)
class EnvironmentPin:
    lean_version: str
    mathlib_commit: str
    runtime_digest: str
    manifest_digest: str
    schema_version: str = "1"

    @property
    def fingerprint(self) -> str:
        return digest(asdict(self))


@dataclass(frozen=True)
class CheckReceipt:
    """Host-issued result; callers cannot promote a bare PASS string."""

    payload: dict[str, Any]
    signature: str


@dataclass(frozen=True)
class Evidence:
    claim_fingerprint: str
    formalization_fingerprint: str
    environment_fingerprint: str
    level: EvidenceLevel
    axioms: tuple[str, ...]
    artifact_directory: str
    receipt_digest: str
    policy_digest: str
    schema_version: str = "1"


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    run_status: RunStatus
    math_verdict: Verdict
    evidence_level: EvidenceLevel
    reason: str
    artifact_directory: str
    schema_version: str = "1"
