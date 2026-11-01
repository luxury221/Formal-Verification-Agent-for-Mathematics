"""Full byte verification with streaming comparison and bounded parallel reads.

The existing seal format is consumed without caching successful checks or
changing the trusted runtime. Every invocation reads every declared file.
"""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import stat
import threading
import time

from . import analysis_foundation as foundation
from .domain import digest
from .formalizer import strict_json
from .integrity import ContentSnapshot, IntegrityError, _stamp

SCAN_POLICY = {
    "version": "streaming-content-scan-v1",
    "parallel_readers": 2,
    "pending_limit": 8,
    "parallel_threshold_bytes": 65536,
    "buffer_bytes": 262144,
    "success_cache": False,
    "checks_actual_bytes": True,
    "checks_exact_tree_scope": True,
}
_LOCAL = threading.local()


def _record(path, before):
    if not stat.S_ISREG(before.st_mode):
        raise IntegrityError(f"Expected a regular file: {path}")
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    with os.fdopen(descriptor, "rb") as incoming:
        if _stamp(before) != _stamp(os.fstat(incoming.fileno())):
            raise IntegrityError(f"File changed while opening: {path}")
        if not hasattr(_LOCAL, "buffer"):
            _LOCAL.buffer = bytearray(SCAN_POLICY["buffer_bytes"])
        buffer = _LOCAL.buffer
        view = memoryview(buffer)
        hasher = hashlib.sha256()
        count = 0
        while size := incoming.readinto(buffer):
            hasher.update(view[:size])
            count += size
        after = os.fstat(incoming.fileno())
    if _stamp(before) != _stamp(after) or _stamp(after) != _stamp(path.lstat()):
        raise IntegrityError(f"File changed while hashing: {path}")
    return {
        "kind": "file",
        "sha256": hasher.hexdigest(),
        "mode": stat.S_IMODE(after.st_mode),
    }, count


class _Reader:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=SCAN_POLICY["parallel_readers"])
        self.pending = deque()
        self.count = self.bytes = self.parallel = self.peak = 0

    def account(self, result):
        record, size = result
        self.count += 1
        self.bytes += size
        return record

    def take(self):
        path, expected, future = self.pending.popleft()
        if self.account(future.result()) != expected:
            raise IntegrityError(f"Runtime file changed: {path}")

    def check(self, path, expected, info=None, *, parallel=False):
        info = path.lstat() if info is None else info
        if not stat.S_ISREG(info.st_mode):
            raise IntegrityError(f"Expected a regular file: {path}")
        if parallel and info.st_size > SCAN_POLICY["parallel_threshold_bytes"]:
            if len(self.pending) >= SCAN_POLICY["pending_limit"]:
                self.take()
            self.pending.append((path, expected, self.pool.submit(_record, path, info)))
            self.parallel += 1
            self.peak = max(self.peak, len(self.pending))
        elif self.account(_record(path, info)) != expected:
            raise IntegrityError(f"Runtime file changed: {path}")

    def hash_only(self, path, expected):
        if self.account(_record(path, path.lstat()))["sha256"] != expected:
            raise IntegrityError(f"Runtime seal changed: {path}")

    def drain(self):
        while self.pending:
            self.take()

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=True)


