from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import sys
import time

from .harness import MinimalResearchEngine
from .formalizer import CandidateReview, SourceClaim, StructuredFormalizer, strict_json
from .intake import FixtureModelService, IntakeStore
from .model_service import ModelBudget
from .datasets import SPLITS, load_dataset
from .evaluation import evaluation_plan, run_reference_evaluation
from .tasks import TaskStore
from .certificates import parse_certificate, read_certificate, target_contract, target_descriptor

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MRA: real Lean checks of fixed targets and bounded proof certificates"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    suggestions = commands.add_parser(
        "structure-lemma-suggestions",
        help="Propose explicit current-source applications without Lean",
    )
    suggestions.add_argument("--input", required=True)
    web = commands.add_parser(
        "serve", help="Open a loopback workbench with one durable task worker"
    )
    web.add_argument("--port", type=int, default=8765)
    commands.add_parser("cases", help="Display original and exact formal statements before review")
    commands.add_parser(
        "environment", help="Verify the sealed runtime and display its content fingerprint"
    )
    runtime_audit = commands.add_parser(
        "runtime-audit", help="Measure a full-content runtime scan; diagnostic only, no Lean"
    )
    runtime_audit.add_argument("--workers", type=int, choices=(1, 2, 4), default=1)
    check = commands.add_parser(
        "check", help="Check one fixture; omit --reviewer to retain E3 pending alignment"
    )
    check.add_argument("case_id")
    check.add_argument(
        "--reviewer",
        help="Named person/process explicitly attesting to the displayed fixture's semantic alignment",
    )
    check.add_argument("--timeout", type=float, default=30.0)
    target = commands.add_parser(
        "certificate-target", help="Inspect an enabled exact certificate target; no Lean call"
    )
    target.add_argument("case_id")
    certificate = commands.add_parser(
        "check-certificate", help="Check bounded kernel-expression JSON against a fixed target"
    )
    certificate.add_argument("case_id")
    certificate.add_argument("--input", required=True)
    certificate.add_argument(
        "--reviewer", help="Explicit local assertion of this target's alignment"
    )
    certificate.add_argument("--timeout", type=float, default=30.0)
    benchmark = commands.add_parser(
        "benchmark", help="Run real Lean golden cases with benchmark-owned alignment records"
    )
    benchmark.add_argument("--output", default="artifacts/p0-benchmark.json")
    dataset = commands.add_parser(
        "dataset", help="Inspect the arithmetic evaluation protocol; no Lean/model call"
    )
    references = commands.add_parser(
        "reference-benchmark",
        help="Validate authored arithmetic references with real Lean; no model score",
    )
    model_benchmark = commands.add_parser(
        "model-benchmark", help="One model proof plan and real Lean check per authored Nat target"
    )
    model_benchmark.add_argument(
        "--plan", action="store_true", help="Inspect only; no model or Lean"
    )
    model_report = commands.add_parser(
        "model-benchmark-report", help="Read historical model evaluation with artifact hash checks"
    )
    model_report.add_argument("directory")
    mathlib_benchmark = commands.add_parser(
        "mathlib-benchmark", help="Real fixed-budget authored Mathlib diagnostics; no model score"
    )
    mathlib_benchmark.add_argument("--plan", action="store_true")
    mathlib_benchmark.add_argument(
        "--split", choices=("all", "development", "regression"), default="all"
    )
    mathlib_benchmark.add_argument("--plan-fingerprint")
    mathlib_benchmark.add_argument("--reviewer")
    mathlib_benchmark.add_argument("--confirmed", action="store_true")
    mathlib_benchmark_report = commands.add_parser(
        "mathlib-benchmark-report", help="Audit exact Mathlib evaluation artifacts and diagnostics"
    )
    mathlib_benchmark_report.add_argument("evaluation_id")
    commands.add_parser(
        "reuse-evaluation-prepare", help="Prepare the fixed paired proof reuse comparison; no Lean"
    )
    reuse_run = commands.add_parser(
        "reuse-evaluation-run", help="Run reviewed authored programs with real Lean; no model score"
    )
    reuse_run.add_argument("evaluation_id")
    reuse_run.add_argument("--plan-fingerprint", required=True)
    reuse_run.add_argument("--reviewer", required=True)
    reuse_run.add_argument("--confirmed", action="store_true")
    reuse_report = commands.add_parser(
        "reuse-evaluation-report", help="Audit paired comparison evidence and costs"
    )
    reuse_report.add_argument("evaluation_id")
    commands.add_parser(
        "typed-reuse-evaluation-prepare",
        help="Prepare the fixed paired proof reuse comparison; no Lean",
    )
    typed_eval_run = commands.add_parser(
        "typed-reuse-evaluation-run",
        help="Run reviewed authored programs with real Lean; no model score",
    )
    typed_eval_run.add_argument("evaluation_id")
    typed_eval_run.add_argument("--plan-fingerprint", required=True)
    typed_eval_run.add_argument("--reviewer", required=True)
    typed_eval_run.add_argument("--confirmed", action="store_true")
    typed_eval_report = commands.add_parser(
        "typed-reuse-evaluation-report", help="Audit paired comparison evidence and costs"
    )
    typed_eval_report.add_argument("evaluation_id")
    commands.add_parser(
        "structure-reuse-evaluation-prepare",
        help="Prepare the fixed structure proof reuse comparison; no Lean",
    )
    structure_eval_run = commands.add_parser(
        "structure-reuse-evaluation-run",
        help="Run reviewed structure programs with real Lean; no model score",
    )
    structure_eval_run.add_argument("evaluation_id")
    structure_eval_run.add_argument("--plan-fingerprint", required=True)
    structure_eval_run.add_argument("--reviewer", required=True)
    structure_eval_run.add_argument("--confirmed", action="store_true")
    structure_eval_report = commands.add_parser(
        "structure-reuse-evaluation-report", help="Audit paired structure evidence and costs"
    )
    structure_eval_report.add_argument("evaluation_id")
    search_prepare = commands.add_parser(
        "search-evaluation-prepare", help="Generate both bounded search variants; no Lean"
    )
    search_prepare.add_argument(
        "--split", choices=("all", "development", "regression"), default="all"
    )
    search_run = commands.add_parser(
        "search-evaluation-run", help="Check explicitly reviewed generated plans with real Lean"
    )
    search_run.add_argument("evaluation_id")
    search_run.add_argument("--plan-fingerprint", required=True)
    search_run.add_argument("--reviewer", required=True)
    search_run.add_argument("--confirmed", action="store_true")
    search_report = commands.add_parser(
        "search-evaluation-report", help="Read and audit a saved search comparison"
    )
    search_report.add_argument("evaluation_id")
    for action in ("search", "prepare"):
        cex = commands.add_parser(
            "counterexample-" + action,
            help="Finite Nat exploration or preparation of an exact negation; no Lean",
        )
        cex.add_argument("--input", required=True)
    cex_run = commands.add_parser(
        "counterexample-run",
        help="Check a reviewed counterexample with real Lean and independent replay",
    )
    cex_run.add_argument("counterexample_id")
    cex_run.add_argument("--plan-fingerprint", required=True)
    cex_run.add_argument("--reviewer", required=True)
    cex_run.add_argument("--confirmed", action="store_true")
    cex_report = commands.add_parser(
        "counterexample-report", help="Read and audit historical refutation evidence"
    )
    cex_report.add_argument("counterexample_id")
    matching = commands.add_parser(
        "structure-export-match",
        help="Match current standalone exports to a complete target; no Lean",
    )
    matching.add_argument("--input", required=True)
    obligation = commands.add_parser(
        "structure-obligation-propose", help="Propose bounded checked equality obligations; no Lean"
    )
    obligation.add_argument("--input", required=True)
    for prefix in ("structure-lemma-export-", "structure-export-application-"):
        for action in (
            "catalog",
            "preview",
            "submit",
            "work",
            "report",
            "list",
            "cancel",
            "revoke",
            "recover",
        ):
            command = commands.add_parser(prefix + action)
            if action in ("preview", "submit", "revoke"):
                command.add_argument("--input", required=True)
            if action in ("report", "cancel", "revoke"):
                command.add_argument("run_id")
            if action == "work":
                command.add_argument("--run-id")
    for action in (
        "catalog",
        "preview",
        "submit",
        "work",
        "report",
        "list",
        "cancel",
        "revoke",
        "recover",
    ):
        command = commands.add_parser("structure-recursion-compact-" + action)
        if action in ("preview", "submit", "revoke"):
            command.add_argument("--input", required=True)
        if action in ("report", "cancel", "revoke"):
            command.add_argument("run_id")
        if action == "work":
            command.add_argument("--run-id")
    for action in (
        "propose",
        "catalog",
        "preview",
        "submit",
        "work",
        "report",
        "list",
        "cancel",
        "revoke",
        "recover",
    ):
        command = commands.add_parser("structure-recursion-" + action)
        if action in ("propose", "preview", "submit", "revoke"):
            command.add_argument("--input", required=True)
        if action in ("report", "cancel", "revoke"):
            command.add_argument("run_id")
        if action == "work":
            command.add_argument("--run-id")
    for kind in ("plan", "node"):
        command = commands.add_parser(
            "structure-strategy-v4-" + kind,
            help="Propose scope-safe, prioritized structure programs; no Lean",
        )
        command.add_argument("--input", required=True)
        if kind == "node":
            command.add_argument("graph_id")
    for action in (
        "catalog",
        "list",
        "recover",
        "preview",
        "submit",
        "work",
        "report",
        "cancel",
        "revoke",
    ):
        command = commands.add_parser(
            "structure-" + action,
            help="Review explicit carriers, laws and controlled natural induction",
        )
        if action in ("preview", "submit", "revoke"):
            command.add_argument("--input", required=True)
        if action in ("report", "cancel", "revoke"):
            command.add_argument("run_id")
        if action == "work":
            command.add_argument("--run-id")
    for action in (
        "catalog",
        "list",
        "recover",
        "preview",
        "submit",
        "work",
        "report",
        "cancel",
        "revoke",
    ):
        command = commands.add_parser(
            "typed-" + action, help="Review and check explicit numeric types and closed definitions"
        )
        if action in ("preview", "submit", "revoke"):
            command.add_argument("--input", required=True)
        if action in ("report", "cancel", "revoke"):
            command.add_argument("run_id")
        if action == "work":
            command.add_argument("--run-id")
    commands.add_parser(
        "function-dynamics-evaluation-prepare",
        help="Freeze public-source iteration targets and equal legacy/current budgets",
    )
    dynamics_eval_run = commands.add_parser(
        "function-dynamics-evaluation-run",
        help="Execute the named real function-dynamics diagnostic",
    )
    dynamics_eval_run.add_argument("evaluation_id")
    dynamics_eval_run.add_argument("--plan-fingerprint", required=True)
    dynamics_eval_run.add_argument("--reviewer", required=True)
    dynamics_eval_run.add_argument("--confirmed", action="store_true")
    dynamics_eval_report = commands.add_parser(
        "function-dynamics-evaluation-report",
        help="Reaudit current strategy evidence, failures and complete denominators",
    )
    dynamics_eval_report.add_argument("evaluation_id")
    commands.add_parser(
        "structure-strategy-evaluation-prepare",
        help="Freeze complete diagnostic targets, generated programs and equal attempt ceilings",
    )
    strategy_eval_run = commands.add_parser(
        "structure-strategy-evaluation-run",
        help="Execute one named fixed-budget real strategy diagnostic",
    )
    strategy_eval_run.add_argument("evaluation_id")
    strategy_eval_run.add_argument("--plan-fingerprint", required=True)
    strategy_eval_run.add_argument("--reviewer", required=True)
    strategy_eval_run.add_argument("--confirmed", action="store_true")
    strategy_eval_report = commands.add_parser(
        "structure-strategy-evaluation-report",
        help="Reaudit current strategy evidence, failures and complete denominators",
    )
    strategy_eval_report.add_argument("evaluation_id")
    strategy_plan = commands.add_parser(
        "structure-strategy-plan",
        help="Propose bounded complete-target structure programs; no Lean",
    )
    strategy_plan.add_argument("--input", required=True)
    strategy_node = commands.add_parser(
        "structure-strategy-node",
        help="Propose programs and read actual feedback for an existing structure node",
    )
    strategy_node.add_argument("graph_id")
    strategy_node.add_argument("--input", required=True)
    for kind in ("plan", "node"):
        command = commands.add_parser(
            "structure-strategy-v2-" + kind,
            help="Propose structure programs using bounded recursive-index ordering; no Lean",
        )
        command.add_argument("--input", required=True)
        if kind == "node":
            command.add_argument("graph_id")
    for kind in ("plan", "node"):
        command = commands.add_parser(
            "structure-strategy-v3-" + kind,
            help="Propose bounded original-parameter generalization; no Lean",
        )
        command.add_argument("--input", required=True)
        if kind == "node":
            command.add_argument("graph_id")
    for action in (
        "catalog",
        "preview",
        "submit",
        "work",
        "report",
        "list",
        "cancel",
        "revoke",
        "recover",
    ):
        command = commands.add_parser("typed-reuse-" + action)
        if action in ("preview", "submit", "revoke"):
            command.add_argument("--input", required=True)
        if action in ("report", "cancel", "revoke"):
            command.add_argument("run_id")
        if action == "catalog":
            command.add_argument("graph_id")
            command.add_argument("node_id")
        if action == "work":
            command.add_argument("--run-id")
    for action in (
        "catalog",
        "preview",
        "submit",
        "work",
        "report",
        "list",
        "cancel",
        "revoke",
        "recover",
    ):
        command = commands.add_parser("structure-reuse-" + action)
        if action in ("preview", "submit", "revoke"):
            command.add_argument("--input", required=True)
        if action in ("report", "cancel", "revoke"):
            command.add_argument("run_id")
        if action == "catalog":
            command.add_argument("graph_id")
            command.add_argument("node_id")
        if action == "work":
            command.add_argument("--run-id")
    for action in (
        "preview",
        "create",
        "catalog",
        "report",
        "node-preview",
        "submit",
        "cancel",
        "withdraw",
        "work",
        "recover",
    ):
        command = commands.add_parser("structure-research-" + action)
        if action in ("preview", "create", "node-preview", "submit", "cancel", "withdraw"):
            command.add_argument("--input", required=True)
        if action in ("report", "node-preview", "submit", "cancel", "withdraw"):
            command.add_argument("graph_id")
        if action == "catalog":
            command.add_argument("project_id")
            command.add_argument("--branch-id", default="main")
        if action == "work":
            command.add_argument("--graph-id")
    for action in (
        "preview",
        "create",
        "catalog",
        "report",
        "node-preview",
        "submit",
        "cancel",
        "withdraw",
        "work",
        "recover",
    ):
        command = commands.add_parser("typed-research-" + action)
        if action in ("preview", "create", "node-preview", "submit", "cancel", "withdraw"):
            command.add_argument("--input", required=True)
        if action in ("report", "node-preview", "submit", "cancel", "withdraw"):
            command.add_argument("graph_id")
        if action == "catalog":
            command.add_argument("project_id")
            command.add_argument("--branch-id", default="main")
        if action == "work":
            command.add_argument("--graph-id")
    for action in ("catalog", "list", "recover"):
        commands.add_parser(
            "lean-strategy-" + action, help="Inspect or recover fixed Lean strategy experiments"
        )
    for action in ("preview", "submit"):
        command = commands.add_parser(
            "lean-strategy-" + action, help="Review or submit one exact Nat strategy request"
        )
        command.add_argument("--input", required=True)
    strategy_work = commands.add_parser(
        "lean-strategy-work", help="Execute one confirmed fixed strategy, no retries"
    )
    strategy_work.add_argument("--run-id")
    for action in ("report", "cancel", "revoke"):
        command = commands.add_parser(
            "lean-strategy-" + action, help="Read, cancel or revoke strategy evidence"
        )
        command.add_argument("run_id")
        if action == "revoke":
            command.add_argument("--input", required=True)
    for action in ("preview", "create"):
        command = commands.add_parser(
            "research-flow-" + action, help="Prepare a project-bound research graph without Lean"
        )
        command.add_argument("--input", required=True)
    flow_catalog = commands.add_parser(
        "research-flow-catalog", help="List research tasks in one project branch"
    )
    flow_catalog.add_argument("project_id")
    flow_catalog.add_argument("--branch-id", default="main")
    for action in ("report", "propose", "submit", "cancel", "withdraw"):
        command = commands.add_parser(
            "research-flow-" + action, help="Review a research route or inspect exact evidence"
        )
        command.add_argument("flow_id")
        if action != "report":
            command.add_argument("--input", required=True)
    for action in ("work", "recover"):
        commands.add_parser(
            "research-flow-" + action, help="Run one confirmed check or recover without retry"
        )
    for action in ("report", "preview", "bind", "withdraw"):
        command = commands.add_parser("research-evidence-" + action)
        command.add_argument("flow_id")
        if action != "report":
            command.add_argument("--input", required=True)
    for action in ("report", "export", "catalog", "preview", "bind", "withdraw"):
        command = commands.add_parser("research-composition-" + action)
        command.add_argument("flow_id")
        if action == "catalog":
            command.add_argument("node_id")
        elif action not in ("report", "export"):
            command.add_argument("--input", required=True)
    for action in ("report", "preview", "create", "propose", "submit", "cancel", "withdraw"):
        command = commands.add_parser("research-continuation-" + action)
        command.add_argument("flow_id")
        if action not in ("report", "preview"):
            command.add_argument("--input", required=True)
    for action in ("work", "recover"):
        commands.add_parser("research-continuation-" + action)
    for action in ("preview", "create"):
        command = commands.add_parser("proof-steps-" + action)
        command.add_argument("--input", required=True)
    command = commands.add_parser("proof-steps-catalog")
    command.add_argument("flow_id")
    command.add_argument("node_id")
    for action in ("report", "step-preview", "submit", "cancel"):
        command = commands.add_parser("proof-steps-" + action)
        command.add_argument("scope_id")
        if action != "report":
            command.add_argument("--input", required=True)
    for action in ("work", "recover"):
        commands.add_parser("proof-steps-" + action)
    paper_plan = commands.add_parser(
        "paper-review-plan",
        help="Inspect manually annotated single-theorem obligations; no Lean/model",
    )
    paper_plan.add_argument("--input", required=True)
    paper_run = commands.add_parser(
        "paper-review", help="Run a confirmed single-theorem review using real Lean"
    )
    paper_run.add_argument("--input", required=True)
    paper_run.add_argument("--plan-fingerprint", required=True)
    paper_run.add_argument("--reviewer", required=True)
    paper_run.add_argument("--request-id", required=True)
    paper_report = commands.add_parser(
        "paper-review-report", help="Read a paper review with historical artifact checks"
    )
    paper_report.add_argument("review_id")
    knowledge_plan = commands.add_parser(
        "knowledge-plan",
        help="Inspect private admission of PROVED/E4 paper obligations; no Lean/model",
    )
    knowledge_plan.add_argument("review_id")
    knowledge_admit = commands.add_parser(
        "knowledge-admit", help="Archive confirmed exact paper knowledge privately"
    )
    knowledge_admit.add_argument("review_id")
    knowledge_admit.add_argument("--plan-fingerprint", required=True)
    knowledge_admit.add_argument("--reviewer", required=True)
    knowledge_admit.add_argument("--request-id", required=True)
    knowledge_admit.add_argument("--rights-confirmed", action="store_true")
    knowledge_search = commands.add_parser(
        "knowledge-search", help="Search private knowledge after checking source and archived bytes"
    )
    knowledge_search.add_argument("--query", default="")
    knowledge_search.add_argument("--include-inactive", action="store_true")
    knowledge_report = commands.add_parser(
        "knowledge-report", help="Read provenance, dependencies and archived evidence metadata"
    )
    knowledge_report.add_argument("item_id")
    for action in ("withdraw", "remove"):
        command = commands.add_parser(
            "knowledge-" + action,
            help="Withdraw or remove a private item with dependency propagation",
        )
        command.add_argument("item_id")
        command.add_argument("--reviewer", required=True)
        command.add_argument("--reason", required=True)
        command.add_argument("--confirmed", action="store_true")
    for command in (dataset, references, model_benchmark):
        command.add_argument("--split", choices=(*SPLITS, "all"), default="regression")
        command.add_argument("--include-reserved", action="store_true")
    report = commands.add_parser(
        "report", help="Read a saved report (historical record, not a fresh attestation)"
    )
    report.add_argument("directory")
    formalize = commands.add_parser(
        "formalize",
        help="Preview structured candidates using explicit local fixtures; no model call or proof",
    )
    formalize.add_argument("--input", required=True, help="Versioned source JSON")
    formalize.add_argument("--kind", choices=("claim", "step"), default="claim")
    formalize.add_argument("--timeout", type=float, default=30.0)
    formalize.add_argument(
        "--revise", help="Prior draft directory; input must be its next source version"
    )
    draft_report = commands.add_parser(
        "formalization-report", help="Read and validate a saved candidate bundle"
    )
    draft_report.add_argument("directory")
    review = commands.add_parser(
        "review-formalization", help="Record a named semantic review; grants no Lean evidence"
    )
    review.add_argument("directory")
    review.add_argument("--draft-fingerprint", required=True)
    review.add_argument(
        "--candidate", required=True, help="Exact candidate fingerprint displayed in the draft"
    )
    review.add_argument("--reviewer", required=True)
    review.add_argument("--decision", choices=("accept", "reject"), required=True)
    review.add_argument("--note", required=True)
    review.add_argument("--event-id", help="Stable id for an idempotent retry")
    revoke = commands.add_parser(
        "revoke-formalization", help="Withdraw an exact review; earlier decisions do not revive"
    )
    revoke.add_argument("directory")
    revoke.add_argument("--draft-fingerprint", required=True)
    revoke.add_argument("--candidate", required=True)
    revoke.add_argument("--review-id", required=True)
    revoke.add_argument("--reviewer", required=True)
    revoke.add_argument("--note", required=True)
    revoke.add_argument("--event-id", help="Stable id for an idempotent retry")
    register = commands.add_parser(
        "register-formalization", help="Register a legacy bundle; old review files stay historical"
    )
    register.add_argument("directory")
    register.add_argument("--revise", help="Registered parent for a legacy revision")
    submit = commands.add_parser(
        "task-submit", help="Queue an exact repository reference; no Lean execution"
    )
    submit.add_argument("case_id")
    submit.add_argument("--request-id", required=True, help="Stable idempotency key")
    submit.add_argument("--suite", choices=("golden", "nat-arithmetic-v1"), default="golden")
    submit.add_argument("--timeout", type=float, default=30.0)
    submit.add_argument(
        "--reviewer", help="Explicit local assertion of this exact fixture's alignment"
    )
    submit_certificate = commands.add_parser(
        "task-submit-certificate", help="Queue bounded certificate data for a fixed target"
    )
    submit_certificate.add_argument("case_id")
    submit_certificate.add_argument("--input", required=True)
    submit_certificate.add_argument("--request-id", required=True, help="Stable idempotency key")
    submit_certificate.add_argument("--timeout", type=float, default=30.0)
    submit_certificate.add_argument(
        "--reviewer", help="Explicit local assertion of this exact target's alignment"
    )
    task_report = commands.add_parser(
        "task-report", help="Read durable state and ordered events; historical evidence"
    )
    task_report.add_argument("task_id")
    cancel = commands.add_parser(
        "task-cancel", help="Cancel queued work or request running checker termination"
    )
    cancel.add_argument("task_id")
    retry = commands.add_parser(
        "task-retry", help="Explicit bounded retry of an exact failed attempt"
    )
    retry.add_argument("task_id")
    retry.add_argument("--attempt-id", required=True)
    retry.add_argument("--request-id", required=True)
    work = commands.add_parser(
        "task-work", help="Execute at most one queued task; local single worker"
    )
    work.add_argument("--task-id", help="Only execute this task if queued")
    commands.add_parser(
        "task-recover", help="Mark interrupted attempts failed; never import old evidence"
    )
    commands.add_parser("research-list", help="List private research projects")
    for action in ("create", "edit", "fork", "prepare-binding", "bind", "withdraw"):
        command = commands.add_parser(
            "research-" + action, help="Versioned private research " + action
        )
        if action != "create":
            command.add_argument("project_id")
        command.add_argument(
            "--input", required=True, help="Strict JSON request including version and request ID"
        )
    for action in ("report", "history", "export"):
        command = commands.add_parser("research-" + action)
        command.add_argument("project_id")
        if action != "report":
            command.add_argument("branch_id")
        if action == "export":
            command.add_argument("node_id")
    for action in ("plan", "submit", "list", "report", "cancel", "work", "recover"):
        command = commands.add_parser("research-run-" + action)
        if action in ("plan", "submit", "list"):
            command.add_argument("project_id")
        if action in ("report", "cancel"):
            command.add_argument("run_id")
        if action == "work":
            command.add_argument("--run-id")
        if action in ("plan", "submit"):
            command.add_argument("--input", required=True)
    for action in (
        "preview",
        "create",
        "list",
        "report",
        "plan",
        "submit",
        "work",
        "cancel",
        "run-report",
        "recover",
    ):
        command = commands.add_parser("knowledge-instance-" + action)
        if action in ("report", "plan", "submit"):
            command.add_argument("instance_id")
        if action in ("cancel", "run-report"):
            command.add_argument("run_id")
        if action == "work":
            command.add_argument("--run-id")
        if action in ("preview", "create", "plan", "submit"):
            command.add_argument("--input", required=True)
    for action in (
        "catalog",
        "search",
        "preview",
        "submit",
        "list",
        "report",
        "work",
        "cancel",
        "revoke",
        "recover",
    ):
        command = commands.add_parser(
            "number-theory-" + action, help="Controlled gcd and coprimality experiments"
        )
        if action in ("search", "preview", "submit", "revoke"):
            command.add_argument("--input", required=True)
        if action in ("report", "cancel", "revoke"):
            command.add_argument("run_id")
        if action == "work":
            command.add_argument("--run-id")
    for action in (
        "catalog",
        "preview",
        "submit",
        "list",
        "report",
        "work",
        "cancel",
        "revoke",
        "recover",
    ):
        command = commands.add_parser(
            "mathlib-" + action, help="Controlled Nat divisibility library terms"
        )
        if action in ("preview", "submit", "revoke"):
            command.add_argument("--input", required=True)
        if action in ("report", "cancel", "revoke"):
            command.add_argument("run_id")
        if action == "work":
            command.add_argument("--run-id")
    for action in (
        "report",
        "history",
        "export",
        "edit",
        "preview-attachment",
        "attach",
        "withdraw",
    ):
        command = commands.add_parser(
            "research-mathlib-" + action, help="Versioned Mathlib research nodes"
        )
        command.add_argument("project_id")
        if action in ("report", "history", "export"):
            command.add_argument("branch_id")
        else:
            command.add_argument("--input", required=True)
    for action in (
        "prepare",
        "admit",
        "search",
        "report",
        "export",
        "request",
        "withdraw",
        "remove",
    ):
        command = commands.add_parser(
            "mathlib-knowledge-" + action, help="Private verified Mathlib archives"
        )
        if action in ("prepare", "admit", "withdraw", "remove"):
            command.add_argument("--input", required=True)
        if action in ("report", "export", "request", "withdraw", "remove"):
            command.add_argument("item_id")
        if action == "search":
            command.add_argument("--query", default="")
            command.add_argument("--include-inactive", action="store_true")
    for action in (
        "preview",
        "create",
        "search",
        "report",
        "export",
        "request",
        "preview-attachment",
        "attach",
        "withdraw",
        "remove",
    ):
        command = commands.add_parser(
            "mathlib-reuse-" + action, help="Explicit private Mathlib lemma specialization"
        )
        if action in ("preview", "create", "preview-attachment", "attach", "withdraw", "remove"):
            command.add_argument("--input", required=True)
        if action in (
            "report",
            "export",
            "request",
            "preview-attachment",
            "attach",
            "withdraw",
            "remove",
        ):
            command.add_argument("reuse_id")
        if action == "search":
            command.add_argument("--query", default="")
            command.add_argument("--include-inactive", action="store_true")
    for action in (
        "catalog",
        "list",
        "recover",
        "preview",
        "submit",
        "work",
        "report",
        "export",
        "cancel",
        "revoke",
    ):
        command = commands.add_parser(
            "lemma-compose-" + action,
            help="Compose private theorem proof data with fresh kernel checks",
        )
        if action in ("preview", "submit", "revoke"):
            command.add_argument("--input", required=True)
        if action in ("report", "export", "cancel", "revoke"):
            command.add_argument("run_id")
        if action == "work":
            command.add_argument("--run-id")
    for action in (
        "catalog",
        "list",
        "preview",
        "submit",
        "work",
        "recover",
        "report",
        "export",
        "cancel",
        "revoke",
    ):
        command = commands.add_parser("library-compose-" + action)
        if action in ("preview", "submit", "revoke"):
            command.add_argument("--input", required=True)
        if action in ("report", "export", "cancel", "revoke"):
            command.add_argument("run_id")
        if action == "work":
            command.add_argument("--run-id")
    for action in ("preview", "admit", "list", "report", "withdraw", "remove"):
        command = commands.add_parser("proof-library-" + action)
        if action in ("preview", "admit", "withdraw", "remove"):
            command.add_argument("--input", required=True)
        if action in ("report", "withdraw", "remove"):
            command.add_argument("item_id")
        if action == "list":
            command.add_argument("--query", default="")
            command.add_argument("--include-inactive", action="store_true")
    for action in (
        "catalog",
        "preview",
        "submit",
        "list",
        "report",
        "export",
        "cancel",
        "withdraw",
        "work",
        "recover",
    ):
        command = commands.add_parser("campaign-" + action)
        if action in ("preview", "submit", "withdraw"):
            command.add_argument("--input", required=True)
        if action in ("report", "export", "cancel", "withdraw"):
            command.add_argument("campaign_id")
        if action == "work":
            command.add_argument("--campaign-id")
        if action == "catalog":
            command.add_argument("flow_id")
        if action == "list":
            command.add_argument("--flow-id")
    args = parser.parse_args()
    if args.command.startswith("campaign-"):
        from .research_campaign import ResearchCampaignStore

        try:
            store = ResearchCampaignStore(ROOT)
            action = args.command.removeprefix("campaign-")
            if hasattr(args, "input"):
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Campaign input exceeds 192 KiB")
                data = strict_json(path.read_text(encoding="utf-8"))
            if action in ("preview", "submit"):
                result = getattr(store, action)(data)
            elif action == "withdraw":
                result = store.withdraw(args.campaign_id, data)
            elif action in ("report", "export", "cancel"):
                result = getattr(store, "report" if action == "export" else action)(
                    args.campaign_id
                )
            elif action == "catalog":
                result = store.catalog(args.flow_id)
            elif action == "list":
                result = {"runs": store.list_runs(args.flow_id)}
            elif action == "work":
                result = store.work_once(args.campaign_id)
            else:
                result = store.recover()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command.startswith(("proof-library-", "library-compose-")):
        from .proof_library import ProofLibraryStore

        try:
            library = ProofLibraryStore(ROOT)
            archive_action = args.command.startswith("proof-library-")
            action = args.command.removeprefix(
                "proof-library-" if archive_action else "library-compose-"
            )
            if hasattr(args, "input"):
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Proof library input exceeds 192 KiB")
                data = strict_json(path.read_text(encoding="utf-8"))
            if archive_action:
                if action in ("withdraw", "remove"):
                    result = library.retire(args.item_id, data, remove=action == "remove")
                elif action == "report":
                    result = library.report(args.item_id)
                elif action == "list":
                    result = library.search(args.query, args.include_inactive)
                else:
                    result = getattr(library, "prepare" if action == "preview" else action)(data)
            else:
                store = library.compositions
                if action in ("preview", "submit"):
                    result = getattr(store, action)(data)
                elif action == "revoke":
                    result = store.revoke(args.run_id, data)
                elif action in ("report", "export", "cancel"):
                    result = getattr(store, "report" if action == "export" else action)(args.run_id)
                elif action == "work":
                    result = store.work_once(args.run_id)
                elif action == "list":
                    result = {"runs": store.list_runs()}
                else:
                    result = getattr(store, action)()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command.startswith("lemma-compose-"):
        from .lemma_composition import LemmaCompositionStore

        try:
            store = LemmaCompositionStore(ROOT)
            action = args.command.removeprefix("lemma-compose-")
            if action in ("preview", "submit", "revoke"):
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Composition input exceeds 192 KiB")
                data = strict_json(path.read_text(encoding="utf-8"))
                result = (
                    store.revoke(args.run_id, data)
                    if action == "revoke"
                    else getattr(store, action)(data)
                )
            elif action in ("report", "export", "cancel"):
                result = getattr(store, "report" if action == "export" else action)(args.run_id)
            elif action == "work":
                result = store.work_once(args.run_id)
            elif action == "list":
                result = {"runs": store.list_runs()}
            else:
                result = getattr(store, action)()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command.startswith("research-continuation-"):
        from .research_continuation import ResearchContinuationStore
        from .formalizer import exact_object

        try:
            store = ResearchContinuationStore(ROOT)
            action = args.command.removeprefix("research-continuation-")
            if action in ("work", "recover"):
                result = store.work_once() if action == "work" else store.recover()
            elif action in ("report", "preview"):
                result = getattr(store, action)(args.flow_id)
            else:
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Continuation input exceeds 192 KiB")
                data = strict_json(path.read_text(encoding="utf-8"))
                if action == "create":
                    exact_object(data, {"request_id", "plan_fingerprint"}, "Save continuation")
                    result = store.create({**data, "flow_id": args.flow_id})
                elif action == "cancel":
                    exact_object(data, {"attempt_id"}, "Cancel continuation")
                    result = store.cancel(args.flow_id, data["attempt_id"])
                else:
                    result = getattr(store, action)(args.flow_id, data)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command.startswith("research-composition-"):
        from .research_compositions import ResearchCompositionStore

        try:
            store = ResearchCompositionStore(ROOT)
            action = args.command.removeprefix("research-composition-")
            if action in ("report", "export"):
                result = store.report(args.flow_id)
            elif action == "catalog":
                result = store.catalog(args.flow_id, args.node_id)
            else:
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Composition association input exceeds 192 KiB")
                result = getattr(store, action)(
                    args.flow_id, strict_json(path.read_text(encoding="utf-8"))
                )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command.startswith("research-evidence-"):
        from .research_evidence import ResearchEvidenceStore

        try:
            store = ResearchEvidenceStore(ROOT)
            action = args.command.removeprefix("research-evidence-")
            if action == "report":
                result = store.report(args.flow_id)
            else:
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Research evidence input exceeds 192 KiB")
                result = getattr(store, action)(
                    args.flow_id, strict_json(path.read_text(encoding="utf-8"))
                )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command.startswith("proof-steps-"):
        from .proof_steps import ProofStepStore

        try:
            store = ProofStepStore(ROOT)
            action = args.command.removeprefix("proof-steps-")
            if action in ("preview", "create", "step-preview", "submit", "cancel"):
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Proof-step input exceeds 192 KiB")
                data = strict_json(path.read_text(encoding="utf-8"))
                if action in ("preview", "create"):
                    result = getattr(store, action)(data)
                elif action == "step-preview":
                    result = store.preview_step(args.scope_id, data)
                elif action == "cancel":
                    if set(data) != {"attempt_id"}:
                        raise ValueError("Cancel requires only attempt_id")
                    result = store.cancel(args.scope_id, data["attempt_id"])
                else:
                    result = store.submit(args.scope_id, data)
            elif action == "catalog":
                result = store.catalog(args.flow_id, args.node_id)
            elif action == "report":
                result = store.report(args.scope_id)
            else:
                result = store.work_once() if action == "work" else store.recover()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command.startswith("research-flow-"):
        from .research_flow import ResearchFlowStore

        try:
            store = ResearchFlowStore(ROOT)
            action = args.command.removeprefix("research-flow-")
            if action in ("preview", "create", "propose", "submit", "cancel", "withdraw"):
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Research flow input exceeds 192 KiB")
                data = strict_json(path.read_text(encoding="utf-8"))
                if action == "cancel":
                    from .formalizer import exact_object

                    exact_object(data, {"attempt_id"}, "Cancel research check")
                    result = store.cancel(args.flow_id, data["attempt_id"])
                elif action in ("preview", "create"):
                    result = getattr(store, action)(data)
                else:
                    result = getattr(store, action)(args.flow_id, data)
            elif action == "catalog":
                result = store.catalog(args.project_id, args.branch_id)
            elif action == "report":
                result = store.report(args.flow_id)
            else:
                result = store.work_once() if action == "work" else store.recover()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command in ("mathlib-benchmark", "mathlib-benchmark-report"):
        from .mathlib_evaluation import (
            evaluation_plan as mathlib_evaluation_plan,
            read_evaluation,
            run_evaluation,
        )

        try:
            if args.command == "mathlib-benchmark-report":
                result = read_evaluation(ROOT, args.evaluation_id)
            elif args.plan:
                result = mathlib_evaluation_plan(ROOT, args.split)
            else:
                result = run_evaluation(
                    ROOT,
                    split=args.split,
                    plan_fingerprint=args.plan_fingerprint,
                    reviewer=args.reviewer,
                    confirmed=args.confirmed,
                )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return (
                0
                if result.get("state") not in ("INTERRUPTED", "EXECUTION_ERROR", "UNAVAILABLE")
                else 2
            )
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(f"MRA Mathlib evaluation error: {error}", file=sys.stderr)
            return 2
    if args.command.startswith("mathlib-reuse-"):
        from .mathlib_reuse import MathlibReuseStore

        try:
            store = MathlibReuseStore(ROOT)
            action = args.command.removeprefix("mathlib-reuse-")
            if action in (
                "preview",
                "create",
                "preview-attachment",
                "attach",
                "withdraw",
                "remove",
            ):
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Mathlib reuse request exceeds 192 KiB")
                data = strict_json(path.read_text(encoding="utf-8"))
                if action in ("preview", "create"):
                    result = getattr(store, action)(data)
                elif action in ("withdraw", "remove"):
                    result = store.retire(args.reuse_id, data, remove=action == "remove")
                else:
                    result = getattr(store, action.replace("-", "_"))(args.reuse_id, data)
            elif action == "search":
                result = store.search(args.query, args.include_inactive)
            else:
                result = getattr(store, "report" if action == "export" else action)(args.reuse_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, sqlite3.Error) as error:
            print(f"Mathlib reuse error: {error}", file=sys.stderr)
            return 2
    if args.command.startswith("mathlib-knowledge-"):
        from .mathlib_knowledge import MathlibKnowledgeStore

        try:
            store = MathlibKnowledgeStore(ROOT)
            action = args.command.removeprefix("mathlib-knowledge-")
            if action in ("prepare", "admit", "withdraw", "remove"):
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Mathlib knowledge request exceeds 192 KiB")
                data = strict_json(path.read_text(encoding="utf-8"))
                result = (
                    store.retire(args.item_id, data, remove=action == "remove")
                    if action in ("withdraw", "remove")
                    else getattr(store, action)(data)
                )
            elif action == "search":
                result = store.search(args.query, args.include_inactive)
            elif action == "request":
                result = store.recheck_request(args.item_id)
            else:
                result = store.report(args.item_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, sqlite3.Error) as error:
            print(f"Mathlib knowledge error: {error}", file=sys.stderr)
            return 2
    if args.command.startswith("research-mathlib-"):
        from .research_mathlib import ResearchMathlibStore

        try:
            store = ResearchMathlibStore(ROOT)
            action = args.command.removeprefix("research-mathlib-")
            method = getattr(store, action.replace("-", "_"))
            if action in ("report", "history", "export"):
                result = method(args.project_id, args.branch_id)
            else:
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Mathlib research request exceeds 192 KiB")
                result = method(args.project_id, strict_json(path.read_text(encoding="utf-8")))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, sqlite3.Error) as error:
            print(f"Mathlib research error: {error}", file=sys.stderr)
            return 2
    if args.command.startswith("typed-research-"):
        from .typed_research import TypedResearchStore

        try:
            store = TypedResearchStore(ROOT)
            action = args.command.removeprefix("typed-research-")
            if action in ("preview", "create", "node-preview", "submit", "cancel", "withdraw"):
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Typed research input exceeds 192 KiB")
                data = strict_json(path.read_text(encoding="utf-8"))
                method = getattr(store, action.replace("node-preview", "preview_node"))
                result = (
                    method(data) if action in ("preview", "create") else method(args.graph_id, data)
                )
            elif action == "catalog":
                result = store.catalog(args.project_id, args.branch_id)
            elif action == "report":
                result = store.report(args.graph_id)
            elif action == "work":
                result = store.work_once(args.graph_id)
            else:
                result = store.recover()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command.startswith("structure-reuse-evaluation-"):
        from .structure_reuse_evaluation import prepare, report, run

        try:
            if args.command == "structure-reuse-evaluation-prepare":
                result = prepare(ROOT)
            elif args.command == "structure-reuse-evaluation-run":
                result = run(
                    ROOT,
                    args.evaluation_id,
                    plan_fingerprint=args.plan_fingerprint,
                    reviewer=args.reviewer,
                    confirmed=args.confirmed,
                )
            else:
                result = report(ROOT, args.evaluation_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, sqlite3.Error) as error:
            print(f"Proof reuse evaluation error: {error}", file=sys.stderr)
            return 2
    if args.command.startswith("typed-reuse-evaluation-"):
        from .typed_reuse_evaluation import prepare, report, run

        try:
            if args.command == "typed-reuse-evaluation-prepare":
                result = prepare(ROOT)
            elif args.command == "typed-reuse-evaluation-run":
                result = run(
                    ROOT,
                    args.evaluation_id,
                    plan_fingerprint=args.plan_fingerprint,
                    reviewer=args.reviewer,
                    confirmed=args.confirmed,
                )
            else:
                result = report(ROOT, args.evaluation_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, sqlite3.Error) as error:
            print(f"Proof reuse evaluation error: {error}", file=sys.stderr)
            return 2
    if args.command.startswith("typed-reuse-"):
        from .typed_reuse import TypedReuseStore

        try:
            store = TypedReuseStore(ROOT)
            action = args.command.removeprefix("typed-reuse-")
            if action in ("preview", "submit", "revoke"):
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Typed reuse input exceeds 192 KiB")
                data = strict_json(path.read_text(encoding="utf-8"))
                result = (
                    store.revoke(args.run_id, data)
                    if action == "revoke"
                    else getattr(store, action)(data)
                )
            elif action == "catalog":
                result = store.catalog(args.graph_id, args.node_id)
            elif action == "list":
                result = {"runs": store.list_runs()}
            elif action == "work":
                result = store.work_once(args.run_id)
            elif action in ("report", "cancel"):
                result = getattr(store, action)(args.run_id)
            else:
                result = store.recover()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command == "structure-lemma-suggestions":
        from .structure_lemma_suggestions import suggest
        from .structure_reuse import StructureReuseStore

        try:
            path = Path(args.input)
            if path.stat().st_size > 196608:
                raise ValueError("Lemma suggestion input exceeds 192 KiB")
            result = suggest(
                StructureReuseStore(ROOT), strict_json(path.read_text(encoding="utf-8"))
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command.startswith("structure-reuse-"):
        from .structure_reuse import StructureReuseStore

        try:
            store = StructureReuseStore(ROOT)
            action = args.command.removeprefix("structure-reuse-")
            if action in ("preview", "submit", "revoke"):
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Structure reuse input exceeds 192 KiB")
                data = strict_json(path.read_text(encoding="utf-8"))
                result = (
                    store.revoke(args.run_id, data)
                    if action == "revoke"
                    else getattr(store, action)(data)
                )
            elif action == "catalog":
                result = store.catalog(args.graph_id, args.node_id)
            elif action == "list":
                result = {"runs": store.list_runs()}
            elif action == "work":
                result = store.work_once(args.run_id)
            elif action in ("report", "cancel"):
                result = getattr(store, action)(args.run_id)
            else:
                result = store.recover()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command.startswith("function-dynamics-evaluation-"):
        from .function_dynamics_evaluation import prepare, report, run

        try:
            if args.command == "function-dynamics-evaluation-prepare":
                result = prepare(ROOT)
            elif args.command == "function-dynamics-evaluation-run":
                result = run(
                    ROOT,
                    args.evaluation_id,
                    plan_fingerprint=args.plan_fingerprint,
                    reviewer=args.reviewer,
                    confirmed=args.confirmed,
                )
            else:
                result = report(ROOT, args.evaluation_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command.startswith("structure-strategy-evaluation-"):
        from .structure_strategy_evaluation import prepare, report, run

        try:
            if args.command == "structure-strategy-evaluation-prepare":
                result = prepare(ROOT)
            elif args.command == "structure-strategy-evaluation-run":
                result = run(
                    ROOT,
                    args.evaluation_id,
                    plan_fingerprint=args.plan_fingerprint,
                    reviewer=args.reviewer,
                    confirmed=args.confirmed,
                )
            else:
                result = report(ROOT, args.evaluation_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command in (
        "structure-strategy-plan",
        "structure-strategy-node",
        "structure-strategy-v2-plan",
        "structure-strategy-v2-node",
        "structure-strategy-v3-plan",
        "structure-strategy-v3-node",
        "structure-strategy-v4-plan",
        "structure-strategy-v4-node",
    ):
        if "-v4-" in args.command:
            from .structure_strategies_v4 import propose, graph_proposals
        elif "-v3-" in args.command:
            from .structure_strategies_v3 import propose, graph_proposals
        elif "-v2-" in args.command:
            from .structure_strategies_v2 import propose, graph_proposals
        else:
            from .structure_strategies import propose, graph_proposals
        from .formalizer import exact_object
        from .structure_research import StructureResearchStore

        try:
            path = Path(args.input)
            if path.stat().st_size > 196608:
                raise ValueError("Structure strategy input exceeds 192 KiB")
            raw = strict_json(path.read_text(encoding="utf-8"))
            if args.command.endswith("-plan"):
                result = propose(ROOT, raw)
            else:
                exact_object(raw, {"node_id", "budget"}, "Structure node strategy request")
                result = graph_proposals(
                    StructureResearchStore(ROOT), {"graph_id": args.graph_id, **raw}
                )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command.startswith("structure-research-"):
        from .structure_graph_publication import (
            PublishedStructureResearchStore as StructureResearchStore,
        )

        try:
            store = StructureResearchStore(ROOT)
            action = args.command.removeprefix("structure-research-")
            if action in ("preview", "create", "node-preview", "submit", "cancel", "withdraw"):
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Structure research input exceeds 192 KiB")
                data = strict_json(path.read_text(encoding="utf-8"))
                method = getattr(store, action.replace("node-preview", "preview_node"))
                result = (
                    method(data) if action in ("preview", "create") else method(args.graph_id, data)
                )
            elif action == "catalog":
                result = store.catalog(args.project_id, args.branch_id)
            elif action == "report":
                result = store.report(args.graph_id)
            elif action == "work":
                result = store.work_once(args.graph_id)
            else:
                result = store.recover()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command == "structure-export-match":
        from .export_lemma_matching import suggest
        from .structure_lemma_library import ExportApplicationStore

        try:
            path = Path(args.input)
            if path.stat().st_size > 196608:
                raise ValueError("Matching input exceeds 192 KiB")
            result = suggest(
                ExportApplicationStore(ROOT), strict_json(path.read_text(encoding="utf-8"))
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command == "structure-obligation-propose":
        from .obligation_planning import propose

        try:
            path = Path(args.input)
            if path.stat().st_size > 196608:
                raise ValueError("Obligation input exceeds 192 KiB")
            result = propose(ROOT, strict_json(path.read_text(encoding="utf-8")))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command.startswith(("structure-lemma-export-", "structure-export-application-")):
        from .structure_lemma_library import LemmaExportStore, ExportApplicationStore

        try:
            prefix = (
                "structure-lemma-export-"
                if args.command.startswith("structure-lemma-export-")
                else "structure-export-application-"
            )
            action = args.command.removeprefix(prefix)
            data = None
            if action in ("preview", "submit", "revoke"):
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Lemma input exceeds 192 KiB")
                data = strict_json(path.read_text(encoding="utf-8"))
            store = (
                LemmaExportStore(ROOT)
                if prefix == "structure-lemma-export-"
                else ExportApplicationStore(ROOT)
            )
            if action in ("preview", "submit"):
                result = getattr(store, action)(data)
            elif action == "revoke":
                result = store.revoke(args.run_id, data)
            elif action in ("report", "cancel"):
                result = getattr(store, action)(args.run_id)
            elif action == "work":
                result = store.work_once(args.run_id)
            elif action == "list":
                result = {"runs": store.list_runs()}
            else:
                result = getattr(store, action)()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command.startswith("structure-recursion-compact-"):
        from .compact_recursion import CompactRecursionStore, prepare

        try:
            action = args.command.removeprefix("structure-recursion-compact-")
            data = None
            if action in ("preview", "submit", "revoke"):
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Compact recursion input exceeds 192 KiB")
                data = strict_json(path.read_text(encoding="utf-8"))
            if action == "preview":
                result = prepare(ROOT, data)
            else:
                store = CompactRecursionStore(ROOT)
                if action == "submit":
                    result = store.submit(data)
                elif action == "revoke":
                    result = store.revoke(args.run_id, data)
                elif action in ("report", "cancel"):
                    result = getattr(store, action)(args.run_id)
                elif action == "work":
                    result = store.work_once(args.run_id)
                elif action == "list":
                    result = {"runs": store.list_runs()}
                elif action == "catalog":
                    result = store.catalog()
                else:
                    result = store.recover()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command.startswith("structure-recursion-"):
        from .recursion_programs import RecursionStore, prepare
        from .recursion_strategies import propose

        try:
            action = args.command.removeprefix("structure-recursion-")
            data = None
            if action in ("propose", "preview", "submit", "revoke"):
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Recursion input exceeds 192 KiB")
                data = strict_json(path.read_text(encoding="utf-8"))
            if action == "propose":
                result = propose(ROOT, data)
            elif action == "preview":
                result = prepare(ROOT, data)
            else:
                store = RecursionStore(ROOT)
                if action == "submit":
                    result = store.submit(data)
                elif action == "revoke":
                    result = store.revoke(args.run_id, data)
                elif action in ("report", "cancel"):
                    result = getattr(store, action)(args.run_id)
                elif action == "work":
                    result = store.work_once(args.run_id)
                elif action == "list":
                    result = {"runs": store.list_runs()}
                elif action == "catalog":
                    result = store.catalog()
                else:
                    result = store.recover()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command.startswith("structure-"):
        from .structures import StructureStore, catalog, prepare

        try:
            action = args.command.removeprefix("structure-")
            data = None
            if action in ("preview", "submit", "revoke"):
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Structure input exceeds 192 KiB")
                data = strict_json(path.read_text(encoding="utf-8"))
            if action == "catalog":
                result = catalog()
            elif action == "preview":
                result = prepare(ROOT, data)
            else:
                store = StructureStore(ROOT)
                if action == "submit":
                    result = store.submit(data)
                elif action == "revoke":
                    result = store.revoke(args.run_id, data)
                elif action in ("report", "cancel"):
                    result = getattr(store, action)(args.run_id)
                elif action == "work":
                    result = store.work_once(args.run_id)
                elif action == "list":
                    result = {"runs": store.list_runs()}
                else:
                    result = store.recover()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command.startswith("typed-"):
        from .typed_definitions import TypedDefinitionStore, catalog, prepare

        try:
            action = args.command.removeprefix("typed-")
            data = None
            if action in ("preview", "submit", "revoke"):
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Typed input exceeds 192 KiB")
                data = strict_json(path.read_text(encoding="utf-8"))
            if action == "catalog":
                result = catalog()
            elif action == "preview":
                result = prepare(ROOT, data)
            else:
                store = TypedDefinitionStore(ROOT)
                if action == "submit":
                    result = store.submit(data)
                elif action == "revoke":
                    result = store.revoke(args.run_id, data)
                elif action in ("report", "cancel"):
                    result = getattr(store, action)(args.run_id)
                elif action == "work":
                    result = store.work_once(args.run_id)
                elif action == "list":
                    result = {"runs": store.list_runs()}
                else:
                    result = store.recover()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command.startswith("lean-strategy-"):
        from .lean_strategies import LeanStrategyStore

        try:
            store = LeanStrategyStore(ROOT)
            action = args.command.removeprefix("lean-strategy-")
            if action in ("preview", "submit", "revoke"):
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Lean strategy input exceeds 192 KiB")
                data = strict_json(path.read_text(encoding="utf-8"))
                result = (
                    store.revoke(args.run_id, data)
                    if action == "revoke"
                    else getattr(store, action)(data)
                )
            elif action in ("report", "cancel"):
                result = getattr(store, action)(args.run_id)
            elif action == "work":
                result = store.work_once(args.run_id)
            elif action == "list":
                result = {"runs": store.list_runs()}
            else:
                result = getattr(store, action)()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command.startswith("counterexample-"):
        from .counterexamples import prepare, report as counterexample_report, run, search

        try:
            if args.command in ("counterexample-search", "counterexample-prepare"):
                if Path(args.input).stat().st_size > 196608:
                    raise ValueError("Counterexample input exceeds 192 KiB")
                data = strict_json(Path(args.input).read_text(encoding="utf-8"))
                result = (
                    search(data, ROOT)
                    if args.command == "counterexample-search"
                    else prepare(ROOT, data)
                )
            elif args.command == "counterexample-run":
                result = run(
                    ROOT,
                    args.counterexample_id,
                    plan_fingerprint=args.plan_fingerprint,
                    reviewer=args.reviewer,
                    confirmed=args.confirmed,
                )
            else:
                result = counterexample_report(ROOT, args.counterexample_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command == "runtime-audit":
        from .runtime_audit import inspect

        try:
            result = inspect(ROOT, workers=args.workers)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["observed_integrity_ok"] else 2
        except (OSError, RuntimeError, ValueError) as error:
            print(f"Runtime audit error: {error}", file=sys.stderr)
            return 2
    if args.command.startswith("reuse-evaluation-"):
        from .reuse_evaluation import prepare, report, run

        try:
            if args.command == "reuse-evaluation-prepare":
                result = prepare(ROOT)
            elif args.command == "reuse-evaluation-run":
                result = run(
                    ROOT,
                    args.evaluation_id,
                    plan_fingerprint=args.plan_fingerprint,
                    reviewer=args.reviewer,
                    confirmed=args.confirmed,
                )
            else:
                result = report(ROOT, args.evaluation_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, sqlite3.Error) as error:
            print(f"Proof reuse evaluation error: {error}", file=sys.stderr)
            return 2
    if args.command.startswith("search-evaluation-"):
        from .search_evaluation import prepare_evaluation, read_evaluation, run_evaluation

        try:
            if args.command == "search-evaluation-prepare":
                result = prepare_evaluation(ROOT, args.split)
            elif args.command == "search-evaluation-run":
                result = run_evaluation(
                    ROOT,
                    args.evaluation_id,
                    plan_fingerprint=args.plan_fingerprint,
                    reviewer=args.reviewer,
                    confirmed=args.confirmed,
                )
            else:
                result = read_evaluation(ROOT, args.evaluation_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, sqlite3.Error) as error:
            print(f"Search evaluation error: {error}", file=sys.stderr)
            return 2
    if args.command == "number-theory-search":
        from .number_theory_search import search

        try:
            path = Path(args.input)
            if path.stat().st_size > 196608:
                raise ValueError("Number theory search input exceeds 192 KiB")
            result = search(strict_json(path.read_text(encoding="utf-8")), ROOT)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, RuntimeError, TypeError, KeyError) as error:
            print(f"Number theory search error: {error}", file=sys.stderr)
            return 2
    if args.command.startswith("number-theory-"):
        from .number_theory_lab import NumberTheoryStore

        try:
            store = NumberTheoryStore(ROOT)
            action = args.command.removeprefix("number-theory-")
            if action in ("preview", "submit", "revoke"):
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Number theory input exceeds 192 KiB")
                data = strict_json(path.read_text(encoding="utf-8"))
                result = (
                    store.revoke(args.run_id, data)
                    if action == "revoke"
                    else getattr(store, action)(data)
                )
            elif action == "list":
                result = {"runs": store.list_runs()}
            elif action in ("report", "cancel"):
                result = getattr(store, action)(args.run_id)
            elif action == "work":
                result = store.work_once(args.run_id)
            else:
                result = getattr(store, action)()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, sqlite3.Error) as error:
            print(f"Number theory experiment error: {error}", file=sys.stderr)
            return 2
    if args.command.startswith("mathlib-"):
        from .mathlib_lab import MathlibLabStore

        try:
            store = MathlibLabStore(ROOT)
            action = args.command.removeprefix("mathlib-")
            if action in ("preview", "submit", "revoke"):
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Mathlib input exceeds 192 KiB")
                data = strict_json(path.read_text(encoding="utf-8"))
                result = (
                    store.revoke(args.run_id, data)
                    if action == "revoke"
                    else getattr(store, action)(data)
                )
            elif action == "list":
                result = {"runs": store.list_runs()}
            elif action in ("report", "cancel"):
                result = getattr(store, action)(args.run_id)
            elif action == "work":
                result = store.work_once(args.run_id)
            else:
                result = getattr(store, action)()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, sqlite3.Error) as error:
            print(f"Mathlib experiment error: {error}", file=sys.stderr)
            return 2
    if args.command.startswith("knowledge-instance-"):
        from .knowledge_instances import KnowledgeInstanceStore

        try:
            store = KnowledgeInstanceStore(ROOT)
            action = args.command.removeprefix("knowledge-instance-")
            if action in ("preview", "create", "plan", "submit"):
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Instance request exceeds 192 KiB")
                data = strict_json(path.read_text(encoding="utf-8"))
                method = getattr(store, "prepare" if action == "plan" else action)
                result = (
                    method(args.instance_id, data) if action in ("plan", "submit") else method(data)
                )
            elif action == "list":
                result = {"instances": store.list_instances()}
            elif action == "report":
                result = store.report_instance(args.instance_id)
            elif action == "work":
                result = store.work_once(args.run_id)
            elif action == "run-report":
                result = store.report(args.run_id)
            elif action == "cancel":
                result = store.cancel(args.run_id)
            else:
                result = store.recover()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, sqlite3.Error) as error:
            print(f"Knowledge instance error: {error}", file=sys.stderr)
            return 2
    if args.command.startswith("research-run-"):
        from .research_runs import ResearchRunStore

        try:
            store = ResearchRunStore(ROOT)
            action = args.command.removeprefix("research-run-")
            if action in ("plan", "submit"):
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Execution request exceeds 192 KiB")
                data = strict_json(path.read_text(encoding="utf-8"))
                method = store.prepare if action == "plan" else store.submit
                result = method(args.project_id, data)
            elif action == "list":
                result = {"runs": store.list_runs(args.project_id)}
            elif action == "work":
                result = store.work_once(args.run_id)
            elif action == "recover":
                result = store.recover()
            else:
                result = getattr(store, action)(args.run_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, sqlite3.Error) as error:
            print(f"Research execution error: {error}", file=sys.stderr)
            return 2
    if args.command.startswith("research-"):
        from .research import ResearchStore

        try:
            store = ResearchStore(ROOT)
            action = args.command.removeprefix("research-")
            if action == "list":
                result = {"projects": store.list_projects()}
            elif action == "report":
                result = store.report(args.project_id)
            elif action == "history":
                result = store.history(args.project_id, args.branch_id)
            elif action == "export":
                result = store.export_node(args.project_id, args.branch_id, args.node_id)
            else:
                path = Path(args.input)
                if path.stat().st_size > 196608:
                    raise ValueError("Research request exceeds 192 KiB")
                data = strict_json(path.read_text(encoding="utf-8"))
                method = getattr(
                    store, "prepare_binding" if action == "prepare-binding" else action
                )
                result = method(data) if action == "create" else method(args.project_id, data)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, sqlite3.Error) as error:
            print(f"Research error: {error}", file=sys.stderr)
            return 2
    if args.command.startswith("knowledge-"):
        from .knowledge import KnowledgeStore
        from .knowledge_instances import KnowledgeInstanceStore

        try:
            KnowledgeInstanceStore(ROOT).reconcile()
            store = KnowledgeStore(ROOT)
            if args.command == "knowledge-plan":
                result = store.prepare(args.review_id)
            elif args.command == "knowledge-admit":
                result = store.admit(
                    {
                        "review_id": args.review_id,
                        "request_id": args.request_id,
                        "plan_fingerprint": args.plan_fingerprint,
                        "reviewer": args.reviewer,
                        "confirmed": True,
                        "rights_confirmed": args.rights_confirmed,
                    }
                )
            elif args.command == "knowledge-search":
                result = store.search(args.query, args.include_inactive)
            elif args.command == "knowledge-report":
                result = store.report(args.item_id)
            else:
                result = store.action(
                    args.item_id,
                    {"reviewer": args.reviewer, "reason": args.reason, "confirmed": args.confirmed},
                    delete=args.command == "knowledge-remove",
                )
                KnowledgeInstanceStore(ROOT).reconcile()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, sqlite3.Error) as error:
            print(f"Knowledge error: {error}", file=sys.stderr)
            return 2
    if args.command in ("paper-review-plan", "paper-review", "paper-review-report"):
        from .paper_review import PaperReviewStore, prepare_review

        try:
            if args.command == "paper-review-report":
                result = PaperReviewStore(ROOT).report(args.review_id)
            else:
                path = Path(args.input)
                if path.stat().st_size > 65536:
                    raise ValueError("Paper annotations exceed 64 KiB")
                bundle = strict_json(path.read_text(encoding="utf-8"))
                if args.command == "paper-review-plan":
                    result = prepare_review(bundle)
                else:
                    store = PaperReviewStore(ROOT)
                    result = store.submit(
                        {
                            "request_id": args.request_id,
                            "bundle": bundle,
                            "plan_fingerprint": args.plan_fingerprint,
                            "reviewer": args.reviewer,
                            "confirmed": True,
                        }
                    )
                    if result["state"] == "QUEUED":
                        store.work_once(result["review_id"])
                    result = store.report(result["review_id"])
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, sqlite3.Error) as error:
            print(f"Paper review error: {error}", file=sys.stderr)
            return 2
    if args.command == "serve":
        from .http_api import serve

        try:
            serve(ROOT, args.port)
            return 0
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            print(f"MRA workbench error: {error}", file=sys.stderr)
            return 1
    if args.command in ("model-benchmark", "model-benchmark-report"):
        from .model_evaluation import (
            model_evaluation_plan,
            read_model_evaluation,
            run_model_evaluation,
        )

        try:
            if args.command == "model-benchmark-report":
                result = read_model_evaluation(ROOT, ROOT / args.directory)
                exit_code = 0 if result["integrity_ok"] else 2
            elif args.plan:
                result = model_evaluation_plan(
                    ROOT, args.split, include_reserved=args.include_reserved
                )
                exit_code = 0
            else:
                result = run_model_evaluation(
                    ROOT, args.split, include_reserved=args.include_reserved
                )
                exit_code = 0 if result["state"] == "COMPLETED" else 2
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return exit_code
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, sqlite3.Error) as error:
            print(f"MRA model evaluation error: {error}", file=sys.stderr)
            return 1
    if args.command.startswith("task-"):
        try:
            store = TaskStore(ROOT)
            if args.command == "task-submit":
                result = store.submit(
                    args.request_id,
                    args.case_id,
                    suite=args.suite,
                    timeout_seconds=args.timeout,
                    reviewer=args.reviewer,
                )
            elif args.command == "task-submit-certificate":
                result = store.submit_certificate(
                    args.request_id,
                    args.case_id,
                    read_certificate(Path(args.input)),
                    timeout_seconds=args.timeout,
                    reviewer=args.reviewer,
                )
            elif args.command == "task-report":
                result = store.report(args.task_id)
            elif args.command == "task-cancel":
                result = store.cancel(args.task_id)
            elif args.command == "task-retry":
                result = store.retry(args.task_id, args.attempt_id, args.request_id)
            elif args.command == "task-recover":
                result = {"recovered_task_ids": store.recover()}
            else:
                result = store.work_once(args.task_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if args.command == "task-work" and result.get("task"):
                return 2 if result["task"]["state"] in ("FAILED", "CANCELLED") else 0
            return 0
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, sqlite3.Error) as error:
            print(f"MRA task error: {error}", file=sys.stderr)
            return 1
    if args.command in ("dataset", "reference-benchmark"):
        try:
            if args.command == "dataset":
                result = evaluation_plan(
                    load_dataset(ROOT), args.split, include_reserved=args.include_reserved
                )
            else:
                result = run_reference_evaluation(
                    ROOT, args.split, include_reserved=args.include_reserved
                )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if args.command == "dataset" or result["all_passed"] else 2
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
            print(f"MRA evaluation error: {error}", file=sys.stderr)
            return 1
    if args.command in (
        "formalize",
        "formalization-report",
        "review-formalization",
        "revoke-formalization",
        "register-formalization",
    ):
        try:
            store = IntakeStore(ROOT)
            exit_code = 0
            if args.command == "formalize":
                source = SourceClaim.from_dict(
                    strict_json(Path(args.input).read_text(encoding="utf-8"))
                )
                if args.revise:
                    previous = store.load(ROOT / args.revise).source
                    if source != previous.revise(source.statement, source.context):
                        raise ValueError(
                            "A revision must keep its source id and increment its version"
                        )
                formalizer = StructuredFormalizer(
                    FixtureModelService(ROOT / "evals/formalization/fixtures.json")
                )
                method = (
                    formalizer.formalize_claim
                    if args.kind == "claim"
                    else formalizer.formalize_step
                )
                draft = method(source, ModelBudget(timeout_seconds=args.timeout))
                exit_code = 0 if draft.run_status.value == "COMPLETED" else 2
                directory = store.save(draft, ROOT / args.revise if args.revise else None)
                result = {**store.report(directory), "artifact_directory": str(directory)}
            elif args.command == "formalization-report":
                result = store.report(ROOT / args.directory)
            elif args.command == "register-formalization":
                store.register(ROOT / args.directory, ROOT / args.revise if args.revise else None)
                result = store.report(ROOT / args.directory)
            elif args.command == "review-formalization":
                attestation = CandidateReview(
                    args.draft_fingerprint, args.candidate, args.reviewer, args.decision, args.note
                )
                path = store.review(ROOT / args.directory, attestation, event_id=args.event_id)
                result = strict_json(path.read_text(encoding="utf-8"))
                result["artifact_file"] = str(path)
                result["review_state"] = store.report(ROOT / args.directory)["review_state"]
            else:
                path = store.revoke(
                    ROOT / args.directory,
                    target=args.review_id,
                    reviewer=args.reviewer,
                    note=args.note,
                    draft_fingerprint=args.draft_fingerprint,
                    candidate_fingerprint=args.candidate,
                    event_id=args.event_id,
                )
                result = strict_json(path.read_text(encoding="utf-8"))
                result["artifact_file"] = str(path)
                result["review_state"] = store.report(ROOT / args.directory)["review_state"]
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return exit_code
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(f"MRA formalization error: {error}", file=sys.stderr)
            return 1
    cases = json.loads((ROOT / "evals/golden_cases.json").read_text(encoding="utf-8"))
    if args.command == "certificate-target":
        try:
            case = next((case for case in cases if case["id"] == args.case_id), None)
            if case is None:
                raise ValueError("Unknown certificate target")
            print(json.dumps(target_descriptor(case), ensure_ascii=False, indent=2))
            return 0
        except (KeyError, ValueError) as error:
            print(f"MRA certificate error: {error}", file=sys.stderr)
            return 1
    if args.command == "cases":
        print(json.dumps(cases, ensure_ascii=False, indent=2))
        return 0
    if args.command == "report":
        directory = (ROOT / args.directory).resolve()
        if not directory.is_relative_to(ROOT / "artifacts/runs"):
            parser.error("Report must be in this project's artifacts/runs directory")
        print((directory / "report.json").read_text(encoding="utf-8"))
        return 0
    try:
        started = time.monotonic()
        raw = None
        if args.command == "check-certificate":
            case = next((case for case in cases if case["id"] == args.case_id), None)
            if case is None:
                raise ValueError("Unknown certificate target")
            claim, formalization = target_contract(case)
            raw = read_certificate(Path(args.input))
            parse_certificate(raw, claim, formalization)
        engine = MinimalResearchEngine(
            ROOT, args.timeout if args.command in ("check", "check-certificate") else 30.0
        )
        if args.command == "environment":
            print(
                json.dumps(
                    {
                        "verified": True,
                        "environment": asdict(engine.backend.environment),
                        "elapsed_seconds": round(time.monotonic() - started, 4),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command in ("check", "check-certificate"):
            if args.case_id not in engine.cases:
                parser.error("Unknown fixture; use python3 -m mra cases")
            result = engine.run_case(args.case_id, args.reviewer, certificate_raw=raw)
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
            return 0 if result.run_status.value == "COMPLETED" else 2
        results = []
        for case in cases:
            result = engine.run_case(case["id"], reviewer="repository-golden-case-review-v1")
            passed = (
                result.run_status.value,
                result.math_verdict.value,
                result.evidence_level.value,
            ) == (case["expected_status"], case["expected_verdict"], case["expected_level"])
            results.append({"id": case["id"], "passed": passed, **asdict(result)})
            print(
                f"{'PASS' if passed else 'FAIL'} {case['id']}: {result.run_status.value} / {result.math_verdict.value} / {result.evidence_level.value}",
                flush=True,
            )
        output = (ROOT / args.output).resolve()
        if not output.is_relative_to(ROOT / "artifacts"):
            parser.error("Benchmark output must be inside project artifacts")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "schema_version": "1",
                    "kind": "real-lean-integration",
                    "mock": False,
                    "dataset": "evals/golden_cases.json",
                    "environment": asdict(engine.backend.environment),
                    "total": len(results),
                    "passed": sum(r["passed"] for r in results),
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return 0 if all(r["passed"] for r in results) else 1
    except (OSError, ValueError, RuntimeError) as error:
        print(f"MRA setup/execution error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
