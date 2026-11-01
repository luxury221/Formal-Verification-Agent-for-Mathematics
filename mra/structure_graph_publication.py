"""Publish complete graph directories without exposing an unfinished ledger to readers."""

import os
import uuid

from .domain import digest
from .formalizer import exact_object
from .mathlib_lab import now
from .research_flow import atomic, lease
from .structure_research import ERRORS, LIMITS, SCHEMA, StructureGraphChecks, StructureResearchStore
from .tasks import _identifier


class PublishedStructureResearchStore(StructureResearchStore):
    """Creation adapter; saved mathematical plans and the original checker stay unchanged."""

    def create(self, raw):
        exact_object(
            raw, {"request_id", "request", "plan_fingerprint"}, "Save structure research graph"
        )
        _identifier(raw["request_id"])
        plan = self.preview(raw["request"])
        if raw["plan_fingerprint"] != plan["plan_fingerprint"]:
            raise ValueError("Full graph preview changed; review all definitions and targets again")
        with lease(self.base):
            paths = self.paths()
            for path in paths:
                try:
                    old = self.read(path.name)
                except ERRORS:
                    continue
                if old["request_id"] == raw["request_id"]:
                    if old["submission_fingerprint"] != digest(raw):
                        raise ValueError("Request ID is bound to another structure graph")
                    key = old["graph_id"]
                    break
            else:
                if len(paths) >= LIMITS["graphs"]:
                    raise ValueError("Export before saving more than fifty structure graphs")
                key = "structureflow-" + uuid.uuid4().hex
                directory = self.directory(key)
                staging = self.base / (".creating-" + key)
                if staging.resolve() != staging or directory.exists():
                    raise ValueError("Graph publication location changed")
                staging.mkdir()
                atomic(staging / "plan.json", plan)
                StructureGraphChecks(self.root, staging, create=True)
                row = {
                    "schema": SCHEMA,
                    "graph_id": key,
                    "request_id": raw["request_id"],
                    "submission_fingerprint": digest(raw),
                    "created_at": now(),
                    "plan_fingerprint": plan["plan_fingerprint"],
                    "revision": 0,
                    "invalidated": False,
                    "reason": None,
                    "attempts": [],
                    "events": [],
                }
                self.event(row, "SAVED_WITHOUT_LEAN")
                atomic(staging / "record.json", row)
                # Same-filesystem directory rename is the visibility boundary.
                # Failed preparations stay hidden for inspection; no repair revives them.
                staging.rename(directory)
                fd = os.open(self.base, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        return self.report(key)
