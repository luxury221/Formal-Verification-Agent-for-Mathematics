"""Pure proposal/HTTP contracts and separately enabled genuine generated-program checks."""

from copy import deepcopy
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

import test_structure_research as core
from mra.domain import canonical
from mra.structured_examples import examples
from mra.structured_terms import compile_request
from mra.structure_research import StructureResearchStore
from mra.structure_strategies import DEFAULT_BUDGET, FILES, SCHEMA, graph_proposals, propose
from mra.structure_strategy_terms import Planner, alpha_key, MAX_NORMALIZATION_NODES
from mra.verifier import NativeLeanBackend
from test_http_api import request as http, running_server

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/structure-strategy-20261029-01"


def target(request):
    return {k: deepcopy(v) for k, v in request.items() if k != "proof"}


def request_for(value, **budget):
    return {"schema": SCHEMA, "target": target(value), "budget": {**DEFAULT_BUDGET, **budget}}


def renamed(value, names):
    if isinstance(value, list):
        return [renamed(v, names) for v in value]
    if isinstance(value, dict):
        return {k: renamed(v, names) for k, v in value.items()}
    return names.get(value, value) if isinstance(value, str) else value


def graph_request(request):
    return {
        "schema": "structure-project-research-v1",
        **{k: deepcopy(request[k]) for k in ("carriers", "operations", "definitions")},
        "laws": [],
        "nodes": [
            {
                "id": "problem",
                "kind": "problem",
                "depends_on": [],
                "target": {
                    k: deepcopy(v)
                    for k, v in request.items()
                    if k not in ("schema", "carriers", "operations", "definitions")
                },
            }
        ],
    }


