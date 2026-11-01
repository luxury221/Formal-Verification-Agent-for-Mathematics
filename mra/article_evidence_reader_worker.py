"""Fixed evidence-provider child. This is not a configurable command runner."""

import json
import os
from pathlib import Path
import signal
import sys
import time


def expire(_signal, _frame):
    # This process is a session/group leader. A parent crash cannot disable this
    # independent limit or leave same-group provider children running forever.
    os.killpg(os.getpgrp(), signal.SIGKILL)
    os._exit(124)


def main():
    started = time.monotonic()
    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, 300)
    # Isolated Python omits cwd/PYTHONPATH. Only this fixed code tree is imported.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from contextlib import redirect_stdout
    from mra.article_evidence_reader import (
        MAX_INPUT_BYTES,
        MAX_OUTPUT_BYTES,
        SCHEMA,
        _strict_json,
        _validate_request,
    )
    from mra.tasks import WorkerBusy

    request = None
    try:
        if len(sys.argv) != 1:
            raise ValueError("The evidence reader accepts no command arguments")
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("Evidence reader input exceeds its byte budget")
        request = _validate_request(_strict_json(raw.decode("utf-8", errors="strict")))
        remaining = request["timeout_seconds"] - (time.monotonic() - started)
        if remaining <= 0:
            expire(None, None)
        signal.setitimer(signal.ITIMER_REAL, remaining)
    except Exception as error:
        # Without a valid identity there is no meaningful result envelope.
        sys.stderr.write("Invalid reader request: " + str(error)[:1000] + "\n")
        return 2

    envelope = {
        "schema": SCHEMA,
        "backend": request["backend"],
        "graph_id": request["graph_id"],
        "status": "ERROR",
        "report": None,
        "error": None,
    }
    try:
        # No provider code is imported or called until the wall timer and exact
        # transport request have been established. Original invalidation writes
        # are retained even if the parent later discards this observation.
        from mra.article_audit import backend_report

        with redirect_stdout(sys.stderr):
            report = backend_report(Path(request["root"]), request["backend"], request["graph_id"])
        if not isinstance(report, dict):
            raise ValueError("Original provider did not return a report object")
        envelope.update(status="RETURNED", report=report)
    except WorkerBusy as error:
        envelope.update(
            status="BUSY",
            error={"code": "WORKER_BUSY", "message": str(error)[:2000] or "Provider busy"},
        )
    except Exception as error:
        envelope["error"] = {
            "code": "PROVIDER_ERROR",
            "message": (type(error).__name__ + ": " + str(error))[:2000],
        }

    try:
        encoded = bytearray()
        encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        for part in encoder.iterencode(envelope):
            data = part.encode("utf-8")
            if len(encoded) + len(data) > MAX_OUTPUT_BYTES:
                raise ValueError("Original provider report exceeds the complete-result byte budget")
            encoded.extend(data)
    except (ValueError, TypeError, RecursionError) as error:
        envelope.update(
            status="ERROR",
            report=None,
            error={
                "code": "RESULT_LIMIT",
                "message": str(error)[:2000] or "Result cannot be encoded",
            },
        )
        encoded = json.dumps(
            envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()
    # Keep the independent alarm armed through interpreter shutdown too: a
    # provider's background non-daemon thread must not outlive the lease bound.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
