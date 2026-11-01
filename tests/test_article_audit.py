"""Article provenance/lifecycle contracts. Reporter fixtures do not execute Lean."""

from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest

from mra.article_audit import ArticleAuditStore
from mra.article_source import index_html
from mra.research_flow import atomic
from mra.tasks import WorkerBusy
from test_http_api import copy_web_inputs, request, running_server

ROOT = Path(__file__).resolve().parents[1]
HTML = """<html><script>DO_NOT_EXECUTE</script><body><article><section id="S1">
<h2>1 Proof</h2><div class="ltx_theorem ltx_theorem_prop" id="claim">
<p class="ltx_p">Every x satisfies x = x.</p></div>
<p class="ltx_p" id="step1">By reflexivity.</p>
<table class="ltx_equationgroup"><tbody id="eq1"><tr class="ltx_equation">
<td><math alttext="x = x"><mi>DUPLICATE_MATHML</mi></math></td></tr></tbody></table>
<p class="ltx_p" id="step2">Hence the conclusion.</p>
<p class="ltx_p" id="context">Some experiments.</p></section></article></body></html>"""
FORMAL = "∀ x : ℝ, x = x"


def fixture_report(original="Exact step", proved=True):
    return {
        "invalidated": False,
        "node_states": {
            "root": {
                "math_verdict": "PROVED" if proved else "UNKNOWN",
                "evidence_level": "E4" if proved else "E0",
                "best_attempt": "fixture-attempt",
                "source_fingerprint": "fixture-proof-source",
            }
        },
        "plan": {
            "plan_fingerprint": "fixture-plan",
            "request": {"nodes": [{"id": "root", "target": {"original": original}}]},
            "node_plans": {"root": {"formal_statement": FORMAL, "request": {"original": original}}},
        },
        "checks": {
            "fixture-attempt": {
                "state": "COMPLETED",
                "math_verdict": "PROVED",
                "evidence_level": "E4",
                "invalidated": False,
                "artifact_status": "MATCHES_RECORDED_HASHES",
                "files": {"fixture/Candidate.lean": "fixture-no-real-Lean-artifacts"},
                "plan": {"formal_statement": FORMAL},
            }
        },
    }


class ArticleAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "artifacts", prefix="article-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.evidence = fixture_report()
        self.calls = []

        def reporter(backend, key):
            self.calls.append((backend, key))
            return deepcopy(self.evidence)

        self.store = ArticleAuditStore(self.root, reporter=reporter)
        self.input = {
            "request_id": "fixture-import",
            "title": "Fixture article",
            "url": "https://example.org/paper/v1",
            "html": HTML,
        }
        self.row = self.store.create(self.input)
        self.key = self.row["article_id"]

    def mutation(self, **extra):
        revision = self.store.report(self.key)["revision"]
        return {"request_id": "fixture-" + str(revision), "expected_revision": revision, **extra}

    def annotate(
        self,
        block_id="step1",
        classification="author_step",
        dependencies=None,
        statement="Exact step",
    ):
        return self.store.annotate(
            self.key,
            self.mutation(
                block_id=block_id,
                classification=classification,
                statement=statement,
                assumptions=[],
                depends_on=dependencies or [],
                note="Manual fixture mapping, not mathematical evidence",
            ),
        )

    def bind(self, block_id="step1", relation="equivalent", **extra):
        return self.store.bind(
            self.key,
            {
                **self.mutation(
                    block_id=block_id,
                    backend="calculus",
                    graph_id="fixture-graph",
                    node_id="root",
                    formal_statement=FORMAL,
                    relation=relation,
                    scope="Exact local fixture obligation only",
                    reviewer="fixture-author",
                    confirmed=True,
                ),
                **extra,
            },
        )

    def block(self, key, recheck=True):
        return next(
            item
            for item in self.store.report(self.key, recheck=recheck)["blocks"]
            if item["id"] == key
        )

    def test_source_quotes_equation_tex_sections_and_exact_slice(self):
        inventory = index_html(HTML)
        block = next(item for item in inventory["blocks"] if item["id"] == "eq1")
        self.assertIn("$x = x$", block["quote"])
        self.assertNotIn("DUPLICATE_MATHML", block["quote"])
        self.assertEqual(block["section"], "S1")
        a, b = block["span"]
        self.assertEqual(
            hashlib.sha256(HTML[a:b].encode()).hexdigest(), block["source_slice_sha256"]
        )
        self.assertFalse(inventory["exhaustive_semantic_review"])
        self.assertFalse(any("DO_NOT_EXECUTE" in item["quote"] for item in inventory["blocks"]))

    def test_duplicate_anchors_source_limits_and_unsafe_citation_rejected(self):
        with self.assertRaises(ValueError):
            index_html(HTML.replace('id="step2"', 'id="step1"'))
        with self.assertRaises(ValueError):
            index_html("x" * 1048577)
        with self.assertRaises(ValueError):
            self.store.create({**self.input, "url": "javascript:alert(1)"})
        self.assertEqual(index_html('<p id="p">x &amp; y')["blocks"][0]["quote"], "x & y")

    def test_generic_article_paragraphs_do_not_require_arxiv_classes(self):
        inventory = index_html(
            '<article><section><h2>Proof</h2><p id="p">x = x</p></section></article>'
        )
        self.assertEqual(inventory["blocks"][0]["quote"], "x = x")
        self.assertEqual(inventory["blocks"][0]["id"], "p")

    def test_import_is_idempotent_but_changed_request_cannot_replace_source(self):
        self.assertEqual(self.store.create(self.input)["article_id"], self.key)
        with self.assertRaisesRegex(ValueError, "different source"):
            self.store.create({**self.input, "html": HTML + " "})
        different = self.store.create(
            {**self.input, "request_id": "fixture-v2", "html": HTML + " "}
        )
        self.assertNotEqual(different["article_id"], self.key)

    def test_source_tampering_is_not_silently_reimported(self):
        path = self.store.directory(self.key) / "source.json"
        source = self.store.envelope(path)
        source["html"] += "changed"
        atomic(path, source)
        with self.assertRaisesRegex(ValueError, "source identity"):
            self.store.report(self.key)

    def test_stale_revision_idempotency_and_history_retention(self):
        data = self.mutation(
            block_id="step1",
            classification="author_step",
            statement="Exact step",
            assumptions=[],
            depends_on=[],
            note="Fixture mapping",
        )
        one = self.store.annotate(self.key, data)
        self.assertEqual(self.store.annotate(self.key, data)["revision"], one["revision"])
        with self.assertRaises(ValueError):
            self.store.annotate(self.key, {**data, "statement": "changed"})
        with self.assertRaisesRegex(ValueError, "reload"):
            self.store.annotate(self.key, {**data, "request_id": "new-request"})
        self.assertEqual(len(list((self.store.directory(self.key) / "history").glob("*.json"))), 1)

    def test_missing_prerequisite_cycle_and_nonmath_dependency_rejected(self):
        with self.assertRaises(ValueError):
            self.annotate(dependencies=["step2"])
        self.annotate()
        self.annotate("step2", dependencies=["step1"])
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.annotate(dependencies=["step2"])
        with self.assertRaisesRegex(ValueError, "mathematical"):
            self.annotate(classification="context")
        self.assertEqual(self.block("step1")["annotation"]["version"], 1)

    def test_get_does_not_execute_or_trust_historical_evidence(self):
        self.annotate()
        self.bind()
        self.calls.clear()
        result = self.block("step1", recheck=False)
        self.assertEqual(self.calls, [])
        self.assertEqual(result["claim_verdict"], "UNKNOWN")
        self.assertEqual(result["links"][0]["observation"]["status"], "NOT_RECHECKED")
        current = self.block("step1")
        self.assertEqual(current["step_status"], "VERIFIED")

    def test_exact_source_statement_full_target_confirmation_and_e4_required(self):
        self.annotate()
        for changes in ({"confirmed": False}, {"formal_statement": "True"}):
            with self.assertRaises(ValueError):
                self.bind(**changes)
        self.evidence = fixture_report(original="A different obligation")
        with self.assertRaisesRegex(ValueError, "Verifier original"):
            self.bind()
        self.evidence = fixture_report(proved=False)
        with self.assertRaisesRegex(ValueError, "PROVED/E4"):
            self.bind()

    def test_conclusion_proof_does_not_certify_authors_argument_or_whole_article(self):
        self.annotate("claim", classification="claim")
        self.bind("claim")
        current = self.block("claim")
        self.assertEqual(current["claim_verdict"], "PROVED")
        self.assertEqual(current["author_proof_verdict"], "GAP_FOUND")
        self.assertFalse(self.store.report(self.key, recheck=True)["whole_article_verified"])

    def test_pde_graph_target_shape_and_mismatched_child_are_checked(self):
        self.annotate()
        plan = self.evidence["plan"]
        plan["graph_compilation"] = {"node_plans": plan.pop("node_plans")}
        self.bind()
        self.assertEqual(self.block("step1")["step_status"], "VERIFIED")
        self.evidence["checks"]["fixture-attempt"]["plan"]["formal_statement"] = "True"
        self.assertEqual(self.block("step1")["claim_verdict"], "UNKNOWN")
        self.evidence["checks"]["fixture-attempt"]["plan"]["formal_statement"] = FORMAL
        plan["request"]["nodes"] = []
        self.assertEqual(self.block("step1")["links"][0]["observation"]["status"], "UNAVAILABLE")

    def test_proven_dependent_stays_blocked_until_prerequisite_has_evidence(self):
        self.annotate()
        self.annotate("step2", dependencies=["step1"])
        self.bind("step2")
        self.assertEqual(self.block("step2")["claim_verdict"], "PROVED")
        self.assertEqual(self.block("step2")["step_status"], "BLOCKED")
        self.bind()
        self.assertEqual(self.block("step2")["step_status"], "VERIFIED")

    def test_edit_restore_invalidates_own_and_dependent_bindings_without_revival(self):
        self.annotate()
        self.annotate("step2", dependencies=["step1"])
        self.bind()
        self.bind("step2")
        self.annotate(statement="Changed premise")
        self.annotate(statement="Exact step")
        for key in ("step1", "step2"):
            self.assertEqual(self.block(key)["claim_verdict"], "UNKNOWN")
            self.assertEqual(self.block(key)["links"][0]["observation"]["status"], "STALE")

    def test_revoked_damaged_or_replaced_proof_loses_evidence(self):
        self.annotate()
        self.bind()
        for field, value in (("artifact_status", "UNAVAILABLE"), ("invalidated", True)):
            self.evidence = fixture_report()
            self.evidence["checks"]["fixture-attempt"][field] = value
            self.assertEqual(self.block("step1")["claim_verdict"], "UNKNOWN")
        self.evidence = fixture_report()
        self.evidence["node_states"]["root"]["source_fingerprint"] = "another-proof"
        self.assertEqual(self.block("step1")["links"][0]["observation"]["status"], "UNAVAILABLE")

    def test_counterexample_specialization_is_a_finding_not_a_proof_of_original(self):
        self.annotate("claim", classification="claim", statement="All matrices have property P")
        self.bind("claim", relation="counterexample_specialization")
        current = self.block("claim")
        self.assertEqual(current["finding"], "CHECKED_COUNTEREXAMPLE_SPECIALIZATION")
        self.assertEqual((current["claim_verdict"], current["evidence_level"]), ("UNKNOWN", "E0"))

    def test_busy_provider_grants_no_verdict_and_does_not_start_work(self):
        self.annotate()
        self.bind()

        def busy(*args):
            raise WorkerBusy("Explicit fixture: no execution")

        self.store.reporter = busy
        current = self.block("step1")
        self.assertEqual(current["links"][0]["observation"]["status"], "BUSY")
        self.assertEqual(current["claim_verdict"], "UNKNOWN")


class ArticleAuditHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "artifacts", prefix="article-http-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_web_inputs(self.root)
        context = running_server(self.root)
        self.server = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.input = {
            "request_id": "http-import",
            "title": "HTTP fixture",
            "url": "https://example.org/paper/v1",
            "html": HTML,
        }

    def test_actual_assets_import_listing_and_report_do_not_execute_lean(self):
        for path in ("/articles", "/articles.js", "/articles.css"):
            self.assertEqual(request(self.server, "GET", path)[0], 200)
        code, row, _ = request(self.server, "POST", "/v1/article-audits", self.input)
        self.assertEqual(code, 201)
        self.assertEqual(
            request(self.server, "GET", "/v1/article-audits")[1]["articles"][0]["article_id"],
            row["article_id"],
        )
        current = request(self.server, "GET", "/v1/article-audits/" + row["article_id"])[1]
        self.assertFalse(current["evidence_rechecked"])
        self.assertFalse(current["whole_article_verified"])
        self.assertEqual(current["new_lean_executions"], 0)

    def test_large_html_import_has_its_own_budget_without_expanding_other_routes(self):
        large = {**self.input, "html": HTML + "<!--" + "x" * 230000 + "-->"}
        self.assertEqual(request(self.server, "POST", "/v1/article-audits", large)[0], 201)
        self.assertEqual(request(self.server, "POST", "/v1/tasks", large)[0], 413)
        self.assertEqual(
            request(self.server, "POST", "/v1/article-audits", {**large, "html": "x" * 1048577})[0],
            400,
        )

    def test_import_and_recheck_require_same_origin_token(self):
        self.assertEqual(
            request(
                self.server,
                "POST",
                "/v1/article-audits",
                self.input,
                headers={"X-MRA-CSRF": "wrong"},
            )[0],
            403,
        )
        self.assertEqual(
            request(
                self.server,
                "POST",
                "/v1/article-audits",
                self.input,
                headers={"Origin": "https://example.org"},
            )[0],
            403,
        )

    def test_annotation_conflicts_and_nonempty_recheck_return_errors(self):
        _, row, _ = request(self.server, "POST", "/v1/article-audits", self.input)
        prefix = "/v1/article-audits/" + row["article_id"]
        data = {
            "request_id": "http-edit",
            "expected_revision": 0,
            "block_id": "step1",
            "classification": "author_step",
            "statement": "Exact step",
            "assumptions": [],
            "depends_on": [],
            "note": "Explicit HTTP fixture interpretation",
        }
        self.assertEqual(request(self.server, "POST", prefix + "/annotate", data)[0], 200)
        self.assertEqual(
            request(self.server, "POST", prefix + "/annotate", {**data, "request_id": "stale"})[0],
            400,
        )
        self.assertEqual(
            request(self.server, "POST", prefix + "/recheck", {"execute": True})[0], 400
        )
        result = request(self.server, "POST", prefix + "/recheck", {})
        self.assertEqual(result[0], 200)
        self.assertTrue(result[1]["evidence_rechecked"])
        self.assertEqual(result[1]["new_lean_executions"], 0)
        self.assertEqual(request(self.server, "GET", prefix + "?html=true")[0], 400)


if __name__ == "__main__":
    unittest.main()
