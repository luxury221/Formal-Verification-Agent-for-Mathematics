"""Candidate contracts, actual HTTP/CLI, and real Lean checks of rule mappings."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mra.domain import digest
from mra.number_theory_search import (
    SCHEMA,
    TARGET_FIELDS,
    document,
    reference,
    rules,
    search,
    substitute,
)
from mra.number_theory_terms import REFERENCES, compile_request, examples
from test_http_api import copy_web_inputs, request, running_server
from test_number_theory import isolated_store, submit

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/number-theory-search-20261027-01"


def intake(value=None, depth=6, expansions=3000):
    value = value or examples()[2]
    return {
        "schema": SCHEMA,
        "target": {k: deepcopy(value[k]) for k in TARGET_FIELDS},
        "budget": {"max_depth": depth, "max_expansions": expansions},
    }


def v(name):
    return {"var": name}


def f(op, a, b):
    return {op: [a, b]}


def problem(goal, assumptions=()):
    return {
        **examples()[2],
        "title": "Authored search fixture",
        "original": "Explicit Nat fixture; complete premises below.",
        "variables": ["a", "b", "d"],
        "assumptions": list(assumptions),
        "goal": goal,
    }


def composition():
    a, b, d = v("a"), v("b"), v("d")
    return problem(
        f("dvd", d, f("gcd", f("add", a, b), a)),
        [
            {"name": "ha", "formula": f("dvd", d, a)},
            {"name": "hb", "formula": f("dvd", d, b)},
        ],
    )


def reverse_iff():
    a, b = v("a"), v("b")
    return problem(
        f("coprime", a, b), [{"name": "hg", "formula": f("eq", f("gcd", a, b), {"nat": 1})}]
    )


def transitivity_and_subtraction():
    a, b, d = v("a"), v("b"), v("d")
    return problem(
        f("and", f("dvd", a, d), f("dvd", a, f("sub", a, b))),
        [
            {"name": "hab", "formula": f("dvd", a, b)},
            {"name": "hbd", "formula": f("dvd", b, d)},
            {"name": "hsub", "formula": f("or", f("eq", b, {"nat": 0}), f("le", a, b))},
        ],
    )


class ContractTests(unittest.TestCase):
    def test_examples_preserve_full_target_and_grant_no_evidence(self):
        for value in examples()[:5]:
            with self.subTest(title=value["title"]):
                raw = intake(value)
                result = search(raw, ROOT)
                self.assertEqual(result["status"], "CANDIDATE_FOUND")
                self.assertEqual(result["target"], raw["target"])
                self.assertEqual(
                    {k: x for k, x in result["candidate"].items() if k != "proof"}, result["target"]
                )
                self.assertEqual(
                    (
                        result["math_verdict"],
                        result["evidence_level"],
                        result["lean_calls"],
                        result["model_calls"],
                    ),
                    ("UNKNOWN", "E0", 0, 0),
                )
                self.assertTrue(result["requires_preview_and_named_confirmation"])
                self.assertEqual(result["provenance"]["input_sha256"], digest(raw))
                self.assertEqual(
                    result["provenance"]["candidate_sha256"], digest(result["candidate"])
                )
                record_hash = result.pop("record_sha256")
                self.assertEqual(record_hash, digest(result))
                compile_request(result["candidate"])

    def test_composes_addition_and_gcd_without_changing_premises(self):
        result = search(intake(composition()), ROOT)
        self.assertEqual(result["status"], "CANDIDATE_FOUND")
        self.assertEqual(result["references"], ["Nat.dvd_add", "Nat.dvd_gcd"])
        self.assertEqual(result["stats"]["last_depth"], 3)

    def test_transitivity_fills_intermediate_nat_from_input_pool(self):
        a, b, d = v("a"), v("b"), v("d")
        value = problem(
            f("dvd", a, d),
            [
                {"name": "hab", "formula": f("dvd", a, b)},
                {"name": "hbd", "formula": f("dvd", b, d)},
            ],
        )
        result = search(intake(value), ROOT)
        self.assertEqual(result["candidate"]["proof"]["terms"], [a, b, d])
        self.assertEqual(result["references"], ["Nat.dvd_trans"])

    def test_iff_can_be_used_in_both_directions_and_from_exact_hypothesis(self):
        result = search(intake(reverse_iff()), ROOT)
        self.assertIn("mpr", result["candidate"]["proof"])
        a, b = v("a"), v("b")
        left, right = f("le", a, b), f("le", b, a)
        for mode, source, goal in (("mp", left, right), ("mpr", right, left)):
            value = problem(
                goal,
                [
                    {"name": "hi", "formula": f("iff", left, right)},
                    {"name": "hs", "formula": source},
                ],
            )
            result = search(intake(value), ROOT)
            self.assertEqual(result["candidate"]["proof"], {mode: [{"hyp": "hi"}, {"hyp": "hs"}]})

    def test_subtraction_iff_preserves_truncation_disjunction(self):
        a, b = v("a"), v("b")
        premise = f("or", f("eq", b, {"nat": 0}), f("le", a, b))
        value = problem(f("dvd", a, f("sub", a, b)), [{"name": "h", "formula": premise}])
        result = search(intake(value), ROOT)
        self.assertEqual(result["references"], ["Nat.dvd_sub_self_left"])
        self.assertIn("mpr", result["candidate"]["proof"])

    def test_missing_premise_false_and_unsupported_true_goals_remain_unknown(self):
        a, b = v("a"), v("b")
        values = [
            examples()[5],
            problem(f("coprime", {"nat": 0}, {"nat": 0})),
            problem(f("eq", a, b)),
            problem(f("le", a, a)),
            problem(
                f("dvd", a, b),
                [{"name": "hp", "formula": f("and", f("dvd", a, b), f("dvd", b, a))}],
            ),
        ]
        for value in values:
            with self.subTest(goal=value["goal"]):
                result = search(intake(value), ROOT)
                self.assertIsNone(result["candidate"])
                self.assertEqual(
                    (result["status"], result["math_verdict"]), ("NO_CANDIDATE", "UNKNOWN")
                )
                self.assertLessEqual(result["stats"]["expansions"], 3000)

    def test_depth_and_step_limits_terminate_without_false_refutation(self):
        shallow = search(intake(composition(), depth=1), ROOT)
        self.assertEqual(shallow["stop_reason"], "depth_limit")
        small = search(intake(expansions=1), ROOT)
        self.assertEqual(small["stop_reason"], "expansion_limit")
        self.assertEqual(small["stats"]["expansions"], 1)

    def test_wall_limit_is_cooperative_and_reports_unknown(self):
        with patch("mra.number_theory_search.time.monotonic", side_effect=[100, 101, 101]):
            result = search(intake(), ROOT)
        self.assertEqual(result["stop_reason"], "wall_time_limit")
        self.assertIsNone(result["candidate"])

    def test_term_pool_cap_is_disclosed_and_no_arbitrary_term_growth(self):
        terms = [{"nat": i + 2} for i in range(40)]
        while len(terms) > 1:
            terms = [
                f("add", terms[i], terms[i + 1]) if i + 1 < len(terms) else terms[i]
                for i in range(0, len(terms), 2)
            ]
        result = search(intake(problem(f("coprime", terms[0], {"nat": 2}))), ROOT)
        self.assertTrue(result["stats"]["term_pool_truncated"])
        self.assertEqual(result["stats"]["used_terms"], 32)

    def test_strict_schema_and_resource_fields_and_language_rejection(self):
        variants = []
        for key, value in (
            ("max_depth", True),
            ("max_depth", 0),
            ("max_depth", 9),
            ("max_expansions", 6001),
            ("max_expansions", 1.5),
        ):
            raw = intake()
            raw["budget"][key] = value
            variants.append(raw)
        raw = intake()
        raw["target"]["proof"] = {"source": "by sorry"}
        variants.append(raw)
        raw = intake()
        raw["target"]["goal"] = {"prime": [v("a")]}
        variants.append(raw)
        raw = intake()
        raw["target"]["assumptions"] = []
        raw["target"]["variables"] = []
        variants.append(raw)
        raw = intake()
        raw["schema"] = "unknown"
        variants.append(raw)
        raw = intake()
        raw["budget"]["wall_seconds"] = 999
        variants.append(raw)
        for raw in variants:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                search(raw, ROOT)

    def test_input_is_unchanged_and_search_calls_no_checker_queue_or_model(self):
        raw = intake()
        before = deepcopy(raw)
        with (
            patch("subprocess.run", side_effect=AssertionError("Search must not execute")),
            patch(
                "mra.number_theory_lab.NumberTheoryStore.submit",
                side_effect=AssertionError("No automatic queue"),
            ),
        ):
            result = search(raw, ROOT)
        self.assertEqual(raw, before)
        self.assertEqual(result["status"], "CANDIDATE_FOUND")

    def test_rule_coverage_and_allowlist_drift_are_fail_closed(self):
        self.assertEqual({r.ref for r in rules()}, set(REFERENCES))
        with patch.dict(REFERENCES, {"Nat.dvd_gcd": (3, 1, "drift", "drift", "lean")}):
            with self.assertRaises(ValueError):
                search(intake(), ROOT)

    def test_cli_returns_candidate_and_rejects_invalid_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "search.json"
            for data, expected in ((intake(), 0), ({**intake(), "schema": "invalid"}, 2)):
                path.write_text(json.dumps(data))
                result = subprocess.run(
                    [sys.executable, "-m", "mra", "number-theory-search", "--input", str(path)],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(result.returncode, expected, result.stderr)
                if expected == 0:
                    self.assertEqual(json.loads(result.stdout)["status"], "CANDIDATE_FOUND")


class HttpTests(unittest.TestCase):
    def test_same_origin_csrf_strict_json_and_no_automatic_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_web_inputs(root)
            with running_server(root) as server:
                server.number_theory = isolated_store()
                for route in (
                    "/number-theory",
                    "/number-theory-search.js",
                    "/number-theory-search.css",
                ):
                    self.assertEqual(request(server, "GET", route)[0], 200)
                status, result, _ = request(server, "POST", "/v1/number-theory/search", intake())
                self.assertEqual(status, 200)
                self.assertEqual(result["status"], "CANDIDATE_FOUND")
                self.assertEqual(server.number_theory.list_runs(), [])
                for headers in ({"X-MRA-CSRF": "invalid"}, {"Origin": "https://elsewhere.invalid"}):
                    self.assertEqual(
                        request(
                            server, "POST", "/v1/number-theory/search", intake(), headers=headers
                        )[0],
                        403,
                    )
                self.assertEqual(
                    request(
                        server,
                        "POST",
                        "/v1/number-theory/search",
                        raw=b'{"schema":"a","schema":"b"}',
                    )[0],
                    400,
                )
                self.assertEqual(
                    request(
                        server, "POST", "/v1/number-theory/search", {**intake(), "unexpected": True}
                    )[0],
                    400,
                )


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "Set MRA_REAL_LEAN=1")
class RealTests(unittest.TestCase):
    records = []

    @classmethod
    def tearDownClass(cls):
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "real-runs.json").write_text(
            json.dumps(cls.records, ensure_ascii=False, indent=2) + "\n"
        )

    def check(self, value, expected="PROVED"):
        store = isolated_store()
        row = submit(store, value)
        store.work_once(row["run_id"])
        result = store.report(row["run_id"])
        self.records.append(
            {
                "run_id": row["run_id"],
                "store": str(store.base.relative_to(ROOT)),
                "math_verdict": result["math_verdict"],
                "state": result["state"],
                "references": result["plan"]["references"],
                "files": result["files"],
            }
        )
        self.assertEqual(result["math_verdict"], expected, result.get("result"))
        if expected == "PROVED":
            self.assertEqual(result["evidence_level"], "E4")
            self.assertTrue(result["result"]["candidate"])
            self.assertTrue(result["result"]["replay"])
        return store, result

    def test_generated_examples_and_multistep_proofs_pass_real_lean_and_replay(self):
        for value in [
            *examples()[:5],
            composition(),
            reverse_iff(),
            transitivity_and_subtraction(),
        ]:
            with self.subTest(title=value["title"]):
                proposal = search(intake(value), ROOT)
                self.assertIsNotNone(proposal["candidate"])
                self.check(proposal["candidate"])

    def test_every_rule_signature_and_explicit_argument_order_in_real_lean(self):
        bindings = {"$a": ("var", "a"), "$b": ("var", "b"), "$c": ("var", "d")}
        all_premises = list(
            dict.fromkeys(substitute(p, bindings) for r in rules() for p in r.premises)
        )
        assumptions = [
            {"name": f"h{i}", "formula": document(p)} for i, p in enumerate(all_premises)
        ]

        def combine(items, key):
            if len(items) == 1:
                return items[0]
            mid = len(items) // 2
            return {key: [combine(items[:mid], key), combine(items[mid:], key)]}

        for offset in range(0, len(rules()), 4):
            group = rules()[offset : offset + 4]
            value = problem(
                combine([document(substitute(r.conclusion, bindings)) for r in group], "and"),
                assumptions,
            )
            value["proof"] = combine(
                [
                    reference(
                        r,
                        bindings,
                        [
                            {"hyp": f"h{all_premises.index(substitute(p, bindings))}"}
                            for p in r.premises
                        ],
                    )
                    for r in group
                ],
                "pair",
            )
            self.assertLessEqual(compile_request(value)["node_count"], 240)
            self.check(value)

    def test_no_premise_invention_and_confirmed_wrong_manual_proof_stays_unknown(self):
        proposal = search(intake(examples()[5]), ROOT)
        self.assertIsNone(proposal["candidate"])
        _, result = self.check(examples()[5], expected="UNKNOWN")
        self.assertEqual(result["state"], "REJECTED")

    def test_confirmation_cancel_and_revoke_keep_existing_lifecycle(self):
        candidate = search(intake(composition()), ROOT)["candidate"]
        store = isolated_store()
        with self.assertRaises(ValueError):
            submit(store, candidate, confirmed=False)
        with self.assertRaises(ValueError):
            submit(store, candidate, plan_fingerprint="stale")
        self.assertEqual(store.list_runs(), [])
        queued = submit(store, candidate)
        self.assertEqual(store.cancel(queued["run_id"])["math_verdict"], "UNKNOWN")
        self.assertEqual(store.work_once(queued["run_id"])["worker_state"], "IDLE")
        store, result = self.check(candidate)
        revoked = store.revoke(
            result["run_id"],
            {"reviewer": "Search QA", "reason": "Exercise revocation", "confirmed": True},
        )
        self.assertTrue(revoked["invalidated"])
        self.assertEqual(store.report(result["run_id"])["math_verdict"], "UNKNOWN")
