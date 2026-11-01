"""Expansion must preserve complete goals, binders, external identities and input bounds."""

from copy import deepcopy
import unittest
from mra.analysis_examples import op, r, rule, v
from mra.domain import canonical
from mra.pde_language import compile_request
from mra.pde_language_examples import example, source_apply
from mra.pde_research_examples import examples
from mra.pde_research_graph import SCHEMA, compile_graph
from mra.pde_expansion import expand_graph


def graph():
    return {"schema": SCHEMA, "nodes": deepcopy(examples()[1]["nodes"])}


class ExpansionTests(unittest.TestCase):
    def test_four_steps_preserve_exact_final_statement_and_all_original_conditions(self):
        raw = graph()
        before = canonical(raw)
        result = expand_graph(raw)
        final = result["nodes"][0]["target"]
        self.assertEqual(canonical(raw), before)
        self.assertEqual(len(result["steps"]), 4)
        original = next(n["target"] for n in raw["nodes"] if n["kind"] == "problem")
        for key in ("goal", "variables", "assumptions", "original", "timeout_seconds"):
            self.assertEqual(final[key], original[key])
        self.assertEqual(len(final["sources"]), 1)
        self.assertEqual(
            compile_request(final)["formal_statement"],
            compile_graph(raw)["node_plans"]["energy"]["formal_statement"],
        )
        self.assertFalse(result["intermediate_types_kernel_checked"])
        self.assertTrue(all(s["math_verdict"] == "UNKNOWN" for s in result["steps"]))

    def test_free_argument_is_not_captured_by_callee_binder(self):
        lemma = example("scalar-cauchy-schwarz")
        lemma.update(
            variables=[{"name": "x", "type": "Real"}],
            goal={
                "forall": {
                    "name": "y",
                    "type": "Real",
                    "body": op("eq", op("mul", v("x"), v("y")), op("mul", v("y"), v("x"))),
                }
            },
            proof={
                "term": {
                    "intro": {
                        "name": "y",
                        "type": "Real",
                        "proof": rule("mul_comm", [v("x"), v("y")]),
                    }
                }
            },
        )
        final = example("scalar-cauchy-schwarz")
        final.update(
            variables=[{"name": "y", "type": "Real"}],
            goal={
                "forall": {
                    "name": "z",
                    "type": "Real",
                    "body": op("eq", op("mul", v("y"), v("z")), op("mul", v("z"), v("y"))),
                }
            },
            sources=[{"name": "L", "node_id": "lemma"}],
            proof={"term": source_apply("L", [v("y")], [])},
        )
        raw = {
            "schema": SCHEMA,
            "nodes": [
                {"id": "lemma", "kind": "lemma", "depends_on": [], "target": lemma},
                {"id": "problem", "kind": "problem", "depends_on": ["lemma"], "target": final},
            ],
        }
        expanded = expand_graph(raw)["nodes"][0]["target"]
        intro = expanded["proof"]["term"]["intro"]
        self.assertNotEqual(intro["name"], "y")
        self.assertEqual(intro["proof"]["rule"]["terms"], [v("y"), v(intro["name"])])
        self.assertEqual(
            compile_request(expanded)["formal_statement"],
            compile_graph(raw)["node_plans"]["problem"]["formal_statement"],
        )

    def test_an_incorrect_original_is_preserved_and_not_declared_proved(self):
        raw = graph()
        raw["nodes"][-1]["target"]["goal"] = op("eq", r(1), r(0))
        result = expand_graph(raw)
        self.assertEqual(result["nodes"][0]["target"]["goal"], op("eq", r(1), r(0)))
        self.assertEqual(result["math_verdict"], "UNKNOWN")

    def test_exact_internal_arity_is_required(self):
        for field in ("terms", "proofs"):
            raw = graph()
            raw["nodes"][-1]["target"]["proof"]["term"]["source_apply"][field].pop()
            with self.subTest(field=field), self.assertRaises(ValueError):
                expand_graph(raw)

    def test_scheduling_only_steps_are_not_reported_as_expanded(self):
        raw = graph()
        raw["nodes"][-1]["target"] = example("scalar-cauchy-schwarz")
        with self.assertRaises(ValueError):
            expand_graph(raw)

    def test_cycles_and_unknown_sources_are_rejected(self):
        raw = graph()
        raw["nodes"][0]["depends_on"] = ["energy"]
        with self.assertRaises(ValueError):
            expand_graph(raw)
        raw = graph()
        raw["nodes"][-1]["target"]["sources"][0]["node_id"] = "unknown"
        with self.assertRaises(ValueError):
            expand_graph(raw)

    def test_external_identity_is_retained_without_authorization(self):
        raw = graph()
        source = raw["nodes"][0]["target"]["sources"][0]
        source.update(run_id="pdewatched-" + "a" * 32, type_fingerprint="b" * 64)
        result = expand_graph(raw)
        final = result["nodes"][0]["target"]["sources"][0]
        self.assertEqual(
            (final["run_id"], final["type_fingerprint"]),
            (source["run_id"], source["type_fingerprint"]),
        )
        self.assertEqual(result["evidence_level"], "E0")
        self.assertFalse(
            compile_request(result["nodes"][0]["target"])["source_authorization_checked"]
        )


if __name__ == "__main__":
    unittest.main()
