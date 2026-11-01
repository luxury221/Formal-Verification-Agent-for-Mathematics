"""Bounded full-content scans; no metadata cache, skipped bytes or persistent trust."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import stat
import threading
import time
import uuid

from .domain import canonical, digest
from .integrity import IntegrityError, _stamp, file_hash, file_record, host_record

MODE = "buffered-full-content-audit-v1"
THREADS = 1
BATCH = 64
_buffers = threading.local()


def buffered_record(path):
    """Reuse scratch space, never content hashes; retain all moving-file checks."""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise IntegrityError(f"Expected a regular file: {path}")
    if not hasattr(_buffers, "value"):
        _buffers.value = bytearray(1024 * 1024)
    buffer = _buffers.value
    view = memoryview(buffer)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb", buffering=0) as incoming:
        if _stamp(before) != _stamp(os.fstat(incoming.fileno())):
            raise IntegrityError(f"File changed while opening: {path}")
        hasher = hashlib.sha256()
        size = 0
        while True:
            count = incoming.readinto(buffer)
            if count == 0:
                break
            size += count
            hasher.update(view[:count])
        after = os.fstat(incoming.fileno())
    if _stamp(before) != _stamp(after) or _stamp(after) != _stamp(path.lstat()):
        raise IntegrityError(f"File changed while hashing: {path}")
    return {"kind": "file", "sha256": hasher.hexdigest(), "mode": stat.S_IMODE(after.st_mode)}, size


def tree_records(root, *, workers=THREADS):
    """Same records as the frozen scanner; at most 64 queued, 1–4 open files."""
    root = Path(root)
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError("Full-content audit requires 1–4 threads")
    if root.is_symlink() or not root.is_dir():
        raise IntegrityError(f"Missing or redirected runtime directory: {root}")
    resolved = root.resolve()
    records, files = {}, []
    byte_count = 0

    def hashed(item):
        name, path = item
        value, size = buffered_record(path)
        return name, value, size

    def flush(pool):
        nonlocal byte_count
        jobs = pool.map(hashed, files) if pool else map(hashed, files)
        for name, value, size in jobs:
            records[name] = value
            byte_count += size
        files.clear()

    def visit(path, relative, pool):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise IntegrityError(f"Runtime directory changed while scanning: {path}")
        records[relative or "."] = {"kind": "directory", "mode": stat.S_IMODE(info.st_mode)}
        with os.scandir(path) as entries:
            names = sorted(entry.name for entry in entries if entry.name != ".git")
        for name in names:
            child = path / name
            key = relative + "/" + name if relative else name
            info = child.lstat()
            if stat.S_ISLNK(info.st_mode):
                if not child.resolve(strict=True).is_relative_to(resolved):
                    raise IntegrityError(f"Runtime symlink escapes its tree: {child}")
                records[key] = {"kind": "symlink", "target": os.readlink(child)}
            elif stat.S_ISDIR(info.st_mode):
                visit(child, key, pool)
            else:
                files.append((key, child))
                if len(files) == BATCH:
                    flush(pool)

    if workers == 1:
        visit(root, "", None)
        flush(None)
    else:
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="mra-content-audit"
        ) as pool:
            visit(root, "", pool)
            flush(pool)
    return records, {
        "files": sum(v["kind"] == "file" for v in records.values()),
        "bytes": byte_count,
        "entries": len(records),
    }


def verify(snapshot, *, workers=THREADS):
    """Read every covered byte on every call and compare the unchanged sealed baseline."""
    started = time.monotonic()
    totals = {"files": 0, "bytes": 0, "entries": 0}
    trees = {}
    try:
        for name, expected in snapshot._seal_hashes.items():
            if file_hash(snapshot.root / name) != expected:
                raise IntegrityError(f"Runtime seal changed: {name}")
        for name, expected in snapshot.data["files"].items():
            if file_record(snapshot.root / name) != expected:
                raise IntegrityError(f"Runtime file changed: {name}")
        for name, expected in snapshot.data["trees"].items():
            actual, counts = tree_records(snapshot.root / name, workers=workers)
            if actual != expected:
                changed = sorted(
                    k for k in actual.keys() | expected.keys() if actual.get(k) != expected.get(k)
                )
                raise IntegrityError(f"Runtime tree changed: {name}/{changed[0]}")
            trees[name] = {**counts, "records_sha256": digest(actual)}
            for key in totals:
                totals[key] += counts[key]
        for name, expected in snapshot.data["host_files"].items():
            if host_record(Path(name)) != expected:
                raise IntegrityError(f"Host runtime dependency changed: {name}")
    except OSError as error:
        raise IntegrityError(
            f"Runtime dependency missing or unreadable: {error.filename}"
        ) from error
    return {
        "mode": MODE,
        "workers": workers,
        "batch_size": BATCH,
        "elapsed_seconds": time.monotonic() - started,
        "runtime_tree_totals": totals,
        "trees": trees,
        "fixed_files": len(snapshot.data["files"]),
        "host_files": len(snapshot.data["host_files"]),
        "metadata_cache": False,
        "complete": True,
    }


def inspect(root, *, workers=THREADS):
    """Persist a fresh diagnostic; this entry point cannot publish a mathematical verdict."""
    from .integrity import ContentSnapshot

    root = Path(root).resolve()
    if type(workers) is not int or workers not in (1, 2, 4):
        raise ValueError("Choose 1, 2 or 4 audit threads")
    base = root / "artifacts/runtime-audits"
    if base.resolve() != base:
        raise ValueError("Runtime audit storage cannot be redirected")
    base.mkdir(parents=True, exist_ok=True)
    if len(list(base.iterdir())) >= 100:
        raise ValueError("At most 100 saved local runtime diagnostics")
    key = "audit-" + uuid.uuid4().hex
    area = base / key
    area.mkdir()
    started = time.monotonic()
    files = ("mra/runtime_audit.py", "mra/integrity.py", "mra/domain.py")
    implementation = {name: file_hash(root / name) for name in files}
    result = {
        "mode": MODE,
        "audit_id": key,
        "run_status": "POLICY_BLOCKED",
        "observed_integrity_ok": False,
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "model_calls": 0,
        "lean_calls": 0,
        "snapshot_fingerprint": None,
        "implementation_sha256": implementation,
        "workers": workers,
        "scan": None,
        "error": None,
        "historical_record_when_saved": True,
        "fresh_attestation": False,
        "default_verification_path_changed": False,
    }
    try:
        snapshot = ContentSnapshot.load_runtime(root)
        result["snapshot_fingerprint"] = snapshot.fingerprint
        scan = verify(snapshot, workers=workers)
        if any(file_hash(root / name) != expected for name, expected in implementation.items()):
            raise IntegrityError("Runtime audit implementation changed during scanning")
        result.update(run_status="COMPLETED", observed_integrity_ok=True, scan=scan)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        result["error"] = str(error)
    result["elapsed_seconds"] = time.monotonic() - started
    report = canonical(result) + b"\n"
    with (area / "report.json").open("xb") as stream:
        stream.write(report)
    result["report_path"] = str(area / "report.json")
    return result
