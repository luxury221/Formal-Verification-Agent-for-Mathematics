"""Graph contracts use no fabricated proof results; positive bindings use actual Lean."""

from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid

from mra.domain import canonical
from mra.paper_review import PaperReviewStore
from mra.research import ResearchStore, validate_nodes
from test_http_api import copy_web_inputs, request, running_server
from test_paper_review import submission

ROOT = Path(__file__).resolve().parents[1]


def rid():
    return "research-test-" + uuid.uuid4().hex


def formula(amount=1):
    return {
        "op": "forall",
        "name": "n",
        "body": {
            "op": "lt",
            "left": {
                "op": "sub",
                "left": {"op": "var", "name": "n"},
                "right": {"op": "num", "value": amount},
            },
            "right": {"op": "var", "name": "n"},
        },
    }


def node(key="l1", dependencies=(), expression=None):
    return {
        "id": key,
        "kind": "lemma",
        "title": "Repository research test " + key,
        "statement": "对任意整数n，n减1小于n。",
        "domain": "Int",
        "formula": expression or formula(),
        "depends_on": list(dependencies),
        "context": [],
        "notes": "Authored integration case, not model output",
    }


def graph():
    return [node(), node("l2", ["l1"]), {**node("main", ["l2"], formula(3)), "kind": "problem"}]


class ResearchFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_web_inputs(self.root)
        self.store = ResearchStore(self.root)
        self.project = self.store.create(
            {
                "request_id": rid(),
                "title": "Repository authored research",
                "question": "How should a long problem be decomposed?",
            }
        )
        self.pid = self.project["project_id"]

    def edit(self, nodes, project=None):
        project = project or self.project
        self.project = self.store.edit(
            self.pid,
            {
                "request_id": rid(),
                "expected_version": project["version"],
                "branch_id": "main",
                "nodes": nodes,
            },
        )
        return self.project


