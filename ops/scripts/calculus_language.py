"""Explicit CLI for bounded calculus-language-v1; no automatic worker or retries."""

import argparse
import json
from pathlib import Path

from mra.calculus_examples import examples
from mra.calculus_lab import CalculusStore
from mra.calculus_language import MODE, catalog
from mra.formalizer import strict_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("catalog")
    for name in ("preview", "submit", "revoke"):
        item = sub.add_parser(name)
        item.add_argument("--input", required=True)
        if name == "revoke":
            item.add_argument("--run-id", required=True)
    for name in ("report", "cancel"):
        item = sub.add_parser(name)
        item.add_argument("--run-id", required=True)
    worker = sub.add_parser("work")
    worker.add_argument("--run-id")
    sub.add_parser("list")
    sub.add_parser("recover")
    args = parser.parse_args()
    if args.command == "catalog":
        value = {**catalog(), "examples": examples(), "automatic_worker": False}
    else:
        store = CalculusStore(Path(__file__).resolve().parents[2])
        raw = None
        if args.command in ("preview", "submit", "revoke"):
            data = Path(args.input).read_bytes()
            if len(data) > 196608:
                raise ValueError("Analysis CLI input exceeds 192 KiB")
            raw = strict_json(data.decode("utf-8"))
        if args.command == "preview":
            value = store.preview(raw)
        elif args.command == "submit":
            value = store.submit(raw)
        elif args.command == "report":
            value = store.report(args.run_id)
        elif args.command == "cancel":
            value = store.cancel(args.run_id)
        elif args.command == "revoke":
            value = store.revoke(args.run_id, raw)
        elif args.command == "work":
            value = store.work_once(args.run_id)
        elif args.command == "list":
            value = {"runs": store.list_runs()}
        else:
            value = store.recover()
        value = {"mode": MODE, **value}
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
