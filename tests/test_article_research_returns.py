"""Return-relation contracts using labelled transport fixtures, never real Lean."""

from copy import deepcopy
import unittest

from mra.article_research_returns import ArticleResearchReturns
import test_article_audit as audit_tests
import test_article_workflow as workflow_tests
from test_article_audit import fixture_report


class ArticleResearchReturnTests(unittest.TestCase):
    mutation = audit_tests.ArticleAuditTests.mutation
    annotate = audit_tests.ArticleAuditTests.annotate
    workspace = workflow_tests.ArticleWorkflowTests.workspace
    fixture_transfer = workflow_tests.ArticleWorkflowTests.fixture_transfer
    return_data = workflow_tests.ArticleWorkflowTests.return_data

    def setUp(self):
        audit_tests.ArticleAuditTests.setUp(self)
        self.flow = ArticleResearchReturns(self.store)

    def specialization(self):
        data = self.fixture_transfer()
        article, _, _ = self.store.read(self.key)
        workspace = self.flow.workspace(article, data["workspace_id"])
        studio = self.flow.studio
        study = studio.report(workspace["study_id"])
        plan = deepcopy(study["plan"])
        plan["steps"][0]["depends_on"] = ["witness"]
        plan["steps"].insert(
            0,
            {
                "id": "witness",
                "kind": "subproblem",
                "title": "Explicit local witness",
                "statement": "Restricted witness only",
                "depends_on": [],
                "approach": "Investigate an explicitly restricted case; relation still needs review",
                "additional_assumptions": ["Specialization parameters are explicit"],
            },
        )
        study = studio.plan(
            study["study_id"],
            {
                "request_id": "fixture-plan-witness",
                "expected_revision": study["revision"],
                "plan": plan,
            },
        )
        study = studio.add_manual_round(
            study["study_id"],
            {
                "request_id": "fixture-round-witness",
                "expected_revision": study["revision"],
                "proposal": {
                    "source_fingerprint": study["source_fingerprint"],
                    "plan_fingerprint": study["plan_fingerprint"],
                    "node_id": "witness",
                    "reasoning": "Labelled protocol fixture; no real mathematical execution",
                    "gaps": ["Semantic source relation requires a local named reviewer"],
                    "next_actions": [],
                    "formalization": {
                        "backend": "proof",
                        "request": {
                            "nodes": [
                                {
                                    "id": "root",
                                    "kind": "problem",
                                    "target": {"original": "Restricted witness only"},
                                }
                            ]
                        },
                    },
                },
            },
        )
        row = studio.read(study["study_id"])
        row["transfers"].append(
            {
                "round_id": study["rounds"][-1]["round_id"],
                "backend": "proof",
                "graph_id": "fixture-witness",
                "plan_fingerprint": "fixture-plan",
                "href": "/fixture",
                "reviewer": "fixture",
                "at": "fixture",
            }
        )
        studio.save(row, "LABELLED_FIXTURE_TRANSFER")
        self.evidence = fixture_report(original="Restricted witness only")
        return {**data, "graph_id": "fixture-witness", "relation": "counterexample_specialization"}

    def body(self, preview):
        return self.return_data(preview, relation=preview["relation"])

    def test_legacy_equivalent_request_remains_compatible(self):
        data = self.fixture_transfer()
        preview = self.flow.evidence_preview(self.key, data)
        self.assertNotIn("relation", preview)
        self.flow.bind_workspace(self.key, self.return_data(preview))
        row, _, _ = self.store.read(self.key)
        self.assertEqual(row["links"][0]["relation"], "equivalent")

    def test_explicit_equivalent_keeps_original_obligation_gate(self):
        data = {**self.fixture_transfer(), "relation": "equivalent"}
        preview = self.flow.evidence_preview(self.key, data)
        self.assertEqual(preview["relation"], "equivalent")
        self.assertEqual(preview["original"], preview["identity"]["original"])
        self.flow.bind_workspace(self.key, self.body(preview))
        self.assertEqual(self.store.read(self.key)[0]["links"][0]["relation"], "equivalent")

    def test_specialization_preserves_original_and_returns_only_explicit_finding(self):
        data = self.specialization()
        tasks = self.flow.research_tasks(self.key, data["workspace_id"])
        self.assertEqual(tasks["transfers"][-1]["eligible_node_ids"], [])
        node = tasks["transfers"][-1]["candidate_nodes"][0]
        self.assertEqual(node["relations"], ["counterexample_specialization"])
        preview = self.flow.evidence_preview(self.key, data)
        self.assertEqual(preview["original"], "Exact step")
        self.assertEqual(preview["identity"]["original"], "Restricted witness only")
        self.assertEqual(
            preview["research_node"]["additional_assumptions"],
            ["Specialization parameters are explicit"],
        )
        body = self.body(preview)
        self.flow.bind_workspace(self.key, body)
        first, _, _ = self.store.read(self.key)
        self.flow.bind_workspace(self.key, body)
        second, _, _ = self.store.read(self.key)
        self.assertEqual(first, second)
        self.assertEqual(second["annotations"]["step1"]["statement"], "Exact step")
        self.assertEqual(second["links"][0]["relation"], "counterexample_specialization")
        result = self.flow.report(self.key, recheck=True)
        block = next(b for b in result["blocks"] if b["id"] == "step1")
        self.assertEqual(block["claim_verdict"], "UNKNOWN")
        self.assertEqual(block["evidence_level"], "E0")
        self.assertEqual(block["finding"], "CHECKED_COUNTEREXAMPLE_SPECIALIZATION")
        self.assertFalse(result["whole_article_verified"])

    def test_specialization_cannot_be_relabeled_equivalent_or_use_unrelated_graph(self):
        data = self.specialization()
        for changes in (
            {"relation": "equivalent"},
            {"graph_id": "unrelated"},
            {"node_id": "other"},
        ):
            with self.assertRaises(ValueError):
                self.flow.evidence_preview(self.key, {**data, **changes})
        preview = self.flow.evidence_preview(self.key, data)
        with self.assertRaises(ValueError):
            self.flow.bind_workspace(self.key, {**self.body(preview), "relation": "equivalent"})

    def test_missing_confirmation_reviewer_scope_or_current_evidence_rejects(self):
        data = self.specialization()
        preview = self.flow.evidence_preview(self.key, data)
        body = self.body(preview)
        for change in ({"confirmed": False}, {"confirmed": 1}, {"reviewer": ""}, {"scope": ""}):
            with self.assertRaises(ValueError):
                self.flow.bind_workspace(self.key, {**body, **change})
        self.evidence["checks"]["fixture-attempt"]["invalidated"] = True
        with self.assertRaises(ValueError):
            self.flow.bind_workspace(self.key, body)
        self.assertEqual(self.store.read(self.key)[0]["links"], [])

    def test_source_plan_and_proof_changes_invalidate_preview(self):
        data = self.specialization()
        preview = self.flow.evidence_preview(self.key, data)
        self.evidence["node_states"]["root"]["source_fingerprint"] = "changed-proof"
        with self.assertRaisesRegex(ValueError, "preview changed"):
            self.flow.bind_workspace(self.key, self.body(preview))
        self.evidence = fixture_report(original="Restricted witness only")
        study = self.flow.studio.report(preview["study_id"])
        plan = deepcopy(study["plan"])
        plan["steps"][0]["approach"] = "A different explicit route"
        self.flow.studio.plan(
            study["study_id"],
            {
                "request_id": "fixture-new-plan",
                "expected_revision": study["revision"],
                "plan": plan,
            },
        )
        self.assertEqual(
            self.flow.research_tasks(self.key, data["workspace_id"])["transfers"][-1][
                "candidate_nodes"
            ],
            [],
        )
        with self.assertRaises(ValueError):
            self.flow.evidence_preview(self.key, data)

    def test_article_edit_after_preview_rejects_return_without_adding_link(self):
        data = self.specialization()
        preview = self.flow.evidence_preview(self.key, data)
        self.annotate(statement="A changed complete obligation")
        with self.assertRaisesRegex(ValueError, "no longer corresponds"):
            self.flow.bind_workspace(self.key, self.body(preview))
        self.assertEqual(self.store.read(self.key)[0]["links"], [])

    def test_notebook_edit_during_provider_read_requires_new_preview(self):
        data = self.specialization()
        workspace = self.flow.workspace(self.store.read(self.key)[0], data["workspace_id"])

        def changing_reporter(backend, graph):
            row = self.flow.studio.read(workspace["study_id"])
            self.flow.studio.save(row, "LABELLED_FIXTURE_CONCURRENT_EDIT")
            return fixture_report(original="Restricted witness only")

        self.store.reporter = changing_reporter
        with self.assertRaisesRegex(ValueError, "changed during"):
            self.flow.evidence_preview(self.key, data)


if __name__ == "__main__":
    unittest.main()
