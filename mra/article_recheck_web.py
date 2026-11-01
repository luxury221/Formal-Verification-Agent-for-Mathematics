"""Same-origin transport for historical article evidence recheck jobs."""

from urllib.parse import urlsplit


def dispatch(handler, write):
    parsed = urlsplit(handler.path)
    parts = parsed.path.split("/")
    if parts[:3] != ["", "v1", "article-audits"] or len(parts) < 5 or parts[4] != "rechecks":
        return False
    if parsed.query:
        raise ValueError("Article recheck routes do not accept query parameters")
    store, article = handler.server.article_rechecks, parts[3]
    status = 200
    if len(parts) == 5:
        if write:
            result = store.create(article, handler.body())
            status = 202
            if handler.server.article_recheck_pump:
                handler.server.article_recheck_pump.wake.set()
        else:
            result = store.listing(article)
    elif len(parts) == 6 and not write:
        result = store.report(article, parts[5])
    elif len(parts) == 7 and parts[6] == "result" and not write:
        result = store.result(article, parts[5])
    elif len(parts) == 7 and parts[6] == "cancel" and write:
        result = store.cancel(article, parts[5], handler.body())
        if handler.server.article_recheck_pump:
            handler.server.article_recheck_pump.wake.set()
    else:
        return False
    handler.reply(status, result)
    return True
