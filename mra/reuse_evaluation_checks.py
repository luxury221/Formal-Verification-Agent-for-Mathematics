"""Storage adapters only: retain the frozen real checkers and evidence gates."""

from copy import deepcopy
from pathlib import Path

from .domain import digest
from .library_composition import LibraryCompositionStore
from .number_theory_lab import NumberTheoryStore
from .proof_library import proof_data
from .research_flow import atomic
from .reuse_evaluation_terms import ALIAS, ITEM
from .tasks import TaskStore


def initialize(store, root, directory, name, create):
    store.root = Path(root).resolve()
    store.base = directory / name
    store.path = store.base / "lab.sqlite3"
    store.tasks = TaskStore(store.root)
    if store.base.resolve() != store.base or store.path.is_symlink():
        raise ValueError("Evaluation check storage cannot be redirected")
    if create:
        store.base.mkdir(exist_ok=False)
        with store.transaction() as db:
            db.execute(
                "CREATE TABLE runs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL)"
            )
    elif not store.path.is_file():
        raise ValueError("Evaluation check ledger is unavailable")


class DirectChecks(NumberTheoryStore):
    def __init__(self, root, directory, create=False):
        initialize(self, root, directory, "direct-checks", create)


class ImportChecks(LibraryCompositionStore):
    def __init__(self, root, directory, direct, source_id, context, create=False):
        initialize(self, root, directory, "import-checks", create)
        self.directory_path = directory
        self.direct, self.source_id, self.context = direct, source_id, context
        self.knowledge = self

    def damage_marker(self, namespace, key, reason=None):
        path = self.directory_path / ("damaged-" + namespace + "-" + key + ".json")
        if reason is not None and not path.exists():
            atomic(path, {"reason": str(reason)[:1000]})
        return path.exists()

    def load_source(self, binding):
        if binding != {"alias": ALIAS, "item_id": ITEM} or self.source_id is None:
            raise ValueError("No matching same-evaluation prerequisite")
        row = self.direct.report(self.source_id)
        if (row["state"], row["math_verdict"], row["evidence_level"], row["invalidated"]) != (
            "COMPLETED",
            "PROVED",
            "E4",
            False,
        ):
            raise ValueError("The prerequisite has no intact committed E4 proof")
        name = "replay/proof.ndjson"
        raw = proof_data(self.direct.directory(self.source_id) / name, row["files"][name])
        fingerprint = digest(
            {"plan": row["plan"], "files": row["files"], "alignment": row["alignment"]}
        )
        return {
            **binding,
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
        value = super().preview(request)
        value["source_manifest"] = {
            "provider": value["source_manifest"],
            "evaluation": self.context,
        }
        value.pop("plan_fingerprint")
        value["plan_fingerprint"] = digest(value)
        return value

    def gate(self, row, check_files=True):
        from .reuse_evaluation import context

        if (
            self.context != context(self.root)
            or row["plan"]["source_manifest"]["evaluation"] != self.context
        ):
            return "Evaluation implementation or protocol changed"
        view = deepcopy(row)
        view["plan"]["source_manifest"] = row["plan"]["source_manifest"]["provider"]
        return super().gate(view, check_files)
