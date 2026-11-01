"""Private campaign children using frozen data import and independent replay."""

from copy import deepcopy
import time

from .domain import digest
from .library_composition import LibraryCompositionStore
from .research_flow import atomic


class CampaignSources:
    def __init__(self, campaign, key):
        self.campaign, self.key = campaign, key

    def damage_marker(self, namespace, key, reason=None):
        path = self.campaign.directory(self.key) / ("damaged-" + namespace + "-" + key + ".json")
        if reason is not None:
            atomic(path, {"reason": str(reason)[:1000]})
        return path.exists()


class CampaignChecks(LibraryCompositionStore):
    def __init__(self, campaign, key, create=False):
        self.campaign, self.key = campaign, key
        self.root = campaign.root
        self.base = campaign.directory(key) / "checks"
        self.path = self.base / "library.sqlite3"
        self.tasks = campaign.tasks
        self.knowledge = CampaignSources(campaign, key)
        self.last_guard = 0.0
        self.guard_failed = False
        if self.base.resolve() != self.base or self.path.is_symlink():
            raise ValueError("Campaign checks cannot be redirected")
        if create:
            self.base.mkdir(exist_ok=False)
            with self.transaction() as db:
                db.execute(
                    "CREATE TABLE runs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL)"
                )
        elif not self.path.is_file():
            raise ValueError("Campaign check ledger is unavailable")

    def load_source(self, binding):
        if binding["item_id"].startswith("node."):
            return self.campaign.node_source(self.key, binding)
        return self.campaign.library.load_source(binding)

    def preview(self, request):
        plan = super().preview(request)
        plan["source_manifest"] = {
            "provider": plan["source_manifest"],
            "campaign": self.campaign.manifest(),
        }
        plan.pop("plan_fingerprint")
        plan["plan_fingerprint"] = digest(plan)
        return plan

    def gate(self, row, check_files=True):
        if row["plan"]["source_manifest"]["campaign"] != self.campaign.manifest():
            return "Campaign implementation changed"
        view = deepcopy(row)
        view["plan"]["source_manifest"] = row["plan"]["source_manifest"]["provider"]
        return super().gate(view, check_files)

    def read(self, run_id, db=None):
        row = super().read(run_id, db)
        if row["state"] == "RUNNING":
            parent = self.campaign.read(self.key)
            stop = parent["state"] != "RUNNING" or parent["cancel_requested"]
            stop = stop or self.campaign.expired(parent)
            if not stop and time.monotonic() - self.last_guard >= 1:
                self.last_guard = time.monotonic()
                # Reading the parent re-audits completed prerequisites, never this running child.
                report = self.campaign.report(self.key)
                node = next(n for n in report["nodes"] if n["child_id"] == run_id)
                self.guard_failed = report["invalidated"] or node["invalidated"]
            if stop or self.guard_failed:
                row["cancel_requested"] = True
        return row
