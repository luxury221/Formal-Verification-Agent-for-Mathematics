"""Explicit local import, annotation and evidence-reading routes for articles."""

from urllib.parse import urlsplit
from .article_research_returns import ArticleResearchReturns as ArticleWorkflow


def dispatch(handler, write):
    parsed = urlsplit(handler.path)
    parts = parsed.path.split("/")
    if parts[:3] != ["", "v1", "article-audits"]:
        return False
    if parsed.query:
        raise ValueError("Article routes do not accept query parameters")
    store = handler.server.article_audits
    workflow = ArticleWorkflow(store, studio=handler.server.studio)
    if len(parts) == 3:
        if write:
            result = store.create(handler.body(maximum=2097152))
        else:
            result = {"articles": store.listing()}
    elif len(parts) == 4 and not write:
        result = workflow.report(parts[3])
    elif len(parts) == 6 and parts[4] == "research-tasks" and not write:
        result = workflow.research_tasks(parts[3], parts[5])
    elif len(parts) == 5 and write:
        if parts[4] == "annotate":
            result = store.annotate(parts[3], handler.body())
        elif parts[4] == "annotate-many":
            result = store.annotate_many(parts[3], handler.body())
        elif parts[4] == "research":
            result = workflow.create_workspace(parts[3], handler.body())
        elif parts[4] == "research-preview":
            result = workflow.evidence_preview(parts[3], handler.body())
        elif parts[4] == "research-bind":
            result = workflow.bind_workspace(parts[3], handler.body())
        elif parts[4] == "bind":
            result = store.bind(parts[3], handler.body())
        elif parts[4] == "recheck":
            if handler.body() != {}:
                raise ValueError("Evidence recheck takes an empty object")
            result = workflow.report(parts[3], recheck=True)
        else:
            return False
    else:
        return False
    if (
        isinstance(result, dict)
        and result.get("schema") == "article-reasoning-audit-v1"
        and "workflow" not in result
    ):
        result = workflow.enrich(result)
    handler.reply(201 if write and len(parts) == 3 else 200, result)
    return True
