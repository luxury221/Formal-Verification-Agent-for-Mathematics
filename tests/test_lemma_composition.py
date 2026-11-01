"""Contract tests use nonexecutable fixtures; RealTests require actual Lean processes."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from mra.domain import canonical
from mra.lemma_composition import FILES, LemmaCompositionStore, audit_imports, render_composition
from mra.lemma_composition_terms import MODE
from mra.mathlib_lab import POLICY, audit
from mra.mathlib_terms import examples
from mra.tasks import TaskStore, WorkerBusy
from mra.verifier import NativeLeanBackend
from test_search_evaluation import copy_sources

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/lemma-composition-20261028-01"


def signatures():
    chain = examples()[0]
    x, y = {"var": "x"}, {"var": "y"}
    square = {
        **chain,
        "title": "整除平方的条件引理",
        "original": "自然数x、y，若x整除y，则x整除y*y。",
        "variables": ["x", "y"],
        "assumptions": [{"name": "hxy", "formula": {"dvd": [x, y]}}],
        "goal": {"dvd": [x, {"mul": [y, y]}]},
        "proof": {
            "ref": "Nat.dvd_trans",
            "terms": [x, y, {"mul": [y, y]}],
            "proofs": [{"hyp": "hxy"}, {"ref": "Nat.dvd_mul_right", "terms": [y, y], "proofs": []}],
        },
    }
    return chain, square


def composition(ids=None):
    ids = ids or ["ml-knowledge-" + "a" * 32, "ml-knowledge-" + "b" * 32]
    d, x, y = ({"var": name} for name in ("d", "x", "y"))
    total = {"add": [x, y]}
    return {
        "schema": MODE,
        "title": "用两个已归档条件引理证明整除平方",
        "original": "d、x、y为自然数，若d整除x且x整除y，则d整除(x+y)*(x+y)。",
        "variables": ["d", "x", "y"],
        "assumptions": [
            {"name": "hdx", "formula": {"dvd": [d, x]}},
            {"name": "hxy", "formula": {"dvd": [x, y]}},
        ],
        "goal": {"dvd": [d, {"mul": [total, total]}]},
        "sources": [
            {"alias": name, "item_id": item} for name, item in zip(("chain", "square"), ids)
        ],
        "proof": {
            "lemma": "square",
            "terms": [d, total],
            "proofs": [
                {"lemma": "chain", "terms": [d, x, y], "proofs": [{"hyp": "hdx"}, {"hyp": "hxy"}]}
            ],
        },
        "timeout_seconds": 30,
    }


def submission(store, request, **changes):
    return {
        "request_id": "composition-" + uuid.uuid4().hex,
        "request": request,
        "plan_fingerprint": store.preview(request)["plan_fingerprint"],
        "reviewer": "MRA047 explicit composition reviewer",
        "confirmed": True,
        **changes,
    }


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_sources(self.root)
        for name in FILES:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        self.store = LemmaCompositionStore(self.root)
        self.request = composition()
        self.fixtures = {
            b["item_id"]: {
                **b,
                "request": signature,
                "formal_statement": "not executable contract data",
            }
            for b, signature in zip(self.request["sources"], signatures())
        }

        def contract_source(_, binding):
            return {**deepcopy(self.fixtures[binding["item_id"]]), **binding}, b"not a proof"

        self.addCleanup(patch.stopall)
        patch("mra.lemma_composition.load_source", side_effect=contract_source).start()

    def test_full_target_and_all_instantiated_conditions_remain_visible(self):
        with patch.object(
            NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean in preview")
        ):
            plan = self.store.preview(self.request)
        self.assertEqual(plan["request"], self.request)
        self.assertEqual(plan["math_verdict"], "UNKNOWN")
        self.assertEqual(len(plan["applications"]), 2)
        self.assertEqual([len(a["instantiated_assumptions"]) for a in plan["applications"]], [2, 1])
        self.assertIn("@MraCandidate.Reused.s0.checked", plan["proof_source"])
        self.assertIn("@MraCandidate.Reused.s1.checked", plan["proof_source"])
        self.assertEqual(plan["references"], [])
        self.assertEqual(self.store.list_runs(), [])

    def test_confirmation_is_named_exact_and_idempotent(self):
        data = submission(self.store, self.request)
        for change in (
            {"confirmed": False},
            {"confirmed": 1},
            {"reviewer": " "},
            {"plan_fingerprint": "old"},
        ):
            with self.assertRaises(ValueError):
                self.store.submit({**data, **change})
        row = self.store.submit(data)
        self.assertEqual(self.store.submit(data)["run_id"], row["run_id"])
        with self.assertRaises(ValueError):
            self.store.submit({**data, "reviewer": "Someone else"})

    def test_missing_premise_wrong_arity_unknown_alias_and_arbitrary_source_rejected(self):
        for proof in (
            {"lemma": "square", "terms": [{"var": "d"}], "proofs": []},
            {"lemma": "absent", "terms": [], "proofs": []},
            {"lemma": "square", "terms": [{"var": "d"}, {"var": "x"}], "proofs": []},
            "by sorry",
            {"ref": "False.elim", "terms": [], "proofs": []},
        ):
            with self.subTest(proof=proof), self.assertRaises(ValueError):
                self.store.preview({**self.request, "proof": proof})

    def test_wrong_typed_premise_is_not_rewritten_or_added_during_preview(self):
        request = deepcopy(self.request)
        request["proof"]["proofs"][0]["proofs"][1] = {"hyp": "hdx"}
        plan = self.store.preview(request)
        self.assertEqual(plan["request"]["assumptions"], request["assumptions"])
        self.assertEqual(plan["request"]["proof"], request["proof"])
        self.assertEqual(plan["math_verdict"], "UNKNOWN")

    def test_unsafe_sources_limits_unknown_fields_and_injection_are_rejected(self):
        for changes in (
            {"sources": []},
            {"sources": self.request["sources"] * 3},
            {"sources": [{**self.request["sources"][0], "alias": "x; sorry"}]},
            {"sources": [{**self.request["sources"][0], "item_id": "../proof"}]},
            {"sources": [self.request["sources"][0]] * 2},
            {"timeout_seconds": True},
            {"schema": "arbitrary-lean"},
            {"imports": ["Mathlib"]},
            {"goal": {"not": [{"nat": 0}]}},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.preview({**self.request, **changes})

    def test_unused_source_is_not_silently_trusted(self):
        request = deepcopy(self.request)
        request["proof"] = request["proof"]["proofs"][0]
        with self.assertRaisesRegex(ValueError, "explicitly used"):
            self.store.preview(request)

    def test_preview_does_not_expose_mutable_global_policy(self):
        before = deepcopy(POLICY)
        plan = self.store.preview(self.request)
        plan["source_manifest"]["policy"]["allowed_axioms"].append("False")
        plan["source_manifest"]["executor"]["policy"]["allowed_axioms"].append("False")
        self.assertEqual(POLICY, before)

    def test_import_audit_requires_exact_slots_types_and_policy(self):
        record = {
            "slot": 0,
            "type_matches": True,
            "candidate_module_imported": False,
            "replayed_declarations": 1,
            "axioms": [],
        }
        self.assertEqual(audit_imports("MRA_IMPORT:" + json.dumps(record), 1), [record])
        for change in (
            {"slot": True},
            {"slot": 1},
            {"type_matches": False},
            {"candidate_module_imported": True},
            {"axioms": ["sorryAx"]},
            {"replayed_declarations": 0},
        ):
            with self.assertRaises(ValueError):
                audit_imports("MRA_IMPORT:" + json.dumps({**record, **change}), 1)
        with self.assertRaises(ValueError):
            audit_imports("", 1)
        with self.assertRaises(ValueError):
            audit_imports("\n".join(["MRA_IMPORT:" + json.dumps(record)] * 2), 1)

    def test_cancel_and_recovery_never_execute_or_promote(self):
        first = self.store.submit(submission(self.store, self.request))
        self.store.cancel(first["run_id"])
        with patch.object(self.store, "execute", side_effect=AssertionError("No cancelled work")):
            self.assertEqual(self.store.work_once(first["run_id"])["worker_state"], "IDLE")
        second = self.store.submit(submission(self.store, self.request))
        with self.store.transaction() as db:
            row = self.store.read(second["run_id"], db)
            row.update(state="RUNNING", stage="REPLAY_RUNNING")
            self.store.save(db, row)
        self.assertEqual(self.store.recover()["interrupted"], [second["run_id"]])
        self.assertEqual(self.store.report(second["run_id"])["math_verdict"], "UNKNOWN")

    def test_worker_lock_and_temporary_source_errors_do_not_invalidate(self):
        row = self.store.submit(submission(self.store, self.request))
        with self.store.tasks.worker_lock(), self.assertRaises(WorkerBusy):
            self.store.work_once(row["run_id"])
        for error in (WorkerBusy("busy"), sqlite3.OperationalError("database is locked")):
            with (
                patch("mra.lemma_composition.load_source", side_effect=error),
                self.assertRaises(type(error)),
            ):
                self.store.report(row["run_id"])
            self.assertFalse(self.store.report(row["run_id"])["invalidated"])

    def test_source_version_drift_is_persistent_after_restoration(self):
        row = self.store.submit(submission(self.store, self.request))
        key = self.request["sources"][0]["item_id"]
        self.fixtures[key]["version"] = 2
        self.assertTrue(self.store.report(row["run_id"])["invalidated"])
        self.fixtures[key].pop("version")
        self.assertTrue(self.store.report(row["run_id"])["invalidated"])

    def test_generated_source_uses_only_data_imports_and_isolates_both_slots(self):
        plan = self.store.preview(self.request)
        source = render_composition(self.root, plan)
        self.assertIn('"/work/source-0.ndjson"', source)
        self.assertIn('"/work/source-1.ndjson"', source)
        self.assertIn("MraCandidate.Reused.s0.checked", source)
        self.assertIn("MraCandidate.Reused.s1.checked", source)
        self.assertNotIn("__MRA_", source)
        self.assertNotIn("ofKernelEnv", source.split("namespace MraCandidate\n")[-1])
        self.assertNotIn("import Candidate", source)

    def test_capture_free_application_handles_target_name_matching_source_premise_name(self):
        request = deepcopy(self.request)
        request["variables"].append("premise0")
        self.assertEqual(self.store.preview(request)["request"]["variables"], request["variables"])

    def test_corrupt_ledger_and_redirected_store_are_rejected(self):
        row = self.store.submit(submission(self.store, self.request))
        with self.store.transaction() as db:
            db.execute("UPDATE runs SET sha256='wrong'")
        with self.assertRaises(ValueError):
            self.store.report(row["run_id"])
        target = self.store.path
        self.store.path = self.store.base / "redirected.sqlite3"
        self.store.path.symlink_to(target)
        with self.assertRaises(ValueError):
            self.store.list_runs()


def real_store(base, source_lab=None):
    """Independent data stores, shared real sealed runtime and global worker lock."""
    store = LemmaCompositionStore(base)
    store.root = ROOT
    store.tasks = TaskStore(ROOT)
    store.knowledge.root = ROOT
    store.knowledge.sources.root = ROOT
    if source_lab is not None:
        store.knowledge.sources.mathlib = source_lab
    lab = store.knowledge.sources.mathlib
    lab.root = ROOT
    lab.tasks = TaskStore(ROOT)
    return store


def admit(knowledge, run_id):
    plan = knowledge.prepare({"run_id": run_id})
    return knowledge.admit(
        {
            "request_id": "composition-source-" + uuid.uuid4().hex,
            "run_id": run_id,
            "plan_fingerprint": plan["plan_fingerprint"],
            "reviewer": "MRA047 authored-source alignment reviewer",
            "confirmed": True,
            "rights_confirmed": True,
        }
    )


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "Requires actual sealed Lean and kernel replay"
)
class RealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suite = OUT / ("real-suite-" + uuid.uuid4().hex)
        cls.suite.mkdir(parents=True)
        cls.source_hashes = {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FILES
        }
        (cls.suite / "source-hashes.json").write_bytes(canonical(cls.source_hashes) + b"\n")
        cls.store = real_store(cls.suite / "primary")
        cls.lab = cls.store.knowledge.sources.mathlib
        cls.sources = []
        for value in signatures():
            row = cls.lab.submit(submission(cls.lab, value))
            cls.lab.work_once(row["run_id"])
            checked = cls.lab.report(row["run_id"])
            assert checked["state"] == "COMPLETED" and checked["math_verdict"] == "PROVED", checked
            cls.sources.append(admit(cls.store.knowledge, row["run_id"]))
        cls.request = composition([s["item_id"] for s in cls.sources])
        (cls.suite / "request.json").write_bytes(canonical(cls.request) + b"\n")
        (cls.suite / "sources.json").write_bytes(canonical(cls.sources) + b"\n")

    @classmethod
    def tearDownClass(cls):
        assert all(
            hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == value
            for name, value in cls.source_hashes.items()
        )

    def execute(self, request=None, store=None):
        store = store or self.store
        row = store.submit(submission(store, request or self.request))
        store.work_once(row["run_id"])
        result = store.report(row["run_id"])
        (self.suite / (self._testMethodName + ".json")).write_bytes(canonical(result) + b"\n")
        return result

    def test_a_two_different_same_named_theorems_form_a_new_full_theorem(self):
        row = self.execute()
        self.assertEqual(
            (row["state"], row["math_verdict"], row["evidence_level"]),
            ("COMPLETED", "PROVED", "E4"),
            row["result"],
        )
        candidate, replay = row["result"]["candidate"], row["result"]["replay"]
        self.assertNotEqual(candidate["pid"], replay["pid"])
        self.assertEqual(len(candidate["imports"]), 2)
        self.assertEqual(replay["audit"]["candidate_module_imported"], False)
        self.assertGreaterEqual(replay["audit"]["replayed_declarations"], 5)
        self.assertEqual(row["plan"]["references"], [])
        data = (self.store.directory(row["run_id"]) / "replay/proof.ndjson").read_text()
        self.assertIn('"str":"Reused"', data)
        self.assertIn('"str":"s0"', data)
        self.assertIn('"str":"s1"', data)
        self.assertEqual(self.store.report(row["run_id"])["math_verdict"], "PROVED")

    def test_b_wrong_premise_is_rejected_by_real_lean(self):
        value = deepcopy(self.request)
        value["proof"]["proofs"][0]["proofs"][1] = {"hyp": "hdx"}
        row = self.execute(value)
        self.assertEqual(
            (row["state"], row["math_verdict"]), ("REJECTED", "UNKNOWN"), row["result"]
        )
        self.assertIsNone(row["result"]["replay"])
        self.assertIn("error:", row["result"]["error"])

    def test_c_wrong_nat_argument_is_rejected_by_real_lean(self):
        value = deepcopy(self.request)
        value["proof"]["terms"][1] = {"var": "x"}
        row = self.execute(value)
        self.assertEqual((row["state"], row["math_verdict"]), ("REJECTED", "UNKNOWN"))
        self.assertIsNone(row["result"]["replay"])

    def test_d_source_type_bridge_rejects_wrong_host_signature(self):
        original = render_composition

        def wrong_type(root, plan):
            plan = deepcopy(plan)
            plan["source_items"][0]["formal_statement"] = "False"
            return original(root, plan)

        with patch("mra.lemma_composition.render_composition", side_effect=wrong_type):
            row = self.execute()
        self.assertEqual(row["math_verdict"], "UNKNOWN")
        self.assertIn("MRA_IMPORT_TARGET_MISMATCH", row["result"]["error"])

    def test_e_new_axiom_in_proof_data_is_rejected_before_use(self):
        original = NativeLeanBackend._run_worker

        def inject(backend, work, *args, **kwargs):
            if kwargs["phase"] == "LEAN_RUNNING":
                path = work / "source-0.ndjson"
                objects = [json.loads(line) for line in path.read_text().splitlines()]
                theorem = next(o["thm"] for o in objects if "thm" in o)
                at = next(i for i, o in enumerate(objects) if "thm" in o)
                objects[at] = {
                    "axiom": {
                        "name": theorem["name"],
                        "levelParams": theorem["levelParams"],
                        "type": theorem["type"],
                        "isUnsafe": False,
                    }
                }
                path.write_text("\n".join(json.dumps(o) for o in objects) + "\n")
            return original(backend, work, *args, **kwargs)

        with patch.object(NativeLeanBackend, "_run_worker", inject):
            row = self.execute()
        self.assertEqual(row["math_verdict"], "UNKNOWN")
        self.assertIn("MRA_IMPORT_UNAPPROVED_DECLARATION", row["result"]["error"])

    def test_f_completed_result_is_persistently_invalidated_after_source_withdrawal(self):
        store = real_store(self.suite / "withdrawal", self.lab)
        sources = [admit(store.knowledge, s["source_ref"]["run_id"]) for s in self.sources]
        row = self.execute(composition([s["item_id"] for s in sources]), store)
        self.assertEqual(row["math_verdict"], "PROVED", row["result"])
        store.knowledge.retire(
            sources[0]["item_id"],
            {
                "request_id": uuid.uuid4().hex,
                "reviewer": "MRA047 lifecycle reviewer",
                "reason": "Withdraw only this independent test archive",
                "confirmed": True,
            },
        )
        final = store.report(row["run_id"])
        self.assertEqual(
            (final["state"], final["math_verdict"], final["invalidated"]),
            ("COMPLETED", "UNKNOWN", True),
        )
        self.assertEqual(self.store.knowledge.report(self.sources[0]["item_id"])["state"], "ACTIVE")
        (self.suite / (self._testMethodName + "-after.json")).write_bytes(canonical(final) + b"\n")

    def test_g_archived_byte_restoration_does_not_revive_a_confirmed_plan(self):
        store = real_store(self.suite / "corruption", self.lab)
        sources = [admit(store.knowledge, s["source_ref"]["run_id"]) for s in self.sources]
        row = store.submit(submission(store, composition([s["item_id"] for s in sources])))
        path = store.knowledge.directory(sources[0]["item_id"]) / "source-record.json"
        raw = path.read_bytes()
        path.write_bytes(raw + b"\nchanged")
        self.assertTrue(store.report(row["run_id"])["invalidated"])
        path.write_bytes(raw)
        self.assertTrue(store.report(row["run_id"])["invalidated"])
        self.assertEqual(store.report(row["run_id"])["math_verdict"], "UNKNOWN")

    def test_h_successful_raw_replay_followed_by_supervisor_exit_never_promotes(self):
        row = self.store.submit(submission(self.store, self.request))
        script = "\n".join(
            [
                "import os, sys",
                "from pathlib import Path",
                "from mra.domain import canonical",
                "from mra.verifier import NativeLeanBackend",
                "from test_lemma_composition import real_store",
                "store = real_store(Path(sys.argv[1]))",
                "original = NativeLeanBackend._run_worker",
                "def stop_after_replay(self, work, *args, **kwargs):",
                "    result = original(self, work, *args, **kwargs)",
                "    if kwargs['phase'] == 'REPLAY_RUNNING':",
                "        (work / 'supervisor-before-exit.json').write_bytes(canonical(result))",
                "        os._exit(73)",
                "    return result",
                "NativeLeanBackend._run_worker = stop_after_replay",
                "store.work_once(sys.argv[2])",
            ]
        )
        result = subprocess.run(
            [sys.executable, "-c", script, str(self.suite / "primary"), row["run_id"]],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(result.returncode, 73, result.stderr)
        directory = self.store.directory(row["run_id"])
        outcome = json.loads((directory / "replay/supervisor-before-exit.json").read_text())
        self.assertEqual((outcome["run_status"], outcome["returncode"]), ("COMPLETED", 0))
        audit((directory / "replay/stdout.log").read_text(), "MRA_REPLAY:", replay=True)
        self.assertEqual(self.store.read(row["run_id"])["state"], "RUNNING")
        self.store.recover()
        final = self.store.report(row["run_id"])
        self.assertEqual((final["state"], final["math_verdict"]), ("INTERRUPTED", "UNKNOWN"))
        (self.suite / (self._testMethodName + ".json")).write_bytes(canonical(final) + b"\n")


if __name__ == "__main__":
    unittest.main()
