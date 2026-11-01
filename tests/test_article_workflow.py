"""Lifecycle/HTTP contracts. The labelled reporter fixture is not Lean proof evidence."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from mra.article_workflow import ArticleWorkflow
from mra.research_flow import atomic
import test_article_audit as audit_tests
from test_article_audit import FORMAL, fixture_report
from test_http_api import request


class ArticleWorkflowTests(unittest.TestCase):
    mutation = audit_tests.ArticleAuditTests.mutation
    annotate = audit_tests.ArticleAuditTests.annotate
    bind = audit_tests.ArticleAuditTests.bind
    block = audit_tests.ArticleAuditTests.block

    def setUp(self):
        audit_tests.ArticleAuditTests.setUp(self)
        self.flow = ArticleWorkflow(self.store)

    def workspace(self, block_id="step1", **extra):
        return self.flow.create_workspace(
            self.key,
            self.mutation(
                block_id=block_id, area="实分析与复分析", context="Exact local definitions", **extra
            ),
        )

    def fixture_transfer(self):
        """Injected transport fixture; it does not run a real backend or Lean."""
        self.annotate()
        article = self.workspace()
        workspace = article["workspaces"][0]
        studio = self.flow.studio
        study = studio.report(workspace["study_id"])
        study = studio.add_manual_round(
            study["study_id"],
            {
                "request_id": "fixture-round",
                "expected_revision": study["revision"],
                "proposal": {
                    "source_fingerprint": study["source_fingerprint"],
                    "plan_fingerprint": study["plan_fingerprint"],
                    "node_id": "root",
                    "reasoning": "Labelled protocol fixture only",
                    "gaps": ["No real Lean execution in this contract test"],
                    "next_actions": [],
                    "formalization": {
                        "backend": "calculus",
                        "request": {
                            "nodes": [
                                {
                                    "id": "root",
                                    "kind": "problem",
                                    "target": {"original": "Exact step"},
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
                "backend": "calculus",
                "graph_id": "fixture-graph",
                "plan_fingerprint": "fixture-plan",
                "href": "/fixture",
                "reviewer": "fixture",
                "at": "fixture",
            }
        )
        studio.save(row, "LABELLED_FIXTURE_TRANSFER")
        return {
            "workspace_id": workspace["workspace_id"],
            "graph_id": "fixture-graph",
            "node_id": "root",
        }

    def return_data(self, preview, **extra):
        return self.mutation(
            **{
                name: preview[name]
                for name in ("workspace_id", "graph_id", "node_id", "preview_fingerprint")
            },
            reviewer="fixture local reviewer",
            scope="Exact complete fixture obligation only",
            confirmed=True,
            **extra,
        )

    def test_create_preserves_question_conditions_and_unproved_prerequisites(self):
        self.annotate()
        assumptions = ["固定数学空间 E，不新增有界性假设。", "原文分支包含零向量。"]
        self.store.annotate(
            self.key,
            self.mutation(
                block_id="step2",
                classification="author_step",
                statement="Dependent exact step",
                assumptions=assumptions,
                depends_on=["step1"],
                note="Explicit complete fixture conditions",
            ),
        )
        result = self.workspace("step2")
        workspace = result["workspaces"][0]
        study = self.flow.studio.report(workspace["study_id"])
        self.assertEqual(study["source"]["question"], "Dependent exact step")
        self.assertIn("Exact step", study["source"]["definitions"])
        self.assertIn("禁止直接添加为新假设", study["source"]["definitions"])
        self.assertNotIn("Exact step", study["source"]["assumptions"])
        self.assertEqual(study["source"]["assumptions"], "\n".join(assumptions))
        self.assertEqual(study["math_verdict"], "UNKNOWN")
        self.assertEqual(study["model_calls"], 0)
        self.assertEqual(self.calls, [])
        self.assertEqual(workspace["observation"]["status"], "CURRENT")

    def test_unclassified_nonmath_and_invalid_area_cannot_create_research(self):
        for area in ("实分析与复分析", "unsupported"):
            with self.assertRaises(ValueError):
                self.flow.create_workspace(
                    self.key, self.mutation(block_id="step1", area=area, context="")
                )
        self.annotate(classification="context")
        with self.assertRaises(ValueError):
            self.workspace()
        self.assertEqual(self.flow.studio.paths(), [])

    def test_workspace_idempotency_revision_conflict_and_content_conflict(self):
        self.annotate()
        data = self.mutation(block_id="step1", area="实分析与复分析", context="Full definitions")
        result = self.flow.create_workspace(self.key, data)
        self.assertEqual(self.flow.create_workspace(self.key, data)["revision"], result["revision"])
        self.assertEqual(len(self.flow.studio.paths()), 1)
        with self.assertRaises(ValueError):
            self.flow.create_workspace(self.key, {**data, "context": "changed"})
        with self.assertRaisesRegex(ValueError, "reload"):
            self.flow.create_workspace(self.key, {**data, "request_id": "stale-request"})

    def test_retry_after_article_commit_failure_recovers_same_created_study(self):
        self.annotate()
        data = self.mutation(block_id="step1", area="实分析与复分析", context="Full context")
        before = self.store.report(self.key)["revision"]
        with patch(
            "mra.article_audit.atomic",
            side_effect=OSError("fixture storage failure after study creation"),
        ):
            with self.assertRaises(OSError):
                self.flow.create_workspace(self.key, data)
        self.assertEqual(self.store.report(self.key)["revision"], before)
        self.assertEqual(len(self.flow.studio.paths()), 1)
        result = self.flow.create_workspace(self.key, data)
        self.assertEqual(len(result["workspaces"]), 1)
        self.assertEqual(len(self.flow.studio.paths()), 1)

    def test_atomic_batch_supports_forward_dependencies_and_rolls_back_invalid_batch(self):
        def entry(key, dependencies):
            return {
                "block_id": key,
                "classification": "author_step",
                "statement": "Exact step",
                "assumptions": [],
                "depends_on": dependencies,
                "note": "Explicit fixture review",
            }

        self.store.annotate_many(
            self.key, self.mutation(annotations=[entry("step2", ["step1"]), entry("step1", [])])
        )
        before = self.store.report(self.key)
        with self.assertRaises(ValueError):
            self.store.annotate_many(
                self.key,
                self.mutation(annotations=[entry("step1", ["step2"]), entry("context", [])]),
            )
        self.assertEqual(self.store.report(self.key), before)
        for entries in ([entry("step1", []), entry("step1", [])], [], [entry("step1", [])] * 33):
            with self.assertRaises(ValueError):
                self.store.annotate_many(self.key, self.mutation(annotations=entries))

    def test_old_single_annotation_fingerprint_remains_replayable_after_upgrade(self):
        from mra.domain import digest

        data = self.mutation(
            block_id="step1",
            classification="author_step",
            statement="Exact step",
            assumptions=[],
            depends_on=[],
            note="Old v1 annotation request",
        )
        self.store.annotate(self.key, data)
        row, _, _ = self.store.read(self.key)
        self.assertEqual(row["requests"][-1]["fingerprint"], digest(data))
        self.assertEqual(self.store.annotate(self.key, data)["revision"], row["revision"])

    def test_frontier_depth_impact_and_current_evidence_are_distinct(self):
        self.annotate()
        self.annotate("step2", dependencies=["step1"])
        self.annotate("claim", classification="claim", dependencies=["step2"])
        self.bind()
        unknown = self.flow.report(self.key)
        self.assertEqual(unknown["workflow"]["frontier"][0]["block_id"], "step1")
        self.assertEqual(unknown["workflow"]["frontier"][0]["downstream_obligations"], 2)
        current = self.flow.report(self.key, recheck=True)
        self.assertEqual(current["workflow"]["frontier"][0]["block_id"], "step2")
        self.assertTrue(current["workflow"]["frontier"][0]["ready"])
        self.assertEqual(
            [item["depth"] for item in current["workflow"]["dependency_graph"]], [0, 1, 2]
        )
        self.assertFalse(current["whole_article_verified"])

    def test_dependency_edit_restore_cannot_revive_workspace(self):
        self.annotate()
        self.annotate("step2", dependencies=["step1"])
        workspace = self.workspace("step2")["workspaces"][0]
        self.annotate(statement="Changed assumption")
        self.annotate(statement="Exact step")
        result = self.flow.report(self.key)
        self.assertEqual(result["workspaces"][0]["observation"]["status"], "STALE")
        with self.assertRaises(ValueError):
            self.flow.research_tasks(self.key, workspace["workspace_id"])
        self.assertEqual(len(self.flow.studio.paths()), 1)

    def test_damaged_study_does_not_hide_original_article_or_grant_proof(self):
        self.annotate()
        workspace = self.workspace()["workspaces"][0]
        path = self.flow.studio.directory(workspace["study_id"]) / "record.json"
        path.write_text("{}")
        result = self.flow.report(self.key)
        self.assertEqual(result["workspaces"][0]["observation"]["status"], "UNAVAILABLE")
        self.assertEqual(result["counts"]["current_verified_obligations"], 0)
        self.assertEqual(len(result["blocks"]), 5)

    def test_only_actual_current_transfer_can_be_previewed(self):
        data = self.fixture_transfer()
        self.assertEqual(
            self.flow.research_tasks(self.key, data["workspace_id"])["transfers"][0][
                "eligible_node_ids"
            ],
            ["root"],
        )
        preview = self.flow.evidence_preview(self.key, data)
        self.assertEqual(preview["identity"]["formal_statement"], FORMAL)
        with self.assertRaises(ValueError):
            self.flow.evidence_preview(self.key, {**data, "graph_id": "unrelated-graph"})
        self.evidence["plan"]["request"]["nodes"][0]["target"]["original"] = "Unrelated statement"
        with self.assertRaisesRegex(ValueError, "complete original"):
            self.flow.evidence_preview(self.key, data)

    def test_binding_uses_current_preview_confirmed_target_and_idempotency(self):
        data = self.fixture_transfer()
        preview = self.flow.evidence_preview(self.key, data)
        body = self.return_data(preview)
        for changes in ({"confirmed": False}, {"preview_fingerprint": "forged"}):
            with self.assertRaises(ValueError):
                self.flow.bind_workspace(self.key, {**body, **changes})
        result = self.flow.bind_workspace(self.key, body)
        self.assertEqual(self.flow.bind_workspace(self.key, body)["revision"], result["revision"])
        self.assertEqual(result["counts"]["current_verified_obligations"], 0)
        current = self.flow.report(self.key, recheck=True)
        self.assertEqual(current["counts"]["current_verified_obligations"], 1)
        self.assertFalse(current["whole_article_verified"])

    def test_study_or_proof_change_after_preview_requires_new_review(self):
        data = self.fixture_transfer()
        preview = self.flow.evidence_preview(self.key, data)
        self.evidence["node_states"]["root"]["source_fingerprint"] = "fixture-replaced-proof"
        with self.assertRaisesRegex(ValueError, "preview changed"):
            self.flow.bind_workspace(self.key, self.return_data(preview))
        self.evidence = fixture_report()
        study_id = preview["study_id"]
        row = self.flow.studio.read(study_id)
        self.flow.studio.save(row, "FIXTURE_NOTEBOOK_CHANGED")
        with self.assertRaisesRegex(ValueError, "preview changed"):
            self.flow.bind_workspace(self.key, self.return_data(preview))

    def test_replaced_plan_removes_eligible_candidates_and_does_not_bind_old_round(self):
        data = self.fixture_transfer()
        study = self.flow.studio.read(
            self.flow.workspace(self.store.read(self.key)[0], data["workspace_id"])["study_id"]
        )
        plan = deepcopy(study["plans"][-1]["plan"])
        plan["steps"][0]["approach"] = "A different explicit route"
        self.flow.studio.plan(
            study["study_id"],
            {
                "request_id": "new-fixture-plan",
                "expected_revision": study["revision"],
                "plan": plan,
            },
        )
        tasks = self.flow.research_tasks(self.key, data["workspace_id"])
        self.assertEqual(tasks["transfers"][0]["eligible_node_ids"], [])
        with self.assertRaises(ValueError):
            self.flow.evidence_preview(self.key, data)

    def test_revoked_or_damaged_evidence_cannot_be_returned(self):
        data = self.fixture_transfer()
        for changes in ({"invalidated": True}, {"artifact_status": "UNAVAILABLE"}):
            self.evidence = fixture_report()
            self.evidence["checks"]["fixture-attempt"].update(changes)
            with self.assertRaises(ValueError):
                self.flow.evidence_preview(self.key, data)

    def test_notebook_changed_during_slow_evidence_read_is_rejected(self):
        data = self.fixture_transfer()
        study_id = self.store.read(self.key)[0]["workspaces"][0]["study_id"]

        def changing_reporter(backend, graph):
            row = self.flow.studio.read(study_id)
            self.flow.studio.save(row, "FIXTURE_CONCURRENT_EDIT")
            return fixture_report()

        self.store.reporter = changing_reporter
        with self.assertRaisesRegex(ValueError, "changed during"):
            self.flow.evidence_preview(self.key, data)

    def test_context_budget_failure_does_not_truncate_conditions_or_create_study(self):
        self.annotate(statement="Exact step")
        row, _, _ = self.store.read(self.key)
        row["annotations"]["step1"]["assumptions"] = ["x" * 2000] * 5
        atomic(self.store.directory(self.key) / "record.json", row)
        with self.assertRaisesRegex(ValueError, "instead of truncating"):
            self.workspace()
        self.assertEqual(self.flow.studio.paths(), [])

    def test_readable_report_escapes_source_markdown_and_discloses_verification_gaps(self):
        self.annotate(statement="![external](https://example.org/track) <img src=x>")
        result = self.flow.report(self.key)
        self.assertIn(r"\!\[external\]", result["review_markdown"])
        self.assertIn("全文状态：UNKNOWN", result["review_markdown"])
        self.assertIn("未复核当前有效性", result["review_markdown"])


class ArticleWorkflowHttpTests(unittest.TestCase):
    setUp = audit_tests.ArticleAuditHttpTests.setUp

    def seed(self):
        _, row, _ = request(self.server, "POST", "/v1/article-audits", self.input)
        prefix = "/v1/article-audits/" + row["article_id"]
        _, row, _ = request(
            self.server,
            "POST",
            prefix + "/annotate",
            {
                "request_id": "http-step",
                "expected_revision": 0,
                "block_id": "step1",
                "classification": "author_step",
                "statement": "Exact step",
                "assumptions": [],
                "depends_on": [],
                "note": "Explicit local fixture statement",
            },
        )
        return prefix, row

    def test_actual_http_research_create_list_and_enriched_report(self):
        prefix, row = self.seed()
        code, created, _ = request(
            self.server,
            "POST",
            prefix + "/research",
            {
                "request_id": "http-research",
                "expected_revision": row["revision"],
                "block_id": "step1",
                "area": "实分析与复分析",
                "context": "Explicit fixture definitions",
            },
        )
        self.assertEqual(code, 200)
        workspace = created["workspaces"][0]
        self.assertEqual(
            request(self.server, "GET", prefix + "/research-tasks/" + workspace["workspace_id"])[1][
                "transfers"
            ],
            [],
        )
        self.assertIn("review_markdown", request(self.server, "GET", prefix)[1])
        self.assertEqual(created["model_calls"], 0)

    def test_actual_http_batch_rollback_and_conflict(self):
        prefix, row = self.seed()
        entry = {
            "block_id": "context",
            "classification": "context",
            "statement": "Some experiments.",
            "assumptions": [],
            "depends_on": [],
            "note": "Reviewed prose metadata only",
        }
        data = {
            "request_id": "http-batch",
            "expected_revision": row["revision"],
            "annotations": [entry],
        }
        self.assertEqual(request(self.server, "POST", prefix + "/annotate-many", data)[0], 200)
        self.assertEqual(
            request(
                self.server, "POST", prefix + "/annotate-many", {**data, "request_id": "http-stale"}
            )[0],
            400,
        )
        self.assertEqual(request(self.server, "GET", prefix)[1]["counts"]["classified"], 2)

    def test_new_writes_require_csrf_and_never_execute_unknown_tasks(self):
        prefix, row = self.seed()
        payload = {
            "request_id": "http-research",
            "expected_revision": row["revision"],
            "block_id": "step1",
            "area": "实分析与复分析",
            "context": "",
        }
        self.assertEqual(
            request(
                self.server, "POST", prefix + "/research", payload, headers={"X-MRA-CSRF": "wrong"}
            )[0],
            403,
        )
        self.assertEqual(
            request(
                self.server,
                "POST",
                prefix + "/research-preview",
                {"workspace_id": "unknown", "graph_id": "unknown", "node_id": "root"},
            )[0],
            400,
        )
        self.assertEqual(request(self.server, "GET", prefix + "?recheck=1")[0], 400)


if __name__ == "__main__":
    unittest.main()
