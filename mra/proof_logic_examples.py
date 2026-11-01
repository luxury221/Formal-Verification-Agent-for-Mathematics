"""Explicit structured proofs of Cauchy-Schwarz equality, including zero vectors."""

from copy import deepcopy

from .proof_examples import equation25, rule as r
from .proof_logic import MODE


def var(name):
    return {"var": name}


def real(n):
    return {"real": [n, 1]}


def hyp(name):
    return {"hyp": name}


def binary(op, a, b):
    return {op: [a, b]}


def exists(name, formula):
    return {"exists": {"name": name, "type": "Real", "body": formula}}


def witness(term, proof):
    return {"exists_intro": {"witness": term, "proof": proof}}


def intro(name, formula, proof):
    return {"intro_hyp": {"name": name, "formula": formula, "proof": proof}}


def eliminate(proof, name, hypothesis, formula, then):
    return {
        "exists_elim": {
            "proof": proof,
            "name": name,
            "type": "Real",
            "hypothesis": hypothesis,
            "formula": formula,
            "then": then,
        }
    }


def entries(items):
    return [
        {"lemma": x, "reverse": False} if isinstance(x, str) else {"proof": x, "reverse": False}
        for x in items
    ]


def rewrite(proof, *items):
    return {"rewrite": {"proof": proof, "equalities": entries(items)}}


def normalize(*items):
    return {"normalize": {"equalities": entries(items)}}


def arithmetic(proof):
    return {"arithmetic": {"method": "nlinarith", "proofs": [proof]}}


def equality_case(*, proposition=False):
    target = equation25()
    target["schema"] = MODE
    u = binary("apply", var("g"), var("x"))
    v = binary("apply", var("h"), var("x"))
    zero = {"zero": {"space": "E"}}

    def smul(a, b):
        return binary("smul", a, b)

    def eq(a, b):
        return binary("eq", a, b)

    def iff(a, b):
        return binary("iff", a, b)

    def either(a, b):
        return binary("or", a, b)

    def both(a, b):
        return binary("and", a, b)

    def ne0(a):
        return {"not": eq(a, real(0))}

    a, b, scalar = var("a"), var("b"), var("r")
    norm_product = binary("mul", {"norm": u}, {"norm": v})
    inner_abs = {"abs": binary("inner", u, v)}
    norm_equality = eq(inner_abs, norm_product)
    scalar_equality = eq(v, smul(scalar, u))
    normalized = either(eq(u, zero), exists("r", scalar_equality))
    coefficients = both(either(ne0(a), ne0(b)), eq(smul(a, u), smul(b, v)))
    dependent = exists("a", exists("b", coefficients))
    residual = target["goal"]["le"][1]
    residual_zero = eq(residual, real(0))

    forward = {
        "or_cases": {
            "proof": hyp("normalized"),
            "left": {
                "name": "u_zero",
                "proof": witness(
                    real(1),
                    witness(
                        real(0),
                        r(
                            "and_intro",
                            proofs=[
                                r("or_left", proofs=[r("one_ne_zero")]),
                                normalize("one_smul", "zero_smul", hyp("u_zero")),
                            ],
                        ),
                    ),
                ),
            },
            "right": {
                "name": "multiple",
                "proof": eliminate(
                    hyp("multiple"),
                    "r",
                    "multiple_eq",
                    scalar_equality,
                    witness(
                        scalar,
                        witness(
                            real(1),
                            r(
                                "and_intro",
                                proofs=[
                                    r("or_right", proofs=[r("one_ne_zero")]),
                                    normalize("one_smul", hyp("multiple_eq")),
                                ],
                            ),
                        ),
                    ),
                ),
            },
        }
    }
    a_nonzero = {
        "or_cases": {
            "proof": r("and_left", proofs=[hyp("coefficients")]),
            "left": {"name": "a_nonzero", "proof": hyp("a_nonzero")},
            "right": {
                "name": "b_nonzero",
                "proof": r(
                    "false_elim", proofs=[r("not_elim", proofs=[hyp("b_nonzero"), hyp("b_zero")])]
                ),
            },
        }
    }
    u_zero = r(
        "iff_mp",
        proofs=[
            r("smul_eq_zero_iff", [a, u], [a_nonzero]),
            rewrite(r("and_right", proofs=[hyp("coefficients")]), hyp("b_zero"), "zero_smul"),
        ],
    )
    ratio_eq = rewrite(
        r(
            "eq_symm",
            proofs=[
                r(
                    "iff_mpr",
                    proofs=[
                        r("inv_smul_eq_iff", [b, smul(a, u), v], [hyp("b_zero")]),
                        r("and_right", proofs=[hyp("coefficients")]),
                    ],
                )
            ],
        ),
        "smul_smul",
        "div_eq_mul_inv",
        "mul_comm",
    )
    backward = eliminate(
        hyp("dependent"),
        "a",
        "second_coefficient",
        exists("b", coefficients),
        eliminate(
            hyp("second_coefficient"),
            "b",
            "coefficients",
            coefficients,
            {
                "by_cases": {
                    "name": "b_zero",
                    "formula": eq(b, real(0)),
                    "yes": r("or_left", proofs=[u_zero]),
                    "no": r("or_right", proofs=[witness(binary("div", a, b), ratio_eq)]),
                }
            },
        ),
    )
    normalized_to_coefficients = r(
        "iff_intro",
        proofs=[intro("normalized", normalized, forward), intro("dependent", dependent, backward)],
    )

    square_equality = eq(binary("pow", inner_abs, 2), binary("pow", norm_product, 2))
    square_iff = r(
        "sq_eq_sq_nonneg",
        proofs=[
            r("abs_nonneg", [binary("inner", u, v)]),
            r("mul_nonneg", proofs=[r("norm_nonneg", [u]), r("norm_nonneg", [v])]),
        ],
    )
    zero_to_norm = intro(
        "residual_zero",
        residual_zero,
        r("iff_mp", proofs=[hyp("squares"), arithmetic(hyp("residual_zero"))]),
    )
    norm_to_zero = intro(
        "norm_equality",
        norm_equality,
        arithmetic(r("iff_mpr", proofs=[hyp("squares"), hyp("norm_equality")])),
    )
    zero_iff = {
        "steps": {
            "lemmas": [
                {
                    "name": "squares",
                    "formula": iff(square_equality, norm_equality),
                    "proof": square_iff,
                }
            ],
            "then": r("iff_intro", proofs=[zero_to_norm, norm_to_zero]),
        }
    }
    lemmas = [
        {
            "name": "norm_dependence",
            "formula": iff(norm_equality, normalized),
            "proof": r("norm_cs_equality", [u, v]),
        },
        {
            "name": "coefficient_dependence",
            "formula": iff(normalized, dependent),
            "proof": normalized_to_coefficients,
        },
        {
            "name": "zero_norm_equality",
            "formula": iff(residual_zero, norm_equality),
            "proof": zero_iff,
        },
    ]
    norm_dep = r("iff_trans", proofs=[hyp("norm_dependence"), hyp("coefficient_dependence")])
    zero_dep = r("iff_trans", proofs=[hyp("zero_norm_equality"), norm_dep])
    if proposition:
        target["title"] = "命题1 · 完整非负性及线性相关零点条件"
        target["original"] = (
            "任意g,h:D→E及x:D，ℓ(x)=‖g(x)‖²‖h(x)‖²−|⟨g(x),h(x)⟩|²非负；ℓ(x)=0当且仅当存在不全为零的实数a,b使a·g(x)=b·h(x)。"
        )
        target["goal"] = both(target["goal"], iff(residual_zero, dependent))
        final = r("and_intro", proofs=[target["proof"]["term"], zero_dep])
    else:
        target["title"] = "柯西–施瓦茨等号条件 · 含零向量与存在证人"
        target["original"] = (
            "任意g,h:D→E及x:D，|⟨g(x),h(x)⟩|=‖g(x)‖‖h(x)‖当且仅当g(x),h(x)按非平凡系数定义线性相关，且等价于ℓ(x)=0；包含任一向量为零的情形。"
        )
        target["goal"] = both(iff(norm_equality, dependent), iff(residual_zero, dependent))
        final = r("and_intro", proofs=[norm_dep, zero_dep])
    target["proof"] = {"term": {"steps": {"lemmas": lemmas, "then": final}}}
    return target


