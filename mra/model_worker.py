"""Network-only model child. Secrets enter on stdin and never appear in errors."""

import math
import signal
import sys
import urllib.error
import urllib.request

from .chat_model import ModelConfig, request_payload
from .domain import canonical
from .formalizer import strict_json
from .model_service import ModelBudget, ModelRequest


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise OSError("Redirects are disabled")


def main():
    try:
        data = strict_json(sys.stdin.buffer.read(1100001).decode())
        config = ModelConfig(**data["config"])
        fields = data["request"]
        fields["budget"] = ModelBudget(**fields["budget"])
        request = ModelRequest(**fields)
        signal.signal(signal.SIGALRM, lambda *_: sys.exit(124))
        signal.alarm(max(1, math.ceil(request.budget.timeout_seconds) + 1))
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if data["api_key"]:
            headers["Authorization"] = "Bearer " + data["api_key"]
        req = urllib.request.Request(
            config.endpoint,
            data=canonical(request_payload(request, config)),
            headers=headers,
            method="POST",
        )
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(req, timeout=request.budget.timeout_seconds) as response:
            raw = response.read(1048577)
            if response.status != 200 or len(raw) > 1048576:
                raise ValueError("Invalid provider envelope")
        envelope = strict_json(raw.decode())
        if len(envelope["choices"]) != 1 or envelope["choices"][0]["finish_reason"] != "stop":
            raise ValueError("Incomplete provider response")
        choice = envelope["choices"][0]["message"]
        if (
            choice.get("tool_calls")
            or choice.get("refusal")
            or not isinstance(choice.get("content"), str)
        ):
            raise ValueError("Expected complete JSON text")
        result = {
            "model": envelope["model"],
            "output_json": choice["content"],
            "usage": {
                "input_tokens": envelope["usage"]["prompt_tokens"],
                "output_tokens": envelope["usage"]["completion_tokens"],
            },
        }
    except TimeoutError:
        result = {"error": "TIMEOUT"}
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        result = {"error": "TRANSPORT_OR_INVALID_RESPONSE"}
    sys.stdout.buffer.write(canonical(result))


if __name__ == "__main__":
    main()
