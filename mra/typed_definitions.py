"""Typed targets with closed definitions, using the unchanged two-process executor."""

from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from .domain import digest
from .integrity import file_hash
from .mathlib_lab import MathlibLabStore, source_manifest
from .tasks import TaskStore
from .typed_terms import LIMITS, MODE, TYPES, compile_request

STRATEGIES = {
    "rfl": {
        "title": "定义相等",
        "proof_source": "by\n  dsimp (config := { failIfUnchanged := false }) only\n  intros\n  rfl",
    },
    "omega": {
        "title": "自然数／整数线性算术",
        "proof_source": "by\n  dsimp (config := { failIfUnchanged := false }) only\n  intros\n  omega",
    },
    "grind": {
        "title": "核心代数与逻辑策略",
        "proof_source": "by\n  dsimp (config := { failIfUnchanged := false }) only\n  intros\n  grind",
    },
}
LIMITATIONS = [
    "Definitions are transparent closed nonrecursive functions or predicates, not new axioms.",
    "Every original premise is retained; consistency of premises is not certified.",
    "Nat subtraction truncates at zero; Int and Rat subtraction do not.",
    "Rat division is total: a / 0 = 0. Nonzero hypotheses are never inserted automatically.",
    "One fixed tactic attempt; failure or timeout is UNKNOWN, never REFUTED.",
    "Same Lean kernel implementation in independent processes; names are local assertions only.",
    "No arbitrary Lean, imports, recursion, model calls or automatic old research-graph admission.",
]


def manifest(root):
    return {
        "mode": MODE,
        "executor": source_manifest(root),
        "extension_files": {
            name: file_hash(root / name)
            for name in (
                "mra/typed_terms.py",
                "mra/typed_definitions.py",
                "mra/number_theory_terms.py",
            )
        },
        "strategies": deepcopy(STRATEGIES),
    }


def prepare(root, request):
    compiled = compile_request(request)
    strategy = request["strategy"]
    if not isinstance(strategy, str) or strategy not in STRATEGIES:
        raise ValueError("Select rfl, omega or grind; arbitrary tactics are forbidden")
    plan = {
        **compiled,
        "proof_source": STRATEGIES[strategy]["proof_source"],
        "strategy": deepcopy(STRATEGIES[strategy]),
        "source_manifest": manifest(Path(root).resolve()),
        "budget": {
            "candidate_processes": 1,
            "replay_processes": 1,
            "shared_worker_seconds": request["timeout_seconds"],
            "max_heartbeats": 200000,
            "integrity_time_separate": True,
            "automatic_retries": 0,
        },
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "model_calls": 0,
        "limitations": list(LIMITATIONS),
    }
    plan["plan_fingerprint"] = digest(plan)
    return plan


def catalog():
    return {
        "mode": MODE,
        "types": list(TYPES),
        "definition_result_types": [*TYPES, "Prop"],
        "strategies": deepcopy(STRATEGIES),
        "limits": dict(LIMITS),
        "maximum_runs": 200,
        "examples": examples(),
        "limitations": list(LIMITATIONS),
        "provenance": "repository-author-examples-not-model-output",
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "model_calls": 0,
    }


class TypedDefinitionStore(MathlibLabStore):
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/typed-definitions"
        self.path = self.base / "lab.sqlite3"
        if self.base.resolve() != self.base:
            raise ValueError("Typed definition storage cannot be redirected")
        self.base.mkdir(parents=True, exist_ok=True)
        self.tasks = TaskStore(self.root)
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL)"
            )

    @contextmanager
    def transaction(self, write=True):
        if self.base.resolve() != self.base or self.path.is_symlink():
            raise ValueError("Typed definition ledger cannot be redirected")
        with super().transaction(write) as db:
            yield db

    def preview(self, request):
        return prepare(self.root, request)

    def gate(self, row, check_files=True):
        if row["plan"]["source_manifest"] != manifest(self.root):
            return "Typed compiler, fixed tactics, executor or runtime changed"
        view = {
            **row,
            "plan": {**row["plan"], "source_manifest": row["plan"]["source_manifest"]["executor"]},
        }
        return super().gate(view, check_files)

    def catalog(self):
        return catalog()

    def report(self, run_id):
        return {**super().report(run_id), "mode": MODE, "fresh_attestation": False}


