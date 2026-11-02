"""Scripted model protocol fixtures. Not evidence of actual LLM reading quality."""

from copy import deepcopy
import json

from mra.domain import digest
from mra.model_service import ModelIdentity, ModelResponse, TokenUsage
from mra.paper_protocol import AUDIT_CHECKS

HTML = '<article><p>Let n denote a natural number.</p><div class="ltx_theorem"><p>For every natural number n, n+0=n.</p></div><p>Proof. n+0=n by definition of addition.</p><p>End of this owned example.</p></article>'
CONFIG = {
    "endpoint": "http://127.0.0.1:9/v1/chat/completions",
    "model": "scripted-software-fixture",
    "revision": "fixture-1",
    "api_key_env": "MRA_PAPER_TEST_KEY",
    "token_field": "max_tokens",
}


def content():
    return {
        "title": "自然数加零",
        "kind": "claim",
        "sources": [{"block_id": "block-0001", "role": "statement"}],
        "objects": "n : Nat",
        "definitions": "自然数加法",
        "quantifiers": "对任意自然数 n",
        "assumptions": [],
        "conclusion": "n+0=n",
        "references": [],
        "depends_on": [],
        "questions": [],
    }


def candidate():
    return {
        "schema": "typed-definitions-v1",
        "definitions": [],
        "variables": [{"name": "n", "type": "Nat"}],
        "assumptions": [],
        "goal": {"eq": [{"add": [{"var": "n"}, {"nat": 0}]}, {"var": "n"}]},
        "strategy": "rfl",
    }


def reading(packet):
    rows, obligations = [], []
    for unit in packet["focus"]:
        ids = []
        if unit["kind"] in ("claim", "proof") or "n+0=n" in unit["text"]:
            key = "o" + str(len(obligations) + 1)
            ids.append(key)
            c = content()
            obligations.append(
                {k: deepcopy(v) for k, v in c.items() if k not in ("sources", "depends_on")}
            )
            obligations[-1].update(
                local_id=key,
                kind="author_step" if unit["kind"] == "proof" else "claim",
                quotes=[
                    {
                        "block_id": unit["block_id"],
                        "span": unit["span"],
                        "quote": unit["text"],
                        "role": "statement",
                    }
                ],
                dependency_blocks=[],
            )
        rows.append(
            {
                "unit_id": unit["id"],
                "disposition": "REQUIRES_PROOF" if ids else "CONTEXT",
                "reason": "Scripted fixture classification; not a semantic-quality result",
                "obligations": ids,
            }
        )
    return {
        "source_fingerprint": packet["source_fingerprint"],
        "coverage": rows,
        "obligations": obligations,
        "required_context": [],
        "issues": [],
    }


def audit(packet):
    result = {
        "source_fingerprint": packet["source_fingerprint"],
        "proposal_fingerprint": packet["proposal_fingerprint"],
        "decision": "ACCEPT",
        "checks": dict.fromkeys(AUDIT_CHECKS, True),
        "issues": [],
        "required_context": [],
    }
    if "alignment_requirements" in packet:
        result["alignments"] = []
        counts = {}
        for row in packet["alignment_requirements"]:
            paths = row["formal_paths"]
            if row["kind"] in ("assumptions", "conclusion"):
                index = counts.get(row["kind"], 0)
                counts[row["kind"]] = index + 1
                paths = paths[index : index + 1]
            result["alignments"].append(
                {
                    "id": row["id"],
                    "source_refs": deepcopy(row["origins"][:1]),
                    "formal_paths": deepcopy(paths),
                    "relation": "MATCH",
                    "explanation": "Scripted correspondence fixture; not evidence of mathematical interpretation quality",
                }
            )
    return result


class FixtureService:
    seen = []
    unsupported = False

    def __init__(self, *_args, **_kwargs):
        pass

    def generate(self, request):
        packet = json.loads(request.input_json)
        type(self).seen.append((request.task, deepcopy(packet)))
        if request.task == "paper_scan":
            output = reading(packet)
        elif request.task == "paper_audit":
            output = audit(packet)
        else:
            output = {
                "source_fingerprint": packet["source_fingerprint"],
                "obligation_fingerprint": packet["obligation_fingerprint"],
                "status": "UNSUPPORTED" if self.unsupported else "CANDIDATE",
                "profile": None if self.unsupported else "typed",
                "request": None if self.unsupported else candidate(),
                "questions": ["Fixture configured to leave a gap"] if self.unsupported else [],
                "required_context": [],
            }
        return ModelResponse(
            request.fingerprint,
            ModelIdentity("scripted-local", "fixture", "fixture-1", "fixture"),
            json.dumps(output, ensure_ascii=False),
            TokenUsage(10, 10),
        )


def unavailable_kernel(_root, directory, value, **_kwargs):
    directory.mkdir(parents=True, exist_ok=False)
    return {
        "candidate_fingerprint": value["candidate_fingerprint"],
        "kernel_status": "UNKNOWN",
        "phases": {},
        "worker_seconds": 0,
        "diagnostic": "Explicit software fixture; no Lean proof generated",
        "files": {},
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "human_confirmation": None,
    }


def source(text="For every natural number n, n+0=n.", kind="claim"):
    return {
        "blocks": [
            {
                "id": "block-0001",
                "kind": kind,
                "text": text,
                "source": "owned.html",
                "dependencies": [],
            }
        ]
    }


def fingerprint(value):
    return digest(value)
