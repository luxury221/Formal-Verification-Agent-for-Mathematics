"""Product routes and read-only evaluation transport; no simulated proof verdicts."""

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from mra.domain import canonical
from test_http_api import copy_web_inputs, request, running_server

ROOT = Path(__file__).resolve().parents[1]


class WorkspaceUiHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_web_inputs(self.root)
        for name in ("model_evaluation.py", "evaluation_targets.py"):
            shutil.copyfile(ROOT / "mra" / name, self.root / "mra" / name)
        self.context = running_server(self.root)
        self.server = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def metadata(self, suffix="a", **values):
        directory = self.root / "artifacts/model-evaluations" / ("nat-plans-" + suffix * 32)
        directory.mkdir(parents=True)
        data = {
            "state": "CANCELLED",
            "recorded_at": "2026-10-25T08:00:00+08:00",
            "simulation": True,
            "total": 6,
            "processed": 1,
            **values,
        }
        (directory / "report.json").write_bytes(canonical(data))
        return directory

    def test_complete_product_routes_assets_and_verification_entry_use_same_guards(self):
        for route in (
            "/",
            "/ideas",
            "/research",
            "/verify",
            "/papers",
            "/library",
            "/evaluations",
            "/activity",
            "/workflow",
            "/settings",
            "/shell.js",
            "/workspace.js",
            "/workspace.css",
            "/research.js",
            "/research.css",
            "/favicon.svg",
        ):
            with self.subTest(route=route):
                status, body, headers = request(self.server, "GET", route)
                self.assertEqual(status, 200)
                self.assertTrue(body)
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertIn(b"workspace.js", request(self.server, "GET", "/")[1])
        self.assertIn(b'id="submit-form"', request(self.server, "GET", "/verify")[1])
        self.assertFalse((self.root / "artifacts/runs").exists())

    def test_evaluation_plan_is_read_only_hides_answers_and_needs_no_model_or_lean(self):
        status, data, _ = request(self.server, "GET", "/v1/evaluations")
        self.assertEqual(status, 200)
        self.assertEqual(
            data["split_counts"], {"train": 0, "development": 6, "regression": 6, "reserved": 6}
        )
        self.assertEqual(len(data["plan"]["targets"]), 6)
        self.assertEqual(data["plan"]["model_calls"], 0)
        self.assertIsNone(data["plan"]["alignment_accuracy"])
        raw = json.dumps(data)
        self.assertNotIn('"proof"', raw)
        self.assertNotIn('"expected_verdict"', raw)
        self.assertNotIn('"witnesses"', raw)
        self.assertFalse((self.root / "artifacts/runs").exists())
        self.assertFalse((self.root / "artifacts/model-evaluations").exists())

    def test_evaluation_list_never_promotes_stored_scores_to_mathematical_results(self):
        directory = self.metadata(model_solve_rate=1, math_verdict="PROVED", configured_model=None)
        status, data, _ = request(self.server, "GET", "/v1/evaluations")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["runs"]), 1)
        self.assertNotIn("model_solve_rate", data["runs"][0])
        self.assertNotIn("math_verdict", data["runs"][0])
        status, detail, _ = request(self.server, "GET", "/v1/evaluations/" + directory.name)
        self.assertEqual(status, 200)
        self.assertFalse(detail["integrity_ok"])
        self.assertIsNone(detail["model_solve_rate"])

    def test_malformed_or_oversized_evaluation_records_do_not_break_catalog(self):
        self.metadata("a", configured_model=["malformed"])
        self.metadata("b", processed=20)
        self.metadata("c", simulation="false")
        self.metadata("d", total=True)
        directory = self.metadata("e")
        (directory / "report.json").write_bytes(b"{" * 1048577)
        status, data, _ = request(self.server, "GET", "/v1/evaluations")
        self.assertEqual(status, 200)
        self.assertEqual(data["runs"], [])

    def test_evaluation_paths_and_symlinks_cannot_read_arbitrary_files(self):
        directory = self.metadata()
        target = self.root / "outside"
        target.mkdir()
        (target / "report.json").write_bytes((directory / "report.json").read_bytes())
        shutil.rmtree(directory)
        directory.symlink_to(target, target_is_directory=True)
        self.assertEqual(request(self.server, "GET", "/v1/evaluations/" + directory.name)[0], 404)
        for path in (
            "/v1/evaluations/../settings",
            "/v1/evaluations/%2e%2e",
            "/v1/evaluations/nat-plans-unknown",
        ):
            self.assertEqual(request(self.server, "GET", path)[0], 404)
        status, data, _ = request(self.server, "GET", "/v1/evaluations")
        self.assertEqual(status, 200)
        self.assertEqual(data["runs"], [])

    def test_new_endpoints_retain_origin_guards_and_have_no_http_execution_or_config_write(self):
        for route in ("/v1/evaluations", "/v1/evaluations/nat-plans-" + "a" * 32, "/settings"):
            self.assertEqual(
                request(self.server, "GET", route, headers={"Origin": "https://outside.example"})[
                    0
                ],
                403,
            )
        for route in ("/v1/evaluations", "/v1/evaluations/run", "/v1/settings", "/v1/model-config"):
            self.assertEqual(request(self.server, "POST", route, {})[0], 404)
        self.assertFalse((self.root / "config/model.local.json").exists())
        self.assertEqual(self.server.store.list_tasks(), [])


if __name__ == "__main__":
    unittest.main()
