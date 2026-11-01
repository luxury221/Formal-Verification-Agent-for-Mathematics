"""Opt-in complete PDE graphs and actual publication faults; no substituted checker results."""

from copy import deepcopy
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from mra.domain import canonical
from mra.http_api import WorkbenchServer, TaskPump
from mra.integrity import ContentSnapshot
from mra.pde_language_lab import PDELanguageStore
from mra import pde_language_runtime as runtime
from mra.pde_research import PDEResearchStore
from mra.pde_research_examples import examples
from mra.analysis_examples import op, r

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "opt-in actual PDE graph and worker acceptance"
)
class RealTests(unittest.TestCase):
    def test_complete_graph_actual_imports_and_publication_lifecycle(self):
        out = ROOT / "artifacts/pde-research-20261029-01" / ("real-" + uuid.uuid4().hex)
        out.mkdir()

        def write(name, value):
            (out / name).write_bytes(canonical(value) + b"\n")

        progress = []

        def mark(label, **details):
            progress.append({"label": label, "at": time.time(), **details})
            write("progress.json", progress)

        cg = Path("/sys/fs/cgroup") / Path("/proc/self/cgroup").read_text().strip().split(":")[
            -1
        ].lstrip("/")
        resources = {n: (cg / n).read_text().strip() for n in ("memory.max", "memory.swap.max")}
        self.assertEqual(
            resources, {"memory.max": str(6 * 1024**3), "memory.swap.max": str(512 * 1024**2)}
        )
        write("resources.json", resources)
        write(
            "declared-plan.json",
            {
                "full_chain_nodes": ["ibp", "green", "test", "energy"],
                "false_original": True,
                "real_success_then_cancel": True,
                "real_success_then_supervisor_exit": 71,
                "parent_context_change": True,
                "proof_and_graph_record_restoration": True,
                "named_withdrawal": True,
                "maximum_candidate_attempts": 9,
                "automatic_retries": 0,
                "model_calls": 0,
                "known_author_visible_targets": True,
                "general_weak_PDE": False,
            },
        )
        legacy = ContentSnapshot.load_runtime(ROOT)
        legacy.verify()
        runtime.load(ROOT)
        store = PDEResearchStore(ROOT, base=out / "pde-research-private")
        server = WorkbenchServer(ROOT, 0, start_worker=False)
        server.pde_research = store
        server.pump = TaskPump(server.store, pde_research=store)
        server.pump.thread.start()
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 10)
        self.addCleanup(server.shutdown)
        calls = []

        def http(method, route, data=None):
            connection = HTTPConnection(*server.server_address, timeout=1800)
            headers = (
                {
                    "Origin": server.origin,
                    "X-MRA-CSRF": server.csrf,
                    "Content-Type": "application/json",
                }
                if method == "POST"
                else {}
            )
            try:
                connection.request(
                    method,
                    route,
                    body=canonical(data) if data is not None else None,
                    headers=headers,
                )
                response = connection.getresponse()
                value = json.loads(response.read())
                calls.append({"method": method, "route": route, "status": response.status})
                write("http-calls.json", calls)
                if response.status != 503:
                    self.assertLess(response.status, 400, value)
                return response.status, value
            finally:
                connection.close()

        prefix = "/v1/pde-research"
        _, parent = http(
            "POST",
            "/v1/research",
            {
                "request_id": "actual-pde-" + uuid.uuid4().hex,
                "title": "MRA088 实际 PDE 图验收",
                "question": "保留经典 Poisson 全条件，逐节点实际导入来源并独立重放；检查失败、取消、监督中断和证据失效。",
            },
        )
        write("parent.json", parent)

        def request(nodes):
            return {
                "project_id": parent["project_id"],
                "branch_id": "main",
                "nodes": deepcopy(nodes),
            }

        def save(nodes, label):
            _, plan = http("POST", prefix + "/preview", request(nodes))
            _, row = http(
                "POST",
                prefix,
                {
                    "request_id": label + "-" + uuid.uuid4().hex,
                    "request": plan["request"],
                    "plan_fingerprint": plan["plan_fingerprint"],
                },
            )
            mark("graph-saved", case=label, graph_id=row["graph_id"])
            return row["graph_id"]

        def submit(key, node):
            mark("preview-start", graph_id=key, node=node)
            _, preview = http("POST", prefix + "/" + key + "/node-preview", {"node_id": node})
            _, attempt = http(
                "POST",
                prefix + "/" + key + "/submit",
                {
                    "request_id": "actual-check-" + uuid.uuid4().hex,
                    "node_id": node,
                    "execution_fingerprint": preview["execution_fingerprint"],
                    "reviewer": "Codex author review of full classical PDE target; not independent expert review",
                    "confirmed": True,
                },
            )
            mark("submitted", graph_id=key, node=node, attempt_id=attempt["attempt_id"])
            return attempt

        observations = []

        def terminal(key, attempt):
            end = time.monotonic() + 3600
            while time.monotonic() < end:
                status, progress = http("GET", prefix + "/" + key + "/progress")
                if status == 503:
                    time.sleep(0.5)
                    continue
                self.assertFalse(progress["evidence_checked"])
                current = next(
                    a for a in progress["attempts"] if a["attempt_id"] == attempt["attempt_id"]
                )
                if current["state"] not in ("QUEUED", "RUNNING"):
                    report_status, row = http("GET", prefix + "/" + key)
                    if report_status == 503:
                        time.sleep(0.5)
                        continue
                    current = next(
                        a for a in row["attempts"] if a["attempt_id"] == attempt["attempt_id"]
                    )
                    observations.append({"graph_id": key, "attempt": current, "report": row})
                    write("observations.json", observations)
                    mark(
                        "terminal",
                        graph_id=key,
                        node=current["node_id"],
                        state=current["state"],
                        math_verdict=row["node_states"][current["node_id"]]["math_verdict"],
                    )
                    return row
                time.sleep(0.5)
            self.fail(
                "Actual graph attempt exceeded declared observation bound; no automatic restart"
            )

        # Bind an existing watched proof explicitly; this is test data, never a product default.
        seed = "pdewatched-18a1d58bfcd746e1bd2a22fded0b4e6d"
        _, descriptor = http("POST", prefix + "/source-preview", {"run_id": seed})
        write("seed-source.json", descriptor)
        chain = deepcopy(examples()[1]["nodes"])
        chain[0]["target"]["sources"] = [
            {"name": "ibp", "run_id": seed, "type_fingerprint": descriptor["type_fingerprint"]}
        ]
        full_key = save(chain, "complete-energy-chain")
        mark("full-chain-started", graph_id=full_key)
        for node in ("ibp", "green", "test", "energy"):
            final = terminal(full_key, submit(full_key, node))
            self.assertEqual(final["node_states"][node]["math_verdict"], "PROVED")
            self.assertEqual(final["math_verdict"], "PROVED" if node == "energy" else "UNKNOWN")
            child = final["checks"][final["node_states"][node]["best_attempt"]]
            self.assertNotEqual(
                child["result"]["candidate"]["pid"], child["result"]["replay"]["pid"]
            )
            self.assertTrue(child["result"]["candidate"]["import_audits"])
            self.assertTrue(child["result"]["candidate"]["source_use"])
            self.assertEqual(
                child["plan"]["formal_statement"],
                final["plan"]["graph_compilation"]["node_plans"][node]["formal_statement"],
            )
        write("full-chain-success.json", final)
        single = deepcopy(examples()[0]["nodes"])
        negative = [{**deepcopy(single[0]), "id": "lemma", "kind": "lemma"}, deepcopy(single[0])]
        negative[1]["depends_on"] = ["lemma"]
        negative[1]["target"].update(
            original="对任意实数x、y，1≤0。用于检查错误原问题不继承前置证明。",
            goal=op("le", r(1), r(0)),
            proof={"tactic": "rfl"},
        )
        false_key = save(negative, "false-original")
        good = terminal(false_key, submit(false_key, "lemma"))
        self.assertEqual(good["math_verdict"], "UNKNOWN")
        false = terminal(false_key, submit(false_key, "problem"))
        self.assertEqual(false["attempts"][-1]["state"], "REJECTED")
        self.assertEqual(false["math_verdict"], "UNKNOWN")
        write("false-original.json", false)
        cancelled_key = save(single, "cancel-after-actual-child-success")
        original_execute = PDELanguageStore.execute

        def cancel_after(checker, *args, **kwargs):
            result = original_execute(checker, *args, **kwargs)
            self.assertEqual(
                (result["state"], result["math_verdict"], result["evidence_level"]),
                ("COMPLETED", "PROVED", "E4"),
            )
            write("child-success-before-graph-cancellation.json", result)
            attempt = store.read(cancelled_key)["attempts"][-1]
            http(
                "POST",
                prefix + "/" + cancelled_key + "/cancel",
                {"attempt_id": attempt["attempt_id"]},
            )
            return result

        with patch.object(PDELanguageStore, "execute", cancel_after):
            cancelled = terminal(cancelled_key, submit(cancelled_key, "problem"))
        self.assertEqual(cancelled["attempts"][-1]["state"], "CANCELLED")
        self.assertEqual(cancelled["math_verdict"], "UNKNOWN")
        write("cancelled-after-success.json", cancelled)
        context_key = save(single, "parent-context-control")
        context = terminal(context_key, submit(context_key, "problem"))
        self.assertEqual(context["math_verdict"], "PROVED")
        write("context-before-change.json", context)
        # Stop the real queue before the separately supervised exit control.
        server.pump.close()
        orphan_key = save(single, "supervisor-exit-after-child-success")
        orphan_attempt = submit(orphan_key, "problem")
        marker = out / "supervisor-child-success.json"
        program = """from pathlib import Path
import os,sys
from unittest.mock import patch
from mra.pde_research import PDEResearchStore
from mra.pde_language_lab import PDELanguageStore
from mra.domain import canonical
original=PDELanguageStore.execute
def exit_after(checker,*args,**kwargs):
 result=original(checker,*args,**kwargs)
 if (result['state'],result['math_verdict'],result['evidence_level'])!=('COMPLETED','PROVED','E4'):raise RuntimeError('Real child did not complete')
 Path(sys.argv[3]).write_bytes(canonical(result)+b'\\n')
 os._exit(71)
with patch.object(PDELanguageStore,'execute',exit_after):
 PDEResearchStore(Path.cwd(),base=Path(sys.argv[1])).work_once(sys.argv[2])
"""
        (out / "supervisor-program.py").write_text(program)
        with (
            (out / "supervisor-stdout.log").open("xb") as stdout,
            (out / "supervisor-stderr.log").open("xb") as stderr,
        ):
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-u",
                    str(out / "supervisor-program.py"),
                    str(store.base),
                    orphan_key,
                    str(marker),
                ],
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
                env={**os.environ, "PYTHONPATH": "."},
            )
            try:
                child.wait(timeout=3600)
            except BaseException:
                from pdelang_post_runner import clean_owned_group

                clean_owned_group(child)
                raise
        self.assertEqual(child.returncode, 71, (out / "supervisor-stderr.log").read_text())
        self.assertEqual(json.loads(marker.read_text())["evidence_level"], "E4")
        orphan = store.report(orphan_key)
        self.assertEqual(orphan["attempts"][-1]["state"], "RUNNING")
        self.assertEqual(orphan["math_verdict"], "UNKNOWN")
        record_path = store.directory(orphan_key) / "record.json"
        orphan_record = record_path.read_bytes()
        (out / "supervisor-parent-record.json").write_bytes(orphan_record)
        recovered = store.recover()
        self.assertIn(orphan_attempt["attempt_id"], recovered["interrupted"])
        interrupted = store.report(orphan_key)
        self.assertEqual(interrupted["attempts"][-1]["state"], "INTERRUPTED")
        self.assertEqual(interrupted["math_verdict"], "UNKNOWN")
        record_path.write_bytes(orphan_record)
        store.recover()
        self.assertEqual(store.report(orphan_key)["math_verdict"], "UNKNOWN")
        self.assertEqual(store.work_once(orphan_key)["worker_state"], "IDLE")
        write(
            "supervisor-recovery.json",
            {
                "exit_code": child.returncode,
                "before": orphan,
                "after": interrupted,
                "recovery": recovered,
                "parent_record_restored_without_promotion": True,
            },
        )
        mark("supervisor-recovery-passed")
        # Damage a real imported ancestor, observe revocation, then restore both bytes and old graph record.
        record_path = store.directory(full_key) / "record.json"
        original_record = record_path.read_bytes()
        (out / "complete-chain-record-before-damage.json").write_bytes(original_record)
        first = store.read(full_key)["attempts"][0]["child_id"]
        proof = store.checker(full_key).directory(first) / "replay/proof.ndjson"
        before = proof.read_bytes()
        (out / "source-proof-before-damage.ndjson").write_bytes(before)
        proof.write_bytes(before + b" ")
        try:
            damaged = store.report(full_key)
            self.assertEqual(damaged["math_verdict"], "UNKNOWN")
        finally:
            proof.write_bytes(before)
        record_path.write_bytes(original_record)
        restored = store.report(full_key)
        self.assertTrue(all(a["invalidated"] for a in restored["attempts"]))
        self.assertTrue(
            all(s["math_verdict"] == "UNKNOWN" for s in restored["node_states"].values())
        )
        write("damage-restoration.json", {"damaged": damaged, "restored": restored})
        mark("damage-restoration-passed")
        _, withdrawn = http(
            "POST",
            prefix + "/" + false_key + "/withdraw",
            {
                "node_id": "lemma",
                "reviewer": "Actual graph lifecycle author",
                "reason": "Verify precursor withdrawal independently of source damage",
                "confirmed": True,
            },
        )
        self.assertTrue(all(a["invalidated"] for a in withdrawn["attempts"]))
        write("withdrawal.json", withdrawn)
        parent_now = store.research.report(parent["project_id"])
        edited = store.research.edit(
            parent["project_id"],
            {
                "request_id": "edit-context-" + uuid.uuid4().hex,
                "expected_version": parent_now["version"],
                "branch_id": "main",
                "nodes": parent_now["branches"]["main"]["nodes"],
            },
        )
        changed = store.report(context_key)
        self.assertTrue(changed["invalidated"])
        self.assertEqual(changed["math_verdict"], "UNKNOWN")
        write("parent-context-change.json", {"parent": edited, "graph": changed})
        current = [store.report(p.name) for p in store.paths()]
        legacy.verify()
        runtime.load(ROOT)
        write(
            "summary.json",
            {
                "all_passed": True,
                "graphs": len(current),
                "parent_id": parent["project_id"],
                "store": str(store.base.relative_to(ROOT)),
                "historical_graph_node_E4": sum(
                    a["attempt"]["state"] == "COMPLETED" for a in observations
                ),
                "current_node_E4": sum(
                    s["math_verdict"] == "PROVED"
                    for row in current
                    for s in row["node_states"].values()
                ),
                "current_original_E4": sum(row["math_verdict"] == "PROVED" for row in current),
                "current": current,
                "known_author_visible_targets": True,
                "model_calls": 0,
                "automatic_retries": 0,
                "full_original_verified_before_controls": True,
                "general_weak_PDE": False,
            },
        )
        mark("all-controls-passed")
