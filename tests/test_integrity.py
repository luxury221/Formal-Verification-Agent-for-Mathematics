"""Exercise content and topology tampering on disposable dependency copies."""

import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest

from mra.integrity import (
    ContentSnapshot,
    FILES,
    LOCK,
    SNAPSHOT,
    TREES,
    IntegrityError,
    canonical,
)
from ops.scripts.seal_runtime import verify_members
from mra.verifier import NativeLeanBackend


class SnapshotIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = self.root / "runtime/lib/lean/Init.olean"
        self.library.parent.mkdir(parents=True)
        self.library.write_bytes(b"trusted import")
        self.worker = self.root / "worker.py"
        self.worker.write_text("trusted launcher\n")
        self.snapshot = ContentSnapshot.capture(self.root, ("runtime",), ("worker.py",))

    def test_unchanged_snapshot_verifies(self):
        self.snapshot.verify()

    def test_same_size_and_restored_mtime_cannot_hide_modified_import(self):
        before = self.library.stat()
        self.library.write_bytes(b"altered import")
        os.utime(self.library, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(IntegrityError, "Init.olean"):
            self.snapshot.verify()

    def test_removed_import_is_rejected(self):
        self.library.unlink()
        with self.assertRaises(IntegrityError):
            self.snapshot.verify()

    def test_added_dynamic_library_is_rejected(self):
        (self.library.parent / "injected.so").write_bytes(b"injected library")
        with self.assertRaisesRegex(IntegrityError, "injected.so"):
            self.snapshot.verify()

    def test_launcher_change_is_rejected(self):
        self.worker.write_text("changed launcher\n")
        with self.assertRaisesRegex(IntegrityError, "worker.py"):
            self.snapshot.verify()

    @unittest.skipIf(os.name == "nt", "POSIX modes and symlinks")
    def test_symlink_redirect_and_mode_change_are_rejected(self):
        alias = self.library.parent / "alias.olean"
        alias.symlink_to(self.library.name)
        snapshot = ContentSnapshot.capture(self.root, ("runtime",), ())
        alias.unlink()
        alias.symlink_to(self.worker)
        with self.assertRaisesRegex(IntegrityError, "escapes"):
            snapshot.verify()
        alias.unlink()
        self.library.chmod(0o700)
        with self.assertRaises(IntegrityError):
            self.snapshot.verify()

    def test_host_library_change_is_rejected(self):
        snapshot = ContentSnapshot.capture(self.root, (), (), (str(self.worker),))
        self.worker.write_text("changed host library\n")
        with self.assertRaisesRegex(IntegrityError, "Host runtime"):
            snapshot.verify()

    def test_missing_or_edited_baseline_cannot_be_loaded(self):
        with self.assertRaises(IntegrityError):
            ContentSnapshot.load_runtime(self.root)
        (self.root / ".runtime").mkdir()
        (self.root / "lean_workspace").mkdir()
        self.snapshot.write()
        with self.assertRaisesRegex(IntegrityError, "incomplete scope"):
            ContentSnapshot.load_runtime(self.root)
        (self.root / SNAPSHOT).write_text("{}\n")
        with self.assertRaisesRegex(IntegrityError, "lock does not match"):
            ContentSnapshot.load_runtime(self.root)

    def test_existing_baseline_is_not_silently_replaced(self):
        (self.root / ".runtime").mkdir()
        (self.root / "lean_workspace").mkdir()
        self.snapshot.write()
        self.worker.write_text("changed launcher\n")
        changed = ContentSnapshot.capture(self.root, ("runtime",), ("worker.py",))
        with self.assertRaisesRegex(IntegrityError, "already exists"):
            changed.write()

    def test_edited_seal_invalidates_loaded_snapshot(self):
        for name in TREES:
            (self.root / name).mkdir(parents=True)
        for name in FILES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n")
        snapshot = ContentSnapshot.capture(self.root, TREES, FILES, (str(self.worker),))
        snapshot.write()
        loaded = ContentSnapshot.load_runtime(self.root)
        loaded.verify()
        lock_path = self.root / LOCK
        lock_path.write_bytes(canonical({**json.loads(lock_path.read_text()), "extra": "changed"}))
        with self.assertRaisesRegex(IntegrityError, "seal changed"):
            loaded.verify()

    def test_changed_executable_is_blocked_before_version_probe(self):
        for name in TREES:
            (self.root / name).mkdir(parents=True)
        for name in FILES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n")
        lean = self.root / TREES[0] / "bin/lean"
        lean.parent.mkdir()
        lean.write_text("#!/bin/sh\nexit 0\n")
        lean.chmod(0o755)
        ContentSnapshot.capture(self.root, TREES, FILES, (str(self.worker),)).write()
        marker = self.root / "untrusted-code-ran"
        lean.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
        with self.assertRaises(IntegrityError):
            NativeLeanBackend(self.root)
        self.assertFalse(marker.exists())

    def test_archive_validation_rejects_installed_tampering(self):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w") as archive:
            member = tarfile.TarInfo("official/Init.olean")
            member.size = len(b"official bytes")
            archive.addfile(member, io.BytesIO(b"official bytes"))
        self.library.write_bytes(b"modified bytes")
        raw.seek(0)
        with tarfile.open(fileobj=raw, mode="r") as archive:
            with self.assertRaisesRegex(IntegrityError, "differs from its archive"):
                verify_members(archive, self.library.parent)


if __name__ == "__main__":
    unittest.main()
