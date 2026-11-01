"""Read-only research actions in a cancellable child. No proof submission or network tool."""

from pathlib import Path
import sys

from .domain import canonical
from .formalizer import strict_json
from .research_tools import perform


def main():
    payload = strict_json(sys.stdin.buffer.read(1048577).decode())
    root = Path(payload["root"]).resolve()
    context = payload["context"]
    seen = {tuple(x) for x in payload["seen_queries"]}

    def preview(formal):
        from .research_studio import backend_store

        request = {"project_id": context["project_id"], "branch_id": "main", **formal["request"]}
        return backend_store(root, formal["backend"]).preview(request)

    result = perform(root, payload["request"], context, preview, seen)
    reply = canonical({"result": result, "seen_queries": sorted(seen)})
    if len(reply) > 1048576:
        reply = canonical(
            {
                "result": {
                    "state": "DIAGNOSTIC",
                    "math_verdict": "UNKNOWN",
                    "evidence_level": "E0",
                    "new_lean_checks": 0,
                    "error": "Complete tool result exceeds output budget; no conditions were truncated",
                },
                "seen_queries": sorted(seen),
            }
        )
    sys.stdout.buffer.write(reply)


if __name__ == "__main__":
    main()
