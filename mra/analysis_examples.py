"""Repository-known analysis examples; development evidence, not a blind benchmark."""

from copy import deepcopy
from .analysis_language import MODE


def v(name):
    return {"var": name}


def r(n, d=1):
    return {"real": [n, d]}


def op(name, x, y):
    return {name: [x, y]}


def rule(name, terms=(), proofs=()):
    return {"rule": {"name": name, "terms": list(terms), "proofs": list(proofs)}}


def examples():
    real, space = "Real", {"space": "E"}
    fn = {"function": [real, space]}
    scalar_fn = {"function": [real, real]}

    def binding(name, typ):
        return {"name": name, "type": typ}

    def case(identifier, original, variables, assumptions, goal, proof, spaces=True):
        return {
            "id": identifier,
            "expected": "COMPLETED",
            "request": {
                "schema": MODE,
                "title": identifier,
                "original": original,
                "spaces": [{"name": "E", "structure": "real-inner-product"}] if spaces else [],
                "variables": variables,
                "assumptions": assumptions,
                "goal": goal,
                "proof": {"term": proof},
                "timeout_seconds": 30,
            },
        }

    f, g, t, a, x, y = (v(s) for s in ("f", "g", "t", "a", "x", "y"))
    ft, gt = op("apply", f, t), op("apply", g, t)

    def norm(z):
        return {"norm": z}

    cs_goal = op("le", norm(op("inner", ft, gt)), op("mul", norm(ft), norm(gt)))
    cs = case(
        "pointwise-cauchy-schwarz",
        "任意Type 0实内积空间E，任意函数f,g:ℝ→E及实数t，在t处满足Cauchy–Schwarz。",
        [binding("f", fn), binding("g", fn), binding("t", real)],
        [],
        cs_goal,
        rule("cauchy_schwarz", (ft, gt)),
    )
    lam = {"lambda": {"name": "t", "type": real, "body": op("smul", a, op("apply", f, v("t")))}}
    energy = op("apply", lam, t)
    scaled = case(
        "lambda-scaled-energy",
        "任意实数a,t和函数f:ℝ→E，(λt,a•f(t))(t)的自身实内积非负；λ内部t为局部绑定。",
        [binding("f", fn), binding("a", real), binding("t", real)],
        [],
        op("le", r(0), op("inner", energy, energy)),
        rule("inner_self_nonneg", (energy,)),
    )
    triangle = case(
        "norm-triangle",
        "任意实内积空间E的x,y满足范数三角不等式。",
        [binding("x", space), binding("y", space)],
        [],
        op("le", norm(op("add", x, y)), op("add", norm(x), norm(y))),
        rule("norm_add_le", (x, y)),
    )
    division = case(
        "division-nonzero",
        "对任意非零实数a，有a/a=1；非零条件明确保留。",
        [binding("a", real)],
        [{"name": "hne", "formula": {"not": op("eq", a, r(0))}}],
        op("eq", op("div", a, a), r(1)),
        rule("div_self", proofs=({"hyp": "hne"},)),
        False,
    )
    all_nonneg = {
        "forall": {"name": "z", "type": real, "body": op("le", r(0), op("apply", f, v("z")))}
    }
    pointwise = case(
        "pointwise-hypothesis",
        "函数f:ℝ→ℝ在所有实数上非负时，在给定t处非负。",
        [binding("f", scalar_fn), binding("t", real)],
        [{"name": "hpos", "formula": all_nonneg}],
        op("le", r(0), ft),
        {"use_hyp": {"name": "hpos", "terms": [t], "proofs": []}},
        False,
    )
    square = case(
        "inner-energy-square",
        "任意实内积空间E中，x的自身内积等于范数平方。",
        [binding("x", space)],
        [],
        op("eq", op("inner", x, x), {"pow": [norm(x), 2]}),
        rule("inner_self_norm_sq", (x,)),
    )
    congruence = case(
        "function-congruence",
        "f:ℝ→E且x=y时，f(x)=f(y)。",
        [binding("f", fn), binding("x", real), binding("y", real)],
        [{"name": "hxy", "formula": op("eq", x, y)}],
        op("eq", op("apply", f, x), op("apply", f, y)),
        rule("congr_arg", (f,), ({"hyp": "hxy"},)),
    )
    quantified = case(
        "quantified-square",
        "每个实数s的平方非负。",
        [],
        [],
        {"forall": {"name": "s", "type": real, "body": op("le", r(0), {"pow": [v("s"), 2]})}},
        {"intro": {"name": "u", "type": real, "proof": rule("sq_nonneg", (v("u"),))}},
        False,
    )
    higher = case(
        "higher-order-function",
        "H:(ℝ→ℝ)→ℝ且f=g时，H(f)=H(g)。",
        [
            binding("H", {"function": [scalar_fn, real]}),
            binding("f", scalar_fn),
            binding("g", scalar_fn),
        ],
        [{"name": "heq", "formula": op("eq", f, g)}],
        op("eq", op("apply", v("H"), f), op("apply", v("H"), g)),
        rule("congr_arg", (v("H"),), ({"hyp": "heq"},)),
        False,
    )
    beta = case(
        "lambda-beta",
        "恒等实函数在a处的值为a。",
        [binding("a", real)],
        [],
        op("eq", op("apply", {"lambda": {"name": "a", "type": real, "body": v("a")}}, a), a),
        {},
        False,
    )
    beta["request"]["proof"] = {"tactic": "rfl"}
    no_nonzero = deepcopy(division)
    no_nonzero.update(id="missing-nonzero", expected="REJECTED")
    no_nonzero["request"].update(
        title="missing-nonzero",
        original="失败对照：不带非零条件尝试对所有实数a断言a/a=1。",
        assumptions=[],
        proof={"tactic": "simp"},
    )
    wrong_sign = deepcopy(scaled)
    wrong_sign.update(id="wrong-energy-sign", expected="REJECTED")
    wrong_sign["request"].update(
        title="wrong-energy-sign",
        original="失败对照：错误地断言缩放后向量自身内积非正。",
        goal=op("le", op("inner", energy, energy), r(0)),
    )
    unused = case(
        "unused-original-condition",
        "原条件a≤0虽未用于证明，仍完整保留：任意x的范数非负。",
        [binding("x", space), binding("a", real)],
        [{"name": "unused", "formula": op("le", a, r(0))}],
        op("le", r(0), norm(x)),
        rule("norm_nonneg", (x,)),
    )
    nested_lambda = {
        "lambda": {
            "name": "x",
            "type": real,
            "body": {"lambda": {"name": "x", "type": real, "body": v("x")}},
        }
    }
    nested = case(
        "nested-shadowing",
        "λx.λx.x的内部x遮蔽外部x；依次应用a,b得到b。",
        [binding("a", real), binding("b", real)],
        [],
        op("eq", op("apply", op("apply", nested_lambda, a), v("b")), v("b")),
        {},
        False,
    )
    nested["request"]["proof"] = {"tactic": "rfl"}
    return [
        cs,
        scaled,
        triangle,
        division,
        pointwise,
        square,
        congruence,
        quantified,
        higher,
        beta,
        nested,
        unused,
        no_nonzero,
        wrong_sign,
    ]
