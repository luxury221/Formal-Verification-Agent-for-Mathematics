"""Private strategy-check storage; the original structure executor and replay stay unchanged."""

from copy import deepcopy
from .domain import digest
from .reuse_evaluation_checks import initialize
from .structures import StructureStore, prepare
from .structure_reuse import checked
from .mathlib_lab import POLICY


class Checks(StructureStore):
    def __init__(self, root, directory, create=False):
        initialize(self, root, directory, "checks", create)

    def preview(self, request):
        from .structure_strategy_evaluation import context

        value = prepare(self.root, request)
        value["source_manifest"] = {
            "provider": value["source_manifest"],
            "evaluation": context(self.root),
        }
        value.pop("plan_fingerprint")
        value["plan_fingerprint"] = digest(value)
        return value

    def gate(self, row, check_files=True):
        from .structure_strategy_evaluation import context

        if row["plan"]["source_manifest"]["evaluation"] != context(self.root):
            return "Strategy dataset, saved programs, evaluator, generator or runtime changed"
        view = deepcopy(row)
        view["plan"]["source_manifest"] = row["plan"]["source_manifest"]["provider"]
        return StructureStore.gate(self, view, check_files)


def verified(row):
    """Only intact full-type, two-process, approved-axiom evidence can stop a search arm."""
    checked(row)
    for phase in ("candidate", "replay"):
        audit = row["result"][phase]["audit"]
        if (
            audit["local_axioms"]
            or not set(audit["axioms"]) <= set(POLICY["allowed_axioms"])
            or not set(audit["local_dependency_axioms"]) <= set(POLICY["allowed_axioms"])
        ):
            raise ValueError("Unapproved axiom in strategy checking evidence")
