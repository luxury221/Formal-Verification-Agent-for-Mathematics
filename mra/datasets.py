"""Pinned, repository-authored evaluation data; reference checks are not model scores."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import math
from pathlib import Path
import re

from .domain import digest
from .formalizer import SourceClaim, Variable, exact_object, strict_json
from .model_service import ModelBudget, integer_field, text_field

DATASET_ID = "nat-arithmetic-v1"
CASES_PATH = "evals/nat-arithmetic-v1/cases.json"
MANIFEST_PATH = "evals/datasets_manifest/nat-arithmetic-v1.json"
SPLITS = ("train", "development", "regression", "reserved")


@dataclass(frozen=True)
class EvaluationBudget:
    model: ModelBudget
    lean_timeout_seconds: float
    max_generation_attempts: int
    max_lean_checks_per_case: int

    def __post_init__(self):
        if not isinstance(self.model, ModelBudget):
            raise ValueError("Evaluation requires a typed model budget")
        timeout = self.lean_timeout_seconds
        if (
            type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or not 0 < timeout <= 120
        ):
            raise ValueError("Evaluation Lean timeout must be finite and in (0, 120]")
        integer_field(self.max_generation_attempts, "max_generation_attempts", 1, 1)
        integer_field(self.max_lean_checks_per_case, "max_lean_checks_per_case", 1, 1)


@dataclass(frozen=True)
class EvaluationDataset:
    manifest: dict
    cases: tuple[dict, ...]
    budget: EvaluationBudget

    @property
    def fingerprint(self):
        return digest({"manifest": self.manifest, "cases": self.cases})

    def select(self, split="regression", *, include_reserved=False):
        if split not in (*SPLITS, "all"):
            raise ValueError("Unknown evaluation split")
        if split in ("reserved", "all") and not include_reserved:
            raise ValueError("Reserved references require explicit --include-reserved")
        ids = {
            case_id
            for name, members in self.manifest["splits"].items()
            if split == "all" or split == name
            for case_id in members
        }
        return tuple(case for case in self.cases if case["id"] in ids)

    def public_inputs(self, split="regression", *, include_reserved=False):
        return [
            asdict(SourceClaim(case["id"], 1, case["original_statement"]))
            for case in self.select(split, include_reserved=include_reserved)
        ]


def normalized(text):
    return re.sub(r"\s+", "", text)


def _read(root, relative):
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Evaluation data must stay inside the project without file aliases")
    return path.read_bytes()


def load_dataset(root: Path) -> EvaluationDataset:
    manifest = strict_json(_read(root, MANIFEST_PATH).decode("utf-8"))
    exact_object(
        manifest,
        {
            "schema_version",
            "dataset_id",
            "path",
            "sha256",
            "domain",
            "license",
            "consent",
            "provenance",
            "training_use",
            "public_benchmark_imported",
            "splits",
            "families",
            "budget",
            "reserved_status",
            "limitations",
        },
        "dataset manifest",
    )
    if (
        manifest["schema_version"],
        manifest["dataset_id"],
        manifest["path"],
        manifest["domain"],
    ) != ("1", DATASET_ID, CASES_PATH, "elementary-natural-arithmetic"):
        raise ValueError("Unsupported dataset identity, path or domain")
    if (
        (manifest["license"], manifest["consent"])
        != ("project-owned-internal", "project-development-and-evaluation-only")
        or manifest["training_use"] is not False
        or manifest["public_benchmark_imported"] is not False
    ):
        raise ValueError("Dataset permissions do not authorize training or external imports")
    for name in ("provenance", "reserved_status", "limitations"):
        text_field(manifest[name], name)
    raw = _read(root, CASES_PATH)
    if hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
        raise ValueError("Dataset content differs from the manifest hash")
    # strict_json rejects duplicate keys and nonstandard constants, also in nested cases.
    envelope = strict_json(raw.decode("utf-8"))
    exact_object(envelope, {"schema_version", "cases"}, "dataset cases")
    if (
        envelope["schema_version"] != "1"
        or not isinstance(envelope["cases"], list)
        or not envelope["cases"]
    ):
        raise ValueError("Empty or unsupported dataset")
    cases, seen_ids, statements, originals = envelope["cases"], set(), set(), set()
    golden = strict_json(
        '{"cases":' + _read(root, "evals/golden_cases.json").decode("utf-8") + "}"
    )["cases"]
    golden_statements = {normalized(case["formal_statement"]) for case in golden}
    golden_originals = {normalized(case["original_statement"]) for case in golden}
    golden_ids = {case["id"] for case in golden}
    for case in cases:
        exact_object(
            case,
            {
                "id",
                "original_statement",
                "formal_statement",
                "direction",
                "proof",
                "extra_declarations",
                "expected_status",
                "expected_verdict",
                "expected_level",
                "annotation",
            },
            "reference case",
        )
        for name in ("id", "original_statement", "formal_statement", "proof"):
            text_field(case[name], name)
        if not re.fullmatch(r"natv1_[a-z0-9_]+", case["id"]) or case["id"] in seen_ids | golden_ids:
            raise ValueError("Invalid or duplicate dataset case id")
        statement, original = (
            normalized(case["formal_statement"]),
            normalized(case["original_statement"]),
        )
        if statement in statements | golden_statements or original in originals | golden_originals:
            raise ValueError("Duplicate statement or overlap with P0 regression cases")
        seen_ids.add(case["id"])
        statements.add(statement)
        originals.add(original)
        expected = {"prove": "PROVED", "refute": "REFUTED"}.get(case["direction"])
        if (
            expected is None
            or (case["expected_status"], case["expected_verdict"], case["expected_level"])
            != ("COMPLETED", expected, "E4_ALIGNED_AND_VERIFIED")
            or case["extra_declarations"] != ""
        ):
            raise ValueError(
                "Reference must prove its exact target/negation without extra declarations"
            )
        annotation = case["annotation"]
        exact_object(
            annotation, {"variables", "assumptions", "witnesses", "alignment_note"}, "annotation"
        )
        text_field(annotation["alignment_note"], "alignment_note")
        if not isinstance(annotation["variables"], list) or not annotation["variables"]:
            raise ValueError("Reference must declare its natural number variables")
        variables = [Variable(**variable) for variable in annotation["variables"]]
        if any(v.domain != "Nat" or v.quantifier != "forall" for v in variables) or len(
            {v.name for v in variables}
        ) != len(variables):
            raise ValueError("Dataset variables must be distinct universal natural numbers")
        if not isinstance(annotation["assumptions"], list):
            raise ValueError("Assumptions must be explicit")
        for assumption in annotation["assumptions"]:
            text_field(assumption, "assumption")
        witnesses = annotation["witnesses"]
        if not isinstance(witnesses, dict) or (case["direction"] == "refute") != bool(witnesses):
            raise ValueError("Refutations must declare concrete witness annotations")
        if witnesses and set(witnesses) != {v.name for v in variables}:
            raise ValueError("Refutation witness names must match the bound variables")
        for witness in witnesses.values():
            if not isinstance(witness, str) or not re.fullmatch(r"0|[1-9][0-9]*", witness):
                raise ValueError("Witness annotation must be a natural number literal")
    exact_object(manifest["splits"], set(SPLITS), "splits")
    assigned = []
    for name, members in manifest["splits"].items():
        if not isinstance(members, list) or any(not isinstance(member, str) for member in members):
            raise ValueError("Split membership must be case ids")
        assigned.extend(members)
        if name != "train" and not members:
            raise ValueError("Development, regression and reserved splits must be nonempty")
    if (
        manifest["splits"]["train"]
        or len(assigned) != len(set(assigned))
        or set(assigned) != seen_ids
    ):
        raise ValueError("Every case must belong to one evaluation split; training remains empty")
    families = manifest["families"]
    if not isinstance(families, dict) or set(families) != seen_ids:
        raise ValueError("Each case requires a family")
    family_splits = {}
    for split, members in manifest["splits"].items():
        for case_id in members:
            family = text_field(families[case_id], "family", 128)
            if family in family_splits and family_splits[family] != split:
                raise ValueError("Theorem family leaks across evaluation splits")
            family_splits[family] = split
    budget = manifest["budget"]
    exact_object(
        budget,
        {"model", "lean_timeout_seconds", "max_generation_attempts", "max_lean_checks_per_case"},
        "evaluation budget",
    )
    typed_budget = EvaluationBudget(
        ModelBudget(**budget["model"]),
        budget["lean_timeout_seconds"],
        budget["max_generation_attempts"],
        budget["max_lean_checks_per_case"],
    )
    return EvaluationDataset(manifest, tuple(cases), typed_budget)


def repository_cases(root: Path) -> dict[str, dict]:
    golden = strict_json(
        '{"cases":' + _read(root, "evals/golden_cases.json").decode("utf-8") + "}"
    )["cases"]
    all_cases = [*golden, *load_dataset(root).cases]
    result = {case["id"]: case for case in all_cases}
    if len(result) != len(all_cases):
        raise ValueError("Duplicate repository fixture identity")
    return result
