"""Nonexecutable protocol fixtures and separately enabled genuine chain reuse."""

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

from mra.domain import canonical, digest
from mra.lemma_composition import FILES as LEGACY_FILES
from mra.lemma_composition_terms import compile_composition
from mra.library_composition import MODE
from mra.proof_library import FILES, LIMITS, ProofLibraryStore, audit_scope, provenance
from mra.research_flow import lease
from mra.tasks import TaskStore, WorkerBusy
from mra.verifier import NativeLeanBackend
from test_lemma_composition import composition, signatures, submission
from test_http_api import request as http, running_server
from test_search_evaluation import copy_sources

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/proof-library-20261028-01"
NONEXECUTABLE = (
    canonical({"meta": {"format": {"version": "3.1.0"}, "lean": {"version": "4.35.0"}}}) + b"\n"
)


def rid():
    return "proof-library-" + uuid.uuid4().hex


def admission(library, run_id, origin="composition"):
    ref = {"origin": origin, "run_id": run_id}
    return {
        **ref,
        "request_id": rid(),
        "plan_fingerprint": library.prepare(ref)["plan_fingerprint"],
        "reviewer": "MRA050 complete proof and storage rights reviewer",
        "confirmed": True,
        "rights_confirmed": True,
    }


def retirement():
    return {
        "request_id": rid(),
        "reviewer": "MRA050 retirement reviewer",
        "reason": "Isolated cascade verification",
        "confirmed": True,
    }


