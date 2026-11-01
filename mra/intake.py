"""Local fixture preview, content-bound bundles and review ledger; no Lean execution."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import os
from pathlib import Path
import uuid

from .domain import RunStatus, canonical
from .formalizer import (
    CandidateReview,
    DraftStatus,
    FormalizationDraft,
    SourceClaim,
    exact_object,
    parse_output,
    strict_json,
)
from .model_service import ModelBudget, ModelIdentity, ModelRequest, ModelResponse, TokenUsage
from .reviews import ReviewLedger


class FixtureModelService:
    """Exact repository examples, deliberately identified as fixtures, never an LLM."""

    def __init__(self, path: Path) -> None:
        raw = path.read_bytes()
        self.examples = strict_json(raw.decode("utf-8"))["examples"]
        self.identity = ModelIdentity(
            "repository", "formalization-fixtures", hashlib.sha256(raw).hexdigest(), "fixture"
        )

    def generate(self, request: ModelRequest) -> ModelResponse:
        data = strict_json(request.input_json)
        source = SourceClaim.from_dict(data["source"])
        output = {
            "schema_version": "1",
            "source_fingerprint": source.fingerprint,
            "status": "UNSUPPORTED",
            "candidates": [],
            "questions": [],
        }
        if request.task == "formalize_claim":
            for example in self.examples:
                if (source.statement, list(source.context)) == (
                    example["statement"],
                    example["context"],
                ):
                    output.update({k: example[k] for k in ("status", "candidates", "questions")})
                    break
        return ModelResponse(
            request.fingerprint, self.identity, canonical(output).decode("utf-8"), TokenUsage(0, 0)
        )


def restore_draft(data: dict) -> FormalizationDraft:
    source = SourceClaim.from_dict(data["source"])
    request_data = data["request"]
    exact_object(request_data, {"id", "task", "input_json", "budget", "output_schema"}, "request")
    request = ModelRequest(
        request_data["id"],
        request_data["task"],
        request_data["input_json"],
        ModelBudget(**request_data["budget"]),
        request_data["output_schema"],
    )
    if strict_json(request.input_json) != {
        "source": strict_json(canonical(asdict(source)).decode()),
        "source_fingerprint": source.fingerprint,
    }:
        raise ValueError("Stored request and source version differ")
    producer = ModelIdentity(**data["producer"]) if data["producer"] else None
    usage = TokenUsage(**data["usage"]) if data["usage"] else None
    status, run_status = DraftStatus(data["status"]), RunStatus(data["run_status"])
    candidates, questions = (), ()
    if status in (
        DraftStatus.CANDIDATES_READY,
        DraftStatus.NEEDS_CLARIFICATION,
        DraftStatus.UNSUPPORTED,
    ):
        if producer is None or usage is None or run_status != RunStatus.COMPLETED:
            raise ValueError("Missing producer, usage, or completed status")
        parsed_status, candidates, questions = parse_output(data["raw_response"], source, producer)
        if parsed_status != status:
            raise ValueError("Stored status contradicts the producer response")
    draft = FormalizationDraft(
        source,
        request,
        status,
        run_status,
        candidates,
        questions,
        producer,
        usage,
        data["raw_response"],
        data["reason"],
    )
    if canonical(draft.to_dict()) != canonical(data):
        raise ValueError("Stored draft metadata/fingerprints were changed")
    return draft


class IntakeStore:
    def __init__(self, root: Path) -> None:
        self.base = root.resolve() / "artifacts/formalization"
        self.ledger = ReviewLedger(self.base, self.load)

    def save(self, draft: FormalizationDraft, previous: Path | None = None) -> Path:
        if not self.base.resolve().is_relative_to(self.base.parents[1]):
            raise ValueError("Formalization storage redirects outside the project")
        directory = self.base / uuid.uuid4().hex
        directory.mkdir(parents=True)
        files = {}
        for name, value in (("source.json", asdict(draft.source)), ("draft.json", draft.to_dict())):
            raw = canonical(value) + b"\n"
            with (directory / name).open("xb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            files[name] = hashlib.sha256(raw).hexdigest()
        with (directory / "manifest.json").open("xb") as output:
            output.write(canonical({"schema_version": "1", "files": files}) + b"\n")
            output.flush()
            os.fsync(output.fileno())
        self.register(directory, previous)
        return directory

    def load(self, directory: Path) -> FormalizationDraft:
        if directory.is_symlink():
            raise ValueError("Formalization bundle may not be a symlink")
        directory = directory.resolve()
        if (
            not self.base.resolve().is_relative_to(self.base.parents[1])
            or not directory.is_relative_to(self.base)
            or directory == self.base
        ):
            raise ValueError("Draft must be inside this project's artifacts/formalization")
        if (directory / "manifest.json").is_symlink():
            raise ValueError("Formalization manifest may not be a symlink")
        manifest = strict_json((directory / "manifest.json").read_text(encoding="utf-8"))
        exact_object(manifest, {"schema_version", "files"}, "manifest")
        if manifest["schema_version"] != "1" or set(manifest["files"]) != {
            "source.json",
            "draft.json",
        }:
            raise ValueError("Incomplete formalization artifact bundle")
        loaded = {}
        for name, expected in manifest["files"].items():
            path = directory / name
            if path.is_symlink() or not path.resolve().is_relative_to(directory):
                raise ValueError("Formalization artifact redirects outside its bundle")
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError("Formalization artifact changed")
            loaded[name] = strict_json(raw.decode("utf-8"))
        draft = restore_draft(loaded["draft.json"])
        if canonical(asdict(draft.source)) != canonical(loaded["source.json"]):
            raise ValueError("Source artifact and draft differ")
        return draft

    def register(self, directory: Path, previous: Path | None = None) -> None:
        self.ledger.register(directory, previous)

    def report(self, directory: Path) -> dict:
        return self.ledger.report(directory)

    def _export_event(self, directory: Path, event: dict) -> Path:
        destination = directory.resolve() / "reviews" / (event["event_id"] + ".json")
        if destination.parent.is_symlink():
            raise ValueError("Review storage may not be a symlink")
        destination.parent.mkdir(exist_ok=True)
        temporary = destination.parent / (".export-" + uuid.uuid4().hex)
        try:
            with temporary.open("xb") as output:
                output.write(canonical(event) + b"\n")
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def review(self, directory: Path, review: CandidateReview, *, event_id=None) -> Path:
        event = self.ledger.record(directory, review, event_id=event_id)
        return self._export_event(directory, event)

    def revoke(
        self,
        directory: Path,
        *,
        target: str,
        reviewer: str,
        note: str,
        draft_fingerprint: str,
        candidate_fingerprint: str,
        event_id=None,
    ) -> Path:
        event = self.ledger.record(
            directory,
            target=target,
            reviewer=reviewer,
            note=note,
            draft_fingerprint=draft_fingerprint,
            candidate_fingerprint=candidate_fingerprint,
            event_id=event_id,
        )
        return self._export_event(directory, event)
