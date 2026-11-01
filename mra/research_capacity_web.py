"""Authenticated organization routes; they never execute mathematical checks."""

from urllib.parse import urlsplit


def dispatch(handler, write):
    parsed = urlsplit(handler.path)
    parts = parsed.path.split("/")
    if parts[:3] != ["", "v1", "research-capacity"]:
        return False
    if parsed.query:
        raise ValueError("Project capacity routes take no query parameters")
    store = handler.server.research
    if len(parts) == 3 and not write:
        handler.reply(200, store.capacity_catalog())
    elif len(parts) == 5 and write and parts[4] in ("archive", "restore"):
        handler.reply(200, store.change_lifecycle(parts[3], parts[4], handler.body()))
    else:
        return False
    return True
