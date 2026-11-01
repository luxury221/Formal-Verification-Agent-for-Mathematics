"""Editable known classical PDE examples; placeholders never authorize a source."""

from copy import deepcopy

from .analysis_examples import op, r, rule, v
from .calculus_examples import FUNCTION, VOLUME, binding, has_deriv, integrable, integral, lam
from .domain import digest
from .pde_language import MODE


def apply(f, x):
    return op("apply", f, x)


def hyp(name):
    return {"hyp": name}


def derivative(f, df, a, b):
    x = v("x")
    return {
        "forall": {
            "name": "x",
            "type": "Real",
            "body": op(
                "imp",
                {"in_uIcc": {"term": x, "lower": a, "upper": b}},
                has_deriv(f, apply(df, x), x),
            ),
        }
    }


def equality(proof, reverse=False):
    return {"proof": proof, "reverse": reverse}


def rewrite(proof, *equalities):
    return {"rewrite": {"proof": proof, "equalities": list(equalities)}}


def source_apply(name, terms, proofs):
    return {"source_apply": {"source": name, "terms": list(terms), "proofs": list(proofs)}}


def example(identifier, bindings=None):
    bindings = bindings or {}
    u, w, du, dw, ddu, f, phi, dphi, a, b, x = (
        v(n) for n in ("u", "v", "du", "dv", "ddu", "f", "phi", "dphi", "a", "b", "x")
    )
    conditions = []

    def condition(name, formula):
        conditions.append({"name": name, "formula": formula})

    def source(name):
        value = bindings.get(name)
        if value is None:
            value = {"run_id": "pdelang-" + "0" * 32, "type_fingerprint": digest("UNBOUND EXAMPLE")}
        return {"name": name, **deepcopy(value)}

    def area(left, right):
        return integral(op("mul", apply(left, x), apply(right, x)), a, b, name="x")

    def congr(body):
        return {
            "interval_congr": {
                "name": "x",
                "membership": "member",
                "lower": a,
                "upper": b,
                "measure": deepcopy(VOLUME),
                "proof": body,
            }
        }

    if identifier == "scalar-cauchy-schwarz":
        variables = [binding("x"), binding("y")]
        sources = []
        goal = op(
            "le",
            {"norm": op("inner", v("x"), v("y"))},
            op("mul", {"norm": v("x")}, {"norm": v("y")}),
        )
        proof = rule("cauchy_schwarz", [v("x"), v("y")])
    elif identifier in (
        "integration-by-parts",
        "zero-boundary-green",
        "reversed-endpoints",
        "equal-endpoints",
    ):
        if identifier == "reversed-endpoints":
            a, b = b, a
        if identifier == "equal-endpoints":
            b = a
        variables = [binding(n, FUNCTION) for n in ("u", "v", "du", "dv")]
        variables += [binding("a")] + ([] if identifier == "equal-endpoints" else [binding("b")])
        condition("hu", derivative(u, du, a, b))
        condition("hv", derivative(w, dw, a, b))
        condition("hdu", integrable(du, a, b))
        condition("hdv", integrable(dw, a, b))
        application = source_apply(
            "ibp", [u, w, du, dw, a, b], [hyp(n) for n in ("hu", "hv", "hdu", "hdv")]
        )
        sources = [source("ibp")]
        goal = op(
            "eq",
            area(u, dw),
            op(
                "sub",
                op("sub", op("mul", apply(u, b), apply(w, b)), op("mul", apply(u, a), apply(w, a))),
                area(du, w),
            ),
        )
        proof = application
        if identifier == "zero-boundary-green":
            condition("ha", op("eq", apply(u, a), r(0)))
            condition("hb", op("eq", apply(u, b), r(0)))
            goal = op("eq", area(u, dw), {"neg": area(du, w)})
            proof = rewrite(application, equality(hyp("ha")), equality(hyp("hb")))
    elif identifier == "poisson-smooth-test":
        variables = [binding(n, FUNCTION) for n in ("u", "du", "ddu", "f", "phi", "dphi")]
        variables += [binding("a"), binding("b")]
        condition("hu", derivative(u, du, a, b))
        condition("hdu", derivative(du, ddu, a, b))
        condition("hphi", derivative(phi, dphi, a, b))
        condition("hi", integrable(du, a, b))
        condition("hi2", integrable(ddu, a, b))
        condition("hiphi", integrable(dphi, a, b))
        member = {"in_uIcc": {"term": x, "lower": a, "upper": b}}
        condition(
            "equation",
            {
                "forall": {
                    "name": "x",
                    "type": "Real",
                    "body": op(
                        "imp",
                        member,
                        op("eq", {"neg": apply(ddu, x)}, apply(f, x)),
                    ),
                }
            },
        )
        condition("ha", op("eq", apply(phi, a), r(0)))
        condition("hb", op("eq", apply(phi, b), r(0)))
        sources = [source("green")]
        green = source_apply(
            "green",
            [phi, du, dphi, ddu, a, b],
            [hyp(n) for n in ("hphi", "hdu", "hiphi", "hi2", "ha", "hb")],
        )
        swapped = congr(rule("mul_comm", [apply(du, x), apply(dphi, x)]))
        identity = rewrite(rule("eq_refl", [area(dphi, du)]), equality(green))
        equation = {"use_hyp": {"name": "equation", "terms": [x], "proofs": [hyp("member")]}}
        change = congr(
            rewrite(
                rule("mul_comm", [{"neg": apply(ddu, x)}, apply(phi, x)]),
                equality(equation, True),
            )
        )
        negative = rule(
            "interval_neg",
            [
                lam("x", op("mul", apply(phi, x), apply(ddu, x))),
                deepcopy(VOLUME),
                a,
                b,
            ],
        )
        force = rule("eq_trans", proofs=[change, negative])
        proof = rule(
            "eq_trans",
            proofs=[
                swapped,
                rule("eq_trans", proofs=[identity, rule("eq_symm", proofs=[force])]),
            ],
        )
        goal = op("eq", area(du, dphi), area(f, phi))
    elif identifier in ("poisson-energy", "poisson-work-nonnegative"):
        variables = [binding(n, FUNCTION) for n in ("u", "du", "ddu", "f")]
        variables += [binding("a"), binding("b")]
        condition("hu", derivative(u, du, a, b))
        condition("hdu", derivative(du, ddu, a, b))
        condition("hi", integrable(du, a, b))
        condition("hi2", integrable(ddu, a, b))
        member = {"in_uIcc": {"term": x, "lower": a, "upper": b}}
        condition(
            "equation",
            {
                "forall": {
                    "name": "x",
                    "type": "Real",
                    "body": op(
                        "imp",
                        member,
                        op("eq", {"neg": apply(ddu, x)}, apply(f, x)),
                    ),
                }
            },
        )
        condition("ha", op("eq", apply(u, a), r(0)))
        condition("hb", op("eq", apply(u, b), r(0)))
        if identifier == "poisson-energy":
            sources = [source("test")]
            proof = source_apply(
                "test",
                [u, du, ddu, f, u, du, a, b],
                [hyp(n) for n in ("hu", "hdu", "hu", "hi", "hi2", "hi", "equation", "ha", "hb")],
            )
            goal = op("eq", area(du, du), area(f, u))
        else:
            sources = [source("energy")]
            energy = source_apply(
                "energy",
                [u, du, ddu, f, a, b],
                [hyp(n) for n in ("hu", "hdu", "hi", "hi2", "equation", "ha", "hb")],
            )
            condition("order", op("le", a, b))
            positive = rule(
                "interval_nonneg_forall",
                [
                    lam("x", op("mul", apply(du, x), apply(du, x))),
                    deepcopy(VOLUME),
                    a,
                    b,
                ],
                [
                    hyp("order"),
                    {
                        "intro": {
                            "name": "x",
                            "type": "Real",
                            "proof": rule("mul_self_nonneg", [apply(du, x)]),
                        }
                    },
                ],
            )
            proof = rewrite(positive, equality(energy))
            goal = op("le", r(0), area(f, u))
    else:
        raise ValueError("Unknown classical PDE example")
    return {
        "schema": MODE,
        "title": identifier,
        "original": {
            "scalar-cauchy-schwarz": "实数内积的Cauchy–Schwarz界，使用明确实数类型；这是已知估计，不声明原创性。",
            "integration-by-parts": "保留完整闭区间导数、可积性和非零端点项，实际应用已验证分部积分来源。",
            "zero-boundary-green": "函数u在两个端点均为零时的积分恒等式；全部导数、可积条件和有向端点保留。",
            "reversed-endpoints": "将分部积分的端点反向，完整条件与端点项按反向区间保留，不补a≤b。",
            "equal-endpoints": "分部积分的两个端点相同；仍保留原有导数和可积性条件。",
            "poisson-smooth-test": "一维经典Poisson方程对零边界测试函数的积分恒等式；显式导数、可积性、方程及两个边界条件，不声明一般弱解。",
            "poisson-energy": "一维经典零Dirichlet Poisson解取自身为测试函数得到能量恒等式；不是存在性证明。",
            "poisson-work-nonnegative": "在全部经典Poisson条件之外明确要求a≤b，用能量恒等式证明外力功积分非负。",
        }[identifier],
        "spaces": [],
        "variables": variables,
        "assumptions": conditions,
        "goal": goal,
        "sources": sources,
        "proof": {"term": proof},
        "timeout_seconds": 40,
    }


EXAMPLES = (
    "scalar-cauchy-schwarz",
    "integration-by-parts",
    "zero-boundary-green",
    "poisson-smooth-test",
    "poisson-energy",
    "poisson-work-nonnegative",
    "reversed-endpoints",
    "equal-endpoints",
)


def examples():
    return [
        {
            "id": name,
            "request": example(name),
            "sources_bound": False,
            "limits": "Known editable example with invalid source placeholders; not a benchmark result",
        }
        for name in EXAMPLES
    ]