def application(item, source_request, *, title="复用完整已归档组合"):
    return {
        **source_request,
        "schema": MODE,
        "title": title,
        "sources": [{"alias": "previous", "item_id": item}],
        "proof": {
            "lemma": "previous",
            "terms": [{"var": v} for v in source_request["variables"]],
            "proofs": [{"hyp": h["name"]} for h in source_request["assumptions"]],
        },
    }


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_sources(self.root)
        for n in (*LEGACY_FILES, *FILES):
            p = self.root / n
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / n, p)
        self.library = ProofLibraryStore(self.root)
        self.request = composition()
        self.sources = []
        for binding, request in zip(self.request["sources"], signatures()):
            d = {
                **binding,
                "request": request,
                "formal_statement": "NONEXECUTABLE CONTRACT SOURCE",
                "origin": "mathlib-archive",
                "version": 1,
                "archive_fingerprint": digest(binding),
                "source_fingerprint": "NONEXECUTABLE",
                "alignment": {"reviewer": "NONEXECUTABLE"},
                "data_sha256": hashlib.sha256(NONEXECUTABLE).hexdigest(),
                "data_bytes": len(NONEXECUTABLE),
                "depth": 0,
            }
            d["ancestors"] = [{k: d[k] for k in ("item_id", "version", "archive_fingerprint")}]
            self.sources.append(d)
        original_loader = self.library.load_source

        def load(binding):
            source = next((s for s in self.sources if s["item_id"] == binding["item_id"]), None)
            return (
                ({**deepcopy(source), **binding}, NONEXECUTABLE)
                if source
                else original_loader(binding)
            )

        self.loader = patch.object(self.library, "load_source", side_effect=load)
        self.loader.start()
        self.addCleanup(self.loader.stop)
        self.run_id = "mathlib-" + uuid.uuid4().hex
        self.fake = {
            "run_id": self.run_id,
            "state": "COMPLETED",
            "math_verdict": "PROVED",
            "evidence_level": "E4",
            "invalidated": False,
            "cancel_requested": False,
            "artifact_status": "MATCHES_RECORDED_HASHES",
            "plan": {
                **compile_composition(self.request, self.sources),
                "source_items": self.sources,
            },
            "alignment": {"confirmed": True, "reviewer": "NONEXECUTABLE CONTRACT FIXTURE"},
            "submission_fingerprint": "NONEXECUTABLE",
            "result": {
                "candidate": {"pid": 11, "audit": {"type_matches": True}},
                "replay": {"pid": 12, "audit": {"mode": "data-only-kernel-replay-v1"}},
            },
        }
        directory = self.library.legacy.directory(self.run_id)
        (directory / "replay").mkdir(parents=True)
        (directory / "replay/proof.ndjson").write_bytes(NONEXECUTABLE)
        self.fake["files"] = {"replay/proof.ndjson": hashlib.sha256(NONEXECUTABLE).hexdigest()}
        patcher = patch.object(
            self.library.legacy, "report", side_effect=lambda _: deepcopy(self.fake)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def archive(self):
        return self.library.admit(admission(self.library, self.run_id))

    def complete_fixture(self, request):
        """Never execute this synthetic metadata-only export or count it as mathematical evidence."""
        store = self.library.compositions
        row = store.submit(submission(store, request))
        directory = store.directory(row["run_id"])
        (directory / "replay").mkdir(parents=True)
        (directory / "replay/proof.ndjson").write_bytes(NONEXECUTABLE)
        row.update(
            state="COMPLETED",
            stage="COMPLETED",
            math_verdict="PROVED",
            evidence_level="E4",
            files=self.fake["files"],
            result=deepcopy(self.fake["result"]),
        )
        with store.transaction() as db:
            store.save(db, row)
        return row

    def test_preview_preserves_complete_target_and_does_not_copy_or_execute(self):
        with patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")):
            plan = self.library.prepare({"origin": "composition", "run_id": self.run_id})
        self.assertEqual(
            (plan["math_verdict"], plan["evidence_level"], plan["new_lean_calls"]),
            ("UNKNOWN", "E0", 0),
        )
        self.assertEqual(
            plan["content"]["source"]["request"]["assumptions"], self.request["assumptions"]
        )
        self.assertFalse(self.library.directory(plan["content"]["item_id"]).exists())
        self.assertFalse(plan["privacy"]["sharing_allowed"] or plan["privacy"]["training_allowed"])

    def test_named_rights_fingerprint_identity_and_retirement_are_strict(self):
        data = admission(self.library, self.run_id)
        for change in (
            {"confirmed": False},
            {"rights_confirmed": False},
            {"rights_confirmed": 1},
            {"reviewer": " "},
            {"plan_fingerprint": "stale"},
            {"origin": "other"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.library.admit({**data, **change})
        first = self.library.admit(data)
        self.assertEqual(self.library.admit(data), first)
        with self.assertRaises(ValueError):
            self.library.admit({**data, "reviewer": "Other"})
        self.library.retire(first["item_id"], retirement())
        self.assertEqual(self.library.admit(data)["state"], "WITHDRAWN")
        with self.assertRaises(ValueError):
            self.library.admit(admission(self.library, self.run_id))

    def test_only_independently_replayed_intact_aligned_e4_can_be_archived(self):
        original = deepcopy(self.fake)
        for change in (
            {"state": "RUNNING"},
            {"state": "INTERRUPTED"},
            {"math_verdict": "UNKNOWN"},
            {"evidence_level": "E3"},
            {"invalidated": True},
            {"cancel_requested": True},
            {"artifact_status": "UNAVAILABLE"},
            {"alignment": {"confirmed": False}},
        ):
            self.fake = {**deepcopy(original), **change}
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.library.prepare({"origin": "composition", "run_id": self.run_id})
        self.fake = deepcopy(original)
        self.fake["result"]["replay"]["pid"] = 11
        with self.assertRaises(ValueError):
            self.library.prepare({"origin": "composition", "run_id": self.run_id})

    def test_archive_change_restore_and_old_request_never_revive_evidence(self):
        data = admission(self.library, self.run_id)
        row = self.library.admit(data)
        p = self.library.directory(row["item_id"]) / "proof/replay/proof.ndjson"
        raw = p.read_bytes()
        p.write_bytes(raw + b"damage")
        self.assertEqual(self.library.report(row["item_id"])["state"], "INVALIDATED")
        p.write_bytes(raw)
        self.assertEqual(self.library.admit(data)["math_verdict"], "UNKNOWN")

    def test_detected_item_and_run_ledger_corruption_survives_restored_rows(self):
        item = self.archive()
        run = self.complete_fixture(application(item["item_id"], self.request))
        with self.library.transaction() as db:
            db.execute("UPDATE items SET hash='damaged' WHERE id=?", (item["item_id"],))
        with self.assertRaises(ValueError):
            self.library.report(item["item_id"])
        with self.library.transaction() as db:
            self.library.save(
                db,
                {
                    k: item[k]
                    for k in (
                        "schema",
                        "item_id",
                        "source_ref",
                        "state",
                        "version",
                        "created_at",
                        "content",
                        "privacy",
                        "events",
                        "admission",
                    )
                },
            )
        self.assertEqual(self.library.report(item["item_id"])["state"], "INVALIDATED")
        with self.library.compositions.transaction() as db:
            db.execute("UPDATE runs SET sha256='damaged' WHERE id=?", (run["run_id"],))
        with self.assertRaises(ValueError):
            self.library.compositions.report(run["run_id"])
        with self.library.compositions.transaction() as db:
            self.library.compositions.save(db, run)
        self.assertTrue(self.library.compositions.report(run["run_id"])["invalidated"])

    def test_source_alignment_drift_invalidates_descendants_after_restore(self):
        first = self.archive()
        request = application(first["item_id"], self.request)
        second_run = self.complete_fixture(request)
        second = self.library.admit(
            admission(self.library, second_run["run_id"], "library-composition")
        )
        third_run = self.complete_fixture(application(second["item_id"], request))
        self.fake["alignment"]["reviewer"] = "changed"
        self.assertEqual(
            self.library.compositions.report(third_run["run_id"])["math_verdict"], "UNKNOWN"
        )
        self.fake["alignment"]["reviewer"] = "NONEXECUTABLE CONTRACT FIXTURE"
        self.assertEqual(self.library.report(first["item_id"])["state"], "INVALIDATED")
        self.assertEqual(self.library.report(second["item_id"])["state"], "INVALIDATED")
        self.assertTrue(self.library.compositions.report(third_run["run_id"])["invalidated"])

    def test_full_nat_gcd_signature_survives_archival_and_reuse(self):
        first = self.archive()
        request = application(first["item_id"], self.request)
        request["goal"] = {
            "and": [request["goal"], {"eq": [{"gcd": [{"var": "d"}, {"var": "d"}]}, {"var": "d"}]}]
        }
        request["proof"] = {
            "pair": [
                request["proof"],
                {"ref": "Nat.gcd_self", "terms": [{"var": "d"}], "proofs": []},
            ]
        }
        run = self.complete_fixture(request)
        item = self.library.admit(admission(self.library, run["run_id"], "library-composition"))
        p = self.library.compositions.preview(application(item["item_id"], request))
        self.assertEqual(p["source_items"][0]["request"]["goal"], request["goal"])
        self.assertIn("Nat.gcd", p["formal_statement"])
        bad = application(item["item_id"], request)
        bad["proof"]["proofs"] = []
        with self.assertRaises(ValueError):
            self.library.compositions.preview(bad)

    def test_temporary_busy_and_archive_lock_do_not_mark_healthy_records_damaged(self):
        row = self.archive()
        for error in (WorkerBusy("busy"), sqlite3.OperationalError("database is locked")):
            with (
                patch.object(self.library, "candidate", side_effect=error),
                self.assertRaises(type(error)),
            ):
                self.library.report(row["item_id"])
        self.assertEqual(self.library.report(row["item_id"])["state"], "ACTIVE")
        with lease(self.library.base), self.assertRaises(WorkerBusy):
            self.library.retire(row["item_id"], retirement())

    def test_cycle_and_ancestry_budgets_are_bounded_and_temporary_audit_exhaustion_is_not_damage(
        self,
    ):
        with audit_scope("a"), self.assertRaises(ValueError), audit_scope("a"):
            pass
        with audit_scope("a"):
            for _ in range(LIMITS["audit_visits"] - 1):
                with audit_scope("b"):
                    pass
            with self.assertRaises(WorkerBusy), audit_scope("b"):
                pass
        for source in (
            {"depth": 9, "ancestors": []},
            {"depth": 0, "ancestors": [{"item_id": str(i)} for i in range(64)]},
        ):
            with self.assertRaises(ValueError):
                provenance([source])
        self.assertEqual(self.archive()["state"], "ACTIVE")

    def test_deleted_source_removes_private_bytes_and_persistently_blocks_dependents(self):
        item = self.archive()
        run = self.complete_fixture(application(item["item_id"], self.request))
        self.library.retire(item["item_id"], retirement(), remove=True)
        self.assertFalse(self.library.directory(item["item_id"]).exists())
        self.assertEqual(self.library.compositions.report(run["run_id"])["math_verdict"], "UNKNOWN")
        self.assertEqual(self.library.search(include_inactive=True)["items"][0]["state"], "DELETED")

    def test_http_origin_csrf_strict_json_archive_read_withdraw_and_temporary_busy(self):
        with running_server(self.root) as server:
            server.proof_library = self.library
            ref = {"origin": "composition", "run_id": self.run_id}
            self.assertEqual(http(server, "POST", "/v1/proof-library/preview", ref)[0], 200)
            data = admission(self.library, self.run_id)
            self.assertEqual(
                http(
                    server,
                    "POST",
                    "/v1/proof-library/admit",
                    data,
                    headers={"Origin": "https://invalid.example"},
                )[0],
                403,
            )
            self.assertEqual(
                http(
                    server, "POST", "/v1/proof-library/admit", data, headers={"X-MRA-CSRF": "bad"}
                )[0],
                403,
            )
            self.assertEqual(
                http(
                    server, "POST", "/v1/proof-library/preview", raw=b'{"origin":"x","origin":"y"}'
                )[0],
                400,
            )
            item = http(server, "POST", "/v1/proof-library/admit", data)[1]
            base = "/v1/proof-library/items/" + item["item_id"]
            self.assertEqual(http(server, "GET", base)[1]["state"], "ACTIVE")
            self.assertEqual(http(server, "GET", "/v1/proof-library?unknown=1")[0], 400)
            detail = http(server, "GET", "/v1/library-compositions/sources/" + item["item_id"])[1]
            self.assertEqual(detail["signature"]["assumptions"], self.request["assumptions"])
            self.assertNotIn("proof.ndjson", json.dumps(detail))
            req = application(item["item_id"], self.request)
            plan = http(server, "POST", "/v1/library-compositions/preview", req)[1]
            body = {
                "request_id": rid(),
                "request": req,
                "plan_fingerprint": plan["plan_fingerprint"],
                "reviewer": "NONEXECUTABLE HTTP FIXTURE",
                "confirmed": True,
            }
            queued = http(server, "POST", "/v1/library-compositions/runs", body)[1]
            url = "/v1/library-compositions/runs/" + queued["run_id"]
            self.assertEqual(http(server, "GET", url)[1]["state"], "QUEUED")
            self.assertEqual(http(server, "POST", url + "/cancel", {})[1]["state"], "CANCELLED")
            with patch.object(self.library, "candidate", side_effect=WorkerBusy("busy")):
                self.assertEqual(http(server, "GET", base)[0], 503)
            self.assertEqual(
                http(server, "POST", base + "/withdraw", retirement())[1]["state"], "WITHDRAWN"
            )


def real_library(base):
    library = ProofLibraryStore(base)
    library.root = ROOT
    library.compositions.root = ROOT
    library.compositions.tasks = TaskStore(ROOT)
    library.legacy.root = ROOT
    library.legacy.tasks = TaskStore(ROOT)
    library.legacy.knowledge.root = ROOT
    library.legacy.knowledge.sources.root = ROOT
    # Read the actual legacy source and its existing private roots without altering either.
    from mra.lemma_composition import LemmaCompositionStore

    library.legacy = LemmaCompositionStore(ROOT)
    return library


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "Explicit real sealed Lean chain integration"
)
class RealTests(unittest.TestCase):
    def test_actual_composition_archive_can_be_imported_rearchived_and_imported_again(self):
        out = OUT / ("real-chain-" + uuid.uuid4().hex)
        out.mkdir()
        library = real_library(out / "data")
        first = library.admit(admission(library, "mathlib-c2d3df2f5585479b9c4bad13f1270db9"))
        request = application(first["item_id"], first["content"]["source"]["request"])
        a = {"var": request["variables"][0]}
        request.update(
            title="归档组合与自然数自身gcd的合取",
            original="保留截断减法等价式的完整合取，同时自然数a的自身gcd等于a。",
        )
        request["goal"] = {"and": [request["goal"], {"eq": [{"gcd": [a, a]}, a]}]}
        request["proof"] = {
            "pair": [request["proof"], {"ref": "Nat.gcd_self", "terms": [a], "proofs": []}]
        }
        store = library.compositions
        run = store.submit(submission(store, request))
        store.work_once(run["run_id"])
        run = store.report(run["run_id"])
        self.assertEqual(run["math_verdict"], "PROVED", run["result"])
        second = library.admit(admission(library, run["run_id"], "library-composition"))
        last = application(second["item_id"], request, title="再次复用含gcd的组合归档")
        third = store.submit(submission(store, last))
        store.work_once(third["run_id"])
        third = store.report(third["run_id"])
        self.assertEqual(third["math_verdict"], "PROVED", third["result"])
        (out / "proved.json").write_bytes(
            canonical({"first": first, "second": second, "run": run, "third": third}) + b"\n"
        )
        # Withdraw only this experiment's copy of the first archive, preserving all old roots.
        library.retire(first["item_id"], retirement())
        self.assertEqual(library.report(second["item_id"])["state"], "INVALIDATED")
        self.assertEqual(store.report(third["run_id"])["math_verdict"], "UNKNOWN")
        (out / "withdrawn.json").write_bytes(
            canonical(
                {
                    "second": library.report(second["item_id"]),
                    "third": store.report(third["run_id"]),
                }
            )
            + b"\n"
        )

    def test_genuine_condition_proof_rejection_cancellation_and_supervisor_exit(self):
        out = OUT / ("real-boundaries-" + uuid.uuid4().hex)
        out.mkdir()
        library = real_library(out / "data")
        first = library.admit(admission(library, "mathlib-c2d3df2f5585479b9c4bad13f1270db9"))
        request = application(first["item_id"], first["content"]["source"]["request"])
        a, b = [{"var": v} for v in request["variables"][:2]]
        request["assumptions"] = [{"name": "hcoprime", "formula": {"coprime": [a, b]}}]
        request["goal"] = {"and": [request["goal"], {"eq": [{"gcd": [a, b]}, {"nat": 1}]}]}
        request["proof"] = {
            "pair": [
                request["proof"],
                {"ref": "Nat.Coprime.gcd_eq_one", "terms": [a, b], "proofs": [{"hyp": "hcoprime"}]},
            ]
        }
        request.update(
            title="有完整互素条件的组合归档",
            original="假设a与b互素，保留原截断减法合取并证明gcd(a,b)=1。",
        )
        store = library.compositions
        row = store.submit(submission(store, request))
        store.work_once(row["run_id"])
        row = store.report(row["run_id"])
        self.assertEqual(row["math_verdict"], "PROVED", row["result"])
        conditional = library.admit(admission(library, row["run_id"], "library-composition"))
        good = application(conditional["item_id"], request)
        bad = deepcopy(good)
        bad["proof"]["proofs"] = [{"ref": "Nat.dvd_refl", "terms": [a], "proofs": []}]
        rejected = store.submit(submission(store, bad))
        store.work_once(rejected["run_id"])
        rejected = store.report(rejected["run_id"])
        self.assertEqual((rejected["state"], rejected["math_verdict"]), ("REJECTED", "UNKNOWN"))
        cancelled = store.submit(submission(store, good))
        store.cancel(cancelled["run_id"])
        self.assertEqual(store.report(cancelled["run_id"])["state"], "CANCELLED")
        interrupted = store.submit(submission(store, good))
        script = "\n".join(
            [
                "import os, sys",
                "from pathlib import Path",
                "from mra.domain import canonical",
                "from mra.verifier import NativeLeanBackend",
                "from test_proof_library import real_library",
                "library = real_library(Path(sys.argv[1]))",
                "original = NativeLeanBackend._run_worker",
                "def stop_after_replay(self, work, *args, **kwargs):",
                "    result = original(self, work, *args, **kwargs)",
                "    if kwargs['phase'] == 'REPLAY_RUNNING':",
                "        (work / 'supervisor-before-exit.json').write_bytes(canonical(result))",
                "        os._exit(73)",
                "    return result",
                "NativeLeanBackend._run_worker = stop_after_replay",
                "library.compositions.work_once(sys.argv[2])",
            ]
        )
        p = subprocess.run(
            [sys.executable, "-c", script, str(out / "data"), interrupted["run_id"]],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=240,
        )
        (out / "supervisor-stdout.log").write_text(p.stdout)
        (out / "supervisor-stderr.log").write_text(p.stderr)
        self.assertEqual(p.returncode, 73, p.stderr)
        raw = json.loads(
            (
                store.directory(interrupted["run_id"]) / "replay/supervisor-before-exit.json"
            ).read_text()
        )
        self.assertEqual((raw["run_status"], raw["returncode"]), ("COMPLETED", 0))
        store.recover()
        interrupted = store.report(interrupted["run_id"])
        self.assertEqual(
            (interrupted["state"], interrupted["math_verdict"]), ("INTERRUPTED", "UNKNOWN")
        )
        with self.assertRaises(ValueError):
            library.prepare({"origin": "library-composition", "run_id": interrupted["run_id"]})
        (out / "results.json").write_bytes(
            canonical(
                {
                    "conditional": conditional,
                    "rejected": rejected,
                    "cancelled": store.report(cancelled["run_id"]),
                    "interrupted": interrupted,
                }
            )
            + b"\n"
        )
        library.retire(first["item_id"], retirement())


if __name__ == "__main__":
    unittest.main()
