"""Same-origin cross-domain notebook and explicit verifier handoff routes."""

from urllib.parse import urlsplit
from . import studio_premises, research_tools, premise_application


def dispatch(handler, write):
    parsed = urlsplit(handler.path)
    parts = parsed.path.split("/")
    if parts[:3] != ["", "v1", "studio"]:
        return False
    if parsed.query:
        raise ValueError("Research notebook routes do not accept query parameters")
    store = handler.server.studio
    result, status = None, 200
    if not write and parts == ["", "v1", "studio", "catalog"]:
        result = store.catalog()
    elif len(parts) == 3:
        if write:
            result, status = store.create(handler.body()), 201
        else:
            result = {"studies": store.listing()}
    elif len(parts) == 4 and not write:
        result = store.report(parts[3])
    elif len(parts) == 6 and parts[4] == "trace" and not write:
        result = research_tools.trace(store, parts[3], parts[5])
    elif len(parts) == 5 and write:
        methods = {
            "plan": "plan",
            "scaffold": "scaffold",
            "round": "add_manual_round",
            "generate": "generate",
            "cancel": "cancel",
            "recover": "recover",
            "transfer-preview": "preview_transfer",
            "transfer": "transfer",
            "evidence": "evidence",
            "feedback": "feedback",
        }
        if parts[4] == "application-preview":
            result = premise_application.start(store, parts[3], handler.body())
        elif parts[4] in (
            "premise-search",
            "attach-premise",
            "withdraw-premise",
            "inspect-premise",
        ):
            method = {
                "premise-search": "search",
                "attach-premise": "attach",
                "withdraw-premise": "withdraw",
                "inspect-premise": "inspect",
            }[parts[4]]
            result = getattr(studio_premises, method)(store, parts[3], handler.body())
        elif parts[4] in methods:
            result = getattr(store, methods[parts[4]])(parts[3], handler.body())
    if result is None:
        return False
    handler.reply(status, result)
    return True
