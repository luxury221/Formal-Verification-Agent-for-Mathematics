"""Exact finite lp statements; examples never grant a verdict by themselves."""

from copy import deepcopy

from .finite_lp_language import MODE
from .proof_logic_examples import binary as b, var, hyp, exists, witness, eliminate


def real(n, d=1):
    return {"real": [n, d]}


def vec(*coordinates):
    return {"finite_vector": [real(x) for x in coordinates]}


def rpow(x, y):
    return {"real_rpow": [x, y]}


def lp(p, u):
    return {"lp_norm": {"exponent": p, "vector": u}}


def total(p, u):
    return {"lp_sum": {"exponent": p, "vector": u}}


def rule(name, terms=(), proofs=()):
    return {"rule": {"name": name, "terms": list(terms), "proofs": list(proofs)}}


def compute(*proofs):
    return {"finite_compute": {"equalities": [{"proof": p, "reverse": False} for p in proofs]}}


def simplify(proof, *equalities):
    return {
        "finite_simplify": {
            "proof": proof,
            "equalities": [{"proof": p, "reverse": False} for p in equalities],
        }
    }


def conjunction(items):
    result = items[-1]
    for value in reversed(items[:-1]):
        result = b("and", value, result)
    return result


def combine(proofs):
    result = proofs[-1]
    for proof in reversed(proofs[:-1]):
        result = rule("and_intro", proofs=[proof, result])
    return result


def dependent(u, v):
    return exists(
        "a",
        exists(
            "b",
            b(
                "and",
                b("or", {"not": b("eq", var("a"), real(0))}, {"not": b("eq", var("b"), real(0))}),
                b("eq", b("smul", var("a"), u), b("smul", var("b"), v)),
            ),
        ),
    )


def independent_proof(u, v):
    equation = b("eq", b("smul", var("a"), u), b("smul", var("b"), v))
    alternatives = b("or", {"not": b("eq", var("a"), real(0))}, {"not": b("eq", var("b"), real(0))})
    facts = [
        simplify(
            {
                "coordinate_eq": {
                    "left": b("smul", var("a"), u),
                    "right": b("smul", var("b"), v),
                    "index": i,
                    "proof": rule("and_right", proofs=[hyp("ab_conditions")]),
                }
            }
        )
        for i in (0, 1)
    ]

    def impossible(name):
        return rule(
            "not_elim", proofs=[hyp(name), {"arithmetic": {"method": "linarith", "proofs": facts}}]
        )

    cases = {
        "or_cases": {
            "proof": rule("and_left", proofs=[hyp("ab_conditions")]),
            "left": {"name": "a_nonzero", "proof": impossible("a_nonzero")},
            "right": {"name": "b_nonzero", "proof": impossible("b_nonzero")},
        }
    }
    a_body = exists("b", b("and", alternatives, equation))
    b_body = b("and", alternatives, equation)
    elimination = eliminate(
        hyp("dependent"),
        "a",
        "a_body",
        a_body,
        eliminate(hyp("a_body"), "b", "ab_conditions", b_body, cases),
    )
    return {"intro_hyp": {"name": "dependent", "formula": dependent(u, v), "proof": elimination}}


def request(title, original, goal, proof, variables=(), assumptions=()):
    return {
        "schema": MODE,
        "title": title,
        "original": original,
        "spaces": [],
        "variables": list(variables),
        "assumptions": list(assumptions),
        "goal": goal,
        "proof": {"term": proof},
        "timeout_seconds": 40,
    }


