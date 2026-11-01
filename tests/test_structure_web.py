"""Strict structure graph HTTP contracts and a separately enabled real background worker."""

import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch
import uuid

import test_structure_research as core
from mra.domain import canonical
from mra.http_api import TaskPump
from mra.structure_research import StructureResearchStore
from mra.structure_research_examples import example
from mra.typed_reuse import checked
from mra.verifier import NativeLeanBackend
from test_http_api import request as http, running_server

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/structure-web-20261029-01"


class ContractTests(unittest.TestCase):
    def setUp(self):
        core.ContractTests.setUp(self)

    def test_assets_catalog_complete_preview_and_export_are_inert(self):
        with (
            running_server(self.root) as server,
            patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")),
        ):
            for path in (
                "/typed-research",
                "/typed-research.js",
                "/typed-research.css",
                "/structure-notation.js",
            ):
                self.assertEqual(http(server, "GET", path)[0], 200)
            status, value, _ = http(
                server,
                "GET",
                "/v1/structure-research/projects/" + self.project["project_id"] + "/main",
            )
            self.assertEqual(status, 200)
            self.assertEqual(value["example"], example())
            value = http(
                server, "POST", "/v1/structure-research/preview", self.value["plan"]["request"]
            )[1]
            self.assertEqual(value["node_plans"], self.value["plan"]["node_plans"])
            self.assertEqual(value["math_verdict"], "UNKNOWN")
            exported = http(server, "GET", "/v1/structure-research/" + self.key + "/export")[1]
            self.assertEqual(exported["plan"], self.value["plan"])
            self.assertEqual(exported["checks"], {})

    def test_strict_csrf_origin_query_body_duplicate_keys_and_oversize(self):
        raw = self.value["plan"]["request"]
        with running_server(self.root) as server:
            for headers in (
                {"Origin": "https://example.invalid"},
                {"X-MRA-CSRF": "wrong"},
                {"Sec-Fetch-Site": "cross-site"},
            ):
                self.assertEqual(
                    http(server, "POST", "/v1/structure-research/preview", raw, headers=headers)[0],
                    403,
                )
            for path in (
                "/v1/structure-research/" + self.key + "?execute=1",
                "/v1/structure-research/projects/" + self.project["project_id"] + "/main?x=1",
            ):
                self.assertEqual(http(server, "GET", path)[0], 400)
            for value in (b'{"schema":1,"schema":2}', b"[]", b'{"schema":"old"}', b'{"n":NaN}'):
                self.assertEqual(
                    http(server, "POST", "/v1/structure-research/preview", raw=value)[0], 400
                )
            self.assertEqual(
                http(server, "POST", "/v1/structure-research/preview", raw=b" " * 196609)[0], 413
            )

    def test_named_idempotent_queue_wake_cancel_withdraw_and_unknown_export(self):
        with running_server(self.root) as server:
            server.pump = TaskPump(server.store, structure_research=server.structure_research)
            p = http(
                server,
                "POST",
                f"/v1/structure-research/{self.key}/node-preview",
                {"node_id": "unit"},
            )[1]
            raw = {
                "request_id": core.rid(),
                "node_id": "unit",
                "execution_fingerprint": p["execution_fingerprint"],
                "reviewer": "MRA061 inert HTTP contract reviewer",
                "confirmed": True,
            }
            for changes in (
                {"confirmed": 1},
                {"execution_fingerprint": "0" * 64},
                {"reviewer": " "},
            ):
                self.assertEqual(
                    http(
                        server,
                        "POST",
                        f"/v1/structure-research/{self.key}/submit",
                        {**raw, **changes},
                    )[0],
                    400,
                )
            status, value, _ = http(
                server, "POST", f"/v1/structure-research/{self.key}/submit", raw
            )
            self.assertEqual(status, 202)
            self.assertTrue(server.pump.wake.is_set())
            self.assertEqual(
                http(server, "POST", f"/v1/structure-research/{self.key}/submit", raw)[1][
                    "attempt_id"
                ],
                value["attempt_id"],
            )
            self.assertEqual(
                http(
                    server,
                    "POST",
                    f"/v1/structure-research/{self.key}/submit",
                    {**raw, "reviewer": "another"},
                )[0],
                400,
            )
            cancelled = http(
                server,
                "POST",
                f"/v1/structure-research/{self.key}/cancel",
                {"attempt_id": value["attempt_id"]},
            )[1]
            self.assertEqual(cancelled["attempts"][0]["state"], "CANCELLED")
            withdrawn = http(
                server,
                "POST",
                f"/v1/structure-research/{self.key}/withdraw",
                {
                    "node_id": "unit",
                    "reviewer": "QA",
                    "reason": "Inert lifecycle",
                    "confirmed": True,
                },
            )[1]
            self.assertTrue(withdrawn["attempts"][0]["invalidated"])
            self.assertEqual(
                http(server, "GET", f"/v1/structure-research/{self.key}/export")[1]["math_verdict"],
                "UNKNOWN",
            )

    def test_protocol_and_project_contexts_are_not_interchangeable(self):
        with running_server(self.root) as server:
            self.assertEqual(http(server, "GET", f"/v1/typed-research/{self.key}")[0], 400)
            self.assertEqual(
                http(server, "GET", f"/v1/structure-research/typedflow-{'0' * 32}")[0], 400
            )
            catalog = http(
                server, "GET", "/v1/typed-research/projects/" + self.project["project_id"] + "/main"
            )[1]
            self.assertEqual(catalog["graphs"], [])
            self.assertEqual(
                http(
                    server,
                    "POST",
                    f"/v1/structure-research/{self.key}/node-preview",
                    {"node_id": "problem"},
                )[0],
                400,
            )
            self.assertEqual(
                http(server, "POST", f"/v1/structure-research/{self.key}/export", {})[0], 404
            )

    def test_pump_reaches_structure_queue_and_old_constructor_stays_optional(self):
        class Idle:
            def work_once(self):
                return {"worker_state": "IDLE"}

        class Queue:
            calls = 0

            def work_once(self):
                self.calls += 1
                if self.calls == 1:
                    return {"worker_state": "EXECUTED"}
                pump.stopped.set()
                return {"worker_state": "IDLE"}

        queue = Queue()
        pump = TaskPump(Idle(), structure_research=queue)
        pump.thread.start()
        pump.thread.join(3)
        self.assertFalse(pump.thread.is_alive())
        self.assertEqual(queue.calls, 2)
        self.assertIsNone(TaskPump(Idle()).structure_research)


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "New real structure HTTP background execution"
)
class RealTests(unittest.TestCase):
    def test_full_graph_wrong_induction_export_cancel_and_withdraw_via_actual_background_queue(
        self,
    ):
        area = OUT / ("real-http-" + uuid.uuid4().hex)
        area.mkdir(parents=True)
        project = core.parent(ROOT)
        graphs = StructureResearchStore(ROOT, base=area / "graphs")
        (area / "fixture.json").write_bytes(
            canonical({"project_id": project["project_id"], "base": str(graphs.base)}) + b"\n"
        )
        calls = []
        with running_server(ROOT) as server:
            server.structure_research = graphs
            server.pump = TaskPump(server.store, structure_research=graphs)
            server.pump.thread.start()

            def send(method, path, data=None, expected=200):
                deadline = time.monotonic() + 60
                count = 0
                while time.monotonic() < deadline:
                    status, value, _ = http(server, method, path, data)
                    calls.append(
                        {
                            "method": method,
                            "path": path,
                            "status": status,
                            "manual_same_body_retry": count,
                        }
                    )
                    (area / "http-calls.json").write_bytes(canonical(calls) + b"\n")
                    if status != 503:
                        self.assertEqual(status, expected, value)
                        return value
                    count += 1
                    time.sleep(0.3)
                self.fail("HTTP contention did not clear")

            def save(raw):
                request = {"project_id": project["project_id"], "branch_id": "main", **raw}
                plan = send("POST", "/v1/structure-research/preview", request)
                return send(
                    "POST",
                    "/v1/structure-research",
                    {
                        "request_id": core.rid(),
                        "request": request,
                        "plan_fingerprint": plan["plan_fingerprint"],
                    },
                    201,
                )["graph_id"]

            def node(key, name):
                p = send("POST", f"/v1/structure-research/{key}/node-preview", {"node_id": name})
                raw = {
                    "request_id": core.rid(),
                    "node_id": name,
                    "execution_fingerprint": p["execution_fingerprint"],
                    "reviewer": "MRA061 authored real HTTP semantic QA",
                    "confirmed": True,
                }
                first = send("POST", f"/v1/structure-research/{key}/submit", raw, 202)
                self.assertEqual(
                    send("POST", f"/v1/structure-research/{key}/submit", raw, 202)["attempt_id"],
                    first["attempt_id"],
                )
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    value = send("GET", f"/v1/structure-research/{key}")
                    attempt = next(
                        a for a in value["attempts"] if a["attempt_id"] == first["attempt_id"]
                    )
                    if attempt["state"] not in ("QUEUED", "RUNNING"):
                        (area / (first["attempt_id"] + ".json")).write_bytes(
                            canonical(value) + b"\n"
                        )
                        return value, attempt
                    time.sleep(0.6)
                self.fail("Real background graph did not finish")

            key = save(example())
            for name in ("unit", "product", "problem"):
                value, attempt = node(key, name)
                checked(value["checks"][attempt["attempt_id"]])
                self.assertEqual(
                    value["math_verdict"], "PROVED" if name == "problem" else "UNKNOWN"
                )
            exported = send("GET", f"/v1/structure-research/{key}/export")
            self.assertEqual(exported["plan"], value["plan"])
            self.assertEqual(exported["math_verdict"], "PROVED")
            bad = core.one_node()
            bad["nodes"][0]["target"]["proof"]["step"] = {"method": "rfl"}
            badkey = save(bad)
            value, attempt = node(badkey, "problem")
            self.assertEqual((value["math_verdict"], attempt["state"]), ("UNKNOWN", "REJECTED"))
            self.assertIsNone(value["checks"][attempt["attempt_id"]]["result"]["replay"])
            server.pump.close()
            cancelkey = save(core.one_node())
            p = send(
                "POST", f"/v1/structure-research/{cancelkey}/node-preview", {"node_id": "problem"}
            )
            a = send(
                "POST",
                f"/v1/structure-research/{cancelkey}/submit",
                {
                    "request_id": core.rid(),
                    "node_id": "problem",
                    "execution_fingerprint": p["execution_fingerprint"],
                    "reviewer": "QA",
                    "confirmed": True,
                },
                202,
            )
            value = send(
                "POST",
                f"/v1/structure-research/{cancelkey}/cancel",
                {"attempt_id": a["attempt_id"]},
            )
            self.assertEqual(
                (value["math_verdict"], value["attempts"][0]["state"]), ("UNKNOWN", "CANCELLED")
            )
            value = send(
                "POST",
                f"/v1/structure-research/{key}/withdraw",
                {
                    "node_id": "unit",
                    "reviewer": "HTTP QA",
                    "reason": "Real precursor withdrawal",
                    "confirmed": True,
                },
            )
            self.assertEqual(value["math_verdict"], "UNKNOWN")
            self.assertTrue(all(a["invalidated"] for a in value["attempts"]))
            (area / "withdrawn.json").write_bytes(canonical(value) + b"\n")
        (area / "summary.json").write_bytes(
            canonical(
                {
                    "all_passed": True,
                    "graph_id": key,
                    "wrong_graph_id": badkey,
                    "cancelled_graph_id": cancelkey,
                    "complete_checks": 4,
                    "real_workers": 7,
                }
            )
            + b"\n"
        )