def zero_counterexample():
    u, v = real(0), real(1)
    wrong = exists("r", binary("eq", v, binary("smul", var("r"), u)))
    residual = binary(
        "sub",
        binary("mul", binary("pow", {"norm": u}, 2), binary("pow", {"norm": v}, 2)),
        binary("pow", {"abs": binary("inner", u, v)}, 2),
    )
    zero_normalization = normalize(
        "Real.norm_eq_abs", "norm_zero", "inner_zero_left", "pow_two", "zero_mul", "sub_zero"
    )
    zero_normalization["normalize"]["equalities"][0]["reverse"] = True
    return {
        "schema": MODE,
        "title": "零向量边界 · 单向倍数写法的反例证书",
        "original": "取实内积空间ℝ中的u=0,v=1：CS差式为0，但不存在实数r使v=r·u。",
        "spaces": [],
        "variables": [],
        "assumptions": [],
        "goal": binary("and", binary("eq", residual, real(0)), {"not": wrong}),
        "proof": {
            "term": r(
                "and_intro",
                proofs=[
                    zero_normalization,
                    intro(
                        "wrong",
                        wrong,
                        eliminate(
                            hyp("wrong"),
                            "r",
                            "impossible",
                            wrong["exists"]["body"],
                            r(
                                "not_elim",
                                proofs=[
                                    r("one_ne_zero"),
                                    rewrite(hyp("impossible"), "smul_eq_mul", "mul_zero"),
                                ],
                            ),
                        ),
                    ),
                ],
            )
        },
        "timeout_seconds": 40,
    }


def examples():
    return [
        {
            "id": key,
            "title": target["title"],
            "nodes": [
                {"id": "root", "kind": "problem", "depends_on": [], "target": deepcopy(target)}
            ],
        }
        for key, target in (
            ("cs-equality-v2", equality_case()),
            ("proposition1-v2", equality_case(proposition=True)),
            ("zero-boundary-v2", zero_counterexample()),
        )
    ]
