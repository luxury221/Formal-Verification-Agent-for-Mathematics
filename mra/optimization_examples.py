"""Complete paper obligations expressed with attained, globally bounded minima."""

from copy import deepcopy
from .proof_examples import equation25, rule
from .proof_logic_examples import var, real, binary, hyp, witness, rewrite
from .optimization_language import MODE


def square(x):
    return binary("pow", x, 2)


def objective(u, v, *, reverse=False):
    multiple = binary("smul", var("b"), v)
    difference = binary("sub", multiple, u) if reverse else binary("sub", u, multiple)
    return {"lambda": {"name": "b", "type": "Real", "body": square({"norm": difference})}}


def minimum(function, value):
    return {"is_minimum": {"function": function, "value": value}}


def minimum_value(u, v):
    return binary(
        "sub",
        square({"norm": u}),
        binary("div", square({"abs": binary("inner", u, v)}), square({"norm": v})),
    )


def base(title, *, nonzero=False):
    target = equation25()
    target.update(schema=MODE, title=title)
    u = binary("apply", var("g"), var("x"))
    v = binary("apply", var("h"), var("x"))
    if nonzero:
        target["assumptions"] = [
            {"name": "h_nonzero", "formula": {"not": binary("eq", v, {"zero": {"space": "E"}})}}
        ]
    return target, u, v


def stationary():
    target, u, v = base("公式26 · 明确求导与非零驻点", nonzero=True)
    target["variables"].append({"name": "beta", "type": "Real"})
    beta = var("beta")
    target["original"] = (
        "固定g,h:D→E及x:D，h(x)≠0；平方距离对β的导数为零，推出β=⟨g(x),h(x)⟩/‖h(x)‖²。"
    )
    target["goal"] = binary(
        "imp",
        binary("eq", {"deriv": {"function": objective(u, v), "at": beta}}, real(0)),
        binary("eq", beta, binary("div", binary("inner", u, v), square({"norm": v}))),
    )
    target["proof"] = {"term": rule("least_squares_stationary", [u, v, beta], [hyp("h_nonzero")])}
    return target


def minimum_formula():
    target, u, v = base("公式27 · 取得最小值与全局下界", nonzero=True)
    target["original"] = (
        "固定g,h:D→E及x:D，h(x)≠0；minβ ‖g(x)−βh(x)‖²=‖g(x)‖²−|⟨g(x),h(x)⟩|²/‖h(x)‖²，包括最小值取得和对所有β的全局下界。"
    )
    target["goal"] = minimum(objective(u, v), minimum_value(u, v))
    target["proof"] = {"term": rule("and_left", proofs=[rule("least_squares_minimum", [u, v])])}
    return target


def scaled_minimum():
    target, u, v = base("公式28 · 加权最小值恒等式", nonzero=True)
    loss = target["goal"]["le"][1]
    target["original"] = "固定g,h:D→E及x:D，h(x)≠0；ℓ(x)=‖h(x)‖²minβ‖g(x)−βh(x)‖²。"
    target["goal"] = {
        "exists": {
            "name": "m",
            "type": "Real",
            "body": binary(
                "and",
                minimum(objective(u, v), var("m")),
                binary("eq", loss, binary("mul", square({"norm": v}), var("m"))),
            ),
        }
    }
    target["proof"] = {"term": witness(minimum_value(u, v), rule("least_squares_minimum", [u, v]))}
    return target


def lemma1(*, author_step=False):
    title = "零向量与交换分支 · 完整双侧极小化" if author_step else "Lemma 1 · 完整双侧最小二乘等式"
    target, u, v = base(title)
    loss = target["goal"]["le"][1]
    target["original"] = (
        "任意g,h:D→E及x:D，ℓ(x)=‖g(x)‖²minβ‖βg(x)−h(x)‖²=‖h(x)‖²minβ‖g(x)−βh(x)‖²；两个最小值均取得，包含任一零向量，无新增非零假设。"
    )
    m, n = var("m"), var("n")
    body = binary(
        "and",
        minimum(objective(v, u, reverse=True), m),
        binary(
            "and",
            minimum(objective(u, v), n),
            binary(
                "and",
                binary("eq", loss, binary("mul", square({"norm": u}), m)),
                binary("eq", loss, binary("mul", square({"norm": v}), n)),
            ),
        ),
    )
    target["goal"] = {
        "exists": {
            "name": "m",
            "type": "Real",
            "body": {"exists": {"name": "n", "type": "Real", "body": body}},
        }
    }
    left = hyp("left_min")
    right = hyp("right_min")
    proof = rule(
        "and_intro",
        proofs=[
            rewrite(rule("and_left", proofs=[left]), "norm_sub_rev"),
            rule(
                "and_intro",
                proofs=[
                    rule("and_left", proofs=[right]),
                    rule(
                        "and_intro",
                        proofs=[
                            rewrite(
                                rule("and_right", proofs=[left]), "real_inner_comm", "mul_comm"
                            ),
                            rule("and_right", proofs=[right]),
                        ],
                    ),
                ],
            ),
        ],
    )
    swapped_loss = binary(
        "sub",
        binary("mul", square({"norm": v}), square({"norm": u})),
        square({"abs": binary("inner", v, u)}),
    )
    lemmas = [
        {
            "name": "left_min",
            "formula": binary(
                "and",
                minimum(objective(v, u), minimum_value(v, u)),
                binary("eq", swapped_loss, binary("mul", square({"norm": u}), minimum_value(v, u))),
            ),
            "proof": rule("least_squares_minimum", [v, u]),
        },
        {
            "name": "right_min",
            "formula": binary(
                "and",
                minimum(objective(u, v), minimum_value(u, v)),
                binary("eq", loss, binary("mul", square({"norm": v}), minimum_value(u, v))),
            ),
            "proof": rule("least_squares_minimum", [u, v]),
        },
    ]
    target["proof"] = {
        "term": {
            "steps": {
                "lemmas": lemmas,
                "then": witness(minimum_value(v, u), witness(minimum_value(u, v), proof)),
            }
        }
    }
    return target


def local_maximum_control():
    target, _, _ = base("负对照 · 驻点不是全局最小值")
    target.update(
        spaces=[],
        variables=[],
        assumptions=[],
        original="错误对照：−t²在0驻定，却声称0是其全局最小值。",
    )
    function = {"lambda": {"name": "t", "type": "Real", "body": {"neg": square(var("t"))}}}
    target["goal"] = minimum(function, real(0))
    target["proof"] = {
        "term": rule(
            "and_intro",
            proofs=[
                witness(real(0), {"arithmetic": {"method": "ring", "proofs": []}}),
                {"intro": {"name": "t", "type": "Real", "proof": rule("sq_nonneg", [var("t")])}},
            ],
        )
    }
    return target


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
            ("least-squares-stationary-v3", stationary()),
            ("least-squares-minimum-v3", minimum_formula()),
            ("lemma1-v3", lemma1()),
        )
    ]
