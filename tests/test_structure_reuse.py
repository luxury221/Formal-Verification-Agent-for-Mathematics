"""Typed application contracts and new, explicitly opted-in real kernel experiments."""

from copy import deepcopy
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from mra.domain import canonical
from mra.lemma_composition import FILES as IMPORT_FILES
from mra.library_composition import LibraryCompositionStore
from mra.research_flow import lease
from mra.tasks import WorkerBusy
from mra.structure_composition_terms import compile_composition, substitute
from mra.structure_research import SOURCES, StructureResearchStore
from mra.structure_reuse import FILES, StructureReuseStore, checked
from mra.structures import FILES as STRUCTURE_FILES
from mra.structure_reuse_examples import example as scenario, request, var, call
from mra.verifier import NativeLeanBackend
from test_search_evaluation import copy_sources
from test_structure_research import confirmation, create, parent

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/structure-reuse-20261029-01"


def binary(op, a, b):
    return {op: [a, b]}


def full_target(value, node):
    target = deepcopy(next(n["target"] for n in value["nodes"] if n["id"] == node))
    return {
        "schema": "explicit-structures-v1",
        **{k: deepcopy(value[k]) for k in ("carriers", "operations", "definitions")},
        **target,
        "assumptions": [*deepcopy(value["laws"]), *target["assumptions"]],
    }


def descriptor(value, graph_id, node="power", alias="power"):
    from mra.structured_terms import compile_request

    target = full_target(value, node)
    return {
        "alias": alias,
        "item_id": f"node.{graph_id}.{node}",
        "graph_id": graph_id,
        "node_id": node,
        "request": target,
        "formal_statement": compile_request(target)["formal_statement"],
        "depth": 0,
        "ancestors": [
            {
                "item_id": f"node.{graph_id}.{node}",
                "version": 1,
                "archive_fingerprint": "NONEXECUTABLE-contract-only",
            }
        ],
    }


