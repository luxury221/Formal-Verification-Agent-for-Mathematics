"""Opt-in actual Poisson proof-chain, failure and evidence-integrity controls."""

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

from mra import pde_energy_watched as p
from mra.domain import canonical
from mra.integrity import file_hash
from mra.tasks import TaskStore

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "opt-in real Lean")
class RealTests(unittest.TestCase):
    def test_smooth_poisson_proof_data_chain_and_safety(self):
        out = ROOT / "artifacts/watched-integrity-20261029-01" / ("real-" + uuid.uuid4().hex)
        out.mkdir()
        plan = {
            "scope": "Known one-dimensional smooth identities; no model, novelty, blind test or general weak PDE claim",
            "catalog": p.catalog(),
            "attempts_per_probe": 1,
            "shared_candidate_and_replay_worker_seconds": 40,
            "integrity_seconds_extra": True,
            "extra_controls": [
                "wrong-replay-target",
                "malformed-data",
                "wrong-imported-theorem",
                "cancel-after-successful-replay",
                "supervisor-exit-after-successful-replay",
                "producer-proof-corrupt-and-restore-with-descendant-invalidation",
            ],
        }
        (out / "declared-plan.json").write_bytes(canonical(plan) + b"\n")
        group = Path("/sys/fs/cgroup") / Path("/proc/self/cgroup").read_text().strip().split(":")[
            -1
        ].lstrip("/")
        resources = {n: (group / n).read_text().strip() for n in ("memory.max", "memory.swap.max")}
        self.assertEqual(
            resources, {"memory.max": str(6 * 1024**3), "memory.swap.max": str(512 * 1024**2)}
        )
        (out / "resources.json").write_bytes(canonical(resources) + b"\n")
        runs, rows = {}, []
        for probe in p.PROBES:
            source = runs.get(probe.get("source_probe"))
            if probe.get("source_probe") and source is None:
                rows.append(
                    {
                        "probe": probe["id"],
                        "state": "NOT_RUN",
                        "math_verdict": "UNKNOWN",
                        "reason": "required producer did not verify",
                    }
                )
                (out / "progress.json").write_bytes(canonical(rows) + b"\n")
                with self.subTest(probe=probe["id"]):
                    self.fail("Required producer did not verify; no replacement or retry")
                continue
            value = p.run(
                ROOT,
                probe["id"],
                "Codex local known-source semantic alignment",
                source_run_id=source,
            )
            row = {"probe": probe["id"], **value}
            rows.append(row)
            (out / "progress.json").write_bytes(canonical(rows) + b"\n")
            if value["state"] == "VERIFIED":
                runs[probe["id"]] = Path(value["directory"]).name
            with self.subTest(probe=probe["id"]):
                self.assertEqual(value["state"], probe["expected"])
                self.assertEqual(
                    value["math_verdict"], "PROVED" if value["state"] == "VERIFIED" else "UNKNOWN"
                )
                if probe.get("source_probe") and value["state"] == "VERIFIED":
                    self.assertIn(
                        "MraCandidate.Reused.s0.checked", value["candidate"]["source_use"]
                    )
        (out / "source-runs.json").write_bytes(canonical(runs) + b"\n")
        if len(runs) != 5:
            self.fail("Known positive chain incomplete; retain all outcomes and stop controls")
        controls = []
        first = ROOT / rows[0]["directory"]
        with TaskStore(ROOT).worker_lock() as fd:
            for kind in ("wrong-replay-target", "malformed-data", "wrong-imported-theorem"):
                work = out / kind
                work.mkdir()
                replay = kind != "wrong-imported-theorem"
                probe = p.PROBES[1]
                (work / "Candidate.lean").write_text(p.render(ROOT, probe, replay=replay))
                if replay:
                    raw = (
                        (first / "replay/proof.ndjson").read_bytes()
                        if kind == "wrong-replay-target"
                        else b'{"bad":true}\n'
                    )
                    (work / "proof.ndjson").write_bytes(raw)
                    (work / "policy.json").write_bytes(canonical(p.POLICY) + b"\n")
                else:
                    energy = ROOT / "artifacts/pde-energy-watched-v2/runs" / runs["poisson-energy"]
                    (work / "source-0.ndjson").write_bytes(
                        (energy / "replay/proof.ndjson").read_bytes()
                    )
                p.load(ROOT)
                execution = p._Runner(ROOT)._run_worker(
                    work,
                    out / ("sandbox-" + kind),
                    work / "stdout.log",
                    work / "stderr.log",
                    40,
                    phase="REPLAY_RUNNING" if replay else "LEAN_RUNNING",
                    lifecycle_lock_fd=fd,
                )
                controls.append({"kind": kind, **execution})
                (out / "controls.json").write_bytes(canonical(controls) + b"\n")
                self.assertEqual(execution["run_status"], "COMPLETED")
                self.assertNotEqual(execution["returncode"], 0)
                if kind != "malformed-data":
                    expected = (
                        "MRA_REPLAY_TARGET_MISMATCH" if replay else "MRA_IMPORT_TARGET_MISMATCH"
                    )
                    self.assertIn(expected, (work / "stdout.log").read_text())
        original_worker = p._Runner._run_worker
        cancelled = {"value": False}

        def cancel_after_replay(runner, *args, **kwargs):
            result = original_worker(runner, *args, **kwargs)
            if kwargs["phase"] == "REPLAY_RUNNING" and result["returncode"] == 0:
                cancelled["value"] = True
            return result

        with patch.object(p._Runner, "_run_worker", cancel_after_replay):
            cancelled_result = p.run(
                ROOT,
                p.PROBES[0]["id"],
                "Codex cancellation control",
                cancel_requested=lambda: cancelled["value"],
            )
        (out / "cancelled.json").write_bytes(canonical(cancelled_result) + b"\n")
        self.assertEqual(cancelled_result["state"], "CANCELLED")
        self.assertEqual(cancelled_result["math_verdict"], "UNKNOWN")
        self.assertEqual(cancelled_result["replay"]["returncode"], 0)
        marker = out / "supervisor-marker.json"
        script = out / "supervisor.py"
        script.write_text(
            "import os\nfrom pathlib import Path\nfrom mra import pde_energy_watched as p\nfrom mra.domain import canonical\n"
            "root=Path.cwd()\n"
            "marker=Path(" + repr(str(marker)) + ")\n"
            "original=p._Runner._run_worker\nrows=[]\n"
            "def worker(self,*args,**kwargs):"
            "\n    value=original(self,*args,**kwargs)\n"
            "    rows.append({'phase':kwargs['phase'],**value})\n"
            "    marker.write_bytes(canonical(rows)+b'\\n')\n"
            "    if kwargs['phase']=='REPLAY_RUNNING' and value['returncode']==0: os._exit(71)\n"
            "    return value\n"
            "p._Runner._run_worker=worker\n"
            "p.run(root,'integration-by-parts','Codex explicit crash control')\n"
        )
        before = set((ROOT / "artifacts/pde-energy-watched-v2/runs").iterdir())
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT,
            timeout=240,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": "."},
        )
        (out / "supervisor-stdout.log").write_text(proc.stdout)
        (out / "supervisor-stderr.log").write_text(proc.stderr)
        self.assertEqual(proc.returncode, 71)
        added = set((ROOT / "artifacts/pde-energy-watched-v2/runs").iterdir()) - before
        self.assertEqual(len(added), 1)
        interrupted = added.pop()
        observed = p.report(ROOT, interrupted.name)
        self.assertEqual(observed["state"], "RUNNING")
        recovered = p.recover(ROOT, interrupted.name)
        self.assertEqual(recovered["state"], "INTERRUPTED")
        self.assertEqual(recovered["math_verdict"], "UNKNOWN")
        (out / "supervisor-result.json").write_bytes(
            canonical(
                {
                    "exit_code": proc.returncode,
                    "run_id": interrupted.name,
                    "before": observed,
                    "after": recovered,
                    "recorded_workers": json.loads(marker.read_text()),
                }
            )
            + b"\n"
        )
        green = ROOT / "artifacts/pde-energy-watched-v2/runs" / runs["zero-boundary-green"]
        target = green / "replay/proof.ndjson"
        original_bytes = target.read_bytes()
        (out / "producer-original-proof.ndjson").write_bytes(original_bytes)
        target.write_bytes(original_bytes + b" ")
        invalid = p.report(ROOT, green.name)
        target.write_bytes(original_bytes)
        restored = p.report(ROOT, green.name)
        descendants = {
            name: p.report(ROOT, runs[name])
            for name in ("poisson-smooth-test", "poisson-energy", "poisson-work-nonnegative")
        }
        (out / "invalidation.json").write_bytes(
            canonical(
                {
                    "invalid": invalid,
                    "after_original_bytes_restored": restored,
                    "descendants": descendants,
                    "producer_bytes_restored": file_hash(target) == p.digest_bytes(original_bytes),
                }
            )
            + b"\n"
        )
        self.assertEqual(invalid["state"], "INVALIDATED")
        self.assertEqual(restored["state"], "INVALIDATED")
        self.assertTrue(
            all(
                r["state"] == "INVALIDATED" and r["math_verdict"] == "UNKNOWN"
                for r in descendants.values()
            )
        )
        self.assertEqual(p.report(ROOT, runs["integration-by-parts"])["state"], "VERIFIED")
        (out / "completed.json").write_bytes(
            canonical(
                {
                    "all_passed": True,
                    "historical_positive_E4": 5,
                    "current_positive_E4_after_controls": 1,
                    "source_exposure": "known",
                    "model_calls": 0,
                }
            )
            + b"\n"
        )
