"""Strict same-origin transport for explicit structure project graphs."""

from urllib.parse import urlsplit

from .formalizer import exact_object
from .structure_strategies import graph_proposals
from .structure_strategies_v2 import graph_proposals as graph_proposals_v2
from .structure_strategies_v3 import graph_proposals as graph_proposals_v3


def dispatch(handler, write):
    parsed = urlsplit(handler.path)
    parts = parsed.path.split("/")
    if parts[:3] != ["", "v1", "structure-research"]:
        return False
    if parsed.query:
        raise ValueError("Typed research routes do not accept query parameters")
    store = handler.server.structure_research
    result, status = None, 200
    if write and len(parts) == 4 and parts[3] == "preview":
        result = store.preview(handler.body())
    elif write and len(parts) == 3:
        result, status = store.create(handler.body()), 201
    elif not write and len(parts) == 6 and parts[3] == "projects":
        result = store.catalog(parts[4], parts[5])
    elif not write and (len(parts) == 4 or len(parts) == 5 and parts[4] == "export"):
        result = store.report(parts[3])
    elif write and len(parts) == 5:
        key, action = parts[3:]
        if action in ("strategy-proposals", "strategy-proposals-v2", "strategy-proposals-v3"):
            raw = handler.body()
            exact_object(raw, {"node_id", "budget"}, "Structure node strategy proposal")
            provider = {
                "strategy-proposals": graph_proposals,
                "strategy-proposals-v2": graph_proposals_v2,
                "strategy-proposals-v3": graph_proposals_v3,
            }[action]
            result = provider(store, {"graph_id": key, **raw})
        elif action == "node-preview":
            result = store.preview_node(key, handler.body())
        elif action == "submit":
            result, status = store.submit(key, handler.body()), 202
            if handler.server.pump:
                handler.server.pump.wake.set()
        elif action in ("cancel", "withdraw"):
            result = getattr(store, action)(key, handler.body())
    if result is None:
        return False
    handler.reply(status, result)
    return True
