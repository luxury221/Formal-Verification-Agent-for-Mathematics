"""v2 contracts plus separately enabled real, author-visible paired diagnostics."""

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

import test_structure_research as core
from test_http_api import request as http, running_server
from test_structure_strategies import target, request_for
from mra.domain import canonical, digest
from mra.structured_examples import examples
from mra.structured_terms import compile_request
from mra.structure_strategies import propose as propose_v1
from mra.structure_strategies_v2 import (
    DEFAULT_BUDGET,
    FILES,
    SCHEMA,
    graph_proposals,
    manifest,
    propose,
)
from mra.structure_strategy_ast import RankedPlanner, active_indices, free_names
from mra.structure_strategy_terms import MAX_NORMALIZATION_NODES
from mra.reuse_evaluation_checks import initialize
from mra.structure_strategy_checks import verified
from mra.structures import StructureStore, prepare
from mra.verifier import NativeLeanBackend

ROOT = Path(__file__).resolve().parents[1]
DATASET = "evals/structure-strategy-ast-v1/cases.json"
OUT = ROOT / "artifacts/structure-strategy-ast-20261029-01"


def cases():
    return json.loads((ROOT / DATASET).read_text())["cases"]


def raw(value, **budget):
    return {
        "schema": SCHEMA,
        "target": target(value),
        "budget": {**DEFAULT_BUDGET, **budget},
    }


class DiagnosticChecks(StructureStore):
    """Test-private storage, binding both generators and exact declared dataset bytes."""

    def __init__(self, root, directory):
        initialize(self, root, directory, "checks", True)

    def context(self):
        from mra.integrity import file_hash
        from mra.structure_strategies import manifest as old_manifest

        return {
            "v1": old_manifest(self.root),
            "v2": manifest(self.root),
            "dataset": file_hash(self.root / DATASET),
            "runner": file_hash(self.root / "tests/test_structure_strategy_ast.py"),
        }

    def preview(self, request):
        value = prepare(self.root, request)
        value["source_manifest"] = {
            "provider": value["source_manifest"],
            "diagnostic": self.context(),
        }
        value.pop("plan_fingerprint")
        value["plan_fingerprint"] = digest(value)
        return value

    def gate(self, row, check_files=True):
        if row["plan"]["source_manifest"]["diagnostic"] != self.context():
            return "Diagnostic generators, runner or dataset changed"
        view = deepcopy(row)
        view["plan"]["source_manifest"] = row["plan"]["source_manifest"]["provider"]
        return StructureStore.gate(self, view, check_files)


