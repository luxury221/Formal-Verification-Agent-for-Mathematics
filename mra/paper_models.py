"""Independent paper-model adapter; existing sealed model protocols stay unchanged."""

from dataclasses import asdict, dataclass
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit
import urllib.request

from .chat_model import ModelConfig
from .domain import canonical, digest
from .formalizer import exact_object, strict_json
from .model_service import ModelBudget, ModelIdentity, ModelResponse, TokenUsage, text_field
from .model_worker import NoRedirect
from .paper_protocol import MAX_INPUT, PROMPTS


@dataclass(frozen=True)
class PaperModelRequest:
    id: str
    task: str
    input_json: str
    budget: ModelBudget

    def __post_init__(self):
        text_field(self.id, "Paper model request ID", 128)
        if self.task not in PROMPTS or not isinstance(self.budget, ModelBudget):
            raise ValueError("Unsupported paper model task or budget")
        text_field(self.input_json, "Complete paper model input", MAX_INPUT)
        if len(self.input_json) > self.budget.max_input_chars:
            raise ValueError("Complete source context exceeds the model input budget")

    @property
    def fingerprint(self):
        return digest(asdict(self))


class PaperModelService:
    def __init__(self, root, config, *, cancel_requested=None, lifecycle_lock_fd=None):
        self.root, self.config = Path(root), config
        self.cancel_requested = cancel_requested or (lambda: False)
        self.lifecycle_lock_fd = lifecycle_lock_fd

    def generate(self, request):
        if not isinstance(request, PaperModelRequest):
            raise ValueError("Use the bounded whole-paper request contract")
        key = os.environ.get(self.config.api_key_env, "")
        if "\r" in key or "\n" in key:
            raise ValueError("Invalid model credential header")
        payload = {"config": asdict(self.config), "api_key": key, "request": asdict(request)}
        if self.cancel_requested():
            raise InterruptedError("Paper model call cancelled")
        process = subprocess.Popen(
            [sys.executable, "-I", str(Path(__file__).with_name("paper_model_worker.py"))],
            cwd=self.root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            pass_fds=() if self.lifecycle_lock_fd is None else (self.lifecycle_lock_fd,),
        )
        started, outgoing = time.monotonic(), canonical(payload)
        try:
            while True:
                if self.cancel_requested():
                    raise InterruptedError("Paper model call cancelled")
                remaining = request.budget.timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("Paper model deadline exceeded")
                try:
                    raw, _ = process.communicate(input=outgoing, timeout=min(0.1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    outgoing = None
            if process.returncode or len(raw) > 1100000:
                raise OSError("Paper model transport failed; check local configuration")
            result = strict_json(raw.decode("utf-8"))
            if result.get("error"):
                raise OSError("Paper model provider failed or returned an incomplete response")
            exact_object(result, {"model", "output_json", "usage"}, "Paper provider result")
            usage = TokenUsage(**result["usage"])
            output = result["output_json"]
            if (
                not isinstance(output, str)
                or len(output.encode()) > request.budget.max_output_bytes
                or usage.output_tokens > request.budget.max_output_tokens
            ):
                raise ValueError("Paper model output exceeds its shared budget")
            if key:
                output = output.replace(key, "[REDACTED]")
            identity = ModelIdentity(
                urlsplit(self.config.endpoint).hostname,
                result["model"],
                self.config.revision,
                "model",
            )
            return ModelResponse(request.fingerprint, identity, output, usage)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()


def worker_main():
    """Network-only fixed worker. API keys enter stdin, never argv or diagnostics."""
    try:
        raw = sys.stdin.buffer.read(1100001)
        if len(raw) > 1100000:
            raise ValueError("Oversized model transport")
        data = strict_json(raw.decode())
        exact_object(data, {"config", "api_key", "request"}, "Paper model transport")
        config = ModelConfig(**data["config"])
        fields = data["request"]
        fields["budget"] = ModelBudget(**fields["budget"])
        request = PaperModelRequest(**fields)
        signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
        signal.alarm(max(1, math.ceil(request.budget.timeout_seconds) + 1))
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        key = data["api_key"]
        if not isinstance(key, str) or "\r" in key or "\n" in key:
            raise ValueError("Invalid credential")
        if key:
            headers["Authorization"] = "Bearer " + key
        body = {
            "model": config.model,
            "messages": [
                {"role": "system", "content": PROMPTS[request.task]},
                {"role": "user", "content": request.input_json},
            ],
            config.token_field: request.budget.max_output_tokens,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            config.endpoint, data=canonical(body), headers=headers, method="POST"
        )
        with urllib.request.build_opener(NoRedirect()).open(
            req, timeout=request.budget.timeout_seconds
        ) as response:
            raw = response.read(1048577)
            if response.status != 200 or len(raw) > 1048576:
                raise ValueError("Invalid provider envelope")
        envelope = strict_json(raw.decode())
        if len(envelope["choices"]) != 1 or envelope["choices"][0]["finish_reason"] != "stop":
            raise ValueError("Incomplete provider response")
        message = envelope["choices"][0]["message"]
        if (
            message.get("tool_calls")
            or message.get("refusal")
            or not isinstance(message.get("content"), str)
        ):
            raise ValueError("Expected complete JSON text")
        result = {
            "model": envelope["model"],
            "output_json": message["content"],
            "usage": {
                "input_tokens": envelope["usage"]["prompt_tokens"],
                "output_tokens": envelope["usage"]["completion_tokens"],
            },
        }
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        result = {"error": "TRANSPORT_OR_INVALID_RESPONSE"}
    sys.stdout.buffer.write(canonical(result))