def _tree(root, expected, reader):
    if root.is_symlink() or not root.is_dir():
        raise IntegrityError(f"Missing or redirected runtime directory: {root}")
    if not isinstance(expected, dict):
        raise IntegrityError("Invalid runtime tree records")
    seen, directories = set(), []
    counts = {"entries": 0, "files": 0, "directories": 0, "symlinks": 0}

    def remember(name, value):
        if name in seen or expected.get(name) != value:
            raise IntegrityError(f"Runtime tree changed: {root}/{name}")
        seen.add(name)
        counts["entries"] += 1
        counts[
            {"file": "files", "directory": "directories", "symlink": "symlinks"}[value["kind"]]
        ] += 1

    def visit(directory):
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise IntegrityError(f"Runtime directory was redirected: {directory}")
        name = directory.relative_to(root).as_posix()
        remember(name, {"kind": "directory", "mode": stat.S_IMODE(info.st_mode)})
        before_directory = _stamp(info)
        directories.append((directory, before_directory))
        with os.scandir(directory) as children:
            for entry in children:
                if entry.name == ".git":
                    continue
                path = Path(entry.path)
                name = path.relative_to(root).as_posix()
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    visit(path)
                elif stat.S_ISLNK(info.st_mode):
                    try:
                        resolved = path.resolve(strict=True)
                    except (OSError, RuntimeError) as error:
                        raise IntegrityError(f"Invalid runtime symlink: {path}") from error
                    if not resolved.is_relative_to(root.resolve()):
                        raise IntegrityError(f"Runtime symlink escapes its tree: {path}")
                    remember(name, {"kind": "symlink", "target": os.readlink(path)})
                    if _stamp(info) != _stamp(path.lstat()):
                        raise IntegrityError(f"Runtime symlink changed while checking: {path}")
                else:
                    value = expected.get(name)
                    if name in seen or not isinstance(value, dict) or value.get("kind") != "file":
                        raise IntegrityError(f"Runtime tree changed: {root}/{name}")
                    seen.add(name)
                    counts["entries"] += 1
                    counts["files"] += 1
                    reader.check(path, value, info, parallel=True)
        if _stamp(directory.lstat()) != before_directory:
            raise IntegrityError(f"Runtime directory changed while scanning: {directory}")

    visit(root)
    reader.drain()
    if seen != expected.keys():
        missing = sorted(expected.keys() - seen)
        raise IntegrityError(f"Runtime tree has missing entries: {root}/{missing[0]}")
    for directory, before in directories:
        if _stamp(directory.lstat()) != before:
            raise IntegrityError(f"Runtime directory changed while checking: {directory}")
    return counts


class StreamingSnapshot(ContentSnapshot):
    def verify(self):
        started = time.monotonic()
        reader = _Reader()
        trees = {}
        matched = False
        try:
            for name, expected in self._seal_hashes.items():
                reader.hash_only(foundation._safe(self.root, name), expected)
            for name, expected in self.data["files"].items():
                reader.check(foundation._safe(self.root, name), expected)
            for name, expected in self.data["trees"].items():
                trees[name] = _tree(foundation._safe(self.root, name), expected, reader)
            for name, expected in self.data["host_files"].items():
                resolved = Path(name).resolve(strict=True)
                if str(resolved) != expected["resolved"]:
                    raise IntegrityError(f"Host runtime dependency changed: {name}")
                reader.check(resolved, {k: v for k, v in expected.items() if k != "resolved"})
            for name, expected in self._seal_hashes.items():
                reader.hash_only(foundation._safe(self.root, name), expected)
            matched = True
        except OSError as error:
            raise IntegrityError(
                f"Runtime dependency missing or unreadable: {error.filename}"
            ) from error
        finally:
            reader.close()
            self.last_scan = {
                "level": "E0",
                "scope": "one full verification invocation, not a mathematical verdict",
                "matched": matched,
                "seconds": time.monotonic() - started,
                "file_reads": reader.count,
                "bytes_hashed": reader.bytes,
                "parallel_jobs": reader.parallel,
                "peak_pending": reader.peak,
                "trees": trees,
                "policy": dict(SCAN_POLICY),
            }


def _load(root):
    root = Path(root).resolve()
    lock = strict_json(foundation._safe(root, foundation.LOCK).read_text())
    raw = foundation._safe(root, foundation.SNAPSHOT).read_bytes()
    if lock != {"mode": foundation.MODE, "sha256": hashlib.sha256(raw).hexdigest()}:
        raise IntegrityError("Analysis snapshot does not match its lock")
    data = strict_json(raw.decode())
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != "1"
        or not isinstance(data.get("trees"), dict)
        or not isinstance(data.get("files"), dict)
        or not isinstance(data.get("host_files"), dict)
        or set(data["trees"]) != set(foundation.TREES)
        or set(data["files"]) != set(foundation.FILES)
        or not data["host_files"]
        or data["provenance"]
        != {
            "mode": foundation.MODE,
            "import_scope": [foundation.IMPORT],
            "catalog": digest(foundation.catalog()),
        }
    ):
        raise IntegrityError("Incomplete analysis snapshot scope")
    snapshot = StreamingSnapshot(root, data)
    snapshot._seal_hashes = {
        foundation.SNAPSHOT: hashlib.sha256(raw).hexdigest(),
        foundation.LOCK: hashlib.sha256(
            foundation._safe(root, foundation.LOCK).read_bytes()
        ).hexdigest(),
    }
    snapshot.verify()
    return snapshot


def load(root):
    try:
        return _load(root)
    except IntegrityError:
        raise
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        raise IntegrityError("Analysis snapshot unavailable or malformed") from error
