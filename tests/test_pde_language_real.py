"""Opt-in new PDE ledger checks; fixed known tasks, real Lean and retained failures."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

from mra import pde_energy_watched as prior
from mra import pde_language_runtime as runtime
from mra.analysis_examples import v
from mra.calculus_examples import binding
from mra.domain import canonical, digest
from mra.integrity import IntegrityError
from mra.pde_language import compile_request
from mra.pde_language_examples import EXAMPLES, example, hyp
from mra.pde_language_lab import PDELanguageStore, render
from mra.tasks import TaskStore

ROOT = Path(__file__).resolve().parents[1]
FIXED_IBP = "pdewatched-18a1d58bfcd746e1bd2a22fded0b4e6d"


def negative_cases(bindings):
    raw = example("zero-boundary-green", bindings)
    raw["goal"]["eq"][1] = raw["goal"]["eq"][1]["neg"]
    yield "wrong-green-sign", raw

    raw = example("zero-boundary-green", bindings)
    raw["assumptions"].pop()
    raw["proof"]["term"]["rewrite"]["equalities"].pop()
    yield "missing-second-boundary", raw

    raw = example("integration-by-parts", bindings)
    raw["assumptions"].pop(1)
    raw["proof"]["term"]["source_apply"]["proofs"].pop(1)
    yield "missing-derivative", raw

    raw = example("integration-by-parts", bindings)
    raw["variables"].append(binding("mu", "MeasureReal"))
    raw["goal"]["eq"][0]["interval_integral"]["measure"] = v("mu")
    yield "different-measure", raw

    raw = example("poisson-work-nonnegative", bindings)
    raw["assumptions"].pop()
    raw["proof"]["term"]["rewrite"]["proof"]["rule"]["proofs"][0] = hyp("hi")
    yield "missing-endpoint-order", raw


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "opt-in actual Lean and source closure checks"
)
class RealTests(unittest.TestCase):
    def test_classical_pde_inputs_source_chain_and_publication_controls(self):
        out = ROOT / "artifacts/pde-language-20261029-01" / ("real-" + uuid.uuid4().hex)
        out.mkdir()
        store = PDELanguageStore(ROOT, base=out / "pde-language-private")
        declared = {
            "scope": "Known editable classic one-dimensional PDE identities; not novelty, blind evaluation or general weak PDE",
            "positive_cases": list(EXAMPLES),
            "negative_cases": [n for n, _ in negative_cases({})],
            "attempts_per_case": 1,
            "shared_worker_seconds": 40,
            "model_calls": 0,
            "integrity_time_is_extra": True,
            "extra_controls": [
                "wrong-type-fingerprint-before-submit",
                "wrong-replay-target",
                "wrong-imported-full-type",
                "cancel-after-real-successful-replay",
                "supervisor-exit-after-real-successful-replay",
                "source-corrupt-restore-and-descendant-invalidation",
            ],
        }
        (out / "declared-plan.json").write_bytes(canonical(declared) + b"\n")
        group = Path("/sys/fs/cgroup") / Path("/proc/self/cgroup").read_text().strip().split(":")[
            -1
        ].lstrip("/")
        resources = {n: (group / n).read_text().strip() for n in ("memory.max", "memory.swap.max")}
        self.assertEqual(
            resources, {"memory.max": str(6 * 1024**3), "memory.swap.max": str(512 * 1024**2)}
        )
        (out / "resources.json").write_bytes(canonical(resources) + b"\n")
        fixed = prior.report(ROOT, FIXED_IBP)
        self.assertEqual(fixed["math_verdict"], "PROVED")
        old_request = json.loads(
            (ROOT / "artifacts/pde-energy-watched-v2/runs" / FIXED_IBP / "request.json").read_text()
        )
        bindings = {
            "ibp": {
                "run_id": FIXED_IBP,
                "type_fingerprint": digest(old_request["probe"]["statement"]),
            }
        }
        initial_bindings = deepcopy(bindings)
        rows, ids, requests = [], {}, {}

        def save_progress():
            (out / "progress.json").write_bytes(canonical(rows) + b"\n")

        def submit(raw, label):
            plan = store.preview(raw)
            row = store.submit(
                {
                    "request_id": label,
                    "request": raw,
                    "plan_fingerprint": plan["plan_fingerprint"],
                    "reviewer": "Codex known classical PDE local alignment",
                    "confirmed": True,
                }
            )
            self.assertEqual((row["math_verdict"], row["evidence_level"]), ("UNKNOWN", "E0"))
            return row["run_id"]

        for name in EXAMPLES:
            raw = example(name, bindings)
            requests[name] = raw
            run_id = submit(raw, name)
            store.work_once(run_id)
            row = store.report(run_id)
            rows.append({"case": name, "run_id": run_id, "record": row})
            save_progress()
            ids[name] = run_id
            if row["state"] != "COMPLETED" or row["math_verdict"] != "PROVED":
                self.fail("Declared positive did not verify; retained result, no retry: " + name)
            self.assertEqual(row["evidence_level"], "E4")
            self.assertNotEqual(row["result"]["candidate"]["pid"], row["result"]["replay"]["pid"])
            self.assertLessEqual(row["result"]["worker_seconds"], 40.2)
            key = {
                "integration-by-parts": "ibp",
                "zero-boundary-green": "green",
                "poisson-smooth-test": "test",
                "poisson-energy": "energy",
            }.get(name)
            if key:
                bindings[key] = {
                    "run_id": run_id,
                    "type_fingerprint": digest(row["plan"]["formal_statement"]),
                }
        for name, raw in negative_cases(bindings):
            run_id = submit(raw, name)
            store.work_once(run_id)
            row = store.report(run_id)
            rows.append({"case": name, "run_id": run_id, "record": row})
            save_progress()
            with self.subTest(name=name):
                self.assertEqual(row["state"], "REJECTED")
                self.assertEqual((row["math_verdict"], row["evidence_level"]), ("UNKNOWN", "E0"))
                self.assertFalse(row["result"]["diagnostic"]["mathematical_counterexample"])

        controls = []
        raw = example("integration-by-parts", bindings)
        raw["sources"][0]["type_fingerprint"] = "0" * 64
        with self.assertRaises(IntegrityError):
            store.preview(raw)
        controls.append(
            {"kind": "wrong-type-fingerprint-before-submit", "workers": 0, "blocked": True}
        )
        (out / "controls.json").write_bytes(canonical(controls) + b"\n")

        first = store.directory(ids["integration-by-parts"])
        with TaskStore(ROOT).worker_lock() as lock_fd:
            for kind in ("wrong-replay-target", "wrong-imported-full-type"):
                work = out / kind
                work.mkdir()
                replay = kind == "wrong-replay-target"
                compiled = compile_request(requests["zero-boundary-green"])
                compiled["resolved_sources"] = store.read(ids["zero-boundary-green"])["plan"][
                    "resolved_sources"
                ]
                (work / "Candidate.lean").write_text(render(ROOT, compiled, replay=replay))
                if replay:
                    (work / "proof.ndjson").write_bytes(
                        (first / "replay/proof.ndjson").read_bytes()
                    )
                    (work / "policy.json").write_bytes(canonical(runtime.POLICY) + b"\n")
                else:
                    other = store.directory(ids["poisson-energy"])
                    (work / "source-0.ndjson").write_bytes(
                        (other / "replay/proof.ndjson").read_bytes()
                    )
                runtime.load(ROOT)
                outcome = runtime.Runner(ROOT)._run_worker(
                    work,
                    out / ("sandbox-" + kind),
                    work / "stdout.log",
                    work / "stderr.log",
                    40,
                    phase="REPLAY_RUNNING" if replay else "LEAN_RUNNING",
                    lifecycle_lock_fd=lock_fd,
                )
                controls.append({"kind": kind, **outcome})
                (out / "controls.json").write_bytes(canonical(controls) + b"\n")
                self.assertEqual(outcome["run_status"], "COMPLETED")
                self.assertNotEqual(outcome["returncode"], 0)
                self.assertIn(
                    "MRA_REPLAY_TARGET_MISMATCH" if replay else "MRA_IMPORT_TARGET_MISMATCH",
                    runtime.read_log(work / "stdout.log"),
                )

        original_worker = runtime.Runner._run_worker
        cancelled_id = submit(
            example("integration-by-parts", initial_bindings), "cancel-after-replay"
        )

        def cancel_after_replay(runner, *args, **kwargs):
            result = original_worker(runner, *args, **kwargs)
            if kwargs["phase"] == "REPLAY_RUNNING" and result["returncode"] == 0:
                store.cancel(cancelled_id)
            return result

        with patch.object(runtime.Runner, "_run_worker", cancel_after_replay):
            store.work_once(cancelled_id)
        cancelled = store.report(cancelled_id)
        self.assertEqual(
            (cancelled["state"], cancelled["math_verdict"], cancelled["evidence_level"]),
            ("CANCELLED", "UNKNOWN", "E0"),
        )
        self.assertEqual(cancelled["result"]["replay"]["returncode"], 0)
        controls.append(
            {
                "kind": "cancel-after-real-successful-replay",
                "run_id": cancelled_id,
                "record": cancelled,
            }
        )
        (out / "controls.json").write_bytes(canonical(controls) + b"\n")

        interrupted_id = submit(
            example("integration-by-parts", initial_bindings), "supervisor-exit-after-replay"
        )
        marker = out / "supervisor-stages.json"
        code = """
