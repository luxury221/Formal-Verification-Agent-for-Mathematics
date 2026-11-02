"""Automatic paper routes inherit the shared Host/Origin/CSRF/strict JSON guards."""

from urllib.parse import unquote, urlsplit


def dispatch(handler, write):
    parsed = urlsplit(handler.path)
    parts = parsed.path.split("/")
    if parts[:3] != ["", "v1", "paper-automation"]:
        return False
    if parsed.query:
        raise ValueError("Paper automation routes do not accept query parameters")
    store = handler.server.paper_automation
    if len(parts) == 4 and parts[3] == "capabilities" and not write:
        value = store.capabilities()
        value["worker"] = (
            handler.server.paper_pump.snapshot()
            if handler.server.paper_pump
            else {"state": "EXTERNAL", "error": None}
        )
        handler.reply(200, value)
    elif len(parts) == 3:
        if write:
            result = store.create(handler.body())
            if handler.server.paper_pump:
                handler.server.paper_pump.wake.set()
            handler.reply(202, result)
        else:
            handler.reply(200, {"jobs": store.listing()})
    elif len(parts) == 4 and not write:
        handler.reply(200, store.report(parts[3]))
    elif len(parts) == 5 and parts[4] == "export" and not write:
        handler.reply(200, store.report(parts[3]))
    elif len(parts) == 6 and parts[4] == "artifacts" and not write:
        name = unquote(parts[5], errors="strict")
        handler.reply(200, store.artifact(store.read(parts[3]), name))
    elif len(parts) == 5 and parts[4] in ("cancel", "resume") and write:
        result = store.action(parts[3], parts[4], handler.body())
        if handler.server.paper_pump:
            handler.server.paper_pump.wake.set()
        handler.reply(200, result)
    else:
        return False
    return True
