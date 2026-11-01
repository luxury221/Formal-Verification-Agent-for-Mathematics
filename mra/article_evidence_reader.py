"""Bounded subprocess transport for the original article evidence providers.

The provider can persist its original invalidation diagnostics. Cancellation
discards this transport's uncommitted observation; it does not roll those writes
back or grant any mathematical authority.
"""

import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time

from .tasks import WorkerBusy

SCHEMA = "article-evidence-reader-v1"
BACKENDS = ("calculus", "pde", "typed", "proof")
DEFAULT_TIMEOUT_SECONDS = 300
WALL_SECONDS = DEFAULT_TIMEOUT_SECONDS
MAX_INPUT_BYTES = 8192
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024


class ArticleEvidenceReaderError(RuntimeError):
    def __init__(self, message, *, code="READER_FAILED"):
        super().__init__(message)
        self.code = code


class ArticleEvidenceReadCancelled(ArticleEvidenceReaderError):
    def __init__(self):
        super().__init__(
            "Article evidence read cancelled; no observation committed", code="CANCELLED"
        )


class ArticleEvidenceReadTimeout(ArticleEvidenceReaderError):
    def __init__(self):
        super().__init__("Article evidence provider exceeded its wall-clock budget", code="TIMEOUT")


def _strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("Non-finite JSON constant")

    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    # Iterate containers without duplicating all of a broad report into a second
    # stack of (value, depth) pairs before the value budget can be checked.
    stack = [(iter((result,)), 0)]
    values = 0
    while stack:
        iterator, depth = stack[-1]
        try:
            value = next(iterator)
        except StopIteration:
            stack.pop()
            continue
        values += 1
        if depth > 96 or values > 500000:
            raise ValueError("Reader JSON structure exceeds its budget")
        if isinstance(value, dict):
            for key in value:
                key.encode("utf-8", errors="strict")
            stack.append((iter(value.values()), depth + 1))
        elif isinstance(value, list):
            stack.append((iter(value), depth + 1))
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Non-finite JSON number")
        elif isinstance(value, str):
            value.encode("utf-8", errors="strict")
    return result


