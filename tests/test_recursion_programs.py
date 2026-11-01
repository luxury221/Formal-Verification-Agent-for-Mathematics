"""Versioned recursion contracts and explicitly enabled genuine checking/control cases."""

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

import test_structure_research as core
from mra.domain import canonical, digest
from mra.integrity import file_hash
from mra.mathlib_lab import audit
from mra.recursion_programs import MODE, FILES, RecursionStore, compile_program, manifest, prepare
from mra.recursion_strategies import (
    SCHEMA,
    DEFAULT_BUDGET,
    FILES as STRATEGY_FILES,
    ArithmeticPlanner,
    propose,
    templates,
)
from mra.reuse_evaluation_checks import initialize
from mra.structure_strategies_v4 import (
    FILES as OLD_FILES,
    propose as old_propose,
    manifest as old_manifest,
)
from mra.structures import StructureStore
from mra.structured_terms import compile_request
from mra.function_dynamics_checks import verified
from mra.verifier import NativeLeanBackend

ROOT = Path(__file__).resolve().parents[1]
DATASET = "evals/structure-recursion-v1/cases.json"
OUT = ROOT / "artifacts/structure-recursion-programs-20261029-01"


def cases():
    return json.loads((ROOT / DATASET).read_text())["cases"]


def request(target, proof=None, lemmas=()):
    return {
        "schema": MODE,
        "target": deepcopy(target),
        "program": {"lemmas": deepcopy(list(lemmas)), "proof": proof or {"method": "rfl"}},
        "timeout_seconds": target["timeout_seconds"],
    }


def proposal(root, target, **budget):
    return propose(
        root, {"schema": SCHEMA, "target": target, "budget": {**DEFAULT_BUDGET, **budget}}
    )


class DiagnosticContext:
    def context(self):
        return {
            "old": old_manifest(self.root),
            "new": manifest(self.root),
            "proposal_files": {n: file_hash(self.root / n) for n in STRATEGY_FILES},
            "dataset": file_hash(self.root / DATASET),
            "runner": file_hash(self.root / "tests/test_recursion_programs.py"),
        }

    def preview(self, raw):
        value = super().preview(raw)
        value["source_manifest"] = {
            "provider": value["source_manifest"],
            "diagnostic": self.context(),
        }
        value.pop("plan_fingerprint")
        value["plan_fingerprint"] = digest(value)
        return value

    def gate(self, row, check_files=True):
        if row["plan"]["source_manifest"]["diagnostic"] != self.context():
            return "Recursion diagnostic sources, programs or dataset changed"
        view = deepcopy(row)
        view["plan"]["source_manifest"] = row["plan"]["source_manifest"]["provider"]
        return super().gate(view, check_files)


class OldChecks(DiagnosticContext, StructureStore):
    def __init__(self, root, directory, create=True):
        initialize(self, root, directory, "old-checks", create)


class NewChecks(DiagnosticContext, RecursionStore):
    def __init__(self, root, directory, create=True):
        initialize(self, root, directory, "new-checks", create)


