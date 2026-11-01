"""Linux event observation for a bounded full-byte verification invocation.

No success cache or fallback is provided. The monitor is not a continuous
guard, an atomic snapshot, or a defence against a hostile host.
"""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
import ctypes
import hashlib
import os
from pathlib import Path
import stat
import struct
import sys
import threading
import time

from .integrity import ContentSnapshot, IntegrityError, _stamp

SCAN_POLICY = {
    "version": "watched-streaming-content-scan-v2",
    "parallel_readers": 2,
    "pending_limit": 8,
    "parallel_threshold_bytes": 65536,
    "buffer_bytes": 262144,
    "success_cache": False,
    "checks_actual_bytes": True,
    "checks_exact_tree_scope": True,
    "final_structure_scan": True,
    "native_monitor": "linux-inotify",
    "monitor_required": True,
    "event_drain_limit_bytes": 8388608,
    "event_drain_limit_reads": 128,
    "monitor_window": "after all watches installed through final EAGAIN drain",
}
_LOCAL = threading.local()
_EVENT = struct.Struct("=iIII")
_CHANGE = 0x00000002 | 0x00000004 | 0x00000008 | 0x00000040 | 0x00000080
_CHANGE |= 0x00000100 | 0x00000200 | 0x00000400 | 0x00000800 | 0x00002000
_OVERFLOW, _IGNORED, _ISDIR = 0x00004000, 0x00008000, 0x40000000
_ONLYDIR, _NOFOLLOW = 0x01000000, 0x02000000


def _safe(root, name):
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise IntegrityError("Invalid runtime relative path")
    path = root / name
    if not path.is_relative_to(root) or path.resolve(strict=True) != path.absolute():
        raise IntegrityError(f"Runtime path missing or redirected: {name}")
    return path


