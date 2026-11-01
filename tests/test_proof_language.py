"""Compiler boundaries and lexical proof scope; these tests are not mathematical evidence."""

from copy import deepcopy
import unittest

from mra.calculus_language import compile_request as old_compile
from mra.proof_language import Compiler, MODE, catalog, compile_request
from mra.proof_examples import arithmetic, equation25, rule


class ProofLanguageTests(unittest.TestCase):
    def test_full_original_conditions_and_target_are_retained(self):
        request = equation25()
        plan = compile_request(request)
        self.assertEqual(plan["request"], request)
        self.assertEqual(plan["conditions"], [])
        self.assertNotIn("≠", plan["formal_statement"])
        self.assertEqual(
            [s["kind"] for s in plan["proof_steps"]], ["rule", "have", "rewrite", "have", "rule"]
        )
        request = arithmetic()
        request["assumptions"].append(
            {"name": "unused", "formula": {"lt": [{"var": "x"}, {"real": [100, 1]}]}}
        )
        self.assertEqual(
            [c["name"] for c in compile_request(request)["conditions"]], ["ordered", "unused"]
        )

    def test_old_and_new_versions_do_not_silently_adopt_each_other(self):
        request = equation25()
        with self.assertRaises(ValueError):
            old_compile(request)
        request["schema"] = "calculus-language-v1"
        with self.assertRaises(ValueError):
            compile_request(request)
        self.assertEqual(catalog()["mode"], MODE)

    def test_have_cannot_assume_itself_or_shadow_an_original(self):
        for change in ("self", "variable", "duplicate"):
            request = equation25()
            outer = request["proof"]["term"]["have"]
            if change == "self":
                outer["proof"] = {"hyp": "inner_bound"}
            elif change == "variable":
                outer["name"] = "g"
            else:
                outer["then"]["have"]["name"] = "inner_bound"
            with self.subTest(change=change), self.assertRaises(ValueError):
                compile_request(request)

    def test_local_lemma_does_not_escape_to_a_sibling(self):
        request = equation25()
        body = deepcopy(request["proof"]["term"])
        request["proof"]["term"] = rule("and_intro", proofs=[body, {"hyp": "square_bound"}])
        with self.assertRaises(ValueError):
            compile_request(request)

    def test_rewrite_is_explicit_bounded_and_never_accepts_source(self):
        for bad in (
            [],
            [{"lemma": "sq_abs; sorry", "reverse": False}],
            [{"lemma": "sq_abs", "reverse": 1}],
            [{"lemma": "sq_abs", "reverse": False}] * 9,
        ):
            request = equation25()
            request["proof"]["term"]["have"]["then"]["have"]["proof"]["rewrite"]["equalities"] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                compile_request(request)

    def test_rewrite_with_local_equality_stays_in_lexical_scope(self):
        compiler = Compiler([])
        value = {
            "rewrite": {
                "proof": {"hyp": "base"},
                "equalities": [{"proof": {"hyp": "equality"}, "reverse": True}],
            }
        }
        source = compiler.proof(value, {}, {"base": "mraBase", "equality": "mraEquality"})
        self.assertIn("← (mraEquality)", source)
        self.assertIn("simpa only", source)
        with self.assertRaises(ValueError):
            compiler.proof(value, {}, {"base": "mraBase"})

    def test_rule_types_arities_and_unapproved_theorems_are_rejected(self):
        for proof in (
            rule("real_inner_mul_inner_self_le", [{"real": [1, 1]}]),
            rule("sub_nonneg_of_le"),
            rule("sorryAx"),
            {"rule": {"name": [], "terms": [], "proofs": []}},
            rule("mul_le_mul_of_nonneg_left", [{"var": "g"}], [{"hyp": "missing"}] * 2),
        ):
            request = equation25()
            request["proof"] = {"term": proof}
            with self.subTest(proof=proof), self.assertRaises(ValueError):
                compile_request(request)
        request = equation25()
        request["proof"] = {
            "term": rule(
                "real_inner_mul_inner_self_le",
                [{"var": "x"}, {"apply": [{"var": "g"}, {"var": "x"}]}],
            )
        }
        with self.assertRaises(ValueError):
            compile_request(request)

    def test_arithmetic_uses_only_fixed_methods_and_supplied_facts(self):
        request = arithmetic()
        self.assertIn("linarith only [mraHyp0]", compile_request(request)["proof_source"])
        for bad in (
            {"method": "linarith; sorry", "proofs": []},
            {"method": "ring", "proofs": [{"hyp": "ordered"}]},
            {"method": "nlinarith", "proofs": [{"hyp": "missing"}]},
        ):
            request["proof"] = {"term": {"arithmetic": bad}}
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                compile_request(request)

    def test_no_arbitrary_imports_axioms_or_extra_proof_fields(self):
        for field in ("imports", "extra_declarations", "axioms"):
            request = equation25()
            request[field] = "axiom bad : False"
            with self.assertRaises(ValueError):
                compile_request(request)
        request = equation25()
        request["proof"]["term"]["have"]["source"] = "sorry"
        with self.assertRaises(ValueError):
            compile_request(request)

    def test_nested_proofs_have_distinct_names_and_original_inputs_are_not_mutated(self):
        request = equation25()
        original = deepcopy(request)
        plan = compile_request(request)
        names = [s["lean"] for s in plan["proof_steps"] if s["kind"] == "have"]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(request, original)


if __name__ == "__main__":
    unittest.main()
