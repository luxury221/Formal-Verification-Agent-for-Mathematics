"""Read-only web descriptors for the frozen private composition protocol."""

from .lemma_composition import load_source
from .number_theory_terms import REFERENCES
from .tasks import _identifier


def catalog(store):
    return {
        **store.catalog(),
        "references": {
            name: {"terms": n, "premises": p, "formula": formula, "title": title}
            for name, (n, p, formula, title, _) in REFERENCES.items()
        },
    }


def source_detail(store, item_id):
    _identifier(item_id)
    source, _ = load_source(store.knowledge, {"alias": "source", "item_id": item_id})
    # Never transport archived candidate source, proof expressions or raw NDJSON.
    signature = source["request"]
    return {
        "item_id": item_id,
        "version": source["version"],
        "archive_fingerprint": source["archive_fingerprint"],
        "source_fingerprint": source["source_fingerprint"],
        "formal_statement": source["formal_statement"],
        "signature": {
            key: signature[key] for key in ("title", "original", "variables", "assumptions", "goal")
        },
        "alignment": source["alignment"],
        "href": "/library#" + item_id,
        "fresh_attestation": False,
    }
