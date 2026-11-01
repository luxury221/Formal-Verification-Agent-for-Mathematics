"""Real filesystem corruption contracts and separately enabled full-runtime scans."""

import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from mra.domain import canonical, digest
from mra.integrity import ContentSnapshot, IntegrityError, tree_records as original_records
from mra.runtime_audit import buffered_record, inspect, tree_records, verify
from mra.verifier import NativeLeanBackend

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/runtime-audit-20261028-01"


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tree = self.root / "tree"
        (self.tree / "nested").mkdir(parents=True)
        (self.tree / "empty").write_bytes(b"")
        (self.tree / "content").write_bytes(b"first version")
        (self.tree / "nested/large").write_bytes(b"a" * (2 * 1024 * 1024 + 17))
        (self.tree / "link").symlink_to("nested/large")
        (self.tree / ".git").mkdir()
        (self.tree / ".git/untracked").write_text("Excluded transport bookkeeping")
        (self.root / "fixed").write_bytes(b"fixed data")
        (self.root / "host").write_bytes(b"fake host fixture; not an executable")
        self.snapshot = ContentSnapshot.capture(
            self.root, ("tree",), ("fixed",), (str(self.root / "host"),)
        )
        for name in ("mra/runtime_audit.py", "mra/integrity.py", "mra/domain.py"):
            p = self.root / name
            p.parent.mkdir(exist_ok=True)
            shutil.copyfile(ROOT / name, p)

    def test_complete_records_match_frozen_scanner_with_all_thread_options(self):
        expected = original_records(self.tree)
        for workers in (1, 2, 4):
            records, counts = tree_records(self.tree, workers=workers)
            self.assertEqual(records, expected)
            self.assertEqual(counts["files"], 3)
            self.assertEqual(counts["bytes"], 2 * 1024 * 1024 + 17 + len(b"first version"))
            self.assertEqual(
                verify(self.snapshot, workers=workers)["trees"]["tree"]["records_sha256"],
                digest(expected),
            )

    def test_reused_scratch_buffer_never_hashes_old_tail_bytes(self):
        paths = [self.tree / "nested/large", self.tree / "content", self.tree / "empty"]
        for path in paths * 2:
            record, size = buffered_record(path)
            self.assertEqual(size, path.stat().st_size)
            self.assertEqual(record["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_same_size_and_restored_mtime_cannot_hide_content_change(self):
        path = self.tree / "content"
        before = path.stat()
        path.write_bytes(b"other version")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(path.stat().st_size, before.st_size)
        for workers in (1, 2, 4):
            with self.assertRaises(IntegrityError):
                verify(self.snapshot, workers=workers)

    def test_added_deleted_and_permission_changes_fail_closed(self):
        extra = self.tree / "extra"
        extra.write_bytes(b"extra")
        with self.assertRaises(IntegrityError):
            verify(self.snapshot)
        extra.unlink()
        path = self.tree / "content"
        data, mode = path.read_bytes(), path.stat().st_mode & 0o777
        path.unlink()
        with self.assertRaises(IntegrityError):
            verify(self.snapshot)
        path.write_bytes(data)
        path.chmod(mode ^ 0o100)
        with self.assertRaises(IntegrityError):
            verify(self.snapshot)

    def test_retargeted_and_escaping_links_are_rejected(self):
        link = self.tree / "link"
        link.unlink()
        link.symlink_to("content")
        with self.assertRaises(IntegrityError):
            verify(self.snapshot)
        link.unlink()
        link.symlink_to(self.root / "host")
        with self.assertRaises(IntegrityError):
            verify(self.snapshot)

    def test_fixed_host_and_loaded_seal_bytes_are_rechecked(self):
        for name in ("fixed", "host"):
            path = self.root / name
            original = path.read_bytes()
            path.write_bytes(original + b" ")
            with self.assertRaises(IntegrityError):
                verify(self.snapshot)
            path.write_bytes(original)
        seal = self.root / "seal"
        seal.write_bytes(b"seal")
        self.snapshot._seal_hashes = {"seal": hashlib.sha256(b"seal").hexdigest()}
        seal.write_bytes(b"SEAL")
        with self.assertRaises(IntegrityError):
            verify(self.snapshot)

    def test_file_replaced_with_symlink_between_stat_and_open_fails(self):
        real_open = os.open
        path = self.tree / "content"

        def redirected(name, flags, *args, **kwargs):
            if Path(name) == path:
                path.unlink()
                path.symlink_to("empty")
            return real_open(name, flags, *args, **kwargs)

        with patch("mra.runtime_audit.os.open", side_effect=redirected):
            with self.assertRaises((IntegrityError, OSError)):
                buffered_record(path)

    def test_mutation_after_read_is_detected_by_final_stamp(self):
        path = self.tree / "content"
        actual_fstat, calls = os.fstat, 0

        def changed(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                before = path.stat()
                path.write_bytes(b"other version")
                # Exercise the final-stamp guard without assuming clock resolution.
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
            return actual_fstat(fd)

        with patch("mra.runtime_audit.os.fstat", side_effect=changed):
            with self.assertRaises(IntegrityError):
                buffered_record(path)

    def test_parallel_read_error_cannot_publish_partial_scan(self):
        with patch(
            "mra.runtime_audit.buffered_record",
            side_effect=OSError(errno.EIO, "Contract read failure"),
        ):
            with self.assertRaises(IntegrityError):
                verify(self.snapshot, workers=4)

    def test_diagnostic_is_never_math_evidence_and_does_not_execute_lean(self):
        with (
            patch.object(ContentSnapshot, "load_runtime", return_value=self.snapshot),
            patch.object(
                NativeLeanBackend,
                "_run_worker",
                side_effect=AssertionError("No Lean in diagnostics"),
            ),
        ):
            result = inspect(self.root)
        self.assertTrue(result["observed_integrity_ok"])
        self.assertEqual(
            (result["math_verdict"], result["evidence_level"], result["lean_calls"]),
            ("UNKNOWN", "E0", 0),
        )
        self.assertFalse(result["default_verification_path_changed"])
        self.assertTrue(
            json.loads(Path(result["report_path"]).read_text())["historical_record_when_saved"]
        )

    def test_failed_diagnostic_saves_unknown_without_success_metrics(self):
        (self.tree / "content").write_bytes(b"corrupt")
        with patch.object(ContentSnapshot, "load_runtime", return_value=self.snapshot):
            result = inspect(self.root)
        self.assertEqual(result["run_status"], "POLICY_BLOCKED")
        self.assertFalse(result["observed_integrity_ok"])
        self.assertIsNone(result["scan"])
        self.assertTrue(Path(result["report_path"]).is_file())

    def test_storage_redirect_and_invalid_threads_are_rejected(self):
        for workers in (True, 0, 3, 5, "1"):
            with self.assertRaises(ValueError):
                inspect(self.root, workers=workers)
        (self.root / "artifacts").symlink_to(self.tree, target_is_directory=True)
        with self.assertRaises(ValueError):
            inspect(self.root)


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "Explicit actual sealed runtime scan")
class RealTests(unittest.TestCase):
    def test_full_runtime_scan_matches_frozen_records_and_all_original_scopes(self):
        out = OUT / ("real-scan-" + uuid.uuid4().hex)
        out.mkdir()
        snapshot = ContentSnapshot.load_runtime(ROOT)
        snapshot.verify()
        result = verify(snapshot)
        self.assertTrue(result["complete"])
        for name, records in snapshot.data["trees"].items():
            self.assertEqual(result["trees"][name]["records_sha256"], digest(records))
        self.assertEqual(
            result["runtime_tree_totals"]["entries"],
            sum(len(v) for v in snapshot.data["trees"].values()),
        )
        self.assertEqual(result["fixed_files"], len(snapshot.data["files"]))
        self.assertEqual(result["host_files"], len(snapshot.data["host_files"]))
        (out / "report.json").write_bytes(canonical(result) + b"\n")

    def test_actual_cli_diagnostic_preserves_seals_and_reports_no_math_verdict(self):
        out = OUT / ("real-cli-" + uuid.uuid4().hex)
        out.mkdir()
        files = (".runtime/runtime-snapshot.json", "lean_workspace/runtime-lock.json")
        before = {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in files}
        value = subprocess.run(
            [sys.executable, "-m", "mra", "runtime-audit"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=ROOT,
            timeout=120,
        )
        (out / "stdout.log").write_text(value.stdout)
        (out / "stderr.log").write_text(value.stderr)
        self.assertEqual(value.returncode, 0, value.stderr)
        result = json.loads(value.stdout)
        self.assertEqual(
            (result["math_verdict"], result["evidence_level"], result["lean_calls"]),
            ("UNKNOWN", "E0", 0),
        )
        self.assertTrue(result["observed_integrity_ok"])
        self.assertEqual(
            before, {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in files}
        )
        (out / "report.json").write_bytes(canonical(result) + b"\n")
