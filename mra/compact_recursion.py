"""Opt-in compact grind diagnostics with the unchanged full-target kernel executor."""

from copy import deepcopy
import hashlib
from pathlib import Path

from .domain import digest
from .integrity import file_hash
from .lemma_composition import LemmaCompositionStore
from .mathlib_lab import MathlibLabStore
from .recursion_programs import MODE as LEGACY_MODE, RecursionStore, manifest as legacy_manifest
from .recursion_programs import prepare as legacy_prepare
from .structure_lemma_library import temporary_busy

MODE = "structure-recursion-program-v2"
FILE = "mra/compact_recursion.py"
PROFILE = {
    "name": "compact-grind-failure-v1",
    "grind_verbose": False,
    "sandbox_file_limit_bytes": 1048576,
    "automatic_retry": False,
}


def compact(source):
    # Only rewrite a complete fixed compiler-generated tactic line. Neither the
    # client data nor theorem identifiers are used as textual Lean fragments.
    return "\n".join(
        line[: len(line) - len(line.lstrip())] + "all_goals (grind -verbose)"
        if line.strip() == "all_goals grind"
        else line
        for line in source.splitlines()
    )


def manifest(root):
    return {
        "mode": MODE,
        "legacy_provider": legacy_manifest(root),
        "files": {FILE: file_hash(Path(root) / FILE)},
        "diagnostic_profile": deepcopy(PROFILE),
    }


def prepare(root, raw):
    if not isinstance(raw, dict) or raw.get("schema") != MODE:
        raise ValueError("Use structure-recursion-program-v2 for compact diagnostics")
    legacy = deepcopy(raw)
    legacy["schema"] = LEGACY_MODE
    value = legacy_prepare(root, legacy)
    value["request"] = deepcopy(raw)
    value["proof_source"] = compact(value["proof_source"])
    for lemma in value["local_lemmas"]:
        lemma["proof_source"] = compact(lemma["proof_source"])
    value["source_manifest"] = manifest(root)
    value["diagnostic_profile"] = deepcopy(PROFILE)
    value["limitations"] += [
        "Only grind verbose failure output is disabled; full goals and kernel audits remain.",
        "Output may still reach the unchanged 1 MiB per-file sandbox limit; no hard guarantee.",
        "Opt-in separate ledger; no automatic old-run migration, retry, export or graph admission.",
    ]
    value.pop("plan_fingerprint")
    value["plan_fingerprint"] = digest(value)
    return value


def diagnostics(row, directory):
    """A bounded observation, never an independent mathematical judgment."""
    value = {
        "status": "NOT_AVAILABLE",
        "phase": None,
        "first_error": None,
        "stdout_bytes": None,
        "stderr_bytes": None,
        "evidence_level": "E0",
    }
    if row["invalidated"] or not row["files"]:
        return value
    if row["state"] not in ("REJECTED", "EXECUTION_ERROR"):
        return {**value, "status": "NO_FAILURE_DIAGNOSIS"}
    phase = "replay" if (row["result"] or {}).get("replay") else "candidate"
    stdout = directory / phase / "stdout.log"
    stderr = directory / phase / "stderr.log"
    if any(phase + "/" + n not in row["files"] for n in ("stdout.log", "stderr.log")):
        return value
    # Recheck the same bounded bytes used for the summary, so a change between
    # the full report audit and this read cannot yield a stale error header.
    logs = {}
    try:
        if directory.resolve() != directory or (directory / phase).resolve() != directory / phase:
            return value
        for name, source in (("stdout.log", stdout), ("stderr.log", stderr)):
            if source.is_symlink():
                return value
            with source.open("rb") as handle:
                data = handle.read(PROFILE["sandbox_file_limit_bytes"] + 1)
            if (
                len(data) > PROFILE["sandbox_file_limit_bytes"]
                or hashlib.sha256(data).hexdigest() != row["files"][phase + "/" + name]
            ):
                return value
            logs[name] = data
    except OSError:
        return value
    head = logs["stdout.log"][:4096].decode("utf-8", errors="replace")
    error = logs["stderr.log"][:4096].decode("utf-8", errors="replace")
    size = len(logs["stdout.log"])
    first = next((s for s in head.splitlines() if "error:" in s), None)
    if first is None:
        first = (
            error.splitlines()[0]
            if error
            else head.splitlines()[0]
            if head
            else "No error header in bounded prefix"
        )
    status = "CANDIDATE_REJECTED" if row["state"] == "REJECTED" else "EXECUTION_FAILURE"
    if size == PROFILE["sandbox_file_limit_bytes"] and "file too large" in error:
        status = "OUTPUT_FILE_LIMIT"
    return {
        **value,
        "status": status,
        "phase": phase,
        "first_error": first[:500],
        "stdout_bytes": size,
        "stderr_bytes": len(logs["stderr.log"]),
        "observed_log_sha256": {
            n: row["files"][phase + "/" + n] for n in ("stdout.log", "stderr.log")
        },
    }


class CompactRecursionStore(RecursionStore):
    def __init__(self, root, *, base=None):
        chosen = (
            Path(base) if base is not None else Path(root) / "artifacts/structure-recursion-compact"
        )
        if chosen.resolve() == Path(root).resolve() / "artifacts/structure-recursion":
            raise ValueError("Compact and original recursion ledgers must be separate")
        super().__init__(root, base=chosen)

    def preview(self, request):
        return prepare(self.root, request)

    def gate(self, row, check_files=True):
        if row["plan"]["source_manifest"] != manifest(self.root):
            return "Compact diagnostic profile, original compiler or runtime changed"
        if self.preview(row["plan"]["request"]) != row["plan"]:
            return "Complete compact plan differs from its reviewed request"
        view = {
            **row,
            "plan": {
                **row["plan"],
                "source_manifest": row["plan"]["source_manifest"]["legacy_provider"]["provider"][
                    "executor"
                ],
            },
        }
        return MathlibLabStore.gate(self, view, check_files)

    def report(self, run_id):
        row = temporary_busy(lambda: LemmaCompositionStore.report(self, run_id))
        return {
            **row,
            "execution_diagnostics": diagnostics(row, self.directory(run_id)),
            "mode": MODE,
            "diagnostic_profile": deepcopy(PROFILE),
            "fresh_attestation": False,
        }

    def catalog(self):
        return {
            **super().catalog(),
            "mode": MODE,
            "diagnostic_profile": deepcopy(PROFILE),
            "automatic_migration": False,
            "automatic_retry": False,
            "exports_as_sources": False,
        }