class ContractTests(unittest.TestCase):
    def setUp(self):
        core.ContractTests.setUp(self)
        for n in (*FILES, "mra/structure_research_web.py"):
            p = self.root / n
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / n, p)

    def test_proposals_preserve_complete_target_and_never_execute_or_claim_proof(self):
        raw = request_for(examples()[2])
        original = deepcopy(raw)
        with patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")):
            value = propose(self.root, raw)
        self.assertEqual(raw, original)
        self.assertEqual(value["target"], raw["target"])
        self.assertFalse(value["reference_proof_used"])
        self.assertEqual((value["lean_calls"], value["model_calls"]), (0, 0))
        self.assertTrue(value["requires_preview_and_named_confirmation"])
        for candidate in value["candidates"]:
            self.assertEqual(target(candidate["request"]), raw["target"])
            self.assertEqual(
                compile_request(candidate["request"])["formal_statement"], value["formal_statement"]
            )
            self.assertEqual(
                (candidate["math_verdict"], candidate["evidence_level"]), ("UNKNOWN", "E0")
            )

    def test_rewrite_laws_and_induction_are_synthesized_without_reference_or_label_matching(self):
        names = {
            "mul": "combine",
            "one": "unit",
            "power": "iterate",
            "x": "u",
            "m": "first",
            "n": "second",
            "assoc": "law_a",
            "left_id": "law_b",
            "right_id": "law_c",
        }
        value = renamed(target(examples()[2]), names)
        value["title"] = value["original"] = "Opaque author-visible diagnostic target"
        result = propose(self.root, {"schema": SCHEMA, "target": value, "budget": DEFAULT_BUDGET})
        program = next(
            c["request"]["proof"]
            for c in result["candidates"]
            if c["origin"] == "symbolic-induction"
        )
        self.assertEqual(program["variable"], "second")
        self.assertEqual(program["base"]["steps"], [{"rule": "law_c", "reverse": False}])
        self.assertEqual([s["rule"] for s in program["step"]["steps"]], ["@ih", "law_a"])
        direct = propose(self.root, request_for(examples()[0]))
        program = next(
            c["request"]["proof"] for c in direct["candidates"] if c["origin"] == "symbolic-rewrite"
        )
        self.assertEqual([s["rule"] for s in program["steps"]], ["left_id", "right_id"])

    def test_missing_commutativity_cannot_be_closed_by_unrelated_associativity(self):
        raw = request_for(examples()[4])
        result = propose(self.root, raw)
        self.assertFalse(any(c["origin"] == "symbolic-rewrite" for c in result["candidates"]))
        engine = Planner(raw["target"], DEFAULT_BUDGET)
        law = engine.rules()[0]
        self.assertIsNone(engine.rewrite(raw["target"]["goal"], law, False))
        self.assertEqual(result["target"]["assumptions"], raw["target"]["assumptions"])

    def test_finite_budget_and_invalid_reference_proof_raw_code_or_shape_rejected(self):
        raw = request_for(examples()[2])
        for key, value in (
            ("max_expansions", True),
            ("max_candidates", 0),
            ("max_rewrite_steps", 7),
            ("max_expansions", 1.5),
        ):
            with self.assertRaises(ValueError):
                propose(self.root, {**raw, "budget": {**raw["budget"], key: value}})
        for changes in (
            {"proof": {"method": "sorry"}},
            {"schema": "old"},
            {"goal": {"lean": "True"}},
        ):
            with self.assertRaises(ValueError):
                propose(self.root, {**raw, "target": {**raw["target"], **changes}})
        limited = propose(self.root, request_for(examples()[2], max_expansions=1, max_candidates=1))
        self.assertEqual(len(limited["candidates"]), 1)
        self.assertLessEqual(limited["stats"]["expansions"], 1)
        self.assertLessEqual(limited["stats"]["normalization_nodes"], MAX_NORMALIZATION_NODES)
        self.assertEqual(limited["math_verdict"], "UNKNOWN")

    def test_fold_alpha_equivalence_does_not_capture_free_argument(self):
        r = target(examples()[2])
        r["variables"][0]["name"] = "acc"
        r["goal"] = renamed(r["goal"], {"x": "acc"})
        engine = Planner(r, DEFAULT_BUDGET)
        folded = engine.norm({"call": {"name": "power", "args": [{"var": "acc"}, {"var": "n"}]}})
        raw = folded["nat_fold"]
        self.assertNotEqual(raw["step"]["value"], "acc")
        alternate = deepcopy(folded)
        alternate["nat_fold"]["step"] = {
            "index": "fresh_k",
            "value": "fresh_acc",
            "body": renamed(
                raw["step"]["body"],
                {raw["step"]["index"]: "fresh_k", raw["step"]["value"]: "fresh_acc"},
            ),
        }
        self.assertEqual(alpha_key(folded), alpha_key(alternate))
        self.assertNotEqual(
            alpha_key(folded), alpha_key(renamed(folded, {"acc": "different_free"}))
        )

    def test_existing_node_feedback_is_read_only_and_keeps_cancelled_attempt(self):
        before = self.store.report(self.key)
        value = graph_proposals(
            self.store, {"graph_id": self.key, "node_id": "unit", "budget": DEFAULT_BUDGET}
        )
        self.assertEqual(value["feedback"], [])
        self.assertEqual(value["context"]["revision"], before["revision"])
        self.assertEqual(self.store.report(self.key), before)
        queued = self.store.submit(self.key, core.confirmation(self.store, self.key))
        self.store.cancel(self.key, {"attempt_id": queued["attempt_id"]})
        value = graph_proposals(
            self.store, {"graph_id": self.key, "node_id": "unit", "budget": DEFAULT_BUDGET}
        )
        self.assertEqual(value["feedback"][0]["state"], "CANCELLED")
        self.assertEqual(value["feedback"][0]["math_verdict"], "UNKNOWN")
        self.assertIsNone(value["feedback"][0]["replay_state"])
        with self.assertRaises(ValueError):
            graph_proposals(
                self.store, {"graph_id": self.key, "node_id": "missing", "budget": DEFAULT_BUDGET}
            )

    def test_planner_provenance_changes_without_changing_old_graph_protocol(self):
        a = propose(self.root, request_for(examples()[0]))
        p = self.root / FILES[1]
        p.write_bytes(p.read_bytes() + b"\n")
        b = propose(self.root, request_for(examples()[0]))
        self.assertNotEqual(a["source_manifest"], b["source_manifest"])
        self.assertNotEqual(a["plan_fingerprint"], b["plan_fingerprint"])
        self.assertFalse(self.store.report(self.key)["invalidated"])

    def test_transparent_definition_expansion_is_capped_before_large_substitution(self):
        r = examples()[0]
        r["variables"] = [{"name": "x", "type": "g"}]
        r["assumptions"] = []

        def call(name, *args):
            return {"call": {"name": name, "args": list(args)}}

        x = {"var": "x"}
        r["definitions"] = []
        for i in range(6):
            body = (
                call("mul", x, x) if i == 0 else call("f" + str(i - 1), call("f" + str(i - 1), x))
            )
            r["definitions"].append(
                {
                    "name": "f" + str(i),
                    "original": "Transparent bounded expression",
                    "parameters": [{"name": "x", "type": "g"}],
                    "result_type": "g",
                    "body": body,
                }
            )
        r["goal"] = {"eq": [call("f5", x), x]}
        value = propose(self.root, request_for(r))
        self.assertIn(
            value["stats"]["stop_reason"],
            ("intermediate_expression_limit", "normalization_limit", "synthesis_wall_limit"),
        )
        self.assertEqual(value["math_verdict"], "UNKNOWN")
        self.assertLessEqual(value["stats"]["normalization_nodes"], MAX_NORMALIZATION_NODES)

    def test_http_complete_current_node_origin_strict_json_and_no_queue(self):
        route = f"/v1/structure-research/{self.key}/strategy-proposals"
        raw = {"node_id": "unit", "budget": DEFAULT_BUDGET}
        with (
            running_server(self.root) as server,
            patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")),
        ):
            status, result, _ = http(server, "POST", route, raw)
            self.assertEqual(status, 200)
            self.assertEqual(result["context"]["graph_id"], self.key)
            self.assertEqual(result["lean_calls"], 0)
            for headers in ({"Origin": "https://example.invalid"}, {"X-MRA-CSRF": "wrong"}):
                self.assertEqual(http(server, "POST", route, raw, headers=headers)[0], 403)
            self.assertEqual(http(server, "POST", route + "?x=1", raw)[0], 400)
            for bad in ({**raw, "confirmed": True}, {**raw, "node_id": "missing"}):
                self.assertEqual(http(server, "POST", route, bad)[0], 400)
            self.assertEqual(
                http(server, "POST", route, raw=b'{"node_id":"unit","node_id":"other"}')[0], 400
            )
            self.assertEqual(http(server, "POST", route, raw=b" " * 196609)[0], 413)
            self.assertEqual(server.structure_research.report(self.key)["attempts"], [])


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "Explicit genuine generated structure programs"
)
class RealTests(unittest.TestCase):
    def setUp(self):
        self.out = OUT / ("real-" + uuid.uuid4().hex)
        self.out.mkdir(parents=True)
        self.project = core.parent(ROOT)
        self.store = StructureResearchStore(ROOT, base=self.out / "graphs")
        self.results = []

    def save(self, name, value):
        (self.out / name).write_bytes(canonical(value) + b"\n")

    def create(self, request):
        return core.create(self.store, self.project, graph_request(request))

    def check(self, graph):
        key = graph["graph_id"]
        confirmation = core.confirmation(self.store, key, "problem")
        confirmation["reviewer"] = "MRA065 generated complete-target program alignment reviewer"
        self.store.submit(key, confirmation)
        self.assertEqual(self.store.work_once(key)["worker_state"], "EXECUTED")
        value = self.store.report(key)
        self.results.append(value)
        return value

    def test_actual_rejection_feedback_then_generated_induction_and_withdrawal(self):
        request = examples()[2]
        request["proof"] = {"method": "rfl"}
        original = self.create(request)
        failed = self.check(original)
        self.save("failed.json", failed)
        self.assertEqual(failed["math_verdict"], "UNKNOWN")
        proposals = graph_proposals(
            self.store,
            {"graph_id": original["graph_id"], "node_id": "problem", "budget": DEFAULT_BUDGET},
        )
        self.save("proposals.json", proposals)
        feedback = proposals["feedback"][0]
        self.assertEqual(feedback["state"], "REJECTED")
        self.assertNotEqual(feedback["candidate_returncode"], 0)
        self.assertIsNone(feedback["replay_state"])
        self.assertTrue(proposals["candidates"][-1]["previously_rejected"])
        candidate = next(c for c in proposals["candidates"] if c["origin"] == "symbolic-induction")
        self.assertEqual(target(candidate["request"]), target(request))
        revised = self.create(candidate["request"])
        proved = self.check(revised)
        self.save("proved-before-withdrawal.json", proved)
        self.assertEqual(proved["math_verdict"], "PROVED")
        child = next(iter(proved["checks"].values()))
        self.assertNotEqual(child["result"]["candidate"]["pid"], child["result"]["replay"]["pid"])
        self.assertEqual(self.store.report(original["graph_id"])["math_verdict"], "UNKNOWN")
        self.store.withdraw(
            revised["graph_id"],
            {
                "node_id": "problem",
                "reviewer": "MRA065 QA",
                "reason": "Real feedback withdrawal boundary",
                "confirmed": True,
            },
        )
        current = graph_proposals(
            self.store,
            {"graph_id": revised["graph_id"], "node_id": "problem", "budget": DEFAULT_BUDGET},
        )
        self.save("withdrawn-feedback.json", current)
        self.assertEqual(
            (current["feedback"][0]["math_verdict"], current["feedback"][0]["evidence_level"]),
            ("UNKNOWN", "E0"),
        )
        self.assertTrue(current["feedback"][0]["invalidated"])
        self.save(
            "locator.json",
            {
                "project_id": self.project["project_id"],
                "graphs": [original["graph_id"], revised["graph_id"]],
            },
        )

    def test_renamed_homomorphism_rewrite_and_missing_commutativity_stays_unknown(self):
        request = renamed(
            examples()[1],
            {
                "src_mul": "source_op",
                "dst_mul": "target_op",
                "map": "function_f",
                "preserves": "law_one",
                "dst_assoc": "law_two",
            },
        )
        proposals = propose(ROOT, request_for(request))
        self.save("rewrite-proposals.json", proposals)
        candidate = next(c for c in proposals["candidates"] if c["origin"] == "symbolic-rewrite")
        self.assertEqual(target(candidate["request"]), target(request))
        proved = self.check(self.create(candidate["request"]))
        self.save("rewrite-proved.json", proved)
        self.assertEqual(proved["math_verdict"], "PROVED")
        missing = propose(ROOT, request_for(examples()[4]))
        self.save("missing-proposals.json", missing)
        candidate = next(
            c for c in missing["candidates"] if c["request"]["proof"] == {"method": "grind"}
        )
        unknown = self.check(self.create(candidate["request"]))
        self.save("missing-unknown.json", unknown)
        self.assertEqual(unknown["math_verdict"], "UNKNOWN")
        child = next(iter(unknown["checks"].values()))
        self.assertEqual(child["state"], "REJECTED")
        self.assertIsNone(child["result"]["replay"])
        self.assertIn("grind", child["result"]["error"])
        self.save(
            "locator.json",
            {
                "project_id": self.project["project_id"],
                "graphs": [proved["graph_id"], unknown["graph_id"]],
            },
        )
