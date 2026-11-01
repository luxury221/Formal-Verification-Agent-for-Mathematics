"""Same-origin typed reuse transport; source descriptors never carry proof data."""

from urllib.parse import urlsplit

from .formalizer import exact_object
from .tasks import _identifier


def source_detail(store, item_id):
    _identifier(item_id)
    source, _ = store.load_source({"alias": "source", "item_id": item_id})
    return {
        k: source[k]
        for k in (
            "item_id",
            "origin",
            "graph_id",
            "node_id",
            "context_fingerprint",
            "source_fingerprint",
            "formal_statement",
            "request",
            "alignment",
            "depth",
            "ancestors",
        )
    } | {"math_verdict": "PROVED", "evidence_level": "E4", "fresh_attestation": False}


def catalog(
    store, graph_id, node_id, *, node_origin="typed-graph-node", run_origin="typed-composition-run"
):
    value = store.catalog(graph_id, node_id)
    with store.transaction(False) as db:
        rows = [
            store.decode(r)
            for r in db.execute("SELECT body,sha256 FROM runs ORDER BY rowid DESC LIMIT 200")
        ]
    by_id = {"run." + r["run_id"]: r for r in rows}
    candidates = []
    for key in value["candidate_ids"]:
        if key.startswith("node."):
            candidates.append(
                {"item_id": key, "node_id": key.split(".", 2)[2], "origin": node_origin}
            )
        else:
            r = by_id[key]
            candidates.append(
                {
                    "item_id": key,
                    "node_id": r["plan"]["request"]["node_id"],
                    "origin": run_origin,
                    "stored_state": r["state"],
                    "stored_invalidated": r["invalidated"],
                }
            )
    matching = [
        r
        for r in rows
        if r["plan"]["request"]["graph_id"] == graph_id
        and r["plan"]["request"]["node_id"] == node_id
    ]
    history = []
    for recorded in matching[:20]:
        r = store.report(recorded["run_id"])
        result = r["result"] or {}
        history.append(
            {
                **{
                    k: r[k]
                    for k in (
                        "run_id",
                        "state",
                        "stage",
                        "math_verdict",
                        "evidence_level",
                        "invalidated",
                        "created_at",
                        "artifact_status",
                    )
                },
                "candidate_pid": (result.get("candidate") or {}).get("pid"),
                "replay_pid": (result.get("replay") or {}).get("pid"),
            }
        )
    return {
        **value,
        "source_candidates": candidates,
        "history": history,
        "matching_runs": len(matching),
        "history_limit": 20,
    }


def dispatch(handler, write):
    parsed = urlsplit(handler.path)
    parts = parsed.path.split("/")
    if parts[:3] != ["", "v1", "typed-reuse"]:
        return False
    if parsed.query:
        raise ValueError("Typed reuse routes do not accept query parameters")
    store = handler.server.typed_reuse
    result, status = None, 200
    if not write and len(parts) == 7 and parts[3] == "graphs" and parts[5] == "nodes":
        result = catalog(store, parts[4], parts[6])
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
                exact_object(handler.body(), set(), "Cancel typed composition")
                result = store.cancel(key)
            elif parts[5] == "revoke":
                result = store.revoke(key, handler.body())
    if result is None:
        return False
    handler.reply(status, result)
    return True
