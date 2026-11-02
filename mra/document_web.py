"""Same-origin intake routes; source extraction never invokes a verifier."""

from urllib.parse import urlsplit


def dispatch(handler, write):
    parsed = urlsplit(handler.path)
    parts = parsed.path.split("/")
    if parts[:3] != ["", "v1", "documents"]:
        return False
    if parsed.query:
        raise ValueError("Document routes do not accept query parameters")
    store = handler.server.documents
    if len(parts) == 4 and parts[3] == "capabilities" and not write:
        runtime = store.runtime_root / ".runtime/document-ingestion-v1"
        handler.reply(
            200,
            {
                "pdf": (runtime / "python/bin/python").is_file(),
                "ocr": (runtime / "ocr/usr/bin/tesseract").is_file()
                and (
                    runtime / "ocr/usr/share/tesseract-ocr/4.00/tessdata/eng.traineddata"
                ).is_file(),
                "ocr_language": "English",
                "remote_uploads": False,
            },
        )
        return True
    if len(parts) == 3:
        if write:
            value = store.create(handler.body(maximum=24 * 1024 * 1024))
            if handler.server.document_pump:
                handler.server.document_pump.wake.set()
            handler.reply(202, value)
        else:
            handler.reply(200, {"documents": store.listing()})
        return True
    if len(parts) == 4 and not write:
        handler.reply(200, store.report(parts[3]))
        return True
    if len(parts) == 5:
        key, action = parts[3:]
        if not write and action == "draft":
            handler.reply(200, store.draft(key))
        elif not write and action == "original-pdf":
            _, inputs = store.checked_inputs(key)
            if not inputs["pdf"]:
                raise ValueError("This import has no PDF")
            handler.reply(
                200,
                (store.directory(key) / "document.pdf").read_bytes(),
                "application/octet-stream",
            )
        elif write and action == "cancel":
            if handler.body() != {}:
                raise ValueError("Cancellation takes an empty object")
            handler.reply(200, store.cancel(key))
        elif write and action == "review":
            handler.reply(200, store.review(key, handler.body()))
        elif write and action == "article":
            handler.reply(201, store.publish(key, handler.body(), handler.server.article_audits))
        else:
            return False
        return True
    if len(parts) == 6 and not write and parts[4] in ("pages", "sources"):
        number = parts[5]
        if not number.isascii() or not number.isdigit() or len(number) > 3:
            raise ValueError("Use a bounded numeric page/source index")
        if parts[4] == "pages":
            handler.reply(200, store.image(parts[3], int(number)), "image/png")
        else:
            handler.reply(200, store.source(parts[3], int(number)))
        return True
    return False
