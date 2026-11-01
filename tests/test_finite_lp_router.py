"""Four explicit providers, cross-version identity and fixed worker scheduling."""

from contextlib import ExitStack
from copy import deepcopy
import hashlib
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from mra.finite_lp_lab import TRUST_FILES
from mra.proof_lab import TRUST_FILES as V1_TRUST
from mra.proof_logic_lab import TRUST_FILES as V2_TRUST
from mra.optimization_lab import TRUST_FILES as V3_TRUST
from mra.finite_lp_research import SOURCES
from mra.proof_router import ProofResearchRouter, V1, V2, V3, V4, catalog
from mra.proof_examples import examples as v1_examples
from mra.proof_logic_examples import examples as v2_examples
from mra.optimization_examples import examples as v3_examples
from mra.finite_lp_examples import examples as v4_examples
from mra.proof_research import SOURCES as V1_SOURCES
from mra.proof_logic_research import SOURCES as V2_SOURCES
from mra.optimization_research import SOURCES as V3_SOURCES
import test_finite_lp_research as helpers


class FiniteLpRouterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=helpers.ROOT / ".tmp")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        helpers.copy_sources(self.root)
        for name in dict.fromkeys(
            (
                *TRUST_FILES,
                *V1_TRUST,
                *V2_TRUST,
                *V3_TRUST,
                *SOURCES,
                *V1_SOURCES,
                *V2_SOURCES,
                *V3_SOURCES,
            )
        ):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(helpers.ROOT / name, target)
        self.project = helpers.parent(self.root)
        self.store = ProofResearchRouter(self.root)

    def raw(self, version):
        example = {V1: v1_examples, V2: v2_examples, V3: v3_examples, V4: v4_examples}[version]()[0]
        return {
            "project_id": self.project["project_id"],
            "branch_id": "main",
            "nodes": deepcopy(example["nodes"]),
        }

    def payload(self, version, request_id=None):
        raw = self.raw(version)
        plan = self.store.preview(raw)
        return {
            "request_id": request_id or helpers.rid(),
            "request": raw,
            "plan_fingerprint": plan["plan_fingerprint"],
        }

    def test_four_explicit_versions_keep_their_own_schema_and_ledger(self):
        for version, prefix, schema in (
            (V1, "proofflow-", "proof-project-research-v1"),
            (V2, "proof2flow-", "proof-project-research-v2"),
            (V3, "proof3flow-", "proof-project-research-v3"),
            (V4, "proof4flow-", "proof-project-research-v4"),
        ):
            with self.subTest(version=version):
                raw = self.payload(version)
                report = self.store.create(raw)
                self.assertTrue(report["graph_id"].startswith(prefix))
                self.assertEqual(report["plan"]["schema"], schema)
                self.assertEqual(report["plan"]["request"], raw["request"])
                self.assertEqual(report["math_verdict"], "UNKNOWN")
                self.assertEqual(self.store.create(raw)["graph_id"], report["graph_id"])
        graphs = self.store.catalog(self.project["project_id"], "main")["graphs"]
        self.assertEqual({g["language_version"] for g in graphs}, {V1, V2, V3, V4})

    def test_v3_request_cannot_be_reused_as_v4_or_rewrite_old_record(self):
        request_id = helpers.rid()
        report = self.store.create(self.payload(V3, request_id))
        path = self.store.directory(report["graph_id"]) / "record.json"
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaises(ValueError):
            self.store.create(self.payload(V4, request_id))
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        self.assertEqual(self.store.stores[V4].paths(), [])

    def test_v4_mixed_with_old_targets_never_falls_back(self):
        raw = self.raw(V4)
        old = deepcopy(self.raw(V3)["nodes"][0])
        old["id"] = "old"
        raw["nodes"].append(old)
        with self.assertRaises(ValueError):
            self.store.preview(raw)
        self.assertEqual(self.store.stores[V4].paths(), [])

    def test_four_queues_rotate_in_fixed_order(self):
        calls = []
        with ExitStack() as stack:
            for version, provider in self.store.stores.items():

                def work(version=version):
                    calls.append(version)
                    return {"worker_state": "EXECUTED"}

                stack.enter_context(patch.object(provider, "work_once", side_effect=work))
            for _ in range(8):
                self.store.work_once()
        self.assertEqual(calls, [V1, V2, V3, V4] * 2)
        value = catalog()
        self.assertEqual(value["supported_languages"], [V1, V2, V3, V4])
        self.assertEqual(value["default_language"], V4)
        self.assertEqual(len(value["examples"]), 11)

    def test_v4_catalog_only_advertises_v4_and_rejects_v3_ledger_identity(self):
        report = self.store.create(self.payload(V4))
        provider = self.store.stores[V4]
        value = provider.catalog(self.project["project_id"], "main")
        self.assertEqual(
            {node["target"]["schema"] for ex in value["examples"] for node in ex["nodes"]},
            {V4},
        )
        dbpath = self.store.checker(report["graph_id"]).path
        with sqlite3.connect(dbpath) as db:
            db.execute("ALTER TABLE proof_finite_lp_identity RENAME TO proof_optimization_identity")
        with self.assertRaises(sqlite3.OperationalError):
            self.store.report(report["graph_id"])
        with sqlite3.connect(dbpath) as db:
            self.assertIsNone(
                db.execute(
                    "SELECT name FROM sqlite_master WHERE name='proof_finite_lp_identity'"
                ).fetchone()
            )


if __name__ == "__main__":
    unittest.main()
