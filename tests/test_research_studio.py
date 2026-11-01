"""Research workflow contracts and labelled transport simulations, not model capability scores."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from mra.domain import digest
from mra.model_service import ModelBudget, ModelIdentity, ModelRequest, ModelResponse, TokenUsage
from mra.chat_model import ChatModelService
import test_ideas

from mra.research_studio import StudioStore
from mra.research_protocol import scaffold, validate_plan
from mra.tasks import WorkerBusy
from test_http_api import copy_web_inputs, request, running_server

ROOT = test_ideas.ROOT


def source(area="拓扑"):
    return {
        "title": "跨领域研究契约",
        "question": "证明每个紧致度量空间上的连续实函数一致连续。",
        "areas": [area],
        "definitions": "(X,d) 为紧致度量空间；f:X→ℝ 连续。",
        "assumptions": "保留紧致性及连续性，不增加可微性假设。",
    }


class Fixture:
    seen = []

    def __init__(self, root, config, **kwargs):
        self.cancel_requested = kwargs["cancel_requested"]

    def generate(self, req):
        self.seen.append(req)
        c = json.loads(req.input_json)
        if req.task == "math_research_plan":
            result = scaffold(c["source"], c["source_fingerprint"])
            result["steps"][0]["approach"] = "尝试有限子覆盖论证，记录尚待展开的邻域半径选择。"
        else:
            result = {
                "source_fingerprint": c["source_fingerprint"],
                "plan_fingerprint": c["plan_fingerprint"],
                "node_id": c["node"]["id"],
                "reasoning": "候选思路：用紧致性提取有限子覆盖；这段文字尚未交给内核。",
                "gaps": ["证明统一半径适用于所有点对"],
                "next_actions": ["展开邻域与三角不等式"],
                "formalization": None,
            }
        return ModelResponse(
            req.fingerprint,
            ModelIdentity("test", "fixture", "fixture-v1", "fixture"),
            json.dumps(result),
            TokenUsage(10, 20),
        )


class CrossDomainTransportTests(unittest.TestCase):
    setUp = test_ideas.ChatTransportTests.setUp
    close = test_ideas.ChatTransportTests.close

    def test_both_research_tasks_use_real_child_and_correct_system_prompt(self):
        for task, marker in (
            ("plan", "across any mathematical field"),
            ("step", "Investigate ONE"),
        ):
            response = ChatModelService(ROOT, self.config).generate(
                ModelRequest(
                    "studio-transport-" + task,
                    "math_research_" + task,
                    "{}",
                    ModelBudget(timeout_seconds=3),
                    "math-research-" + task + "-v1",
                )
            )
            self.assertIn(marker, self.received[-1][0]["messages"][0]["content"])
            self.assertEqual(response.output_json, '{"test":true}')
            self.assertEqual(self.received[-1][0]["messages"][1]["content"], "{}")


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_web_inputs(self.root)
        self.store = StudioStore(self.root, service_factory=Fixture)
        self.row = self.store.create({"request_id": "initial-study", "source": source()})
        self.key = self.row["study_id"]

    def mutate(self, **extra):
        return {
            "request_id": "test-" + str(self.store.read(self.key)["revision"]),
            "expected_revision": self.store.read(self.key)["revision"],
            **extra,
        }

    def base_plan(self):
        self.row = self.store.scaffold(self.key, self.mutate())
        return self.row

    def proposal(self, formal=None):
        row = self.store.report(self.key)
        return {
            "source_fingerprint": row["source_fingerprint"],
            "plan_fingerprint": row["plan_fingerprint"],
            "node_id": "root",
            "reasoning": "人工推演，尚未验证。",
            "gaps": ["需要完整证明"],
            "next_actions": ["继续细化"],
            "formalization": formal,
        }

    def configure(self):
        p = self.root / "config"
        p.mkdir(exist_ok=True)
        (p / "model.local.json").write_text(
            json.dumps(
                {
                    "endpoint": "http://127.0.0.1:9999/v1/chat/completions",
                    "model": "fixture",
                    "revision": "fixture-v1",
                    "api_key_env": "MRA_STUDIO_TEST_KEY",
                    "token_field": "max_tokens",
                }
            )
        )

    def test_arbitrary_area_source_and_idempotency_preserve_complete_context(self):
        data = {"request_id": "initial-study", "source": source()}
        self.assertEqual(self.store.create(data)["study_id"], self.key)
        data["source"]["assumptions"] += " changed"
        with self.assertRaises(ValueError):
            self.store.create(data)
        row = self.store.create({"request_id": "custom", "source": source("代数拓扑 / 自定义方向")})
        self.assertEqual(row["source"]["definitions"], source()["definitions"])
        self.assertEqual((row["math_verdict"], row["evidence_level"]), ("UNKNOWN", "E0"))

    def test_missing_model_never_fabricates_generation_or_mutates_record(self):
        before = self.store.read(self.key)
        with self.assertRaisesRegex(ValueError, "MODEL_NOT_CONFIGURED"):
            self.store.generate(self.key, self.mutate(operation="plan", node_id="", backend="none"))
        self.assertEqual(before, self.store.read(self.key))

    def test_manual_scaffold_idempotency_and_stale_revision(self):
        data = self.mutate()
        first = self.store.scaffold(self.key, data)
        self.assertEqual(first["revision"], self.store.scaffold(self.key, data)["revision"])
        with self.assertRaises(ValueError):
            self.store.scaffold(self.key, {**data, "request_id": "different"})
        self.assertEqual(first["plan"]["steps"][0]["statement"], source()["question"])

    def test_plan_rejects_changed_goal_added_assumptions_cycles_and_disconnected_nodes(self):
        good = scaffold(source(), digest(source()))
        for mutate in (
            lambda p: p["steps"][0].update(statement="a simpler problem"),
            lambda p: p["steps"][0].update(additional_assumptions=["assume the result"]),
            lambda p: p["steps"][0].update(depends_on=["root"]),
            lambda p: p["steps"].append({**p["steps"][0], "id": "orphan", "kind": "lemma"}),
        ):
            bad = deepcopy(good)
            mutate(bad)
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_plan(bad, source(), digest(source()))

    def test_intermediate_conditional_assumptions_remain_explicit(self):
        p = scaffold(source(), digest(source()))
        lemma = {
            **deepcopy(p["steps"][0]),
            "id": "conditional",
            "kind": "lemma",
            "additional_assumptions": ["condition only for this lemma"],
        }
        p["steps"].insert(0, lemma)
        p["steps"][-1]["depends_on"] = ["conditional"]
        result = validate_plan(p, source(), digest(source()))
        self.assertEqual(
            result["steps"][0]["additional_assumptions"], lemma["additional_assumptions"]
        )

    def test_clarification_cannot_silently_generate_a_plan(self):
        p = scaffold(source(), digest(source()))
        p["questions"] = ["使用哪种拓扑？"]
        with self.assertRaises(ValueError):
            validate_plan(p, source(), digest(source()))
        p["steps"] = []
        self.assertEqual(validate_plan(p, source(), digest(source())), p)

    def test_manual_reasoning_never_promotes_model_success_or_old_plan(self):
        self.base_plan()
        p = self.proposal()
        p["math_verdict"] = "PROVED"
        with self.assertRaises(ValueError):
            self.store.add_manual_round(self.key, self.mutate(proposal=p))
        p = self.proposal()
        row = self.store.add_manual_round(self.key, self.mutate(proposal=p))
        self.assertEqual(row["rounds"][0]["evidence_level"], "E0")
        changed = deepcopy(row["plan"])
        changed["steps"][0]["approach"] = "另一条路线"
        self.store.plan(self.key, self.mutate(plan=changed))
        with self.assertRaises(ValueError):
            self.store.add_manual_round(self.key, self.mutate(proposal=p))

    def test_model_plan_and_continuation_are_fixture_labelled_with_complete_context(self):
        self.configure()
        Fixture.seen = []
        row = self.store.generate(
            self.key, self.mutate(operation="plan", node_id="", backend="none")
        )
        self.assertEqual(row["model_calls"], 0)
        self.assertEqual(row["simulation_calls"], 1)
        row = self.store.generate(
            self.key, self.mutate(operation="step", node_id="root", backend="none")
        )
        row = self.store.generate(
            self.key, self.mutate(operation="step", node_id="root", backend="none")
        )
        context = json.loads(Fixture.seen[-1].input_json)
        self.assertEqual(context["source"], source())
        self.assertEqual(len(context["previous_attempts"]), 1)
        self.assertEqual(len(row["rounds"]), 2)
        self.assertEqual(row["math_verdict"], "UNKNOWN")

    def test_model_response_cannot_change_source_identity(self):
        self.configure()

        class Wrong(Fixture):
            def generate(self, req):
                r = super().generate(req)
                return ModelResponse("wrong-request", r.identity, r.output_json, r.usage)

        self.store.service_factory = Wrong
        row = self.store.generate(
            self.key, self.mutate(operation="plan", node_id="", backend="none")
        )
        self.assertEqual(row["operations"][-1]["state"], "FAILED")
        self.assertIsNone(row["plan"])

    def test_non_object_model_response_finishes_failed_and_can_be_followed_by_new_request(self):
        self.configure()
        self.base_plan()

        class NonObject(Fixture):
            def generate(self, req):
                result = super().generate(req)
                return ModelResponse(req.fingerprint, result.identity, "[]", result.usage)

        self.store.service_factory = NonObject
        row = self.store.generate(
            self.key, self.mutate(operation="step", node_id="root", backend="none")
        )
        self.assertEqual(row["operations"][-1]["state"], "FAILED")
        self.assertIsNone(row["active_operation"])
        self.assertEqual(row["rounds"], [])
        self.store.service_factory = Fixture
        row = self.store.generate(
            self.key, self.mutate(operation="step", node_id="root", backend="none")
        )
        self.assertEqual(row["operations"][-1]["state"], "COMPLETED")
        self.assertEqual(row["math_verdict"], "UNKNOWN")

    def test_live_model_cannot_be_recovered_and_cancellation_discards_output(self):
        self.configure()
        entered = threading.Event()
        release = threading.Event()
        results = []

        class Waiting(Fixture):
            def generate(self, req):
                entered.set()
                release.wait(5)
                if self.cancel_requested():
                    raise InterruptedError("cancelled")
                return super().generate(req)

        self.store.service_factory = Waiting
        data = self.mutate(operation="plan", node_id="", backend="none")
        t = threading.Thread(target=lambda: results.append(self.store.generate(self.key, data)))
        t.start()
        self.assertTrue(entered.wait(5))
        op = self.store.report(self.key)["active_operation"]["id"]
        try:
            with self.assertRaises(WorkerBusy):
                self.store.recover(self.key, {"operation_id": op})
            self.store.cancel(self.key, {"operation_id": op})
        finally:
            release.set()
            t.join(10)
        self.assertFalse(t.is_alive())
        self.assertEqual(results[0]["operations"][-1]["state"], "CANCELLED")
        self.assertIsNone(results[0]["plan"])
        self.assertEqual(
            self.store.generate(self.key, data)["operations"][-1]["state"], "CANCELLED"
        )

    def test_actual_complete_candidate_is_required_before_transfer(self):
        self.base_plan()
        candidate = {
            "backend": "typed",
            "request": {
                "definitions": [],
                "nodes": [
                    {
                        "id": "root",
                        "kind": "problem",
                        "depends_on": [],
                        "target": {"original": "changed source"},
                    }
                ],
            },
        }
        with self.assertRaises(ValueError):
            self.store.add_manual_round(self.key, self.mutate(proposal=self.proposal(candidate)))

    def test_handoff_requires_current_preview_named_confirmation_and_does_not_execute(self):
        self.base_plan()
        formal = {
            "backend": "typed",
            "request": {
                "definitions": [],
                "nodes": [
                    {
                        "id": "root",
                        "kind": "problem",
                        "depends_on": [],
                        "target": {"original": source()["question"]},
                    }
                ],
            },
        }
        row = self.store.add_manual_round(self.key, self.mutate(proposal=self.proposal(formal)))
        rid = row["rounds"][0]["round_id"]

        class FakeBackend:
            def __init__(self):
                self.created = []

            def preview(self, raw):
                return {"request": raw, "plan_fingerprint": digest(raw), "math_verdict": "UNKNOWN"}

            def create(self, data):
                self.created.append(data)
                return {"graph_id": "typedflow-" + "a" * 32, "attempts": []}

        backend = FakeBackend()
        with patch("mra.research_studio.backend_store", return_value=backend):
            p = self.store.preview_transfer(self.key, {"round_id": rid})
            data = self.mutate(
                round_id=rid,
                preview_fingerprint=p["preview_fingerprint"],
                reviewer="contract author",
                confirmed=False,
            )
            with self.assertRaises(ValueError):
                self.store.transfer(self.key, data)
            data["confirmed"] = True
            data["preview_fingerprint"] = "changed"
            with self.assertRaises(ValueError):
                self.store.transfer(self.key, data)
            data["preview_fingerprint"] = p["preview_fingerprint"]
            row = self.store.transfer(self.key, data)
            self.store.transfer(self.key, data)
        self.assertEqual(len(backend.created), 1)
        self.assertEqual(row["math_verdict"], "UNKNOWN")
        self.assertIn(row["project_id"], row["transfers"][0]["href"])

    def test_corrupted_record_never_becomes_evidence(self):
        p = self.store.directory(self.key) / "record.json"
        r = json.loads(p.read_text())
        r["payload"]["source"]["question"] = "changed"
        p.write_text(json.dumps(r))
        with self.assertRaises(ValueError):
            self.store.report(self.key)

    def test_loopback_http_has_guarded_writes_and_new_assets(self):
        with running_server(self.root) as server:
            for path in ("/studio", "/studio.js", "/studio.css"):
                self.assertEqual(request(server, "GET", path)[0], 200)
            status, c, _ = request(server, "GET", "/v1/studio/catalog")
            self.assertEqual(status, 200)
            self.assertFalse(c["capabilities"]["all_mathematics_solved"])
            data = {"request_id": "http-problem", "source": source("概率与统计")}
            self.assertEqual(
                request(server, "POST", "/v1/studio", data, headers={"X-MRA-CSRF": "bad"})[0], 403
            )
            status, row, _ = request(server, "POST", "/v1/studio", data)
            self.assertEqual(status, 201)
            self.assertEqual(row["math_verdict"], "UNKNOWN")
            self.assertEqual(request(server, "GET", "/v1/studio?unexpected=1")[0], 400)
            self.assertEqual(
                request(
                    server,
                    "POST",
                    "/v1/studio/" + row["study_id"] + "/scaffold",
                    {"request_id": "http-scaffold", "expected_revision": 0},
                )[0],
                200,
            )


if __name__ == "__main__":
    unittest.main()
