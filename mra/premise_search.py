"""Local, source-bound Mathlib retrieval. Hits never certify a research claim."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import uuid

from .domain import canonical, digest
from .formalizer import exact_object, strict_json
from .integrity import ContentSnapshot, file_record
from .model_service import text_field
from .research_flow import lease

SCHEMA = "mathlib-source-premises-v1"
SOURCE_TREE = ".runtime/mathlib"
ALIASES = {
    "一致连续": "uniform continuous",
    "均匀连续": "uniform continuous",
    "连续": "continuous",
    "紧致": "compact",
    "完备": "complete",
    "可积": "integrable",
    "积分": "integral",
    "导数": "deriv",
    "微分": "differentiable",
    "素数": "prime",
    "互素": "coprime",
    "同态": "hom",
    "同胚": "homeomorph",
    "概率": "probability",
    "测度": "measure",
    "收敛": "tendsto",
    "特征值": "eigenvalue",
    "柯西施瓦茨": "inner norm",
}
DECLARATION = re.compile(
    r"(?m)^[ \t]*(?:@\[[^\n]*?\][ \t]*)*"
    r"(?:(?:private|protected|noncomputable|nonrec|unsafe)[ \t]+)*"
    r"(?P<kind>theorem|lemma|def|abbrev|structure|class)[ \t]+"
    r"(?P<name>«[^»\n]+»|[^\s:({\[\]=,;]+)"
)
TOKENS = re.compile(r'/-|--|r(#+)?"|"')
COMMENT_MARKERS = re.compile(r"/-|-/")
STRING = re.compile(r'"(?:\\[\s\S]|[^"\\])*"')


def mask_noncode(source):
    """Preserve offsets/newlines while masking nested comments and strings."""
    parts, pos = [], 0
    while match := TOKENS.search(source, pos):
        start = match.start()
        if match.group() == "--":
            end = source.find("\n", match.end())
            end = len(source) if end == -1 else end
        elif match.group() == "/-":
            depth, end = 1, len(source)
            for marker in COMMENT_MARKERS.finditer(source, match.end()):
                depth += 1 if marker.group() == "/-" else -1
                if depth == 0:
                    end = marker.end()
                    break
        elif match.group().startswith("r"):
            closing = '"' + (match.group(1) or "")
            found = source.find(closing, match.end())
            end = len(source) if found == -1 else found + len(closing)
        else:
            literal = STRING.match(source, start)
            end = literal.end() if literal else len(source)
        parts.extend((source[pos:start], re.sub(r"[^\n]", " ", source[start:end])))
        pos = end
    parts.append(source[pos:])
    return "".join(parts)


def words(text):
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return re.findall(r"[^\W_]+", text.casefold())


def query_terms(query):
    text_field(query, "Search words", 240)
    for original, english in ALIASES.items():
        query = query.replace(original, " " + english + " ")
    terms = list(dict.fromkeys(words(query)))
    if not 1 <= len(terms) <= 12:
        raise ValueError("Search with 1-12 words or a supported mathematical concept")
    return terms


def excerpts(path, source, sha256, identity, commit):
    matches = list(DECLARATION.finditer(mask_noncode(source)))
    lines = source.splitlines(keepends=True)
    offsets, offset = [], 0
    for line_text in lines:
        offsets.append(offset)
        offset += len(line_text)
    from bisect import bisect_right

    for i, match in enumerate(matches):
        line = bisect_right(offsets, match.start())
        next_line = (
            bisect_right(offsets, matches[i + 1].start())
            if i + 1 < len(matches)
            else len(lines) + 1
        )
        first, last = max(1, line - 6), min(len(lines), line + 20, next_line - 1)
        raw = "".join(lines[first - 1 : last])
        snippet = raw[:2400]
        item = {
            "index_identity": identity,
            "mathlib_commit": commit,
            "license": "Apache-2.0",
            "path": path,
            "module": path[:-5].replace("/", "."),
            "written_name": source[match.start("name") : match.end("name")],
            "kind": match.group("kind"),
            "declaration_line": line,
            "line_start": first,
            "line_end": first + snippet.rstrip("\n").count("\n"),
            "excerpt": snippet,
            "excerpt_truncated": len(raw) > len(snippet) or last < next_line - 1,
            "source_sha256": sha256,
            "source_url": f"https://github.com/leanprover-community/mathlib4/blob/{commit}/{path}#L{line}",
            "scope": "Source excerpt; name as written, not an elaborated fully qualified name or type",
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
        }
        yield {"item_id": "premise-" + digest(item), **item}


class PremiseIndex:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/mathlib-premise-index"
        self.path = self.base / "catalog.sqlite3"
        self.base.mkdir(parents=True, exist_ok=True)
        self.paths_intact()

    def paths_intact(self):
        if self.base.resolve() != self.base or self.path.is_symlink():
            raise ValueError("Premise index storage was redirected")

    def identity(self):
        snapshot = ContentSnapshot.load_runtime(self.root)
        environment = self.root / "lean_workspace/environment.json"
        if file_record(environment) != snapshot.data["files"]["lean_workspace/environment.json"]:
            raise ValueError("Mathlib version configuration changed")
        config = strict_json(environment.read_text(encoding="utf-8"))
        commit = config["mathlib_commit"]
        if not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40}", commit):
            raise ValueError("A fixed Mathlib commit is required")
        tree = snapshot.data["trees"][SOURCE_TREE]
        source_files = {
            name: row["sha256"]
            for name, row in tree.items()
            if name.startswith("Mathlib/") and name.endswith(".lean") and row.get("kind") == "file"
        }
        if file_record(self.source_path("LICENSE")) != tree.get("LICENSE"):
            raise ValueError("Mathlib license no longer matches the fixed source")
        value = {
            "schema": SCHEMA,
            "runtime_snapshot": snapshot.fingerprint,
            "mathlib_commit": commit,
            "license": "Apache-2.0",
            "source_files": len(source_files),
            "extractor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
        if not source_files:
            raise ValueError("Fixed Mathlib source files are unavailable")
        return {"identity": digest(value), **value}, source_files

    def source_path(self, name):
        p = PurePosixPath(name)
        if p.is_absolute() or str(p) != name or "\\" in name or ".." in p.parts:
            raise ValueError("Invalid Mathlib source path")
        target = self.root / SOURCE_TREE / name
        if target.resolve() != target or not target.is_file():
            raise ValueError("Mathlib source is missing or redirected")
        return target

    def read_source(self, name, expected):
        target = self.source_path(name)
        if target.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("Mathlib source exceeds 8 MiB")
        raw = target.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("Mathlib source changed; retrieval cannot use stale contents")
        return raw.decode("utf-8")

    @contextmanager
    def database(self):
        self.paths_intact()
        if not self.path.is_file():
            raise ValueError("PREMISE_INDEX_MISSING: build the local source index first")
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            yield db
        finally:
            db.close()

    def metadata(self, db):
        value = strict_json(db.execute("SELECT body FROM metadata WHERE id=1").fetchone()[0])
        current, files = self.identity()
        if value["source"] != current:
            raise ValueError(
                "PREMISE_INDEX_STALE: explicitly rebuild for the current source version"
            )
        return value, files

    def status(self):
        try:
            with self.database() as db:
                metadata, _ = self.metadata(db)
            return {"ready": True, **metadata}
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            return {"ready": False, "reason": str(error)[:240]}

    def build(self):
        self.paths_intact()
        with lease(self.base):
            identity, files = self.identity()
            pending = self.base / (".pending-" + uuid.uuid4().hex + ".sqlite3")
            db = sqlite3.connect(pending)
            try:
                db.execute("CREATE TABLE metadata (id INTEGER PRIMARY KEY,body TEXT NOT NULL)")
                db.execute("CREATE TABLE entries (id TEXT PRIMARY KEY,body TEXT NOT NULL)")
                db.execute("CREATE VIRTUAL TABLE terms USING fts5(id UNINDEXED,name,module,body)")
                count = 0
                for name, sha in sorted(files.items()):
                    source = self.read_source(name, sha)
                    for item in excerpts(
                        name, source, sha, identity["identity"], identity["mathlib_commit"]
                    ):
                        db.execute(
                            "INSERT INTO entries VALUES (?,?)",
                            (item["item_id"], canonical(item).decode()),
                        )
                        db.execute(
                            "INSERT INTO terms VALUES (?,?,?,?)",
                            (
                                item["item_id"],
                                " ".join(words(item["written_name"])),
                                " ".join(words(item["module"])),
                                " ".join(words(item["excerpt"])),
                            ),
                        )
                        count += 1
                metadata = {
                    "source": identity,
                    "entries": count,
                    "built_at": datetime.now(timezone.utc).isoformat(),
                    "ranking": "SQLite FTS5 lexical BM25; explicit concept aliases, no semantic or proof guarantee",
                }
                db.execute("INSERT INTO metadata VALUES (1,?)", (canonical(metadata).decode(),))
                db.commit()
                if self.identity()[0] != identity:
                    raise ValueError("Source identity changed while indexing")
                db.close()
                with pending.open("rb") as stream:
                    os.fsync(stream.fileno())
                pending.replace(self.path)
                fd = os.open(self.base, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            finally:
                db.close()
                if pending.exists():
                    pending.unlink()
        return metadata

    def checked_item(self, body, identity, files, requested_id):
        item = strict_json(body)
        if item["item_id"] != requested_id:
            raise ValueError("Premise row identity does not match the requested source")
        name = item["path"]
        if name not in files or item["index_identity"] != identity["identity"]:
            raise ValueError("Premise is outside the fixed Mathlib source")
        source = self.read_source(name, files[name])
        actual = next(
            (
                x
                for x in excerpts(
                    name, source, files[name], identity["identity"], identity["mathlib_commit"]
                )
                if x["item_id"] == item["item_id"]
            ),
            None,
        )
        if actual != item:
            raise ValueError("Premise index entry no longer matches its real source excerpt")
        return item

    def lookup(self, item_id):
        if not isinstance(item_id, str) or not re.fullmatch(r"premise-[a-f0-9]{64}", item_id):
            raise ValueError("Invalid source premise identifier")
        with self.database() as db:
            metadata, files = self.metadata(db)
            row = db.execute("SELECT body FROM entries WHERE id=?", (item_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown source premise")
            return self.checked_item(row[0], metadata["source"], files, item_id)

    def search(self, data):
        exact_object(data, {"query", "limit"}, "Mathlib source search")
        if type(data["limit"]) is not int or not 1 <= data["limit"] <= 12:
            raise ValueError("Search limit must be 1-12")
        terms = query_terms(data["query"])
        match = " AND ".join('"' + word + '"' for word in terms)
        with self.database() as db:
            metadata, files = self.metadata(db)
            rows = db.execute(
                "SELECT e.id,e.body FROM terms JOIN entries e ON e.id=terms.id WHERE terms MATCH ? "
                "ORDER BY CASE WHEN terms.name=? THEN 0 WHEN terms.name LIKE ? THEN 1 ELSE 2 END, "
                "bm25(terms,0,8,2,1),terms.id LIMIT ?",
                (
                    match,
                    " ".join(words(data["query"])),
                    "% " + " ".join(words(data["query"])),
                    data["limit"],
                ),
            ).fetchall()
            hits = [self.checked_item(row[1], metadata["source"], files, row[0]) for row in rows]
        return {
            "query": data["query"],
            "query_terms": terms,
            "hits": hits,
            "index": metadata,
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
            "new_lean_checks": 0,
        }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("build", "status", "search"))
    parser.add_argument("--query")
    args = parser.parse_args()
    index = PremiseIndex(Path.cwd())
    value = (
        index.build()
        if args.operation == "build"
        else index.status()
        if args.operation == "status"
        else index.search({"query": args.query, "limit": 8})
    )
    print(json.dumps(value, ensure_ascii=False, indent=2))
