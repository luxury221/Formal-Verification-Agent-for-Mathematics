"""Explicit operations entry for the isolated analysis foundation experiment."""

import argparse
import json
from pathlib import Path

from mra.calculus_foundation import catalog, report, run, seal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("catalog")
    sub.add_parser("seal")
    execution = sub.add_parser("run")
    execution.add_argument("--probe", required=True)
    execution.add_argument("--reviewer", required=True)
    reading = sub.add_parser("report")
    reading.add_argument("--run-id", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    value = (
        catalog()
        if args.command == "catalog"
        else seal(root)
        if args.command == "seal"
        else run(root, args.probe, args.reviewer)
        if args.command == "run"
        else report(root, args.run_id)
    )
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
