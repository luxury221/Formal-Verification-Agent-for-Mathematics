"""Fixed Lean sandbox launcher retaining both queue and global checker leases."""

import math
import os
import signal
import subprocess
import time


def run(runner, work, sandbox, stdout_path, stderr_path, seconds, owner_fd, checker_fd, checkpoint):
    sandbox.mkdir()
    command = [
        "/usr/bin/unshare",
        "--user",
        "--map-root-user",
        "--mount",
        "--net",
        "--pid",
        "--fork",
        "--kill-child",
        "/usr/bin/python3",
        str(runner.root / "ops/scripts/sandbox_worker.py"),
        str(runner.runtime),
        str(runner.mathlib),
        str(work),
        str(sandbox),
        str(max(1, math.ceil(seconds))),
        "16384",
    ]
    started, status, code, pid = time.monotonic(), "COMPLETED", None, None
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        if seconds <= 0:
            status = "TIMEOUT"
        else:
            process = subprocess.Popen(
                command,
                cwd=work,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                pass_fds=tuple(set((owner_fd, checker_fd))),
            )
            pid = process.pid
            try:
                checkpoint("SANDBOX_RUNNING", str(work))
                code = process.wait(timeout=seconds)
            except subprocess.TimeoutExpired:
                status = "TIMEOUT"
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
    return {
        "run_status": status,
        "returncode": code,
        "pid": pid,
        "elapsed_seconds": time.monotonic() - started,
    }