class ResearchProtocolTests(ResearchFixture):
    def test_offline_save_restart_and_history_preserve_unknown_without_creating_checker_runs(self):
        self.edit(graph())
        restored = ResearchStore(self.root).report(self.pid)
        self.assertEqual(restored, self.project)
        self.assertEqual(
            restored["branches"]["main"]["node_states"]["main"]["math_verdict"], "UNKNOWN"
        )
        self.assertEqual(restored["branches"]["main"]["node_states"]["l2"]["blocked_by"], ["l1"])
        self.assertEqual(len(self.store.history(self.pid, "main")["revisions"]), 2)
        self.assertFalse((self.root / "artifacts/runs").exists())
        self.assertEqual((restored["model_calls"], restored["lean_calls"]), (0, 0))

    def test_mutations_are_idempotent_and_conflicting_request_content_is_rejected(self):
        data = {"request_id": rid(), "expected_version": 1, "branch_id": "main", "nodes": graph()}
        first = self.store.edit(self.pid, data)
        self.assertEqual(first, self.store.edit(self.pid, data))
        with self.assertRaises(ValueError):
            self.store.edit(self.pid, {**data, "nodes": []})

    def test_parallel_edits_have_one_winner_and_never_overwrite_a_stale_graph(self):
        def update(index):
            try:
                return self.store.edit(
                    self.pid,
                    {
                        "request_id": rid(),
                        "expected_version": 1,
                        "branch_id": "main",
                        "nodes": [node("n" + str(index))],
                    },
                )
            except ValueError:
                return None

        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(update, range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(len(self.store.history(self.pid, "main")["revisions"]), 2)

    def test_cycles_missing_dependencies_duplicate_ids_and_untyped_source_are_rejected(self):
        variants = [
            [node("a", ["a"])],
            [node("a", ["b"]), node("b", ["a"])],
            [node("a", ["missing"])],
            [node(), node()],
            [{**node(), "lean": "by sorry"}],
            [{**node(), "domain": "Real"}],
            [{**node(), "formula": {"op": "lean", "source": "by sorry"}}],
        ]
        for nodes in variants:
            with self.subTest(nodes=nodes), self.assertRaises((ValueError, TypeError)):
                self.edit(nodes)
        self.assertEqual(self.store.report(self.pid)["version"], 1)

    def test_definition_context_is_distinct_from_checked_premises(self):
        definition = {**node("d1"), "kind": "definition", "domain": None, "formula": None}
        nodes = [definition, {**node(), "context": ["d1"]}]
        self.edit(nodes)
        self.assertEqual(
            self.project["branches"]["main"]["node_states"]["d1"]["status"], "DOCUMENTED"
        )
        with self.assertRaises(ValueError):
            self.edit([definition, node(dependencies=["d1"])])
        with self.assertRaises(ValueError):
            self.store.export_node(self.pid, "main", "d1")

    def test_node_and_revision_budgets_are_enforced_before_save(self):
        with self.assertRaises(ValueError):
            validate_nodes([node("n" + str(index)) for index in range(25)])
        with self.assertRaises(ValueError):
            validate_nodes([{**node(), "notes": "x" * 4001}])
        with self.assertRaises(ValueError):
            self.edit([{**node(), "depends_on": ["a"] * 13}])
        for _ in range(7):
            self.project = self.store.fork(
                self.pid,
                {
                    "request_id": rid(),
                    "expected_version": self.project["version"],
                    "branch_id": "main",
                    "label": "Explicit trial",
                },
            )
        with self.assertRaises(ValueError):
            self.store.fork(
                self.pid,
                {
                    "request_id": rid(),
                    "expected_version": self.project["version"],
                    "branch_id": "main",
                    "label": "Over budget",
                },
            )

    def test_revision_limit_and_historical_hashes_are_checked(self):
        for _ in range(63):
            self.edit([])
        with self.assertRaisesRegex(ValueError, "revision budget"):
            self.edit([])
        with sqlite3.connect(self.store.path) as db:
            db.execute(
                "UPDATE revisions SET body='{}' WHERE project_id=? AND revision=1", (self.pid,)
            )
        with self.assertRaisesRegex(ValueError, "revision changed"):
            self.store.history(self.pid, "main")

    def test_fork_and_old_graph_snapshots_stay_independent(self):
        self.edit(graph())
        old = self.store.history(self.pid, "main")["revisions"][0]
        forked = self.store.fork(
            self.pid,
            {
                "request_id": rid(),
                "expected_version": self.project["version"],
                "branch_id": "main",
                "label": "Alternative approach",
            },
        )
        key = forked["events"][-1]["branch_id"]
        self.assertEqual(forked["branches"][key]["nodes"], graph())
        self.assertEqual(forked["branches"][key]["bindings"], {})
        self.project = forked
        self.edit([])
        self.assertEqual(self.store.report(self.pid)["branches"][key]["nodes"], graph())
        self.assertEqual(self.store.history(self.pid, "main")["revisions"][1], old)

    def test_unexecuted_source_and_unverified_prerequisites_cannot_be_bound(self):
        self.edit(graph())
        bundle = json.loads((ROOT / "evals/integer-review-v1/valid-proof.json").read_text())
        pending = self.store.papers.submit(submission(bundle))
        data = {
            "expected_version": self.project["version"],
            "branch_id": "main",
            "node_id": "l1",
            "review_id": pending["review_id"],
            "key": "claim",
        }
        with self.assertRaises(ValueError):
            self.store.prepare_binding(self.pid, data)
        with self.assertRaisesRegex(ValueError, "prerequisites"):
            self.store.prepare_binding(self.pid, {**data, "node_id": "l2"})
        with self.assertRaises(ValueError):
            self.store.bind(
                self.pid,
                {
                    **data,
                    "request_id": rid(),
                    "binding_fingerprint": "forged",
                    "reviewer": "test",
                    "confirmed": True,
                    "math_verdict": "PROVED",
                },
            )

    def test_export_has_exact_full_formula_and_does_not_insert_dependency_assumptions(self):
        self.edit(graph())
        data = self.store.export_node(self.pid, "main", "main")
        self.assertEqual(data["bundle"]["conclusion"], formula(3))
        self.assertEqual(data["bundle"]["assumptions"], [])
        self.assertEqual(data["bundle"]["variables"], [])
        self.assertEqual(data["bundle"]["steps"], [])
        self.assertEqual(data["lean_calls"], 0)

    def test_storage_redirection_and_modified_ledger_are_rejected(self):
        with sqlite3.connect(self.store.path) as db:
            db.execute("UPDATE projects SET body=? WHERE id=?", ("{}", self.pid))
        with self.assertRaises(ValueError):
            self.store.report(self.pid)
        self.store.path.rename(self.root / "moved.sqlite3")
        self.store.path.symlink_to(self.root / "moved.sqlite3")
        with self.assertRaises(ValueError):
            ResearchStore(self.root)

    def test_cli_uses_the_same_persistent_store_and_cleanly_rejects_stale_input(self):
        data = self.root / "edit.json"
        data.write_bytes(
            canonical(
                {"request_id": rid(), "expected_version": 1, "branch_id": "main", "nodes": graph()}
            )
        )
        code = "import sys;from pathlib import Path;import mra.__main__ as m;m.ROOT=Path(sys.argv[1]);sys.argv=['mra',*sys.argv[2:]];raise SystemExit(m.main())"
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                str(self.root),
                "research-edit",
                self.pid,
                "--input",
                str(data),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["version"], 2)
        old = json.loads(data.read_bytes())
        old["request_id"] = rid()
        data.write_bytes(canonical(old))
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                str(self.root),
                "research-edit",
                self.pid,
                "--input",
                str(data),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)

    def test_http_project_graph_branch_export_and_guards_need_no_model(self):
        with running_server(self.root) as server:
            self.assertEqual(request(server, "GET", "/research")[0], 200)
            for asset in ("/research.js", "/research.css"):
                self.assertEqual(request(server, "GET", asset)[0], 200)
            self.assertEqual(
                request(server, "GET", "/v1/research")[1]["projects"][0]["project_id"], self.pid
            )
            path = "/v1/research/" + self.pid
            data = {
                "request_id": rid(),
                "expected_version": 1,
                "branch_id": "main",
                "nodes": graph(),
            }
            self.assertEqual(
                request(server, "POST", path + "/edit", data, headers={"X-MRA-CSRF": "wrong"})[0],
                403,
            )
            status, edited, _ = request(server, "POST", path + "/edit", data)
            self.assertEqual((status, edited["version"]), (200, 2))
            self.assertEqual(request(server, "GET", path + "/history/main")[0], 200)
            self.assertEqual(
                request(server, "GET", path + "/export/main/l1")[1]["bundle"]["conclusion"],
                formula(),
            )
            self.assertEqual(
                request(server, "GET", path, headers={"Origin": "https://outside.example"})[0], 403
            )
            self.assertEqual(request(server, "GET", "/v1/research/%2e%2e")[0], 400)
            self.assertEqual(request(server, "POST", path + "/run", {})[0], 404)
            self.assertFalse((self.root / "artifacts/runs").exists())


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "Enable actual Lean/replay source checks"
)
class ResearchRealLeanTests(ResearchFixture):
    # The real group below uses actual root proof sources and a separate temporary research ledger.
    @classmethod
    def setUpClass(cls):
        cls.real_papers = PaperReviewStore(ROOT)
        cls.sources = {}
        for name in ("valid-proof", "missing-condition"):
            bundle = json.loads((ROOT / "evals/integer-review-v1" / (name + ".json")).read_text())
            queued = cls.real_papers.submit(submission(bundle))
            report = cls.real_papers.work_once(queued["review_id"])["review"]
            if not report["results_available"]:
                raise AssertionError(report)
            cls.sources[name] = report

    def setUp(self):
        super().setUp()
        self.store.papers = self.real_papers

    def binding(self, key="l1", source=None, obligation="claim"):
        source = source or self.sources["valid-proof"]
        current = self.store.report(self.pid)
        data = {
            "expected_version": current["version"],
            "branch_id": "main",
            "node_id": key,
            "review_id": source["review_id"],
            "key": obligation,
        }
        plan = self.store.prepare_binding(self.pid, data)
        request_data = {
            **data,
            "request_id": rid(),
            "binding_fingerprint": plan["binding_fingerprint"],
            "reviewer": "repository-research-test-author",
            "confirmed": True,
        }
        result = self.store.bind(self.pid, request_data)
        self.project = result
        return result, request_data

    def test_real_binding_prerequisites_do_not_solve_an_unchecked_main_problem(self):
        self.edit(graph())
        self.binding()
        report, _ = self.binding("l2")
        states = report["branches"]["main"]["node_states"]
        self.assertEqual(states["l1"]["math_verdict"], "PROVED")
        self.assertEqual(states["l2"]["math_verdict"], "PROVED")
        self.assertEqual(
            (states["main"]["status"], states["main"]["math_verdict"]), ("READY", "UNKNOWN")
        )
        self.assertFalse(report["fresh_attestation"])

    def test_real_same_ast_other_domain_and_changed_full_formula_are_rejected(self):
        for changed in ({**node(), "domain": "Nat"}, {**node(), "formula": formula(2)}):
            self.edit([changed])
            with self.assertRaisesRegex(ValueError, "exact domain"):
                self.binding()

    def test_real_checked_negative_refutation_blocks_a_dependent(self):
        source = self.sources["missing-condition"]
        expression = source["plan"]["obligations"][0]["target"]["formula"]
        self.edit([node(expression=expression), node("later", ["l1"])])
        report, _ = self.binding(source=source)
        self.assertEqual(report["branches"]["main"]["node_states"]["l1"]["math_verdict"], "REFUTED")
        self.assertEqual(
            report["branches"]["main"]["node_states"]["later"]["math_verdict"], "UNKNOWN"
        )
        with self.assertRaisesRegex(ValueError, "prerequisites"):
            self.binding("later")

    def test_real_withdrawal_cascades_and_rebinding_a_parent_does_not_revive_its_child(self):
        self.edit(graph())
        self.binding()
        report, prior = self.binding("l2")
        self.store.withdraw(
            self.pid,
            {
                "request_id": rid(),
                "expected_version": report["version"],
                "branch_id": "main",
                "node_id": "l1",
                "reviewer": "test-author",
                "reason": "Test withdrawal",
                "confirmed": True,
            },
        )
        report, _ = self.binding()
        self.assertEqual(report["branches"]["main"]["bindings"]["l2"]["state"], "INVALIDATED")
        replayed = self.store.bind(self.pid, prior)
        self.assertEqual(
            replayed["branches"]["main"]["node_states"]["l2"]["math_verdict"], "UNKNOWN"
        )

    def test_real_context_change_and_fork_require_new_explicit_binding(self):
        definition = {**node("d1"), "kind": "definition", "domain": None, "formula": None}
        nodes = [definition, {**node(), "context": ["d1"]}, node("l2", ["l1"])]
        self.edit(nodes)
        self.binding()
        self.binding("l2")
        changed = copy.deepcopy(nodes)
        changed[0]["statement"] = "An explicitly changed definition note"
        report = self.edit(changed)
        self.assertTrue(
            all(
                b["state"] == "INVALIDATED" for b in report["branches"]["main"]["bindings"].values()
            )
        )
        report = self.store.fork(
            self.pid,
            {
                "request_id": rid(),
                "expected_version": report["version"],
                "branch_id": "main",
                "label": "Independent branch",
            },
        )
        key = report["events"][-1]["branch_id"]
        self.assertEqual(report["branches"][key]["bindings"], {})

    def test_real_source_file_change_is_monotonic_after_bytes_are_restored(self):
        self.edit(graph())
        self.binding()
        self.binding("l2")
        check = self.sources["valid-proof"]["checks"][0]
        path = Path(check["result"]["artifact_directory"]) / "stdout.log"
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"\nchanged research source")
            invalidated = self.store.report(self.pid)
        finally:
            path.write_bytes(original)
        self.assertTrue(
            all(
                b["state"] == "INVALIDATED"
                for b in invalidated["branches"]["main"]["bindings"].values()
            )
        )
        self.assertEqual(
            self.store.report(self.pid)["branches"]["main"]["node_states"]["l1"]["math_verdict"],
            "UNKNOWN",
        )

    def test_real_revoked_review_disables_bound_research_evidence(self):
        bundle = json.loads((ROOT / "evals/integer-review-v1/missing-condition.json").read_text())
        queued = self.real_papers.submit(submission(bundle))
        source = self.real_papers.work_once(queued["review_id"])["review"]
        self.edit([node(expression=source["plan"]["obligations"][0]["target"]["formula"])])
        self.binding(source=source)
        self.real_papers.cancel(source["review_id"], revoke=True)
        report = self.store.report(self.pid)
        self.assertEqual(report["branches"]["main"]["bindings"]["l1"]["state"], "INVALIDATED")
        self.assertEqual(report["branches"]["main"]["node_states"]["l1"]["math_verdict"], "UNKNOWN")
