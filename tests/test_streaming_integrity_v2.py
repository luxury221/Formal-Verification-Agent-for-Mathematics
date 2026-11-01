"""Actual-byte streaming verification and equivalence with the sealed format."""

from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mra import streaming_integrity_v2 as streaming
from mra.integrity import ContentSnapshot, IntegrityError, file_hash

ROOT = Path(__file__).resolve().parents[1]


class WatchedScanTests(unittest.TestCase):
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
        self.snapshot = streaming.WatchedSnapshot(self.root, deepcopy(self.legacy.data))

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

    def mutate_while_reading(self, action, *, hide_stamps=False):
        original = streaming._record
        changed = False

        def moving(path, before):
            nonlocal changed
            result = original(path, before)
            if path == self.small and not changed:
                changed = True
                action()
            return result

        with patch.object(streaming, "_record", moving):
            if hide_stamps:
                with patch.object(streaming, "_stamp", return_value=("controlled-equal-stamp",)):
                    self.snapshot.verify()
            else:
                self.snapshot.verify()

    def test_same_bytes_with_equal_stamp_control_uses_native_events(self):
        with self.assertRaisesRegex(IntegrityError, "observed by inotify"):
            self.mutate_while_reading(
                lambda: self.small.write_bytes(self.small.read_bytes()), hide_stamps=True
            )
        self.assertGreater(self.snapshot.last_scan["native_events_read"], 0)
        self.assertFalse(self.snapshot.last_scan["matched"])

    def test_changed_then_restored_bytes_are_observed(self):
        original = self.small.read_bytes()

        def restoring():
            self.small.write_bytes(b"temporary tamper")
            self.small.write_bytes(original)

        with self.assertRaisesRegex(IntegrityError, "observed by inotify"):
            self.mutate_while_reading(restoring, hide_stamps=True)
        self.assertEqual(self.small.read_bytes(), original)

    def test_created_then_removed_entry_is_observed(self):
        def transient():
            temporary = self.tree / "transient"
            temporary.write_bytes(b"temporary")
            temporary.unlink()

        with self.assertRaisesRegex(IntegrityError, "observed by inotify"):
            self.mutate_while_reading(transient, hide_stamps=True)
        self.assertFalse((self.tree / "transient").exists())

    def test_hardlink_write_outside_named_parent_scope_is_observed(self):
        alias = self.root / "unwatched-alias"
        os.link(self.small, alias)
        with self.assertRaisesRegex(IntegrityError, "observed by inotify"):
            self.mutate_while_reading(
                lambda: alias.write_bytes(alias.read_bytes()), hide_stamps=True
            )

    def test_unrelated_sibling_and_git_transport_writes_are_filtered(self):
        def unrelated():
            (self.root / "unrelated-log").write_bytes(b"log")
            (self.tree / ".git/transport").write_bytes(b"ignored")

        self.mutate_while_reading(unrelated)
        self.assertTrue(self.snapshot.last_scan["matched"])
        self.assertGreater(self.snapshot.last_scan["native_events_read"], 0)

    def test_post_layout_mutation_is_still_observed(self):
        original = streaming._layout

        def late(root, expected):
            value = original(root, expected)
            (self.tree / "late").write_bytes(b"added after final traversal")
            return value

        with patch.object(streaming, "_layout", late):
            with self.assertRaisesRegex(IntegrityError, "observed by inotify"):
                self.snapshot.verify()

    def test_final_layout_reenumerates_even_with_equal_stamps(self):
        (self.tree / "extra").write_bytes(b"extra")
        with patch.object(streaming, "_stamp", return_value=("same",)):
            with self.assertRaises(IntegrityError):
                streaming._layout(self.tree, self.snapshot.data["trees"]["runtime"])

    def test_write_during_installation_is_not_discarded(self):
        original = streaming._Observer.watch
        changed = False

        def installing(observer, path, **kwargs):
            nonlocal changed
            value = original(observer, path, **kwargs)
            if path == self.small and not changed:
                changed = True
                self.small.write_bytes(self.small.read_bytes())
            return value

        with patch.object(streaming._Observer, "watch", installing):
            with patch.object(streaming, "_stamp", return_value=("same",)):
                with self.assertRaisesRegex(IntegrityError, "observed by inotify"):
                    self.snapshot.verify()
        self.assertTrue(changed)

    def test_native_file_events_from_another_process_are_observed(self):
        import subprocess

        def process_write():
            subprocess.run(
                [
                    "/usr/bin/python3",
                    "-c",
                    "from pathlib import Path; import sys; p=Path(sys.argv[1]); p.write_bytes(p.read_bytes())",
                    str(self.small),
                ],
                check=True,
                timeout=10,
            )

        with self.assertRaisesRegex(IntegrityError, "observed by inotify"):
            self.mutate_while_reading(process_write, hide_stamps=True)

    def test_missing_monitor_and_nonlinux_never_hash_or_fallback(self):
        for target, value in (
            ("sys.platform", "unsupported"),
            ("ctypes.CDLL", OSError("unavailable")),
        ):
            hook = (
                patch.object(streaming.sys, "platform", value)
                if target == "sys.platform"
                else patch.object(streaming.ctypes, "CDLL", side_effect=value)
            )
            with hook, patch.object(streaming, "_record") as hashing:
                with self.assertRaises(IntegrityError):
                    self.snapshot.verify()
                hashing.assert_not_called()
            self.assertFalse(self.snapshot.last_scan["matched"])

    def test_watch_limit_failure_closes_descriptor_without_hashing(self):
        before = len(list(Path("/proc/self/fd").iterdir()))
        original = streaming._Observer.__init__

        def insufficient(observer):
            original(observer)
            observer.add = lambda *args: -1

        with patch.object(streaming._Observer, "__init__", insufficient):
            with patch.object(streaming, "_record") as hashing:
                with self.assertRaisesRegex(IntegrityError, "watch unavailable"):
                    self.snapshot.verify()
                hashing.assert_not_called()
        self.assertEqual(before, len(list(Path("/proc/self/fd").iterdir())))
        self.assertFalse(self.snapshot.last_scan["matched"])

    def test_success_and_rejection_do_not_leak_descriptors(self):
        before = len(list(Path("/proc/self/fd").iterdir()))
        self.snapshot.verify()
        with self.assertRaises(IntegrityError):
            self.mutate_while_reading(
                lambda: self.small.write_bytes(self.small.read_bytes()), hide_stamps=True
            )
        self.assertEqual(before, len(list(Path("/proc/self/fd").iterdir())))

    def test_hardlink_watch_rules_merge_without_losing_paths(self):
        alias = self.root / "alias"
        os.link(self.small, alias)
        observer = streaming._Observer()
        self.addCleanup(observer.close)
        observer.watch(self.small, directory=False)
        observer.watch(alias, directory=False)
        self.assertEqual(len(observer.rules), 1)
        self.assertEqual(len(next(iter(observer.rules.values()))["paths"]), 2)
        alias.write_bytes(alias.read_bytes())
        with self.assertRaisesRegex(IntegrityError, "observed by inotify"):
            observer.check()

    def test_parent_filters_merge_all_relevant_names(self):
        observer = streaming._Observer()
        self.addCleanup(observer.close)
        observer.watch(self.root, directory=True, name="first")
        observer.watch(self.root, directory=True, name="second")
        (self.root / "second").write_bytes(b"change")
        with self.assertRaisesRegex(IntegrityError, "observed by inotify"):
            observer.check()

    def test_native_kernel_queue_overflow_fails_closed(self):
        capacity = int(Path("/proc/sys/fs/inotify/max_queued_events").read_text())
        if capacity > 65536:
            self.skipTest("Kernel queue exceeds bounded resource-test allowance")
        observer = streaming._Observer()
        self.addCleanup(observer.close)
        directory = self.root / "event-storm"
        directory.mkdir()
        observer.watch(directory, directory=True, name="protected")
        # Distinct creates cannot be coalesced. They are irrelevant names until
        # the kernel reports that observation has been lost.
        for index in range(capacity + 16):
            (directory / str(index)).touch()
        with self.assertRaisesRegex(IntegrityError, "lost or overflowed"):
            observer.check()

    def test_malformed_native_event_records_fail_closed(self):
        observer = streaming._Observer()
        self.addCleanup(observer.close)
        observer.watch(self.root, directory=True, name="protected")
        wd = next(iter(observer.rules))
        cases = [
            b"short",
            streaming._EVENT.pack(wd, 0x100, 0, 4) + b"x",
            streaming._EVENT.pack(wd, 0x100, 0, 3) + b"x\0\0",
            streaming._EVENT.pack(wd, 0x100, 0, 4) + b"xxxx",
            streaming._EVENT.pack(wd, 0x100, 0, 4) + b"x\0y\0",
            streaming._EVENT.pack(wd, 0x100, 0, 4) + b"/\0\0\0",
            streaming._EVENT.pack(wd, 0, 0, 0),
            streaming._EVENT.pack(wd + 999999, 0x100, 0, 0),
            streaming._EVENT.pack(wd, 0x80000000, 0, 0),
            streaming._EVENT.pack(-1, streaming._OVERFLOW, 0, 0),
            streaming._EVENT.pack(wd, streaming._IGNORED, 0, 0),
        ]
        for record in cases:
            with self.subTest(record=record):
                with self.assertRaises(IntegrityError):
                    observer.consume(record)

    def test_unbounded_irrelevant_event_stream_fails_closed(self):
        observer = streaming._Observer()
        self.addCleanup(observer.close)
        observer.watch(self.root, directory=True, name="protected")
        wd = next(iter(observer.rules))
        event = streaming._EVENT.pack(wd, 0x100, 0, 4) + b"x\0\0\0"
        with patch.object(streaming.os, "read", return_value=event):
            with self.assertRaisesRegex(IntegrityError, "read budget"):
                observer.check()

    def test_native_read_error_and_empty_stream_fail_closed(self):
        for fault in (b"", OSError("read failure")):
            with self.subTest(fault=str(fault)):
                hook = (
                    patch.object(streaming.os, "read", return_value=fault)
                    if isinstance(fault, bytes)
                    else patch.object(streaming.os, "read", side_effect=fault)
                )
                with hook:
                    with self.assertRaises(IntegrityError):
                        self.snapshot.verify()
                self.assertFalse(self.snapshot.last_scan["matched"])
