"""Typed finite-lp syntax, scope and explicit mathematical premises; no kernel fixtures."""

from copy import deepcopy
import unittest

from mra.finite_lp_language import Compiler, MODE, compile_request, catalog
from mra.finite_lp_examples import (
    holder_witness,
    norm_power,
    rational_power,
    real,
    vec,
    lp,
    rule,
    compute,
)
from mra.proof_language import compile_request as v1
from mra.proof_logic import compile_request as v2
from mra.optimization_language import compile_request as v3
from mra.optimization_examples import lemma1


class FiniteLpCompilerTests(unittest.TestCase):
    def test_full_witness_keeps_functions_rational_parameters_and_zero_condition(self):
        raw = holder_witness()
        before = deepcopy(raw)
        compiled = compile_request(raw)
        self.assertEqual(raw, before)
        self.assertEqual(compiled["request"], before)
        self.assertEqual(compiled["conditions"], [])
        self.assertEqual(compiled["formal_statement"].count("∃"), 5)
        self.assertIn("Fin 2 → ℝ", compiled["formal_statement"])
        self.assertIn("Finset.univ.sum", compiled["formal_statement"])
        self.assertNotIn("math_verdict", compiled)

    def test_finite_dimensions_reject_bool_zero_negative_and_excessive_values(self):
        for value in (True, 0, -1, 9, "2", 2.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Compiler([]).typ({"finite_vector": value})

    def test_literals_require_bounded_nonempty_scalar_coordinates(self):
        for value in ([], [real(0)] * 9, [vec(1)], [True], "sorry"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Compiler([]).expr({"finite_vector": value}, {})

    def test_coordinate_rejects_out_of_range_bool_and_nonvector(self):
        for vector, index in ((vec(1, 2), 2), (vec(1, 2), -1), (vec(1, 2), True), (real(1), 0)):
            with self.subTest(vector=vector, index=index), self.assertRaises(ValueError):
                Compiler([]).expr({"coordinate": {"vector": vector, "index": index}}, {})

    def test_dot_requires_matching_explicit_dimensions(self):
        for a, b in ((vec(1), vec(1, 2)), (real(1), real(2))):
            with self.subTest(a=a, b=b), self.assertRaises(ValueError):
                Compiler([]).expr({"dot": [a, b]}, {})

    def test_finite_vectors_cannot_silently_use_pi_supremum_norm_or_inner(self):
        for value in ({"norm": vec(1, 2)}, {"inner": [vec(1, 2), vec(1, 2)]}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "explicit"):
                Compiler([]).expr(value, {})

    def test_inherited_norm_and_inner_rules_cannot_bypass_finite_vector_semantics(self):
        u, v, p = vec(1, 2), vec(1, 4), real(2)
        for name, terms in (
            ("inner_self_nonneg", [u]),
            ("cauchy_schwarz", [u, v]),
            ("norm_nonneg", [u]),
            ("norm_add_le", [u, v]),
            ("norm_smul", [p, u]),
            ("inner_self_norm_sq", [u]),
            ("real_inner_mul_inner_self_le", [u, v]),
            ("norm_cs_equality", [u, v]),
            ("least_squares_minimum", [u, v]),
            ("least_squares_derivative", [u, v, p]),
            ("least_squares_stationary", [u, v, p]),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "explicit"):
                Compiler([]).proof(rule(name, terms), {}, {})

    def test_finite_pointwise_linear_rules_remain_available(self):
        source = Compiler([]).proof(rule("eq_refl", [vec(1, 2)]), {}, {})
        self.assertIn("Eq.refl", source)
        self.assertIn("Fin 2", source)

    def test_lp_expressions_require_scalar_exponent_finite_vector_and_exact_fields(self):
        for value in (
            {"exponent": vec(1), "vector": vec(1)},
            {"exponent": real(2), "vector": real(1)},
            {"exponent": real(2), "vector": vec(1), "assume_positive": True},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Compiler([]).expr({"lp_norm": value}, {})

    def test_real_power_cannot_take_vectors(self):
        for operands in ([vec(1), real(2)], [real(2), vec(1)]):
            with self.subTest(operands=operands), self.assertRaises(ValueError):
                Compiler([]).expr({"real_rpow": operands}, {})

    def test_total_expression_does_not_invent_positivity(self):
        c = Compiler([])
        source, _ = c.expr(lp(real(0), vec(0)), {})
        self.assertIn("/", source)
        positive = compile_request(norm_power())
        self.assertEqual([condition["name"] for condition in positive["conditions"]], ["positive"])
        raw = norm_power()
        raw["assumptions"] = []
        with self.assertRaises(ValueError):
            compile_request(raw)
        with self.assertRaises(ValueError):
            c.proof(rule("lp_norm_rpow", [real(2), vec(1)]), {}, {})

    def test_rational_root_certificate_requires_explicit_nonnegative_and_root_power(self):
        proof = rational_power()["proof"]["term"]["finite_compute"]["equalities"][0]["proof"]
        for field in ("nonnegative", "root_power"):
            broken = deepcopy(proof)
            del broken["rational_power"][field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                Compiler([]).proof(broken, {}, {})

    def test_rational_power_indices_are_strict_bounded_integers(self):
        proof = rational_power()["proof"]["term"]["finite_compute"]["equalities"][0]["proof"]
        for field, value in (
            ("denominator", 0),
            ("denominator", True),
            ("denominator", 9),
            ("numerator", -1),
            ("numerator", True),
            ("numerator", 9),
        ):
            broken = deepcopy(proof)
            broken["rational_power"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                Compiler([]).proof(broken, {}, {})

    def test_coordinate_congruence_requires_matching_types_and_valid_index(self):
        for b, index in ((vec(1), 0), (vec(1, 2), 2), (vec(1, 2), True)):
            value = {
                "coordinate_eq": {"left": vec(1, 2), "right": b, "index": index, "proof": compute()}
            }
            with self.subTest(b=b, index=index), self.assertRaises(ValueError):
                Compiler([]).proof(value, {}, {})

    def test_fixed_compute_rejects_source_injection_and_truthy_direction(self):
        for value in (
            {"finite_compute": {"equalities": [], "lean": "sorry"}},
            {"finite_compute": {"equalities": [{"lemma": "rfl; sorry", "reverse": False}]}},
            {"finite_compute": {"equalities": [{"lemma": "mul_comm", "reverse": 1}]}},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Compiler([]).proof(value, {}, {})

    def test_malformed_rule_names_are_rejected_before_inherited_dispatch(self):
        for name in (None, True, [], {}, ["norm_nonneg"]):
            with self.subTest(name=name), self.assertRaises(ValueError):
                Compiler([]).proof(rule(name), {}, {})

    def test_natural_real_power_rule_requires_integer_literal_and_exact_argument_counts(self):
        for exponent in (real(1, 2), real(2, 2), real(-1), real(9), {"var": "n"}):
            with self.subTest(exponent=exponent), self.assertRaises(ValueError):
                Compiler([]).proof(rule("real_rpow_nat", [real(2), exponent]), {}, {})
        with self.assertRaises(ValueError):
            Compiler([]).proof(rule("real_rpow_mul", [real(2), real(3), real(4)]), {}, {})

    def test_literals_and_coordinates_do_not_capture_user_symbols(self):
        c = Compiler([])
        _, source, _, scope = c.binding({"name": "mraLpIndex", "type": "Real"}, {}, "test")
        expression, _ = c.expr({"finite_vector": [{"var": "mraLpIndex"}, real(2)]}, scope)
        self.assertIn(source, expression)
        with self.assertRaises(ValueError):
            c.expr({"coordinate": {"vector": {"var": "outside"}, "index": 0}}, scope)

    def test_versions_do_not_fallback_or_auto_upgrade(self):
        for old in (v1, v2, v3):
            with self.subTest(old=old), self.assertRaises(ValueError):
                old(holder_witness())
        for version in ("proof-language-v3", "proof-language-v99"):
            raw = holder_witness()
            raw["schema"] = version
            with self.subTest(version=version), self.assertRaises(ValueError):
                compile_request(raw)

    def test_prior_mathematical_target_is_preserved_when_explicitly_selected_as_v4(self):
        old = lemma1()
        target = v3(old)["formal_statement"]
        old["schema"] = MODE
        self.assertEqual(compile_request(old)["formal_statement"], target)

    def test_catalog_states_dimension_limit_total_power_and_explicit_norm(self):
        value = catalog()
        self.assertEqual(value["mode"], MODE)
        self.assertEqual(value["limits"]["finite_dimension"], 8)
        self.assertIn("total", value["finite_vector_semantics"]["real_rpow"])
        self.assertIn("lp_norm", value["finite_vector_semantics"]["norm"])
        self.assertIn("p>=1", value["finite_vector_semantics"]["norm"])
        self.assertIn("exclude finite vectors", value["finite_vector_semantics"]["inherited_rules"])
        self.assertIn({"finite_vector": "dimension 1..8"}, value["types"])
        self.assertTrue(
            {"finite_vector", "coordinate", "dot", "lp_sum", "lp_norm", "real_rpow"}
            <= set(value["terms"])
        )


if __name__ == "__main__":
    unittest.main()
