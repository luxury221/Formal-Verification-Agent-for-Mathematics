"""Opt-in actual HTTP/worker/Lean graph checks; no mock substitutes mathematical results."""

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

from mra import calculus_foundation_v2 as foundation
from mra.calculus_examples import examples as calculus_examples
from mra.calculus_research import CalculusResearchStore, examples
from mra.domain import canonical
from mra.http_api import WorkbenchServer, TaskPump
from mra.integrity import ContentSnapshot
from test_calculus_research import raw

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "opt-in actual calculus graph Lean/HTTP"
)
class RealTests(unittest.TestCase):
    def test_full_graph_http_worker_and_actual_lifecycle(self):
        out = ROOT / "artifacts/calculus-research-20261029-01" / ("real-" + uuid.uuid4().hex)
        out.mkdir()

        def write(name, value):
            (out / name).write_bytes(canonical(value) + b"\n")

        write(
            "declared-plan.json",
            {
                "known_examples": examples(),
                "positive_graphs": 2,
                "false_original_graphs": 1,
                "cancel_after_successful_replay": 1,
                "supervisor_exit_after_successful_replay": 1,
                "restoration": "real proof bytes and old parent record",
                "withdrawal": "precursor and descendant states",
                "per_check_shared_worker_seconds": 40,
                "retries": 0,
                "scope": "new graph/HTTP integration using known targets; not rerunning original MRA083 cohort scores, not blind or PDE",
            },
        )
        cg = Path("/sys/fs/cgroup") / Path("/proc/self/cgroup").read_text().strip().split(":")[
            -1
        ].lstrip("/")
        resources = {n: (cg / n).read_text().strip() for n in ("memory.max", "memory.swap.max")}
        self.assertEqual(
            resources, {"memory.max": str(6 * 1024**3), "memory.swap.max": str(512 * 1024**2)}
        )
        write("resources.json", resources)
        legacy = ContentSnapshot.load_runtime(ROOT)
        legacy.verify()
        foundation.load(ROOT)
        store = CalculusResearchStore(ROOT, base=out / "calculus-research-private")
        server = WorkbenchServer(ROOT, 0, start_worker=False)
        server.calculus_research = store
        server.pump = TaskPump(server.store, calculus_research=store)
        server.pump.thread.start()
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 10)
        self.addCleanup(server.shutdown)
        calls = []

        def http(method, route, data=None):
            connection = HTTPConnection(*server.server_address, timeout=180)
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
                if response.status != 503:
                    self.assertLess(response.status, 400, value)
                return response.status, value
            finally:
                connection.close()

        prefix = "/v1/calculus-research"
        _, project = http(
            "POST",
            "/v1/research",
            {
                "request_id": "real-analysis-" + uuid.uuid4().hex,
                "title": "MRA084 真实项目图验收",
                "question": "已知导数与积分目标的完整语境、真实检查、错误原问题与安全生命周期。",
            },
        )
        write("parent.json", project)
        observations = []

        def save(value, label):
            _, p = http("POST", prefix + "/preview", value)
            _, r = http(
                "POST",
                prefix,
                {
                    "request_id": "real-" + label + uuid.uuid4().hex,
                    "request": value,
                    "plan_fingerprint": p["plan_fingerprint"],
                },
            )
            return r["graph_id"]

        def submit(key, node):
            _, p = http("POST", prefix + "/" + key + "/node-preview", {"node_id": node})
            _, attempt = http(
                "POST",
                prefix + "/" + key + "/submit",
                {
                    "request_id": "check-" + uuid.uuid4().hex,
                    "node_id": node,
                    "execution_fingerprint": p["execution_fingerprint"],
                    "reviewer": "Codex actual analysis graph full statement review",
                    "confirmed": True,
                },
            )
            return attempt

        def terminal(key, attempt):
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                code, report = http("GET", prefix + "/" + key)
                if code == 503:
                    time.sleep(0.2)
                    continue
                found = next(
                    a for a in report["attempts"] if a["attempt_id"] == attempt["attempt_id"]
                )
                if found["state"] not in ("QUEUED", "RUNNING"):
                    observations.append({"graph_id": key, "attempt": found, "report": report})
                    write("results.json", observations)
                    return report
                time.sleep(0.2)
            self.fail("Bounded graph worker did not reach a terminal state")

        positives = []
        for index in (0, 1):
            key = save(raw(project, index), "positive")
            first = terminal(key, submit(key, "lemma"))
            self.assertEqual(first["node_states"]["lemma"]["math_verdict"], "PROVED")
            self.assertEqual(first["math_verdict"], "UNKNOWN")
            final = terminal(key, submit(key, "problem"))
            self.assertEqual(final["math_verdict"], "PROVED")
            for child in final["checks"].values():
                self.assertNotEqual(
                    child["result"]["candidate"]["pid"], child["result"]["replay"]["pid"]
                )
            positives.append(key)
        bad = raw(project, 1)
        bad["nodes"][1]["target"] = deepcopy(
            next(e["request"] for e in calculus_examples() if e["id"] == "wrong-orientation")
        )
        bad_key = save(bad, "false-original")
        self.assertEqual(
            terminal(bad_key, submit(bad_key, "lemma"))["node_states"]["lemma"]["math_verdict"],
            "PROVED",
        )
        rejected = terminal(bad_key, submit(bad_key, "problem"))
        self.assertEqual(rejected["attempts"][-1]["state"], "REJECTED")
        self.assertEqual(rejected["math_verdict"], "UNKNOWN")
        single = raw(project, 1)
        single["nodes"] = [{**single["nodes"][0], "id": "problem", "kind": "problem"}]
        cancelled_key = save(single, "cancel")
        original_worker = foundation._Runner._run_worker

        def cancel_after(runner, *args, **kwargs):
            outcome = original_worker(runner, *args, **kwargs)
            if (
                kwargs["phase"] == "REPLAY_RUNNING"
                and outcome["run_status"] == "COMPLETED"
                and outcome["returncode"] == 0
            ):
                attempt = store.read(cancelled_key)["attempts"][-1]
                store.cancel(cancelled_key, {"attempt_id": attempt["attempt_id"]})
            return outcome

        with patch.object(foundation._Runner, "_run_worker", cancel_after):
            cancelled = terminal(cancelled_key, submit(cancelled_key, "problem"))
        self.assertEqual(cancelled["attempts"][-1]["state"], "CANCELLED")
        self.assertEqual(cancelled["math_verdict"], "UNKNOWN")
        self.assertEqual(
            next(iter(cancelled["checks"].values()))["result"]["replay"]["returncode"], 0
        )
        damaged_key = positives[0]
        original_record = (store.directory(damaged_key) / "record.json").read_bytes()
        child_id = store.read(damaged_key)["attempts"][0]["child_id"]
        proof = store.checker(damaged_key).directory(child_id) / "replay/proof.ndjson"
        original = proof.read_bytes()
        (out / "original-proof.ndjson").write_bytes(original)
        (out / "original-parent-record.json").write_bytes(original_record)
        proof.write_bytes(original + b" ")
        damaged = store.report(damaged_key)
        self.assertEqual(damaged["math_verdict"], "UNKNOWN")
        proof.write_bytes(original)
        (store.directory(damaged_key) / "record.json").write_bytes(original_record)
        restored = store.report(damaged_key)
        self.assertEqual(restored["math_verdict"], "UNKNOWN")
        self.assertTrue(all(a["invalidated"] for a in restored["attempts"]))
        write("proof-restoration.json", {"damaged": damaged, "restored": restored})
        _, withdrawn = http(
            "POST",
            prefix + "/" + positives[1] + "/withdraw",
            {
                "node_id": "lemma",
                "reviewer": "Codex real precursor withdrawal",
                "reason": "Check exact descendant graph invalidation",
                "confirmed": True,
            },
        )
        self.assertEqual(withdrawn["math_verdict"], "UNKNOWN")
        self.assertTrue(all(a["invalidated"] for a in withdrawn["attempts"]))
        write("withdrawal.json", withdrawn)
        server.pump.close()
        orphan_key = save(single, "supervisor-exit")
        orphan_attempt = submit(orphan_key, "problem")
        marker = out / "supervisor-replay-outcome.json"
        program = """from pathlib import Path
import os,sys
from unittest.mock import patch
from mra import calculus_foundation_v2 as f
from mra.calculus_research import CalculusResearchStore
from mra.domain import canonical
original=f._Runner._run_worker
def exit_after(runner,*args,**kwargs):
 result=original(runner,*args,**kwargs)
 if kwargs['phase']=='REPLAY_RUNNING' and result['run_status']=='COMPLETED' and result['returncode']==0:
  Path(sys.argv[3]).write_bytes(canonical(result))
  os._exit(71)
 return result
with patch.object(f._Runner,'_run_worker',exit_after):
 CalculusResearchStore(Path.cwd(),base=Path(sys.argv[1])).work_once(sys.argv[2])
"""
        execution = subprocess.run(
            [sys.executable, "-c", program, str(store.base), orphan_key, str(marker)],
            capture_output=True,
            text=True,
            timeout=600,
            env={**os.environ, "PYTHONPATH": "."},
        )
        (out / "supervisor-stdout.log").write_text(execution.stdout)
        (out / "supervisor-stderr.log").write_text(execution.stderr)
        self.assertEqual(execution.returncode, 71, execution.stderr)
        self.assertEqual(json.loads(marker.read_text())["returncode"], 0)
        before = store.report(orphan_key)
        self.assertEqual(before["math_verdict"], "UNKNOWN")
        self.assertEqual(before["attempts"][-1]["state"], "RUNNING")
        recovered = store.recover()
        self.assertIn(orphan_attempt["attempt_id"], recovered["interrupted"])
        after = store.report(orphan_key)
        self.assertEqual(after["math_verdict"], "UNKNOWN")
        self.assertEqual(after["attempts"][-1]["state"], "INTERRUPTED")
        self.assertEqual(store.work_once(orphan_key)["worker_state"], "IDLE")
        write(
            "supervisor-exit.json",
            {
                "exit_code": execution.returncode,
                "before": before,
                "recovery": recovered,
                "after": after,
                "automatic_retry": False,
            },
        )
        current = [store.report(p.name) for p in store.paths()]
        legacy.verify()
        foundation.load(ROOT)
        write("http-calls.json", calls)
        write(
            "summary.json",
            {
                "all_passed": True,
                "store": str(store.base.relative_to(ROOT)),
                "parent_id": project["project_id"],
                "graphs": len(current),
                "observed_terminal_checks": len(observations),
                "historical_node_e4": sum(
                    e["attempt"]["state"] == "COMPLETED" for e in observations
                ),
                "current_node_e4": sum(
                    s["math_verdict"] == "PROVED"
                    for r in current
                    for s in r["node_states"].values()
                ),
                "current_original_e4": sum(r["math_verdict"] == "PROVED" for r in current),
                "current": current,
                "model_calls": 0,
                "legacy_runtime_preserved": True,
                "pde_verified": False,
                "independent_blind_evaluation": False,
            },
        )
