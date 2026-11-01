"""Contracts and opt-in genuine comparison of bounded equality-obligation planning."""

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

import test_recursion_programs as previous
from mra.domain import canonical, digest
from mra.function_dynamics_checks import verified
from mra.integrity import file_hash
from mra.obligation_planning import SCHEMA, FILES, inner_equalities, propose
from mra.recursion_programs import RecursionStore, compile_program
from mra.recursion_strategies import DEFAULT_BUDGET, propose as old_propose
from mra.reuse_evaluation_checks import initialize
from mra.structured_terms import compile_request
from mra.verifier import NativeLeanBackend

ROOT = Path(__file__).resolve().parents[1]
DATASET = "evals/structure-obligations-v1/cases.json"
OUT = ROOT / "artifacts/structure-obligation-planning-20261029-01"


def cases():
    return json.loads((ROOT / DATASET).read_text())["cases"]


def proposal(root, target, **budget):
    return propose(
        root, {"schema": SCHEMA, "target": target, "budget": {**DEFAULT_BUDGET, **budget}}
    )


class Checks(RecursionStore):
    def __init__(self, root, area, arm, create=True):
        initialize(self, root, area, arm + "-checks", create)

    def context(self):
        return {
            "sources": {
                n: file_hash(self.root / n)
                for n in (*FILES, DATASET, "tests/test_obligation_planning.py")
            }
        }

    def preview(self, raw):
        p = super().preview(raw)
        p["source_manifest"] = {"provider": p["source_manifest"], "diagnostic": self.context()}
        p.pop("plan_fingerprint")
        p["plan_fingerprint"] = digest(p)
        return p

    def gate(self, row, check_files=True):
        if row["plan"]["source_manifest"]["diagnostic"] != self.context():
            return "Obligation planner, old generator, dataset or runner changed"
        view = deepcopy(row)
        view["plan"]["source_manifest"] = row["plan"]["source_manifest"]["provider"]
        return super().gate(view, check_files)


