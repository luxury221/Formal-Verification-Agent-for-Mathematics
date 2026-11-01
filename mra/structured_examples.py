"""Author-visible general algebra and genuine induction programs; not research scores."""

from copy import deepcopy

from .structured_terms import MODE


def examples():
    def v(n):
        return {"var": n}

    def b(n, t):
        return {"name": n, "type": t}

    def f(op, a, c):
        return {op: [a, c]}

    def call(n, *args):
        return {"call": {"name": n, "args": list(args)}}

    def all_(names, body, typ="g"):
        return {"forall": {"binders": [b(n, typ) for n in names], "body": body}}

    def operation(name, parameters, result, original):
        return {"name": name, "parameters": parameters, "result_type": result, "original": original}

    def law(name, formula):
        return {"name": name, "formula": formula}

    def rewrite(*rules):
        return {
            "method": "rewrite",
            "steps": [{"rule": rule, "reverse": False} for rule in rules],
            "finish": "rfl",
        }

    def example(title, carriers, operations, definitions, variables, assumptions, goal, proof):
        return {
            "schema": MODE,
            "title": title,
            "original": title,
            "carriers": carriers,
            "operations": operations,
            "definitions": definitions,
            "variables": variables,
            "assumptions": assumptions,
            "goal": goal,
            "proof": proof,
            "timeout_seconds": 30,
        }

    carrier = [{"name": "g", "original": "任意载体g，处于Type宇宙。"}]
    operations = [
        operation("mul", ["g", "g"], "g", "任意二元运算，不默认交换。"),
        operation("one", [], "g", "显式单位元参数。"),
    ]
    a, c, d, x, y, z, n, m = (v(t) for t in ("a", "b", "c", "x", "y", "z", "n", "m"))
    one = call("one")

    def mul(a, c):
        return call("mul", a, c)

    laws = [
        law("assoc", all_(["a", "b", "c"], f("eq", mul(mul(a, c), d), mul(a, mul(c, d))))),
        law("left_id", all_(["a"], f("eq", mul(one, a), a))),
        law("right_id", all_(["a"], f("eq", mul(a, one), a))),
    ]
    fold = {
        "nat_fold": {
            "index": n,
            "zero": one,
            "step": {"index": "k", "value": "acc", "body": mul(v("acc"), x)},
        }
    }
    power = {
        "name": "power",
        "original": "power(x,0)=one；power(x,k+1)=mul(power(x,k),x)。",
        "parameters": [b("x", "g"), b("n", "Nat")],
        "result_type": "g",
        "body": fold,
    }

    def pow_(a, i):
        return call("power", a, i)

    induction = {
        "method": "nat_induction",
        "variable": "n",
        "generalize": [],
        "base": rewrite("right_id"),
        "step": rewrite("@ih", "assoc"),
    }
    monoid = example(
        "任意满足显式幺半群定律的载体，mul(mul(one,x),mul(y,one))=mul(x,y)。",
        carrier,
        operations,
        [],
        [b("x", "g"), b("y", "g")],
        laws,
        f("eq", mul(mul(one, x), mul(y, one)), mul(x, y)),
        rewrite("left_id", "right_id"),
    )
    power_add = example(
        "任意显式幺半群中，power(x,m+n)=mul(power(x,m),power(x,n))；不需要交换律。",
        carrier,
        operations,
        [power],
        [b("x", "g"), b("m", "Nat"), b("n", "Nat")],
        laws,
        f("eq", pow_(x, f("add", m, n)), mul(pow_(x, m), pow_(x, n))),
        induction,
    )
    hom_operations = [
        operation("src_mul", ["g", "g"], "g", "源载体二元运算。"),
        operation("dst_mul", ["h", "h"], "h", "目标载体二元运算。"),
        operation("map", ["g"], "h", "显式映射参数。"),
    ]

    def dst(a, c):
        return call("dst_mul", a, c)

    hom = example(
        "两个载体之间保持乘法的映射，将三重乘积映为目标乘积；目标结合律显式保留。",
        [*carrier, {"name": "h", "original": "第二个任意载体h。"}],
        hom_operations,
        [],
        [b(t, "g") for t in ("x", "y", "z")],
        [
            law(
                "preserves",
                all_(
                    ["a", "b"],
                    f(
                        "eq",
                        call("map", call("src_mul", a, c)),
                        dst(call("map", a), call("map", c)),
                    ),
                ),
            ),
            law(
                "dst_assoc",
                all_(["a", "b", "c"], f("eq", dst(dst(a, c), d), dst(a, dst(c, d))), "h"),
            ),
        ],
        f(
            "eq",
            call("map", call("src_mul", call("src_mul", x, y), z)),
            dst(call("map", x), dst(call("map", y), call("map", z))),
        ),
        rewrite("preserves", "preserves", "dst_assoc"),
    )
    tri = {
        "name": "tri",
        "original": "tri(0)=0；tri(k+1)=tri(k)+(k+1)。",
        "parameters": [b("n", "Nat")],
        "result_type": "Nat",
        "body": {
            "nat_fold": {
                "index": n,
                "zero": {"nat": 0},
                "step": {
                    "index": "k",
                    "value": "acc",
                    "body": f("add", v("acc"), f("add", v("k"), {"nat": 1})),
                },
            }
        },
    }
    triangular = example(
        "对任意自然数n，2*tri(n)=n*(n+1)，其中tri由自然数递归显式定义。",
        [],
        [],
        [tri],
        [b("n", "Nat")],
        [],
        f("eq", f("mul", {"nat": 2}, call("tri", n)), f("mul", n, f("add", n, {"nat": 1}))),
        {
            "method": "nat_induction",
            "variable": "n",
            "generalize": [],
            "base": {"method": "rfl"},
            "step": {"method": "grind"},
        },
    )
    wrong_comm = example(
        "待检：只有幺半群定律却尝试断言mul(x,y)=mul(y,x)。",
        carrier,
        operations,
        [],
        [b("x", "g"), b("y", "g")],
        laws,
        f("eq", mul(x, y), mul(y, x)),
        {"method": "rfl"},
    )
    wrong_step = deepcopy(power_add)
    wrong_step["title"] = wrong_step["original"] = (
        "待检：正确幂加法目标，但归纳步只给rfl而没有使用归纳假设和结合律。"
    )
    wrong_step["proof"]["step"] = {"method": "rfl"}
    generalize = deepcopy(power_add)
    generalize["title"] = generalize["original"] = (
        "幂加法归纳时显式泛化m，并在步分支应用泛化归纳假设。"
    )
    generalize["proof"]["generalize"] = ["m"]
    return [
        deepcopy(value)
        for value in (monoid, hom, power_add, triangular, wrong_comm, wrong_step, generalize)
    ]
