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
from mra.typed_composition_terms import MODE, compile_composition, substitute
from mra.typed_research import SOURCES, TypedResearchStore
from mra.typed_reuse import FILES, TypedReuseStore
from mra.verifier import NativeLeanBackend
from test_search_evaluation import copy_sources
from test_typed_research import confirmation, create, parent

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/typed-proof-reuse-20261028-01"


def var(name):
    return {"var": name}


def binary(op, a, b):
    return {op: [a, b]}


def call(name, *args):
    return {"call": {"name": name, "args": list(args)}}


def app(alias, terms, *proofs):
    return {"lemma": alias, "terms": terms, "proofs": list(proofs)}


def scenario():
    n, q, x, y, z, w = (var(s) for s in ("n", "q", "x", "y", "z", "w"))
    lift = call("lift", n)

    def eq(a, b):
        return binary("eq", a, b)

    def square(a):
        return call("square", a)

    def plus(a):
        return binary("add", a, a)

    defs = [
        {
            "name": "lift",
            "original": "自然数经整数显式转换到有理数。",
            "parameters": [{"name": "n", "type": "Nat"}],
            "result_type": "Rat",
            "body": {"cast": {"to": "Rat", "value": {"cast": {"to": "Int", "value": n}}}},
        },
        {
            "name": "square",
            "original": "有理数平方。",
            "parameters": [{"name": "x", "type": "Rat"}],
            "result_type": "Rat",
            "body": binary("mul", x, x),
        },
    ]

    def target(title, variables, hypotheses, goal):
        return {
            "title": title,
            "original": title + "；保留全部原条件。",
            "variables": [{"name": k, "type": t} for k, t in variables],
            "assumptions": [{"name": k, "formula": f} for k, f in hypotheses],
            "goal": goal,
            "strategy": "grind",
            "timeout_seconds": 30,
        }

    a = target(
        "相等有理数的平方相等",
        [("x", "Rat"), ("y", "Rat")],
        [("equal", eq(x, y))],
        eq(square(x), square(y)),
    )
    d = target(
        "相等整数的两倍相等",
        [("z", "Int"), ("w", "Int")],
        [("equal", eq(z, w))],
        eq(plus(z), plus(w)),
    )
    b = target(
        "有理数与自然数嵌入的平方",
        [("q", "Rat"), ("n", "Nat")],
        [("step", eq(q, lift))],
        eq(square(q), square(lift)),
    )
    c = target(
        "原问题保留两种数域的条件",
        [("n", "Nat"), ("q", "Rat"), ("z", "Int"), ("w", "Int")],
        [("hq", eq(q, lift)), ("hz", eq(z, w))],
        binary("and", b["goal"], d["goal"]),
    )
    return {
        "definitions": defs,
        "nodes": [
            {"id": "a", "kind": "lemma", "depends_on": [], "target": a},
            {"id": "d", "kind": "lemma", "depends_on": [], "target": d},
            {"id": "b", "kind": "lemma", "depends_on": ["a"], "target": b},
            {"id": "problem", "kind": "problem", "depends_on": ["b", "d"], "target": c},
        ],
    }


def request(graph_id, node="b", source=None):
    return {
        "schema": MODE,
        "graph_id": graph_id,
        "node_id": node,
        "sources": [{"alias": "a", "item_id": source or f"node.{graph_id}.a"}],
        "proof": app("a", [var("q"), call("lift", var("n"))], {"hyp": "step"}),
        "timeout_seconds": 30,
    }


def full_target(value, node):
    return {
        "schema": "typed-definitions-v1",
        "definitions": value["definitions"],
        **next(n["target"] for n in value["nodes"] if n["id"] == node),
    }


