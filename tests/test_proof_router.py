"""Version routing, request recovery and preservation of existing published plans."""

from copy import deepcopy
import hashlib
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from mra.proof_lab import TRUST_FILES as OLD_FILES
from mra.proof_research import SOURCES as OLD_SOURCES
from mra.proof_router import ProofResearchRouter, V1, V2
import test_proof_logic_research as helpers
import test_proof_research as old_helpers


class VersionRouterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=helpers.ROOT / ".tmp")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        helpers.copy_sources(self.root)
        for name in (*OLD_FILES, *OLD_SOURCES, *helpers.TRUST_FILES, *helpers.SOURCES):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(helpers.ROOT / name, target)
        self.project = helpers.parent(self.root)
        self.store = ProofResearchRouter(self.root)

    def payload(self, request, request_id=None):
        plan = self.store.preview(request)
        return {
            "request_id": request_id or helpers.rid(),
            "request": request,
            "plan_fingerprint": plan["plan_fingerprint"],
        }

    def test_legacy_and_new_plans_use_exactly_their_original_provider(self):
        for raw, prefix, schema in (
            (old_helpers.raw(self.project), "proofflow-", "proof-project-research-v1"),
            (helpers.raw(self.project), "proof2flow-", "proof-project-research-v2"),
        ):
            with self.subTest(schema=schema):
                value = self.store.create(self.payload(raw))
                self.assertTrue(value["graph_id"].startswith(prefix))
                self.assertEqual(value["plan"]["schema"], schema)
                self.assertEqual(value["math_verdict"], "UNKNOWN")
        catalog = self.store.catalog(self.project["project_id"], "main")
        self.assertEqual({g["language_version"] for g in catalog["graphs"]}, {V1, V2})

    def test_unknown_and_mixed_versions_never_fallback(self):
        original = helpers.raw(self.project)
        for version in ("proof-language-v999", [V1], V1):
            raw = deepcopy(original)
            raw["nodes"][0]["target"]["schema"] = version
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.store.preview(raw)
        self.assertEqual(self.store.stores[V1].paths(), [])
        self.assertEqual(self.store.stores[V2].paths(), [])

    def test_repeated_request_cannot_create_another_version(self):
        request_id = helpers.rid()
        payload = self.payload(old_helpers.raw(self.project), request_id)
        first = self.store.create(payload)
        record = self.store.directory(first["graph_id"]) / "record.json"
        before = hashlib.sha256(record.read_bytes()).hexdigest()
        self.assertEqual(self.store.create(payload)["graph_id"], first["graph_id"])
        with self.assertRaises(ValueError):
            self.store.create(self.payload(helpers.raw(self.project), request_id))
        self.assertEqual(hashlib.sha256(record.read_bytes()).hexdigest(), before)
        self.assertEqual(self.store.stores[V2].paths(), [])

    def test_failed_publication_can_resume_same_request_without_duplicate_graph(self):
        payload = self.payload(helpers.raw(self.project))
        with patch.object(
            self.store.stores[V2], "create", side_effect=OSError("publication failed")
        ):
            with self.assertRaises(OSError):
                self.store.create(payload)
        first = self.store.create(payload)
        self.assertEqual(self.store.create(payload)["graph_id"], first["graph_id"])
        self.assertEqual(len(self.store.stores[V2].paths()), 1)

    def test_invalid_graph_identifiers_do_not_probe_another_store(self):
        for key in ("../proofflow-" + "a" * 32, "proof2flow-../bad", None):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.store.report(key)

    def test_router_storage_cannot_be_redirected(self):
        base = self.root / "artifacts/proof-router"
        moved = base.with_name("proof-router-moved")
        base.rename(moved)
        base.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.store.create(self.payload(helpers.raw(self.project)))

    def test_worker_turns_alternate_between_versions_without_parallel_execution(self):
        calls = []

        def result(version):
            calls.append(version)
            return {"worker_state": "EXECUTED"}

        with (
            patch.object(self.store.stores[V1], "work_once", side_effect=lambda: result(V1)),
            patch.object(self.store.stores[V2], "work_once", side_effect=lambda: result(V2)),
        ):
            self.store.work_once()
            self.store.work_once()
        self.assertEqual(calls, [V1, V2])


if __name__ == "__main__":
    unittest.main()
