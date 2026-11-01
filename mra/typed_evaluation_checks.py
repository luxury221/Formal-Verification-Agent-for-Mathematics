"""Private evaluation adapters; use the unchanged real candidate and replay executors."""

from copy import deepcopy

from .domain import digest
from .library_composition import LibraryCompositionStore
from .mathlib_lab import MathlibLabStore
from .proof_library import proof_data, provenance
from .reuse_evaluation_checks import initialize, ImportChecks as PriorImportChecks
from .typed_definitions import prepare
from .typed_evaluation_terms import (
    ALIAS,
    ITEM,
    IMPORT_MODE,
    INLINE_MODE,
    compile_program,
    source_program,
)
from .typed_reuse import checked
from .typed_terms import MODE


def manifest(root):
    from .typed_reuse_evaluation import context

    return context(root)


def finish(root, compiled, **extra):
    value = {
        **compiled,
        **extra,
        "source_manifest": manifest(root),
        "budget": {
            "candidate_processes": 1,
            "replay_processes": 1,
            "shared_worker_seconds": 30,
            "integrity_time_separate": True,
            "automatic_retries": 0,
        },
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "model_calls": 0,
        "limitations": [
            "Authored visible fixed programs; no independent research or model solve score.",
            "Private evaluation only; no original research graph admission or promotion.",
            "Full target and every original premise require candidate and independent replay.",
            "Same pinned Lean kernel implementation; local reviewer names are not authenticated.",
        ],
    }
    value.pop("plan_fingerprint", None)
    value["plan_fingerprint"] = digest(value)
    return value


def direct_gate(store, row, check_files):
    if row["plan"]["source_manifest"] != manifest(store.root):
        return "Typed paired dataset, programs, budgets, implementation or runtime changed"
    view = deepcopy(row)
    view["plan"]["source_manifest"] = row["plan"]["source_manifest"]["typed_provider"]["executor"]
    return MathlibLabStore.gate(store, view, check_files)


class DirectChecks(MathlibLabStore):
    def __init__(self, root, directory, create=False):
        initialize(self, root, directory, "direct-checks", create)

    def preview(self, request):
        if request.get("schema") == MODE:
            return finish(
                self.root, {**prepare(self.root, request), "proof_source": source_program(request)}
            )
        if request.get("schema") != INLINE_MODE:
            raise ValueError("Only typed source or explicit inline evaluation programs")
        return finish(self.root, compile_program(request))

    def gate(self, row, check_files=True):
        return direct_gate(self, row, check_files)


class ImportChecks(PriorImportChecks):
    """Reuse the import executor but bind a private same-evaluation typed source."""

    def load_source(self, binding):
        if binding != {"alias": ALIAS, "item_id": ITEM} or self.source_id is None:
            raise ValueError("No matching same-evaluation typed prerequisite")
        row = self.direct.report(self.source_id)
        checked(row)
        name = "replay/proof.ndjson"
        raw = proof_data(self.direct.directory(self.source_id) / name, row["files"][name])
        fingerprint = digest(
            {"plan": row["plan"], "files": row["files"], "alignment": row["alignment"]}
        )
        return {
            **binding,
            "origin": "private-typed-evaluation",
            "version": 1,
            "archive_fingerprint": fingerprint,
            "source_fingerprint": row["submission_fingerprint"],
            "request": deepcopy(row["plan"]["request"]),
            "formal_statement": row["plan"]["formal_statement"],
            "alignment": deepcopy(row["alignment"]),
            "data_sha256": row["files"][name],
            "data_bytes": len(raw),
            "depth": 0,
            "ancestors": [{"item_id": ITEM, "version": 1, "archive_fingerprint": fingerprint}],
        }, raw

    def preview(self, request):
        if request.get("schema") != IMPORT_MODE:
            raise ValueError("Use the private typed import evaluation program")
        compiled = compile_program(request)
        source = self.load_source({"alias": ALIAS, "item_id": ITEM})[0]
        if (
            source["request"] != request["source"]
            or source["formal_statement"]
            != prepare(self.root, request["source"])["formal_statement"]
        ):
            raise ValueError("Reviewed complete source differs from the checked archive")
        return finish(self.root, compiled, source_items=[source], provenance=provenance([source]))

    def gate(self, row, check_files=True):
        if self.damage_marker("runs", row["run_id"]):
            return "Previously detected damaged private evaluation import ledger"
        sources = [self.load_source({"alias": ALIAS, "item_id": ITEM})[0]]
        if (
            sources != row["plan"]["source_items"]
            or provenance(sources) != row["plan"]["provenance"]
        ):
            return "Current complete typed source or provenance changed"
        return direct_gate(self, row, check_files)

    execute = LibraryCompositionStore.execute
