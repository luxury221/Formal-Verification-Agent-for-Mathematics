"""Actual-byte streaming verification and equivalence with the sealed format."""

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mra import streaming_integrity as streaming
from mra.domain import canonical, digest
from mra.integrity import ContentSnapshot, IntegrityError, file_hash

ROOT = Path(__file__).resolve().parents[1]


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tree = self.root / "runtime"
        self.tree.mkdir()
        self.small = self.tree / "small"
        self.small.write_bytes(b"sealed small file")
        for index in range(12):
            (self.tree / ("large" + str(index))).write_bytes(bytes([index]) * 131073)
        (self.tree / "directory").mkdir()
        (self.tree / "directory/link").symlink_to("../small")
        (self.tree / ".git").mkdir()
        (self.tree / ".git/transport").write_bytes(b"ignored transport")
        (self.root / "source.py").write_bytes(b"trusted source")
        host = self.root / "host"
        host.write_bytes(b"host dependency")
        self.legacy = ContentSnapshot.capture(self.root, ("runtime",), ("source.py",), (str(host),))
        self.snapshot = streaming.StreamingSnapshot(self.root, deepcopy(self.legacy.data))

    def test_unchanged_scope_matches_legacy_and_reads_every_time(self):
        self.legacy.verify()
        self.snapshot.verify()
        first = self.snapshot.last_scan
        self.assertEqual(self.snapshot.fingerprint, self.legacy.fingerprint)
        self.snapshot.verify()
        second = self.snapshot.last_scan
        self.assertTrue(first["matched"] and second["matched"])
        self.assertEqual(first["bytes_hashed"], second["bytes_hashed"])
        self.assertGreater(first["bytes_hashed"], 12 * 131073)
        self.assertEqual(first["trees"]["runtime"]["files"], 13)
        self.assertEqual(first["trees"]["runtime"]["symlinks"], 1)
        self.assertEqual(first["parallel_jobs"], 12)
        self.assertLessEqual(first["peak_pending"], 8)
        self.assertFalse(first["policy"]["success_cache"])
        self.assertEqual(first["level"], "E0")

    def test_same_length_same_mtime_content_change_is_rejected(self):
        self.snapshot.verify()
        before = self.small.stat()
        self.small.write_bytes(b"changed small txt")
        self.assertEqual(self.small.stat().st_size, before.st_size)
        os.utime(self.small, ns=(before.st_atime_ns, before.st_mtime_ns))
        for snapshot in (self.legacy, self.snapshot):
            with self.assertRaises(IntegrityError):
                snapshot.verify()
        self.assertFalse(self.snapshot.last_scan["matched"])

    def test_missing_and_extra_entries_are_rejected(self):
        extra = self.tree / "unexpected"
        extra.write_bytes(b"extra")
        with self.assertRaises(IntegrityError):
            self.snapshot.verify()
        extra.unlink()
        self.small.unlink()
        with self.assertRaises(IntegrityError):
            self.snapshot.verify()

    def test_file_and_directory_permission_changes_are_rejected(self):
        original = self.small.stat().st_mode & 0o7777
        self.small.chmod(original ^ 0o100)
        with self.assertRaises(IntegrityError):
            self.snapshot.verify()
        self.small.chmod(original)
        directory = self.tree / "directory"
        directory.chmod((directory.stat().st_mode & 0o7777) ^ 0o100)
        with self.assertRaises(IntegrityError):
            self.snapshot.verify()

    def test_symlink_escape_redirect_and_cycle_are_rejected(self):
        link = self.tree / "directory/link"
        for target in ("../../source.py", "../large0", "link"):
            link.unlink()
            link.symlink_to(target)
            with self.assertRaises(IntegrityError):
                self.snapshot.verify()

    def test_nonregular_file_is_rejected_without_opening_fifo(self):
        self.small.unlink()
        os.mkfifo(self.small)
        with self.assertRaises(IntegrityError):
            self.snapshot.verify()

    def test_source_symlink_and_host_resolution_change_are_rejected(self):
        source = self.root / "source.py"
        source.unlink()
        source.symlink_to("host")
        with self.assertRaises(IntegrityError):
            self.snapshot.verify()
        source.unlink()
        source.write_bytes(b"trusted source")
        host = self.root / "host"
        other = self.root / "other-host"
        other.write_bytes(host.read_bytes())
        host.unlink()
        host.symlink_to("other-host")
        with self.assertRaises(IntegrityError):
            self.snapshot.verify()

    def test_same_bytes_rewrite_during_open_is_rejected(self):
        original = streaming._record
        changed = False

        def moving(path, before):
            nonlocal changed
            if path == self.small and not changed:
                changed = True
                path.write_bytes(path.read_bytes())
            return original(path, before)

        with patch.object(streaming, "_record", moving):
            with self.assertRaises(IntegrityError):
                self.snapshot.verify()
        self.assertTrue(changed)

    def test_directory_growth_during_hashing_is_rejected(self):
        original = streaming._record
        changed = False

        def growing(path, before):
            nonlocal changed
            result = original(path, before)
            if path == self.small and not changed:
                changed = True
                (self.tree / "late-entry").write_bytes(b"late")
            return result

        with patch.object(streaming, "_record", growing):
            with self.assertRaises(IntegrityError):
                self.snapshot.verify()
        self.assertTrue(changed)

    def test_git_transport_is_excluded_exactly_as_in_legacy(self):
        (self.tree / ".git/transport").write_bytes(b"different ignored content")
        self.legacy.verify()
        self.snapshot.verify()

    def test_seal_is_rehashed_and_cannot_be_redirected(self):
        seal = self.root / "seal.json"
        seal.write_bytes(b"original")
        self.snapshot._seal_hashes = {"seal.json": file_hash(seal)}
        self.snapshot.verify()
        before = seal.stat()
        seal.write_bytes(b"modified")
        os.utime(seal, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaises(IntegrityError):
            self.snapshot.verify()
        seal.unlink()
        seal.symlink_to("host")
        with self.assertRaises(IntegrityError):
            self.snapshot.verify()

    def test_fifo_swap_between_stat_and_open_is_rejected(self):
        original = os.open
        changed = False

        def swapping(path, flags, *args, **kwargs):
            nonlocal changed
            if Path(path) == self.small and not changed:
                changed = True
                self.small.unlink()
                os.mkfifo(self.small)
            return original(path, flags, *args, **kwargs)

        with patch.object(streaming.os, "open", swapping):
            with self.assertRaises(IntegrityError):
                self.snapshot.verify()
        self.assertTrue(changed)

    def test_seal_change_after_initial_read_is_rejected(self):
        seal = self.root / "seal.json"
        seal.write_bytes(b"original")
        self.snapshot._seal_hashes = {"seal.json": file_hash(seal)}
        original = streaming._record

        def changing(path, before):
            result = original(path, before)
            if path == self.small:
                seal.write_bytes(b"modified")
            return result

        with patch.object(streaming, "_record", changing):
            with self.assertRaises(IntegrityError):
                self.snapshot.verify()

    def test_loader_validates_scope_metadata_and_actual_bytes(self):
        data = deepcopy(self.legacy.data)
        data["provenance"] = {
            "mode": streaming.foundation.MODE,
            "import_scope": [streaming.foundation.IMPORT],
            "catalog": digest(streaming.foundation.catalog()),
        }

        def seal(value):
            raw = canonical(value) + b"\n"
            (self.root / "snapshot.json").write_bytes(raw)
            (self.root / "lock.json").write_bytes(
                canonical(
                    {
                        "mode": streaming.foundation.MODE,
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }
                )
            )

        with patch.multiple(
            streaming.foundation,
            TREES=("runtime",),
            FILES=("source.py",),
            SNAPSHOT="snapshot.json",
            LOCK="lock.json",
        ):
            seal(data)
            loaded = streaming.load(self.root)
            self.assertEqual(loaded.fingerprint, digest(data))
            self.assertTrue(loaded.last_scan["matched"])
            for bad in (
                [],
                {},
                {**data, "trees": []},
                {**data, "host_files": {}},
                {**data, "files": {}},
                {**data, "provenance": {}},
                {**data, "schema_version": "2"},
            ):
                seal(bad)
                with self.assertRaises(IntegrityError):
                    streaming.load(self.root)
            seal(data)
            self.small.write_bytes(b"changed small txt")
            with self.assertRaises(IntegrityError):
                streaming.load(self.root)