class _Observer:
    """One nonblocking inotify descriptor; all subscriptions die on close."""

    def __init__(self):
        self.fd = -1
        self.rules = {}
        self.paths = {}
        self.events = self.reads = self.bytes = 0
        self.window_started = self.window_ended = None
        if sys.platform != "linux":
            raise IntegrityError("Required Linux inotify monitor unavailable")
        try:
            api = ctypes.CDLL(None, use_errno=True)
            self.init = api.inotify_init1
            self.init.argtypes = [ctypes.c_int]
            self.init.restype = ctypes.c_int
            self.add = api.inotify_add_watch
            self.add.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
            self.add.restype = ctypes.c_int
            self.fd = self.init(os.O_NONBLOCK | os.O_CLOEXEC)
            if self.fd < 0:
                raise OSError(ctypes.get_errno(), "inotify_init1 failed")
        except (AttributeError, OSError) as error:
            self.close()
            raise IntegrityError("Required Linux inotify monitor unavailable") from error

    def close(self):
        if self.fd >= 0:
            descriptor, self.fd = self.fd, -1
            os.close(descriptor)

    def watch(self, path, *, directory, all_children=False, name=None):
        path = Path(path)
        if path not in self.paths:
            before = path.lstat()
            valid = stat.S_ISDIR(before.st_mode) if directory else stat.S_ISREG(before.st_mode)
            if not valid:
                raise IntegrityError(f"Cannot monitor redirected or nonregular path: {path}")
            flags = _CHANGE | _NOFOLLOW | (_ONLYDIR if directory else 0)
            wd = self.add(self.fd, os.fsencode(path), flags)
            if wd < 0:
                raise IntegrityError(
                    f"Required inotify watch unavailable: {path} (errno {ctypes.get_errno()})"
                )
            if _stamp(before) != _stamp(path.lstat()):
                raise IntegrityError(f"Path changed while installing monitor: {path}")
            self.paths[path] = wd
            # Several hardlink paths can share one native watch descriptor.
            rule = self.rules.setdefault(
                wd, {"directory": directory, "all": False, "names": set(), "paths": set()}
            )
            if rule["directory"] != directory:
                raise IntegrityError("Inconsistent inotify inode identity")
            rule["paths"].add(str(path))
        else:
            wd = self.paths[path]
            rule = self.rules[wd]
        rule["all"] |= all_children
        if name is not None:
            rule["names"].add(name)

    def protect_path(self, path, *, file=True):
        """Watch lexical resolution links as well as the resolved file inode."""
        path = Path(path)
        if not path.is_absolute():
            raise IntegrityError("Host monitor path must be absolute")
        for parent in reversed(path.parents):
            child = path.relative_to(parent).parts[0]
            self.watch(parent.resolve(strict=True), directory=True, name=child)
        if file:
            self.watch(path.resolve(strict=True), directory=False)

    def prepare(self, snapshot):
        for name in set(snapshot.data["files"]) | set(snapshot._seal_hashes):
            self.protect_path(_safe(snapshot.root, name))
        for name, expected in snapshot.data["trees"].items():
            root = _safe(snapshot.root, name)
            self.protect_path(root, file=False)
            if not isinstance(expected, dict):
                raise IntegrityError("Invalid runtime tree records")
            for relative, value in sorted(
                expected.items(), key=lambda item: len(Path(item[0]).parts)
            ):
                kind = value.get("kind")
                if kind == "symlink":
                    candidate = Path(relative)
                    if candidate.is_absolute() or ".." in candidate.parts:
                        raise IntegrityError("Invalid runtime symlink record path")
                    path = _safe(root, candidate.parent.as_posix()) / candidate.name
                else:
                    path = root if relative == "." else _safe(root, relative)
                if kind == "directory":
                    self.watch(path, directory=True, all_children=True)
                elif kind == "file":
                    self.watch(path, directory=False)
                elif kind != "symlink":
                    raise IntegrityError("Invalid runtime tree entry kind")
        for name, expected in snapshot.data["host_files"].items():
            path = Path(name)
            if str(path.resolve(strict=True)) != expected["resolved"]:
                raise IntegrityError(f"Host runtime dependency changed: {name}")
            self.protect_path(path)
        self.window_started = time.monotonic()
        # Do not discard relevant events observed during watch installation.
        self.check()

    def consume(self, data):
        offset = 0
        while offset < len(data):
            if len(data) - offset < _EVENT.size:
                raise IntegrityError("Malformed inotify event header")
            wd, mask, _cookie, length = _EVENT.unpack_from(data, offset)
            offset += _EVENT.size
            if length > 4096 or length % 4 or length > len(data) - offset:
                raise IntegrityError("Malformed inotify event name length")
            raw = data[offset : offset + length]
            offset += length
            if raw:
                end = raw.find(b"\0")
                if end < 0 or any(raw[end:]):
                    raise IntegrityError("Malformed inotify event name")
                raw = raw[:end]
                if not raw or b"/" in raw or raw in (b".", b".."):
                    raise IntegrityError("Malformed inotify child name")
            self.events += 1
            if mask & (_OVERFLOW | _IGNORED) or mask & 0x00002000:
                raise IntegrityError("Required inotify observation lost or overflowed")
            rule = self.rules.get(wd)
            if rule is None or not mask or mask & ~(_CHANGE | _ISDIR):
                raise IntegrityError("Unexpected inotify event identity or mask")
            if raw and not rule["directory"]:
                raise IntegrityError("Unexpected inotify child event on file")
            name = os.fsdecode(raw)
            if (
                not raw
                or not rule["directory"]
                or name in rule["names"]
                or (rule["all"] and name != ".git")
            ):
                raise IntegrityError(
                    "Runtime mutation observed by inotify: " + sorted(rule["paths"])[0]
                )

    def check(self):
        for _ in range(SCAN_POLICY["event_drain_limit_reads"]):
            try:
                data = os.read(self.fd, 262144)
            except BlockingIOError:
                return
            except InterruptedError:
                continue
            self.reads += 1
            self.bytes += len(data)
            if not data or self.bytes > SCAN_POLICY["event_drain_limit_bytes"]:
                raise IntegrityError("Inotify event drain empty or exceeded its byte budget")
            self.consume(data)
        raise IntegrityError("Inotify event drain exceeded its read budget")

    def finish(self):
        self.check()
        self.window_ended = time.monotonic()


