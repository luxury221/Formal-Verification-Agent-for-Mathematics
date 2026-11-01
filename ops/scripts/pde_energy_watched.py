"""Explicit operations entry for the independent event-monitored one-dimensional Poisson profile."""

import argparse
import json
from pathlib import Path

from mra.pde_energy_watched import catalog, recover, report, run, seal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("catalog")
    sub.add_parser("seal")
    execution = sub.add_parser("run")
    execution.add_argument("--probe", required=True)
    execution.add_argument("--reviewer", required=True)
    execution.add_argument("--source-run-id")
    reading = sub.add_parser("report")
    reading.add_argument("--run-id", required=True)
    recovery = sub.add_parser("recover")
    recovery.add_argument("--run-id", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    value = (
        catalog()
        if args.command == "catalog"
        else seal(root)
        if args.command == "seal"
        else run(root, args.probe, args.reviewer, source_run_id=args.source_run_id)
        if args.command == "run"
        else recover(root, args.run_id)
        if args.command == "recover"
        else report(root, args.run_id)
    )
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
