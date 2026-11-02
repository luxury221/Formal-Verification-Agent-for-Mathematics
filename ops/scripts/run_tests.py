"""Run explicit test scopes; deferred environment tests never count as passed."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import sys
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
OPT_INS = ("MRA_REAL_LEAN", "MRA_DOCUMENT_RUNTIME", "MRA_REAL_EVIDENCE_READ")


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Duplicate suite field: {key}")
        value[key] = item
    return value


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def discover(root):
    sys.path[:0] = [str(root), str(root / "tests")]
    loader = unittest.TestLoader()
    cases = list(flatten(loader.discover(str(root / "tests"))))
    if loader.errors:
        raise ValueError("Test discovery failed:\n" + "\n".join(loader.errors))
    unique, duplicates = {}, Counter()
    for case in cases:
        key = case.id()
        if key in unique:
            old = unique[key]
            if type(old) is not type(case) or old._testMethodName != case._testMethodName:
                raise ValueError(f"Conflicting test identity: {key}")
            duplicates[key] += 1
        else:
            unique[key] = case
    if not unique:
        raise ValueError("No tests discovered")
    return list(unique.values()), dict(duplicates)


def load_manifest(root):
    path = root / "tests/suites.json"
    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    if set(data) != {"schema", "requirements", "runtime_contracts", "lean_smoke"}:
        raise ValueError("Invalid suite manifest fields")
    if data["schema"] != "mra-test-suites-v1":
        raise ValueError("Unsupported suite manifest")
    for name, item in data["requirements"].items():
        if set(item) != {"files", "description"} or not item["files"] or not item["description"]:
            raise ValueError(f"Invalid requirement: {name}")
        for file in item["files"]:
            path = PurePosixPath(file)
            if path.is_absolute() or ".." in path.parts or "\\" in file or str(path) != file:
                raise ValueError(f"Invalid requirement path: {file}")
    return data


def classify(cases, manifest):
    """Selectors are exact class or method names, with no fuzzy exclusions."""
    requirements = {}
    for entry in manifest["runtime_contracts"]:
        if set(entry) != {"test", "requires", "reason"} or not entry["reason"]:
            raise ValueError("Invalid runtime test classification")
        required = entry["requires"]
        if not required or any(name not in manifest["requirements"] for name in required):
            raise ValueError(f"Unknown test requirement: {entry['test']}")
        matched = [
            case for case in cases if entry["test"] in (case.id(), case.id().rsplit(".", 1)[0])
        ]
        if not matched:
            raise ValueError(f"Stale test selector: {entry['test']}")
        for case in matched:
            if case.id() in requirements:
                raise ValueError(f"Overlapping test selectors: {case.id()}")
            requirements[case.id()] = {"requires": required, "reason": entry["reason"]}
    smoke = manifest["lean_smoke"]
    if (
        not smoke
        or len(smoke) != len(set(smoke))
        or not set(smoke).issubset({c.id() for c in cases})
    ):
        raise ValueError("Empty, duplicated or stale Lean smoke selectors")
    return requirements


def select(cases, requirements, manifest, scope):
    if scope == "source":
        chosen = [c for c in cases if c.id() not in requirements]
    elif scope == "lean-smoke":
        index = {c.id(): c for c in cases}
        chosen = [index[key] for key in manifest["lean_smoke"]]
    else:
        chosen = list(cases)
    chosen_ids = {c.id() for c in chosen}
    deferred = [
        {
            "test": c.id(),
            **requirements.get(
                c.id(), {"reason": "Outside the requested smoke scope", "requires": []}
            ),
        }
        for c in cases
        if c.id() not in chosen_ids
    ]
    return chosen, deferred


def preflight(root, scope, chosen, requirements, manifest, environ):
    if scope == "source":
        enabled = [name for name in OPT_INS if environ.get(name) == "1"]
        if enabled:
            raise ValueError(
                "Source scope refuses enabled integration flags: " + ", ".join(enabled)
            )
    if scope == "lean-smoke" and environ.get("MRA_REAL_LEAN") != "1":
        raise ValueError("Lean smoke requires explicit MRA_REAL_LEAN=1")
    needed = {name for c in chosen for name in requirements.get(c.id(), {}).get("requires", [])}
    if scope == "lean-smoke":
        needed.add("base-runtime")
    missing = {
        name: [
            file for file in manifest["requirements"][name]["files"] if not (root / file).is_file()
        ]
        for name in sorted(needed)
    }
    missing = {name: files for name, files in missing.items() if files}
    if missing:
        raise ValueError(
            "Missing environment prerequisites (presence is not integrity verification): "
            + json.dumps(missing)
        )


def prerequisite_inventory(root, manifest):
    return {
        name: {
            **item,
            "missing_files": [file for file in item["files"] if not (root / file).is_file()],
            "presence_only_not_integrity_verified": True,
        }
        for name, item in manifest["requirements"].items()
    }


class ReportResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed_ids = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed_ids.append(test.id())


def execute(cases, scope, stream, verbosity):
    result = unittest.TextTestRunner(
        stream=stream, verbosity=verbosity, resultclass=ReportResult
    ).run(unittest.TestSuite(cases))
    details = {
        "tests_run": result.testsRun,
        "passed": len(result.passed_ids),
        "passed_tests": result.passed_ids,
        "skipped": [{"test": t.id(), "reason": reason} for t, reason in result.skipped],
        "failures": [t.id() for t, _ in result.failures],
        "errors": [t.id() for t, _ in result.errors],
        "expected_failures": [t.id() for t, _ in result.expectedFailures],
        "unexpected_successes": [t.id() for t in result.unexpectedSuccesses],
    }
    complete_smoke = scope != "lean-smoke" or len(result.passed_ids) == len(cases)
    success = result.wasSuccessful() and bool(result.passed_ids) and complete_smoke
    return success, details


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("source", "prepared", "lean-smoke"), default="source")
    parser.add_argument(
        "--list", action="store_true", help="Inventory only; no test fixtures or checks run"
    )
    parser.add_argument(
        "--report", type=Path, help="New JSON path below artifacts/; existing files are refused"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    os.chdir(ROOT)
    report_path = args.report or Path("artifacts/test-suites") / (
        args.suite + "-" + uuid.uuid4().hex + ".json"
    )
    report_path = ROOT / report_path
    if not report_path.resolve().is_relative_to(ROOT / "artifacts") or report_path.exists():
        parser.error("Report must be a new file below this project's artifacts/")
    report = {
        "schema": "mra-test-report-v1",
        "suite": args.suite,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PREFLIGHT_FAILED",
        "mathematical_evidence": False,
        "scope_note": "Software test results; not a new theorem, model evaluation or whole-paper verdict.",
    }
    started = time.monotonic()
    exit_code = 2
    try:
        # Check flags before importing test modules, not after their decorators run.
        if args.suite == "source" and any(os.environ.get(n) == "1" for n in OPT_INS):
            raise ValueError("Unset integration flags before running the source suite")
        manifest = load_manifest(ROOT)
        cases, duplicates = discover(ROOT)
        requirements = classify(cases, manifest)
        chosen, deferred = select(cases, requirements, manifest, args.suite)
        report.update(
            discovered_unique=len(cases),
            duplicate_imports_omitted=duplicates,
            selected_tests=[c.id() for c in chosen],
            deferred_tests=deferred,
            runtime_contracts=requirements,
            prerequisites=prerequisite_inventory(ROOT, manifest),
        )
        print(
            f"Suite {args.suite}: {len(chosen)} selected, {len(deferred)} deferred, "
            f"{sum(duplicates.values())} duplicate imports omitted",
            flush=True,
        )
        if args.list:
            for name, item in report["prerequisites"].items():
                missing = ", ".join(item["missing_files"])
                print(
                    f"  {name}: "
                    + ("missing " + missing if missing else "files present; not integrity verified")
                )
            report["status"] = "INVENTORY_ONLY"
            exit_code = 0
        else:
            preflight(ROOT, args.suite, chosen, requirements, manifest, os.environ)
            report["status"] = "RUNNING"
            success, details = execute(chosen, args.suite, sys.stderr, 2 if args.verbose else 1)
            report.update(status="PASSED" if success else "FAILED", result=details)
            exit_code = 0 if success else 1
    except (OSError, ValueError, TypeError, KeyError) as exc:
        report["error"] = str(exc)
        print(str(exc), file=sys.stderr)
    except KeyboardInterrupt:
        report["status"] = "INTERRUPTED"
        report["error"] = "Interrupted before a complete test result was available"
        exit_code = 130
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    report["exit_code"] = exit_code
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("Test report: " + str(report_path.relative_to(ROOT)), flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
