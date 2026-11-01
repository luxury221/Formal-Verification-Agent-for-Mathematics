"""Safe descriptors for private proof-library pages; no raw archived proof data."""

from .tasks import _identifier


def source_detail(store, item_id):
    _identifier(item_id)
    source, _ = store.load_source({"alias": "source", "item_id": item_id})
    return {
        "item_id": item_id,
        "version": source["version"],
        "archive_fingerprint": source["archive_fingerprint"],
        "source_fingerprint": source["source_fingerprint"],
        "formal_statement": source["formal_statement"],
        "origin": source["origin"],
        "signature": {
            k: source["request"][k]
            for k in ("title", "original", "variables", "assumptions", "goal", "timeout_seconds")
        },
        "alignment": source["alignment"],
        "depth": source["depth"],
        "ancestors": source["ancestors"],
        "href": "/proof-library#" + item_id
        if item_id.startswith("proof-knowledge-")
        else "/library#" + item_id,
        "fresh_attestation": False,
    }
