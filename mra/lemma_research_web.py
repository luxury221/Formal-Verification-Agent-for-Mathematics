"""Same-origin transport for the existing complete recursion/export/application stores."""

from urllib.parse import urlsplit

from .export_lemma_matching import demo_target, suggest
from .formalizer import exact_object
from .function_dynamics_checks import verified
from .lemma_composition import LemmaCompositionStore
from .lemma_research_examples import example
from .obligation_planning import propose
from .structure_lemma_library import identity, temporary_busy

KINDS = ("programs", "exports", "applications")


def report(store, kind, key):
    row = (
        temporary_busy(lambda: LemmaCompositionStore.report(store, key))
        if kind == "programs"
        else store.report(key)
    )
    row = {
        **row,
        "mode": "structure-recursion-program-v1" if kind == "programs" else store.mode,
        "fresh_attestation": False,
        "source_fingerprint": None,
    }
    if row["math_verdict"] == "PROVED":
        verified(row)
        row["source_fingerprint"] = identity(row)
    return row


def catalog(store, kind):
    with store.transaction(False) as db:
        ids = [r[0] for r in db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 200")]
    return {
        "runs": [
            {
                k: row[k]
                for k in (
                    "run_id",
                    "request_id",
                    "state",
                    "math_verdict",
                    "evidence_level",
                    "invalidated",
                    "created_at",
                )
            }
            | {"title": row["plan"]["target"]["title"]}
            for row in (report(store, kind, key) for key in ids)
        ]
    }


def dispatch(handler, write):
    parsed = urlsplit(handler.path)
    parts = parsed.path.split("/")
    if parts[:3] != ["", "v1", "lemma-research"]:
        return False
    if parsed.query:
        raise ValueError("Lemma research routes do not accept query parameters")
    result, status = None, 200
    if not write and parts[3:] == ["example"]:
        result = {
            "target": example(),
            "provenance": "Project-authored demonstration; not model output or a blind task",
            "lean_calls": 0,
        }
    elif not write and parts[3:] == ["matching-example"]:
        result = demo_target(handler.server.export_applications)
    elif write and parts[3:] == ["match"]:
        result = suggest(handler.server.export_applications, handler.body())
    elif write and parts[3:] == ["propose"]:
        result = propose(handler.server.root, handler.body())
    elif len(parts) >= 4 and parts[3] in KINDS:
        kind = parts[3]
        store = getattr(
            handler.server,
            {
                "programs": "recursion",
                "exports": "lemma_exports",
                "applications": "export_applications",
            }[kind],
        )
        if not write and len(parts) == 4:
            result = catalog(store, kind)
        elif write and parts[4:] == ["preview"]:
            result = store.preview(handler.body())
        elif write and parts[4:] == ["runs"]:
            result, status = store.submit(handler.body()), 202
            if handler.server.pump:
                handler.server.pump.wake.set()
        elif not write and len(parts) == 6 and parts[5] == "source" and kind == "exports":
            result, _ = handler.server.export_applications.load_source(
                {"alias": "core", "item_id": "export." + parts[4]}
            )
        elif len(parts) in (5, 6):
            key = parts[4]
            if not write and (len(parts) == 5 or parts[5] == "export"):
                result = report(store, kind, key)
            elif write and len(parts) == 6:
                if parts[5] == "cancel":
                    exact_object(handler.body(), set(), "Cancel complete lemma task")
                    result = store.cancel(key)
                elif parts[5] == "revoke":
                    result = store.revoke(key, handler.body())
    if result is None:
        return False
    handler.reply(status, result)
    return True
