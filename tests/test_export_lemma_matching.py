"""Non-executing contracts and a separately opted-in actual reuse comparison."""

from copy import deepcopy
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

import test_structure_lemma_library as core
from mra.domain import canonical
from mra.export_lemma_matching import DEFAULT_BUDGET, FILES, SCHEMA, demo_target, suggest
from mra.function_dynamics_checks import verified
from mra.obligation_planning import propose
from mra.recursion_programs import RecursionStore
from mra.structure_composition_terms import substitute
from mra.structure_lemma_library import ExportApplicationStore, explicit
from mra.structured_terms import compile_request
from mra.tasks import WorkerBusy
from mra.verifier import NativeLeanBackend
from test_http_api import request as http, running_server

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/export-lemma-matching-20261029-01"


def raw(target, **budget):
    return {
        "schema": SCHEMA,
        "target": target,
        "budget": {**DEFAULT_BUDGET, "max_candidates": 2, **budget},
    }


def cases(first, second):
    """Author-visible derived goals; source statements are explicitly disclosed."""
    base = deepcopy(second)
    base.pop("proof", None)
    k, m, y = {"var": "k"}, {"var": "m"}, {"var": "y"}

    def call(name, *args):
        return {"call": {"name": name, "args": list(args)}}

    def inst(goal, n=k, x=y):
        return substitute(goal, {"n": n, "x": x}, {"k", "m", "y"})

    base.update(
        variables=[{"name": "k", "type": "Nat"}, {"name": "y", "type": "a"}],
        title="双引理联合目标",
        original="保留完整原交换条件，同时证明复合迭代等式与g和f迭代的交换等式。",
        goal={"and": [inst(second["goal"]), inst(first["goal"])]},
    )
    shifted = deepcopy(base)
    shift, term = {"add": [k, {"nat": 1}]}, call("f", y)
    eq = inst(second["goal"], shift, term)
    shifted.update(
        title="移位复合实参与反向联合目标",
        original="在完整原条件下，将索引代入k+1、初态代入f(y)，同时证明复合迭代等式的反向和交换等式。",
        goal={"and": [{"eq": eq["eq"][::-1]}, inst(first["goal"], shift, term)]},
    )
    chain = deepcopy(base)
    chain.update(
        title="双层迭代的改写链",
        original="在全部原条件下，将两层复合迭代逐层分解，同时保留m处的交换结论。",
        variables=[
            {"name": "k", "type": "Nat"},
            {"name": "m", "type": "Nat"},
            {"name": "y", "type": "a"},
        ],
        goal={
            "and": [
                {
                    "eq": [
                        call("iter_fg", k, call("iter_fg", m, y)),
                        call(
                            "iter_f", k, call("iter_g", k, call("iter_f", m, call("iter_g", m, y)))
                        ),
                    ]
                },
                inst(first["goal"], m, y),
            ]
        },
    )
    missing = deepcopy(base)
    missing.update(
        title="缺少交换条件",
        original="不提供交换条件，保留原完整双结论；不得自动补充假设。",
        assumptions=[],
    )
    wrong = deepcopy(base)
    wrong.update(
        title="同名但错误的条件",
        original="commutes仅表示y=y，不能冒充全称交换条件。",
        assumptions=[{"name": "commutes", "formula": {"eq": [y, y]}}],
    )
    changed = deepcopy(base)
    changed.update(
        title="扩展运算环境", original="在原完整条件外声明额外任意运算p，本版本不跨环境复用。"
    )
    changed["operations"].append(
        {"name": "p", "parameters": ["a"], "result_type": "a", "original": "额外任意运算"}
    )
    return [
        {"id": name, "target": t}
        for name, t in zip(
            (
                "two-lemmas",
                "shifted-reversed",
                "two-layer-chain",
                "missing-condition",
                "wrong-condition",
                "different-environment",
            ),
            (base, shifted, chain, missing, wrong, changed),
        )
    ]


