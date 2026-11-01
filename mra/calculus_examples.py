"""Known compositional calculus examples; not a blind or novelty benchmark."""

from copy import deepcopy
from .analysis_examples import op, r, rule, v, examples as analysis_examples
from .calculus_language import MODE

REAL = "Real"
FUNCTION = {"function": [REAL, REAL]}
VOLUME = {"volume": True}
ZERO = {"measure_zero": True}


def binding(name, typ=REAL):
    return {"name": name, "type": deepcopy(typ)}


def lam(name, body):
    return {"lambda": {"name": name, "type": REAL, "body": body}}


def integral(body, a, b, measure=VOLUME, name="t"):
    return {
        "interval_integral": {
            "name": name,
            "lower": a,
            "upper": b,
            "measure": deepcopy(measure),
            "body": body,
        }
    }


def has_deriv(f, df, x):
    return {"has_deriv_at": {"function": f, "derivative": df, "at": x}}


def integrable(f, a, b, measure=VOLUME):
    return {
        "interval_integrable": {"function": f, "lower": a, "upper": b, "measure": deepcopy(measure)}
    }


def ae(name, measure, body):
    return {"ae": {"name": name, "measure": deepcopy(measure), "body": body}}


def case(identifier, original, variables, assumptions, goal, proof):
    return {
        "id": identifier,
        "expected": "COMPLETED",
        "request": {
            "schema": MODE,
            "title": identifier,
            "original": original,
            "spaces": [],
            "variables": variables,
            "assumptions": assumptions,
            "goal": goal,
            "proof": {"term": proof},
            "timeout_seconds": 40,
        },
    }