class ContractTests(unittest.TestCase):
    def setUp(self):
        core.ContractTests.setUp(self)
        for n in (*FILES, "mra/structure_strategies.py", "mra/structure_research_web.py"):
            p = self.root / n
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / n, p)

    def test_reordered_declarations_choose_active_successor_and_preserve_every_condition(self):
        t = cases()[0]["target"]
        original = deepcopy(t)
        with patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")):
            value = propose(self.root, raw(t))
        self.assertEqual(t, original)
        self.assertEqual(value["schema"], SCHEMA)
        self.assertEqual(value["induction_ranking"][0]["variable"], "n")
        self.assertEqual(value["induction_ranking"][0]["successor_exposures"], 2)
        self.assertEqual(value["induction_ranking"][1]["successor_exposures"], 1)
        self.assertTrue(value["ranking_complete"])
        program = value["candidates"][1]
        self.assertEqual(program["origin"], "symbolic-induction")
        self.assertEqual(program["request"]["proof"]["variable"], "n")
        self.assertEqual((value["math_verdict"], value["evidence_level"]), ("UNKNOWN", "E0"))
        self.assertEqual((value["lean_calls"], value["model_calls"]), (0, 0))
        for c in value["candidates"]:
            self.assertEqual(target(c["request"]), t)
            self.assertEqual(
                compile_request(c["request"])["formal_statement"], value["formal_statement"]
            )
            self.assertEqual((c["math_verdict"], c["evidence_level"]), ("UNKNOWN", "E0"))

    def test_swapped_renamed_recurrence_and_all_declaration_permutations(self):
        import itertools

        t = cases()[1]["target"]
        for variables in itertools.permutations(t["variables"]):
            value = propose(self.root, raw({**t, "variables": list(variables)}))
            self.assertEqual(value["induction_ranking"][0]["variable"], "beta")
            self.assertEqual(value["candidates"][1]["request"]["proof"]["variable"], "beta")
            self.assertEqual(value["candidates"][1]["origin"], "symbolic-induction")

    def test_unused_definition_and_irrelevant_variable_never_trigger_induction(self):
        value = propose(self.root, raw(cases()[2]["target"]))
        self.assertEqual(value["induction_ranking"], [])
        self.assertEqual(value["candidates"][1]["origin"], "symbolic-rewrite")
        self.assertFalse(
            any(c["request"]["proof"]["method"] == "nat_induction" for c in value["candidates"])
        )

    def test_lexical_binders_suspended_steps_and_free_occurrence_ties(self):
        f = {
            "nat_fold": {
                "index": {"var": "outer"},
                "zero": {"nat": 0},
                "step": {
                    "index": "k",
                    "value": "acc",
                    "body": {
                        "nat_fold": {
                            "index": {"var": "hidden"},
                            "zero": {"nat": 0},
                            "step": {"index": "j", "value": "a", "body": {"var": "a"}},
                        }
                    },
                },
            }
        }
        self.assertEqual(active_indices(f), [({"var": "outer"}, ())])
        bound = {"forall": {"binders": [{"name": "outer", "type": "Nat"}], "body": f}}
        self.assertNotIn("outer", free_names(bound))
        self.assertEqual(active_indices(bound)[0][1], ("outer",))
        t = target(examples()[2])
        t["goal"] = {
            "eq": [
                {"call": {"name": "power", "args": [{"var": "x"}, {"var": n}]}} for n in ("m", "n")
            ]
        }
        t["variables"] = list(reversed(t["variables"]))
        ranking = RankedPlanner(t, DEFAULT_BUDGET).rank()
        self.assertEqual([r["variable"] for r in ranking], ["m", "n"])
        self.assertEqual(ranking[0]["successor_exposures"], ranking[1]["successor_exposures"])

    def test_capture_avoidance_in_definition_expansion_and_quantified_goal(self):
        t = target(examples()[2])
        t["goal"] = {"forall": {"binders": [{"name": "acc", "type": "Nat"}], "body": t["goal"]}}
        value = propose(self.root, raw(t))
        self.assertEqual(value["induction_ranking"][0]["variable"], "n")
        self.assertNotIn("acc", [r["variable"] for r in value["induction_ranking"]])
        self.assertEqual(value["math_verdict"], "UNKNOWN")

    def test_shared_fuel_wall_and_invalid_protocol_never_create_evidence(self):
        for budget in ({"max_expansions": True}, {"max_candidates": 0}):
            with self.assertRaises(ValueError):
                propose(self.root, raw(examples()[2], **budget))
        bad = raw(examples()[2])
        bad["target"]["proof"] = {"method": "grind"}
        with self.assertRaises(ValueError):
            propose(self.root, bad)
        limited = propose(self.root, raw(examples()[2], max_expansions=1))
        self.assertEqual(limited["stats"]["stop_reason"], "expansion_limit")
        self.assertFalse(limited["ranking_complete"])
        self.assertTrue(all(c["origin"] == "fixed" for c in limited["candidates"]))
        self.assertLessEqual(limited["stats"]["normalization_nodes"], MAX_NORMALIZATION_NODES)
        with patch("mra.structure_strategy_terms.WALL_SECONDS", 0):
            timed = propose(self.root, raw(examples()[2]))
        self.assertEqual(timed["stats"]["stop_reason"], "synthesis_wall_limit")
        self.assertEqual(timed["math_verdict"], "UNKNOWN")

    def test_expansion_limit_cannot_be_bypassed_by_the_ranking_pass(self):
        t = target(examples()[0])
        t["definitions"] = []
        x = {"var": "x"}

        def call(name, *args):
            return {"call": {"name": name, "args": list(args)}}

        for i in range(6):
            name = "f" + str(i - 1)
            body = call("mul", x, x) if i == 0 else call(name, call(name, x))
            t["definitions"].append(
                {
                    "name": "f" + str(i),
                    "original": "Bounded expansion diagnostic",
                    "parameters": [{"name": "x", "type": "g"}],
                    "result_type": "g",
                    "body": body,
                }
            )
        t["goal"] = {"eq": [call("f5", x), x]}
        value = propose(self.root, raw(t))
        self.assertIn(
            value["stats"]["stop_reason"],
            ("intermediate_expression_limit", "normalization_limit", "synthesis_wall_limit"),
        )
        self.assertFalse(value["ranking_complete"])
        self.assertEqual(value["math_verdict"], "UNKNOWN")
        self.assertLessEqual(value["stats"]["normalization_nodes"], MAX_NORMALIZATION_NODES)

    def test_v1_remains_separate_and_new_manifest_binds_ranker(self):
        t = cases()[0]["target"]
        old = propose_v1(self.root, request_for(t))
        self.assertEqual(old["candidates"][1]["request"]["proof"]["variable"], "m")
        self.assertEqual(old["schema"], "structure-strategy-proposals-v1")
        a = propose(self.root, raw(t))
        p = self.root / "mra/structure_strategy_ast.py"
        p.write_bytes(p.read_bytes() + b"\n")
        b = propose(self.root, raw(t))
        self.assertNotEqual(a["source_manifest"], b["source_manifest"])
        self.assertNotEqual(a["plan_fingerprint"], b["plan_fingerprint"])
        self.assertEqual(
            propose_v1(self.root, request_for(t))["source_manifest"], old["source_manifest"]
        )

    def test_graph_feedback_is_current_saved_target_and_read_only(self):
        before = self.store.report(self.key)
        value = graph_proposals(
            self.store, {"graph_id": self.key, "node_id": "unit", "budget": DEFAULT_BUDGET}
        )
        self.assertEqual(value["context"]["revision"], before["revision"])
        self.assertEqual(self.store.report(self.key), before)
        with self.assertRaises(ValueError):
            graph_proposals(
                self.store,
                {"graph_id": self.key, "node_id": "unit", "budget": DEFAULT_BUDGET, "feedback": []},
            )

    def test_v2_http_origin_csrf_strict_shape_and_no_execution(self):
        route = f"/v1/structure-research/{self.key}/strategy-proposals-v2"
        request = {"node_id": "unit", "budget": DEFAULT_BUDGET}
        with (
            running_server(self.root) as server,
            patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")),
        ):
            status, value, _ = http(server, "POST", route, request)
            self.assertEqual(status, 200)
            self.assertEqual(value["schema"], SCHEMA)
            for headers in ({"Origin": "https://example.invalid"}, {"X-MRA-CSRF": "wrong"}):
                self.assertEqual(http(server, "POST", route, request, headers=headers)[0], 403)
            self.assertEqual(http(server, "POST", route + "?x=1", request)[0], 400)
            self.assertEqual(http(server, "POST", route, {**request, "feedback": []})[0], 400)
            self.assertEqual(
                http(server, "POST", route, raw=b'{"node_id":"unit","node_id":"other"}')[0], 400
            )
            self.assertEqual(server.structure_research.report(self.key)["attempts"], [])


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "Explicit genuine v1/v2 diagnostics")
class RealTests(unittest.TestCase):
    def test_first_non_rfl_pairs_without_retry_or_reference_proof(self):
        out = OUT / ("real-" + uuid.uuid4().hex)
        out.mkdir(parents=True)

        def save(n, v):
            (out / n).write_bytes(canonical(v) + b"\n")

        store = DiagnosticChecks(ROOT, out)
        plan = {
            "selection": "first non-rfl, one check per arm, 20 shared worker seconds; no retry",
            "context": store.context(),
            "cases": [],
        }
        for case in cases()[1:]:
            entry = {"id": case["id"], "target": case["target"], "proposals": {}, "arms": {}}
            for arm, provider, schema in (
                ("v1", propose_v1, "structure-strategy-proposals-v1"),
                ("v2", propose, SCHEMA),
            ):
                value = provider(ROOT, {**raw(case["target"]), "schema": schema})
                entry["proposals"][arm] = value
                entry["arms"][arm] = next(
                    c["request"]
                    for c in value["candidates"]
                    if c["request"]["proof"]["method"] != "rfl"
                )
            plan["cases"].append(entry)
        save("declared-plan.json", plan)
        results = []
        for i, case in enumerate(plan["cases"]):
            for arm in ("v1", "v2") if i % 2 == 0 else ("v2", "v1"):
                request = case["arms"][arm]
                self.assertEqual(target(request), case["target"])
                row = store.submit(
                    {
                        "request_id": "ast-" + uuid.uuid4().hex,
                        "request": request,
                        "plan_fingerprint": store.preview(request)["plan_fingerprint"],
                        "reviewer": "MRA067 complete original-target alignment reviewer",
                        "confirmed": True,
                    }
                )
                store.work_once(row["run_id"])
                child = store.report(row["run_id"])
                results.append({"case_id": case["id"], "arm": arm, "check": child})
                save("results.json", results)
                if child["math_verdict"] == "PROVED":
                    verified(child)
                else:
                    self.assertEqual(
                        (child["math_verdict"], child["evidence_level"]), ("UNKNOWN", "E0")
                    )
                if case["id"] == "missing-commutativity":
                    self.assertEqual(child["math_verdict"], "UNKNOWN")
        self.assertEqual(len(results), 6)
        selected = next(
            r for r in results if r["case_id"] == "swapped-renamed-indices" and r["arm"] == "v2"
        )
        self.assertEqual(selected["check"]["math_verdict"], "PROVED")
        save(
            "summary.json",
            {
                "all_passed": True,
                "results": [
                    {
                        "case_id": r["case_id"],
                        "arm": r["arm"],
                        "run_id": r["check"]["run_id"],
                        "math_verdict": r["check"]["math_verdict"],
                    }
                    for r in results
                ],
                "math_verdict": "UNKNOWN",
                "evidence_level": "E0",
            },
        )