def submit(store, raw):
    plan = store.preview(raw)
    return store.submit(
        {
            "request_id": "reuse-qa-" + uuid.uuid4().hex,
            "request": raw,
            "plan_fingerprint": plan["plan_fingerprint"],
            "reviewer": "MRA062 authored QA; not independent semantic review",
            "confirmed": True,
        }
    )


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_sources(self.root)
        for name in (*FILES, *IMPORT_FILES, *SOURCES, *STRUCTURE_FILES):
            shutil.copyfile(ROOT / name, self.root / name)
        self.graphs = StructureResearchStore(self.root)
        self.value = scenario()
        self.graph = create(self.graphs, parent(self.root), self.value)
        self.key = self.graph["graph_id"]
        self.store = StructureReuseStore(self.root, graphs=self.graphs)
        self.raw = request(self.key)
        self.source = descriptor(self.value, self.key)

    def compile(self, raw=None, source=None):
        return compile_composition(
            raw or self.raw, full_target(self.value, "lifted"), [source or self.source]
        )

    def test_complete_target_conditions_definitions_and_let_scope(self):
        p = self.compile()
        self.assertEqual(p["target"], full_target(self.value, "lifted"))
        self.assertTrue(p["proof_source"].startswith("fun (mra_t0 : Type)"))
        self.assertIn("let mra_d0", p["proof_source"])
        self.assertIn("MraCandidate.Reused.s0.checked", p["proof_source"])
        self.assertIn("mra_o4", p["proof_source"])
        self.assertEqual(
            p["applications"][0]["premise_proofs"],
            [{"hyp": law["name"]} for law in self.value["laws"]],
        )
        self.assertEqual(
            p["applications"][0]["instantiated_assumptions"][0]["formula"],
            substitute(
                full_target(self.value, "lifted")["assumptions"][0]["formula"], {}, {"x", "n"}
            ),
        )

    def test_explicit_type_every_variable_and_every_premise_are_required(self):
        for mutate in (
            lambda p: p["proof"]["terms"].pop(),
            lambda p: p["proof"]["proofs"].clear(),
            lambda p: p["proof"]["terms"].__setitem__(0, var("n")),
            lambda p: p["proof"]["terms"].__setitem__(
                0, {"cast": {"to": "Int", "value": var("n")}}
            ),
            lambda p: p["proof"]["proofs"].__setitem__(0, {"hyp": "missing"}),
        ):
            raw = deepcopy(self.raw)
            mutate(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.compile(raw)

    def test_exact_definitions_source_type_bindings_and_all_sources_used(self):
        for change in ("definitions", "type", "bindings", "unused", "tactic"):
            raw, source = deepcopy(self.raw), deepcopy(self.source)
            if change == "definitions":
                source["request"]["definitions"][0]["original"] += " "
            elif change == "type":
                source["formal_statement"] = "True"
            elif change == "bindings":
                raw["sources"][0]["alias"] = "other"
            elif change == "unused":
                raw["proof"] = {"refl": var("x")}
            else:
                raw["proof"] = {"lean": "by grind"}
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.compile(raw, source)

    def test_substitution_is_simultaneous_and_avoids_nested_quantifier_capture(self):
        formula = {
            "forall": {
                "binders": [{"name": "y", "type": "Nat"}],
                "body": binary("eq", var("x"), var("y")),
            }
        }
        result = substitute(formula, {"x": var("y"), "y": var("x")}, {"y", "x"})
        binder = result["forall"]["binders"][0]["name"]
        self.assertNotIn(binder, ("x", "y"))
        self.assertEqual(result["forall"]["body"], binary("eq", var("y"), var(binder)))
        self.assertEqual(
            substitute(
                binary("eq", var("x"), var("y")), {"x": var("y"), "y": var("x")}, {"x", "y"}
            ),
            binary("eq", var("y"), var("x")),
        )

    def test_input_size_cycles_depth_proof_budget_and_no_arbitrary_fields(self):
        cyclic = {}
        cyclic["proof"] = cyclic
        deep = {"hyp": "step"}
        for _ in range(16):
            deep = {"symm": deep}
        for raw in (
            cyclic,
            {**self.raw, "proof": deep},
            {**self.raw, "target": {}},
            {**self.raw, "timeout_seconds": True},
            {**self.raw, "timeout_seconds": float("inf")},
            {**self.raw, "schema": "old"},
            {**self.raw, "proof": {"hyp": "x" * 196609}},
        ):
            with self.subTest(raw_type=type(raw)), self.assertRaises(ValueError):
                self.compile(raw)

    def test_preview_requires_actual_source_and_never_runs_lean(self):
        with patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")):
            with self.assertRaises(ValueError):
                self.store.preview(self.raw)
            with patch.object(self.store, "load_source", return_value=(self.source, b"UNIT ONLY")):
                plan = self.store.preview(self.raw)
                self.assertEqual((plan["math_verdict"], plan["evidence_level"]), ("UNKNOWN", "E0"))
                self.assertEqual(plan["target"], full_target(self.value, "lifted"))

    def test_wrong_graph_prerequisite_and_missing_source_are_rejected(self):
        for change in ({"graph_id": "structureflow-" + "a" * 32}, {"node_id": "problem"}):
            with (
                patch.object(
                    self.store,
                    "load_source",
                    return_value=({**self.source, **change}, b"UNIT ONLY"),
                ),
                self.assertRaises(ValueError),
            ):
                self.store.preview(self.raw)
        raw = deepcopy(self.raw)
        raw["sources"] = []
        with self.assertRaises(ValueError):
            self.store.preview(raw)

    def test_queue_idempotency_confirmation_cancel_and_no_automatic_execution(self):
        with patch.object(self.store, "load_source", return_value=(self.source, b"UNIT ONLY")):
            plan = self.store.preview(self.raw)
            data = {
                "request_id": "idempotent",
                "request": self.raw,
                "plan_fingerprint": plan["plan_fingerprint"],
                "reviewer": "QA",
                "confirmed": True,
            }
            for change in ({"confirmed": 1}, {"reviewer": " "}, {"plan_fingerprint": "old"}):
                with self.assertRaises(ValueError):
                    self.store.submit({**data, **change})
            first = self.store.submit(data)
            self.assertEqual(first, self.store.submit(data))
            self.store.cancel(first["run_id"])
            self.assertEqual(self.store.submit(data)["state"], "CANCELLED")
            self.assertEqual(self.store.work_once(first["run_id"])["worker_state"], "IDLE")
            self.assertEqual(
                self.graphs.report(self.key)["node_states"]["lifted"]["math_verdict"], "UNKNOWN"
            )

    def test_ancestor_audit_shares_outer_write_transaction_and_persists_invalidation(self):
        with patch.object(self.store, "load_source", return_value=(self.source, b"UNIT ONLY")):
            row = submit(self.store, self.raw)
        with patch.object(self.store, "load_source", side_effect=ValueError("withdrawn source")):
            with self.store.transaction() as outer:
                value = self.store.report(row["run_id"])
                with self.store.transaction() as inner:
                    self.assertIs(outer, inner)
                self.assertTrue(value["invalidated"])
        self.assertTrue(self.store.read(row["run_id"])["invalidated"])

    def test_temporary_source_contention_does_not_write_damage_or_unknown(self):
        with patch.object(self.store, "load_source", return_value=(self.source, b"UNIT ONLY")):
            row = submit(self.store, self.raw)
        with patch.object(self.store, "load_source", side_effect=WorkerBusy("reader")):
            with self.assertRaises(WorkerBusy):
                self.store.report(row["run_id"])
        self.assertFalse(self.store.read(row["run_id"])["invalidated"])
        self.assertFalse(self.store.damage_marker("runs", row["run_id"]))

    def test_damaged_ledger_marker_survives_byte_restoration(self):
        with patch.object(self.store, "load_source", return_value=(self.source, b"UNIT ONLY")):
            row = submit(self.store, self.raw)
            with self.store.transaction() as db:
                db.execute("UPDATE runs SET sha256='wrong' WHERE id=?", (row["run_id"],))
            with self.assertRaises(ValueError):
                self.store.report(row["run_id"])
            with self.store.transaction() as db:
                self.store.save(db, row)
            self.assertTrue(self.store.report(row["run_id"])["invalidated"])

    def test_worker_waits_for_graph_reader_without_retrying_execution(self):
        held = threading.Event()

        def reader():
            with lease(self.graphs.directory(self.key)):
                held.set()
                threading.Event().wait(0.2)

        thread = threading.Thread(target=reader)
        calls = []

        def once(store, run_id, lock_fd):
            calls.append(run_id)
            thread.start()
            self.assertTrue(held.wait(5))
            return store.context(self.key, "lifted")[0]

        try:
            with patch.object(LibraryCompositionStore, "execute", once):
                result = self.store.execute("mathlib-" + "a" * 32, None)
        finally:
            thread.join(5)
        self.assertEqual(result["node_id"], "lifted")
        self.assertEqual(len(calls), 1)
        with lease(self.graphs.directory(self.key)), self.assertRaises(WorkerBusy):
            self.store.context(self.key, "lifted")

    def test_fold_substitution_keeps_index_and_zero_outer_but_step_binders_local(self):
        fold = {
            "nat_fold": {
                "index": var("x"),
                "zero": var("acc"),
                "step": {
                    "index": "k",
                    "value": "acc",
                    "body": binary("add", var("x"), binary("add", var("k"), var("acc"))),
                },
            }
        }
        value = substitute(fold, {"x": var("k"), "acc": var("x")}, {"k", "x"})["nat_fold"]
        self.assertEqual(value["index"], var("k"))
        self.assertEqual(value["zero"], var("x"))
        step = value["step"]
        self.assertNotIn(step["index"], ("k", "x", "acc"))
        self.assertNotEqual(step["index"], step["value"])
        self.assertEqual(
            step["body"],
            binary("add", var("k"), binary("add", var(step["index"]), var(step["value"]))),
        )
        quantified = {
            "forall": {"binders": [{"name": "k", "type": "Nat"}], "body": binary("eq", fold, fold)}
        }
        result = substitute(quantified, {"x": var("k"), "acc": var("x")}, {"k", "x"})
        self.assertNotEqual(result["forall"]["binders"][0]["name"], "k")

    def test_environment_changes_and_congruence_binder_boundaries_are_explicit(self):
        for key in ("carriers", "operations", "definitions"):
            source = deepcopy(self.source)
            source["request"][key][0]["original"] += " altered"
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.compile(source=source)
        for variable, body in (
            ({"name": "x", "type": "g"}, var("x")),
            ({"name": "z", "type": "undeclared"}, var("z")),
            ({"name": "z", "type": "g"}, binary("eq", var("z"), var("z"))),
        ):
            raw = {
                **self.raw,
                "proof": {
                    "congr": {"variable": variable, "body": body, "proof": self.raw["proof"]}
                },
            }
            with self.subTest(variable=variable), self.assertRaises(ValueError):
                self.compile(raw)

    def test_forged_success_labels_without_real_audits_are_rejected(self):
        forged = {
            "state": "COMPLETED",
            "math_verdict": "PROVED",
            "evidence_level": "E4",
            "artifact_status": "MATCHES_RECORDED_HASHES",
            "invalidated": False,
            "cancel_requested": False,
            "alignment": {"confirmed": True},
            "result": {"candidate": None, "replay": None},
        }
        with self.assertRaises((ValueError, TypeError, KeyError)):
            checked(forged)


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "New opt-in sealed real Lean only")
class RealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = parent(ROOT)
        (OUT / ("real-project-" + uuid.uuid4().hex + ".json")).write_bytes(
            canonical(cls.project) + b"\n"
        )

    def setUp(self):
        self.area = OUT / ("real-" + uuid.uuid4().hex)
        self.area.mkdir()
        self.graphs = StructureResearchStore(ROOT)
        self.graphs.base = self.area / "graphs"
        self.graphs.base.mkdir()
        self.value = scenario()
        self.graph = create(self.graphs, self.project, self.value)
        self.key = self.graph["graph_id"]
        self.store = StructureReuseStore(ROOT, graphs=self.graphs, base=self.area / "reuse")

    def original(self, node):
        self.graphs.submit(self.key, confirmation(self.graphs, self.key, node))
        self.graphs.work_once(self.key)
        self.assertEqual(
            self.graphs.report(self.key)["node_states"][node]["math_verdict"], "PROVED"
        )

    def compose(self, raw, expect="PROVED"):
        row = submit(self.store, raw)
        self.store.work_once(row["run_id"])
        result = self.store.report(row["run_id"])
        (self.area / (row["run_id"] + ".json")).write_bytes(canonical(result) + b"\n")
        self.assertEqual(result["math_verdict"], expect, result)
        if expect == "PROVED":
            self.assertEqual(result["evidence_level"], "E4")
            self.assertNotEqual(
                result["result"]["candidate"]["pid"], result["result"]["replay"]["pid"]
            )
            self.assertTrue(result["result"]["replay"]["audit"]["type_matches"])
            self.assertEqual(len(result["result"]["candidate"]["imports"]), len(raw["sources"]))
        return result

    def test_multilevel_carriers_operations_nat_fold_import_and_persistent_withdrawal(self):
        self.original("power")
        b = self.compose(request(self.key))
        c = self.compose(request(self.key, "problem", "run." + b["run_id"]))
        self.assertEqual(c["plan"]["provenance"]["depth"], 2)
        self.assertEqual(len(c["result"]["candidate"]["imports"]), 2)
        self.assertIn("Nat.rec", c["plan"]["formal_statement"])
        self.assertIn("congrArg", c["plan"]["proof_source"])
        self.assertEqual(len(c["plan"]["target"]["assumptions"]), 8)
        self.assertEqual(self.graphs.report(self.key)["math_verdict"], "UNKNOWN")
        (self.area / "proved-chain.json").write_bytes(
            canonical({"lifted": b, "problem": c}) + b"\n"
        )
        self.graphs.withdraw(
            self.key,
            {
                "node_id": "power",
                "reviewer": "MRA062 QA",
                "reason": "Transitive real structure source withdrawal",
                "confirmed": True,
            },
        )
        self.assertTrue(self.store.report(c["run_id"])["invalidated"])
        self.assertTrue(self.store.read(b["run_id"])["invalidated"])
        self.original("power")
        self.assertTrue(self.store.report(c["run_id"])["invalidated"])
        (self.area / "withdrawn-chain.json").write_bytes(
            canonical(self.store.report(c["run_id"])) + b"\n"
        )

    def test_wrong_original_law_proof_really_rejected_and_damage_restore_never_revives(self):
        self.original("power")
        raw = request(self.key)
        raw["proof"]["proofs"][-1] = {"refl": call("h_one")}
        rejected = self.compose(raw, "UNKNOWN")
        self.assertEqual(rejected["state"], "REJECTED")
        self.assertIn("type mismatch", rejected["result"]["error"].lower())
        self.assertNotIn("unexpected token", rejected["result"]["error"].lower())
        self.assertIsNone(rejected["result"]["replay"])
        b = self.compose(request(self.key))
        graph = self.graphs.report(self.key)
        child = graph["checks"][graph["node_states"]["power"]["best_attempt"]]
        proof = self.graphs.checker(self.key).directory(child["run_id"]) / "replay/proof.ndjson"
        original = proof.read_bytes()
        proof.write_bytes(original + b"\n")
        self.assertTrue(self.store.report(b["run_id"])["invalidated"])
        proof.write_bytes(original)
        self.assertTrue(self.store.report(b["run_id"])["invalidated"])
        (self.area / "damage-restored-still-invalid.json").write_bytes(
            canonical(self.store.report(b["run_id"])) + b"\n"
        )

    def test_quantifier_and_fold_capture_avoidance_in_real_source_application(self):
        def formula(free, bound, index, acc):
            fold = {
                "nat_fold": {
                    "index": var(bound),
                    "zero": var(free),
                    "step": {"index": index, "value": acc, "body": var(acc)},
                }
            }
            return {
                "forall": {
                    "binders": [{"name": bound, "type": "Nat"}],
                    "body": binary("eq", fold, var(free)),
                }
            }

        def target(name, free, bound, index, acc):
            f = formula(free, bound, index, acc)
            return {
                "title": name,
                "original": name + "，保留完整全称条件",
                "variables": [{"name": free, "type": "Nat"}],
                "assumptions": [{"name": "all", "formula": f}],
                "goal": f,
                "proof": {"method": "grind"},
                "timeout_seconds": 30,
            }

        value = {
            "schema": "structure-project-research-v1",
            "carriers": [],
            "operations": [],
            "definitions": [],
            "laws": [],
            "nodes": [
                {
                    "id": "power",
                    "kind": "lemma",
                    "depends_on": [],
                    "target": target("量词与折叠完整条件源", "x", "y", "k", "acc"),
                },
                {
                    "id": "problem",
                    "kind": "problem",
                    "depends_on": ["power"],
                    "target": target("实例化避开折叠捕获", "k", "z", "j", "state"),
                },
            ],
        }
        self.graph = create(self.graphs, self.project, value)
        self.key = self.graph["graph_id"]
        self.original("power")
        raw = request(self.key)
        raw["node_id"] = "problem"
        raw["proof"] = {"lemma": "power", "terms": [var("k")], "proofs": [{"hyp": "all"}]}
        result = self.compose(raw)
        instantiated = result["plan"]["applications"][0]["instantiated_goal"]["forall"]
        self.assertNotEqual(instantiated["binders"][0]["name"], "k")
        fold = instantiated["body"]["eq"][0]["nat_fold"]
        self.assertEqual(fold["zero"], var("k"))
        self.assertNotEqual(fold["step"]["index"], "k")

    def test_cancel_after_actual_replay_and_supervisor_exit_never_publish(self):
        self.original("power")
        first = submit(self.store, request(self.key))
        original = NativeLeanBackend._run_worker

        def cancel(backend, *args, **kwargs):
            value = original(backend, *args, **kwargs)
            if kwargs.get("phase") == "REPLAY_RUNNING":
                self.assertEqual(value["returncode"], 0)
                self.store.cancel(first["run_id"])
            return value

        with patch.object(NativeLeanBackend, "_run_worker", cancel):
            self.store.work_once(first["run_id"])
        cancelled = self.store.report(first["run_id"])
        self.assertEqual((cancelled["state"], cancelled["math_verdict"]), ("CANCELLED", "UNKNOWN"))
        second = submit(self.store, request(self.key))
        script = """import os,sys
from pathlib import Path
from unittest.mock import patch
from mra.structure_research import StructureResearchStore
from mra.structure_reuse import StructureReuseStore
from mra.verifier import NativeLeanBackend
g=StructureResearchStore(Path.cwd());g.base=Path(sys.argv[1])
s=StructureReuseStore(Path.cwd(),graphs=g,base=Path(sys.argv[2]))
original=NativeLeanBackend._run_worker
def stop(self,*args,**kwargs):
 r=original(self,*args,**kwargs)
 if kwargs.get('phase')=='REPLAY_RUNNING':
  assert r['returncode']==0,r
  os._exit(73)
 return r
with patch.object(NativeLeanBackend,'_run_worker',stop):s.work_once(sys.argv[3])
"""
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(self.graphs.base),
                str(self.store.base),
                second["run_id"],
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        (self.area / "supervisor-stderr.log").write_text(process.stderr)
        self.assertEqual(process.returncode, 73, process.stderr)
        self.assertEqual(self.store.recover()["interrupted"], [second["run_id"]])
        self.assertEqual(self.store.work_once(second["run_id"])["worker_state"], "IDLE")
        interrupted = self.store.report(second["run_id"])
        self.assertEqual(
            (interrupted["state"], interrupted["math_verdict"]), ("INTERRUPTED", "UNKNOWN")
        )
        (self.area / "control-results.json").write_bytes(
            canonical({"cancelled": cancelled, "interrupted": interrupted}) + b"\n"
        )

    def test_actual_replay_final_commit_waits_for_graph_reader_once(self):
        self.original("power")
        row = submit(self.store, request(self.key))
        original_worker, original_gate = NativeLeanBackend._run_worker, self.store.gate
        replay_done, held = threading.Event(), threading.Event()
        checks, waits = [], []

        def worker(backend, *args, **kwargs):
            checks.append(kwargs["phase"])
            result = original_worker(backend, *args, **kwargs)
            if kwargs["phase"] == "REPLAY_RUNNING":
                self.assertEqual(result["returncode"], 0)
                replay_done.set()
            return result

        def reader():
            with lease(self.graphs.directory(self.key)):
                held.set()
                threading.Event().wait(0.35)

        thread = threading.Thread(target=reader)

        def gate(current, check_files=True):
            if replay_done.is_set() and not waits:
                waits.append("FINAL_GATE_AFTER_REAL_REPLAY")
                thread.start()
                self.assertTrue(held.wait(5))
            return original_gate(current, check_files)

        try:
            with (
                patch.object(NativeLeanBackend, "_run_worker", worker),
                patch.object(self.store, "gate", gate),
            ):
                self.store.work_once(row["run_id"])
        finally:
            if thread.ident:
                thread.join(5)
        value = self.store.report(row["run_id"])
        self.assertEqual((value["state"], value["math_verdict"]), ("COMPLETED", "PROVED"), value)
        self.assertEqual(checks, ["LEAN_RUNNING", "REPLAY_RUNNING"])
        self.assertEqual(waits, ["FINAL_GATE_AFTER_REAL_REPLAY"])
        self.assertEqual(self.store.recover()["interrupted"], [])
        (self.area / "reader-contention.json").write_bytes(canonical(value) + b"\n")
