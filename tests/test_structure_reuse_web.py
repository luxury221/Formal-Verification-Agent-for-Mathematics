"""HTTP contracts use inert fixtures; separately opted-in integration runs real Lean."""

import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch
import uuid

import test_structure_reuse as core
from mra.domain import canonical
from mra.http_api import TaskPump
from mra.structure_research import StructureResearchStore
from mra.structure_reuse import StructureReuseStore, checked
from mra.structure_reuse_examples import example as scenario, request, var
from mra.verifier import NativeLeanBackend
from test_http_api import request as http, running_server
from test_structure_research import confirmation, create, parent

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/structure-reuse-web-20261029-01"


def body(store, raw):
    return {
        "request_id": "structure-web-" + uuid.uuid4().hex,
        "request": raw,
        "plan_fingerprint": store.preview(raw)["plan_fingerprint"],
        "reviewer": "MRA063 authored QA; not independent semantic review",
        "confirmed": True,
    }


class ContractTests(unittest.TestCase):
    def setUp(self):
        core.ContractTests.setUp(self)

    def test_catalog_preserves_context_and_unverified_sources_never_become_evidence(self):
        with (
            running_server(self.root) as server,
            patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")),
        ):
            status, value, _ = http(
                server, "GET", f"/v1/structure-reuse/graphs/{self.key}/nodes/lifted"
            )
            self.assertEqual(status, 200)
            self.assertEqual(
                value["context"]["target"], self.store.context(self.key, "lifted")[0]["target"]
            )
            self.assertEqual(value["source_candidates"], [])
            self.assertEqual(value["history"], [])
            self.assertEqual(value["math_verdict"], "UNKNOWN")
            self.assertEqual(
                http(server, "GET", f"/v1/structure-reuse/sources/node.{self.key}.power")[0], 400
            )

    def test_strict_csrf_origin_query_schema_and_duplicate_json(self):
        with running_server(self.root) as server:
            for path in (
                f"/v1/structure-reuse/graphs/{self.key}/nodes/lifted?x=1",
                "/v1/structure-reuse/sources/../../x",
            ):
                self.assertIn(http(server, "GET", path)[0], (400, 404))
            for headers in ({"X-MRA-CSRF": "wrong"}, {"Origin": "https://example.invalid"}):
                self.assertEqual(
                    http(server, "POST", "/v1/structure-reuse/preview", self.raw, headers=headers)[
                        0
                    ],
                    403,
                )
            for raw in (b'{"schema":1,"schema":2}', b"[]", b'{"schema":"old"}'):
                self.assertEqual(
                    http(server, "POST", "/v1/structure-reuse/preview", raw=raw)[0], 400
                )
            self.assertEqual(
                http(server, "POST", "/v1/structure-reuse/preview", raw=b" " * 196609)[0], 413
            )

    def test_named_idempotent_queue_cancel_revoke_export_and_worker_wake(self):
        with (
            running_server(self.root) as server,
            patch.object(
                server.structure_reuse,
                "load_source",
                return_value=(self.source, b"INERT PROTOCOL FIXTURE, NOT PROOF DATA"),
            ),
        ):
            server.pump = TaskPump(server.store, structure_reuse=server.structure_reuse)
            p = http(server, "POST", "/v1/structure-reuse/preview", self.raw)[1]
            data = {
                "request_id": "web-idempotent",
                "request": self.raw,
                "plan_fingerprint": p["plan_fingerprint"],
                "reviewer": "INERT UNIT QA",
                "confirmed": True,
            }
            self.assertEqual(
                http(server, "POST", "/v1/structure-reuse/runs", {**data, "confirmed": 1})[0], 400
            )
            first = http(server, "POST", "/v1/structure-reuse/runs", data)
            self.assertEqual(first[0], 202)
            key = first[1]["run_id"]
            self.assertTrue(server.pump.wake.is_set())
            self.assertEqual(
                http(server, "POST", "/v1/structure-reuse/runs", data)[1]["run_id"], key
            )
            self.assertEqual(
                http(server, "POST", f"/v1/structure-reuse/runs/{key}/cancel", {"extra": 1})[0], 400
            )
            self.assertEqual(
                http(server, "POST", f"/v1/structure-reuse/runs/{key}/cancel", {})[1]["state"],
                "CANCELLED",
            )
            self.assertEqual(
                http(server, "POST", "/v1/structure-reuse/runs", data)[1]["state"], "CANCELLED"
            )
            value = http(
                server,
                "POST",
                f"/v1/structure-reuse/runs/{key}/revoke",
                {"reviewer": "QA", "reason": "unit contract only", "confirmed": True},
            )[1]
            self.assertTrue(value["invalidated"])
            self.assertEqual(
                http(server, "GET", f"/v1/structure-reuse/runs/{key}/export")[1]["math_verdict"],
                "UNKNOWN",
            )

    def test_history_is_scoped_and_bounded_without_executing(self):
        with (
            running_server(self.root) as server,
            patch.object(
                server.structure_reuse,
                "load_source",
                return_value=(self.source, b"INERT UNIT ONLY"),
            ),
        ):
            for _ in range(23):
                queued = server.structure_reuse.submit(body(server.structure_reuse, self.raw))
                server.structure_reuse.cancel(queued["run_id"])
            result = http(server, "GET", f"/v1/structure-reuse/graphs/{self.key}/nodes/lifted")[1]
            self.assertEqual((result["matching_runs"], len(result["history"])), (23, 20))
            self.assertTrue(all(r["math_verdict"] == "UNKNOWN" for r in result["history"]))
            self.assertEqual(
                http(server, "GET", f"/v1/structure-reuse/graphs/{self.key}/nodes/problem")[1][
                    "history"
                ],
                [],
            )

    def test_pump_reaches_new_queue_once_and_keeps_optional_old_constructor(self):
        class Idle:
            def work_once(self):
                return {"worker_state": "IDLE"}

        class Queue:
            def __init__(self):
                self.calls = 0

            def work_once(self):
                self.calls += 1
                if self.calls == 1:
                    return {"worker_state": "EXECUTED"}
                pump.stopped.set()
                return {"worker_state": "IDLE"}

        queue = Queue()
        pump = TaskPump(Idle(), structure_reuse=queue)
        pump.thread.start()
        pump.thread.join(3)
        self.assertFalse(pump.thread.is_alive())
        self.assertEqual(queue.calls, 2)
        self.assertIsNone(TaskPump(Idle()).structure_reuse)


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "New real HTTP and sealed Lean")
class RealTests(unittest.TestCase):
    def test_background_multilevel_complete_targets_wrong_premise_cancel_and_transitive_revoke(
        self,
    ):
        area = OUT / ("real-http-" + uuid.uuid4().hex)
        area.mkdir()
        project = parent(ROOT)
        (OUT / ("real-project-" + uuid.uuid4().hex + ".json")).write_bytes(
            canonical(project) + b"\n"
        )
        graphs = StructureResearchStore(ROOT)
        graphs.base = area / "graphs"
        graphs.base.mkdir()
        graph = create(graphs, project, scenario())
        key = graph["graph_id"]
        store = StructureReuseStore(ROOT, graphs=graphs, base=area / "reuse")
        calls = []
        with running_server(ROOT) as server:
            server.structure_research, server.structure_reuse = graphs, store
            server.pump = TaskPump(server.store, structure_research=graphs, structure_reuse=store)
            server.pump.thread.start()

            def send(method, path, data=None, *, tolerate_busy=False):
                deadline = time.monotonic() + (3 if tolerate_busy else 60)
                attempt = 0
                while time.monotonic() < deadline:
                    status, value, _ = http(server, method, path, data)
                    calls.append(
                        {"method": method, "path": path, "status": status, "manual_retry": attempt}
                    )
                    (area / "http-calls.json").write_bytes(canonical(calls) + b"\n")
                    if status != 503:
                        self.assertIn(status, (200, 202), value)
                        return value
                    time.sleep(min(1, 0.2 * (attempt + 1)))
                    attempt += 1
                if tolerate_busy:
                    return None
                self.fail("Temporary contention did not clear")

            def wait(path, predicate):
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    r = send("GET", path, tolerate_busy=True)
                    if r is not None and predicate(r):
                        return r
                    time.sleep(0.6)
                self.fail("Real worker did not finish")

            for node in ("power",):
                send(
                    "POST", f"/v1/structure-research/{key}/submit", confirmation(graphs, key, node)
                )
                wait(
                    f"/v1/structure-research/{key}",
                    lambda r: r["node_states"][node]["math_verdict"] == "PROVED",
                )
            source = send("GET", f"/v1/structure-reuse/sources/node.{key}.power")
            self.assertEqual(source["request"]["definitions"], scenario()["definitions"])
            self.assertEqual(source["request"]["carriers"], scenario()["carriers"])
            self.assertEqual(source["request"]["operations"], scenario()["operations"])
            self.assertEqual(len(source["request"]["assumptions"]), 8)
            self.assertNotIn("proof_source", source)
            self.assertNotIn("data_sha256", source)

            def compose(raw, expected="PROVED"):
                p = send("POST", "/v1/structure-reuse/preview", raw)
                self.assertEqual(p["math_verdict"], "UNKNOWN")
                data = {
                    "request_id": "real-web-" + uuid.uuid4().hex,
                    "request": raw,
                    "plan_fingerprint": p["plan_fingerprint"],
                    "reviewer": "MRA063 authored real HTTP QA",
                    "confirmed": True,
                }
                row = send("POST", "/v1/structure-reuse/runs", data)
                self.assertEqual(
                    send("POST", "/v1/structure-reuse/runs", data)["run_id"], row["run_id"]
                )
                value = wait(
                    "/v1/structure-reuse/runs/" + row["run_id"],
                    lambda r: r["state"] not in ("QUEUED", "RUNNING"),
                )
                self.assertEqual(value["math_verdict"], expected, value)
                (area / (value["run_id"] + ".json")).write_bytes(canonical(value) + b"\n")
                if expected == "PROVED":
                    checked(value)
                    self.assertNotEqual(
                        value["result"]["candidate"]["pid"], value["result"]["replay"]["pid"]
                    )
                    self.assertTrue(value["result"]["replay"]["audit"]["type_matches"])
                return value

            b = compose(request(key))
            raw = request(key, "problem", "run." + b["run_id"])
            result = compose(raw)
            self.assertEqual(len(result["result"]["candidate"]["imports"]), 2)
            self.assertEqual(
                send("GET", f"/v1/structure-research/{key}")["math_verdict"], "UNKNOWN"
            )
            self.assertEqual(
                send("GET", f"/v1/structure-reuse/runs/{result['run_id']}/export")["plan"],
                result["plan"],
            )
            wrong = request(key)
            wrong["proof"]["proofs"][-1] = {"refl": var("x")}
            rejected = compose(wrong, "UNKNOWN")
            self.assertEqual(rejected["state"], "REJECTED")
            self.assertIsNone(rejected["result"]["replay"])
            self.assertEqual(
                send("GET", f"/v1/structure-reuse/runs/{result['run_id']}")["math_verdict"],
                "PROVED",
            )
            server.pump.close()
            control = send("POST", "/v1/structure-reuse/runs", body(store, request(key)))
            self.assertEqual(
                send("POST", f"/v1/structure-reuse/runs/{control['run_id']}/cancel", {})["state"],
                "CANCELLED",
            )
            send(
                "POST",
                f"/v1/structure-reuse/runs/{b['run_id']}/revoke",
                {
                    "reviewer": "HTTP QA",
                    "reason": "transitive structure composition withdrawal",
                    "confirmed": True,
                },
            )
            final = send("GET", f"/v1/structure-reuse/runs/{result['run_id']}")
            self.assertTrue(final["invalidated"])
            self.assertEqual(final["math_verdict"], "UNKNOWN")
            (area / "withdrawn.json").write_bytes(canonical(final) + b"\n")
        (area / "http-calls.json").write_bytes(canonical(calls) + b"\n")