def examples():
    a, b, c, x, df, dg, f, g, mu, t = [
        v(n) for n in ("a", "b", "c", "x", "df", "dg", "f", "g", "mu", "t")
    ]
    ft, gt = op("apply", f, t), op("apply", g, t)
    ident = lam("t", t)
    derivative_id = case(
        "derivative-id",
        "实恒等函数在任意实点x有导数1。",
        [binding("x")],
        [],
        has_deriv(ident, r(1), x),
        rule("has_deriv_id", [x]),
    )
    constant = case(
        "derivative-constant",
        "实常函数c在任意实点x有导数0。",
        [binding("c"), binding("x")],
        [],
        has_deriv(lam("t", c), r(0), x),
        rule("has_deriv_const", [x, c]),
    )
    assumptions = [
        {"name": "hf", "formula": has_deriv(f, df, x)},
        {"name": "hg", "formula": has_deriv(g, dg, x)},
    ]
    sum_fn = lam("t", op("add", ft, gt))
    add_proof = rule("has_deriv_add", proofs=[{"hyp": "hf"}, {"hyp": "hg"}])
    derivative_add = case(
        "derivative-add",
        "f,g在x处分别有导数df,dg，保留两条件，和有导数df+dg。",
        [
            binding("f", FUNCTION),
            binding("g", FUNCTION),
            binding("df"),
            binding("dg"),
            binding("x"),
        ],
        assumptions,
        has_deriv(sum_fn, op("add", df, dg), x),
        add_proof,
    )
    const_integral = case(
        "interval-constant",
        "体积下常数积分等于(b-a)c，端点不加顺序。",
        [binding("a"), binding("b"), binding("c")],
        [],
        op("eq", integral(c, a, b), op("mul", op("sub", b, a), c)),
        rule("interval_const", [a, b, c]),
    )
    interval_add = case(
        "interval-add",
        "f,g分别区间可积时积分可加，两个可积条件保留。",
        [binding("f", FUNCTION), binding("g", FUNCTION), binding("a"), binding("b")],
        [
            {"name": "hf", "formula": integrable(f, a, b)},
            {"name": "hg", "formula": integrable(g, a, b)},
        ],
        op(
            "eq",
            integral(op("add", ft, gt), a, b),
            op("add", integral(ft, a, b), integral(gt, a, b)),
        ),
        rule("interval_add", proofs=[{"hyp": "hf"}, {"hyp": "hg"}]),
    )
    reversal = case(
        "interval-reversal",
        "一般测度及任意实函数的有向积分反向为负，采用总定义。",
        [binding("f", FUNCTION), binding("mu", "MeasureReal"), binding("a"), binding("b")],
        [],
        op("eq", integral(ft, b, a, mu), {"neg": integral(ft, a, b, mu)}),
        rule("interval_reverse", [f, mu, a, b]),
    )
    zero = case(
        "zero-measure",
        "零测度下任意实函数的区间积分为0。",
        [binding("f", FUNCTION), binding("a"), binding("b")],
        [],
        op("eq", integral(ft, a, b, ZERO), r(0)),
        rule("interval_zero_measure", [f, a, b]),
    )
    m_ab = {"measure_ioc_real": {"measure": mu, "lower": a, "upper": b}}
    m_ba = {"measure_ioc_real": {"measure": mu, "lower": b, "upper": a}}
    measured = case(
        "general-measure",
        "一般测度常数积分是两个Ioc测度实值之差乘c，toReal不证明有限。",
        [binding("mu", "MeasureReal"), binding("a"), binding("b"), binding("c")],
        [],
        op("eq", integral(c, a, b, mu), op("mul", op("sub", m_ab, m_ba), c)),
        rule("interval_const_measure", [mu, a, b, c]),
    )
    membership = {"in_uIoc": {"term": t, "lower": a, "upper": b}}
    bound = case(
        "ae-norm-bound",
        "体积下无序区间上几乎处处范数界给出积分范数界；不额外推断可积或逐点界。",
        [binding("f", FUNCTION), binding("a"), binding("b"), binding("c")],
        [
            {
                "name": "hbound",
                "formula": ae("t", VOLUME, op("imp", membership, op("le", {"norm": ft}, c))),
            }
        ],
        op("le", {"norm": integral(ft, a, b)}, op("mul", c, {"abs": op("sub", b, a)})),
        rule("interval_ae_norm_bound", [f, a, b, c], [{"hyp": "hbound"}]),
    )
    shadow = case(
        "shadowed-endpoint",
        "外层变量t作下端点，积分内层同名t仅局部绑定；结果为(b-t)c。",
        [binding("t"), binding("b"), binding("c")],
        [],
        op("eq", integral(c, t, b, name="t"), op("mul", op("sub", b, t), c)),
        rule("interval_const", [t, b, c]),
    )
    quantified = case(
        "quantified-derivative",
        "对每个实点x，恒等函数有导数1；证明引入的局部变量可使用不同名称。",
        [],
        [],
        {"forall": {"name": "x", "type": REAL, "body": has_deriv(ident, r(1), x)}},
        {"intro": {"name": "u", "type": REAL, "proof": rule("has_deriv_id", [v("u")])}},
    )
    composed = deepcopy(derivative_add)
    composed.update(id="composed-derivative")
    composed["request"].update(
        title="composed-derivative",
        original="保留两HasDerivAt条件，通过导数加法再取deriv值，不能从默认值推可导。",
        goal=op("eq", {"deriv": {"function": sum_fn, "at": x}}, op("add", df, dg)),
        proof={"term": rule("deriv_of_has_deriv", proofs=[add_proof])},
    )
    negatives = []
    for source, identifier, original in (
        (derivative_add, "missing-derivative-condition", "失败对照：遗漏第二个可导条件。"),
        (interval_add, "missing-integrability", "失败对照：遗漏第二个区间可积条件。"),
    ):
        bad = deepcopy(source)
        bad.update(id=identifier, expected="REJECTED")
        bad["request"].update(title=identifier, original=original)
        bad["request"]["assumptions"] = bad["request"]["assumptions"][:1]
        # A known typed expression is supplied as the missing proof; Lean must reject it.
        bad["request"]["proof"]["term"]["rule"]["proofs"][1] = {"hyp": "hf"}
        negatives.append(bad)
    orientation = deepcopy(const_integral)
    orientation.update(id="wrong-orientation", expected="REJECTED")
    orientation["request"].update(
        title="wrong-orientation",
        original="失败对照：把b-a改为a-b。",
        goal=op("eq", integral(c, a, b), op("mul", op("sub", a, b), c)),
    )
    negatives.append(orientation)
    measure = deepcopy(measured)
    measure.update(id="wrong-measure", expected="REJECTED")
    measure["request"].update(
        title="wrong-measure",
        original="失败对照：把一般测度积分错误套为体积长度公式。",
        goal=op("eq", integral(c, a, b, mu), op("mul", op("sub", b, a), c)),
    )
    negatives.append(measure)
    pointwise = case(
        "ae-is-not-pointwise",
        "失败对照：任意测度下几乎处处f=0不能提升为每点f=0。",
        [binding("f", FUNCTION), binding("mu", "MeasureReal")],
        [{"name": "hae", "formula": ae("t", mu, op("eq", ft, r(0)))}],
        {"forall": {"name": "t", "type": REAL, "body": op("eq", ft, r(0))}},
        {"hyp": "hae"},
    )
    pointwise["expected"] = "REJECTED"
    negatives.append(pointwise)
    compatibility = deepcopy(analysis_examples()[5])
    compatibility.update(id="inner-energy-compatibility")
    compatibility["request"].update(
        schema=MODE, title="inner-energy-compatibility", timeout_seconds=40
    )
    return [
        derivative_id,
        constant,
        derivative_add,
        const_integral,
        interval_add,
        reversal,
        zero,
        measured,
        bound,
        shadow,
        quantified,
        composed,
        compatibility,
        *negatives,
    ]