class ContractTests(unittest.TestCase):
    def setUp(self):
        core.ContractTests.setUp(self)
        for n in FILES:
            p = self.root / n
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / n, p)
        self.signatures = [
            core.ContractTests.preview(self, core.export_request(self.fixture, i))["target"]
            for i in (0, 1)
        ]
        self.ids = ["mathlib-" + str(i + 1) * 32 for i in range(2)]
        self.rows = {
            key: {
                "invalidated": False,
                "math_verdict": "PROVED",
                "evidence_level": "E4",
                "plan": {"target": t},
            }
            for key, t in zip(self.ids, self.signatures)
        }
        with self.exports.transaction() as db:
            for key in self.ids:
                db.execute("INSERT INTO runs VALUES (?,?,?,?)", (key, key, "{}", "NONEXECUTABLE"))
        self.targets = cases(*self.signatures)

    def source(self, binding):
        target = explicit(self.rows[binding["item_id"].removeprefix("export.")]["plan"]["target"])
        return {
            **binding,
            "request": target,
            "formal_statement": compile_request(target)["formal_statement"],
            "source_fingerprint": "NONEXECUTABLE-contract-fixture",
        }, b"NONEXECUTABLE"

    def match(self, target=None, **budget):
        with (
            patch.object(self.exports, "read", side_effect=lambda k: self.rows[k]),
            patch.object(self.apps, "load_source", side_effect=self.source),
            patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")),
        ):
            return suggest(self.apps, raw(target or self.targets[0]["target"], **budget))

    def test_discovers_multiple_sources_and_complete_original_conditions_without_execution(self):
        value = self.match(max_expansions=4000)
        self.assertTrue(value["candidates"])
        c = value["candidates"][0]
        self.assertEqual(len(c["request"]["sources"]), 2)
        self.assertEqual({a["premise_proofs"][0]["hyp"] for a in c["applications"]}, {"commutes"})
        self.assertEqual(c["request"]["target"], self.targets[0]["target"])
        self.assertEqual(
            (value["math_verdict"], value["evidence_level"], value["lean_calls"]),
            ("UNKNOWN", "E0", 0),
        )
        self.assertEqual(self.apps.list_runs(), [])

    def test_explicit_compound_arguments_reversal_and_two_layer_chain(self):
        for item in self.targets[1:3]:
            with self.subTest(item=item["id"]):
                value = self.match(item["target"], max_expansions=4000)
                self.assertTrue(value["candidates"])
                self.assertEqual(
                    value["candidates"][0]["formal_statement"],
                    compile_request(explicit(item["target"]))["formal_statement"],
                )

    def test_missing_wrong_conditions_and_environment_are_not_silently_repaired(self):
        for item in self.targets[3:]:
            with self.subTest(item=item["id"]):
                v = self.match(item["target"], max_expansions=4000)
                self.assertEqual(v["candidates"], [])
                self.assertEqual(v["target"], item["target"])
                self.assertTrue(v["diagnostics"])

    def test_headers_are_only_hints_and_live_source_failure_is_not_accepted(self):
        with (
            patch.object(self.exports, "read", side_effect=lambda k: self.rows[k]),
            patch.object(
                self.apps, "load_source", side_effect=ValueError("withdrawn current source")
            ),
        ):
            v = suggest(self.apps, raw(self.targets[0]["target"]))
        self.assertFalse(v["candidates"])
        self.assertEqual(v["stats"]["live_source_audits"], 2)
        self.assertTrue(all(not a["available"] for a in v["source_audits"]))

    def test_busy_propagates_without_publishing_partial_suggestions(self):
        with (
            patch.object(self.exports, "read", side_effect=lambda k: self.rows[k]),
            patch.object(self.apps, "load_source", side_effect=WorkerBusy("held")),
            self.assertRaises(WorkerBusy),
        ):
            suggest(self.apps, raw(self.targets[0]["target"]))

    def test_strict_schema_and_no_client_source_descriptors_or_claims(self):
        for change in (
            {"schema": "other"},
            {"sources": []},
            {"feedback": "PROVED"},
            {"budget": {**DEFAULT_BUDGET, "max_depth": True}},
            {"budget": {**DEFAULT_BUDGET, "max_expansions": 4001}},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                suggest(self.apps, {**raw(self.targets[0]["target"]), **change})
        for timeout in (True, float("inf"), 0, 31):
            t = deepcopy(self.targets[0]["target"])
            t["timeout_seconds"] = timeout
            with self.assertRaises(ValueError):
                suggest(self.apps, raw(t))

    def test_shared_search_fuel_bound_and_unproved_headers_are_skipped(self):
        value = self.match(max_expansions=1)
        self.assertFalse(value["candidates"])
        self.assertLessEqual(value["stats"]["expansions"], 1)
        self.rows[self.ids[0]]["math_verdict"] = "UNKNOWN"
        self.rows[self.ids[1]]["invalidated"] = True
        value = self.match()
        self.assertFalse(value["candidates"])
        self.assertEqual(value["stats"]["live_source_audits"], 0)

    def test_catalog_and_live_audit_limits_do_not_expand_with_library_size(self):
        template = self.rows[self.ids[0]]
        with self.exports.transaction() as db:
            for i in range(2, 205):
                key = "mathlib-" + format(i, "032x")
                self.rows[key] = deepcopy(template)
                db.execute("INSERT INTO runs VALUES (?,?,?,?)", (key, key, "{}", "NONEXECUTABLE"))
        value = self.match(max_expansions=1)
        self.assertEqual(value["stats"]["headers_scanned"], 200)
        self.assertEqual(value["stats"]["live_source_audits"], 8)
        self.assertTrue(value["catalog_truncated"])

    def test_demo_retains_exact_live_environment_and_identifies_known_source(self):
        with (
            patch.object(self.exports, "read", side_effect=lambda k: self.rows[k]),
            patch.object(self.apps, "load_source", side_effect=self.source),
        ):
            value = demo_target(self.apps)
        self.assertEqual(value["source_item_id"], "export." + self.ids[1])
        self.assertEqual(value["target"]["carriers"], self.signatures[1]["carriers"])
        self.assertEqual(value["target"]["goal"]["and"][0], self.signatures[1]["goal"])
        self.assertIn("not a blind task", value["provenance"])

    def test_http_origin_contract_and_no_lean_execution(self):
        with running_server(self.root) as server:
            self.assertEqual(
                http(
                    server,
                    "POST",
                    "/v1/lemma-research/match",
                    raw(self.targets[0]["target"]),
                    headers={"Origin": "https://example.invalid"},
                )[0],
                403,
            )
            value = http(
                server, "POST", "/v1/lemma-research/match", raw(self.targets[0]["target"])
            )[1]
            self.assertEqual((value["candidates"], value["lean_calls"]), ([], 0))
            self.assertEqual(
                http(
                    server,
                    "POST",
                    "/v1/lemma-research/match",
                    {**raw(self.targets[0]["target"]), "path": "/other"},
                )[0],
                400,
            )


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "Explicit genuine source-matching comparison"
)
class RealTests(unittest.TestCase):
    def test_fixed_derived_targets_compare_no_import_and_discovered_import(self):
        area = OUT / ("real-" + uuid.uuid4().hex)
        area.mkdir(parents=True)
        apps = ExportApplicationStore(ROOT, base=area / "matched-checks")
        parents = RecursionStore(ROOT, base=area / "without-import-checks")
        source_ids = [
            "mathlib-b7e0cda505ee413c9b577cb7c6c00e45",
            "mathlib-ba3c9fa40e644b109dfef292e721ce38",
        ]
        sources = [
            apps.load_source({"alias": "fixed" + str(i), "item_id": "export." + key})[0]
            for i, key in enumerate(source_ids)
        ]
        items = cases(*(s["request"] for s in sources))
        plans = []
        for item in items:
            target = item["target"]
            match = suggest(apps, raw(target, max_expansions=4000))
            baseline = propose(
                ROOT,
                {
                    "schema": "structure-recursion-proposals-v2",
                    "target": target,
                    "budget": {"max_candidates": 2, "max_expansions": 800, "max_rewrite_steps": 4},
                },
            )
            plans.append({**item, "matching": match, "without_import": baseline})

        def save(name, value):
            (area / name).write_bytes(canonical(value) + b"\n")

        save(
            "declared-plan.json",
            {
                "items": plans,
                "sources": sources,
                "author_visible": True,
                "comparison": "Existing obligation-v2 without imported source proofs vs bounded export discovery; different synthesis fuel and prior proof cost disclosed",
                "maximum_checks_per_arm_and_target": 2,
                "worker_seconds_per_complete_check": 20,
                "source_preparation_reexecuted": False,
                "model_calls": 0,
            },
        )
        results = []
        scores = {"without_import": 0, "matched_import": 0}
        for item in plans:
            for arm, store, candidates in (
                ("without_import", parents, item["without_import"]["candidates"]),
                ("matched_import", apps, item["matching"]["candidates"]),
            ):
                success = False
                for candidate in candidates[:2]:
                    row = core.run(store, candidate["request"])
                    results.append(
                        {
                            "case_id": item["id"],
                            "arm": arm,
                            "store": str(store.base.relative_to(ROOT)),
                            "check": row,
                        }
                    )
                    save("results.json", results)
                    if row["math_verdict"] == "PROVED":
                        verified(row)
                        success = True
                        break
                    self.assertEqual(row["math_verdict"], "UNKNOWN")
                scores[arm] += int(success)
        self.assertTrue(
            any(
                r["arm"] == "matched_import" and r["check"]["math_verdict"] == "PROVED"
                for r in results
            )
        )
        for item in plans[3:]:
            self.assertFalse(item["matching"]["candidates"])
        save(
            "summary.json",
            {
                "all_passed": True,
                "scores": scores,
                "targets": len(plans),
                "complete_checks": len(results),
                "unattempted_matching_targets": [
                    p["id"] for p in plans if not p["matching"]["candidates"]
                ],
                "blind_evaluation": False,
                "model_calls": 0,
            },
        )
