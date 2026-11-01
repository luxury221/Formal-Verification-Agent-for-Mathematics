"""Unknown-only protocol contracts, adversarial forged claims and real structure graphs."""

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
from mra.research import ResearchStore
from mra.research_flow import lease
from mra.structure_research import (
    SOURCES,
    StructureGraphChecks,
    StructureResearchStore,
    source_identity,
)
from mra.structure_research_examples import example
from mra.structures import FILES
from mra.tasks import WorkerBusy
from mra.typed_reuse import checked
from mra.verifier import NativeLeanBackend
from test_search_evaluation import copy_sources

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/structure-research-20261029-01"


def rid():
    return "structure-qa-" + uuid.uuid4().hex


def parent(root):
    return ResearchStore(root).create(
        {
            "request_id": rid(),
            "title": "同态保持自然数幂的项目研究",
            "question": "在显式的两幺半群及同态全部定律下，验证单位元、乘积与同态保持自然数幂。",
        }
    )


def create(store, project, value=None):
    raw = {"project_id": project["project_id"], "branch_id": "main", **deepcopy(value or example())}
    plan = store.preview(raw)
    return store.create(
        {"request_id": rid(), "request": raw, "plan_fingerprint": plan["plan_fingerprint"]}
    )


def confirmation(store, key, node="unit"):
    value = store.preview_node(key, {"node_id": node})
    return {
        "request_id": rid(),
        "node_id": node,
        "execution_fingerprint": value["execution_fingerprint"],
        "reviewer": "MRA060 complete shared laws alignment QA",
        "confirmed": True,
    }


def one_node():
    raw = example()
    raw["nodes"] = [{**raw["nodes"][-1], "depends_on": []}]
    return raw


class ContractTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        copy_sources(self.root)
        for n in set(SOURCES) | set(FILES):
            target = self.root / n
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / n, target)
        self.project = parent(self.root)
        self.store = StructureResearchStore(self.root)
        self.value = create(self.store, self.project)
        self.key = self.value["graph_id"]

    def test_shared_environment_all_laws_full_targets_and_proof_programs_are_preserved(self):
        raw = self.value["plan"]["request"]
        before = deepcopy(raw)
        with patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")):
            value = self.store.preview(raw)
        self.assertEqual(raw, before)
        self.assertEqual(value["order"], ["unit", "product", "problem"])
        for node in raw["nodes"]:
            child = value["node_plans"][node["id"]]
            for n in ("carriers", "operations", "definitions"):
                self.assertEqual(child["request"][n], raw[n])
            self.assertEqual(
                child["request"]["assumptions"], [*raw["laws"], *node["target"]["assumptions"]]
            )
            self.assertEqual(child["request"]["goal"], node["target"]["goal"])
            self.assertEqual(child["request"]["proof"], node["target"]["proof"])
            self.assertIn("Nat.rec", child["formal_statement"])
            self.assertEqual((child["math_verdict"], child["evidence_level"]), ("UNKNOWN", "E0"))
        self.assertEqual(self.value["math_verdict"], "UNKNOWN")
        self.assertEqual(self.value["node_states"]["problem"]["blocked_by"], ["product"])
        value["request"]["laws"].clear()
        self.assertEqual(len(self.store.report(self.key)["plan"]["request"]["laws"]), 8)

    def test_graph_cycles_missing_orphans_duplicates_and_extra_shared_overrides_rejected(self):
        cases = []
        for change in ("cycle", "missing", "orphan", "problem", "override", "duplicate"):
            raw = example()
            if change == "cycle":
                raw["nodes"][0]["depends_on"] = ["problem"]
            elif change == "missing":
                raw["nodes"][-1]["depends_on"] = ["missing"]
            elif change == "orphan":
                raw["nodes"][-1]["depends_on"] = []
            elif change == "problem":
                raw["nodes"][0]["kind"] = "problem"
            elif change == "override":
                raw["nodes"][0]["target"]["carriers"] = []
            else:
                raw["nodes"][1]["depends_on"] = ["unit", "unit"]
            cases.append(raw)
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.store.preview(
                    {"project_id": self.project["project_id"], "branch_id": "main", **raw}
                )

    def test_shared_laws_cannot_capture_node_variables_and_conditions_cannot_shadow_laws(self):
        raw = example()
        raw["laws"] = [{"name": "captured", "formula": {"eq": [{"var": "x"}, {"var": "x"}]}}]
        with self.assertRaises(ValueError):
            create(self.store, self.project, raw)
        raw = example()
        del raw["laws"][4]  # Keep eight total premises so duplicate-name validation is reached.
        raw["nodes"][0]["target"]["assumptions"] = [deepcopy(raw["laws"][0])]
        with self.assertRaises(ValueError):
            create(self.store, self.project, raw)
        raw = example()
        del raw["laws"][0]  # g_assoc is not referenced by these programs.
        plan = self.store.preview(
            {"project_id": self.project["project_id"], "branch_id": "main", **raw}
        )
        self.assertNotEqual(plan["plan_fingerprint"], self.value["plan"]["plan_fingerprint"])
        self.assertEqual(len(plan["node_plans"]["problem"]["assumptions"]), 7)

    def test_schema_input_and_closed_environment_type_boundaries(self):
        for changes in ({"schema": "typed-project-research-v1"}, {"extra": 1}, {"nodes": []}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                create(self.store, self.project, {**example(), **changes})
        raw = example()
        raw["operations"][-1]["result_type"] = "missing"
        with self.assertRaises(ValueError):
            create(self.store, self.project, raw)

    def test_blocked_node_and_named_confirmation_gates(self):
        with self.assertRaises(ValueError):
            confirmation(self.store, self.key, "problem")
        raw = confirmation(self.store, self.key)
        for changes in (
            {"confirmed": 1},
            {"reviewer": " "},
            {"execution_fingerprint": "stale"},
            {"node_id": "product"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.submit(self.key, {**raw, **changes})
        self.assertEqual(self.store.read(self.key)["attempts"], [])

    def test_idempotence_changed_body_cancel_and_no_automatic_requeue(self):
        raw = confirmation(self.store, self.key)
        first = self.store.submit(self.key, raw)
        self.assertEqual(self.store.submit(self.key, raw), first)
        self.store.cancel(self.key, {"attempt_id": first["attempt_id"]})
        self.assertEqual(self.store.submit(self.key, raw)["state"], "CANCELLED")
        with self.assertRaises(ValueError):
            self.store.submit(self.key, {**raw, "reviewer": "other"})
        plan = self.value["plan"]
        self.assertEqual(
            self.store.create(
                {
                    "request_id": self.value["request_id"],
                    "request": plan["request"],
                    "plan_fingerprint": plan["plan_fingerprint"],
                }
            )["graph_id"],
            self.key,
        )
        self.assertEqual(self.store.work_once(self.key)["worker_state"], "IDLE")

    def test_forged_success_without_real_candidate_replay_is_rejected_not_used_as_fixture(self):
        attempt = self.store.submit(self.key, confirmation(self.store, self.key))
        checker = self.store.checker(self.key)
        with checker.transaction() as db:
            child = checker.read(attempt["child_id"], db)
            child.update(state="COMPLETED", math_verdict="PROVED", evidence_level="E4", result=None)
            checker.save(db, child)
        row = self.store.read(self.key)
        row["attempts"][0].update(state="COMPLETED", source_fingerprint=source_identity(child))
        self.store.save(row)
        value = self.store.report(self.key)
        self.assertEqual(value["math_verdict"], "UNKNOWN")
        self.assertEqual(value["node_states"]["unit"]["math_verdict"], "UNKNOWN")
        self.assertTrue(value["attempts"][0]["invalidated"])

    def test_implementation_and_graph_damage_restoration_do_not_revive(self):
        p = self.root / "mra/structured_proofs.py"
        old = p.read_bytes()
        p.write_bytes(old + b"\n")
        self.assertTrue(self.store.report(self.key)["invalidated"])
        p.write_bytes(old)
        self.assertTrue(self.store.report(self.key)["invalidated"])
        other = create(self.store, self.project)
        p = self.store.directory(other["graph_id"]) / "record.json"
        old = p.read_bytes()
        p.write_text("broken")
        with self.assertRaises(ValueError):
            self.store.report(other["graph_id"])
        p.write_bytes(old)
        self.assertTrue(self.store.report(other["graph_id"])["invalidated"])

    def test_parent_context_changes_are_bound_without_editing_old_nodes(self):
        before = self.store.parent(self.project["project_id"], "main")
        with self.store.research.transaction() as db:
            row = ResearchStore.read(db, self.project["project_id"])
            row["question"] += "修订"
            self.store.research.save(db, row)
        self.assertNotEqual(before, self.store.parent(self.project["project_id"], "main"))
        self.assertTrue(self.store.report(self.key)["invalidated"])

    def test_global_lock_restart_and_orphan_checks_remain_unknown(self):
        attempt = self.store.submit(self.key, confirmation(self.store, self.key))
        with self.store.tasks.worker_lock(), self.assertRaises(WorkerBusy):
            self.store.work_once(self.key)
        row = self.store.read(self.key)
        row["attempts"][0]["state"] = "RUNNING"
        self.store.save(row)
        child = self.value["plan"]["node_plans"]["unit"]
        orphan = self.store.checker(self.key).submit(
            {
                "request_id": rid(),
                "request": child["request"],
                "plan_fingerprint": child["plan_fingerprint"],
                "reviewer": "orphan",
                "confirmed": True,
            }
        )
        with patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")):
            self.assertIn(attempt["attempt_id"], self.store.recover()["interrupted"])
        for key in (orphan["run_id"], attempt["child_id"]):
            self.assertEqual(self.store.checker(self.key).report(key)["state"], "CANCELLED")
        self.assertEqual(self.store.work_once(self.key)["worker_state"], "IDLE")
        self.assertEqual(self.store.report(self.key)["math_verdict"], "UNKNOWN")

    def test_cancelled_attempts_reserve_full_budget_and_seventeenth_is_rejected(self):
        for _ in range(16):
            attempt = self.store.submit(self.key, confirmation(self.store, self.key))
            self.store.cancel(self.key, {"attempt_id": attempt["attempt_id"]})
        value = self.store.report(self.key)
        self.assertEqual((value["checks_reserved"], value["worker_seconds_reserved"]), (16, 480))
        with self.assertRaises(ValueError):
            self.store.submit(self.key, confirmation(self.store, self.key))

    def test_concurrent_submit_permits_one_active_attempt(self):
        values, errors = [], []
        inputs = [confirmation(self.store, self.key) for _ in range(2)]
        barrier = threading.Barrier(2)

        def send(raw):
            barrier.wait(10)
            try:
                values.append(self.store.submit(self.key, raw))
            except (ValueError, WorkerBusy) as error:
                errors.append(str(error))

        threads = [threading.Thread(target=send, args=(raw,)) for raw in inputs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)
            self.assertFalse(thread.is_alive())
        self.assertEqual((len(values), len(errors)), (1, 1))
        self.store.cancel(self.key, {"attempt_id": values[0]["attempt_id"]})

    def test_idle_recovery_does_not_reaudit_or_rewrite(self):
        p = self.store.directory(self.key) / "record.json"
        before = p.stat().st_mtime_ns
        with patch.object(self.store, "_audit", side_effect=AssertionError("No idle math audit")):
            self.assertEqual(self.store.recover()["interrupted"], [])
        self.assertEqual(p.stat().st_mtime_ns, before)

    def test_storage_paths_and_child_ledger_redirection_are_rejected(self):
        with self.assertRaises(ValueError):
            StructureResearchStore(self.root, base=self.root / "outside")
        p = self.root / "artifacts/redirected"
        p.symlink_to(self.store.base, target_is_directory=True)
        with self.assertRaises(ValueError):
            StructureResearchStore(self.root, base=p)
        with self.assertRaises(ValueError):
            self.store.report("../escape")
        with self.assertRaises(ValueError):
            StructureGraphChecks(self.root, self.root / "outside", create=True)


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "New project-bound structure and induction checks"
)
class RealTests(unittest.TestCase):
    def setUp(self):
        self.area = OUT / ("real-" + uuid.uuid4().hex)
        self.area.mkdir(parents=True)
        self.project = parent(ROOT)
        self.store = StructureResearchStore(ROOT, base=self.area / "graphs")
        (self.area / "fixture.json").write_bytes(
            canonical({"project_id": self.project["project_id"], "base": str(self.store.base)})
            + b"\n"
        )

    def run_node(self, key, node):
        attempt = self.store.submit(key, confirmation(self.store, key, node))
        self.store.work_once(key)
        value = self.store.report(key)
        (self.area / (attempt["attempt_id"] + ".json")).write_bytes(canonical(value) + b"\n")
        return value, attempt

    def test_complete_homomorphism_graph_original_induction_withdrawal_and_stale_identity(self):
        key = create(self.store, self.project)["graph_id"]
        first, _ = self.run_node(key, "unit")
        self.assertEqual(first["math_verdict"], "UNKNOWN")
        stale = confirmation(self.store, key, "product")
        middle, _ = self.run_node(key, "product")
        self.assertEqual(middle["math_verdict"], "UNKNOWN")
        full, _ = self.run_node(key, "problem")
        self.assertEqual((full["math_verdict"], full["evidence_level"]), ("PROVED", "E4"))
        for child in full["checks"].values():
            checked(child)
            self.assertEqual(len(child["plan"]["request"]["assumptions"]), 8)
            self.assertEqual(
                child["plan"]["request"]["definitions"], full["plan"]["request"]["definitions"]
            )
        self.store.withdraw(
            key,
            {
                "node_id": "unit",
                "reviewer": "QA",
                "reason": "Real precursor withdrawal",
                "confirmed": True,
            },
        )
        revoked = self.store.report(key)
        self.assertTrue(all(a["invalidated"] for a in revoked["attempts"]))
        self.assertEqual(revoked["math_verdict"], "UNKNOWN")
        replacement, _ = self.run_node(key, "unit")
        self.assertEqual(replacement["math_verdict"], "UNKNOWN")
        self.assertTrue(replacement["attempts"][2]["invalidated"])
        with self.assertRaises(ValueError):
            self.store.submit(key, stale)
        (self.area / "summary.json").write_bytes(
            canonical(
                {
                    "all_passed": True,
                    "graph_id": key,
                    "original_had_complete_e4": True,
                    "replacement_did_not_revive": True,
                }
            )
            + b"\n"
        )

    def test_true_precursor_cannot_prove_false_original_and_restored_proof_stays_invalid(self):
        raw = example()
        raw["nodes"] = [raw["nodes"][0], {**raw["nodes"][-1], "depends_on": ["unit"]}]
        raw["nodes"][-1]["target"]["goal"] = {
            "eq": [{"var": "x"}, {"call": {"name": "g_one", "args": []}}]
        }
        raw["nodes"][-1]["target"]["proof"] = {"method": "rfl"}
        key = create(self.store, self.project, raw)["graph_id"]
        unit, first = self.run_node(key, "unit")
        checked(unit["checks"][first["attempt_id"]])
        rejected, bad = self.run_node(key, "problem")
        self.assertEqual(
            (rejected["math_verdict"], rejected["attempts"][-1]["state"]), ("UNKNOWN", "REJECTED")
        )
        result = rejected["checks"][bad["attempt_id"]]["result"]
        self.assertIsNone(result["replay"])
        self.assertNotIn("unexpected", result["error"])
        child = self.store.checker(key)
        p = child.directory(first["child_id"]) / "replay/proof.ndjson"
        before = p.read_bytes()
        p.write_bytes(before + b"\n")
        self.assertEqual(self.store.report(key)["node_states"]["unit"]["math_verdict"], "UNKNOWN")
        p.write_bytes(before)
        self.assertTrue(self.store.report(key)["attempts"][0]["invalidated"])

    def test_replay_cancel_parent_commit_exit_and_report_contention_are_conservative(self):
        key = create(self.store, self.project, one_node())["graph_id"]
        attempt = self.store.submit(key, confirmation(self.store, key, "problem"))
        original = NativeLeanBackend._run_worker

        def cancel(backend, *args, **kwargs):
            value = original(backend, *args, **kwargs)
            if kwargs["phase"] == "REPLAY_RUNNING" and value["returncode"] == 0:
                self.store.cancel(key, {"attempt_id": attempt["attempt_id"]})
            return value

        with patch.object(NativeLeanBackend, "_run_worker", cancel):
            self.store.work_once(key)
        value = self.store.report(key)
        self.assertEqual(
            (value["math_verdict"], value["attempts"][0]["state"]), ("UNKNOWN", "CANCELLED")
        )
        self.assertEqual(
            value["checks"][attempt["attempt_id"]]["result"]["replay"]["returncode"], 0
        )
        (self.area / "cancelled.json").write_bytes(canonical(value) + b"\n")
        key = create(self.store, self.project, one_node())["graph_id"]
        attempt = self.store.submit(key, confirmation(self.store, key, "problem"))
        script = """import os,sys
from pathlib import Path
from unittest.mock import patch
from mra.domain import canonical
from mra.structure_research import StructureResearchStore,StructureGraphChecks
store=StructureResearchStore(Path.cwd(),base=Path(sys.argv[1]))
original=StructureGraphChecks.execute
def exit_after_child(self,*args,**kwargs):
 value=original(self,*args,**kwargs)
 assert value['math_verdict']=='PROVED',value
 Path(sys.argv[3]).write_bytes(canonical(value)+b'\\n')
 os._exit(73)
with patch.object(StructureGraphChecks,'execute',exit_after_child):store.work_once(sys.argv[2])
"""
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(self.store.base),
                key,
                str(self.area / "child-before-parent-exit.json"),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        (self.area / "exit-stdout.log").write_text(process.stdout)
        (self.area / "exit-stderr.log").write_text(process.stderr)
        self.assertEqual(process.returncode, 73, process.stderr)
        self.assertIn(attempt["attempt_id"], self.store.recover()["interrupted"])
        value = self.store.report(key)
        self.assertEqual(
            (value["math_verdict"], value["attempts"][0]["state"]), ("UNKNOWN", "INTERRUPTED")
        )
        checked(value["checks"][attempt["attempt_id"]])
        self.assertEqual(self.store.work_once(key)["worker_state"], "IDLE")
        (self.area / "interrupted.json").write_bytes(canonical(value) + b"\n")
        key = create(self.store, self.project, one_node())["graph_id"]
        self.store.submit(key, confirmation(self.store, key, "problem"))
        held = threading.Event()

        def reader():
            with lease(self.store.directory(key)):
                held.set()
                threading.Event().wait(0.35)

        thread = threading.Thread(target=reader)
        original_check = StructureGraphChecks.execute

        def hold(checker, *args, **kwargs):
            value = original_check(checker, *args, **kwargs)
            checked(value)
            thread.start()
            self.assertTrue(held.wait(5))
            return value

        try:
            with patch.object(StructureGraphChecks, "execute", hold):
                self.store.work_once(key)
        finally:
            if thread.ident:
                thread.join(5)
        value = self.store.report(key)
        self.assertEqual(
            (value["math_verdict"], value["attempts"][0]["state"]), ("PROVED", "COMPLETED")
        )
        (self.area / "reader-contention.json").write_bytes(canonical(value) + b"\n")
