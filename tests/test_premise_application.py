"""Application contracts with labelled worker fixtures; actual Lean acceptance is separate."""

from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import Mock, patch

from mra import premise_application as app
from mra.domain import digest
from mra.research_flow import atomic
from mra.tasks import WorkerBusy
from test_http_api import request, running_server
from test_premise_metadata import PremiseMetadataTests

ROOT = Path(__file__).resolve().parents[1]


class PremiseApplicationTests(unittest.TestCase):
    def setUp(self):
        fixture = PremiseMetadataTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.execute()
        self.fixture, self.root = fixture, fixture.root
        row = fixture.study()
        self.store, self.key = fixture.store, fixture.key
        for name in app.FILES:
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, self.root / name)
        row = self.store.add_manual_round(
            self.key,
            {
                **fixture.mutation(row),
                "proposal": {
                    "source_fingerprint": row["source_fingerprint"],
                    "plan_fingerprint": row["plan_fingerprint"],
                    "node_id": "root",
                    "reasoning": "Explicit protocol fixture, not a mathematical translation.",
                    "gaps": ["No real Lean in these contract tests"],
                    "next_actions": [],
                    "formalization": {
                        "backend": "pde",
                        "request": {
                            "nodes": [
                                {
                                    "kind": "problem",
                                    "target": {"original": row["source"]["question"]},
                                }
                            ]
                        },
                    },
                },
            },
        )
        self.row = row
        self.draft = {
            **fixture.mutation(row),
            "round_id": row["rounds"][-1]["round_id"],
            "citation_id": row["citations"][0]["citation_id"],
            "declaration": "Fixture.uniformContinuous_fixture",
        }
        self.target = {
            "formal_statement": "∀ (x : ℝ) (h : x < 1), x ≤ 1",
            "variables": [{"name": "x", "lean": "x", "type": "Real"}],
            "conditions": [{"name": "h", "lean": "h", "formal_statement": "x < 1"}],
        }
        self.backend = Mock()
        self.backend.preview.return_value = {
            "problem_id": "problem",
            "plan_fingerprint": "fixture-full-preview-identity",
            "graph_compilation": {"node_plans": {"problem": self.target}},
        }
        self.patcher = patch("mra.research_studio.backend_store", return_value=self.backend)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.reader = app.ApplicationPreview(self.root)

    def spec(self):
        return app.specification(self.store, self.store.read(self.key), self.draft)

    def report(self, state="NO_OPEN_GOALS"):
        return {
            "schema": app.SCHEMA,
            "declaration": self.draft["declaration"],
            "full_type": self.target["formal_statement"],
            "context": [{"name": "x", "type": "ℝ"}],
            "target": "x ≤ 1",
            "state": state,
            "application": "fixtureApplication",
            "bindings": [{"parameter": "a", "kind": "implicit", "type": "ℝ", "value": "x"}],
            "discharged": ["h"],
            "obligations": [],
            "unresolved_metavariables": False,
            "error": None,
        }

    def started(self):
        with patch.object(app.subprocess, "Popen"):
            return app.start(self.store, self.key, self.draft)

    def execute(self, report=None, execution=None):
        row = self.started()
        spec = row["applications"][-1]["specification"]
        saved = self.reader.status(spec)
        directory = self.reader.directory(saved["identity"])
        report = report or self.report()
        execution = execution or {"run_status": "COMPLETED", "returncode": 0, "pid": 42}

        def worker(work, sandbox, stdout, stderr, budget, **kwargs):
            self.assertEqual(budget, 40)
            self.assertIn("lifecycle_lock_fd", kwargs)
            stdout.write_text("MRA_APPLICATION:" + json.dumps(report) + "\n")
            stderr.write_text("")
            return execution

        with (
            patch.object(app.runtime, "load", return_value=Mock()) as load,
            patch.object(app.runtime._Runner, "_run_worker", side_effect=worker),
            (self.root / "fixture-app-fd").open("wb") as fd,
        ):
            self.reader.execute(directory.name, fd.fileno(), fd.fileno())
        load.return_value.verify.assert_called_once()
        return self.reader.status(spec), spec

    def test_spec_uses_complete_backend_target_and_exact_cached_theorem(self):
        spec = self.spec()
        self.assertEqual(spec["conditions"], self.target["conditions"])
        self.assertEqual(spec["formal_statement"], self.target["formal_statement"])
        self.assertEqual(spec["round_fingerprint"], digest(self.row["rounds"][-1]["proposal"]))
        self.assertEqual(spec["source_fingerprint"], self.row["source_fingerprint"])
        self.assertIn("metadata_identity", spec)
        self.assertEqual(
            self.backend.preview.call_args.args[0]["nodes"],
            self.row["rounds"][-1]["proposal"]["formalization"]["request"]["nodes"],
        )

    def test_foreign_name_withdrawn_reference_and_unread_metadata_rejected(self):
        with self.assertRaisesRegex(ValueError, "exact theorem"):
            app.specification(
                self.store, self.store.read(self.key), {**self.draft, "declaration": "Other.name"}
            )
        with patch.object(app.PremiseMetadata, "status", return_value={"state": "NOT_READ"}):
            with self.assertRaisesRegex(ValueError, "Read the Lean"):
                self.spec()
        row = self.store.read(self.key)
        row["citations"][0]["withdrawn"] = True
        with self.assertRaisesRegex(ValueError, "active theorem"):
            app.specification(self.store, row, self.draft)

    def test_non_theorem_and_unsupported_backend_do_not_launch(self):
        metadata = self.fixture.reader.status(self.fixture.item)
        metadata["report"]["matches"][0]["kind"] = "definition"
        with patch.object(app.PremiseMetadata, "status", return_value=metadata):
            with self.assertRaisesRegex(ValueError, "exact theorem"):
                self.spec()
        row = self.store.read(self.key)
        row["rounds"][-1]["proposal"]["formalization"]["backend"] = "typed"
        with self.assertRaisesRegex(ValueError, "currently supports"):
            app.specification(self.store, row, self.draft)

    def test_declaration_is_data_and_invalid_import_cannot_enter_template(self):
        spec = self.spec()
        source = app.render(self.root, spec)
        self.assertEqual(source, app.render(self.root, {**spec, "declaration": "evil\nrun_cmd"}))
        with self.assertRaisesRegex(ValueError, "module"):
            app.render(
                self.root, {**spec, "item": {**spec["item"], "module": "Mathlib.X\nrun_cmd evil"}}
            )

    def test_parser_rejects_duplicate_foreign_identity_and_hidden_obligations(self):
        text = "MRA_APPLICATION:" + json.dumps(self.report())
        with self.assertRaises(ValueError):
            app.parse_report(text + "\n" + text, self.spec())
        for change in (
            {"declaration": "Foreign.name"},
            {"unresolved_metavariables": True},
            {"obligations": [{"type": "x < 1", "context": "x : Real"}]},
        ):
            with self.assertRaises(ValueError):
                app.parse_report(
                    "MRA_APPLICATION:" + json.dumps({**self.report(), **change}), self.spec()
                )

    def test_complete_fixture_execution_has_artifacts_but_no_proof_evidence(self):
        row, spec = self.execute()
        self.assertEqual(
            (row["state"], row["math_verdict"], row["evidence_level"]),
            ("NO_OPEN_GOALS", "UNKNOWN", "E0"),
        )
        self.assertEqual(len(row["artifact_hashes"]), 4)
        with patch.object(app.subprocess, "Popen") as spawn:
            self.reader.start(spec, "explicit-other-request")
            spawn.assert_not_called()

    def test_open_conditions_and_mismatch_are_distinct_from_false(self):
        report = self.report("OPEN_OBLIGATIONS")
        report.update(
            unresolved_metavariables=True,
            obligations=[{"type": "x < 1", "context": "x : Real\n⊢ x < 1"}],
        )
        row, _ = self.execute(report)
        self.assertEqual(row["report"]["obligations"], report["obligations"])
        mismatch = self.report("NO_MATCH")
        mismatch.update(application=None, error="cannot unify")
        self.assertEqual(
            app.parse_report("MRA_APPLICATION:" + json.dumps(mismatch), self.spec())["state"],
            "NO_MATCH",
        )
        self.assertEqual(row["math_verdict"], "UNKNOWN")

    def test_timeout_no_automatic_retry_and_same_request_idempotency(self):
        row, spec = self.execute(execution={"run_status": "TIMEOUT", "returncode": None})
        with patch.object(app.subprocess, "Popen") as spawn:
            self.reader.status(spec)
            self.reader.start(spec, self.draft["request_id"])
            spawn.assert_not_called()
        self.assertEqual(row["state"], "TIMEOUT")

    def test_busy_checker_preserves_visible_request_and_explicit_replay(self):
        @contextmanager
        def busy(_):
            raise WorkerBusy("fixture checker busy")
            yield

        with (
            patch.object(app, "checker_slot", busy),
            patch.object(app.subprocess, "Popen") as spawn,
        ):
            with self.assertRaises(WorkerBusy):
                app.start(self.store, self.key, self.draft)
            spawn.assert_not_called()
        row = self.store.report(self.key)
        self.assertEqual(row["application_states"][0]["state"], "NOT_STARTED")
        replay = self.started()
        self.assertEqual(len(replay["applications"]), 1)
        self.assertEqual(replay["revision"], row["revision"])

    def test_explicit_retry_retains_interrupted_history_and_limit(self):
        row = self.started()
        spec = row["applications"][0]["specification"]
        first = self.reader.status(spec)
        self.assertEqual(first["state"], "INTERRUPTED")
        with patch.object(app.subprocess, "Popen"):
            second = self.reader.start(spec, "explicit-second")
            self.reader.start(spec, "explicit-third")
            with self.assertRaisesRegex(ValueError, "Three explicit"):
                self.reader.start(spec, "explicit-fourth")
        self.assertNotEqual(first["attempt_id"], second["attempt_id"])
        self.assertTrue(
            (
                self.reader.attempt(self.reader.directory(first["identity"]), first) / "record.json"
            ).exists()
        )

    def test_changed_source_or_candidate_is_excluded_from_model_context(self):
        _, spec = self.execute()
        row = self.store.read(self.key)
        row["rounds"][-1]["proposal"]["reasoning"] += " changed"
        result = app.states(self.store, row)
        self.assertEqual(result[0]["state"], "UNAVAILABLE")
        self.assertEqual(app.context(self.store, row, "root")["diagnostics"], [])
        self.assertEqual(len(app.context(self.store, row, "root")["unavailable"]), 1)
        with patch.object(app.PremiseIndex, "lookup", side_effect=ValueError("source drift")):
            with self.assertRaisesRegex(ValueError, "source drift"):
                self.reader.start(spec, "after-source-drift")

    def test_corrupt_artifact_does_not_hide_original_notebook(self):
        saved, spec = self.execute()
        attempt = self.reader.attempt(self.reader.directory(saved["identity"]), saved)
        (attempt / "stdout.log").write_text("corrupt")
        with self.assertRaisesRegex(ValueError, "artifact changed"):
            self.reader.status(spec)
        row = self.store.report(self.key)
        self.assertEqual(row["application_states"][0]["state"], "UNAVAILABLE")
        self.assertEqual(row["source"], self.row["source"])

    def test_bound_reference_tampering_and_request_reuse_rejected(self):
        row = self.started()
        row["applications"][0]["specification"]["declaration"] = "Foreign"
        atomic(
            self.store.directory(self.key) / "record.json",
            {k: v for k, v in row.items() if k not in ("application_states",)},
        )
        with self.assertRaisesRegex(ValueError, "reference identity"):
            self.store.read(self.key)

    def test_http_preview_uses_real_route_but_simulated_worker(self):
        with running_server(self.root) as server, patch.object(app.subprocess, "Popen"):
            code, row, _ = request(
                server, "POST", "/v1/studio/" + self.key + "/application-preview", self.draft
            )
            self.assertEqual(code, 200)
            self.assertEqual(len(row["application_states"]), 1)
            self.assertEqual(row["math_verdict"], "UNKNOWN")
            self.assertEqual(row["model_calls"], 0)
        with self.assertRaisesRegex(ValueError, "different content"):
            app.start(self.store, self.key, {**self.draft, "declaration": "Foreign"})


if __name__ == "__main__":
    unittest.main()
