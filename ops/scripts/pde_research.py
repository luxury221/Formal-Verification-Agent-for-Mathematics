"""Explicit CLI for classical PDE project graphs; worker execution is opt-in."""

import argparse
import json
from pathlib import Path

from mra.pde_research import PDEResearchStore, SCHEMA
from mra.pde_research_examples import examples
from mra.formalizer import strict_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("catalog")
    for name in ("preview", "create", "expanded-preview", "expanded-create"):
        item = sub.add_parser(name)
        item.add_argument("--input", required=True)
    for name in ("node-preview", "submit", "cancel", "withdraw"):
        item = sub.add_parser(name)
        item.add_argument("--graph-id", required=True)
        item.add_argument("--input", required=True)
    item = sub.add_parser("report")
    item.add_argument("--graph-id", required=True)
    item = sub.add_parser("list")
    item.add_argument("--project-id", required=True)
    item.add_argument("--branch-id", default="main")
    item = sub.add_parser("work")
    item.add_argument("--graph-id")
    sub.add_parser("recover")
    args = parser.parse_args()
    if args.command == "catalog":
        result = {"schema": SCHEMA, "examples": examples(), "automatic_worker": False}
    else:
        store = PDEResearchStore(Path(__file__).resolve().parents[2])
        raw = None
        if hasattr(args, "input"):
            data = Path(args.input).read_bytes()
            if len(data) > 196608:
                raise ValueError("Input exceeds 192 KiB")
            raw = strict_json(data.decode("utf-8"))
        if args.command in ("preview", "create", "expanded-preview", "expanded-create"):
            method = {
                "expanded-preview": "preview_expanded",
                "expanded-create": "create_expanded",
            }.get(args.command, args.command)
            result = getattr(store, method)(raw)
        elif args.command in ("node-preview", "submit", "cancel", "withdraw"):
            result = getattr(
                store, "preview_node" if args.command == "node-preview" else args.command
            )(args.graph_id, raw)
        elif args.command == "report":
            result = store.report(args.graph_id)
        elif args.command == "list":
            result = store.catalog(args.project_id, args.branch_id)
        elif args.command == "work":
            result = store.work_once(args.graph_id)
        else:
            result = store.recover()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
