"""Strict loopback routes for reviewed graph campaigns."""

from urllib.parse import parse_qs, urlsplit

from .formalizer import exact_object


def dispatch(handler, write):
    url = urlsplit(handler.path)
    parts = url.path.split("/")
    if parts[:3] != ["", "v1", "research-campaigns"]:
        return False
    store = handler.server.campaigns
    if not write and url.path in ("/v1/research-campaigns/catalog", "/v1/research-campaigns/runs"):
        query = parse_qs(url.query, keep_blank_values=True)
        if set(query) - {"flow_id"} or any(len(v) != 1 for v in query.values()):
            raise ValueError("Use one explicit flow_id query")
        flow_id = query.get("flow_id", [None])[0]
        if flow_id is not None:
            store.flows.directory(flow_id)
        value = (
            store.catalog(flow_id) if parts[-1] == "catalog" else {"runs": store.list_runs(flow_id)}
        )
        handler.reply(200, value)
        return True
    if url.query:
        raise ValueError("Campaign route does not accept query parameters")
    if write and url.path == "/v1/research-campaigns/preview":
        handler.reply(200, store.preview(handler.body()))
        return True
    if write and url.path == "/v1/research-campaigns/runs":
        value = store.submit(handler.body())
        if handler.server.pump:
            handler.server.pump.wake.set()
        handler.reply(201, value)
        return True
    if len(parts) in (5, 6) and parts[3] == "runs":
        key = parts[4]
        if not write and (len(parts) == 5 or parts[5] == "export"):
            handler.reply(200, store.report(key))
            return True
        if write and len(parts) == 6 and parts[5] in ("cancel", "withdraw"):
            raw = handler.body()
            if parts[5] == "cancel":
                exact_object(raw, set(), "Campaign cancellation")
                value = store.cancel(key)
            else:
                value = store.withdraw(key, raw)
            handler.reply(200, value)
            return True
    return False