class ContractTests(unittest.TestCase):
    def setUp(self):
        previous.ContractTests.setUp(self)
        for n in (*FILES, DATASET, "tests/test_obligation_planning.py"):
            p = self.root / n
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / n, p)

    def test_proposals_never_execute_and_keep_the_complete_original_goal(self):
        for case in cases():
            raw = deepcopy(case["target"])
            with patch.object(
                NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")
            ):
                p = proposal(self.root, raw)
            self.assertEqual(raw, case["target"])
            self.assertEqual(
                (p["lean_calls"], p["model_calls"], p["math_verdict"], p["evidence_level"]),
                (0, 0, "UNKNOWN", "E0"),
            )
            self.assertEqual(len(p["candidates"]), 2)
            expected = compile_request({**raw, "proof": {"method": "rfl"}})["formal_statement"]
            for c in p["candidates"]:
                self.assertEqual(c["request"]["target"], raw)
                self.assertEqual(compile_program(c["request"])["formal_statement"], expected)

    def test_two_ordered_obligations_prove_before_outer_context_use(self):
        for case in cases()[:3]:
            c = proposal(self.root, case["target"])["candidates"][0]
            self.assertEqual(len(c["request"]["program"]["lemmas"]), 2)
            first, second = c["request"]["program"]["lemmas"]
            self.assertIn("@lemma." + first["name"], json.dumps(second["proof"]))
            self.assertIn("@lemma." + second["name"], json.dumps(c["request"]["program"]["proof"]))
            self.assertEqual(c["request"]["target"]["assumptions"], case["target"]["assumptions"])

    def test_shared_context_extraction_stops_at_binders_mismatches_and_multiple_arguments(self):
        v, w = {"var": "x"}, {"var": "y"}

        def call(name, *args):
            return {"call": {"name": name, "args": list(args)}}

        self.assertEqual(
            inner_equalities({"eq": [call("h", call("j", v)), call("h", call("j", w))]}),
            [{"eq": [call("j", v), call("j", w)]}, {"eq": [v, w]}],
        )
        for goal in (
            {"eq": [call("h", v), call("j", w)]},
            {"eq": [call("b", v, v), call("b", w, w)]},
            {"forall": {"binders": [], "body": {"eq": [v, w]}}},
        ):
            self.assertEqual(inner_equalities(goal), [])
        for op in ("add", "sub", "mul", "div"):
            self.assertEqual(
                inner_equalities({"eq": [{op: [v, {"nat": 1}]}, {op: [w, {"nat": 1}]}]}),
                [{"eq": [v, w]}],
            )
        self.assertEqual(
            inner_equalities(
                {"eq": [{"cast": {"to": "Int", "value": v}}, {"cast": {"to": "Int", "value": w}}]}
            ),
            [{"eq": [v, w]}],
        )

    def test_dependent_original_premises_are_preserved_without_helpers(self):
        t = deepcopy(cases()[0]["target"])
        t["assumptions"].append(
            {"name": "dependent", "formula": {"eq": [{"var": "x"}, {"var": "x"}]}}
        )
        p = proposal(self.root, t)
        self.assertEqual(p["context_obligations"], [])
        self.assertTrue(all(not c["request"]["program"]["lemmas"] for c in p["candidates"]))
        self.assertEqual(p["target"], t)

    def test_global_fuel_and_normalization_counts_do_not_reset_per_obligation(self):
        for fuel in (1, 30, 100, 800):
            p = proposal(self.root, cases()[0]["target"], max_expansions=fuel)
            self.assertLessEqual(p["stats"]["expansions"], fuel)
            self.assertLessEqual(p["stats"]["normalization_nodes"], 32768)
            counts = [s["expansions_total"] for s in p["synthesis_analysis"]]
            self.assertEqual(counts, sorted(counts))
        with patch("mra.structure_strategy_terms.WALL_SECONDS", 0):
            self.assertEqual(proposal(self.root, cases()[0]["target"])["math_verdict"], "UNKNOWN")

    def test_strict_schema_budget_and_reference_proof_rejection(self):
        t = cases()[0]["target"]
        for budget in ({"max_expansions": True}, {"max_candidates": 0}, {"max_rewrite_steps": 7}):
            with self.assertRaises(ValueError):
                proposal(self.root, t, **budget)
        with self.assertRaises(ValueError):
            proposal(self.root, {**t, "proof": {"method": "rfl"}})
        with self.assertRaises(ValueError):
            propose(
                self.root,
                {
                    "schema": "structure-recursion-proposals-v1",
                    "target": t,
                    "budget": DEFAULT_BUDGET,
                },
            )

    def test_new_planner_source_drift_does_not_change_old_generator_identity(self):
        t = cases()[0]["target"]
        old = old_propose(
            self.root,
            {"schema": "structure-recursion-proposals-v1", "target": t, "budget": DEFAULT_BUDGET},
        )["source_manifest"]
        before = proposal(self.root, t)["source_manifest"]
        p = self.root / FILES[0]
        p.write_bytes(p.read_bytes() + b"\n")
        self.assertNotEqual(proposal(self.root, t)["source_manifest"], before)
        self.assertEqual(
            old_propose(
                self.root,
                {
                    "schema": "structure-recursion-proposals-v1",
                    "target": t,
                    "budget": DEFAULT_BUDGET,
                },
            )["source_manifest"],
            old,
        )

        shared = self.root / "mra/structure_strategy_scoped.py"
        shared.write_bytes(shared.read_bytes() + b"\n")
        self.assertNotEqual(proposal(self.root, t)["source_manifest"], before)
        self.assertIn(
            "mra/structure_strategy_scoped.py", proposal(self.root, t)["source_manifest"]["files"]
        )
        self.assertIn(
            "mra/structure_composition_terms.py", proposal(self.root, t)["source_manifest"]["files"]
        )

    def test_named_plan_binding_and_diagnostic_drift_fail_closed(self):
        area = self.root / "artifacts/NONEXECUTABLE-obligation"
        area.mkdir()
        store = Checks(self.root, area, "new")
        raw = proposal(self.root, cases()[0]["target"])["candidates"][0]["request"]
        p = store.preview(raw)
        envelope = {
            "request_id": "NONEXECUTABLE",
            "request": raw,
            "plan_fingerprint": p["plan_fingerprint"],
            "reviewer": "NONEXECUTABLE protocol fixture",
            "confirmed": True,
        }
        row = store.submit(envelope)
        self.assertEqual(store.submit(envelope)["run_id"], row["run_id"])
        store.cancel(row["run_id"])
        source = self.root / FILES[0]
        source.write_bytes(source.read_bytes() + b"\n")
        self.assertTrue(store.report(row["run_id"])["invalidated"])
        self.assertEqual(store.work_once(row["run_id"]), {"worker_state": "IDLE"})


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "Explicit real obligation comparison")
class RealTests(unittest.TestCase):
    def test_fixed_complete_goal_comparison_keeps_all_failures(self):
        area = OUT / ("real-" + uuid.uuid4().hex)
        area.mkdir(parents=True)

        def save(n, v):
            (area / n).write_bytes(canonical(v) + b"\n")

        stores = {arm: Checks(ROOT, area, arm) for arm in ("old", "new")}
        plan = {
            "context": stores["new"].context(),
            "dataset": json.loads((ROOT / DATASET).read_text()),
            "cases": [],
        }
        for case in cases():
            plan["cases"].append(
                {
                    **case,
                    "arms": {
                        "old": old_propose(
                            ROOT,
                            {
                                "schema": "structure-recursion-proposals-v1",
                                "target": case["target"],
                                "budget": DEFAULT_BUDGET,
                            },
                        ),
                        "new": proposal(ROOT, case["target"]),
                    },
                }
            )
        save("declared-plan.json", plan)
        results = []
        for i, case in enumerate(plan["cases"]):
            for arm in ("old", "new") if i % 2 == 0 else ("new", "old"):
                success = False
                self.assertEqual(len(case["arms"][arm]["candidates"]), 2)
                for slot, candidate in enumerate(case["arms"][arm]["candidates"]):
                    entry = {
                        "case_id": case["id"],
                        "arm": arm,
                        "slot": slot,
                        "candidate_id": candidate["candidate_id"],
                    }
                    if success:
                        entry["skipped"] = "earlier_current_E4"
                    else:
                        store = stores[arm]
                        raw = candidate["request"]
                        self.assertEqual(raw["target"], case["target"])
                        row = store.submit(
                            {
                                "request_id": uuid.uuid4().hex,
                                "request": raw,
                                "plan_fingerprint": store.preview(raw)["plan_fingerprint"],
                                "reviewer": "MRA074 full original goal and ordered equality obligations reviewer",
                                "confirmed": True,
                            }
                        )
                        store.work_once(row["run_id"])
                        c = store.report(row["run_id"])
                        entry["check"] = c
                        if c["math_verdict"] == "PROVED":
                            verified(c)
                            success = True
                        else:
                            self.assertEqual(
                                (c["math_verdict"], c["evidence_level"]), ("UNKNOWN", "E0")
                            )
                        if case["negative"]:
                            self.assertFalse(success)
                    results.append(entry)
                    save("results.json", results)
        arms = {
            arm: {
                "proved": sum(
                    any(
                        r["case_id"] == c["id"]
                        and r["arm"] == arm
                        and r.get("check", {}).get("math_verdict") == "PROVED"
                        for r in results
                    )
                    for c in plan["cases"]
                ),
                "total": len(plan["cases"]),
            }
            for arm in stores
        }
        required = {
            key: any(
                r["case_id"] == key
                and r["arm"] == "new"
                and r.get("check", {}).get("math_verdict") == "PROVED"
                for r in results
            )
            for key in ("observed-composition", "nested-observers", "binary-context")
        }
        save(
            "summary.json",
            {
                "all_passed": len(results) == 32 and all(required.values()),
                "required_goals": required,
                "arms": arms,
                "slots": len(results),
                "checks": sum("check" in r for r in results),
                "math_verdict": "UNKNOWN",
                "evidence_level": "E0",
            },
        )
        self.assertEqual(len(results), 32)
        self.assertTrue(all(required.values()), required)
