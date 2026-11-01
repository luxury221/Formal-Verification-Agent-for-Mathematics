"""Actual HTTP contracts and opt-in real background proof-data workflow."""

from copy import deepcopy
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch
import uuid

import test_structure_lemma_library as core
from mra.domain import canonical
from mra.function_dynamics_checks import verified
from mra.http_api import TaskPump
from mra.lemma_research_examples import example
from mra.obligation_planning import propose
from mra.recursion_programs import RecursionStore
from mra.structure_lemma_library import LemmaExportStore, ExportApplicationStore
from mra.tasks import WorkerBusy
from mra.verifier import NativeLeanBackend
from test_http_api import request as http, running_server

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/structure-lemma-workbench-20261029-01"


def proposal(root):
    return {
        "schema": "structure-recursion-proposals-v2",
        "target": example(),
        "budget": {"max_candidates": 2, "max_expansions": 800, "max_rewrite_steps": 4},
    }


def confirmation(plan):
    return {
        "request_id": "MRA076-http-" + uuid.uuid4().hex,
        "request": plan["request"],
        "plan_fingerprint": plan["plan_fingerprint"],
        "reviewer": "MRA076 complete HTTP semantic QA",
        "confirmed": True,
    }


class ContractTests(unittest.TestCase):
    def setUp(self):
        core.ContractTests.setUp(self)

    def test_integrated_assets_and_proposals_are_inert_and_keep_full_target(self):
        with (
            running_server(self.root) as server,
            patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")),
        ):
            for route in ("/typed-research/lemmas", "/lemma-research.js", "/lemma-research.css"):
                status, content, headers = http(server, "GET", route)
                self.assertEqual(status, 200)
                self.assertTrue(content)
                self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(
                http(server, "GET", "/v1/lemma-research/example")[1]["target"], example()
            )
            p = http(server, "POST", "/v1/lemma-research/propose", proposal(self.root))[1]
            self.assertEqual(
                (p["math_verdict"], p["evidence_level"], p["lean_calls"]), ("UNKNOWN", "E0", 0)
            )
            self.assertEqual(len(p["candidates"]), 2)
            for c in p["candidates"]:
                self.assertEqual(c["request"]["target"], example())
            preview = http(
                server, "POST", "/v1/lemma-research/programs/preview", p["candidates"][0]["request"]
            )[1]
            self.assertEqual(preview["target"], example())
            self.assertEqual(len(preview["local_lemmas"]), 2)

    def test_same_origin_strict_json_queries_and_size(self):
        with running_server(self.root) as server:
            for headers in (
                {"Origin": "https://example.invalid"},
                {"X-MRA-CSRF": "wrong"},
                {"Sec-Fetch-Site": "cross-site"},
            ):
                self.assertEqual(
                    http(
                        server,
                        "POST",
                        "/v1/lemma-research/propose",
                        proposal(self.root),
                        headers=headers,
                    )[0],
                    403,
                )
            for raw in (b'{"schema":1,"schema":2}', b"[]", b'{"n":NaN}'):
                self.assertEqual(
                    http(server, "POST", "/v1/lemma-research/propose", raw=raw)[0], 400
                )
            self.assertEqual(
                http(server, "POST", "/v1/lemma-research/propose", raw=b" " * 196609)[0], 413
            )
            for route in ("/v1/lemma-research/example?x=1", "/v1/lemma-research/exports?execute=1"):
                self.assertEqual(http(server, "GET", route)[0], 400)
            for route in ("/v1/lemma-research/programs/work", "/v1/lemma-research/exports/recover"):
                self.assertEqual(http(server, "POST", route, {})[0], 404)

    def test_named_queue_wakeup_idempotence_cancel_revoke_and_export(self):
        with running_server(self.root) as server:
            server.pump = TaskPump(server.store, recursion=server.recursion)
            raw = propose(self.root, proposal(self.root))["candidates"][0]["request"]
            plan = http(server, "POST", "/v1/lemma-research/programs/preview", raw)[1]
            data = confirmation(plan)
            for update in ({"confirmed": 1}, {"reviewer": " "}, {"plan_fingerprint": "bad"}):
                self.assertEqual(
                    http(server, "POST", "/v1/lemma-research/programs/runs", {**data, **update})[0],
                    400,
                )
            status, row, _ = http(server, "POST", "/v1/lemma-research/programs/runs", data)
            self.assertEqual(status, 202)
            self.assertTrue(server.pump.wake.is_set())
            key = row["run_id"]
            self.assertEqual(
                http(server, "POST", "/v1/lemma-research/programs/runs", data)[1]["run_id"], key
            )
            self.assertEqual(
                http(server, "POST", f"/v1/lemma-research/programs/{key}/cancel", {"extra": 1})[0],
                400,
            )
            self.assertEqual(
                http(server, "POST", f"/v1/lemma-research/programs/{key}/cancel", {})[1]["state"],
                "CANCELLED",
            )
            self.assertEqual(
                http(server, "POST", "/v1/lemma-research/programs/runs", data)[1]["state"],
                "CANCELLED",
            )
            value = http(
                server,
                "POST",
                f"/v1/lemma-research/programs/{key}/revoke",
                {"reviewer": "QA", "reason": "Protocol withdrawal", "confirmed": True},
            )[1]
            self.assertTrue(value["invalidated"])
            exported = http(server, "GET", f"/v1/lemma-research/programs/{key}/export")[1]
            self.assertEqual(
                (exported["math_verdict"], exported["source_fingerprint"]), ("UNKNOWN", None)
            )
            self.assertEqual(
                http(server, "GET", "/v1/lemma-research/programs")[1]["runs"][0]["request_id"],
                data["request_id"],
            )

    def test_busy_read_is_retryable_and_never_invalidates_the_program(self):
        with running_server(self.root) as server:
            raw = propose(self.root, proposal(self.root))["candidates"][0]["request"]
            row = server.recursion.submit(confirmation(server.recursion.preview(raw)))
            with patch.object(server.recursion, "gate", side_effect=WorkerBusy("temporarily held")):
                status, payload, _ = http(
                    server, "GET", "/v1/lemma-research/programs/" + row["run_id"]
                )
                self.assertEqual(status, 503)
                self.assertEqual(payload["code"], "WORKER_BUSY")
                self.assertTrue(payload["retryable_read"])
            self.assertFalse(server.recursion.read(row["run_id"])["invalidated"])
            server.recursion.cancel(row["run_id"])

    def test_protocol_families_unchecked_sources_and_proof_download_are_rejected(self):
        with running_server(self.root) as server:
            raw = propose(self.root, proposal(self.root))["candidates"][0]["request"]
            for kind in ("exports", "applications"):
                self.assertEqual(
                    http(server, "POST", f"/v1/lemma-research/{kind}/preview", raw)[0], 400
                )
            row = server.recursion.submit(confirmation(server.recursion.preview(raw)))
            self.assertEqual(
                http(
                    server,
                    "POST",
                    "/v1/lemma-research/exports/preview",
                    core.export_request(row, 0),
                )[0],
                400,
            )
            self.assertEqual(
                http(server, "GET", "/v1/lemma-research/exports/" + row["run_id"] + "/source")[0],
                400,
            )
            self.assertEqual(
                http(
                    server, "GET", "/v1/lemma-research/programs/" + row["run_id"] + "/proof.ndjson"
                )[0],
                404,
            )
            server.recursion.cancel(row["run_id"])

    def test_background_pump_reaches_all_three_queues_and_preserves_optional_constructor(self):
        calls = []

        class Idle:
            def work_once(self):
                return {"worker_state": "IDLE"}

        class Queue:
            def __init__(self, name):
                self.name, self.done = name, False

            def work_once(self):
                if not self.done:
                    self.done = True
                    calls.append(self.name)
                    return {"worker_state": "EXECUTED"}
                if self.name == "applications":
                    pump.stopped.set()
                return {"worker_state": "IDLE"}

        pump = TaskPump(
            Idle(),
            recursion=Queue("programs"),
            lemma_exports=Queue("exports"),
            export_applications=Queue("applications"),
        )
        pump.thread.start()
        pump.thread.join(3)
        self.assertFalse(pump.thread.is_alive())
        self.assertEqual(calls, ["programs", "exports", "applications"])
        self.assertIsNone(TaskPump(Idle()).lemma_exports)


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "Explicit actual HTTP kernel workflow")
class RealTests(unittest.TestCase):
    def test_background_program_export_new_target_wrong_condition_and_withdrawal(self):
        area = OUT / ("real-http-" + uuid.uuid4().hex)
        area.mkdir(parents=True)
        parents = RecursionStore(ROOT, base=area / "programs")
        exports = LemmaExportStore(ROOT, parents=parents, base=area / "exports")
        apps = ExportApplicationStore(ROOT, exports=exports, base=area / "applications")
        calls, records = [], []

        def save(n, v):
            (area / n).write_bytes(canonical(v) + b"\n")

        save(
            "declared-plan.json",
            {
                "example": example(),
                "controls": [
                    "one real two-helper program",
                    "fresh export",
                    "renamed double-observer application",
                    "wrong premise type",
                    "export revocation cascade",
                ],
                "no_post_auto_retry": True,
            },
        )
        with running_server(ROOT) as server:
            server.recursion, server.lemma_exports, server.export_applications = (
                parents,
                exports,
                apps,
            )
            server.pump = TaskPump(
                server.store, recursion=parents, lemma_exports=exports, export_applications=apps
            )
            server.pump.thread.start()

            def send(method, route, raw=None, expected=200):
                deadline = time.monotonic() + 90
                while True:
                    status, value, _ = http(server, method, "/v1/lemma-research" + route, raw)
                    calls.append({"method": method, "route": route, "status": status})
                    save("http-calls.json", calls)
                    if status == 503 and method == "GET" and time.monotonic() < deadline:
                        time.sleep(0.3)
                        continue
                    self.assertEqual(status, expected, value)
                    return value

            def check(kind, raw, proved=True):
                plan = send("POST", "/" + kind + "/preview", raw)
                row = send("POST", "/" + kind + "/runs", confirmation(plan), 202)
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    current = send("GET", "/" + kind + "/" + row["run_id"])
                    if current["state"] not in ("QUEUED", "RUNNING"):
                        records.append({"kind": kind, "check": current})
                        save("results.json", records)
                        if proved:
                            verified(current)
                        else:
                            self.assertEqual(current["math_verdict"], "UNKNOWN")
                        return current
                    time.sleep(0.5)
                self.fail("Actual background task did not terminate")

            p = send("POST", "/propose", proposal(ROOT))
            parent = check("programs", p["candidates"][0]["request"])
            exported = check("exports", core.export_request(parent, 1))
            descriptor = send("GET", "/exports/" + exported["run_id"] + "/source")
            self.assertEqual(descriptor["formal_statement"], exported["plan"]["formal_statement"])
            self.assertNotIn("proof_data", descriptor)
            applied = check("applications", core.application(exported))
            self.assertTrue(applied["result"]["candidate"]["imports"][0]["type_matches"])
            bad = deepcopy(core.application(exported))
            bad["target"]["assumptions"] = [
                {"name": "commutes", "formula": {"eq": [{"var": "y"}, {"var": "y"}]}}
            ]
            check("applications", bad, False)
            server.pump.close()
            queued_plan = send("POST", "/programs/preview", parent["plan"]["request"])
            queued = send("POST", "/programs/runs", confirmation(queued_plan), 202)
            self.assertEqual(
                send("POST", "/programs/" + queued["run_id"] + "/cancel", {})["state"], "CANCELLED"
            )
            send(
                "POST",
                "/exports/" + exported["run_id"] + "/revoke",
                {
                    "reviewer": "HTTP withdrawal QA",
                    "reason": "Withdraw genuine exported source",
                    "confirmed": True,
                },
            )
            final = send("GET", "/applications/" + applied["run_id"] + "/export")
            self.assertEqual((final["math_verdict"], final["evidence_level"]), ("UNKNOWN", "E0"))
            self.assertTrue(final["invalidated"])
            verified(send("GET", "/programs/" + parent["run_id"]))
            save("withdrawn.json", final)
        save(
            "summary.json",
            {
                "all_passed": True,
                "complete_checks": 4,
                "expected_worker_phases": 7,
                "negative_condition_verdict": "UNKNOWN",
                "post_auto_retries": 0,
                "model_calls": 0,
            },
        )
