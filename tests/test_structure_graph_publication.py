"""Deterministic publication/worker interleaving and interruption contracts; no Lean."""

from copy import deepcopy
import unittest
from unittest.mock import patch

import test_structure_research as core
from mra.structure_graph_publication import PublishedStructureResearchStore
from mra.structure_research import StructureGraphChecks
from mra.verifier import NativeLeanBackend


class ContractTests(unittest.TestCase):
    def setUp(self):
        core.ContractTests.setUp(self)
        self.publisher = PublishedStructureResearchStore(self.root)
        self.raw = {
            "request_id": core.rid(),
            "request": deepcopy(self.value["plan"]["request"]),
            "plan_fingerprint": self.value["plan"]["plan_fingerprint"],
        }

    def test_background_scan_never_observes_partially_created_graph(self):
        original = StructureGraphChecks.__init__
        seen = []

        def during(checks, root, directory, create=False):
            if create:
                self.assertTrue(directory.name.startswith(".creating-structureflow-"))
                self.assertFalse((directory / "record.json").exists())
                before = self.publisher.paths()
                self.assertEqual(self.store.work_once()["worker_state"], "IDLE")
                catalog = self.store.catalog(self.project["project_id"], "main")
                self.assertEqual(len(catalog["graphs"]), len(before))
                self.assertFalse((directory / "invalidated.json").exists())
                seen.append(directory)
            original(checks, root, directory, create)

        with (
            patch.object(StructureGraphChecks, "__init__", during),
            patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")),
        ):
            saved = self.publisher.create(self.raw)
        self.assertEqual(len(seen), 1)
        self.assertFalse(seen[0].exists())
        self.assertFalse(saved["invalidated"])
        self.assertEqual(saved["math_verdict"], "UNKNOWN")
        self.assertEqual(self.publisher.create(self.raw)["graph_id"], saved["graph_id"])
        self.assertFalse(list(self.publisher.base.rglob("invalidated.json")))

    def test_failed_preparation_remains_hidden_and_does_not_consume_a_graph(self):
        before = self.publisher.paths()
        with patch.object(
            StructureGraphChecks, "__init__", side_effect=RuntimeError("publication interruption")
        ):
            with self.assertRaisesRegex(RuntimeError, "publication interruption"):
                self.publisher.create(self.raw)
        self.assertEqual(self.publisher.paths(), before)
        stages = list(self.publisher.base.glob(".creating-*"))
        self.assertEqual(len(stages), 1)
        self.assertTrue((stages[0] / "plan.json").exists())
        self.assertFalse((stages[0] / "record.json").exists())
        self.assertEqual(self.store.work_once()["worker_state"], "IDLE")
        saved = self.publisher.create(self.raw)
        self.assertFalse(saved["invalidated"])
        self.assertTrue(stages[0].exists())

    def test_exit_after_rename_is_recovered_by_same_request_without_republication(self):
        original = self.publisher.report
        with patch.object(self.publisher, "report", side_effect=SystemExit(73)):
            with self.assertRaises(SystemExit):
                self.publisher.create(self.raw)
        before = self.publisher.paths()
        created = {p.name for p in before} - {self.key}
        self.assertEqual(len(created), 1)
        saved = original(created.pop())
        recovered = self.publisher.create(self.raw)
        self.assertEqual(self.publisher.paths(), before)
        self.assertFalse(recovered["invalidated"])
        self.assertEqual(recovered["attempts"], [])
        self.assertEqual(saved["graph_id"], recovered["graph_id"])
        altered = {**self.raw, "plan_fingerprint": "0" * 64}
        with self.assertRaises(ValueError):
            self.publisher.create(altered)

    def test_http_server_uses_publication_adapter_and_plan_schema_is_unchanged(self):
        from test_http_api import running_server

        with running_server(self.root) as server:
            self.assertIsInstance(server.structure_research, PublishedStructureResearchStore)
        self.assertEqual(
            self.publisher.preview(self.raw["request"]), self.store.preview(self.raw["request"])
        )