def examples():
    def v(name):
        return {"var": name}

    def f(op, a, b):
        return {op: [a, b]}

    def call(name, *args):
        return {"call": {"name": name, "args": list(args)}}

    def cast(value, to):
        return {"cast": {"to": to, "value": value}}

    def binding(name, typ):
        return {"name": name, "type": typ}

    def definition(name, original, parameters, result, body):
        return {
            "name": name,
            "original": original,
            "parameters": parameters,
            "result_type": result,
            "body": body,
        }

    def case(title, original, definitions, variables, assumptions, goal, strategy):
        return {
            "schema": MODE,
            "title": title,
            "original": original,
            "definitions": definitions,
            "variables": variables,
            "assumptions": assumptions,
            "goal": goal,
            "strategy": strategy,
            "timeout_seconds": 30,
        }

    a, b, x, y, n, z, q = (v(s) for s in ("a", "b", "x", "y", "n", "z", "q"))
    ns = [binding("a", "Nat"), binding("b", "Nat")]
    ints = [binding("a", "Int"), binding("b", "Int")]
    rats = [binding("x", "Rat"), binding("y", "Rat")]
    gap_nat = definition("gap", "gap(a,b)=a-b，使用自然数截断减法。", ns, "Nat", f("sub", a, b))
    gap_int = definition("gap", "gap(a,b)=a-b，使用整数减法。", ints, "Int", f("sub", a, b))
    square = definition("square", "square(x)=x*x，x为有理数。", [rats[0]], "Rat", f("mul", x, x))
    embed = definition(
        "embed", "将自然数n显式转换为整数。", [binding("n", "Nat")], "Int", cast(n, "Int")
    )
    lift = definition(
        "lift",
        "lift(n)将embed(n)显式转换为有理数。",
        [binding("n", "Nat")],
        "Rat",
        cast(call("embed", n), "Rat"),
    )
    le = definition("before", "before(x,y)表示有理数x≤y。", rats, "Prop", f("le", x, y))
    interval = definition(
        "interval",
        "interval(x,a,b)表示a≤x且x≤b。",
        [binding(s, "Rat") for s in ("x", "a", "b")],
        "Prop",
        f("and", call("before", a, x), call("before", x, b)),
    )
    half = {"rat": [1, 2]}
    nat_case = case(
        "自然数差函数与必要条件",
        "自然数a≤b时，gap(b,a)+a=b。",
        [gap_nat],
        ns,
        [{"name": "hab", "formula": f("le", a, b)}],
        f("eq", f("add", call("gap", b, a), a), b),
        "omega",
    )
    return [
        nat_case,
        case(
            "整数差函数",
            "对任意整数a,b，无需大小条件，gap(b,a)+a=b。",
            [gap_int],
            ints,
            [],
            f("eq", f("add", call("gap", b, a), a), b),
            "omega",
        ),
        case(
            "有理数平方展开",
            "对任意有理数x,y，square(x+y)=square(x)+2*x*y+square(y)。",
            [square],
            rats,
            [],
            f(
                "eq",
                call("square", f("add", x, y)),
                f(
                    "add",
                    f("add", call("square", x), f("mul", f("mul", {"rat": [2, 1]}, x), y)),
                    call("square", y),
                ),
            ),
            "grind",
        ),
        case(
            "精确分数",
            "有理数1/2+1/2=1。",
            [],
            [],
            [],
            f("eq", f("add", half, half), {"rat": [1, 1]}),
            "grind",
        ),
        case(
            "混合类型与定义依赖",
            "n是自然数，z是整数，q是有理数；z-z=0且lift(n)+q-q=lift(n)。",
            [embed, lift],
            [binding("n", "Nat"), binding("z", "Int"), binding("q", "Rat")],
            [],
            f(
                "and",
                f("eq", f("sub", z, z), {"int": 0}),
                f("eq", f("sub", f("add", call("lift", n), q), q), call("lift", n)),
            ),
            "grind",
        ),
        case(
            "命题定义与区间条件",
            "有理数x,a,b满足interval(x,a,b)时，有a≤b。",
            [le, interval],
            [binding(s, "Rat") for s in ("x", "a", "b")],
            [{"name": "hin", "formula": call("interval", x, a, b)}],
            f("le", a, b),
            "grind",
        ),
        case(
            "有理数除法的非零条件",
            "对非零有理数q，有q/q=1。",
            [],
            [binding("q", "Rat")],
            [{"name": "hnz", "formula": {"not": f("eq", q, {"rat": [0, 1]})}}],
            f("eq", f("div", q, q), {"rat": [1, 1]}),
            "grind",
        ),
        case(
            "失败对照：不能删除非零条件",
            "尝试对所有有理数q断言q/q=1。",
            [],
            [binding("q", "Rat")],
            [],
            f("eq", f("div", q, q), {"rat": [1, 1]}),
            "grind",
        ),
        {
            **deepcopy(nat_case),
            "title": "失败对照：不能删除自然数大小条件",
            "assumptions": [],
            "original": "尝试对所有自然数a,b断言gap(b,a)+a=b。",
        },
    ]