def holder_witness():
    u, v = vec(1, 2), vec(1, 4)
    p, q, r = real(3), real(3, 2), real(3)
    loss = b(
        "sub", b("mul", rpow(lp(p, u), r), rpow(lp(q, v), r)), rpow({"abs": b("dot", u, v)}, r)
    )
    four = {
        "rational_power": {
            "base": real(4),
            "root": real(2),
            "numerator": 3,
            "denominator": 2,
            "nonnegative": compute(),
            "root_power": compute(),
        }
    }
    lemmas = [
        {
            "name": "four",
            "formula": b("eq", rpow(real(4), q), {"pow": [real(2), 3]}),
            "proof": four,
        },
        {"name": "sum_u", "formula": b("eq", total(p, u), real(9)), "proof": compute()},
        {"name": "sum_v", "formula": b("eq", total(q, v), real(9)), "proof": compute(hyp("four"))},
        {
            "name": "power_u",
            "formula": b("eq", rpow(lp(p, u), r), real(9)),
            "proof": {
                "rewrite": {
                    "proof": rule("lp_norm_rpow", [p, u], [compute()]),
                    "equalities": [{"proof": hyp("sum_u"), "reverse": False}],
                }
            },
        },
        {
            "name": "power_v",
            "formula": b("eq", rpow(lp(q, v), r), real(81)),
            "proof": compute(
                hyp("sum_v"), rule("real_rpow_mul", [real(9), real(2, 3), r], [compute()])
            ),
        },
    ]
    lemmas.append(
        {
            "name": "dot_power",
            "formula": b("eq", rpow({"abs": b("dot", u, v)}, r), real(729)),
            "proof": compute(),
        }
    )
    body = combine(
        [
            compute(),
            compute(),
            compute(),
            compute(),
            {
                "arithmetic": {
                    "method": "nlinarith",
                    "proofs": [hyp("power_u"), hyp("power_v"), hyp("dot_power")],
                }
            },
            independent_proof(u, v),
        ]
    )
    goal = conjunction(
        [
            b("le", real(1), p),
            b("le", real(1), q),
            b("eq", b("add", b("div", real(1), p), b("div", real(1), q)), real(1)),
            b("lt", real(0), r),
            b("eq", loss, real(0)),
            {"not": dependent(u, v)},
        ]
    )

    def abstract(value):
        if value == u:
            return b("apply", var("g"), var("x"))
        if value == v:
            return b("apply", var("h"), var("x"))
        if isinstance(value, dict):
            return {key: abstract(item) for key, item in value.items()}
        if isinstance(value, list):
            return [abstract(item) for item in value]
        return value

    typ = {"finite_vector": 2}
    goal = abstract(goal)
    for name, binding_type in reversed(
        (("g", {"function": [typ, typ]}), ("h", {"function": [typ, typ]}), ("x", typ))
    ):
        goal = {"exists": {"name": name, "type": binding_type, "body": goal}}
    g = {"lambda": {"name": "ignored_g", "type": typ, "body": u}}
    h = {"lambda": {"name": "ignored_h", "type": typ, "body": v}}
    body = witness(g, witness(h, witness({"zero": typ}, body)))
    original = "二维精确见证：p=3、q=3/2、r=3，存在g,h:ℝ²→ℝ²及x，取常值g=(1,2)、h=(1,4)、x=0。p,q≥1且1/p+1/q=1，r>0；按有限维ℓp范数定义，||g(x)||p^r||h(x)||q^r-|Σg(x)ᵢh(x)ᵢ|^r=0，但不存在不全为零的实数a,b使a·g(x)=b·h(x)。此结论只记录命题2的具体反例特例，不授予整篇论文结论。"
    return request(
        "Hölder等号的二维精确见证", original, goal, {"steps": {"lemmas": lemmas, "then": body}}
    )


def norm_power():
    u, p = var("u"), var("p")
    return request(
        "有限维ℓp范数的p次幂",
        "任意三维实向量u及正实数p，按显式有限维ℓp表达式定义，||u||p^p=Σ|uᵢ|^p；此幂律不声称p<1时满足范数公理。",
        b("eq", rpow(lp(p, u), p), total(p, u)),
        rule("lp_norm_rpow", [p, u], [hyp("positive")]),
        variables=[{"name": "u", "type": {"finite_vector": 3}}, {"name": "p", "type": "Real"}],
        assumptions=[{"name": "positive", "formula": b("lt", real(0), p)}],
    )


def rational_power():
    return request(
        "有理实幂的精确根证书",
        "4^(3/2)=8，使用非负根2及2²=4证书，不使用浮点近似。",
        b("eq", rpow(real(4), real(3, 2)), real(8)),
        compute(
            {
                "rational_power": {
                    "base": real(4),
                    "root": real(2),
                    "numerator": 3,
                    "denominator": 2,
                    "nonnegative": compute(),
                    "root_power": compute(),
                }
            }
        ),
    )


def wrong_equality():
    target = deepcopy(holder_witness())
    target.update(title="错误等号负对照", original="负对照：错误地声称向量(1,2)、(1,4)线性相关。")
    target["goal"] = dependent(vec(1, 2), vec(1, 4))
    target["proof"] = {"tactic": "grind"}
    return target


def examples():
    return [
        {
            "id": key,
            "title": label,
            "nodes": [{"id": "root", "kind": "problem", "depends_on": [], "target": fn()}],
        }
        for key, label, fn in (
            ("finite-lp-holder-witness", "Hölder等号 · 二维精确见证", holder_witness),
            ("finite-lp-norm-power", "有限维ℓp · 正实数p的幂", norm_power),
            ("finite-lp-rational-power", "实幂 · 有理根证书", rational_power),
        )
    ]
