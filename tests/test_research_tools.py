"""Tool-loop contracts. All model responses are labelled fixtures, not capability scores."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch

from mra import research_tools as tools
from mra.chat_model import ChatModelService
from mra.model_service import ModelIdentity, ModelResponse, TokenUsage
from mra.premise_search import PremiseIndex
from test_premise_search import install_fixture_sources
from test_research_studio import StudioTests, Fixture, source
from test_http_api import request, running_server


class ResearchToolTests(unittest.TestCase):
    setUp = StudioTests.setUp
    mutate = StudioTests.mutate
    configure = StudioTests.configure
    base_plan = StudioTests.base_plan
    proposal = StudioTests.proposal

    def prepare(self, actions):
        self.configure()
        self.base_plan()
        self.source_file = install_fixture_sources(self.root)
        PremiseIndex(self.root).build()
        self.seen = []
        parent = self

        class Scripted(Fixture):
            def generate(self, request):
                parent.seen.append(request)
                context = json.loads(request.input_json)
                action = actions[len(parent.seen) - 1]
                if callable(action):
                    return action(request, context)
                if action is None:
                    return super().generate(request)
                output = {
                    "source_fingerprint": context["source_fingerprint"],
                    "plan_fingerprint": context["plan_fingerprint"],
                    "node_id": context["node"]["id"],
                    "tool_request": action,
                }
                return ModelResponse(
                    request.fingerprint,
                    ModelIdentity("test", "fixture", "fixture-v1", "fixture"),
                    json.dumps(output),
                    TokenUsage(10, 20),
                )

        self.store.service_factory = Scripted

    def run_step(self):
        return self.store.generate(
            self.key, self.mutate(operation="step", node_id="root", backend="none")
        )

    @staticmethod
    def search(query="uniformContinuous_fixture"):
        return {"kind": "search_mathlib", "query": query}

    def test_real_search_child_and_second_model_call_preserve_complete_source_and_archive(self):
        self.prepare([self.search(), None])
        row = self.run_step()
        operation = row["operations"][-1]
        self.assertEqual(operation["state"], "COMPLETED")
        self.assertEqual(row["simulation_calls"], 2)
        self.assertEqual(row["model_calls"], 0)
        self.assertEqual(len(operation["tools"]), 1)
        for request_ in self.seen:
            self.assertEqual(json.loads(request_.input_json)["source"], source())
        context = json.loads(self.seen[-1].input_json)
        hit = context["tool_session"]["history"][0]["result"]["hits"][0]
        self.assertEqual(hit["item"]["written_name"], "uniformContinuous_fixture")
        self.assertEqual(hit["item"]["math_verdict"], "UNKNOWN")
        self.assertLess(
            self.seen[-1].budget.max_output_tokens, self.seen[0].budget.max_output_tokens
        )
        trace = tools.trace(self.store, self.key, operation["id"])
        self.assertEqual(len(trace["events"]), 5)
        self.assertEqual(row["rounds"][0]["operation_id"], operation["id"])
        self.assertEqual(row["transfers"], [])
        self.assertEqual((row["math_verdict"], row["evidence_level"]), ("UNKNOWN", "E0"))

    def test_representation_diagnostic_is_returned_for_repair_without_changing_goal(self):
        wrong = {
            "kind": "preview_formalization",
            "formalization": {
                "backend": "typed",
                "request": {
                    "definitions": [],
                    "nodes": [
                        {"id": "root", "kind": "problem", "target": {"original": "weaker target"}}
                    ],
                },
            },
        }
        self.prepare([self.search(), wrong, None])
        row = self.run_step()
        context = json.loads(self.seen[-1].input_json)
        diagnostic = context["tool_session"]["history"][-1]["result"]
        self.assertEqual(diagnostic["state"], "DIAGNOSTIC")
        self.assertIn("exact research step", diagnostic["error"])
        self.assertEqual(diagnostic["new_lean_checks"], 0)
        self.assertEqual(row["operations"][-1]["state"], "COMPLETED")
        self.assertEqual(len(row["rounds"]), 1)

    def test_four_call_budget_stops_before_fourth_tool_and_keeps_partial_trace(self):
        self.prepare(
            [self.search(q) for q in ("first_query", "second_query", "third_query", "fourth_query")]
        )
        row = self.run_step()
        op = row["operations"][-1]
        self.assertEqual(op["state"], "FAILED")
        self.assertIn("budget", op["error"])
        self.assertEqual((len(op["calls"]), len(op["tools"])), (4, 3))
        self.assertEqual(row["rounds"], [])
        self.assertTrue(tools.trace(self.store, self.key, op["id"])["events"])

    def test_duplicate_query_becomes_diagnostic_without_repeating_source_search(self):
        self.prepare([self.search(), self.search(), None])
        row = self.run_step()
        self.assertEqual(row["operations"][-1]["tools"][-1]["state"], "DIAGNOSTIC")
        self.assertIn("already ran", row["operations"][-1]["tools"][-1]["error"])

    def test_notebook_counts_all_calls_and_replaying_request_does_not_spend_budget(self):
        self.prepare(
            ([self.search("a_query"), self.search("b_query"), self.search("c_query"), None]) * 4
        )
        for _ in range(4):
            data = self.mutate(operation="step", node_id="root", backend="none")
            row = self.store.generate(self.key, data)
            self.assertEqual(row["operations"][-1]["state"], "COMPLETED")
        self.assertEqual(row["simulation_calls"], 16)
        self.assertEqual(len(self.seen), 16)
        replay = self.store.generate(self.key, data)
        self.assertEqual(replay["revision"], row["revision"])
        self.assertEqual(len(self.seen), 16)
        before = self.store.read(self.key)
        with self.assertRaisesRegex(ValueError, "Sixteen model calls"):
            self.run_step()
        self.assertEqual(before, self.store.read(self.key))

    def test_foreign_identity_and_arbitrary_tool_are_rejected_without_execution(self):
        def foreign(request_, context):
            output = {
                "source_fingerprint": "wrong",
                "plan_fingerprint": context["plan_fingerprint"],
                "node_id": "root",
                "tool_request": self.search(),
            }
            return ModelResponse(
                request_.fingerprint,
                ModelIdentity("test", "fixture", "v1", "fixture"),
                json.dumps(output),
                TokenUsage(1, 1),
            )

        for action in (foreign, {"kind": "shell", "command": "unsafe"}):
            with self.subTest(action=action):
                # A new explicit operation follows the failed one; no automatic retry.
                if not hasattr(self, "source_file"):
                    self.prepare([action])
                else:
                    self.prepare_next([action])
                with patch.object(tools, "bounded_tool") as tool:
                    row = self.run_step()
                    tool.assert_not_called()
                self.assertEqual(row["operations"][-1]["state"], "FAILED")
                self.assertEqual(row["source"], source())

    def prepare_next(self, actions):
        # Reuse the fixture class with its captured action list through a fresh setup helper.
        self.seen.clear()
        parent = self

        class Next(Fixture):
            def generate(self, request_):
                parent.seen.append(request_)
                output = {
                    "source_fingerprint": json.loads(request_.input_json)["source_fingerprint"],
                    "plan_fingerprint": json.loads(request_.input_json)["plan_fingerprint"],
                    "node_id": "root",
                    "tool_request": actions[0],
                }
                return ModelResponse(
                    request_.fingerprint,
                    ModelIdentity("test", "fixture", "v1", "fixture"),
                    json.dumps(output),
                    TokenUsage(1, 1),
                )

        self.store.service_factory = Next

    def test_transport_failure_has_no_implicit_second_call(self):
        def failure(*_):
            raise OSError("labelled fixture transport failure")

        self.prepare([failure])
        row = self.run_step()
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(row["operations"][-1]["calls"][0]["state"], "FAILED")
        self.assertEqual(row["rounds"], [])

    def test_nearly_full_notebook_rejects_before_a_model_call_or_running_state(self):
        self.prepare([None])
        for _ in range(16):
            proposal = self.proposal()
            proposal.update(
                reasoning="r" * 24000, gaps=["g" * 4000] * 12, next_actions=["n" * 4000] * 12
            )
            self.store.add_manual_round(self.key, self.mutate(proposal=proposal))
        before = self.store.read(self.key)
        with self.assertRaisesRegex(ValueError, "storage headroom"):
            self.run_step()
        self.assertEqual(self.seen, [])
        self.assertEqual(before, self.store.read(self.key))

    def test_cancel_after_model_output_prevents_tool_and_round(self):
        def cancel(request_, context):
            op = self.store.report(self.key)["active_operation"]
            self.store.cancel(self.key, {"operation_id": op["id"]})
            output = {
                "source_fingerprint": context["source_fingerprint"],
                "plan_fingerprint": context["plan_fingerprint"],
                "node_id": "root",
                "tool_request": self.search(),
            }
            return ModelResponse(
                request_.fingerprint,
                ModelIdentity("test", "fixture", "v1", "fixture"),
                json.dumps(output),
                TokenUsage(1, 1),
            )

        self.prepare([cancel])
        with patch.object(tools, "bounded_tool") as tool:
            row = self.run_step()
            tool.assert_not_called()
        self.assertEqual(row["operations"][-1]["state"], "CANCELLED")
        self.assertEqual(row["rounds"], [])

    def test_changed_source_excluded_from_next_call_but_original_tool_artifact_preserved(self):
        self.prepare([self.search(), None])
        original = tools.bounded_tool

        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            self.source_file.write_text(self.source_file.read_text() + "\n-- drift fixture\n")
            return result

        with patch.object(tools, "bounded_tool", side_effect=changed):
            row = self.run_step()
        event = json.loads(self.seen[-1].input_json)["tool_session"]["history"][0]
        self.assertEqual(event["result"]["hits"], [])
        self.assertTrue(event["result"]["unavailable"])
        archived = tools.trace(self.store, self.key, row["operations"][-1]["id"])
        self.assertTrue(
            next(x for x in archived["events"] if x["event"] == "TOOL_FINISHED")["data"]["result"][
                "hits"
            ]
        )

    def test_output_tokens_are_shared_across_calls(self):
        def exhausted(request_, context):
            output = {
                "source_fingerprint": context["source_fingerprint"],
                "plan_fingerprint": context["plan_fingerprint"],
                "node_id": "root",
                "tool_request": self.search(),
            }
            return ModelResponse(
                request_.fingerprint,
                ModelIdentity("test", "fixture", "v1", "fixture"),
                json.dumps(output),
                TokenUsage(10, 8192),
            )

        self.prepare([exhausted])
        row = self.run_step()
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(row["operations"][-1]["state"], "FAILED")
        self.assertIn("output budget", row["operations"][-1]["error"])

    def test_trace_hash_damage_does_not_hide_original_notebook(self):
        self.prepare([self.search(), None])
        row = self.run_step()
        op = row["operations"][-1]
        path = self.store.directory(self.key) / op["id"] / op["trace_files"][0]["name"]
        path.write_text("damaged")
        self.assertEqual(self.store.report(self.key)["source"], source())
        with self.assertRaisesRegex(ValueError, "artifact changed"):
            tools.trace(self.store, self.key, op["id"])
        with running_server(self.root) as server:
            self.assertEqual(
                request(server, "GET", "/v1/studio/" + self.key + "/trace/" + op["id"])[0], 400
            )
            self.assertEqual(request(server, "GET", "/v1/studio/" + self.key)[0], 200)

    def test_real_tool_child_can_be_cancelled_while_sqlite_is_locked(self):
        self.prepare([None])
        index = PremiseIndex(self.root)
        with sqlite3.connect(index.path) as db:
            db.execute("BEGIN EXCLUSIVE")
            cancelled = threading.Event()
            timer = threading.Timer(0.3, cancelled.set)
            started = time.monotonic()
            timer.start()
            try:
                with self.assertRaises(InterruptedError):
                    tools.bounded_tool(
                        self.root,
                        self.search(),
                        {},
                        set(),
                        time.monotonic() + 5,
                        cancelled.is_set,
                        None,
                    )
            finally:
                timer.cancel()
                db.rollback()
            self.assertLess(time.monotonic() - started, 3)

    def test_two_actual_transport_children_use_fixture_http_responses_and_real_search(self):
        self.prepare([None])
        package = self.root / "mra"
        for path in (Path(__file__).resolve().parents[1] / "mra").glob("*.py"):
            shutil.copy2(path, package / path.name)
        received = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                received.append(body)
                context = json.loads(body["messages"][1]["content"])
                result = {
                    "source_fingerprint": context["source_fingerprint"],
                    "plan_fingerprint": context["plan_fingerprint"],
                    "node_id": "root",
                }
                if len(received) == 1:
                    result["tool_request"] = owner.search()
                else:
                    result.update(
                        reasoning="Transport fixture consumed the real local source hit.",
                        gaps=["This is not a model capability assessment"],
                        next_actions=[],
                        formalization=None,
                    )
                raw = json.dumps(
                    {
                        "model": "scripted-protocol-fixture",
                        "choices": [
                            {"finish_reason": "stop", "message": {"content": json.dumps(result)}}
                        ],
                        "usage": {"prompt_tokens": 20, "completion_tokens": 20},
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        class TransportFixture(ChatModelService):
            def generate(self, request_):
                result = super().generate(request_)
                # Explicitly label the responder as a simulation before persisting it.
                return ModelResponse(
                    result.request_fingerprint,
                    ModelIdentity("loopback", "scripted-protocol-fixture", "v1", "fixture"),
                    result.output_json,
                    result.usage,
                )

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
        thread.start()
        config = self.root / "config/model.local.json"
        value = json.loads(config.read_text())
        value["endpoint"] = "http://127.0.0.1:" + str(server.server_port) + "/v1/chat/completions"
        config.write_text(json.dumps(value))
        self.store.service_factory = TransportFixture
        try:
            row = self.run_step()
        finally:
            server.shutdown()
            thread.join(5)
            server.server_close()
        self.assertEqual(row["operations"][-1]["state"], "COMPLETED")
        self.assertEqual(row["simulation_calls"], 2)
        self.assertEqual(row["model_calls"], 0)
        self.assertEqual(len(received), 2)
        self.assertIn("preview_formalization", received[-1]["messages"][0]["content"])
        for body in received:
            self.assertEqual(json.loads(body["messages"][1]["content"])["source"], source())


if __name__ == "__main__":
    unittest.main()
