"""Local tool contracts with real stores and inert runtime metadata; no proof scores."""

from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mra import core_tools
from mra.core_tools import CoreToolAPI, SCHEMA
from mra.premise_search import PremiseIndex
from mra.research_studio import StudioStore
from mra.typed_research import SOURCES, TypedResearchStore, example
from mra.verifier import NativeLeanBackend
from test_premise_search import install_fixture_sources
from test_research_studio import source
from test_search_evaluation import copy_sources

ROOT = Path(__file__).resolve().parents[1]


class CoreToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_sources(self.root)
        for name in (*SOURCES, "mra/typed_terms.py", "mra/typed_definitions.py"):
            shutil.copyfile(ROOT / name, self.root / name)
        guard = patch.object(
            NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")
        )
        guard.start()
        self.addCleanup(guard.stop)
        self.studio = StudioStore(self.root)
        draft = example()
        node = deepcopy(draft["nodes"][0])
        node.update(id="root", kind="problem", depends_on=[])
        self.formal = {
            "backend": "typed",
            "request": {"definitions": draft["definitions"], "nodes": [node]},
        }
        original = source("代数")
        original["question"] = node["target"]["original"]
        original["definitions"] = "有理数平方 square(x)=x*x；其他定义显式保留在形式化候选中。"
        original["assumptions"] = "没有附加假设。"
        row = self.studio.create({"request_id": "initial", "source": original})
        self.key = row["study_id"]
        self.studio.scaffold(self.key, self.change("scaffold"))
        self.api = CoreToolAPI(self.root)
        self.writer = CoreToolAPI(self.root, allow_mutations=True)

    def change(self, request_id, **extra):
        return {
            "request_id": request_id,
            "expected_revision": self.studio.read(self.key)["revision"],
            **extra,
        }

    @staticmethod
    def request(tool, arguments=None, call_id="test-call"):
        return {
            "schema": SCHEMA,
            "call_id": call_id,
            "tool": tool,
            "arguments": {} if arguments is None else arguments,
        }

    def invoke(self, tool, *, writer=False, **arguments):
        api = self.writer if writer else self.api
        return api.call(self.request(tool, arguments))

    def round(self):
        row = self.studio.report(self.key)
        proposal = {
            "source_fingerprint": row["source_fingerprint"],
            "plan_fingerprint": row["plan_fingerprint"],
            "node_id": "root",
            "reasoning": "结构化人工候选；未执行 Lean，未宣称数学正确。",
            "gaps": ["需要确认全部形式化条件并交给 Lean。"],
            "next_actions": ["检查形式化预览。"],
            "formalization": self.formal,
        }
        row = self.studio.add_manual_round(self.key, self.change("round", proposal=proposal))
        self.round_id = row["rounds"][-1]["round_id"]
        return row

    def transfer_request(self):
        self.round()
        preview = self.invoke("study.transfer_preview", study_id=self.key, round_id=self.round_id)
        self.assertEqual(preview["call_status"], "RETURNED", preview)
        return self.change(
            "transfer",
            round_id=self.round_id,
            preview_fingerprint=preview["result"]["preview_fingerprint"],
            reviewer="Local contract operator, not an authenticated identity",
            confirmed=True,
        )

    def transfer(self):
        data = self.transfer_request()
        result = self.invoke("study.transfer", writer=True, study_id=self.key, request=data)
        self.assertEqual(result["call_status"], "RETURNED", result)
        graph_id = result["receipt"]["graph_id"]
        return graph_id, data

    def submit_request(self, graph_id):
        result = self.invoke(
            "verification.preview", study_id=self.key, graph_id=graph_id, node_id="root"
        )
        self.assertEqual(result["call_status"], "RETURNED", result)
        return {
            "request_id": "queue-once",
            "node_id": "root",
            "execution_fingerprint": result["result"]["execution_fingerprint"],
            "reviewer": "Local contract operator",
            "confirmed": True,
        }

    def test_catalog_has_no_storage_or_framework_runtime_dependency(self):
        empty = self.root / "empty"
        empty.mkdir()
        result = CoreToolAPI(empty).call(self.request("catalog"))
        self.assertEqual(result["call_status"], "RETURNED")
        self.assertEqual(list(empty.iterdir()), [])
        catalog = result["result"]
        self.assertTrue(catalog["audit_reads_may_persist_invalidation"])
        self.assertFalse(catalog["executes_lean"])
        self.assertFalse(catalog["generates_model_output"])
        self.assertTrue(
            all(not x["enabled"] for x in catalog["tools"] if x["requires_mutation_access"])
        )
        self.assertNotIn("math_verdict", result)

    def test_duplicate_keys_nonstandard_numbers_utf8_and_depth_fail_before_dispatch(self):
        valid = core_tools.encode(self.request("catalog"))
        too_deep = b'{"tree":' + b"[" * 100 + b"0" + b"]" * 100 + b"}"
        values = (
            valid.replace(b'"schema":', b'"schema":"duplicate","schema":', 1),
            b'{"number":NaN}',
            b'{"number":Infinity}',
            b"\xff",
            too_deep,
            b" " * (core_tools.MAX_INPUT_BYTES + 1),
            b"[]",
        )
        with patch.object(self.api, "_dispatch", side_effect=AssertionError("Must not dispatch")):
            for raw in values:
                with self.subTest(raw=raw[:50]):
                    result = self.api.from_bytes(raw)
                    self.assertEqual(result["call_status"], "REJECTED")
                    self.assertEqual(result["operation_outcome"], "NOT_STARTED")

    def test_python_calls_reject_cycles_non_json_objects_and_nonfinite_numbers(self):
        cyclic = []
        cyclic.append(cyclic)
        for invalid in (cyclic, (1,), {"set"}, float("nan"), {1: "nonstring key"}):
            data = self.request("catalog")
            data["arguments"] = invalid
            self.assertEqual(self.api.call(data)["call_status"], "REJECTED")

    def test_unknown_schema_tools_extra_fields_and_missing_call_id_are_rejected(self):
        valid = self.request("catalog")
        cases = [
            {**valid, "schema": "product-tools-v0"},
            {**valid, "tool": "shell.execute"},
            {**valid, "tool": ["catalog"]},
            {**valid, "extra": "ignore me"},
            {**valid, "arguments": {"extra": True}},
            {**valid, "call_id": "../bad"},
            {k: v for k, v in valid.items() if k != "call_id"},
        ]
        for case in cases:
            self.assertEqual(self.api.call(case)["call_status"], "REJECTED")

    def test_mutation_access_is_explicit_and_checked_before_any_domain_action(self):
        with self.assertRaises(ValueError):
            CoreToolAPI(self.root, allow_mutations="false")
        with patch.object(self.api, "_dispatch", side_effect=AssertionError("Must not dispatch")):
            for name in ("study.transfer", "verification.submit", "verification.cancel"):
                fields = core_tools.TOOLS[name][0]
                result = self.api.call(self.request(name, {f: "placeholder" for f in fields}))
                self.assertEqual(result["call_status"], "REJECTED")

    def test_real_search_returns_source_candidates_and_detects_changed_source(self):
        fixture_source = install_fixture_sources(self.root)
        PremiseIndex(self.root).build()
        result = self.invoke("mathlib.search", query="uniformContinuous_fixture", limit=5)
        self.assertEqual(result["call_status"], "RETURNED", result)
        self.assertEqual(result["result"]["hits"][0]["written_name"], "uniformContinuous_fixture")
        self.assertEqual(result["result"]["math_verdict"], "UNKNOWN")
        fixture_source.write_text(fixture_source.read_text() + "\n-- changed fixture\n")
        self.assertEqual(
            self.invoke("mathlib.search", query="uniformContinuous_fixture", limit=5)[
                "call_status"
            ],
            "FAILED",
        )
        for limit in (True, 0, 6):
            self.assertEqual(
                self.invoke("mathlib.search", query="x", limit=limit)["call_status"], "FAILED"
            )

    def test_formal_preview_derives_original_context_without_creating_graph_or_evidence(self):
        before = self.studio.read(self.key)
        reply = self.invoke(
            "formalization.preview",
            study_id=self.key,
            node_id="root",
            backend="typed",
            formalization=self.formal,
        )
        self.assertEqual(reply["call_status"], "RETURNED", reply)
        result = reply["result"]
        self.assertEqual(result["state"], "REPRESENTABLE", result)
        self.assertEqual((result["math_verdict"], result["evidence_level"]), ("UNKNOWN", "E0"))
        self.assertEqual(result["formal_plan"]["request"]["nodes"], self.formal["request"]["nodes"])
        self.assertEqual(self.studio.read(self.key), before)
        self.assertFalse(list((self.root / "artifacts/typed-research").glob("typedflow-*")))

    def test_changed_original_or_selected_backend_is_diagnostic_not_refutation(self):
        wrong = deepcopy(self.formal)
        wrong["request"]["nodes"][0]["target"]["original"] = "weaker statement"
        for formal in (wrong, {**self.formal, "backend": "calculus"}):
            reply = self.invoke(
                "formalization.preview",
                study_id=self.key,
                node_id="root",
                backend="typed",
                formalization=formal,
            )
            self.assertEqual(reply["call_status"], "RETURNED", reply)
            self.assertEqual(reply["result"]["state"], "DIAGNOSTIC")
            self.assertEqual(reply["result"]["math_verdict"], "UNKNOWN")
        self.assertEqual(self.studio.read(self.key)["transfers"], [])

    def test_transfer_keeps_review_contract_idempotency_and_graph_association(self):
        data = self.transfer_request()
        for bad in ({**data, "confirmed": False}, {**data, "preview_fingerprint": "changed"}):
            result = self.invoke("study.transfer", writer=True, study_id=self.key, request=bad)
            self.assertEqual(result["call_status"], "FAILED")
        self.assertEqual(self.studio.read(self.key)["transfers"], [])
        first = self.invoke("study.transfer", writer=True, study_id=self.key, request=data)
        again = self.writer.call(
            self.request("study.transfer", {"study_id": self.key, "request": data}, "other-call")
        )
        self.assertEqual(first["receipt"]["graph_id"], again["receipt"]["graph_id"])
        self.assertEqual(len(self.studio.read(self.key)["transfers"]), 1)
        graph_id = first["receipt"]["graph_id"]
        report = self.invoke("verification.status", study_id=self.key, graph_id=graph_id)
        self.assertEqual(report["result"]["formal_report"]["attempts"], [])
        self.assertEqual(report["result"]["original_research_verdict"], "UNKNOWN")

    def test_stale_transfer_preview_does_not_silently_refresh_the_confirmation(self):
        data = self.transfer_request()
        row = self.studio.report(self.key)
        plan = deepcopy(row["plan"])
        plan["steps"][0]["approach"] = "新的研究路线，旧的确认不沿用。"
        self.studio.plan(self.key, self.change("new-plan", plan=plan))
        result = self.invoke("study.transfer", writer=True, study_id=self.key, request=data)
        self.assertEqual(result["call_status"], "FAILED")
        self.assertEqual(self.studio.read(self.key)["transfers"], [])

    def test_graph_must_be_bound_to_the_selected_study(self):
        graph_id, _ = self.transfer()
        other = self.studio.create({"request_id": "other-study", "source": source()})
        for tool, extra in (
            ("verification.status", {}),
            ("verification.preview", {"node_id": "root"}),
            ("verification.submit", {"request": {}}),
            ("verification.cancel", {"request": {"attempt_id": "unknown"}}),
        ):
            result = self.invoke(
                tool, writer=True, study_id=other["study_id"], graph_id=graph_id, **extra
            )
            self.assertEqual(result["call_status"], "FAILED")
            self.assertIn("not bound", result["error"]["message"])

    def test_submit_requires_exact_operator_body_and_does_not_supply_confirmation(self):
        graph_id, _ = self.transfer()
        valid = self.submit_request(graph_id)
        cases = (
            {**valid, "confirmed": False},
            {**valid, "confirmed": "true"},
            {**valid, "execution_fingerprint": "changed"},
            {k: v for k, v in valid.items() if k != "reviewer"},
        )
        for body in cases:
            result = self.invoke(
                "verification.submit",
                writer=True,
                study_id=self.key,
                graph_id=graph_id,
                request=body,
            )
            self.assertEqual(result["call_status"], "FAILED")
        self.assertEqual(TypedResearchStore(self.root).report(graph_id)["attempts"], [])

    def test_submit_and_cancel_use_real_queued_state_and_same_business_request(self):
        graph_id, _ = self.transfer()
        data = self.submit_request(graph_id)
        args = {"study_id": self.key, "graph_id": graph_id, "request": data}
        first = self.writer.call(self.request("verification.submit", args, "call-one"))
        again = self.writer.call(self.request("verification.submit", args, "call-two"))
        self.assertEqual(first["call_status"], "RETURNED", first)
        self.assertEqual(first["receipt"]["attempt_id"], again["receipt"]["attempt_id"])
        self.assertEqual(first["result"]["state"], "QUEUED")
        cancelled = self.invoke(
            "verification.cancel",
            writer=True,
            study_id=self.key,
            graph_id=graph_id,
            request={"attempt_id": first["receipt"]["attempt_id"]},
        )
        self.assertEqual(cancelled["call_status"], "RETURNED", cancelled)
        report = cancelled["result"]
        self.assertEqual(len(report["attempts"]), 1)
        self.assertEqual(report["attempts"][0]["state"], "CANCELLED")
        self.assertEqual((report["math_verdict"], report["evidence_level"]), ("UNKNOWN", "E0"))

    def test_output_limit_returns_committed_receipt_and_same_request_can_retrieve_result(self):
        data = self.transfer_request()
        with patch.object(core_tools, "MAX_OUTPUT_BYTES", 1024):
            reply = self.invoke("study.transfer", writer=True, study_id=self.key, request=data)
        self.assertEqual(reply["error"]["code"], "RESULT_LIMIT")
        self.assertEqual(reply["operation_outcome"], "RETURNED")
        self.assertEqual(reply["receipt"]["request_id"], "transfer")
        self.assertIn("graph_id", reply["receipt"])
        replay = self.invoke("study.transfer", writer=True, study_id=self.key, request=data)
        self.assertEqual(replay["call_status"], "RETURNED")
        self.assertEqual(replay["receipt"]["graph_id"], reply["receipt"]["graph_id"])
        self.assertEqual(len(self.studio.read(self.key)["transfers"]), 1)

    def test_failure_after_commit_reports_uncertainty_and_reuses_domain_idempotency(self):
        data = self.transfer_request()
        original = self.writer._dispatch

        def failed_after_return(tool, arguments):
            original(tool, arguments)
            raise OSError("transport failed after the store returned")

        with patch.object(self.writer, "_dispatch", side_effect=failed_after_return):
            reply = self.invoke("study.transfer", writer=True, study_id=self.key, request=data)
        self.assertEqual(reply["operation_outcome"], "UNKNOWN")
        self.assertEqual(reply["receipt"]["request_id"], "transfer")
        replay = self.invoke("study.transfer", writer=True, study_id=self.key, request=data)
        self.assertEqual(replay["call_status"], "RETURNED")
        self.assertEqual(len(self.studio.read(self.key)["transfers"]), 1)

    def test_status_rechecks_audit_and_does_not_cache_by_call_id_or_restore_invalidated_graph(self):
        graph_id, _ = self.transfer()
        call = self.request("verification.status", {"study_id": self.key, "graph_id": graph_id})
        initial = self.api.call(call)
        self.assertFalse(initial["result"]["formal_report"]["invalidated"])
        changed = self.root / "mra/typed_terms.py"
        original = changed.read_bytes()
        changed.write_bytes(original + b"\n# isolated unit source mutation\n")
        damaged = self.api.call(call)
        self.assertTrue(damaged["result"]["formal_report"]["invalidated"])
        changed.write_bytes(original)
        restored = self.api.call(call)
        self.assertTrue(restored["result"]["formal_report"]["invalidated"])
        self.assertEqual(restored["result"]["formal_report"]["math_verdict"], "UNKNOWN")

    def test_cli_uses_one_json_envelope_and_rejects_malformed_stdin(self):
        for payload, code in ((core_tools.encode(self.request("catalog")), 0), (b'{"x":NaN}', 2)):
            result = subprocess.run(
                [sys.executable, str(ROOT / "ops/scripts/core_tools.py")],
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=ROOT,
                env={**os.environ, "PYTHONPATH": str(ROOT)},
                timeout=15,
                check=False,
            )
            self.assertEqual(result.returncode, code, result.stderr.decode())
            self.assertEqual(json.loads(result.stdout)["schema"], core_tools.RESULT_SCHEMA)
            self.assertEqual(result.stderr, b"")

    def assert_cli_input_error(self, path):
        result = subprocess.run(
            [sys.executable, str(ROOT / "ops/scripts/core_tools.py"), "--input", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, b"")
        reply = json.loads(result.stdout)
        self.assertEqual(reply["schema"], core_tools.RESULT_SCHEMA)
        self.assertEqual(reply["call_status"], "REJECTED")
        self.assertEqual(reply["operation_outcome"], "NOT_STARTED")
        self.assertEqual(reply["error"]["code"], "INPUT_IO_ERROR")
        self.assertIsNone(reply["result"])

    def test_cli_missing_input_file_returns_json_before_any_tool_starts(self):
        before = self.studio.read(self.key)
        self.assert_cli_input_error(self.root / "missing-call.json")
        self.assertEqual(self.studio.read(self.key), before)

    def test_cli_input_directory_returns_json_before_any_tool_starts(self):
        self.assert_cli_input_error(self.root)

    def test_cli_unreadable_file_returns_json_instead_of_traceback(self):
        output = io.BytesIO()

        class Output:
            buffer = output

        with (
            patch.object(sys, "argv", ["core_tools", "--input", "unreadable.json"]),
            patch.object(sys, "stdout", Output()),
            patch.object(Path, "open", side_effect=PermissionError("private input")),
            patch.object(CoreToolAPI, "from_bytes", side_effect=AssertionError("No dispatch")),
        ):
            code = core_tools.main(self.root)
        reply = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(reply["error"]["code"], "INPUT_IO_ERROR")
        self.assertEqual(reply["operation_outcome"], "NOT_STARTED")


if __name__ == "__main__":
    unittest.main()