import os,sys
from pathlib import Path
from mra.domain import canonical
from mra import pde_language_runtime as runtime
from mra.pde_language_lab import PDELanguageStore
root=Path.cwd()
store=PDELanguageStore(root,base=Path(sys.argv[1]))
old=runtime.Runner._run_worker
rows=[]
def run(runner,*args,**kw):
 result=old(runner,*args,**kw)
 rows.append({'phase':kw['phase'],**result})
 with Path(sys.argv[3]).open('wb') as target:
  target.write(canonical(rows)+b'\\n')
  target.flush()
  os.fsync(target.fileno())
 if kw['phase']=='REPLAY_RUNNING' and result['returncode']==0:
  os._exit(71)
 return result
runtime.Runner._run_worker=run
store.work_once(sys.argv[2])
"""
        (out / "supervisor-control.py").write_text(code)
        proc = subprocess.run(
            [sys.executable, "-c", code, str(store.base), interrupted_id, str(marker)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=1200,
            env={**os.environ, "PYTHONPATH": "."},
        )
        (out / "supervisor-stdout.log").write_text(proc.stdout)
        (out / "supervisor-stderr.log").write_text(proc.stderr)
        self.assertEqual(proc.returncode, 71)
        interrupted = store.report(interrupted_id)
        self.assertEqual(
            (interrupted["state"], interrupted["math_verdict"]), ("RUNNING", "UNKNOWN")
        )
        recovered = store.recover()
        self.assertIn(interrupted_id, recovered["interrupted"])
        recovered_row = store.report(interrupted_id)
        self.assertEqual(
            (
                recovered_row["state"],
                recovered_row["math_verdict"],
                recovered_row["evidence_level"],
            ),
            ("INTERRUPTED", "UNKNOWN", "E0"),
        )
        stages = json.loads(marker.read_text())
        self.assertEqual(len(stages), 2)
        self.assertTrue(all(s["returncode"] == 0 for s in stages))
        controls.append(
            {
                "kind": "supervisor-exit-after-real-successful-replay",
                "run_id": interrupted_id,
                "stages": stages,
                "recovered": recovered_row,
            }
        )
        (out / "controls.json").write_bytes(canonical(controls) + b"\n")

        producer = ids["poisson-energy"]
        proof = store.directory(producer) / "replay/proof.ndjson"
        original = proof.read_bytes()
        try:
            proof.write_bytes(b'{"deliberate_corrupt_source":true}\n')
            invalid = store.report(producer)
            self.assertTrue(invalid["invalidated"])
            self.assertEqual(invalid["math_verdict"], "UNKNOWN")
            descendant = store.report(ids["poisson-work-nonnegative"])
            self.assertTrue(descendant["invalidated"])
            self.assertEqual(descendant["math_verdict"], "UNKNOWN")
        finally:
            proof.write_bytes(original)
        self.assertTrue(store.report(producer)["invalidated"])
        self.assertTrue(store.report(ids["poisson-work-nonnegative"])["invalidated"])
        controls.append(
            {
                "kind": "source-corrupt-restore-and-descendant-invalidation",
                "producer": producer,
                "descendant": ids["poisson-work-nonnegative"],
                "permanent": True,
            }
        )
        (out / "controls.json").write_bytes(canonical(controls) + b"\n")
        final = {name: store.report(run_id) for name, run_id in ids.items()}
        (out / "final-positive-states.json").write_bytes(canonical(final) + b"\n")
        (out / "source-runs.json").write_bytes(
            canonical({"ids": ids, "bindings": bindings}) + b"\n"
        )
        (out / "summary.json").write_bytes(
            canonical(
                {
                    "all_passed": True,
                    "known_positive_historical_E4": len(EXAMPLES),
                    "current_positive_E4": sum(
                        r["math_verdict"] == "PROVED" for r in final.values()
                    ),
                    "negative_cases": 5,
                    "controls": len(controls),
                    "model_calls": 0,
                    "scope": declared["scope"],
                    "store": str(store.base.relative_to(ROOT)),
                }
            )
            + b"\n"
        )


if __name__ == "__main__":
    unittest.main()
