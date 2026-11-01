"""Content snapshots for the trusted runtime; verification never updates a baseline."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from .domain import canonical, digest

TREES = (".runtime/lean-4.35.0-linux", ".runtime/mathlib")
FILES = (
    "mra/nat_ideas.py",
    "lean_workspace/nat-search-checker.lean.in",
    "lean_workspace/int-search-checker.lean.in",
    "lean_workspace/environment.json",
    "lean_workspace/evidence-policy.json",
    "lean_workspace/replay-checker.lean.in",
    "lean_workspace/certificate-checker.lean.in",
    "evals/golden_cases.json",
    "evals/nat-arithmetic-v1/cases.json",
    "evals/datasets_manifest/nat-arithmetic-v1.json",
    "ops/scripts/sandbox_worker.py",
    ".runtime/lean-release.json",
    ".runtime/mathlib-sources.json",
    ".runtime/mathlib-cache-files.json",
)
SNAPSHOT = ".runtime/runtime-snapshot.json"
LOCK = "lean_workspace/runtime-lock.json"


class IntegrityError(RuntimeError):
    """The installed runtime no longer matches its explicitly sealed baseline."""


def file_hash(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as incoming:
        for block in iter(lambda: incoming.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _stamp(info: os.stat_result) -> tuple:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def file_record(path: Path) -> dict:
    """Hash actual bytes even when size/mtime match; refuse a moving file."""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise IntegrityError(f"Expected a regular file: {path}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as incoming:
        if _stamp(before) != _stamp(os.fstat(incoming.fileno())):
            raise IntegrityError(f"File changed while opening: {path}")
        hasher = hashlib.sha256()
        for block in iter(lambda: incoming.read(1024 * 1024), b""):
            hasher.update(block)
        after = os.fstat(incoming.fileno())
    if _stamp(before) != _stamp(after) or _stamp(after) != _stamp(path.lstat()):
        raise IntegrityError(f"File changed while hashing: {path}")
    return {"kind": "file", "sha256": hasher.hexdigest(), "mode": stat.S_IMODE(after.st_mode)}


def tree_records(root: Path) -> dict:
    records = {}
    if root.is_symlink() or not root.is_dir():
        raise IntegrityError(f"Missing or redirected runtime directory: {root}")

    def visit(directory: Path) -> None:
        records[directory.relative_to(root).as_posix()] = {
            "kind": "directory",
            "mode": stat.S_IMODE(directory.lstat().st_mode),
        }
        for path in sorted(directory.iterdir()):
            if path.name == ".git":
                continue  # Git transport bookkeeping is not a runtime dependency.
            name = path.relative_to(root).as_posix()
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                if not path.resolve(strict=True).is_relative_to(root.resolve()):
                    raise IntegrityError(f"Runtime symlink escapes its tree: {path}")
                records[name] = {"kind": "symlink", "target": os.readlink(path)}
            elif stat.S_ISDIR(info.st_mode):
                visit(path)
            else:
                records[name] = file_record(path)

    visit(root)
    return records


def host_record(path: Path) -> dict:
    resolved = path.resolve(strict=True)
    return {"resolved": str(resolved), **file_record(resolved)}


class ContentSnapshot:
    def __init__(self, root: Path, data: dict) -> None:
        self.root = root.resolve()
        self.data = data
        self.fingerprint = digest(data)
        self._seal_hashes: dict[str, str] = {}

    @classmethod
    def capture(
        cls,
        root: Path,
        trees: tuple[str, ...],
        files: tuple[str, ...],
        host_files: tuple[str, ...] = (),
        provenance: dict | None = None,
    ) -> ContentSnapshot:
        return cls(
            root,
            {
                "schema_version": "1",
                "trees": {name: tree_records(root / name) for name in trees},
                "files": {name: file_record(root / name) for name in files},
                "host_files": {name: host_record(Path(name)) for name in host_files},
                "provenance": provenance or {},
            },
        )

    @classmethod
    def load_runtime(cls, root: Path) -> ContentSnapshot:
        try:
            lock = json.loads((root / LOCK).read_text(encoding="utf-8"))
            raw = (root / SNAPSHOT).read_bytes()
            if lock.get("schema_version") != "1" or hashlib.sha256(raw).hexdigest() != lock.get(
                "sha256"
            ):
                raise IntegrityError("Runtime snapshot was changed; its local lock does not match")
            data = json.loads(raw)
            if (
                data.get("schema_version") != "1"
                or set(data["trees"]) != set(TREES)
                or set(data["files"]) != set(FILES)
                or not data.get("host_files")
            ):
                raise IntegrityError("Runtime snapshot has an unsupported or incomplete scope")
            snapshot = cls(root, data)
            snapshot._seal_hashes = {
                SNAPSHOT: hashlib.sha256(raw).hexdigest(),
                LOCK: file_hash(root / LOCK),
            }
            return snapshot
        except (OSError, KeyError, ValueError) as error:
            raise IntegrityError(
                "Runtime snapshot unavailable; run ops/scripts/seal_runtime.py"
            ) from error

    def verify(self) -> None:
        try:
            for name, expected in self._seal_hashes.items():
                if file_hash(self.root / name) != expected:
                    raise IntegrityError(f"Runtime seal changed: {name}")
            for name, expected in self.data["files"].items():
                if file_record(self.root / name) != expected:
                    raise IntegrityError(f"Runtime file changed: {name}")
            for name, expected in self.data["trees"].items():
                actual = tree_records(self.root / name)
                if actual != expected:
                    changed = sorted(
                        k
                        for k in expected.keys() | actual.keys()
                        if expected.get(k) != actual.get(k)
                    )
                    raise IntegrityError(f"Runtime tree changed: {name}/{changed[0]}")
            for name, expected in self.data["host_files"].items():
                if host_record(Path(name)) != expected:
                    raise IntegrityError(f"Host runtime dependency changed: {name}")
        except OSError as error:
            raise IntegrityError(
                f"Runtime dependency missing or unreadable: {error.filename}"
            ) from error

    def write(self, replace: bool = False) -> None:
        snapshot_path, lock_path = self.root / SNAPSHOT, self.root / LOCK
        if not replace and (snapshot_path.exists() or lock_path.exists()):
            raise IntegrityError(
                "A runtime seal already exists; --replace requires an intentional revalidation"
            )
        raw = canonical(self.data) + b"\n"
        snapshot_path.write_bytes(raw)
        lock_path.write_bytes(
            canonical({"schema_version": "1", "sha256": hashlib.sha256(raw).hexdigest()}) + b"\n"
        )
