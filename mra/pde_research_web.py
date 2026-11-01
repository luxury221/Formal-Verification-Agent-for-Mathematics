"""Same-origin HTTP adapter for project-bound classical PDE research."""

from urllib.parse import urlsplit
from .pde_research import SCHEMA
from .pde_research_examples import examples
from .pde_language_lab import PDELanguageStore
from .formalizer import exact_object
import re
from .pde_language import catalog


def dispatch(handler, write):
    parsed = urlsplit(handler.path)
    parts = parsed.path.split("/")
    if parts[:3] != ["", "v1", "pde-research"]:
        return False
    if parsed.query:
        raise ValueError("Analysis task routes do not accept query parameters")
    store = handler.server.pde_research
    result, status = None, 200
    if not write and parts == ["", "v1", "pde-research", "catalog"]:
        result = {"schema": SCHEMA, "language": catalog(), "examples": examples()}
    elif write and parts == ["", "v1", "pde-research", "source-preview"]:
        data = handler.body()
        exact_object(data, {"run_id"}, "External seed source preview")
        if not isinstance(data["run_id"], str) or not re.fullmatch(
            r"pdewatched-[a-f0-9]{32}", data["run_id"]
        ):
            raise ValueError("External graph seeds must use the watched PDE source provider")
        result = PDELanguageStore(handler.server.root).describe_source(data["run_id"])
    elif write and parts == ["", "v1", "pde-research", "expanded-preview"]:
        result = store.preview_expanded(handler.body())
    elif write and parts == ["", "v1", "pde-research", "expanded"]:
        result, status = store.create_expanded(handler.body()), 201
    elif write and parts == ["", "v1", "pde-research", "preview"]:
        result = store.preview(handler.body())
    elif write and len(parts) == 3:
        result, status = store.create(handler.body()), 201
    elif not write and len(parts) == 6 and parts[3] == "projects":
        result = store.catalog(parts[4], parts[5])
        result = {
            **result,
            "graphs": [
                {
                    "graph_id": g["graph_id"],
                    "title": next(
                        n["target"]["title"]
                        for n in g["plan"]["request"]["nodes"]
                        if n["id"] == g["plan"]["problem_id"]
                    ),
                    "nodes": len(g["plan"]["order"]),
                    "invalidated": g["invalidated"],
                }
                for g in result["graphs"]
            ],
        }
    elif not write and len(parts) == 5 and parts[4] == "progress":
        result = store.progress(parts[3])
    elif not write and len(parts) == 4:
        result = store.report(parts[3])
    elif write and len(parts) == 5:
        key, action = parts[3:]
        if action == "node-preview":
            result = store.preview_node(key, handler.body())
        elif action == "submit":
            result, status = store.submit(key, handler.body()), 202
            if handler.server.pump:
                handler.server.pump.wake.set()
        elif action in ("cancel", "withdraw"):
            result = getattr(store, action)(key, handler.body())
    if result is None:
        return False
    handler.reply(status, result)
    return True
