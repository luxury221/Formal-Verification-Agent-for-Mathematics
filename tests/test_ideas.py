"""Protocol simulations are labelled; all Lean verdict tests use actual workers."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mra.chat_model import ChatModelService, ModelConfig
from mra.domain import Alignment, canonical, digest
from mra.evidence import EvidenceGate
from mra.formalizer import SourceClaim
from mra.ideas import IdeaStore
from mra.model_service import ModelBudget, ModelIdentity, ModelRequest, ModelResponse, TokenUsage
from mra.nat_ideas import NatTarget, proof_source, render_formula
from mra.verifier import NativeLeanBackend
from test_tasks import copy_inputs
from test_http_api import copy_web_inputs, request, running_server

ROOT = Path(__file__).resolve().parents[1]


def num(n):
    return {"op": "num", "value": n}


def var(name="n"):
    return {"op": "var", "name": name}


def binary(op, left, right):
    return {"op": op, "left": left, "right": right}


def forall(body):
    return {"op": "forall", "name": "n", "body": body}


FORMULA = forall(binary("lt", var(), binary("add", var(), num(1))))
FALSE_FORMULA = forall(binary("eq", binary("add", var(), num(1)), var()))
CONFIG = ModelConfig(
    "http://127.0.0.1:9999/v1/chat/completions",
    "protocol-simulation",
    "test-fixture-v1",
    "MRA_TEST_KEY",
    "max_tokens",
)


class SimulatedService:
    """No real model: supplies proposal data, never a Lean verdict."""

    def __init__(self, formula=FORMULA, plans=None, questions=None):
        self.formula, self.plans, self.questions = (
            formula,
            plans or [{"direction": "prove", "strategy": "omega", "witnesses": []}],
            questions,
        )
        self.proof_calls = 0

    def generate(self, request):
        data = json.loads(request.input_json)
        if request.task == "nat_formalize":
            output = {
                "source_fingerprint": data["source_fingerprint"],
                "status": "NEEDS_CLARIFICATION" if self.questions else "CANDIDATES_READY",
                "candidates": []
                if self.questions
                else [
                    {
                        "formula": self.formula,
                        "interpretation": "测试作者提供的自然数命题映射，非模型输出",
                    }
                ],
                "questions": self.questions or [],
            }
        else:
            plan = self.plans[min(self.proof_calls, len(self.plans) - 1)]
            self.proof_calls += 1
            output = {"target_fingerprint": data["target_fingerprint"], "plan": plan}
        return ModelResponse(
            request.fingerprint,
            ModelIdentity("protocol-test", "fixture", "v1", "fixture"),
            json.dumps(output),
            TokenUsage(20, 30),
        )


class NatFormulaTests(unittest.TestCase):
    def test_target_scope_and_version_change_its_contract(self):
        source = SourceClaim("source", 1, "对任意自然数 n，n + 1 大于 n。")
        target = NatTarget.create(source, FORMULA, "自然数，严格不等式")
        self.assertIn("∀", target.contract("prove")[0].formal_statement)
        self.assertTrue(target.contract("refute")[1].checked_statement.startswith("¬ ("))
        revised = NatTarget.create(
            source.revise("对任意自然数 n，n + 2 大于 n。", ()), FORMULA, "自然数，严格不等式"
        )
        self.assertNotEqual(target.fingerprint, revised.fingerprint)
        self.assertEqual(revised.contract("prove")[0].version, 2)

    def test_source_payloads_free_variables_and_wrong_types_are_rejected(self):
        for formula in (
            binary("eq", var(), num(1)),
            {"op": "lean", "source": "by sorry"},
            forall(binary("eq", num(True), num(1))),
            {"op": "forall", "name": "n) := by sorry\n#eval", "body": FORMULA},
            {**FORMULA, "extra": "run_cmd"},
            binary("eq", FORMULA, num(1)),
        ):
            with self.subTest(formula=formula), self.assertRaises(ValueError):
                render_formula(formula)

    def test_shadowing_depth_size_and_integer_budgets(self):
        for formula in (forall(FORMULA), binary("eq", num(1000001), num(1))):
            with self.assertRaises(ValueError):
                render_formula(formula)
        deep = binary("eq", num(0), num(0))
        for _ in range(26):
            deep = {"op": "not", "body": deep}
        with self.assertRaises(ValueError):
            render_formula(deep)

    def test_arbitrary_tactics_and_invalid_witnesses_never_render(self):
        target = NatTarget.create(SourceClaim("s", 1, "自然数命题"), FORMULA, "测试")
        for plan in (
            {"direction": "prove", "strategy": "by sorry", "witnesses": []},
            {"direction": "prove", "strategy": "omega", "witnesses": [1]},
            {"direction": "refute", "strategy": "counterexample", "witnesses": [1, 2]},
            {"direction": "refute", "strategy": "counterexample", "witnesses": [True]},
        ):
            with self.assertRaises(ValueError):
                proof_source(plan, target)


class IdeaStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_inputs(self.root)
        self.service = SimulatedService()
        self.store = IdeaStore(self.root, service_factory=lambda config: self.service)

    def create(self):
        return self.store.create(
            {
                "request_id": uuid.uuid4().hex,
                "statement": "对任意自然数 n，n + 1 大于 n。",
                "context": [],
            }
        )

    def ready(self):
        with patch.object(ModelConfig, "load", return_value=CONFIG):
            self.create()
            return self.store.work_once()["idea"]

    def test_missing_config_saves_but_never_calls_a_model(self):
        idea = self.create()
        self.assertEqual(idea["state"], "WAITING_FOR_MODEL")
        self.assertEqual(self.store.work_once()["worker_state"], "IDLE")
        self.assertEqual(idea["model_calls"], 0)
        self.assertFalse((self.root / "artifacts/runs").exists())
        with self.assertRaises(ValueError):
            self.store.generate(idea["idea_id"])

    def test_creation_is_idempotent_and_conflicting_content_is_rejected(self):
        data = {"request_id": "same", "statement": "自然数命题", "context": []}
        first = self.store.create(data)
        self.assertEqual(first, self.store.create(data))
        with self.assertRaises(ValueError):
            self.store.create({**data, "statement": "另一命题"})

    def test_candidate_requires_exact_named_confirmation(self):
        idea = self.ready()
        self.assertEqual(idea["state"], "WAITING_FOR_REVIEW")
        self.assertEqual(idea["math_verdict"], "UNKNOWN")
        self.assertEqual(idea["model_calls"], 0)
        self.assertEqual(idea["simulation_calls"], 1)
        data = {
            "target_fingerprint": idea["candidates"][0]["target_fingerprint"],
            "source_version": 1,
            "reviewer": "test-author",
            "confirmed": True,
        }
        with patch.object(ModelConfig, "load", return_value=CONFIG):
            for changed in (
                {"target_fingerprint": "bad"},
                {"source_version": 2},
                {"confirmed": False},
            ):
                with self.assertRaises(ValueError):
                    self.store.confirm(idea["idea_id"], {**data, **changed})
            confirmed = self.store.confirm(idea["idea_id"], data)
        self.assertEqual(confirmed["state"], "QUEUED")
        self.assertEqual(confirmed["math_verdict"], "UNKNOWN")
        self.assertFalse((self.root / "artifacts/runs").exists())

    def test_revision_invalidates_candidate_and_old_review(self):
        idea = self.ready()
        revised = self.store.revise(
            idea["idea_id"],
            {"expected_version": 1, "statement": "对任意自然数 n，n + 2 大于 n。", "context": []},
        )
        self.assertEqual(revised["source"]["version"], 2)
        self.assertEqual(revised["candidates"], [])
        with patch.object(ModelConfig, "load", return_value=CONFIG), self.assertRaises(ValueError):
            self.store.confirm(
                idea["idea_id"],
                {
                    "source_version": 1,
                    "target_fingerprint": idea["candidates"][0]["target_fingerprint"],
                    "reviewer": "a",
                    "confirmed": True,
                },
            )

    def test_ambiguous_input_never_reaches_lean(self):
        self.service.questions = ["这里的数是自然数还是实数？"]
        idea = self.ready()
        self.assertEqual(idea["state"], "NEEDS_CLARIFICATION")
        self.assertEqual(idea["math_verdict"], "NEEDS_CLARIFICATION")
        self.assertFalse((self.root / "artifacts/runs").exists())

    def test_cancel_and_crash_recovery_never_promote_old_results(self):
        with patch.object(ModelConfig, "load", return_value=CONFIG):
            idea = self.create()
            self.store.cancel(idea["idea_id"])
            self.assertEqual(self.store.work_once()["worker_state"], "IDLE")
            self.assertEqual(self.store.report(idea["idea_id"])["state"], "CANCELLED")
            idea2 = self.create()
            self.store.mutate(
                idea2["idea_id"],
                lambda body: body.update(state="RUNNING", result={"math_verdict": "PROVED"}),
            )
            self.store.work_once()
            recovered = self.store.report(idea2["idea_id"])
            self.assertEqual(recovered["error"], "INTERRUPTED")
            self.assertEqual(recovered["math_verdict"], "UNKNOWN")

    def test_configuration_drift_blocks_queued_generation(self):
        with patch.object(ModelConfig, "load", return_value=CONFIG):
            self.create()
        result = self.store.work_once()["idea"]
        self.assertEqual(result["state"], "FAILED")
        self.assertEqual(result["math_verdict"], "UNKNOWN")
        self.assertEqual(result["traces"], [])

    def test_new_http_routes_share_existing_origin_and_csrf_guards(self):
        webroot = self.root / "web-test"
        webroot.mkdir()
        copy_web_inputs(webroot)
        with running_server(webroot) as server:
            self.assertEqual(request(server, "GET", "/ideas")[0], 200)
            data = {
                "request_id": "http-new",
                "statement": "对任意自然数 n，n + 1 大于 n。",
                "context": [],
            }
            self.assertEqual(
                request(server, "POST", "/v1/ideas", data, headers={"X-MRA-CSRF": "bad"})[0], 403
            )
            code, idea, _ = request(server, "POST", "/v1/ideas", data)
            self.assertEqual(code, 202)
            self.assertEqual(idea["state"], "WAITING_FOR_MODEL")
            self.assertEqual(request(server, "GET", "/v1/ideas/" + idea["idea_id"])[0], 200)
            self.assertEqual(
                request(server, "POST", "/v1/ideas/" + idea["idea_id"] + "/generate", {})[0], 400
            )


class ChatTransportTests(unittest.TestCase):
    def setUp(self):
        self.received = []
        self.mode = "ok"
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.received.append((body, self.headers.get("Authorization")))
                if owner.mode == "slow":
                    time.sleep(1)
                if owner.mode == "redirect":
                    self.send_response(307)
                    self.send_header("Location", "http://127.0.0.1:1/leak")
                    self.end_headers()
                    return
                raw = canonical(
                    {
                        "model": "protocol-responder-v1",
                        "choices": [
                            {
                                "finish_reason": "length" if owner.mode == "truncated" else "stop",
                                "message": {"content": '{"test":true}'},
                            }
                        ],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
                    }
                )
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}
        )
        self.thread.start()
        self.addCleanup(self.close)
        self.config = ModelConfig(
            f"http://127.0.0.1:{self.server.server_port}/v1/chat/completions",
            "requested-model",
            "deployment-test-v1",
            "MRA_TEST_KEY",
            "max_completion_tokens",
        )

    def close(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def call(self, timeout=3, cancel=None):
        service = ChatModelService(ROOT, self.config, cancel_requested=cancel)
        return service.generate(
            ModelRequest(
                "test",
                "nat_formalize",
                "{}",
                ModelBudget(timeout_seconds=timeout),
                "nat-formalizer-output-v1",
            )
        )

    def test_actual_http_headers_model_identity_usage_and_token_cap(self):
        with patch.dict(os.environ, {"MRA_TEST_KEY": "test-secret-never-an-artifact"}):
            response = self.call()
        body, authorization = self.received[0]
        self.assertEqual(authorization, "Bearer test-secret-never-an-artifact")
        self.assertEqual(body["max_completion_tokens"], 2048)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(response.identity.model, "protocol-responder-v1")
        self.assertEqual(response.usage.output_tokens, 3)
        self.assertNotIn("test-secret", response.output_json)

    def test_wall_clock_timeout_kills_slow_provider(self):
        self.mode = "slow"
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            self.call(0.15)
        self.assertLess(time.monotonic() - started, 1)

    def test_cancel_kills_inflight_model_request(self):
        self.mode = "slow"
        started = time.monotonic()
        with self.assertRaises(InterruptedError):
            self.call(cancel=lambda: time.monotonic() - started > 0.15)
        self.assertLess(time.monotonic() - started, 1)

    def test_redirect_and_truncated_response_are_not_candidates(self):
        for mode in ("redirect", "truncated"):
            self.mode = mode
            with self.subTest(mode=mode), self.assertRaises(OSError):
                self.call()

    def test_plaintext_remote_and_secret_in_url_are_rejected(self):
        for endpoint in (
            "http://remote.example/v1/chat/completions",
            "https://key@remote.example/v1/chat/completions",
            "https://remote.example/?api_key=secret",
        ):
            with self.assertRaises(ValueError):
                ModelConfig(endpoint, "m", "v1", "MRA_TEST_KEY", "max_tokens")


class NaturalIdeaRealLeanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = NativeLeanBackend(ROOT, 30)
        cls.gate = EvidenceGate(ROOT, cls.backend, cls.backend.policy)

    def target(self, formula, version=1):
        return NatTarget.create(
            SourceClaim("new-" + uuid.uuid4().hex, version, "测试作者指定的自然数算术命题"),
            formula,
            "作者对齐记录，非模型生成",
        )

    def check(self, target, plan, aligned=True):
        receipt = self.backend.check_nat_plan(target, plan)
        claim, formalization = target.contract(plan["direction"])
        alignment = (
            Alignment(
                claim.fingerprint,
                digest(asdict(formalization)),
                "integration-test-author",
                "Exact displayed Nat formula reviewed",
            )
            if aligned
            else None
        )
        return self.gate.assess(claim, formalization, receipt, alignment), receipt

    def test_new_universal_inequality_is_really_proved_in_two_processes(self):
        (result, evidence), receipt = self.check(
            self.target(FORMULA, 2), {"direction": "prove", "strategy": "omega", "witnesses": []}
        )
        self.assertEqual(result.math_verdict, "PROVED", result.reason)
        self.assertIsNotNone(evidence)
        self.assertNotEqual(receipt.payload["candidate_pid"], receipt.payload["replay"]["pid"])

    def test_false_universal_claim_is_refuted_using_verified_witness(self):
        (result, _), _ = self.check(
            self.target(FALSE_FORMULA),
            {"direction": "refute", "strategy": "counterexample", "witnesses": [2]},
        )
        self.assertEqual(result.math_verdict, "REFUTED", result.reason)

    def test_proof_failure_cannot_be_reported_as_refutation(self):
        (result, evidence), _ = self.check(
            self.target(FALSE_FORMULA), {"direction": "prove", "strategy": "omega", "witnesses": []}
        )
        self.assertEqual(result.math_verdict, "UNKNOWN")
        self.assertIsNone(evidence)

    def test_kernel_success_without_alignment_remains_e3(self):
        (result, _), _ = self.check(
            self.target(FORMULA),
            {"direction": "prove", "strategy": "omega", "witnesses": []},
            False,
        )
        self.assertEqual(result.evidence_level, "E3_KERNEL_CHECKED", result.reason)
        self.assertEqual(result.math_verdict, "UNKNOWN")

    def test_changed_nat_bundle_invalidates_otherwise_valid_receipt(self):
        target = self.target(FORMULA)
        (checked, _), receipt = self.check(
            target, {"direction": "prove", "strategy": "omega", "witnesses": []}
        )
        self.assertEqual(checked.math_verdict, "PROVED", checked.reason)
        path = Path(receipt.payload["artifact_directory"]) / "input/nat-search.json"
        original = path.read_bytes()
        try:
            path.write_bytes(original + b" ")
            claim, formalization = target.contract("prove")
            result, evidence = self.gate.assess(claim, formalization, receipt)
            self.assertEqual(result.math_verdict, "UNKNOWN")
            self.assertIsNone(evidence)
        finally:
            path.write_bytes(original)

    def test_simp_decide_and_truncated_subtraction_use_real_kernel_evidence(self):
        examples = [
            (forall(binary("eq", binary("mul", var(), num(1)), var())), "simp"),
            (binary("eq", binary("add", num(2), num(3)), num(5)), "decide"),
            (forall(binary("eq", binary("sub", var(), var()), num(0))), "omega"),
        ]
        for formula, strategy in examples:
            with self.subTest(strategy=strategy):
                (result, evidence), _ = self.check(
                    self.target(formula),
                    {"direction": "prove", "strategy": strategy, "witnesses": []},
                )
                self.assertEqual(result.math_verdict, "PROVED", result.reason)
                self.assertIsNotNone(evidence)

    def test_revoke_after_real_gate_prevents_publishing_e4(self):
        service = SimulatedService()
        store = IdeaStore(ROOT, service_factory=lambda config: service)
        with patch.object(ModelConfig, "load", return_value=CONFIG):
            idea = store.create(
                {
                    "request_id": "revoke-idea-" + uuid.uuid4().hex,
                    "statement": "对任意自然数 n，n + 1 大于 n。",
                    "context": [],
                }
            )
            ready = store.work_once()["idea"]
            store.confirm(
                idea["idea_id"],
                {
                    "target_fingerprint": ready["candidates"][0]["target_fingerprint"],
                    "source_version": 1,
                    "reviewer": "test-author",
                    "confirmed": True,
                },
            )
            original = EvidenceGate.assess
            observed = []

            def revoke_after_assess(gate, *args, **kwargs):
                result = original(gate, *args, **kwargs)
                observed.append(result[0].math_verdict)
                store.cancel(idea["idea_id"], revoke=True)
                return result

            with patch.object(EvidenceGate, "assess", revoke_after_assess):
                result = store.work_once()["idea"]
            self.assertEqual(observed, ["PROVED"])
            self.assertEqual(result["state"], "CANCELLED")
            self.assertEqual(result["math_verdict"], "UNKNOWN")
            self.assertFalse(store.evidence(idea["idea_id"])["available"])

    def test_full_workflow_repairs_failed_plan_then_refutes_with_real_lean(self):
        service = SimulatedService(
            FALSE_FORMULA,
            [
                {"direction": "prove", "strategy": "omega", "witnesses": []},
                {"direction": "refute", "strategy": "counterexample", "witnesses": [1]},
            ],
        )
        store = IdeaStore(ROOT, service_factory=lambda config: service)
        with patch.object(ModelConfig, "load", return_value=CONFIG):
            idea = store.create(
                {
                    "request_id": "real-idea-" + uuid.uuid4().hex,
                    "statement": "对任意自然数 n，n + 1 = n。",
                    "context": [],
                }
            )
            ready = store.work_once()["idea"]
            store.confirm(
                idea["idea_id"],
                {
                    "target_fingerprint": ready["candidates"][0]["target_fingerprint"],
                    "source_version": 1,
                    "reviewer": "test-author",
                    "confirmed": True,
                },
            )
            result = store.work_once()["idea"]
            self.assertEqual(result["math_verdict"], "REFUTED", result)
            self.assertEqual(len(result["attempts"]), 2)
            self.assertEqual(result["model_calls"], 0)
            self.assertEqual(result["simulation_calls"], 3)
            self.assertTrue(store.evidence(idea["idea_id"])["available"])
            self.assertEqual(store.cancel(idea["idea_id"], revoke=True)["math_verdict"], "UNKNOWN")
            self.assertFalse(store.evidence(idea["idea_id"])["available"])


if __name__ == "__main__":
    unittest.main()
