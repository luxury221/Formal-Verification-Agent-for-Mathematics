"""Local HTML anchors and links are source locations, never proof dependencies."""

from collections import defaultdict
import hashlib
from pathlib import Path
import posixpath
import re
from urllib.parse import unquote, urljoin, urlsplit

from .article_source import IndexParser

SCHEMA = "local-html-references-v1"
IMPLEMENTATION = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
MAX_REFERENCES = 4096
MAX_LABELS = 8192
IGNORED = {"script", "style", "nav", "noscript", "template"}
CONTAINERS = {"article", "section", "html", "body", "head"}


def target_labels(item):
    """New HTML uses actual, unique DOM IDs; old display anchors stay compatible."""
    labels = item.get("labels", [])
    if item.get("html_reference_schema") != SCHEMA and item.get("label"):
        labels = labels + [item["label"]]
    return set(labels)


class ReferenceParser(IndexParser):
    def __init__(self, source):
        super().__init__(source)
        self.ambiguous_ids = set()
        self.ambiguous_links = set()

    def handle_starttag(self, tag, attrs):
        ids = [v for k, v in attrs if k == "id" and v is not None]
        if len(ids) > 1:
            self.ambiguous_ids.update(ids)
        if sum(k == "href" for k, _ in attrs) > 1:
            self.ambiguous_links.add(self.position())
        super().handle_starttag(tag, attrs)


def decoded(value):
    if re.search(r"%(?![0-9a-fA-F]{2})", value):
        raise ValueError("Invalid URL percent escape")
    return unquote(value, encoding="utf-8", errors="strict")


def same_document(href, name, citation, has_base):
    """No requests, fuzzy paper matching, version guessing or base URL adoption."""
    if len(href) > 2000 or any(ord(c) < 33 or ord(c) == 127 for c in href):
        return None, "INVALID_LINK"
    try:
        parsed = urlsplit(href)
        if parsed.scheme and parsed.scheme.lower() not in ("https", "http"):
            return None, "UNSAFE_SCHEME"
        if parsed.username or parsed.password:
            return None, "INVALID_LINK"
        if not parsed.fragment:
            return None, "NO_FRAGMENT"
        label = decoded(parsed.fragment)
        if not 1 <= len(label) <= 160 or any(
            c.isspace() or ord(c) < 32 or ord(c) == 127 for c in label
        ):
            return None, "INVALID_FRAGMENT"
        if has_base and not parsed.scheme:
            return None, "BASE_URL_REQUIRES_REVIEW"
        if not parsed.scheme and not parsed.netloc:
            if not parsed.path and not parsed.query:
                return label, None
            local_path = decoded(parsed.path)
            if not parsed.query and not local_path.startswith("/"):
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), local_path))
                if resolved == name:
                    return label, None
        if citation:
            target, original = urlsplit(urljoin(citation, href)), urlsplit(citation)
            if (
                target.scheme == original.scheme
                and target.hostname == original.hostname
                and target.port == original.port
                and target.path == original.path
                and target.query == original.query
                and not target.username
                and not target.password
            ):
                return label, None
        return None, "DIFFERENT_DOCUMENT"
    except (ValueError, UnicodeError):
        return None, "INVALID_LINK"


def enrich(blocks, source, name, citation=""):
    parser = ReferenceParser(source)
    parser.feed(source)
    parser.close()
    for node in parser.stack[1:]:
        node.end = len(source)
    indexed = {b["span"][0]: i for i, b in enumerate(blocks)}
    nodes = {n.start: n for n in parser.elements}
    if any(start not in nodes for start in indexed):
        raise ValueError("HTML reference spans disagree with the structural index")
    indexed_nodes = {nodes[start]: i for start, i in indexed.items()}
    owners, outer_descendants = {}, defaultdict(set)
    for n in parser.elements:
        chain = [n, *n.ancestors()]
        owners[n.start] = (
            []
            if any(a.tag in IGNORED for a in chain)
            else [indexed_nodes[a] for a in chain if a in indexed_nodes]
        )
    # A wrapper's nested equation rows must not look like unrelated sibling blocks.
    for start, i in indexed.items():
        outer = i
        for ancestor in nodes[start].ancestors():
            if ancestor in indexed_nodes:
                outer = indexed_nodes[ancestor]
            else:
                outer_descendants[ancestor].add(outer)
    ids = defaultdict(list)
    for n in parser.elements:
        if n.attrs.get("id"):
            ids[n.attrs["id"]].append(n)
    located = {}
    for label, targets in ids.items():
        if len(targets) != 1 or label in parser.ambiguous_ids:
            continue
        n = targets[0]
        if any(a.tag in IGNORED for a in [n, *n.ancestors()]):
            continue
        if owners[n.start]:
            located[label] = owners[n.start][0]
        elif n.tag not in CONTAINERS and len(outer_descendants[n]) == 1:
            located[label] = next(iter(outer_descendants[n]))
    if len(located) > MAX_LABELS:
        raise ValueError("HTML reference target label budget exceeded")
    for b in blocks:
        b.update(html_reference_schema=SCHEMA, labels=[], reference_labels=[], html_references=[])
    for label, i in located.items():
        blocks[i]["labels"].append(label)
    has_base = any(n.tag == "base" and "href" in n.attrs for n in parser.elements)
    total = 0
    for n in parser.elements:
        if n.tag != "a" or "href" not in n.attrs or not owners[n.start]:
            continue
        href = n.attrs["href"]
        if len(href) > 2000:
            raise ValueError("HTML reference URL exceeds the 2000-character budget")
        label, problem = same_document(href, name, citation, has_base)
        if n.start in parser.ambiguous_links:
            label, problem = None, "AMBIGUOUS_HREF_ATTRIBUTE"
        for i in owners[n.start]:
            total += 1
            if total > MAX_REFERENCES:
                raise ValueError("HTML reference link budget exceeded")
            entry = {
                "href": href,
                "label": label,
                "span": [n.start, n.end],
                "status": problem,
                "target_span": None,
                "target_block_span": None,
                "target_count": 0,
            }
            if label is not None:
                targets = ids.get(label, [])
                entry["target_count"] = len(targets)
                if label in parser.ambiguous_ids or len(targets) > 1:
                    entry["status"] = "AMBIGUOUS_TARGET"
                elif not targets:
                    entry["status"] = "MISSING_TARGET"
                else:
                    target = targets[0]
                    entry["target_span"] = [target.start, target.end]
                    if label not in located:
                        entry["target_count"] = len(outer_descendants[target])
                        entry["status"] = (
                            "MULTIPLE_FRAGMENTS"
                            if entry["target_count"] > 1
                            else "UNINDEXED_TARGET"
                        )
                    else:
                        j = located[label]
                        entry["target_block_span"] = list(blocks[j]["span"])
                        entry["status"] = "SELF_REFERENCE" if i == j else "LOCATED"
                if (
                    entry["status"] != "SELF_REFERENCE"
                    and label not in blocks[i]["reference_labels"]
                ):
                    blocks[i]["reference_labels"].append(label)
            blocks[i]["html_references"].append(entry)
    return blocks
