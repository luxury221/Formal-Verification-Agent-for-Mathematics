"""Discover implemented workflows; domain stores and checkers retain authority."""

from copy import deepcopy
from pathlib import Path

from .document_intake import capabilities as document_capabilities
from .paper_automation import capabilities as paper_capabilities
from .research_protocol import BACKENDS

SCHEMA = "product-capabilities-v1"


def capabilities(root):
    """Configuration/presence inventory, not a runtime attestation or proof report."""
    root = Path(root).resolve()
    return {
        "schema": SCHEMA,
        "deployment": "TRUSTED_LOCAL_SINGLE_USER",
        "runtime_assurance": "CONFIGURATION_AND_FILE_PRESENCE_ONLY",
        "workers": {"document": {"state": "NOT_OBSERVED"}, "paper": {"state": "NOT_OBSERVED"}},
        "documents": {
            "http": "/v1/documents",
            "ui": "/documents",
            "tools": [
                "document.capabilities",
                "document.import",
                "document.list",
                "document.status",
                "document.draft",
                "document.cancel",
            ],
            "capabilities": document_capabilities(root),
        },
        "workflows": {
            "paper": {
                "http": "/v1/paper-automation",
                "ui": "/automation",
                "tools": [
                    "paper.capabilities",
                    "paper.create",
                    "paper.list",
                    "paper.status",
                    "paper.artifact",
                    "paper.cancel",
                    "paper.resume",
                ],
                "stages": [
                    "source_extraction",
                    "reading",
                    "reading_audit",
                    "formalization",
                    "alignment_audit",
                    "kernel_check_and_replay",
                    "bounded_repair",
                    "report",
                ],
                "capabilities": paper_capabilities(root),
                "requires_external_worker": True,
                "original_math_verdict": "UNKNOWN",
                "original_evidence_level": "E0",
                "coverage_scope": "EXTRACTED_TEXT_ONLY",
            },
            "research": {
                "http": "/v1/studio",
                "ui": "/studio",
                "tools": [
                    "study.create",
                    "study.list",
                    "study.scaffold",
                    "study.report",
                    "mathlib.search",
                    "formalization.preview",
                    "study.transfer_preview",
                    "study.transfer",
                    "study.evidence",
                    "verification.preview",
                    "verification.status",
                    "verification.submit",
                    "verification.cancel",
                ],
                "backends": deepcopy(BACKENDS),
                "requires_operator_semantic_review": True,
                "requires_external_verification_worker": True,
                "model_generation_tool_available": False,
                "proposal_recording_tool_available": False,
            },
        },
        "authority": {
            "source_versions": "document_intake / research_studio",
            "budgets_and_recovery": "existing domain stores and workers",
            "mathematical_evidence": "domain verifier and evidence policy",
            "tool_call_success_is_proof": False,
            "model_audit_is_human_confirmation": False,
        },
        "extensions": {
            "models": "Existing ModelService request/response/budget protocol",
            "orchestrators": "product-tools-v1 local JSON; optional external process",
            "mathematical_languages": "Explicit reviewed compilers and adopted runtime profiles",
        },
        "not_implemented": [
            "openJiuwen SDK/runtime adapter",
            "unrestricted mathematical languages and autonomous solving",
            "certification of all reasoning in arbitrary papers",
            "evaluation-gated automatic learning and model release",
            "authenticated multi-user or multi-tenant deployment",
        ],
    }
