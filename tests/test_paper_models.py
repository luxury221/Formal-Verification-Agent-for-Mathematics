"""Actual isolated transport against owned loopback providers, no paid/model-quality runs."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

from mra.chat_model import ModelConfig
from mra.model_service import ModelBudget
from mra.paper_models import PaperModelRequest, PaperModelService
from paper_fixtures import CONFIG

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def provider(*, delay=0, redirect=False, truncated=False):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            self.server.received.append(json.loads(body))
            if redirect:
                self.send_response(302)
                self.send_header("Location", "/other")
                self.end_headers()
                return
            time.sleep(delay)
            raw = json.dumps(
                {
                    "model": "owned-loopback-provider",
                    "choices": [
                        {
                            "finish_reason": "length" if truncated else "stop",
                            "message": {"content": '{"secret":"paper-test-secret"}'},
                        }
                    ],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 8},
                }
            ).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.received = []
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.03}, daemon=True
    )
    thread.start()
    try:
        config = ModelConfig(
            **{**CONFIG, "endpoint": f"http://127.0.0.1:{server.server_port}/chat"}
        )
        yield server, config
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


class PaperTransportTests(unittest.TestCase):
    def request(self, timeout=3):
        return PaperModelRequest(
            "transport",
            "paper_scan",
            '{"source":"owned fixture"}',
            ModelBudget(timeout_seconds=timeout),
        )

    def test_actual_child_transport_usage_prompt_and_secret_redaction(self):
        with (
            provider() as (server, config),
            patch.dict("os.environ", {config.api_key_env: "paper-test-secret"}),
        ):
            response = PaperModelService(ROOT, config).generate(self.request())
            self.assertEqual(response.usage.input_tokens, 12)
            self.assertNotIn("paper-test-secret", response.output_json)
            self.assertIn("REDACTED", response.output_json)
            self.assertEqual(len(server.received), 1)
            self.assertIn("EVERY focus", server.received[0]["messages"][0]["content"])
            self.assertEqual(response.identity.kind, "model")

    def test_redirect_and_incomplete_output_rejected_without_retry(self):
        for changes in ({"redirect": True}, {"truncated": True}):
            with provider(**changes) as (server, config):
                with self.assertRaises(OSError):
                    PaperModelService(ROOT, config).generate(self.request())
                self.assertEqual(len(server.received), 1)

    def test_cancellation_interrupts_slow_provider_and_reaps_owned_child(self):
        with provider(delay=2) as (_, config):
            cancel = threading.Event()
            timer = threading.Timer(0.2, cancel.set)
            timer.start()
            try:
                start = time.monotonic()
                with self.assertRaises(InterruptedError):
                    PaperModelService(ROOT, config, cancel_requested=cancel.is_set).generate(
                        self.request()
                    )
                self.assertLess(time.monotonic() - start, 1.5)
            finally:
                timer.cancel()

    def test_timeout_and_request_input_boundaries(self):
        with provider(delay=2) as (_, config):
            with self.assertRaises(TimeoutError):
                PaperModelService(ROOT, config).generate(self.request(0.2))
        with self.assertRaises(ValueError):
            PaperModelRequest("bad", "paper_scan", "x" * 5, ModelBudget(max_input_chars=4))