class ContractTests(unittest.TestCase):
    def setUp(self):
        core.ContractTests.setUp(self)
        for n in (*FILES, *STRATEGY_FILES, *OLD_FILES, DATASET, "tests/test_recursion_programs.py"):
            p = self.root / n
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / n, p)

    def test_all_proposals_keep_full_target_unknown_and_never_execute(self):
        for case in cases():
            before = deepcopy(case["target"])
            with patch.object(
                NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")
            ):
                p = proposal(self.root, case["target"])
            self.assertEqual(case["target"], before)
            self.assertEqual(
                (p["lean_calls"], p["model_calls"], p["math_verdict"], p["evidence_level"]),
                (0, 0, "UNKNOWN", "E0"),
            )
            self.assertLessEqual(len(p["candidates"]), 2)
            for c in p["candidates"]:
                self.assertEqual(c["request"]["target"], before)
                self.assertEqual(
                    compile_program(c["request"])["formal_statement"], p["formal_statement"]
                )
                self.assertEqual((c["math_verdict"], c["evidence_level"]), ("UNKNOWN", "E0"))

    def test_left_arithmetic_is_nat_only_and_compiler_uses_checked_fixed_rules(self):
        t = cases()[0]["target"]
        engine = ArithmeticPlanner(t, DEFAULT_BUDGET)
        self.assertEqual(engine.norm({"add": [{"nat": 0}, {"var": "n"}]}), {"var": "n"})
        self.assertEqual(
            engine.norm({"add": [{"$succ": {"var": "m"}}, {"var": "n"}]}),
            {"$succ": {"add": [{"var": "m"}, {"var": "n"}]}},
        )
        self.assertEqual(
            engine.norm({"add": [{"int": 0}, {"int": 3}]}), {"add": [{"int": 0}, {"int": 3}]}
        )
        c = proposal(self.root, t)["candidates"][0]
        self.assertEqual(c["request"]["program"]["proof"]["variable"], "m")
        self.assertIn("Nat.zero_add", c["proof_source"])
        self.assertIn("Nat.succ_add", c["proof_source"])

    def test_helper_is_proved_before_use_without_appearing_in_final_premises(self):
        p = proposal(self.root, cases()[1]["target"])
        c = p["candidates"][0]
        program = c["request"]["program"]
        self.assertEqual(len(program["lemmas"]), 1)
        self.assertIn("@lemma.", json.dumps(program["proof"]))
        compiled = compile_program(c["request"])
        full = compile_request({**cases()[1]["target"], "proof": {"method": "rfl"}})
        self.assertEqual(compiled["formal_statement"], full["formal_statement"])
        self.assertEqual(len(compiled["assumptions"]), 1)
        self.assertEqual(
            compiled["local_lemmas"][0]["compiled"]["request"]["assumptions"],
            cases()[1]["target"]["assumptions"],
        )
        self.assertLess(
            c["proof_source"].index("have mra_lemma_0"), c["proof_source"].index("have mra_use0")
        )

    def test_circular_forward_unknown_and_raw_rule_references_are_rejected(self):
        t = cases()[0]["target"]
        for rule in ("@lemma.self", "@ih", "by exact False.elim", "Nat.add_comm"):
            proof = {
                "method": "rewrite",
                "steps": [{"rule": rule, "reverse": False}],
                "finish": "rfl",
            }
            with self.assertRaises(ValueError):
                compile_program(request(t, proof))
        lemma = {
            "name": "self",
            "goal": t["goal"],
            "proof": {
                "method": "rewrite",
                "steps": [{"rule": "@lemma.self", "reverse": False}],
                "finish": "rfl",
            },
        }
        with self.assertRaises(ValueError):
            compile_program(request(t, lemmas=[lemma]))

    def test_local_lemma_limits_duplicate_alias_unknown_free_variable_and_wrong_type(self):
        t = cases()[0]["target"]
        lemma = {
            "name": "test",
            "goal": {"eq": [{"nat": 0}, {"nat": 0}]},
            "proof": {"method": "rfl"},
        }
        for lemmas in (
            [lemma, lemma],
            [lemma, {**lemma, "name": "two"}, {**lemma, "name": "three"}],
            [{**lemma, "goal": {"eq": [{"var": "unbound"}, {"nat": 0}]}}],
            [{**lemma, "goal": {"eq": [{"var": "x"}, {"nat": 0}]}}],
        ):
            with self.assertRaises(ValueError):
                compile_program(request(t, lemmas=lemmas))

    def test_local_lemmas_can_use_earlier_proved_obligations_only(self):
        t = cases()[0]["target"]
        first = {
            "name": "first",
            "goal": {"eq": [{"var": "x"}, {"var": "x"}]},
            "proof": {"method": "rfl"},
        }
        second = {
            "name": "second",
            "goal": t["goal"],
            "proof": {
                "method": "rewrite",
                "steps": [{"rule": "@lemma.first", "reverse": False}],
                "finish": "grind",
            },
        }
        value = compile_program(request(t, lemmas=[first, second]))
        self.assertEqual(len(value["local_lemmas"]), 2)
        self.assertIn("rw [mra_use0]", value["local_lemmas"][1]["proof_source"])

    def test_dependent_original_premises_are_not_silently_generalized(self):
        t = deepcopy(cases()[1]["target"])
        t["assumptions"].append(
            {"name": "condition", "formula": {"eq": [{"var": "x"}, {"var": "x"}]}}
        )
        self.assertEqual(templates(t), [])
        lemma = {
            "name": "test",
            "goal": {"eq": [{"var": "x"}, {"var": "x"}]},
            "proof": {"method": "rfl"},
        }
        with self.assertRaisesRegex(ValueError, "closed"):
            compile_program(request(t, lemmas=[lemma]))
        self.assertEqual(compile_program(request(t))["target"], t)

    def test_template_requires_actual_typed_commutation_and_index_independent_step(self):
        self.assertTrue(templates(cases()[1]["target"]))
        self.assertEqual(templates(cases()[4]["target"]), [])
        self.assertEqual(templates(cases()[3]["target"]), [])
        t = deepcopy(cases()[1]["target"])
        t["assumptions"][0]["formula"]["forall"]["body"]["eq"][1] = {"var": "u"}
        self.assertEqual(templates(t), [])

    def test_proposal_shares_expansions_normalization_and_wall_across_helpers(self):
        for n in (1, 30, 100):
            p = proposal(self.root, cases()[1]["target"], max_expansions=n)
            self.assertLessEqual(p["stats"]["expansions"], n)
            counts = [a["expansions_total"] for a in p["synthesis_analysis"]]
            self.assertEqual(counts, sorted(counts))
            self.assertLessEqual(p["stats"]["normalization_nodes"], 32768)
        with patch("mra.structure_strategy_terms.WALL_SECONDS", 0):
            p = proposal(self.root, cases()[1]["target"])
        self.assertEqual(p["math_verdict"], "UNKNOWN")

    def test_cross_schema_budget_reference_and_unknown_fields_are_rejected(self):
        t = cases()[0]["target"]
        for change in (
            {"timeout_seconds": True},
            {"timeout_seconds": 19},
            {"schema": "explicit-structures-v1"},
            {"extra": 1},
        ):
            with self.assertRaises(ValueError):
                compile_program({**request(t), **change})
        for budget in ({"max_expansions": True}, {"max_candidates": 0}):
            with self.assertRaises(ValueError):
                proposal(self.root, t, **budget)
        with self.assertRaises(ValueError):
            proposal(self.root, {**t, "proof": {"method": "rfl"}})

    def test_original_generator_identity_unchanged_by_new_compiler_change(self):
        old = old_manifest(self.root)
        before = manifest(self.root)
        p = self.root / "mra/recursion_proofs.py"
        p.write_bytes(p.read_bytes() + b"\n")
        self.assertEqual(old_manifest(self.root), old)
        self.assertNotEqual(manifest(self.root), before)

    def test_named_confirmation_idempotence_cancel_and_source_drift_fail_closed(self):
        base = self.root / "artifacts/recursion-fixture"
        store = RecursionStore(self.root, base=base)
        raw = request(cases()[0]["target"])
        preview = store.preview(raw)
        envelope = {
            "request_id": "NONEXECUTABLE-protocol-fixture",
            "request": raw,
            "plan_fingerprint": preview["plan_fingerprint"],
            "reviewer": "NONEXECUTABLE protocol fixture",
            "confirmed": True,
        }
        with self.assertRaises(ValueError):
            store.submit({**envelope, "confirmed": False})
        row = store.submit(envelope)
        self.assertEqual(store.submit(envelope)["run_id"], row["run_id"])
        self.assertEqual(store.list_runs()[0]["title"], raw["target"]["title"])
        self.assertEqual(store.list_runs()[0]["state"], "QUEUED")
        self.assertEqual(store.cancel(row["run_id"])["math_verdict"], "UNKNOWN")
        self.assertEqual(store.list_runs()[0]["state"], "CANCELLED")
        p = self.root / "mra/recursion_proofs.py"
        p.write_bytes(p.read_bytes() + b"\n")
        self.assertTrue(store.report(row["run_id"])["invalidated"])
        listed = store.list_runs()[0]
        self.assertTrue(listed["invalidated"])
        self.assertEqual((listed["math_verdict"], listed["evidence_level"]), ("UNKNOWN", "E0"))

    def test_unknown_queued_and_unused_false_helper_never_claim_proof(self):
        t = deepcopy(cases()[0]["target"])
        t["goal"] = {"eq": [{"var": "x"}, {"var": "x"}]}
        bad = {"name": "bad", "goal": {"eq": [{"nat": 0}, {"nat": 1}]}, "proof": {"method": "rfl"}}
        p = prepare(self.root, request(t, lemmas=[bad]))
        self.assertEqual((p["math_verdict"], p["evidence_level"]), ("UNKNOWN", "E0"))
        self.assertEqual(p["local_lemmas"][0]["compiled"]["request"]["goal"], bad["goal"])
        self.assertEqual(p["target"], t)


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "Explicit genuine recursion programs and controls"
)
class RealTests(unittest.TestCase):
    def test_a_declared_comparison_keeps_every_failure_and_original_target(self):
        out = OUT / ("real-" + uuid.uuid4().hex)
        out.mkdir(parents=True)

        def save(n, v):
            (out / n).write_bytes(canonical(v) + b"\n")

        old = OldChecks(ROOT, out)
        new = NewChecks(ROOT, out)
        plan = {
            "context": new.context(),
            "dataset": json.loads((ROOT / DATASET).read_text()),
            "cases": [],
        }
        for case in cases():
            plan["cases"].append(
                {
                    **case,
                    "arms": {
                        "v4": old_propose(
                            ROOT,
                            {
                                "schema": "structure-strategy-proposals-v4",
                                "target": case["target"],
                                "budget": DEFAULT_BUDGET,
                            },
                        ),
                        "recursion": proposal(ROOT, case["target"]),
                    },
                }
            )
        save("declared-plan.json", plan)
        results = []
        for i, case in enumerate(plan["cases"]):
            for arm in ("v4", "recursion") if i % 2 == 0 else ("recursion", "v4"):
                store = old if arm == "v4" else new
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
                        raw = candidate["request"]
                        target = (
                            {k: v for k, v in raw.items() if k != "proof"}
                            if arm == "v4"
                            else raw["target"]
                        )
                        self.assertEqual(target, case["target"])
                        row = store.submit(
                            {
                                "request_id": "recursion-" + uuid.uuid4().hex,
                                "request": raw,
                                "plan_fingerprint": store.preview(raw)["plan_fingerprint"],
                                "reviewer": "MRA073 complete original target and local obligation alignment reviewer",
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
                        if case["id"] == "missing-commutation":
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
            for arm in ("v4", "recursion")
        }
        required = {
            key: any(
                r["case_id"] == key
                and r["arm"] == "recursion"
                and r.get("check", {}).get("math_verdict") == "PROVED"
                for r in results
            )
            for key in ("iterate-addition", "iterate-commuting-composition")
        }
        save(
            "summary.json",
            {
                "all_passed": len(results) == 24 and all(required.values()),
                "required_goals": required,
                "arms": summary,
                "slots": len(results),
                "checks": sum("check" in r for r in results),
                "math_verdict": "UNKNOWN",
                "evidence_level": "E0",
            },
        )
        self.assertEqual(len(results), 24)
        for key, passed in required.items():
            self.assertTrue(passed, key)

    def test_b_false_helper_and_cancel_after_genuine_replay_do_not_promote(self):
        out = OUT / ("real-controls-" + uuid.uuid4().hex)
        out.mkdir(parents=True)
        store = NewChecks(ROOT, out)
        t = deepcopy(cases()[0]["target"])
        t["goal"] = {"eq": [{"var": "x"}, {"var": "x"}]}
        bad = {"name": "bad", "goal": {"eq": [{"nat": 0}, {"nat": 1}]}, "proof": {"method": "rfl"}}
        rows = []
        for label, raw in (
            ("false-helper", request(t, lemmas=[bad])),
            ("cancel-after-replay", request(t)),
        ):
            row = store.submit(
                {
                    "request_id": label,
                    "request": raw,
                    "plan_fingerprint": store.preview(raw)["plan_fingerprint"],
                    "reviewer": "MRA073 real negative/control alignment reviewer",
                    "confirmed": True,
                }
            )
            if label == "cancel-after-replay":
                original = NativeLeanBackend._run_worker

                def worker(backend, *args, **kwargs):
                    result = original(backend, *args, **kwargs)
                    if kwargs["phase"] == "REPLAY_RUNNING":
                        self.assertEqual(result["returncode"], 0)
                        store.cancel(row["run_id"])
                    return result

                with patch.object(NativeLeanBackend, "_run_worker", worker):
                    store.work_once(row["run_id"])
            else:
                store.work_once(row["run_id"])
            child = store.report(row["run_id"])
            rows.append(child)
            self.assertEqual((child["math_verdict"], child["evidence_level"]), ("UNKNOWN", "E0"))
            self.assertEqual(child["state"], "REJECTED" if label == "false-helper" else "CANCELLED")
        (out / "results.json").write_bytes(canonical(rows) + b"\n")
        (out / "summary.json").write_bytes(
            canonical(
                {"all_passed": True, "checks": 2, "math_verdict": "UNKNOWN", "evidence_level": "E0"}
            )
            + b"\n"
        )

    def test_c_real_supervisor_exit_after_replay_does_not_publish_or_resume(self):
        out = OUT / ("real-exit-" + uuid.uuid4().hex)
        out.mkdir(parents=True)
        store = NewChecks(ROOT, out)
        t = deepcopy(cases()[0]["target"])
        t["goal"] = {"eq": [{"var": "x"}, {"var": "x"}]}
        raw = request(t)
        row = store.submit(
            {
                "request_id": "real-exit",
                "request": raw,
                "plan_fingerprint": store.preview(raw)["plan_fingerprint"],
                "reviewer": "MRA073 genuine supervisor exit alignment reviewer",
                "confirmed": True,
            }
        )
        code = """import json,os,sys
from pathlib import Path
from unittest.mock import patch
from mra.verifier import NativeLeanBackend
from test_recursion_programs import NewChecks,ROOT
area=Path(sys.argv[1]);key=sys.argv[2];store=NewChecks(ROOT,area,False);original=NativeLeanBackend._run_worker;phases=[]
def run(backend,*args,**kwargs):
 result=original(backend,*args,**kwargs);phases.append({'phase':kwargs['phase'],'outcome':result});(area/'returned-phases.json').write_text(json.dumps(phases))
 if kwargs['phase']=='REPLAY_RUNNING':os._exit(73)
 return result
with patch.object(NativeLeanBackend,'_run_worker',run):store.work_once(key)
"""
        p = subprocess.run(
            [sys.executable, "-c", code, str(out), row["run_id"]],
            capture_output=True,
            text=True,
            timeout=300,
            env={**os.environ, "PYTHONPATH": ".:tests"},
        )
        (out / "stdout.log").write_text(p.stdout)
        (out / "stderr.log").write_text(p.stderr)
        self.assertEqual(p.returncode, 73)
        phases = json.loads((out / "returned-phases.json").read_text())
        self.assertEqual(len(phases), 2)
        for phase in phases:
            self.assertEqual(phase["outcome"]["returncode"], 0)
        for folder, marker in (("candidate", "MRA_AUDIT:"), ("replay", "MRA_REPLAY:")):
            audit(
                (store.directory(row["run_id"]) / folder / "stdout.log").read_text(),
                marker,
                replay=folder == "replay",
            )
        pending = store.report(row["run_id"])
        self.assertEqual((pending["math_verdict"], pending["evidence_level"]), ("UNKNOWN", "E0"))
        self.assertIn(row["run_id"], store.recover()["interrupted"])
        self.assertEqual(store.report(row["run_id"])["state"], "INTERRUPTED")
        self.assertEqual(store.work_once(row["run_id"]), {"worker_state": "IDLE"})
        (out / "summary.json").write_bytes(
            canonical(
                {
                    "all_passed": True,
                    "exit_code": 73,
                    "run_id": row["run_id"],
                    "check": store.report(row["run_id"]),
                    "math_verdict": "UNKNOWN",
                    "evidence_level": "E0",
                }
            )
            + b"\n"
        )

    def test_d_two_checked_local_lemmas_compose_in_the_full_result(self):
        out = OUT / ("real-chain-" + uuid.uuid4().hex)
        out.mkdir(parents=True)
        store = NewChecks(ROOT, out)
        candidate = proposal(ROOT, cases()[1]["target"])["candidates"][0]["request"]
        raw = deepcopy(candidate)
        first = raw["program"]["lemmas"][0]
        second = {
            "name": "inverse_transport",
            "goal": {"eq": list(reversed(first["goal"]["eq"]))},
            "proof": {
                "method": "rewrite",
                "steps": [{"rule": "@lemma." + first["name"], "reverse": False}],
                "finish": "rfl",
            },
        }
        raw["program"]["lemmas"].append(second)
        for step in raw["program"]["proof"]["step"]["steps"]:
            if step["rule"] == "@lemma." + first["name"]:
                step["rule"] = "@lemma.inverse_transport"
                step["reverse"] = not step["reverse"]
        (out / "declared-request.json").write_bytes(canonical(raw) + b"\n")
        row = store.submit(
            {
                "request_id": "two-local-lemmas",
                "request": raw,
                "plan_fingerprint": store.preview(raw)["plan_fingerprint"],
                "reviewer": "MRA073 two local obligations complete target reviewer",
                "confirmed": True,
            }
        )
        store.work_once(row["run_id"])
        child = store.report(row["run_id"])
        (out / "results.json").write_bytes(canonical([child]) + b"\n")
        verified(child)
        self.assertEqual(len(child["plan"]["local_lemmas"]), 2)
        (out / "summary.json").write_bytes(
            canonical(
                {"all_passed": True, "checks": 1, "math_verdict": "UNKNOWN", "evidence_level": "E0"}
            )
            + b"\n"
        )
