"""Local project organization CLI; never executes a mathematical check."""

import argparse
import json
from pathlib import Path
import sys

from mra.formalizer import strict_json
from mra.research_capacity import ManagedResearchStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    show = commands.add_parser("show")
    show.add_argument("project_id")
    for name in ("create", "archive", "restore"):
        command = commands.add_parser(name)
        if name != "create":
            command.add_argument("project_id")
        command.add_argument("--request-file", type=Path, required=True)
    args = parser.parse_args()
    store = ManagedResearchStore(args.root)
    if args.command == "list":
        result = store.capacity_catalog()
    elif args.command == "show":
        result = store.report(args.project_id)
    else:
        if args.request_file.stat().st_size > 131072:
            raise ValueError("Project organization request exceeds 128 KiB")
        request = strict_json(args.request_file.read_text(encoding="utf-8"))
        result = (
            store.create(request)
            if args.command == "create"
            else store.change_lifecycle(args.project_id, args.command, request)
        )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
