"""Metadata contracts with explicitly simulated workers, never mathematical evidence."""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from mra import analysis_foundation as runtime, studio_premises
from mra.domain import canonical
from mra.integrity import file_hash
from mra.premise_metadata import (
    PremiseMetadata,
    READER_FILES,
    SCHEMA,
    checker_slot,
    parse_report,
    render,
)
from mra.premise_search import PremiseIndex
from mra.research_flow import atomic
from mra.research_studio import StudioStore
from mra.tasks import TaskStore, WorkerBusy
from test_http_api import copy_web_inputs, request, running_server
from test_premise_search import install_fixture_sources
import test_research_studio as studio_fixtures

ROOT = Path(__file__).resolve().parents[1]


class PremiseMetadataTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        copy_web_inputs(self.root)
        source = install_fixture_sources(self.root)
        self.index = PremiseIndex(self.root)
        self.index.build()
        self.item = self.index.search({"query": "uniformContinuous_fixture", "limit": 1})["hits"][0]
        for name in READER_FILES:
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, self.root / name)
        (self.root / runtime.SNAPSHOT).parent.mkdir(parents=True, exist_ok=True)
        (self.root / runtime.SNAPSHOT).write_text('{"fixture":"not a real executable runtime"}')
        (self.root / runtime.LOCK).write_bytes(
            canonical({"mode": runtime.MODE, "sha256": file_hash(self.root / runtime.SNAPSHOT)})
        )
        executable_source = self.root / runtime.TREES[1] / self.item["path"]
        executable_source.parent.mkdir(parents=True)
        shutil.copy2(source, executable_source)
        self.reader = PremiseMetadata(self.root)

    def report(self, names=None):
        names = names if names is not None else ["Fixture.uniformContinuous_fixture"]
        return {
            "schema": SCHEMA,
            "module": self.item["module"],
            "written_name": self.item["written_name"],
            "matches": [
                {
                    "name": name,
                    "module": self.item["module"],
                    "kind": "theorem",
                    "universe_parameters": ["u"],
                    "explicit_type": "∀ {α : Type u}, True",
                    "type": "∀ {α : Type u}, True",
                    "binders": [{"name": "α", "kind": "implicit", "type": "Type u"}],
                    "conclusion": "True",
                }
                for name in names
            ],
        }

    def start(self, request_id="fixture-read"):
        with patch("mra.premise_metadata.subprocess.Popen") as popen:
            row = self.reader.start(self.item, request_id)
            return row, popen

    def execute(self, report=None, execution=None):
        row, _ = self.start()
        key = self.reader.location(row["identity"]).name
        execution = execution or {
            "run_status": "COMPLETED",
            "returncode": 0,
            "pid": 123,
            "elapsed_seconds": 0.01,
        }
        report = report or self.report()

        def worker(work, sandbox, stdout, stderr, budget, **kwargs):
            self.assertEqual(budget, 40)
            self.assertIn("lifecycle_lock_fd", kwargs)
            stdout.write_text("Candidate.lean:1:0: info: MRA_METADATA:" + json.dumps(report) + "\n")
            stderr.write_text("")
            return execution

        snapshot = Mock()
        # These are contract fixtures; no Lean executable is launched.
        with (
            patch.object(runtime, "load", return_value=snapshot),
            patch.object(runtime._Runner, "_run_worker", side_effect=worker),
            (self.root / "fixture-fd").open("wb") as fd,
        ):
            self.reader.execute(key, fd.fileno(), fd.fileno())
        snapshot.verify.assert_called_once()
        return self.reader.status(self.item)

    def study(self):
        self.store = StudioStore(self.root, service_factory=studio_fixtures.Fixture)
        row = self.store.create({"request_id": "study", "source": studio_fixtures.source()})
        self.key = row["study_id"]
        row = self.store.scaffold(self.key, self.mutation(row))
        return studio_premises.attach(
            self.store, self.key, self.mutation(row, node_id="root", item_id=self.item["item_id"])
        )

    @staticmethod
    def mutation(row, **extra):
        return {
            "request_id": "fixture-request-" + str(row["revision"]),
            "expected_revision": row["revision"],
            **extra,
        }

    def test_only_module_identifier_enters_template_not_source_name(self):
        source = render(self.root, self.item)
        self.assertEqual(
            source, render(self.root, {**self.item, "written_name": "x\nrun_cmd evil"})
        )
        with self.assertRaisesRegex(ValueError, "module"):
            render(self.root, {**self.item, "module": "Mathlib.Data.Nat.Basic\nrun_cmd evil"})

    def test_parser_rejects_wrong_module_duplicate_marker_and_foreign_name(self):
        for field, value in (("module", "Mathlib.Forged"), ("written_name", "forged")):
            report = {**self.report(), field: value}
            with self.assertRaises(ValueError):
                parse_report("MRA_METADATA:" + json.dumps(report), self.item)
        with self.assertRaises(ValueError):
            parse_report("MRA_METADATA:{}\nMRA_METADATA:{}", self.item)
        with self.assertRaises(ValueError):
            parse_report("MRA_METADATA:" + json.dumps(self.report(["Wrong.name"])), self.item)

    def test_success_preserves_implicit_parameters_and_complete_execution_artifacts(self):
        row = self.execute()
        self.assertEqual(row["state"], "READY")
        self.assertEqual(row["report"]["matches"][0]["binders"][0]["kind"], "implicit")
        self.assertEqual((row["math_verdict"], row["evidence_level"]), ("UNKNOWN", "E0"))
        self.assertEqual(len(row["artifact_hashes"]), 4)
        with patch("mra.premise_metadata.subprocess.Popen") as popen:
            self.reader.start(self.item, "different-explicit-request")
            popen.assert_not_called()

    def test_ambiguity_and_missing_are_not_silently_resolved(self):
        row = self.execute(
            self.report(["B.uniformContinuous_fixture", "A.uniformContinuous_fixture"])
        )
        self.assertEqual(row["state"], "AMBIGUOUS")
        self.assertEqual(row["report"]["matches"][0]["name"], "A.uniformContinuous_fixture")
        self.assertEqual(
            parse_report("MRA_METADATA:" + json.dumps(self.report([])), self.item)["matches"], []
        )

    def test_timeout_retains_unknown_without_automatic_retry(self):
        row = self.execute(
            execution={
                "run_status": "TIMEOUT",
                "returncode": None,
                "pid": 123,
                "elapsed_seconds": 40,
            }
        )
        self.assertEqual((row["state"], row["math_verdict"]), ("TIMEOUT", "UNKNOWN"))
        with patch("mra.premise_metadata.subprocess.Popen") as popen:
            self.reader.status(self.item)
            self.reader.start(self.item, "fixture-read")
            popen.assert_not_called()

    def test_interrupted_worker_needs_explicit_new_request_and_keeps_history(self):
        first, _ = self.start()
        self.assertEqual(self.reader.status(self.item)["state"], "INTERRUPTED")
        with patch("mra.premise_metadata.subprocess.Popen") as popen:
            self.reader.status(self.item)
            popen.assert_not_called()
        second, popen = self.start("explicit-second-read")
        self.assertNotEqual(first["attempt_id"], second["attempt_id"])
        self.assertEqual(popen.call_count, 1)
        self.assertTrue(
            (self.reader.location(first["identity"]) / first["attempt_id"] / "record.json").exists()
        )

    def test_global_checker_lock_prevents_competing_metadata_worker(self):
        with (
            TaskStore(self.root).worker_lock(),
            patch("mra.premise_metadata.subprocess.Popen") as popen,
        ):
            with self.assertRaises(WorkerBusy):
                self.reader.start(self.item, "busy")
            popen.assert_not_called()

    def test_short_idle_lock_collision_can_release_without_repeating_execution(self):
        lock = TaskStore(self.root).worker_lock()
        lock.__enter__()
        with patch(
            "mra.premise_metadata.time.sleep", side_effect=lambda _: lock.__exit__(None, None, None)
        ) as pause:
            with checker_slot(self.root):
                with self.assertRaises(WorkerBusy):
                    with TaskStore(self.root).worker_lock():
                        pass
            pause.assert_called_once()

    def test_changed_reader_invalidates_cache_and_changed_logs_excluded_from_context(self):
        row = self.execute()
        study = self.study()
        context = studio_premises.context(self.root, self.store.read(self.key), "root")
        self.assertEqual(context["declaration_metadata"][0]["resolution"], "READY")
        path = self.reader.location(row["identity"]) / row["attempt_id"] / "stdout.log"
        path.write_text("forged report")
        state = self.store.report(self.key)["citation_states"][0]
        self.assertEqual(state["status"], "AVAILABLE")
        self.assertEqual(state["metadata"]["state"], "UNAVAILABLE")
        self.assertEqual(
            studio_premises.context(self.root, self.store.read(self.key), "root")[
                "declaration_metadata"
            ],
            [],
        )
        self.assertEqual(self.store.report(self.key)["source"], study["source"])
        (self.root / READER_FILES[1]).write_text("changed reader")
        self.assertEqual(self.reader.status(self.item)["state"], "NOT_READ")

    def test_withdrawn_reference_excluded_from_metadata_model_context(self):
        self.execute()
        row = self.study()
        studio_premises.withdraw(
            self.store, self.key, self.mutation(row, citation_id=row["citations"][0]["citation_id"])
        )
        self.assertEqual(
            studio_premises.context(self.root, self.store.read(self.key), "root")[
                "declaration_metadata"
            ],
            [],
        )

    def test_forged_ready_state_or_request_does_not_survive_cache_validation(self):
        row = self.execute()
        directory = self.reader.location(row["identity"])
        changed = deepcopy(row)
        changed["state"] = "AMBIGUOUS"
        atomic(directory / "record.json", changed)
        with self.assertRaisesRegex(ValueError, "resolution"):
            self.reader.status(self.item)
        changed = deepcopy(row)
        changed["execution"]["run_status"] = "TIMEOUT"
        atomic(directory / "record.json", changed)
        with self.assertRaisesRegex(ValueError, "report"):
            self.reader.status(self.item)

    def test_http_route_requires_csrf_current_revision_and_active_reference(self):
        row = self.study()
        body = self.mutation(row, citation_id=row["citations"][0]["citation_id"])
        path = "/v1/studio/" + self.key + "/inspect-premise"
        with running_server(self.root) as server, patch.object(PremiseMetadata, "start") as start:
            self.assertEqual(
                request(server, "POST", path, body, headers={"X-MRA-CSRF": "bad"})[0], 403
            )
            self.assertEqual(
                request(server, "POST", path, {**body, "expected_revision": -1})[0], 400
            )
            self.assertEqual(request(server, "POST", path, body)[0], 200)
            start.assert_called_once()
            studio_premises.withdraw(
                self.store, self.key, self.mutation(row, citation_id=body["citation_id"])
            )
            latest = self.store.report(self.key)
            self.assertEqual(
                request(server, "POST", path, {**body, "expected_revision": latest["revision"]})[0],
                400,
            )


if __name__ == "__main__":
    unittest.main()
