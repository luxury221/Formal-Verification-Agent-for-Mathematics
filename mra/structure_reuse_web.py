"""Same-origin structure reuse; shared descriptor projection never exposes proof data."""

from urllib.parse import urlsplit

from .formalizer import exact_object
from .structure_lemma_suggestions import SCHEMA, suggest
from .typed_reuse_web import catalog, source_detail


def dispatch(handler, write):
    parsed = urlsplit(handler.path)
    parts = parsed.path.split("/")
    if parts[:3] != ["", "v1", "structure-reuse"]:
        return False
    if parsed.query:
        raise ValueError("Structure reuse routes do not accept query parameters")
    store = handler.server.structure_reuse
    result, status = None, 200
    if not write and len(parts) == 7 and parts[3] == "graphs" and parts[5] == "nodes":
        result = catalog(
            store,
            parts[4],
            parts[6],
            node_origin="structure-graph-node",
            run_origin="structure-composition-run",
        )
    elif (
        write
        and len(parts) == 8
        and parts[3] == "graphs"
        and parts[5] == "nodes"
        and parts[7] == "suggestions"
    ):
        body = handler.body()
        exact_object(body, {"budget", "timeout_seconds"}, "Structure lemma suggestion request")
        result = suggest(
            store, {"schema": SCHEMA, "graph_id": parts[4], "node_id": parts[6], **body}
        )
    elif not write and len(parts) == 5 and parts[3] == "sources":
        result = source_detail(store, parts[4])
    elif write and parts[3:] == ["preview"]:
        result = store.preview(handler.body())
    elif write and parts[3:] == ["runs"]:
        result, status = store.submit(handler.body()), 202
        if handler.server.pump:
            handler.server.pump.wake.set()
    elif len(parts) in (5, 6) and parts[3] == "runs":
        key = parts[4]
        if not write and (len(parts) == 5 or parts[5] == "export"):
            result = store.report(key)
        elif write and len(parts) == 6:
            if parts[5] == "cancel":
                exact_object(handler.body(), set(), "Cancel structure composition")
                result = store.cancel(key)
            elif parts[5] == "revoke":
                result = store.revoke(key, handler.body())
    if result is None:
        return False
    handler.reply(status, result)
    return True
