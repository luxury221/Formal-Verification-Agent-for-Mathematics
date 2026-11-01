"""Bounded original-parameter generalization and separate real diagnostics."""

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
from test_structure_strategies import target
from mra.domain import canonical, digest
from mra.structured_terms import compile_request
from mra.structured_examples import examples
from mra.structure_strategies_v2 import propose as propose_v2
from mra.structure_strategies_v3 import (
    DEFAULT_BUDGET,
    FILES,
    SCHEMA,
    graph_proposals,
    manifest,
    propose,
)
from mra.structure_strategy_generalization import GeneralizingPlanner, protected_names
from mra.structure_strategy_terms import MAX_NORMALIZATION_NODES
from mra.reuse_evaluation_checks import initialize
from mra.structure_strategy_checks import verified
from mra.structures import StructureStore, prepare
from mra.verifier import NativeLeanBackend

ROOT = Path(__file__).resolve().parents[1]
DATASET = "evals/structure-generalization-v1/cases.json"
OUT = ROOT / "artifacts/structure-generalization-20261029-01"


def cases():
    return json.loads((ROOT / DATASET).read_text())["cases"]


def raw(value, **budget):
    return {"schema": SCHEMA, "target": target(value), "budget": {**DEFAULT_BUDGET, **budget}}


class DiagnosticChecks(StructureStore):
    """Test-private storage, binding both generators and exact declared dataset bytes."""

    def __init__(self, root, directory):
        initialize(self, root, directory, "checks", True)

    def context(self):
        from mra.integrity import file_hash
        from mra.structure_strategies_v2 import manifest as old_manifest

        return {
            "v2": old_manifest(self.root),
            "v3": manifest(self.root),
            "dataset": file_hash(self.root / DATASET),
            "runner": file_hash(self.root / "tests/test_structure_generalization.py"),
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
        for n in (
            *FILES,
            "mra/structure_strategies_v2.py",
            "mra/structure_research_web.py",
            "tests/test_structure_generalization.py",
            DATASET,
        ):
            p = self.root / n
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / n, p)

    def test_all_proposals_preserve_the_complete_original_target_without_execution(self):
        for case in cases():
            value = raw(case["target"])
            before = deepcopy(value)
            with patch.object(
                NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")
            ):
                p = propose(self.root, value)
            self.assertEqual(value, before)
            self.assertEqual((p["lean_calls"], p["model_calls"]), (0, 0))
            self.assertEqual((p["math_verdict"], p["evidence_level"]), ("UNKNOWN", "E0"))
            self.assertFalse(p["reference_proof_used"])
            for c in p["candidates"]:
                self.assertEqual(target(c["request"]), case["target"])
                self.assertEqual(
                    compile_request(c["request"])["formal_statement"], p["formal_statement"]
                )
                self.assertEqual((c["math_verdict"], c["evidence_level"]), ("UNKNOWN", "E0"))

    def test_generalized_ih_matches_changed_state_and_renamed_declarations(self):
        for case, parameter, induction in (
            (cases()[0], "state", "count"),
            (cases()[1], "initial", "length"),
        ):
            p = propose(self.root, raw(case["target"]))
            c = p["candidates"][1]
            self.assertEqual(c["origin"], "symbolic-generalized-induction")
            self.assertEqual(c["request"]["proof"]["generalize"], [parameter])
            self.assertEqual(c["request"]["proof"]["variable"], induction)
            self.assertEqual(c["request"]["proof"]["step"]["steps"][1]["rule"], "@ih")
            self.assertIn(" generalizing ", c["proof_source"])

    def test_multiple_states_are_generalized_together_before_singletons(self):
        p = propose(self.root, raw(cases()[2]["target"]))
        self.assertEqual(
            p["generalization_analysis"][0]["options"],
            [["state", "other"], ["state"], ["other"], []],
        )
        self.assertEqual(p["candidates"][1]["request"]["proof"]["generalize"], ["state", "other"])
        self.assertEqual(p["candidates"][1]["origin"], "symbolic-generalized-induction")
        self.assertTrue(
            all(c["origin"] != "symbolic-generalized-induction" for c in p["candidates"][2:])
        )

    def test_original_premise_dependencies_and_irrelevant_parameters_are_excluded(self):
        t = deepcopy(cases()[0]["target"])
        t["assumptions"].append(
            {"name": "state_condition", "formula": {"eq": [{"var": "state"}, {"var": "state"}]}}
        )
        t["variables"].append({"name": "irrelevant", "type": "Nat"})
        p = propose(self.root, raw(t))
        a = p["generalization_analysis"][0]
        self.assertEqual(a["eligible"], [])
        self.assertEqual({x["variable"] for x in a["excluded"]}, {"operator_f", "state"})
        self.assertTrue(all(x["reason"] == "original_premise_dependency" for x in a["excluded"]))
        self.assertFalse(any(c["request"]["proof"].get("generalize") for c in p["candidates"]))
        self.assertEqual(p["target"], t)

    def test_binder_subterms_use_alpha_comparison_and_never_capture_generalized_parameters(self):
        engine = GeneralizingPlanner(cases()[0]["target"], DEFAULT_BUDGET)
        f = {
            "nat_fold": {
                "index": {"var": "count"},
                "zero": {"var": "state"},
                "step": {"index": "j", "value": "acc", "body": {"var": "acc"}},
            }
        }
        renamed = deepcopy(f)
        renamed["nat_fold"]["step"] = {"index": "k", "value": "a", "body": {"var": "a"}}
        self.assertEqual(protected_names(f), {"count", "state"})
        self.assertTrue(engine.match(f, renamed, {}, {}))
        bindings = {}
        self.assertFalse(engine.match(f, renamed, {"state": "states"}, bindings))
        self.assertEqual(bindings, {})
        a = engine.options({"eq": [f, f]}, "count")
        self.assertEqual(a, [[]])
        self.assertEqual(
            engine.generalization_analysis[0]["excluded"][0]["reason"],
            "parameter_under_lexical_binder",
        )

    def test_missing_original_step_never_creates_a_symbolic_generalized_solution(self):
        t = cases()[3]["target"]
        p = propose(self.root, raw(t))
        self.assertEqual(p["target"]["assumptions"], t["assumptions"])
        self.assertFalse(
            any(c["origin"] == "symbolic-generalized-induction" for c in p["candidates"])
        )
        self.assertEqual(p["math_verdict"], "UNKNOWN")

    def test_shared_fuel_invalid_budget_reference_proof_and_cross_schema(self):
        for change in ({"max_expansions": True}, {"max_candidates": 0}):
            with self.assertRaises(ValueError):
                propose(self.root, raw(cases()[0]["target"], **change))
        for changed in (
            {**raw(cases()[0]["target"]), "schema": "structure-strategy-proposals-v2"},
            {
                **raw(cases()[0]["target"]),
                "target": {**cases()[0]["target"], "proof": {"method": "rfl"}},
            },
        ):
            with self.assertRaises(ValueError):
                propose(self.root, changed)
        limited = propose(self.root, raw(cases()[0]["target"], max_expansions=1))
        self.assertTrue(all(c["origin"] == "fixed" for c in limited["candidates"]))
        self.assertLessEqual(limited["stats"]["normalization_nodes"], MAX_NORMALIZATION_NODES)
        with patch("mra.structure_strategy_terms.WALL_SECONDS", 0):
            timed = propose(self.root, raw(cases()[0]["target"]))
        self.assertEqual(timed["stats"]["stop_reason"], "synthesis_wall_limit")

    def test_v2_provenance_and_programs_stay_separate_from_v3(self):
        t = cases()[0]["target"]
        oldraw = {**raw(t), "schema": "structure-strategy-proposals-v2"}
        old = propose_v2(self.root, oldraw)
        self.assertFalse(any(c["request"]["proof"].get("generalize") for c in old["candidates"]))
        a = propose(self.root, raw(t))
        p = self.root / "mra/structure_strategy_generalization.py"
        p.write_bytes(p.read_bytes() + b"\n")
        b = propose(self.root, raw(t))
        self.assertNotEqual(a["source_manifest"], b["source_manifest"])
        self.assertEqual(propose_v2(self.root, oldraw)["source_manifest"], old["source_manifest"])

    def test_saved_private_check_binds_new_generator_and_dataset(self):
        area = self.root / "private"
        area.mkdir()
        store = DiagnosticChecks(self.root, area)
        request = {**cases()[0]["target"], "proof": {"method": "rfl"}}
        row = store.submit(
            {
                "request_id": "saved-v3",
                "request": request,
                "plan_fingerprint": store.preview(request)["plan_fingerprint"],
                "reviewer": "NONEXECUTABLE protocol fixture",
                "confirmed": True,
            }
        )
        p = self.root / "mra/structure_strategy_generalization.py"
        p.write_bytes(p.read_bytes() + b"\n")
        current = store.report(row["run_id"])
        self.assertTrue(current["invalidated"])
        self.assertEqual(current["math_verdict"], "UNKNOWN")

    def test_feedback_is_read_from_saved_current_node_not_client_claims(self):
        before = self.store.report(self.key)
        value = graph_proposals(
            self.store, {"graph_id": self.key, "node_id": "unit", "budget": DEFAULT_BUDGET}
        )
        self.assertEqual(value["context"]["revision"], before["revision"])
        self.assertEqual(self.store.report(self.key), before)
        with self.assertRaises(ValueError):
            graph_proposals(
                self.store,
                {
                    "graph_id": self.key,
                    "node_id": "unit",
                    "budget": DEFAULT_BUDGET,
                    "generalize": ["fake"],
                },
            )

    def test_v3_http_same_origin_csrf_strict_input_and_no_queue(self):
        route = f"/v1/structure-research/{self.key}/strategy-proposals-v3"
        request = {"node_id": "unit", "budget": DEFAULT_BUDGET}
        with (
            running_server(self.root) as server,
            patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")),
        ):
            status, value, _ = http(server, "POST", route, request)
            self.assertEqual((status, value["schema"]), (200, SCHEMA))
            for headers in ({"Origin": "https://example.invalid"}, {"X-MRA-CSRF": "wrong"}):
                self.assertEqual(http(server, "POST", route, request, headers=headers)[0], 403)
            self.assertEqual(http(server, "POST", route + "?x=1", request)[0], 400)
            self.assertEqual(
                http(server, "POST", route, {**request, "generalize": ["fake"]})[0], 400
            )
            self.assertEqual(
                http(server, "POST", route, raw=b'{"node_id":"unit","node_id":"other"}')[0], 400
            )
            self.assertEqual(server.structure_research.report(self.key)["attempts"], [])

    def test_expansion_growth_is_checked_during_generalization_ranking(self):
        t = target(examples()[0])
        t["definitions"] = []
        x = {"var": "x"}

        def call(n, *args):
            return {"call": {"name": n, "args": list(args)}}

        for i in range(6):
            n = "f" + str(i - 1)
            t["definitions"].append(
                {
                    "name": "f" + str(i),
                    "original": "Bounded diagnostic",
                    "parameters": [{"name": "x", "type": "g"}],
                    "result_type": "g",
                    "body": call("mul", x, x) if i == 0 else call(n, call(n, x)),
                }
            )
        t["goal"] = {"eq": [call("f5", x), x]}
        p = propose(self.root, raw(t))
        self.assertIn(
            p["stats"]["stop_reason"],
            ("intermediate_expression_limit", "normalization_limit", "synthesis_wall_limit"),
        )
        self.assertEqual(p["math_verdict"], "UNKNOWN")


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "Explicit genuine v2/v3 generalization diagnostics"
)
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
        for case in (c for c in cases() if c["real"] == "paired"):
            entry = {"id": case["id"], "target": case["target"], "proposals": {}, "arms": {}}
            for arm, provider, schema in (
                ("v2", propose_v2, "structure-strategy-proposals-v2"),
                ("v3", propose, SCHEMA),
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
            for arm in ("v2", "v3") if i % 2 == 0 else ("v3", "v2"):
                request = case["arms"][arm]
                self.assertEqual(target(request), case["target"])
                row = store.submit(
                    {
                        "request_id": "ast-" + uuid.uuid4().hex,
                        "request": request,
                        "plan_fingerprint": store.preview(request)["plan_fingerprint"],
                        "reviewer": "MRA068 complete original-target alignment reviewer",
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
                if case["id"] == "missing-step":
                    self.assertEqual(child["math_verdict"], "UNKNOWN")
        self.assertEqual(len(results), 6)
        for case_id in ("action-model", "two-state-observation"):
            selected = next(r for r in results if r["case_id"] == case_id and r["arm"] == "v3")
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