def _validate_request(request):
    if not isinstance(request, dict) or set(request) != {
        "schema",
        "root",
        "backend",
        "graph_id",
        "timeout_seconds",
        "lifecycle_lock_fd",
    }:
        raise ValueError("Invalid evidence reader request fields")
    if request["schema"] != SCHEMA or request["backend"] not in BACKENDS:
        raise ValueError("Unsupported evidence reader request")
    graph = request["graph_id"]
    if not isinstance(graph, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", graph):
        raise ValueError("Invalid verifier graph identifier")
    root = request["root"]
    if not isinstance(root, str) or not root or "\x00" in root:
        raise ValueError("Invalid evidence reader root")
    path = Path(root)
    if not path.is_absolute() or path.resolve() != path or not path.is_dir():
        raise ValueError("Evidence reader root must be an existing canonical absolute directory")
    timeout = request["timeout_seconds"]
    if (
        type(timeout) not in (int, float)
        or not math.isfinite(timeout)
        or not 0 < timeout <= WALL_SECONDS
    ):
        raise ValueError("Invalid evidence reader wall-clock budget")
    fd = request["lifecycle_lock_fd"]
    if fd is not None:
        if type(fd) is not int or fd < 3:
            raise ValueError("Invalid evidence reader lifecycle descriptor")
        lock = path / "artifacts/article-rechecks/.owner.lock"
        if lock.is_symlink():
            raise ValueError("Article recheck owner lock was redirected")
        actual, expected = os.fstat(fd), lock.stat()
        if not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != (
            expected.st_dev,
            expected.st_ino,
        ):
            raise ValueError("Evidence reader lifecycle descriptor is not this queue's owner file")
    return request


def _decode_result(raw, request):
    try:
        value = _strict_json(raw.decode("utf-8", errors="strict"))
        if not isinstance(value, dict) or set(value) != {
            "schema",
            "backend",
            "graph_id",
            "status",
            "report",
            "error",
        }:
            raise ValueError("Unexpected reader envelope fields")
        if (value["schema"], value["backend"], value["graph_id"]) != (
            SCHEMA,
            request["backend"],
            request["graph_id"],
        ):
            raise ValueError("Reader response identity differs from the request")
        status, error = value["status"], value["error"]
        if status == "RETURNED":
            if not isinstance(value["report"], dict) or error is not None:
                raise ValueError("A returned reader result needs one complete report")
            return value["report"]
        if status not in ("BUSY", "ERROR") or value["report"] is not None:
            raise ValueError("Invalid reader result state")
        if not isinstance(error, dict) or set(error) != {"code", "message"}:
            raise ValueError("Invalid reader error fields")
        if not isinstance(error["message"], str) or not 0 < len(error["message"]) <= 2000:
            raise ValueError("Invalid reader error message")
        if status == "BUSY" and error["code"] == "WORKER_BUSY":
            raise WorkerBusy(error["message"])
        if status == "ERROR" and error["code"] in (
            "PROVIDER_ERROR",
            "RESULT_LIMIT",
            "INVALID_REQUEST",
        ):
            raise ArticleEvidenceReaderError(error["message"], code=error["code"])
        raise ValueError("Unexpected reader error code")
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise ArticleEvidenceReaderError(
            "Invalid evidence reader response: " + str(error)
        ) from error


def _kill_group(process):
    # Always kill the original group, even when its leader has already exited.
    # A provider that deliberately creates a different session is outside this
    # process-group boundary; this is not a general process-tree sandbox.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _run_command(
    command,
    request,
    *,
    cancel_requested=None,
    lifecycle_lock_fd=None,
    timeout_seconds=WALL_SECONDS,
    max_output_bytes=MAX_OUTPUT_BYTES,
):
    """Private process harness for inert executable fixtures; public argv is fixed."""
    encoded = json.dumps(
        request, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > MAX_INPUT_BYTES:
        raise ArticleEvidenceReaderError("Evidence reader request exceeds its input budget")
    process = None
    selector = selectors.DefaultSelector()
    output, diagnostic = bytearray(), bytearray()
    started, exited_at = time.monotonic(), None

    def cancelled():
        if cancel_requested is not None and cancel_requested():
            raise ArticleEvidenceReadCancelled()

    try:
        cancelled()
        process = subprocess.Popen(
            command,
            cwd=request["root"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=True,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
            pass_fds=() if lifecycle_lock_fd is None else (lifecycle_lock_fd,),
        )
        for stream, events, label in (
            (process.stdin, selectors.EVENT_WRITE, "input"),
            (process.stdout, selectors.EVENT_READ, "output"),
            (process.stderr, selectors.EVENT_READ, "diagnostic"),
        ):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, events, label)
        written = 0
        while True:
            cancelled()
            now = time.monotonic()
            remaining = timeout_seconds - (now - started)
            if remaining <= 0:
                raise ArticleEvidenceReadTimeout()
            code = process.poll()
            if code is not None and exited_at is None:
                exited_at = now
                _kill_group(process)
            reads = [key for key in selector.get_map().values() if key.data != "input"]
            if code is not None and not reads:
                break
            if exited_at is not None and now - exited_at > 1:
                raise ArticleEvidenceReaderError(
                    "Reader descendants retained its output pipes after exit"
                )
            for key, _ in selector.select(min(0.05, remaining)):
                stream = key.fileobj
                if key.data == "input":
                    try:
                        written += os.write(stream.fileno(), encoded[written : written + 4096])
                    except BrokenPipeError:
                        selector.unregister(stream)
                        stream.close()
                        continue
                    if written == len(encoded):
                        selector.unregister(stream)
                        stream.close()
                    continue
                chunk = os.read(stream.fileno(), 65536)
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                target = output if key.data == "output" else diagnostic
                limit = max_output_bytes if key.data == "output" else MAX_STDERR_BYTES
                if len(target) + len(chunk) > limit:
                    raise ArticleEvidenceReaderError(
                        "Evidence reader output exceeds its byte budget", code="RESULT_LIMIT"
                    )
                target.extend(chunk)
        cancelled()
        if process.returncode != 0:
            detail = diagnostic.decode("utf-8", errors="replace")[:1000]
            raise ArticleEvidenceReaderError(
                "Evidence reader exited with status "
                + str(process.returncode)
                + (": " + detail if detail else "")
            )
        result = _decode_result(bytes(output), request)
        cancelled()
        if time.monotonic() - started >= timeout_seconds:
            raise ArticleEvidenceReadTimeout()
        return result
    except OSError as error:
        raise ArticleEvidenceReaderError(
            "Evidence reader transport failed: " + str(error)
        ) from error
    finally:
        selector.close()
        if process is not None:
            try:
                _kill_group(process)
            finally:
                process.wait()
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()


class ArticleEvidenceReader:
    """Read one fixed original provider through a bounded Linux subprocess."""

    def __init__(self, root, backend, graph_id, *, timeout_seconds=WALL_SECONDS):
        self.root = Path(root).resolve()
        self.backend = backend
        self.graph_id = graph_id
        self.timeout_seconds = timeout_seconds

    def run(self, *, cancel_requested=None, lifecycle_lock_fd=None):
        if sys.platform != "linux":
            raise ArticleEvidenceReaderError("The controlled evidence reader requires Linux")
        request = {
            "schema": SCHEMA,
            "root": str(self.root),
            "backend": self.backend,
            "graph_id": self.graph_id,
            "timeout_seconds": self.timeout_seconds,
            "lifecycle_lock_fd": lifecycle_lock_fd,
        }
        try:
            _validate_request(request)
        except (OSError, ValueError, TypeError) as error:
            raise ArticleEvidenceReaderError(
                "Invalid evidence reader request: " + str(error)
            ) from error
        worker = Path(__file__).with_name("article_evidence_reader_worker.py").resolve()
        command = [sys.executable, "-I", "-S", str(worker)]
        return _run_command(
            command,
            request,
            cancel_requested=cancel_requested,
            lifecycle_lock_fd=lifecycle_lock_fd,
            timeout_seconds=self.timeout_seconds,
        )
