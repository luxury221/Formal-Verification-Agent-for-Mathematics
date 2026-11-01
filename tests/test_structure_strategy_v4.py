"""Scope/candidate contracts and opt-in genuine v3/v4 diagnostics, never mock proofs."""

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

import test_structure_research as core
from mra.domain import canonical, digest
from mra.integrity import file_hash
from mra.reuse_evaluation_checks import initialize
from mra.structure_strategies_v3 import manifest as old_manifest, propose as old_propose
from mra.structure_strategies_v4 import (
    DEFAULT_BUDGET,
    FILES,
    SCHEMA,
    graph_proposals,
    manifest,
    propose,
)
from mra.structure_strategy_scoped import ScopedPlanner
from mra.structure_strategy_terms import MAX_NORMALIZATION_NODES
from mra.function_dynamics_checks import verified
from mra.structured_terms import compile_request
from mra.structures import StructureStore, prepare
from mra.verifier import NativeLeanBackend

ROOT = Path(__file__).resolve().parents[1]
DATASET = "evals/structure-strategy-v4-v1/cases.json"
OUT = ROOT / "artifacts/structure-strategy-v4-20261029-01"


def cases():
    return json.loads((ROOT / DATASET).read_text())["cases"]


def raw(target, **budget):
    return {"schema": SCHEMA, "target": target, "budget": {**DEFAULT_BUDGET, **budget}}


def var(name):
    return {"var": name}


def fold(index, zero, body, k="j", acc="acc"):
    return {
        "nat_fold": {"index": index, "zero": zero, "step": {"index": k, "value": acc, "body": body}}
    }


def quant(name, body, typ="a", op="forall"):
    return {op: {"binders": [{"name": name, "type": typ}], "body": body}}


