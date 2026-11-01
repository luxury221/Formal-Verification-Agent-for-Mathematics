"""Actual loopback HTTP, durable state, and asynchronous real Lean verification."""

from contextlib import contextmanager
import http.client
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
import uuid

from mra.certificates import MODE, REFERENCE_MODE
from mra.http_api import MAX_BODY, WorkbenchServer
from mra.tasks import TaskStore
from test_tasks import copy_inputs

ROOT = Path(__file__).resolve().parents[1]


def copy_web_inputs(root):
    copy_inputs(root)
    shutil.copytree(ROOT / "mra/web", root / "mra/web")
    shutil.copytree(ROOT / "evals/certificates", root / "evals/certificates")


@contextmanager
def running_server(root, *, worker=False):
    server = WorkbenchServer(root, 0, start_worker=worker)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def request(server, method, path, data=None, *, headers=None, raw=None):
    connection = http.client.HTTPConnection(*server.server_address, timeout=15)
    defaults = {}
    if method == "POST":
        defaults = {
            "Origin": server.origin,
            "X-MRA-CSRF": server.csrf,
            "Content-Type": "application/json",
        }
    if headers is not None:
        defaults.update(headers)
    body = json.dumps(data).encode() if raw is None and data is not None else raw
    try:
        connection.request(method, path, body=body, headers=defaults)
        response = connection.getresponse()
        content = response.read()
        payload = (
            json.loads(content)
            if response.getheader("Content-Type", "").startswith("application/json")
            else content
        )
        return response.status, payload, dict(response.getheaders())
    finally:
        connection.close()


def submission(server, *, case_id="nat_add_zero", mode=MODE, proof=None, align=False, timeout=30):
    target = next(target for target in server.catalog() if target["case_id"] == case_id)
    return {
        "request_id": "http-test-" + uuid.uuid4().hex,
        "case_id": case_id,
        "input_mode": mode,
        "certificate": (
            json.dumps(proof)
            if proof is not None
            else json.dumps(target["samples"][0]["certificate"])
        )
        if mode == MODE
        else None,
        "timeout_seconds": timeout,
        "claim_fingerprint": target["certificate_header"]["claim_fingerprint"],
        "formalization_fingerprint": target["certificate_header"]["formalization_fingerprint"],
        "alignment": {"reviewer": "repository-http-test-author", "confirmed": True}
        if align
        else None,
    }


def wait_terminal(server, task_id):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        status, task, _ = request(server, "GET", "/v1/tasks/" + task_id)
        if status != 200:
            raise AssertionError((status, task))
        if task["state"] not in ("QUEUED", "RUNNING"):
            return task
        time.sleep(0.03)
    raise AssertionError("HTTP task did not reach a terminal state")


class WorkbenchHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_web_inputs(self.root)
        context = running_server(self.root)
        self.server = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def post(self, data):
        return request(self.server, "POST", "/v1/tasks", data)

    def test_bootstrap_and_fixed_assets_work_without_lean(self):
        status, data, headers = request(self.server, "GET", "/v1/session")
        self.assertEqual(status, 200)
        self.assertEqual(data["worker"]["state"], "EXTERNAL")
        self.assertEqual(
            {target["case_id"] for target in data["targets"]},
            {"nat_add_zero", "false_all_nat_zero"},
        )
        self.assertTrue(
            all(target["evidence_level"] == "E0_CANDIDATE" for target in data["targets"])
        )
        self.assertEqual(headers["Cache-Control"], "no-store")
        for route in ("/", "/app.js", "/style.css"):
            code, content, response_headers = request(self.server, "GET", route)
            self.assertEqual(code, 200)
            self.assertTrue(content)
            self.assertIn("frame-ancestors 'none'", response_headers["Content-Security-Policy"])
        self.assertFalse((self.root / "artifacts/runs").exists())

    def test_host_origin_fetch_site_and_csrf_fail_closed(self):
        for headers in (
            {"Host": "evil.example"},
            {"Origin": "https://evil.example"},
            {"Sec-Fetch-Site": "cross-site"},
        ):
            with self.subTest(headers=headers):
                self.assertEqual(
                    request(self.server, "GET", "/v1/session", headers=headers)[0], 403
                )
        body = submission(self.server)
        for headers in (
            {"Origin": ""},
            {"X-MRA-CSRF": "wrong"},
            {"Origin": "http://localhost:8765"},
        ):
            self.assertEqual(
                request(self.server, "POST", "/v1/tasks", body, headers=headers)[0], 403
            )
        self.assertEqual(self.server.store.list_tasks(), [])

    def test_json_type_byte_budget_and_transfer_encoding_are_enforced(self):
        for headers, raw, expected in (
            ({"Content-Type": "text/plain"}, b"{}", 415),
            ({}, b" " * (MAX_BODY + 1), 413),
            ({"Transfer-Encoding": "chunked"}, b"{}", 400),
            ({}, b'{"request_id":1,"request_id":2}', 400),
            ({}, b"\xff", 400),
            ({}, b"[]", 400),
            ({}, b'{"value":NaN}', 400),
        ):
            status, response, _ = request(
                self.server, "POST", "/v1/tasks", headers=headers, raw=raw
            )
            with self.subTest(raw=raw[:40]):
                self.assertEqual(status, expected)
                self.assertNotIn("Traceback", response["error"])
        self.assertEqual(self.server.store.list_tasks(), [])

    def test_duplicate_host_and_content_lengths_cannot_be_smuggled(self):
        for name, value, expected in (
            ("Host", self.server.authority, 403),
            ("Content-Length", "2", 411),
        ):
            connection = http.client.HTTPConnection(*self.server.server_address, timeout=10)
            try:
                connection.putrequest("POST", "/v1/tasks")
                for key, item in (
                    ("Origin", self.server.origin),
                    ("X-MRA-CSRF", self.server.csrf),
                    ("Content-Type", "application/json"),
                    ("Content-Length", "2"),
                    (name, value),
                ):
                    connection.putheader(key, item)
                connection.endheaders(b"{}")
                response = connection.getresponse()
                self.assertEqual(response.status, expected)
                response.read()
            finally:
                connection.close()

    def test_paths_are_routes_and_never_filesystem_requests(self):
        for path in (
            "/../AGENTS.md",
            "/artifacts/tasks/tasks.sqlite3",
            "/%2e%2e/AGENTS.md",
            "/v1/tasks?path=/etc/passwd",
            "/v1/tasks/id/files/../../AGENTS.md",
        ):
            self.assertEqual(request(self.server, "GET", path)[0], 404)
        self.assertFalse((self.root / "artifacts/runs").exists())

    def test_stale_target_and_unconfirmed_alignment_never_queue(self):
        valid = submission(self.server)
        for change in (
            {"claim_fingerprint": "0" * 64},
            {"formalization_fingerprint": "0" * 64},
            {"case_id": "sandbox_boundary"},
            {"alignment": {"reviewer": "x", "confirmed": False}},
            {"alignment": {"reviewer": "", "confirmed": True}},
            {"alignment": {"reviewer": "x", "confirmed": 1}},
            {"math_verdict": "PROVED"},
        ):
            with self.subTest(change=change):
                self.assertEqual(self.post({**valid, **change})[0], 400)
        self.assertEqual(self.server.store.list_tasks(), [])

    def test_raw_certificate_duplicate_keys_source_and_disabled_modes_are_rejected(self):
        valid = submission(self.server)
        for change in (
            {"certificate": '{"proof":1,"proof":2}'},
            {"certificate": '{"source":"by rfl"}'},
            {"certificate": {}},
            {"input_mode": "arbitrary-lean-source"},
            {"input_mode": REFERENCE_MODE},
        ):
            with self.subTest(change=change):
                self.assertEqual(self.post({**valid, **change})[0], 400)
        self.assertEqual(self.server.store.list_tasks(), [])

    def test_canonical_submission_and_mode_conflicts_use_durable_idempotency(self):
        body = submission(self.server)
        status, first, _ = self.post(body)
        self.assertEqual(status, 202)
        body["certificate"] = json.dumps(json.loads(body["certificate"]), indent=4)
        self.assertEqual(self.post(body)[1], first)
        self.assertEqual(first["evidence_level"], "E0_CANDIDATE")
        changed = {**body, "input_mode": REFERENCE_MODE, "certificate": None}
        self.assertEqual(self.post(changed)[0], 400)
        self.assertEqual(TaskStore(self.root).report(first["task_id"]), first)

    def test_cancelled_task_stays_cancelled_on_resubmission_and_has_no_evidence(self):
        body = submission(self.server)
        task = self.post(body)[1]
        cancelled = request(self.server, "POST", f"/v1/tasks/{task['task_id']}/cancel", {})[1]
        self.assertEqual(cancelled["state"], "CANCELLED")
        self.assertEqual(self.post(body)[1], cancelled)
        evidence = request(self.server, "GET", f"/v1/tasks/{task['task_id']}/evidence")[1]
        self.assertFalse(evidence["available"])
        self.assertFalse(evidence["fresh_attestation"])

    def test_history_lists_only_metadata_and_checks_list_limits(self):
        task = self.post(submission(self.server))[1]
        response = request(self.server, "GET", "/v1/tasks")[1]
        self.assertEqual(response["tasks"][0]["task_id"], task["task_id"])
        self.assertNotIn("math_verdict", response["tasks"][0])
        self.assertNotIn("certificate", response["tasks"][0])
        for limit in (0, 101, True):
            with self.assertRaises(ValueError):
                self.server.store.list_tasks(limit)

    def test_metadata_only_runtime_cannot_publish_http_evidence(self):
        with running_server(self.root, worker=True) as server:
            task_id = request(server, "POST", "/v1/tasks", submission(server))[1]["task_id"]
            task = wait_terminal(server, task_id)
            self.assertEqual(task["state"], "FAILED")
            self.assertEqual(task["last_error_code"], "POLICY_BLOCKED")
            self.assertEqual(task["evidence_level"], "E0_CANDIDATE")
        self.assertFalse((self.root / "artifacts/runs").exists())

    def test_server_restart_recovers_interrupted_state_without_promoting_or_retrying(self):
        task_id = self.post(submission(self.server))[1]["task_id"]
        with self.server.store.worker_lock():
            self.server.store._claim(task_id)
        with running_server(self.root, worker=True) as server:
            task = wait_terminal(server, task_id)
            self.assertEqual(task["state"], "FAILED")
            self.assertEqual(task["last_error_code"], "INTERRUPTED")
            self.assertEqual(task["math_verdict"], "UNKNOWN")
            self.assertEqual(len(task["attempts"]), 1)
        self.assertFalse((self.root / "artifacts/runs").exists())

    def test_port_validation_does_not_allow_remote_bind_configuration(self):
        for port in (-1, 65536, True):
            with self.assertRaises(ValueError):
                WorkbenchServer(self.root, port)
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_occupied_port_preserves_bind_error_before_worker_start(self):
        with self.assertRaises(OSError):
            WorkbenchServer(self.root, self.server.server_address[1])


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "requires pinned real Lean")
class WorkbenchRealLeanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = running_server(ROOT, worker=True)
        cls.server = cls.context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)

    def submit(self, **options):
        status, task, _ = request(
            self.server, "POST", "/v1/tasks", submission(self.server, **options)
        )
        self.assertEqual(status, 202, task)
        return wait_terminal(self.server, task["task_id"])

    def test_e3_then_new_named_check_reaches_e4_and_tampering_suppresses_old_evidence(self):
        e3 = self.submit()
        self.assertEqual(
            (e3["math_verdict"], e3["evidence_level"]), ("UNKNOWN", "E3_KERNEL_CHECKED"), e3
        )
        evidence = request(self.server, "GET", f"/v1/tasks/{e3['task_id']}/evidence")[1]
        self.assertTrue(evidence["available"])
        payload = evidence["receipt"]["payload"]
        self.assertNotEqual(payload["candidate_pid"], payload["replay"]["pid"])
        self.assertFalse(evidence["fresh_attestation"])
        e4 = self.submit(mode=REFERENCE_MODE, align=True)
        self.assertEqual(
            (e4["math_verdict"], e4["evidence_level"]), ("PROVED", "E4_ALIGNED_AND_VERIFIED"), e4
        )
        self.assertNotEqual(e3["task_id"], e4["task_id"])
        self.assertEqual(
            request(self.server, "GET", f"/v1/tasks/{e3['task_id']}")[1]["math_verdict"], "UNKNOWN"
        )
        path = Path(e3["attempts"][0]["artifact_directory"]) / "input/certificate.json"
        path.write_text("{}")
        changed = request(self.server, "GET", f"/v1/tasks/{e3['task_id']}")[1]
        self.assertEqual(changed["artifact_status"], "CHANGED_OR_MISSING")
        self.assertEqual(changed["evidence_level"], "E0_CANDIDATE")
        self.assertFalse(
            request(self.server, "GET", f"/v1/tasks/{e3['task_id']}/evidence")[1]["available"]
        )

    def test_complete_negation_certificate_is_actually_refuted_with_alignment(self):
        task = self.submit(case_id="false_all_nat_zero", align=True)
        self.assertEqual(
            (task["math_verdict"], task["evidence_level"]),
            ("REFUTED", "E4_ALIGNED_AND_VERIFIED"),
            task,
        )

    def test_wrong_proof_is_e2_unknown_and_cannot_be_retried_as_a_counterexample(self):
        certificate = self.server.catalog()[0]["samples"][0]["certificate"]
        task = self.submit(proof={**certificate, "proof": {"op": "nat", "value": 0}}, align=True)
        self.assertEqual(
            (task["state"], task["math_verdict"], task["evidence_level"]),
            ("COMPLETED", "UNKNOWN", "E2_FORMALIZED"),
            task,
        )
        self.assertEqual(
            request(
                self.server,
                "POST",
                f"/v1/tasks/{task['task_id']}/retry",
                {
                    "attempt_id": task["latest_attempt_id"],
                    "request_id": "http-wrong-retry-" + uuid.uuid4().hex,
                },
            )[0],
            400,
        )

    def test_timeout_retry_keeps_budget_and_certificate_with_new_artifacts(self):
        first = self.submit(timeout=0.001)
        self.assertEqual(first["run_status"], "TIMEOUT", first)
        response = request(
            self.server,
            "POST",
            f"/v1/tasks/{first['task_id']}/retry",
            {
                "attempt_id": first["latest_attempt_id"],
                "request_id": "http-retry-" + uuid.uuid4().hex,
            },
        )
        self.assertEqual(response[0], 200)
        second = wait_terminal(self.server, first["task_id"])
        self.assertEqual(second["run_status"], "TIMEOUT", second)
        self.assertEqual(second["request"], first["request"])
        self.assertEqual(len(second["attempts"]), 2)
        self.assertNotEqual(*[item["artifact_directory"] for item in second["attempts"]])

    def test_running_checker_can_be_cancelled_through_http(self):
        task = request(self.server, "POST", "/v1/tasks", submission(self.server))[1]
        task_id = task["task_id"]
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            task = request(self.server, "GET", "/v1/tasks/" + task_id)[1]
            if any(event["payload"].get("stage") == "LEAN_RUNNING" for event in task["events"]):
                break
            self.assertIn(task["state"], ("QUEUED", "RUNNING"))
            time.sleep(0.01)
        else:
            self.fail("Real checker did not reach its running checkpoint")
        self.assertEqual(request(self.server, "POST", f"/v1/tasks/{task_id}/cancel", {})[0], 200)
        task = wait_terminal(self.server, task_id)
        self.assertEqual(
            (task["state"], task["math_verdict"], task["evidence_level"]),
            ("CANCELLED", "UNKNOWN", "E0_CANDIDATE"),
            task,
        )
        self.assertFalse(
            request(self.server, "GET", f"/v1/tasks/{task_id}/evidence")[1]["available"]
        )


if __name__ == "__main__":
    unittest.main()
