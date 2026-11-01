"""Fixed Lean tactics for complete Nat targets; the frozen executor certifies them."""

from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from .domain import canonical, digest
from .formalizer import exact_object, strict_json
from .integrity import file_hash
from .mathlib_lab import MathlibLabStore, source_manifest
from .number_theory_search import TARGET_FIELDS
from .number_theory_terms import MODE as TARGET_MODE, compile_request
from .tasks import TaskStore

MODE = "mathlib-nat-strategy-v1"
SIMP_RULES = (
    "Nat.gcd_self",
    "Nat.gcd_zero_left",
    "Nat.gcd_zero_right",
    "Nat.gcd_comm",
    "Nat.coprime_iff_gcd_eq_one",
    "Nat.dvd_refl",
)
STRATEGIES = {
    "omega": {
        "title": "自然数线性算术",
        "description": "处理加法、大小关系和有条件的截断减法；一般乘法、gcd 与整除不在其保证范围。",
        "proof_source": "by\n  intros\n  omega",
        "declared_simp_rules": [],
    },
    "simp-gcd": {
        "title": "受控 gcd 与互素化简",
        "description": "仅使用列明的六条化简规则及 Lean 的基础化简机制；保留全部原前提。",
        "proof_source": "by\n  intros\n  simp_all only [" + ", ".join(SIMP_RULES) + "]",
        "declared_simp_rules": list(SIMP_RULES),
    },
}


def manifest(root):
    return {
        "mode": MODE,
        "executor": source_manifest(root),
        "extension_files": {
            name: file_hash(root / name)
            for name in (
                "mra/lean_strategies.py",
                "mra/number_theory_terms.py",
                "mra/number_theory_search.py",
            )
        },
        "strategies": deepcopy(STRATEGIES),
    }


def prepare(root, raw):
    if len(canonical(raw)) > 196608:
        raise ValueError("Strategy request exceeds 192 KiB")
    raw = strict_json(canonical(raw).decode("utf-8"))
    exact_object(raw, TARGET_FIELDS | {"strategy"}, "Complete Nat strategy request")
    if (
        raw["schema"] != MODE
        or not isinstance(raw["strategy"], str)
        or raw["strategy"] not in STRATEGIES
    ):
        raise ValueError(
            "Select a versioned fixed Lean strategy; arbitrary tactics are not accepted"
        )
    complete = {k: v for k, v in raw.items() if k != "strategy"}
    complete["schema"] = TARGET_MODE
    compiled = compile_request(
        {**complete, "proof": {"ref": "Nat.dvd_refl", "terms": [{"nat": 0}], "proofs": []}}
    )
    value = {k: v for k, v in compiled["request"].items() if k != "proof"}
    strategy = deepcopy(STRATEGIES[raw["strategy"]])
    plan = {
        "request": {**value, "schema": MODE, "strategy": raw["strategy"]},
        "formal_statement": compiled["formal_statement"],
        "variable_mapping": compiled["variable_mapping"],
        "assumptions": compiled["assumptions"],
        "node_count": compiled["node_count"],
        "proof_source": strategy["proof_source"],
        "strategy": strategy,
        "source_manifest": manifest(Path(root).resolve()),
        "budget": {
            "candidate_processes": 1,
            "replay_processes": 1,
            "shared_worker_seconds": value["timeout_seconds"],
            "max_heartbeats": 200000,
            "integrity_time_separate": True,
            "automatic_retries": 0,
        },
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "model_calls": 0,
        "limitations": [
            "One complete-target tactic attempt, not an interactive proof-state search API.",
            "Failure, timeout and unsupported mathematics remain UNKNOWN, never REFUTED.",
            "Every original premise is retained; consistency of premises is not certified.",
            "The independent process uses the same Lean kernel implementation.",
            "No model, arbitrary tactic text, additional imports or added assumptions.",
        ],
    }
    plan["plan_fingerprint"] = digest(plan)
    return plan


class LeanStrategyStore(MathlibLabStore):
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/lean-strategies"
        self.path = self.base / "lab.sqlite3"
        if (self.root / "artifacts").is_symlink() or self.base.is_symlink():
            raise ValueError("Lean strategy storage cannot be redirected")
        self.base.mkdir(parents=True, exist_ok=True)
        self.tasks = TaskStore(self.root)
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL)"
            )

    @contextmanager
    def transaction(self, write=True):
        if self.base.resolve() != self.base or self.path.is_symlink():
            raise ValueError("Lean strategy ledger cannot be redirected")
        with super().transaction(write) as db:
            yield db

    def preview(self, request):
        return prepare(self.root, request)

    def gate(self, row, check_files=True):
        if row["plan"]["source_manifest"] != manifest(self.root):
            return "Lean strategy compiler, tactic catalog, executor or runtime changed"
        view = {
            **row,
            "plan": {**row["plan"], "source_manifest": row["plan"]["source_manifest"]["executor"]},
        }
        return super().gate(view, check_files)

    def report(self, run_id):
        return {**super().report(run_id), "mode": MODE, "fresh_attestation": False}

    def catalog(self):
        return {
            "mode": MODE,
            "target_mode": TARGET_MODE,
            "strategies": deepcopy(STRATEGIES),
            "examples": examples(),
            "maximum_runs": 200,
            "scope": "Fixed tactics for the existing bounded Nat language; no model or general Lean source",
        }


def examples():
    def v(name):
        return {"var": name}

    def f(op, a, b):
        return {op: [a, b]}

    def case(title, original, variables, assumptions, goal, strategy):
        return {
            "schema": MODE,
            "title": title,
            "original": original,
            "variables": variables,
            "assumptions": assumptions,
            "goal": goal,
            "strategy": strategy,
            "timeout_seconds": 30,
        }

    a, b, c = v("a"), v("b"), v("c")
    return [
        case("顺序自反性", "对任意自然数 a，有 a≤a。", ["a"], [], f("le", a, a), "omega"),
        case(
            "保留两个前提的顺序传递",
            "对任意自然数 a、b、c，若 a≤b 且 b≤c，则 a≤c。",
            ["a", "b", "c"],
            [{"name": "hab", "formula": f("le", a, b)}, {"name": "hbc", "formula": f("le", b, c)}],
            f("le", a, c),
            "omega",
        ),
        case(
            "截断减法还原的必要条件",
            "对任意自然数 a、b，若 a≤b，则 (b-a)+a=b。",
            ["a", "b"],
            [{"name": "hab", "formula": f("le", a, b)}],
            f("eq", f("add", f("sub", b, a), a), b),
            "omega",
        ),
        case(
            "gcd 与零的化简",
            "对任意自然数 a，有 gcd(a,0)=a。",
            ["a"],
            [],
            f("eq", f("gcd", a, {"nat": 0}), a),
            "simp-gcd",
        ),
        case(
            "互素的确切定义",
            "对任意自然数 a、b，互素当且仅当 gcd(a,b)=1。",
            ["a", "b"],
            [],
            f("iff", f("coprime", a, b), f("eq", f("gcd", a, b), {"nat": 1})),
            "simp-gcd",
        ),
        case(
            "失败对照：不能省略截断减法条件",
            "对任意自然数 a、b，试图无条件断言 (b-a)+a=b。",
            ["a", "b"],
            [],
            f("eq", f("add", f("sub", b, a), a), b),
            "omega",
        ),
    ]
