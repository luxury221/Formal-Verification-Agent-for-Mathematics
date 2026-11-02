"""Source/obligation routes share the workbench's existing local write guards."""

from urllib.parse import urlsplit


def dispatch(handler, write):
    parsed = urlsplit(handler.path)
    parts = parsed.path.split("/")
    if parts[:3] != ["", "v1", "obligations"]:
        return False
    if parsed.query:
        raise ValueError("Obligation routes do not accept query parameters")
    store = handler.server.obligations
    if len(parts) == 3:
        if write:
            handler.reply(201, store.create(handler.body()))
        else:
            handler.reply(200, {"dossiers": store.listing()})
        return True
    if len(parts) == 4 and not write:
        handler.reply(200, store.catalog() if parts[3] == "catalog" else store.report(parts[3]))
        return True
    if len(parts) == 5 and write:
        actions = {
            "draft-plan": store.draft_plan,
            "draft-preview": store.draft_preview,
            "import-drafts": store.import_drafts,
            "save": store.save,
            "source-review": store.extraction_review,
            "review-preview": store.review_preview,
            "review": store.review,
            "research": store.create_workspace,
            "research-tasks": store.research_tasks,
            "evidence-preview": store.evidence_preview,
            "bind": store.bind,
            "recheck": store.recheck,
        }
        action = actions.get(parts[4])
        if action:
            handler.reply(200, action(parts[3], handler.body()))
            return True
    return False
