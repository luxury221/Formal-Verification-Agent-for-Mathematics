"""Minimum semantics, exact conditions and version isolation; compilation remains E0."""

from copy import deepcopy
import unittest
from mra.optimization_language import Compiler, MODE, compile_request, catalog
from mra.optimization_examples import (
    stationary,
    minimum_formula,
    scaled_minimum,
    lemma1,
    local_maximum_control,
    minimum,
    real,
    var,
)
from mra.proof_language import compile_request as v1
from mra.proof_logic import compile_request as v2


class OptimizationCompilerTests(unittest.TestCase):
    def test_complete_paper_targets_and_conditions_survive_without_mutation(self):
        for factory, conditions in (
            (stationary, 1),
            (minimum_formula, 1),
            (scaled_minimum, 1),
            (lemma1, 0),
        ):
            raw = factory()
            before = deepcopy(raw)
            compiled = compile_request(raw)
            self.assertEqual(raw, before)
            self.assertEqual(compiled["request"], before)
            self.assertEqual(len(compiled["conditions"]), conditions)
            self.assertEqual([v["name"] for v in compiled["variables"]][:3], ["g", "h", "x"])
        self.assertIn("deriv", compile_request(stationary())["formal_statement"])

    def test_minimum_means_attainment_and_bound_for_every_real_input(self):
        raw = minimum_formula()
        compiled = compile_request(raw)
        self.assertIn("∃", compiled["formal_statement"])
        self.assertIn("∀", compiled["formal_statement"])
        self.assertIn("≤", compiled["formal_statement"])
        self.assertNotIn("sInf", compiled["formal_statement"])
        self.assertIn("attained", catalog()["minimum_semantics"])

    def test_minimum_function_and_value_require_exact_real_types(self):
        c = Compiler([])
        for raw in (
            minimum(real(1), real(0)),
            minimum(
                {"lambda": {"name": "t", "type": "Real", "body": var("t")}},
                {"lambda": {"name": "x", "type": "Real", "body": var("x")}},
            ),
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                c.formula(raw, {})

    def test_minimum_cannot_hide_a_free_variable_or_bind_its_value(self):
        raw = minimum({"lambda": {"name": "t", "type": "Real", "body": var("t")}}, var("t"))
        with self.assertRaises(ValueError):
            Compiler([]).formula(raw, {})
        raw["is_minimum"]["value"] = real(0)
        raw["is_minimum"]["function"]["lambda"]["body"] = var("unbound")
        with self.assertRaises(ValueError):
            Compiler([]).formula(raw, {})

    def test_minimum_does_not_accept_unreviewed_domain_or_infimum_options(self):
        for key in ("domain", "default", "infimum", "assume_attained"):
            raw = minimum_formula()
            raw["goal"]["is_minimum"][key] = True
            with self.subTest(key=key), self.assertRaises(ValueError):
                compile_request(raw)

    def test_stationary_rule_requires_explicit_nonzero_evidence(self):
        raw = stationary()
        raw["proof"]["term"]["rule"]["proofs"] = []
        with self.assertRaises(ValueError):
            compile_request(raw)
        raw = stationary()
        raw["assumptions"] = []
        with self.assertRaises(ValueError):
            compile_request(raw)

    def test_least_squares_vectors_cannot_cross_spaces_or_be_functions(self):
        for second in (var("x"), var("g")):
            raw = minimum_formula()
            raw["proof"]["term"]["rule"]["proofs"][0]["rule"]["terms"][1] = second
            with self.subTest(second=second), self.assertRaises(ValueError):
                compile_request(raw)

    def test_derivative_parameter_cannot_be_a_vector(self):
        raw = stationary()
        raw["proof"]["term"]["rule"]["terms"][2] = var("x")
        with self.assertRaises(ValueError):
            compile_request(raw)

    def test_fixed_templates_do_not_accept_lean_extra_fields_or_extra_proofs(self):
        for change in (
            {"lean": "sorry"},
            {"proofs": [], "terms": [real(0)]},
            {"proofs": [{"hyp": "h_nonzero"}, {"hyp": "h_nonzero"}]},
        ):
            raw = stationary()
            raw["proof"]["term"]["rule"].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                compile_request(raw)

    def test_old_languages_reject_v3_and_v3_rejects_old_modes(self):
        for old in (v1, v2):
            with self.assertRaises(ValueError):
                old(lemma1())
        for mode in ("proof-language-v1", "proof-language-v2", "proof-language-v99"):
            raw = lemma1()
            raw["schema"] = mode
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                compile_request(raw)

    def test_no_nonzero_assumptions_added_to_complete_lemma(self):
        result = compile_request(lemma1())
        self.assertEqual(result["conditions"], [])
        self.assertIn("by_cases hv : v = 0", result["proof_source"])
        self.assertIn("∀ b : ℝ", result["proof_source"])
        self.assertIn("refine ⟨inner", result["proof_source"])

    def test_new_rewrites_still_reject_free_source_and_nonboolean_direction(self):
        for item in (
            {"lemma": "norm_sub_rev; sorry", "reverse": False},
            {"lemma": "norm_sub_rev", "reverse": 1},
        ):
            with self.subTest(item=item), self.assertRaises(ValueError):
                Compiler([]).proof({"normalize": {"equalities": [item]}}, {}, {})

    def test_false_minimum_control_is_only_a_compiled_candidate(self):
        result = compile_request(local_maximum_control())
        self.assertEqual(result["request"]["schema"], MODE)
        self.assertIn("≤", result["formal_statement"])
        self.assertNotIn("math_verdict", result)

    def test_local_lemma_cannot_prove_itself_or_use_a_later_lemma(self):
        formula = {"eq": [real(0), real(0)]}
        for reference in ("first", "later"):
            for constructor in ("steps", "have"):
                lemma = {"name": "first", "formula": formula, "proof": {"hyp": reference}}
                value = (
                    {"lemmas": [lemma], "then": {"hyp": "first"}}
                    if constructor == "steps"
                    else {**lemma, "then": {"hyp": "first"}}
                )
                with (
                    self.subTest(reference=reference, constructor=constructor),
                    self.assertRaises(ValueError),
                ):
                    Compiler([]).proof({constructor: value}, {}, {})

    def test_local_lemmas_reject_shadowing_and_do_not_escape_their_scope(self):
        formula = {"eq": [real(0), real(0)]}
        lemma = {
            "name": "fact",
            "formula": formula,
            "proof": {"rule": {"name": "eq_refl", "terms": [real(0)], "proofs": []}},
        }
        for constructor in ("steps", "have"):
            value = (
                {"lemmas": [lemma], "then": {"hyp": "fact"}}
                if constructor == "steps"
                else {**lemma, "then": {"hyp": "fact"}}
            )
            c, hypotheses = Compiler([]), {}
            c.proof({constructor: value}, {}, hypotheses)
            self.assertEqual(hypotheses, {})
            with self.subTest(constructor=constructor), self.assertRaises(ValueError):
                c.proof({"hyp": "fact"}, {}, hypotheses)
            with self.subTest(constructor=constructor), self.assertRaises(ValueError):
                c.proof({constructor: value}, {}, {"fact": "existing"})


class OptimizationRoutingTests(unittest.TestCase):
    def setUp(self):
        import shutil
        import tempfile
        from pathlib import Path
        import test_optimization_research as helpers
        from mra.proof_research import SOURCES as v1sources
        from mra.proof_logic_research import SOURCES as v2sources
        from mra.proof_logic_lab import TRUST_FILES as v2trust
        from mra.proof_router import ProofResearchRouter

        self.helpers = helpers
        self.temp = tempfile.TemporaryDirectory(dir=helpers.ROOT / ".tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        helpers.copy_sources(self.root)
        for name in (*helpers.TRUST_FILES, *helpers.SOURCES, *v1sources, *v2sources, *v2trust):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(helpers.ROOT / name, target)
        self.project = helpers.parent(self.root)
        self.store = ProofResearchRouter(self.root)

    def payload(self, raw, request_id):
        plan = self.store.preview(raw)
        return {
            "request_id": request_id,
            "request": raw,
            "plan_fingerprint": plan["plan_fingerprint"],
        }

    def test_v3_graph_is_idempotent_and_preserves_original_minimum_target(self):
        raw = {
            "project_id": self.project["project_id"],
            "branch_id": "main",
            "nodes": [{"id": "root", "kind": "problem", "depends_on": [], "target": lemma1()}],
        }
        payload = self.payload(raw, self.helpers.rid())
        first = self.store.create(payload)
        self.assertTrue(first["graph_id"].startswith("proof3flow-"))
        self.assertEqual(first["plan"]["request"], raw)
        self.assertEqual(first["math_verdict"], "UNKNOWN")
        self.assertEqual(self.store.create(payload)["graph_id"], first["graph_id"])
        self.assertEqual(
            self.store.catalog(self.project["project_id"], "main")["graphs"][0]["language_version"],
            MODE,
        )

    def test_same_request_cannot_cross_v2_to_v3_and_mixed_versions_fail(self):
        from mra.proof_logic_examples import equality_case

        raw = self.helpers.raw(self.project)
        raw["nodes"] = [
            {"id": "root", "kind": "problem", "depends_on": [], "target": equality_case()}
        ]
        request_id = self.helpers.rid()
        old = self.store.create(self.payload(raw, request_id))
        raw["nodes"][0]["target"] = lemma1()
        with self.assertRaises(ValueError):
            self.store.create(self.payload(raw, request_id))
        self.assertEqual(
            self.store.report(old["graph_id"])["plan"]["schema"], "proof-project-research-v2"
        )
        raw["nodes"].append(
            {"id": "old", "kind": "lemma", "depends_on": [], "target": equality_case()}
        )
        with self.assertRaises(ValueError):
            self.store.preview(raw)

    def test_four_queues_are_served_in_order_and_catalog_advertises_exact_versions(self):
        from unittest.mock import patch
        from contextlib import ExitStack
        from mra.proof_router import catalog as router_catalog

        calls = []
        with ExitStack() as stack:
            for version, store in self.store.stores.items():

                def work(version=version):
                    calls.append(version)
                    return {"worker_state": "EXECUTED"}

                stack.enter_context(patch.object(store, "work_once", side_effect=work))
            for _ in range(4):
                self.store.work_once()
        self.assertEqual(
            calls, ["proof-language-v1", "proof-language-v2", MODE, "proof-language-v4"]
        )
        value = router_catalog()
        self.assertEqual(value["supported_languages"], calls)
        self.assertEqual(value["default_language"], "proof-language-v4")
        self.assertEqual(len(value["examples"]), 11)


if __name__ == "__main__":
    unittest.main()
