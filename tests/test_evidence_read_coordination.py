"""Busy-response contracts use injected errors; real reads audit existing sealed Lean evidence."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from mra.research_flow import lease
from mra.structure_reuse import checked
from mra.tasks import WorkerBusy
from test_http_api import copy_web_inputs, request, running_server

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/evidence-read-coordination-20261029-01"
GRAPH = "structureflow-ac561b70d3744981a50f49a92c248722"
RUN = "mathlib-2f402a17f32c46d8847abeab5f260135"


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_web_inputs(self.root)

    def test_busy_signal_is_explicit_for_reads_and_never_authorizes_post_retry(self):
        with running_server(self.root) as server:
            with patch.object(
                server.structure_research, "report", side_effect=WorkerBusy("INERT LOCK ERROR")
            ):
                status, value, headers = request(server, "GET", "/v1/structure-research/" + GRAPH)
            self.assertEqual(status, 503)
            self.assertEqual(value["code"], "WORKER_BUSY")
            self.assertIs(value["retryable_read"], True)
            self.assertEqual(headers["Retry-After"], "1")
            self.assertEqual(headers["Cache-Control"], "no-store")
            with patch.object(
                server.structure_reuse, "preview", side_effect=WorkerBusy("INERT LOCK ERROR")
            ):
                status, value, headers = request(server, "POST", "/v1/structure-reuse/preview", {})
            self.assertEqual(status, 503)
            self.assertIs(value["retryable_read"], False)
            self.assertEqual(server.store.list_tasks(), [])

    def test_storage_faults_are_not_misreported_as_worker_lock(self):
        with running_server(self.root) as server:
            for error in (OSError("INERT READ ERROR"), RuntimeError("INERT STATE ERROR")):
                with patch.object(server.structure_research, "report", side_effect=error):
                    status, value, headers = request(
                        server, "GET", "/v1/structure-research/" + GRAPH
                    )
                self.assertEqual(status, 503)
                self.assertNotIn("code", value)
                self.assertNotIn("Retry-After", headers)

    def test_origin_and_input_errors_keep_their_original_status(self):
        with running_server(self.root) as server:
            with patch.object(
                server.structure_reuse, "preview", side_effect=AssertionError("must not dispatch")
            ):
                status, value, _ = request(
                    server,
                    "POST",
                    "/v1/structure-reuse/preview",
                    {},
                    headers={"X-MRA-CSRF": "wrong"},
                )
            self.assertEqual(status, 403)
            self.assertNotIn("retryable_read", value)
            self.assertEqual(
                request(server, "GET", "/v1/structure-research/" + GRAPH + "?extra=1")[0], 400
            )


@unittest.skipUnless(
    os.environ.get("MRA_REAL_EVIDENCE_READ") == "1", "Explicit existing real proof-data re-audit"
)
class RealEvidenceReadTests(unittest.TestCase):
    def test_actual_record_lease_returns_busy_then_reaudits_original_complete_e4(self):
        area = OUT / ("real-read-" + uuid.uuid4().hex)
        area.mkdir()
        with running_server(ROOT) as server:
            graph = server.structure_research.report(GRAPH)
            before = server.structure_reuse.report(RUN)
            checked(before)
            calls = []
            with lease(server.structure_research.directory(GRAPH)):
                for route in ("/v1/structure-research/" + GRAPH, "/v1/structure-reuse/runs/" + RUN):
                    status, value, headers = request(server, "GET", route)
                    calls.append(
                        {
                            "method": "GET",
                            "route": route,
                            "status": status,
                            "body": value,
                            "retry_after": headers.get("Retry-After"),
                        }
                    )
                    self.assertEqual(
                        (status, value["code"], value["retryable_read"]), (503, "WORKER_BUSY", True)
                    )
                status, value, headers = request(
                    server, "POST", "/v1/structure-reuse/preview", before["plan"]["request"]
                )
                self.assertEqual((status, value["retryable_read"]), (503, False))
                calls.append(
                    {
                        "method": "POST",
                        "route": "preview only; no math submission",
                        "status": status,
                        "body": value,
                    }
                )
            after_graph = request(server, "GET", "/v1/structure-research/" + GRAPH)
            after = request(server, "GET", "/v1/structure-reuse/runs/" + RUN)
            self.assertEqual(after_graph[0], 200)
            self.assertEqual(after[0], 200)
            checked(after[1])
            self.assertEqual(graph, after_graph[1])
            self.assertEqual(before, after[1])
            (area / "graph.json").write_text(
                json.dumps(after_graph[1], ensure_ascii=False, indent=2) + "\n"
            )
            (area / "composition.json").write_text(
                json.dumps(after[1], ensure_ascii=False, indent=2) + "\n"
            )
            (area / "http-calls.json").write_text(
                json.dumps(calls, ensure_ascii=False, indent=2) + "\n"
            )
            (area / "summary.json").write_text(
                json.dumps(
                    {
                        "all_passed": True,
                        "graph_id": GRAPH,
                        "run_id": RUN,
                        "new_mathematical_submissions": 0,
                        "new_lean_executions": 0,
                        "model_calls": 0,
                        "full_original_report_unchanged": True,
                        "actual_lock_busy_reads": 2,
                    },
                    indent=2,
                )
                + "\n"
            )