def descriptor(value, graph_id, node="a", alias="a"):
    from mra.typed_terms import compile_request

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
                "archive_fingerprint": "unit-only-no-proof",
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
            "reviewer": "MRA056 authored QA; not independent semantic review",
            "confirmed": True,
        }
    )


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_sources(self.root)
        for name in (*FILES, *IMPORT_FILES, *SOURCES):
            shutil.copyfile(ROOT / name, self.root / name)
        self.graphs = TypedResearchStore(self.root)
        self.value = scenario()
        self.graph = create(self.graphs, parent(self.root), self.value)
        self.key = self.graph["graph_id"]
        self.store = TypedReuseStore(self.root, graphs=self.graphs)
        self.raw = request(self.key)
        self.source = descriptor(self.value, self.key)

    def compile(self, raw=None, source=None):
        return compile_composition(
            raw or self.raw, full_target(self.value, "b"), [source or self.source]
        )

    def test_complete_target_conditions_definitions_and_let_scope(self):
        p = self.compile()
        self.assertEqual(p["target"], full_target(self.value, "b"))
        self.assertTrue(p["proof_source"].startswith("let mra_d0"))
        self.assertIn("MraCandidate.Reused.s0.checked", p["proof_source"])
        self.assertIn("mra_d0 mra_v1", p["proof_source"])
        self.assertEqual(p["applications"][0]["premise_proofs"], [{"hyp": "step"}])
        self.assertEqual(
            p["applications"][0]["instantiated_assumptions"][0]["formula"],
            full_target(self.value, "b")["assumptions"][0]["formula"],
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
                raw["proof"] = {"refl": var("q")}
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
                self.assertEqual(plan["target"], full_target(self.value, "b"))

    def test_wrong_graph_prerequisite_and_missing_source_are_rejected(self):
        for change in ({"graph_id": "typedflow-" + "a" * 32}, {"node_id": "d"}):
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
                self.graphs.report(self.key)["node_states"]["b"]["math_verdict"], "UNKNOWN"
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
            return store.context(self.key, "b")[0]

        try:
            with patch.object(LibraryCompositionStore, "execute", once):
                result = self.store.execute("mathlib-" + "a" * 32, None)
        finally:
            thread.join(5)
        self.assertEqual(result["node_id"], "b")
        self.assertEqual(len(calls), 1)
        with lease(self.graphs.directory(self.key)), self.assertRaises(WorkerBusy):
            self.store.context(self.key, "b")


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
        self.graphs = TypedResearchStore(ROOT)
        self.graphs.base = self.area / "graphs"
        self.graphs.base.mkdir()
        self.value = scenario()
        self.graph = create(self.graphs, self.project, self.value)
        self.key = self.graph["graph_id"]
        self.store = TypedReuseStore(ROOT, graphs=self.graphs, base=self.area / "reuse")

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

    def test_multilevel_typed_let_arguments_conditions_namespaces_and_withdrawal(self):
        self.original("a")
        self.original("d")
        b = self.compose(request(self.key))
        raw = request(self.key, "problem", "run." + b["run_id"])
        raw["sources"].append({"alias": "d", "item_id": f"node.{self.key}.d"})
        raw["proof"] = {
            "pair": [
                app("a", [var("q"), var("n")], {"hyp": "hq"}),
                app("d", [var("z"), var("w")], {"hyp": "hz"}),
            ]
        }
        c = self.compose(raw)
        self.assertEqual(c["plan"]["provenance"]["depth"], 2)
        self.assertEqual(len(c["plan"]["provenance"]["ancestors"]), 3)
        self.assertEqual(self.graphs.report(self.key)["math_verdict"], "UNKNOWN")
        (self.area / "proved-chain.json").write_bytes(canonical({"b": b, "problem": c}) + b"\n")
        self.graphs.withdraw(
            self.key,
            {
                "node_id": "a",
                "reviewer": "QA",
                "reason": "real transitive withdrawal",
                "confirmed": True,
            },
        )
        after = self.store.report(c["run_id"])
        self.assertTrue(after["invalidated"])
        self.assertTrue(self.store.read(b["run_id"])["invalidated"])
        self.original("a")
        self.assertTrue(self.store.report(c["run_id"])["invalidated"])
        (self.area / "withdrawn-chain.json").write_bytes(canonical(after) + b"\n")

    def test_wrong_premise_really_reaches_lean_and_damage_restore_never_revives(self):
        self.original("a")
        wrong = request(self.key)
        wrong["proof"]["proofs"] = [{"refl": var("q")}]
        rejected = self.compose(wrong, "UNKNOWN")
        self.assertEqual(rejected["state"], "REJECTED")
        self.assertIsNotNone(rejected["result"]["candidate"]["pid"])
        self.assertIsNone(rejected["result"]["replay"])
        b = self.compose(request(self.key))
        graph = self.graphs.report(self.key)
        child = graph["checks"][graph["node_states"]["a"]["best_attempt"]]
        proof = self.graphs.checker(self.key).directory(child["run_id"]) / "replay/proof.ndjson"
        original = proof.read_bytes()
        proof.write_bytes(original + b"\n")
        self.assertTrue(self.store.report(b["run_id"])["invalidated"])
        proof.write_bytes(original)
        self.assertTrue(self.store.report(b["run_id"])["invalidated"])
        (self.area / "damage-restored-still-invalid.json").write_bytes(
            canonical(self.store.report(b["run_id"])) + b"\n"
        )

    def test_quantifier_capture_is_avoided_in_actual_import_and_premise_application(self):
        def formula(free, bound):
            return {
                "forall": {
                    "binders": [{"name": bound, "type": "Nat"}],
                    "body": binary(
                        "eq",
                        binary("add", var(free), var(bound)),
                        binary("add", var(bound), var(free)),
                    ),
                }
            }

        def target(name, free, bound):
            f = formula(free, bound)
            return {
                "title": name,
                "original": name,
                "variables": [{"name": free, "type": "Nat"}],
                "assumptions": [{"name": "all", "formula": f}],
                "goal": f,
                "strategy": "grind",
                "timeout_seconds": 30,
            }

        value = {
            "definitions": [],
            "nodes": [
                {
                    "id": "a",
                    "kind": "lemma",
                    "depends_on": [],
                    "target": target("量词源", "x", "y"),
                },
                {
                    "id": "problem",
                    "kind": "problem",
                    "depends_on": ["a"],
                    "target": target("避免捕获", "y", "z"),
                },
            ],
        }
        self.graph = create(self.graphs, self.project, value)
        self.key = self.graph["graph_id"]
        self.original("a")
        raw = request(self.key, "problem")
        raw["proof"] = app("a", [var("y")], {"hyp": "all"})
        result = self.compose(raw)
        bound = result["plan"]["applications"][0]["instantiated_goal"]["forall"]["binders"][0][
            "name"
        ]
        self.assertNotEqual(bound, "y")

    def test_cancel_after_actual_replay_and_supervisor_exit_never_publish(self):
        self.original("a")
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
from mra.typed_research import TypedResearchStore
from mra.typed_reuse import TypedReuseStore
from mra.verifier import NativeLeanBackend
g=TypedResearchStore(Path.cwd());g.base=Path(sys.argv[1])
s=TypedReuseStore(Path.cwd(),graphs=g,base=Path(sys.argv[2]))
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
        self.original("a")
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
