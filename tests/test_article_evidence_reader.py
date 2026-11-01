"""Actual process/pipe/lease software fixtures; never call a real provider or Lean."""

import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from mra.article_evidence_reader import (
    ArticleEvidenceReader,
    ArticleEvidenceReaderError,
    ArticleEvidenceReadCancelled,
    ArticleEvidenceReadTimeout,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_OUTPUT_BYTES,
    SCHEMA,
    _run_command,
)
from mra.tasks import WorkerBusy

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "mra/article_evidence_reader_worker.py"


def wait_until(fn, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return
        time.sleep(0.01)
    raise AssertionError("Fixture condition did not become true")


def running(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


@unittest.skipUnless(sys.platform == "linux", "Linux process-group and flock contract")
class ArticleEvidenceReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "artifacts", prefix="evidence-reader-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.request = {
            "schema": SCHEMA,
            "root": str(self.root),
            "backend": "proof",
            "graph_id": "fixture-graph",
            "timeout_seconds": 2,
            "lifecycle_lock_fd": None,
        }

    def fixture(self, body, *, timeout=2, cancel=None, limit=MAX_OUTPUT_BYTES, fd=None):
        prefix = (
            "import json,os,sys,time,signal,subprocess\n"
            "from pathlib import Path\n"
            "request=json.loads(sys.stdin.buffer.read())\n"
            "envelope={'schema':request['schema'],'backend':request['backend'],"
            "'graph_id':request['graph_id'],'status':'RETURNED','report':{'fixture':True},'error':None}\n"
        )
        return _run_command(
            [sys.executable, "-I", "-S", "-c", prefix + body],
            self.request,
            timeout_seconds=timeout,
            cancel_requested=cancel,
            lifecycle_lock_fd=fd,
            max_output_bytes=limit,
        )

    def worker_command(self, provider_body):
        # Only this private test harness patches the fixed original import. No
        # production request or public reader option accepts a command/module.
        script = (
            "import sys,runpy,os,time,signal,subprocess,threading\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0,{str(ROOT)!r})\n"
            "import mra.article_audit as audit\n"
            "from mra.tasks import WorkerBusy\n"
            "def fake(root,backend,graph_id):\n"
            + "\n".join(" " + line for line in provider_body.splitlines())
            + "\n"
            "audit.backend_report=fake\n"
            f"runpy.run_path({str(WORKER)!r},run_name='__main__')\n"
        )
        return [sys.executable, "-I", "-S", "-c", script]

    def test_complete_unicode_report_and_fixed_worker_preserve_all_fields(self):
        report = {
            "invalidated": False,
            "node_states": {"root": {"math_verdict": "UNKNOWN"}},
            "complete_target": "∀ x : ℝ, x ^ 2 ≥ 0\n" * 5000,
            "original": '完整原文 \\(\\sum_i x_i^2\\) <tag> & "quoted"',
            "nested": {"proof": [None, True, False, 1, 0.25, "未截断"]},
        }
        command = self.worker_command("print('inert diagnostic')\nreturn " + repr(report))
        result = _run_command(command, self.request, timeout_seconds=3)
        self.assertEqual(result, report)
        self.assertEqual(DEFAULT_TIMEOUT_SECONDS, 300)
        self.assertEqual(MAX_OUTPUT_BYTES, 16 * 1024 * 1024)

    def test_worker_busy_has_one_fixed_code_and_other_failures_are_errors(self):
        with self.assertRaises(WorkerBusy):
            _run_command(self.worker_command("raise WorkerBusy('fixture locked')"), self.request)
        with self.assertRaisesRegex(ArticleEvidenceReaderError, "fixture unavailable"):
            _run_command(self.worker_command("raise OSError('fixture unavailable')"), self.request)
        for status, code in (("BUSY", "OTHER"), ("ERROR", "WORKER_BUSY")):
            with (
                self.subTest(status=status, code=code),
                self.assertRaises(ArticleEvidenceReaderError),
            ):
                self.fixture(
                    f"envelope.update(status={status!r},report=None,error={{'code':{code!r},'message':'fixture'}})\n"
                    "sys.stdout.write(json.dumps(envelope))"
                )

    def test_invalid_utf8_duplicate_keys_constants_multiple_envelopes_and_crashes_rejected(self):
        bodies = [
            "os._exit(0)",
            "os._exit(7)",
            "sys.stdout.buffer.write(b'\\xff')",
            "sys.stdout.write(json.dumps(envelope)[:-1])",
            "sys.stdout.write(json.dumps(envelope)*2)",
            'sys.stdout.write(json.dumps(envelope).replace(\'"fixture": true\',\'"fixture": true,"fixture": false\'))',
            "envelope['report']={'x':float('nan')};sys.stdout.write(json.dumps(envelope))",
            "sys.stdout.write(json.dumps(envelope).replace('true','1e999'))",
            "envelope['graph_id']='different';sys.stdout.write(json.dumps(envelope))",
            "envelope['report']={'x':chr(0xd800)};sys.stdout.write(json.dumps(envelope))",
        ]
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(ArticleEvidenceReaderError):
                self.fixture(body)

    def test_json_depth_and_value_count_are_bounded_without_partial_result(self):
        bodies = [
            "envelope['report']={'x':[0]*500001};sys.stdout.write(json.dumps(envelope))",
            "value=0\nfor _ in range(110):value=[value]\nenvelope['report']={'x':value};sys.stdout.write(json.dumps(envelope))",
        ]
        for body in bodies:
            with (
                self.subTest(body=body),
                self.assertRaisesRegex(ArticleEvidenceReaderError, "structure"),
            ):
                self.fixture(body)

    def test_child_validates_request_before_the_inert_provider_is_called(self):
        marker = self.root / "provider-must-not-run"
        command = self.worker_command(f"Path({str(marker)!r}).write_text('bad')\nreturn {{}}")
        self.request["backend"] = "arbitrary"
        with self.assertRaises(ArticleEvidenceReaderError):
            _run_command(command, self.request)
        self.assertFalse(marker.exists())

    def test_output_limit_is_streamed_and_stderr_is_bounded(self):
        for stream, size in (("stdout", MAX_OUTPUT_BYTES + 1), ("stderr", 65537)):
            with (
                self.subTest(stream=stream),
                self.assertRaises(ArticleEvidenceReaderError) as error,
            ):
                self.fixture(
                    f"sys.{stream}.buffer.write(b'x'*{size});sys.{stream}.flush();time.sleep(60)"
                )
            self.assertEqual(error.exception.code, "RESULT_LIMIT")

    def test_cancel_before_start_never_spawns_and_callback_error_cleans_process(self):
        with patch("mra.article_evidence_reader.subprocess.Popen") as launch:
            with self.assertRaises(ArticleEvidenceReadCancelled):
                self.fixture("time.sleep(60)", cancel=lambda: True)
            launch.assert_not_called()
        marker = self.root / "callback-pid"

        def fail_after_start():
            if marker.exists():
                raise ValueError("fixture callback failed")
            return False

        with self.assertRaisesRegex(ValueError, "fixture callback failed"):
            self.fixture(
                f"Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(60)",
                cancel=fail_after_start,
            )
        pid = int(marker.read_text())
        self.assertFalse(running(pid))
        with self.assertRaises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)

    def test_inflight_cancel_and_timeout_kill_group_and_reap_leader(self):
        for cancel in (False, True):
            marker = self.root / ("cancel" if cancel else "timeout")
            body = (
                "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
                f"Path({str(marker)!r}).write_text(str(os.getpid())+' '+str(child.pid))\n"
                "time.sleep(60)"
            )
            started = time.monotonic()
            with (
                self.subTest(cancel=cancel),
                self.assertRaises(
                    ArticleEvidenceReadCancelled if cancel else ArticleEvidenceReadTimeout
                ),
            ):
                self.fixture(
                    body, timeout=0.3, cancel=(lambda: marker.exists()) if cancel else None
                )
            self.assertLess(time.monotonic() - started, 2)
            leader, descendant = map(int, marker.read_text().split())
            wait_until(lambda: not running(descendant))
            self.assertFalse(running(leader))
            with self.assertRaises(ChildProcessError):
                os.waitpid(leader, os.WNOHANG)

    def test_exited_leader_leftover_descendant_and_parse_failure_are_cleaned(self):
        for good in (True, False):
            marker = self.root / ("leftover-ok" if good else "leftover-invalid")
            body = (
                "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
                f"Path({str(marker)!r}).write_text(str(child.pid))\n"
                + (
                    "sys.stdout.write(json.dumps(envelope))"
                    if good
                    else "sys.stdout.write('broken')"
                )
            )
            if good:
                self.assertEqual(self.fixture(body), {"fixture": True})
            else:
                with self.assertRaises(ArticleEvidenceReaderError):
                    self.fixture(body)
            wait_until(lambda: not running(int(marker.read_text())))

    def test_cancel_after_successful_stdout_wins_before_return(self):
        # Deterministically arm cancellation after successful envelope parsing,
        # at the last boundary where the report could otherwise escape.
        from mra import article_evidence_reader as module

        cancelled = False
        decode = module._decode_result

        def decoded(*args):
            nonlocal cancelled
            result = decode(*args)
            cancelled = True
            return result

        with patch.object(module, "_decode_result", side_effect=decoded):
            with self.assertRaises(ArticleEvidenceReadCancelled):
                self.fixture("sys.stdout.write(json.dumps(envelope))", cancel=lambda: cancelled)

    def test_public_input_allowlist_owner_fd_and_fixed_command(self):
        for backend, graph, timeout in (
            ("shell", "fixture", 1),
            ("proof", "../escape", 1),
            ("proof", "valid", 0),
            ("proof", "valid", float("nan")),
            ("proof", "valid", True),
            ("proof", "valid", 301),
        ):
            with self.subTest(backend=backend, graph=graph, timeout=timeout):
                with patch("mra.article_evidence_reader._run_command") as run:
                    with self.assertRaises(ArticleEvidenceReaderError):
                        ArticleEvidenceReader(
                            self.root, backend, graph, timeout_seconds=timeout
                        ).run()
                    run.assert_not_called()
        owner = self.root / "artifacts/article-rechecks/.owner.lock"
        owner.parent.mkdir(parents=True)
        fd = os.open(owner, os.O_CREAT | os.O_RDWR, 0o600)
        other = os.open(self.root / "different", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(ArticleEvidenceReaderError):
                ArticleEvidenceReader(self.root, "proof", "fixture").run(lifecycle_lock_fd=other)
            with patch(
                "mra.article_evidence_reader._run_command", return_value={"fixture": True}
            ) as run:
                ArticleEvidenceReader(self.root, "proof", "fixture").run(lifecycle_lock_fd=fd)
                self.assertEqual(run.call_args.args[0], [sys.executable, "-I", "-S", str(WORKER)])
                self.assertEqual(run.call_args.kwargs["lifecycle_lock_fd"], fd)
            self.request["lifecycle_lock_fd"] = fd
            result = self.fixture(
                "envelope['report']={'open':os.fstat(request['lifecycle_lock_fd']).st_ino};sys.stdout.write(json.dumps(envelope))",
                fd=fd,
            )
            self.assertEqual(result["open"], os.fstat(fd).st_ino)
        finally:
            os.close(other)
            os.close(fd)

    def test_successful_decode_cannot_return_after_wall_budget_expires(self):
        from mra import article_evidence_reader as module

        decode = module._decode_result
        monotonic = time.monotonic
        decoded = False

        def parse_crosses_deadline(*args):
            nonlocal decoded
            result = decode(*args)
            decoded = True
            return result

        def clock():
            return monotonic() + (10 if decoded else 0)

        with (
            patch.object(module, "_decode_result", side_effect=parse_crosses_deadline),
            patch.object(module.time, "monotonic", side_effect=clock),
            self.assertRaises(ArticleEvidenceReadTimeout),
        ):
            self.fixture("sys.stdout.write(json.dumps(envelope))")
        self.assertTrue(decoded, "The deadline crossed during successful decoding")

    def test_child_alarm_remains_armed_during_interpreter_thread_shutdown(self):
        self.request["timeout_seconds"] = 0.4
        body = "threading.Thread(target=lambda:time.sleep(60)).start()\nreturn {'fixture':True}"
        started = time.monotonic()
        with self.assertRaises(ArticleEvidenceReaderError):
            _run_command(self.worker_command(body), self.request, timeout_seconds=2)
        self.assertLess(time.monotonic() - started, 1.5)

    def test_parent_death_child_holds_owner_lease_until_its_own_alarm(self):
        owner = self.root / "artifacts/article-rechecks/.owner.lock"
        owner.parent.mkdir(parents=True)
        marker = self.root / "orphan-child-pid"
        self.request["timeout_seconds"] = 1
        command = self.worker_command(
            f"Path({str(marker)!r}).write_text(str(os.getpid()))\ntime.sleep(60)"
        )
        script = (
            "import sys,os,fcntl\n"
            f"sys.path.insert(0,{str(ROOT)!r})\n"
            "from mra.article_evidence_reader import _run_command\n"
            f"fd=os.open({str(owner)!r},os.O_CREAT|os.O_RDWR,0o600)\n"
            "fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
            f"request={self.request!r}\nrequest['lifecycle_lock_fd']=fd\n"
            f"_run_command({command!r},request,lifecycle_lock_fd=fd,timeout_seconds=5)\n"
        )
        supervisor = subprocess.Popen(
            [sys.executable, "-I", "-S", "-c", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        child = None
        probe = None
        try:
            wait_until(marker.exists)
            child = int(marker.read_text())
            os.kill(supervisor.pid, signal.SIGKILL)
            supervisor.wait(timeout=2)
            probe = os.open(owner, os.O_RDWR)
            with self.assertRaises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            wait_until(lambda: not running(child), timeout=3)
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            if supervisor.poll() is None:
                os.killpg(supervisor.pid, signal.SIGKILL)
            supervisor.wait()
            if child is not None and running(child):
                os.killpg(child, signal.SIGKILL)
            if probe is not None:
                os.close(probe)


if __name__ == "__main__":
    unittest.main()
