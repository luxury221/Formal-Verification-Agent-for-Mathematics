"""Explicit, locally reviewed return relations for actual Studio transfers.

This additive adapter preserves the original equivalent-return API. It does not
translate mathematical statements or grant evidence; verifier-owned identities
and the immutable article ledger remain authoritative.
"""

from copy import deepcopy
import uuid

from .article_audit import annotation_identity, evidence_identity
from .article_workflow import ArticleWorkflow
from .domain import digest
from .formalizer import exact_object
from .model_service import text_field
from .tasks import _now


RELATIONS = ("equivalent", "counterexample_specialization")


class ArticleResearchReturns(ArticleWorkflow):
    """Add explicit specialization returns without changing legacy records."""

    def _candidate(self, key, data):
        row, _, _ = self.articles.read(key)
        workspace = self.workspace(row, data["workspace_id"])
        if self.workspace_state(row, workspace)["status"] != "CURRENT":
            raise ValueError("Research task no longer corresponds to this exact obligation")
        study = self.studio.read(workspace["study_id"])
        transfer = next(
            (item for item in study["transfers"] if item["graph_id"] == data["graph_id"]),
            None,
        )
        if transfer is None:
            raise ValueError("Select a verifier task actually transferred from this notebook")
        attempt = self.studio.round(study, transfer["round_id"])
        proposal = attempt["proposal"]
        research_node = self.studio.node(study, proposal["node_id"])
        formal = proposal["formalization"]
        node = next(
            (node for node in formal["request"]["nodes"] if node["id"] == data["node_id"]),
            None,
        )
        if (
            transfer["backend"] != formal["backend"]
            or node is None
            or node["kind"] != "problem"
            or node["target"]["original"] != research_node["statement"]
        ):
            raise ValueError("Select the exact problem target transferred from this research step")
        return row, workspace, study, transfer, research_node

    def evidence_preview(self, key, data):
        if "relation" not in data:
            return super().evidence_preview(key, data)
        exact_object(
            data,
            {"workspace_id", "graph_id", "node_id", "relation"},
            "Preview an explicitly related research result",
        )
        if data["relation"] not in RELATIONS:
            raise ValueError("Choose the exact source relation")
        for name in ("workspace_id", "graph_id", "node_id"):
            text_field(data[name], name, 200)
        row, workspace, study, transfer, research_node = self._candidate(key, data)
        original = row["annotations"][workspace["block_id"]]["statement"]
        if data["relation"] == "equivalent":
            if research_node["statement"] != original:
                raise ValueError("Equivalent return must preserve the complete original obligation")
        elif (
            research_node["kind"] not in ("lemma", "subproblem")
            or research_node["statement"] == original
        ):
            raise ValueError("A specialization must be a distinct explicit lemma or subproblem")
        checked = self.articles.reporter(transfer["backend"], transfer["graph_id"])
        if checked["plan"]["plan_fingerprint"] != transfer["plan_fingerprint"]:
            raise ValueError("Transferred formal task identity changed")
        identity = evidence_identity(checked, data["node_id"])
        if identity["original"] != research_node["statement"]:
            raise ValueError("Checked target differs from the exact transferred research step")
        if self.studio.read(workspace["study_id"])["revision"] != study["revision"]:
            raise ValueError("Research notebook changed during evidence review; preview again")
        value = {
            "schema": "article-research-return-v2",
            "article_id": key,
            "article_revision": row["revision"],
            "workspace_id": workspace["workspace_id"],
            "block_id": workspace["block_id"],
            "study_id": workspace["study_id"],
            "study_revision": study["revision"],
            "backend": transfer["backend"],
            "graph_id": transfer["graph_id"],
            "node_id": data["node_id"],
            "relation": data["relation"],
            "identity": identity,
            "original": original,
            "assumptions": deepcopy(row["annotations"][workspace["block_id"]]["assumptions"]),
            "research_node": deepcopy(research_node),
            "study_source": deepcopy(study["source"]),
            "scope": (
                "Exact complete obligation; source correspondence still requires local semantic review"
                if data["relation"] == "equivalent"
                else "Locally asserted counterexample specialization only; does not certify or formally refute the complete obligation"
            ),
            "semantic_responsibility": "A named local reviewer must establish the mathematical relation; an arbitrary proved subproblem is not automatically a counterexample",
        }
        value["preview_fingerprint"] = digest(value)
        return value

    def bind_workspace(self, key, data):
        if "relation" not in data:
            return super().bind_workspace(key, data)
        exact_object(
            data,
            {
                "request_id",
                "expected_revision",
                "workspace_id",
                "graph_id",
                "node_id",
                "relation",
                "preview_fingerprint",
                "reviewer",
                "scope",
                "confirmed",
            },
            "Return an explicitly related research result",
        )
        text_field(data["reviewer"], "Local semantic reviewer", 200)
        text_field(data["scope"], "Source correspondence and limitations", 4000)
        if data["confirmed"] is not True:
            raise ValueError(
                "Confirm the source, all hypotheses, exact target and selected relation"
            )

        def action(row, inventory):
            preview = self.evidence_preview(
                key,
                {name: data[name] for name in ("workspace_id", "graph_id", "node_id", "relation")},
            )
            if preview["preview_fingerprint"] != data["preview_fingerprint"]:
                raise ValueError(
                    "Evidence preview changed; review the current complete target again"
                )
            if len(row["links"]) >= 64:
                raise ValueError("Article evidence-reference budget exhausted")
            block_id = preview["block_id"]
            row["links"].append(
                {
                    "link_id": "articlelink-" + uuid.uuid4().hex,
                    "block_id": block_id,
                    "backend": preview["backend"],
                    "graph_id": data["graph_id"],
                    "node_id": data["node_id"],
                    "relation": data["relation"],
                    "scope": data["scope"],
                    "reviewer": data["reviewer"],
                    "source_fingerprint": row["source_fingerprint"],
                    "annotation_identity": annotation_identity(row, block_id),
                    "identity": preview["identity"],
                    "confirmed_at": _now(),
                    "authentication": "local-assertion-only",
                    "workspace_id": data["workspace_id"],
                }
            )

        return self.enrich(self.articles.change(key, data, action))

    def research_tasks(self, key, workspace_id):
        result = super().research_tasks(key, workspace_id)
        row, _, _ = self.articles.read(key)
        workspace = self.workspace(row, workspace_id)
        study = self.studio.read(workspace["study_id"])
        original = row["annotations"][workspace["block_id"]]["statement"]
        for transfer in result["transfers"]:
            transfer["candidate_nodes"] = []
            try:
                attempt = self.studio.round(study, transfer["round_id"])
                research_node = self.studio.node(study, attempt["proposal"]["node_id"])
                formal = attempt["proposal"]["formalization"]
                if transfer["backend"] != formal["backend"]:
                    continue
                for node in formal["request"]["nodes"]:
                    if (
                        node["kind"] != "problem"
                        or node["target"]["original"] != research_node["statement"]
                    ):
                        continue
                    relation = (
                        "equivalent"
                        if research_node["statement"] == original
                        else "counterexample_specialization"
                    )
                    if relation != "equivalent" and research_node["kind"] not in (
                        "lemma",
                        "subproblem",
                    ):
                        continue
                    transfer["candidate_nodes"].append(
                        {
                            "node_id": node["id"],
                            "research_node_id": research_node["id"],
                            "original": research_node["statement"],
                            "relations": [relation],
                        }
                    )
            except (ValueError, KeyError, TypeError):
                transfer["candidate_nodes"] = []
        return result