def _layout(root, expected):
    """Re-enumerate the exact structure; file digests were already read in full."""
    seen = set()
    resolved_root = root.resolve(strict=True)

    def visit(directory):
        before = directory.lstat()
        if not stat.S_ISDIR(before.st_mode):
            raise IntegrityError(f"Runtime directory redirected at final scan: {directory}")
        name = directory.relative_to(root).as_posix()
        if expected.get(name) != {"kind": "directory", "mode": stat.S_IMODE(before.st_mode)}:
            raise IntegrityError(f"Runtime directory changed at final scan: {directory}")
        seen.add(name)
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name == ".git":
                    continue
                path = Path(entry.path)
                name = path.relative_to(root).as_posix()
                info = path.lstat()
                if stat.S_ISDIR(info.st_mode):
                    visit(path)
                    continue
                value = expected.get(name)
                if stat.S_ISLNK(info.st_mode):
                    if not path.resolve(strict=True).is_relative_to(resolved_root):
                        raise IntegrityError(f"Runtime symlink escapes at final scan: {path}")
                    actual = {"kind": "symlink", "target": os.readlink(path)}
                    if value != actual:
                        raise IntegrityError(f"Runtime symlink changed at final scan: {path}")
                elif (
                    not stat.S_ISREG(info.st_mode)
                    or not isinstance(value, dict)
                    or value.get("kind") != "file"
                    or value.get("mode") != stat.S_IMODE(info.st_mode)
                ):
                    raise IntegrityError(f"Runtime file changed at final scan: {path}")
                seen.add(name)
        if _stamp(before) != _stamp(directory.lstat()):
            raise IntegrityError(f"Runtime directory moved during final scan: {directory}")

    visit(root)
    if seen != expected.keys():
        raise IntegrityError(f"Runtime tree scope changed at final scan: {root}")
    return len(seen)


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


class WatchedSnapshot(ContentSnapshot):
    def verify(self):
        started = time.monotonic()
        reader, observer = _Reader(), None
        trees, final_entries = {}, {}
        matched = False
        try:
            observer = _Observer()
            observer.prepare(self)
            for name, expected in self._seal_hashes.items():
                reader.hash_only(_safe(self.root, name), expected)
            for name, expected in self.data["files"].items():
                reader.check(_safe(self.root, name), expected)
            for name, expected in self.data["trees"].items():
                trees[name] = _tree(_safe(self.root, name), expected, reader)
            for name, expected in self.data["host_files"].items():
                resolved = Path(name).resolve(strict=True)
                if str(resolved) != expected["resolved"]:
                    raise IntegrityError(f"Host runtime dependency changed: {name}")
                reader.check(resolved, {k: v for k, v in expected.items() if k != "resolved"})
            reader.drain()
            for name, expected in self.data["trees"].items():
                final_entries[name] = _layout(_safe(self.root, name), expected)
            for name in self.data["files"]:
                _safe(self.root, name)
            for name, expected in self.data["host_files"].items():
                if str(Path(name).resolve(strict=True)) != expected["resolved"]:
                    raise IntegrityError(f"Host runtime dependency redirected: {name}")
            for name, expected in self._seal_hashes.items():
                reader.hash_only(_safe(self.root, name), expected)
            observer.finish()
            matched = True
        except OSError as error:
            raise IntegrityError(
                f"Runtime dependency or required monitor unavailable: {error}"
            ) from error
        finally:
            try:
                reader.close()
            finally:
                if observer is not None:
                    observer.close()
                self.last_scan = {
                    "level": "E0",
                    "scope": "one monitored full verification, not a mathematical verdict",
                    "matched": matched,
                    "seconds": time.monotonic() - started,
                    "file_reads": reader.count,
                    "bytes_hashed": reader.bytes,
                    "parallel_jobs": reader.parallel,
                    "peak_pending": reader.peak,
                    "trees": trees,
                    "final_tree_entries": final_entries,
                    "watched_inodes": len(observer.rules) if observer is not None else 0,
                    "native_events_read": observer.events if observer is not None else 0,
                    "monitor_window_started": observer.window_started
                    if observer is not None
                    else None,
                    "monitor_window_ended": observer.window_ended if observer is not None else None,
                    "policy": dict(SCAN_POLICY),
                }


def host_dependencies():
    """Newly used ctypes sources/objects; inherited Python/libc pins also apply."""
    import _ctypes
    import _struct

    modules = (ctypes, sys.modules["ctypes._endian"], _ctypes, _struct)
    paths = {
        str(Path(module.__file__).resolve(strict=True))
        for module in modules
        if getattr(module, "__file__", None)
    }
    # Ubuntu can link _struct into the interpreter instead of a separate object.
    paths.add(str(Path(sys.executable).resolve(strict=True)))
    paths.update(
        str(Path(module.__cached__).resolve(strict=True))
        for module in modules
        if getattr(module, "__cached__", None)
    )
    ffi = set()
    for line in Path("/proc/self/maps").read_text().splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) == 6 and Path(fields[5]).name.startswith("libffi.so."):
            ffi.add(str(Path(fields[5]).resolve(strict=True)))
    if len(ffi) != 1:
        raise IntegrityError("Cannot identify the required loaded libffi object")
    return tuple(sorted(paths | ffi))
