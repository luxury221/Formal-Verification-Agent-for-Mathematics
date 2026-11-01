"""Explicit source-aware suggestions; protocol fixtures never count as mathematical evidence."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

import test_structure_reuse as core
from test_http_api import request as http, running_server
from test_structure_research import confirmation, create, parent
from mra.domain import canonical
from mra.structure_graph_publication import PublishedStructureResearchStore
from mra.structure_lemma_search import LemmaSearch
from mra.structure_lemma_suggestions import DEFAULT_BUDGET, FILES, SCHEMA, suggest
from mra.structure_reuse import StructureReuseStore, checked
from mra.structured_terms import compile_request
from mra.verifier import NativeLeanBackend

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/structure-lemma-suggestions-20261029-01"


def raw(key, node="lifted", **budget):
    return {
        "schema": SCHEMA,
        "graph_id": key,
        "node_id": node,
        "budget": {**DEFAULT_BUDGET, **budget},
        "timeout_seconds": 20,
    }


def conditional_graph():
    def target(name, operation):
        return {
            "title": name,
            "original": "完整原条件 x=y 下的函数合同等式",
            "variables": [{"name": "x", "type": "g"}, {"name": "y", "type": "g"}],
            "assumptions": [{"name": "equal", "formula": {"eq": [core.var("x"), core.var("y")]}}],
            "goal": {
                "eq": [core.call(operation, core.var("x")), core.call(operation, core.var("y"))]
            },
            "proof": {"method": "grind"},
            "timeout_seconds": 20,
        }

    first, second = target("函数 f 的合同引理", "f"), target("函数 g 的合同引理", "g")
    problem = target("嵌套函数的完整目标", "f")
    problem["variables"] = [{"name": "a", "type": "g"}, {"name": "b", "type": "g"}]
    problem["assumptions"] = [{"name": "given", "formula": {"eq": [core.var("a"), core.var("b")]}}]
    problem["goal"] = {
        "eq": [
            core.call("f", core.call("g", core.var("a"))),
            core.call("f", core.call("g", core.var("b"))),
        ]
    }
    problem["proof"] = {"method": "rfl"}
    missing = deepcopy(problem)
    missing["title"] = "不能用 a=a 充当 a=b"
    missing["assumptions"][0]["formula"] = {"eq": [core.var("a"), core.var("a")]}
    return {
        "schema": "structure-project-research-v1",
        "carriers": [{"name": "g", "original": "任意载体"}],
        "operations": [
            {"name": n, "parameters": ["g"], "result_type": "g", "original": "任意函数参数"}
            for n in ("f", "g")
        ],
        "definitions": [],
        "laws": [],
        "nodes": [
            {"id": "first", "kind": "lemma", "depends_on": [], "target": first},
            {"id": "second", "kind": "lemma", "depends_on": [], "target": second},
            {
                "id": "lifted",
                "kind": "lemma",
                "depends_on": ["first", "second"],
                "target": problem,
            },
            {
                "id": "problem",
                "kind": "problem",
                "depends_on": ["lifted"],
                "target": missing,
            },
        ],
    }


class ContractTests(unittest.TestCase):
    def setUp(self):
        core.ContractTests.setUp(self)
        for n in FILES:
            shutil.copyfile(ROOT / n, self.root / n)
        shutil.copyfile(
            ROOT / "mra/structure_reuse_web.py", self.root / "mra/structure_reuse_web.py"
        )

    def suggestions(self, node="lifted", **budget):
        context, _ = self.store.context(self.key, node)
        descriptors = [
            core.descriptor(self.value, self.key, n, "lemma" + str(i))
            for i, n in enumerate(context["depends_on"])
        ]
        for d in descriptors:
            d["source_fingerprint"] = "NONEXECUTABLE-contract-fixture"
        by_id = {d["item_id"]: d for d in descriptors}
        catalog = {"context": context, "candidate_ids": list(by_id)}

        def source(binding):
            return {**by_id[binding["item_id"]], "alias": binding["alias"]}, b"NONEXECUTABLE"

        with (
            patch.object(self.store, "catalog", return_value=catalog),
            patch.object(self.store, "load_source", side_effect=source),
            patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")),
        ):
            return suggest(self.store, raw(self.key, node, **budget))

    def use_conditional(self):
        self.value = conditional_graph()
        self.graph = create(self.graphs, parent(self.root), self.value)
        self.key = self.graph["graph_id"]

    def test_instantiates_complete_source_and_all_original_laws_without_execution(self):
        before = self.graphs.report(self.key)
        value = self.suggestions(max_candidates=1)
        self.assertTrue(value["candidates"])
        self.assertEqual((value["lean_calls"], value["model_calls"]), (0, 0))
        self.assertEqual((value["math_verdict"], value["evidence_level"]), ("UNKNOWN", "E0"))
        self.assertEqual(value["target"], core.full_target(self.value, "lifted"))
        candidate = value["candidates"][0]
        self.assertEqual(
            candidate["request"]["proof"]["terms"], core.request(self.key)["proof"]["terms"]
        )
        self.assertEqual(
            len(candidate["applications"][0]["premise_proofs"]), len(self.value["laws"])
        )
        self.assertEqual(self.graphs.report(self.key), before)
        self.assertEqual(self.store.list_runs(), [])

    def test_nested_conditional_calls_resolve_from_other_source_and_original_premise(self):
        self.use_conditional()
        value = self.suggestions("lifted", max_candidates=1)
        candidate = value["candidates"][0]
        proof = candidate["request"]["proof"]
        self.assertEqual(proof["lemma"], "lemma0")
        self.assertEqual(proof["proofs"][0]["lemma"], "lemma1")
        self.assertEqual(proof["proofs"][0]["proofs"], [{"hyp": "given"}])
        self.assertEqual(len(candidate["applications"]), 2)
        self.assertEqual(
            candidate["formal_statement"], compile_request(value["target"])["formal_statement"]
        )

    def test_missing_required_condition_is_reported_without_adding_a_premise(self):
        self.use_conditional()
        value = self.suggestions("problem", max_expansions=4000)
        self.assertEqual(value["candidates"], [])
        self.assertEqual(value["target"], core.full_target(self.value, "problem"))
        self.assertTrue(
            any(
                d["reason"] == "premise_program_not_found_within_bounds"
                for d in value["diagnostics"]
            )
        )
        self.assertEqual(value["math_verdict"], "UNKNOWN")

    def test_congruence_rewrite_chain_uses_each_direct_source_with_explicit_conditions(self):
        value = self.suggestions("problem", max_candidates=1, max_expansions=4000)
        candidate = value["candidates"][0]
        self.assertIn("congrArg", candidate["proof_source"])
        self.assertIn("Eq.trans", candidate["proof_source"])
        self.assertEqual({a["alias"] for a in candidate["applications"]}, {"lemma0", "lemma1"})

    def test_current_unproved_prerequisite_never_becomes_a_source(self):
        with patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")):
            value = suggest(self.store, raw(self.key))
        self.assertFalse(value["candidates"])
        self.assertTrue(
            any(d["reason"] == "current_e4_source_unavailable" for d in value["diagnostics"])
        )

    def test_root_has_no_composition_source_and_no_automatic_execution(self):
        value = suggest(self.store, raw(self.key, "power"))
        self.assertEqual(value["source_audits"], [])
        self.assertEqual(value["candidates"], [])
        self.assertEqual(self.store.list_runs(), [])

    def test_budget_schema_client_feedback_and_nonfinite_values_are_strict(self):
        for change in (
            {"schema": "other"},
            {"feedback": "proved"},
            {"sources": []},
            {"timeout_seconds": True},
            {"timeout_seconds": float("inf")},
            {"budget": {**DEFAULT_BUDGET, "max_depth": True}},
            {"budget": {**DEFAULT_BUDGET, "max_terms": 65}},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                suggest(self.store, {**raw(self.key), **change})

    def test_shared_expansion_and_wall_limits_keep_partial_results_unknown(self):
        value = self.suggestions(max_expansions=1)
        self.assertFalse(value["candidates"])
        self.assertLessEqual(value["stats"]["expansions"], 1)
        with patch("mra.structure_strategy_terms.WALL_SECONDS", 0):
            value = self.suggestions()
        self.assertEqual(value["stats"]["stop_reason"], "synthesis_wall_limit")

    def test_matcher_refuses_type_errors_capture_and_preserves_repeated_arguments(self):
        target = core.full_target(self.value, "lifted")
        engine = LemmaSearch(target, DEFAULT_BUDGET)
        self.assertFalse(engine.match(core.var("x"), core.var("n"), {"x": "g"}, {}))
        pattern = {"eq": [core.var("x"), core.var("x")]}
        self.assertFalse(
            engine.match(pattern, {"eq": [core.var("x"), core.call("g_one")]}, {"x": "g"}, {})
        )
        bound = {
            "forall": {
                "binders": [{"name": "z", "type": "g"}],
                "body": {"eq": [core.var("x"), core.var("z")]},
            }
        }
        self.assertFalse(engine.match(bound, bound, {"x": "g"}, {}))
        closed = {
            "forall": {
                "binders": [{"name": "x", "type": "g"}],
                "body": {"eq": [core.var("x"), core.var("x")]},
            }
        }
        renamed = {
            "forall": {
                "binders": [{"name": "y", "type": "g"}],
                "body": {"eq": [core.var("y"), core.var("y")]},
            }
        }
        self.assertTrue(engine.match(closed, renamed, {"x": "g"}, {}))

    def test_term_pool_excludes_lexical_locals_and_is_bounded(self):
        target = core.full_target(self.value, "lifted")
        engine = LemmaSearch(target, {**DEFAULT_BUDGET, "max_terms": 1})
        self.assertEqual(len(engine.pool), 1)
        self.assertTrue(engine.pool_truncated)
        target["assumptions"] = [
            {
                "name": "quantified",
                "formula": {
                    "forall": {
                        "binders": [{"name": "local", "type": "g"}],
                        "body": {"eq": [core.var("local"), core.var("local")]},
                    }
                },
            }
        ]
        engine = LemmaSearch(target, DEFAULT_BUDGET)
        self.assertNotIn(core.var("local"), engine.pool)

    def test_source_context_drift_and_wrong_feedback_cannot_be_silently_used(self):
        context, graph = self.store.context(self.key, "lifted")
        with (
            patch.object(
                self.store, "catalog", return_value={"context": {**context, "node_id": "wrong"}}
            ),
            self.assertRaises(ValueError),
        ):
            suggest(self.store, raw(self.key))
        from mra.structure_lemma_suggestions import feedback

        bad = deepcopy(graph)
        bad["attempts"] = [{"node_id": "lifted", "attempt_id": "wrong"}]
        bad["checks"] = {"wrong": {"plan": {"request": core.full_target(self.value, "power")}}}
        with self.assertRaises(ValueError):
            feedback(bad, "lifted", context["target"])

    def test_unused_unrelated_direct_source_is_not_silently_dropped(self):
        self.value["nodes"][1]["target"]["goal"] = {"eq": [core.var("x"), core.call("g_one")]}
        self.graph = create(self.graphs, parent(self.root), self.value)
        self.key = self.graph["graph_id"]
        value = self.suggestions(max_expansions=4000)
        self.assertEqual(value["candidates"], [])
        self.assertEqual(value["context"]["depends_on"], ["power"])

    def test_source_audit_count_and_lexical_unresolved_argument_pool_are_bounded(self):
        context, _ = self.store.context(self.key, "lifted")
        ids = ["run.mathlib-" + format(i, "032x") for i in range(12)]
        source = core.descriptor(self.value, self.key)
        source["source_fingerprint"] = "NONEXECUTABLE-contract-fixture"
        calls = []

        def unavailable(binding):
            calls.append(binding["item_id"])
            raise ValueError("NONEXECUTABLE unavailable fixture")

        with (
            patch.object(
                self.store, "catalog", return_value={"context": context, "candidate_ids": ids}
            ),
            patch.object(
                self.store, "read", return_value={"plan": {"request": {"node_id": "power"}}}
            ),
            patch.object(self.store, "load_source", side_effect=unavailable),
        ):
            result = suggest(self.store, raw(self.key))
        self.assertEqual(len(calls), 8)
        self.assertEqual(result["candidates"], [])

    def test_http_strict_origin_csrf_query_and_no_queue(self):
        route = f"/v1/structure-reuse/graphs/{self.key}/nodes/lifted/suggestions"
        body = {"budget": DEFAULT_BUDGET, "timeout_seconds": 20}
        with (
            running_server(self.root) as server,
            patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")),
        ):
            status, value, _ = http(server, "POST", route, body)
            self.assertEqual((status, value["schema"]), (200, SCHEMA))
            self.assertEqual(value["candidates"], [])
            self.assertEqual(
                http(server, "POST", route, body, headers={"Origin": "https://example.invalid"})[0],
                403,
            )
            self.assertEqual(
                http(server, "POST", route, body, headers={"X-MRA-CSRF": "wrong"})[0], 403
            )
            self.assertEqual(http(server, "POST", route + "?x=1", body)[0], 400)
            self.assertEqual(http(server, "POST", route, {**body, "feedback": "fake"})[0], 400)
            self.assertEqual(http(server, "POST", route, raw=b'{"budget":{},"budget":{}}')[0], 400)
            self.assertEqual(server.structure_reuse.list_runs(), [])


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1",
    "Explicit genuine current-source suggestion and replay experiment",
)
class RealTests(unittest.TestCase):
    def test_generated_nested_import_missing_condition_and_withdrawal(self):
        resume = os.environ.get("MRA069_RESUME_DIR")
        area = Path(resume).resolve() if resume else OUT / ("real-" + uuid.uuid4().hex)
        if not area.is_relative_to(OUT) or area.parent != OUT:
            raise ValueError("Resume only the private diagnostic area inside this stage")
        area.mkdir(parents=True, exist_ok=bool(resume))
        graphs = PublishedStructureResearchStore(ROOT, base=area / "graphs")
        if resume:
            declared = json.loads((area / "declared-plan.json").read_text())
            key = declared["suggestion_request"]["graph_id"]
            graph = graphs.report(key)
            self.assertEqual(graph["plan"]["request"], declared["graph_request"])
            failures = [
                p
                for p in OUT.glob("validation-real-*/summary.json")
                if not json.loads(p.read_text())["all_passed"]
            ]
            self.assertEqual(len(failures), 1)
            original_sources = json.loads(failures[0].read_text())["sources"]
            changed = [
                n
                for n, h in original_sources.items()
                if hashlib.sha256((ROOT / n).read_bytes()).hexdigest() != h
            ]
            self.assertEqual(changed, ["tests/test_structure_lemma_suggestions.py"])
        else:
            graph = create(graphs, parent(ROOT), conditional_graph())
            key = graph["graph_id"]
        phase_dir = area / ("continuation-" + uuid.uuid4().hex) if resume else area
        phase_dir.mkdir(exist_ok=not bool(resume))
        store = StructureReuseStore(ROOT, graphs=graphs, base=area / "reuse")
        self.assertEqual(store.list_runs(), [])

        def submit_program(request):
            preview = store.preview(request)
            return store.submit(
                {
                    "request_id": "mra069-" + uuid.uuid4().hex,
                    "request": request,
                    "plan_fingerprint": preview["plan_fingerprint"],
                    "reviewer": "MRA069 complete original-target and source-condition QA",
                    "confirmed": True,
                }
            )

        def save(name, value):
            destination = phase_dir / name
            self.assertFalse(destination.exists())
            destination.write_bytes(canonical(value) + b"\n")

        save(
            "declared-plan.json",
            {
                "graph_request": graph["plan"]["request"],
                "selection": "first generated complete program, no retries; two roots, one positive composition, one wrong-premise composition",
                "suggestion_request": raw(key, "lifted", max_candidates=1),
            },
        )
        roots = []
        if resume:
            saved_roots = json.loads((area / "roots.json").read_text())
            for node, saved in zip(("first", "second"), saved_roots):
                child = graph["checks"][graph["node_states"][node]["best_attempt"]]
                self.assertEqual(child, saved)
                checked(child)
                roots.append(child)
            self.assertEqual(len(roots), 2)
            save(
                "continuation.json",
                {
                    "source": str(area.relative_to(ROOT)),
                    "original_validation": str(failures[0].relative_to(ROOT)),
                    "reason": "Correct erroneous node argument; both existing roots reaudited without execution",
                    "unchanged_mathematical_sources": True,
                    "new_root_executions": 0,
                },
            )
        else:
            for node in ("first", "second"):
                graphs.submit(
                    key,
                    {
                        **confirmation(graphs, key, node),
                        "reviewer": "MRA069 complete root-condition alignment QA",
                    },
                )
                graphs.work_once(key)
                current = graphs.report(key)
                child = current["checks"][current["node_states"][node]["best_attempt"]]
                checked(child)
                roots.append(child)
        save("roots.json", roots)
        suggestions = suggest(store, raw(key, "lifted", max_candidates=1))
        save("suggestions.json", suggestions)
        self.assertTrue(suggestions["candidates"])
        candidate = suggestions["candidates"][0]
        run = submit_program(candidate["request"])
        store.work_once(run["run_id"])
        proved = store.report(run["run_id"])
        checked(proved)
        self.assertEqual(proved["plan"]["target"], suggestions["target"])
        self.assertEqual(len(proved["result"]["candidate"]["imports"]), 2)
        save("proved.json", proved)
        missing = suggest(store, raw(key, "problem", max_expansions=4000))
        save("missing-suggestions.json", missing)
        self.assertFalse(missing["candidates"])
        wrong = {
            **candidate["request"],
            "node_id": "problem",
            "sources": [{"alias": "lemma0", "item_id": "run." + proved["run_id"]}],
            "proof": {
                "lemma": "lemma0",
                "terms": [core.var("a"), core.var("b")],
                "proofs": [{"hyp": "given"}],
            },
        }
        rejected_run = submit_program(wrong)
        store.work_once(rejected_run["run_id"])
        rejected = store.report(rejected_run["run_id"])
        save("rejected.json", rejected)
        self.assertEqual((rejected["state"], rejected["math_verdict"]), ("REJECTED", "UNKNOWN"))
        self.assertIsNone(rejected["result"]["replay"])
        graphs.withdraw(
            key,
            {
                "node_id": "first",
                "reviewer": "MRA069 local source lifecycle QA",
                "reason": "Verify generated draft cannot bypass withdrawn source",
                "confirmed": True,
            },
        )
        unavailable = suggest(store, raw(key, "problem"))
        save("withdrawn-suggestions.json", unavailable)
        self.assertFalse(unavailable["candidates"])
        invalidated = store.report(proved["run_id"])
        save("invalidated.json", invalidated)
        self.assertTrue(invalidated["invalidated"])
        self.assertEqual(invalidated["math_verdict"], "UNKNOWN")
        save(
            "summary.json",
            {
                "all_passed": True,
                "complete_checks": 4,
                "real_workers": 7,
                "model_calls": 0,
                "graph_id": key,
                "proved_run_id": proved["run_id"],
                "rejected_run_id": rejected["run_id"],
                "historical_proved_later_invalidated": True,
            },
        )