class DiagnosticChecks(StructureStore):
    """Private checks bind both generators, dataset and this runner to original checker."""

    def __init__(self, root, directory):
        initialize(self, root, directory, "checks", True)

    def context(self):
        return {
            "v3": old_manifest(self.root),
            "v4": manifest(self.root),
            "dataset": file_hash(self.root / DATASET),
            "runner": file_hash(self.root / "tests/test_structure_strategy_v4.py"),
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
            return "V4 diagnostic source, generator or dataset changed"
        view = deepcopy(row)
        view["plan"]["source_manifest"] = row["plan"]["source_manifest"]["provider"]
        return StructureStore.gate(self, view, check_files)


class ContractTests(unittest.TestCase):
    def setUp(self):
        core.ContractTests.setUp(self)
        for n in (
            *FILES,
            "mra/structure_strategies_v3.py",
            "mra/structure_strategy_generalization.py",
            "tests/test_structure_strategy_v4.py",
            DATASET,
        ):
            p = self.root / n
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / n, p)
        self.engine = ScopedPlanner(cases()[1]["target"], DEFAULT_BUDGET)

    def test_complete_targets_immutable_unknown_and_no_execution(self):
        for case in cases():
            request = raw(case["target"], max_candidates=2)
            before = deepcopy(request)
            with patch.object(
                NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")
            ):
                p = propose(self.root, request)
            self.assertEqual(request, before)
            self.assertEqual(
                (p["math_verdict"], p["evidence_level"], p["lean_calls"], p["model_calls"]),
                ("UNKNOWN", "E0", 0, 0),
            )
            self.assertLessEqual(len(p["candidates"]), 2)
            for c in p["candidates"]:
                self.assertEqual(
                    {k: v for k, v in c["request"].items() if k != "proof"}, case["target"]
                )
                self.assertEqual(
                    compile_request(c["request"])["formal_statement"], p["formal_statement"]
                )
                self.assertEqual((c["math_verdict"], c["evidence_level"]), ("UNKNOWN", "E0"))

    def test_fold_free_parameter_matches_with_alpha_renamed_step(self):
        pattern = fold(var("n"), var("x"), var("acc"))
        value = fold(var("n"), {"call": {"name": "q", "args": [var("x")]}}, var("r"), "k", "r")
        bindings = {}
        self.assertTrue(self.engine.match(pattern, value, {"x": "a"}, bindings))
        self.assertEqual(bindings["x"], value["nat_fold"]["zero"])

    def test_no_escape_from_fold_or_quantifier_and_transactional_failure(self):
        for p, v in (
            (fold(var("n"), var("x"), var("x")), fold(var("n"), var("x"), var("acc"))),
            (quant("u", {"eq": [var("x"), var("u")]}), quant("v", {"eq": [var("v"), var("v")]})),
        ):
            bindings = {"preserved": var("x")}
            self.assertFalse(self.engine.match(p, v, {"x": "a"}, bindings))
            self.assertEqual(bindings, {"preserved": var("x")})

    def test_nested_shadowing_and_quantifier_types(self):
        p = quant("u", quant("u", {"eq": [var("u"), var("x")]}))
        v = quant("outer", quant("inner", {"eq": [var("inner"), var("x")]}))
        self.assertTrue(self.engine.match(p, v, {"x": "a"}, {}))
        bad = quant("outer", quant("inner", {"eq": [var("outer"), var("x")]}))
        self.assertFalse(self.engine.match(p, bad, {"x": "a"}, {}))
        self.assertFalse(self.engine.match(p, quant("outer", v, "Nat"), {"x": "a"}, {}))
        self.assertFalse(self.engine.match(p, quant("u", p, op="exists"), {"x": "a"}, {}))

    def test_repeated_binding_type_and_free_bound_name_collision(self):
        p = {"eq": [var("x"), var("x")]}
        bindings = {}
        self.assertFalse(self.engine.match(p, {"eq": [var("x"), {"nat": 0}]}, {"x": "a"}, bindings))
        self.assertEqual(bindings, {})
        self.assertFalse(self.engine.match(var("x"), {"nat": 0}, {"x": "a"}, {}))
        self.assertFalse(self.engine.match(quant("u", var("x")), quant("x", var("x")), {}, {}))

    def test_replacement_renames_binders_and_keeps_rewrite_outside_binder_body(self):
        p = quant("u", {"eq": [var("x"), var("u")]})
        replaced = self.engine.replace(p, {"x": var("u")})
        self.assertNotEqual(replaced["forall"]["binders"][0]["name"], "u")
        self.assertEqual(replaced["forall"]["body"]["eq"][0], var("u"))
        lhs = fold(var("n"), var("x"), {"call": {"name": "q", "args": [var("acc")]}})
        goal = {"eq": [lhs, lhs]}
        self.assertIsNone(
            self.engine.rewrite(
                goal,
                ("law", {"x": "a"}, {"call": {"name": "q", "args": [var("x")]}}, var("x")),
                False,
            )
        )

    def test_fold_law_is_usable_without_treating_binders_as_wildcards(self):
        p = propose(self.root, raw(cases()[2]["target"], max_candidates=1))
        c = p["candidates"][0]
        self.assertEqual(c["origin"], "symbolic-rewrite")
        self.assertEqual(c["request"]["proof"]["steps"], [{"rule": "transport", "reverse": False}])
        self.assertEqual(c["request"]["proof"]["finish"], "rfl")

    def test_symbolic_candidate_gets_scarce_slot_and_directions_are_diverse(self):
        p = propose(self.root, raw(cases()[0]["target"], max_candidates=1))
        self.assertEqual(p["candidates"][0]["origin"], "symbolic-rewrite")
        old = old_propose(
            self.root,
            {
                **raw(cases()[0]["target"], max_candidates=1),
                "schema": "structure-strategy-proposals-v3",
            },
        )
        self.assertEqual(old["candidates"][0]["request"]["proof"], {"method": "rfl"})
        p = propose(self.root, raw(cases()[4]["target"], max_candidates=2))
        self.assertEqual(
            {c["request"]["proof"].get("variable") for c in p["candidates"]}, {"m", "n"}
        )

    def test_generalization_preserves_premise_dependencies_and_lexical_free_names(self):
        goal = {"eq": [fold(var("n"), var("x"), var("acc")), var("x")]}
        options = self.engine.options(goal, "n")
        self.assertIn(["x"], options)
        self.engine.target["assumptions"].append(
            {"name": "condition", "formula": {"eq": [var("x"), var("x")]}}
        )
        self.assertEqual(self.engine.options(goal, "n"), [[]])
        self.assertEqual(
            self.engine.generalization_analysis[-1]["excluded"],
            [{"variable": "x", "reason": "original_premise_dependency"}],
        )

    def test_strict_schema_budget_and_shared_hard_fuel(self):
        t = cases()[1]["target"]
        for changes in ({"max_expansions": True}, {"max_candidates": 0}, {"max_rewrite_steps": 7}):
            with self.assertRaises(ValueError):
                propose(self.root, raw(t, **changes))
        for r in (
            {**raw(t), "schema": "structure-strategy-proposals-v3"},
            raw({**t, "proof": {"method": "rfl"}}),
        ):
            with self.assertRaises(ValueError):
                propose(self.root, r)
        p = propose(self.root, raw(t, max_expansions=1))
        self.assertLessEqual(p["stats"]["expansions"], 1)
        self.assertLessEqual(p["stats"]["normalization_nodes"], MAX_NORMALIZATION_NODES)
        with patch("mra.structure_strategy_terms.WALL_SECONDS", 0):
            self.assertEqual(
                propose(self.root, raw(t))["stats"]["stop_reason"], "synthesis_wall_limit"
            )

    def test_old_source_identity_stays_separate_and_saved_v4_check_invalidates(self):
        old = old_manifest(self.root)
        (self.root / "private").mkdir()
        store = DiagnosticChecks(self.root, self.root / "private")
        request = {**cases()[0]["target"], "proof": {"method": "rfl"}}
        row = store.submit(
            {
                "request_id": "v4-fixture",
                "request": request,
                "plan_fingerprint": store.preview(request)["plan_fingerprint"],
                "reviewer": "NONEXECUTABLE protocol fixture",
                "confirmed": True,
            }
        )
        p = self.root / "mra/structure_strategy_scoped.py"
        p.write_bytes(p.read_bytes() + b"\n")
        self.assertEqual(old_manifest(self.root), old)
        self.assertTrue(store.report(row["run_id"])["invalidated"])

    def test_graph_feedback_uses_original_current_target_without_submission(self):
        before = self.store.report(self.key)
        value = graph_proposals(
            self.store, {"graph_id": self.key, "node_id": "unit", "budget": DEFAULT_BUDGET}
        )
        self.assertEqual(value["context"]["revision"], before["revision"])
        self.assertEqual(self.store.report(self.key), before)
        self.assertEqual(value["schema"], SCHEMA)


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "Explicit genuine v3/v4 comparison")
class RealTests(unittest.TestCase):
    def test_declared_equal_budget_tasks_keep_all_failures_and_stop_only_at_e4(self):
        out = OUT / ("real-" + uuid.uuid4().hex)
        out.mkdir(parents=True)

        def save(n, v):
            (out / n).write_bytes(canonical(v) + b"\n")

        store = DiagnosticChecks(ROOT, out)
        plan = {
            "context": store.context(),
            "dataset": json.loads((ROOT / DATASET).read_text()),
            "cases": [],
        }
        for case in cases():
            entry = {"id": case["id"], "target": case["target"], "arms": {}}
            for arm, provider, schema in (
                ("v3", old_propose, "structure-strategy-proposals-v3"),
                ("v4", propose, SCHEMA),
            ):
                entry["arms"][arm] = provider(
                    ROOT, {**raw(case["target"], max_candidates=2), "schema": schema}
                )
            plan["cases"].append(entry)
        save("declared-plan.json", plan)
        results = []
        for i, case in enumerate(plan["cases"]):
            for arm in ("v3", "v4") if i % 2 == 0 else ("v4", "v3"):
                success = False
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
                        request = candidate["request"]
                        self.assertEqual(
                            {k: v for k, v in request.items() if k != "proof"}, case["target"]
                        )
                        row = store.submit(
                            {
                                "request_id": "v4-" + uuid.uuid4().hex,
                                "request": request,
                                "plan_fingerprint": store.preview(request)["plan_fingerprint"],
                                "reviewer": "MRA072 exact authored/adapted full-target alignment reviewer",
                                "confirmed": True,
                            }
                        )
                        store.work_once(row["run_id"])
                        child = store.report(row["run_id"])
                        entry["check"] = child
                        if child["math_verdict"] == "PROVED":
                            verified(child)
                            success = True
                        else:
                            self.assertEqual(
                                (child["math_verdict"], child["evidence_level"]), ("UNKNOWN", "E0")
                            )
                        if case["id"] == "missing-indexed-invariant":
                            self.assertFalse(success)
                    results.append(entry)
                    save("results.json", results)
        summary = {
            arm: {
                "total": len(plan["cases"]),
                "proved": sum(
                    any(
                        r["case_id"] == c["id"]
                        and r["arm"] == arm
                        and r.get("check", {}).get("math_verdict") == "PROVED"
                        for r in results
                    )
                    for c in plan["cases"]
                ),
            }
            for arm in ("v3", "v4")
        }
        save(
            "summary.json",
            {
                "all_passed": True,
                "arms": summary,
                "slots": len(results),
                "checks": sum("check" in r for r in results),
                "math_verdict": "UNKNOWN",
                "evidence_level": "E0",
            },
        )
        self.assertEqual(len(results), 24)
