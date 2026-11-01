"""Logical trust boundaries; compilation is not mathematical evidence."""

from copy import deepcopy
import unittest

from mra.proof_logic import Compiler, MODE, bounded, compile_request, catalog
from mra.proof_language import compile_request as old_compile
from mra.proof_logic_examples import equality_case, zero_counterexample


class LogicCompilerTests(unittest.TestCase):
    def test_all_complete_targets_keep_original_inputs_and_no_extra_conditions(self):
        for target in (equality_case(), equality_case(proposition=True), zero_counterexample()):
            original = deepcopy(target)
            result = compile_request(target)
            self.assertEqual(result["request"], original)
            self.assertEqual(target, original)
            self.assertEqual(result["conditions"], [])
            self.assertIn("∃", result["formal_statement"])
        self.assertIn("↔", compile_request(equality_case())["formal_statement"])

    def test_old_and_new_languages_never_silently_adopt_each_other(self):
        target = equality_case()
        with self.assertRaises(ValueError):
            old_compile(target)
        target["schema"] = "proof-language-v1"
        with self.assertRaises(ValueError):
            compile_request(target)
        self.assertEqual(catalog()["mode"], MODE)

    def test_steps_cannot_assume_themselves_or_forward_reference(self):
        for name in ("norm_dependence", "coefficient_dependence", "g"):
            target = equality_case()
            lemma = target["proof"]["term"]["steps"]["lemmas"][0]
            if name == "g":
                lemma["name"] = name
            else:
                lemma["proof"] = {"hyp": name}
            with self.subTest(name=name), self.assertRaises(ValueError):
                compile_request(target)

    def test_existential_witness_does_not_bind_a_new_free_variable(self):
        with self.assertRaises(ValueError):
            Compiler([]).proof(
                {
                    "exists_intro": {
                        "witness": {"var": "new"},
                        "proof": {"rule": {"name": "one_ne_zero", "terms": [], "proofs": []}},
                    }
                },
                {},
                {},
            )

    def test_existential_elimination_rejects_shadowing_and_self_evidence(self):
        raw = {
            "exists_elim": {
                "proof": {"hyp": "given"},
                "name": "x",
                "type": "Real",
                "hypothesis": "hx",
                "formula": {"truth": True},
                "then": {"hyp": "hx"},
            }
        }
        c = Compiler([])
        for scope, hypotheses in (
            ({"x": ("mraX", ("Real",))}, {"given": "mraGiven"}),
            ({}, {"given": "mraGiven", "hx": "mraOld"}),
            ({}, {}),
        ):
            with self.subTest(scope=scope, hypotheses=hypotheses), self.assertRaises(ValueError):
                c.proof(raw, scope, hypotheses)

    def test_disjunction_branches_do_not_share_evidence(self):
        raw = {
            "or_cases": {
                "proof": {"hyp": "given"},
                "left": {"name": "left", "proof": {"hyp": "left"}},
                "right": {"name": "right", "proof": {"hyp": "left"}},
            }
        }
        with self.assertRaises(ValueError):
            Compiler([]).proof(raw, {}, {"given": "mraGiven"})

    def test_case_split_name_cannot_shadow_original_assumptions(self):
        raw = {
            "by_cases": {
                "name": "original",
                "formula": {"truth": True},
                "yes": {"hyp": "original"},
                "no": {"hyp": "original"},
            }
        }
        with self.assertRaises(ValueError):
            Compiler([]).proof(raw, {}, {"original": "mraOriginal"})

    def test_simplification_rejects_free_source_nonboolean_direction_and_unbounded_rules(self):
        for entries in (
            [{"lemma": "one_smul; sorry", "reverse": False}],
            [{"lemma": "one_smul", "reverse": 0}],
            [{"lemma": "one_smul", "reverse": False}] * 9,
        ):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                Compiler([]).proof({"normalize": {"equalities": entries}}, {}, {})

    def test_budget_is_checked_before_recursive_compilation(self):
        for raw in ([None] * 2401, {"deep": None}, {"float": 1.5}):
            if isinstance(raw, dict) and "deep" in raw:
                for _ in range(65):
                    raw = {"deep": raw}
            with self.subTest(kind=type(raw).__name__), self.assertRaises(ValueError):
                bounded(raw)

    def test_no_arbitrary_imports_axioms_or_extra_fields(self):
        for key in ("imports", "axioms", "extra_declarations"):
            target = equality_case()
            target[key] = "axiom bad : False"
            with self.subTest(key=key), self.assertRaises(ValueError):
                compile_request(target)


if __name__ == "__main__":
    unittest.main()
