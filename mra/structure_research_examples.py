"""An author-visible unital monoid homomorphism graph, not a research benchmark."""

from copy import deepcopy


def example():
    def v(n):
        return {"var": n}

    def binding(n, t):
        return {"name": n, "type": t}

    def call(n, *args):
        return {"call": {"name": n, "args": list(args)}}

    def eq(a, b):
        return {"eq": [a, b]}

    def forall(names, typ, body):
        return {"forall": {"binders": [binding(n, typ) for n in names], "body": body}}

    def rewrite(*rules):
        return {
            "method": "rewrite",
            "steps": [{"rule": n, "reverse": False} for n in rules],
            "finish": "rfl",
        }

    operations = []
    definitions = []
    laws = []
    a, b, c = (v(n) for n in ("a", "b", "c"))
    for typ in ("g", "h"):
        mul, one, power = typ + "_mul", typ + "_one", typ + "_power"
        operations.extend(
            [
                {
                    "name": mul,
                    "original": "显式" + typ + "乘法，不默认交换",
                    "parameters": [typ, typ],
                    "result_type": typ,
                },
                {
                    "name": one,
                    "original": "显式" + typ + "单位元",
                    "parameters": [],
                    "result_type": typ,
                },
            ]
        )
        laws.extend(
            [
                {
                    "name": typ + "_assoc",
                    "formula": forall(
                        ["a", "b", "c"],
                        typ,
                        eq(call(mul, call(mul, a, b), c), call(mul, a, call(mul, b, c))),
                    ),
                },
                {
                    "name": typ + "_left",
                    "formula": forall(["a"], typ, eq(call(mul, call(one), a), a)),
                },
                {
                    "name": typ + "_right",
                    "formula": forall(["a"], typ, eq(call(mul, a, call(one)), a)),
                },
            ]
        )
        definitions.append(
            {
                "name": power,
                "original": power + "(x,0)=one；power(x,k+1)=mul(power(x,k),x)",
                "parameters": [binding("x", typ), binding("n", "Nat")],
                "result_type": typ,
                "body": {
                    "nat_fold": {
                        "index": v("n"),
                        "zero": call(one),
                        "step": {"index": "k", "value": "acc", "body": call(mul, v("acc"), v("x"))},
                    }
                },
            }
        )
    operations.append(
        {
            "name": "map",
            "original": "保持乘法与单位元的显式映射",
            "parameters": ["g"],
            "result_type": "h",
        }
    )
    laws.extend(
        [
            {
                "name": "preserves_mul",
                "formula": forall(
                    ["a", "b"],
                    "g",
                    eq(
                        call("map", call("g_mul", a, b)),
                        call("h_mul", call("map", a), call("map", b)),
                    ),
                ),
            },
            {"name": "preserves_one", "formula": eq(call("map", call("g_one")), call("h_one"))},
        ]
    )

    def target(title, variables, goal, proof):
        return {
            "title": title,
            "original": title,
            "variables": variables,
            "assumptions": [],
            "goal": goal,
            "proof": proof,
            "timeout_seconds": 30,
        }

    x, y, z, n = (v(n) for n in ("x", "y", "z", "n"))
    unit = target(
        "源幺半群左右单位元化简",
        [binding("x", "g")],
        eq(call("g_mul", call("g_mul", call("g_one"), x), call("g_one")), x),
        rewrite("g_left", "g_right"),
    )
    product = target(
        "同态将源三重乘积映到目标结合乘积",
        [binding(q, "g") for q in ("x", "y", "z")],
        eq(
            call("map", call("g_mul", call("g_mul", x, y), z)),
            call("h_mul", call("map", x), call("h_mul", call("map", y), call("map", z))),
        ),
        rewrite("preserves_mul", "preserves_mul", "h_assoc"),
    )
    problem = target(
        "保持单位元和乘法的幺半群同态保持所有自然数幂",
        [binding("x", "g"), binding("n", "Nat")],
        eq(call("map", call("g_power", x, n)), call("h_power", call("map", x), n)),
        {
            "method": "nat_induction",
            "variable": "n",
            "generalize": [],
            "base": rewrite("preserves_one"),
            "step": rewrite("preserves_mul", "@ih"),
        },
    )
    return deepcopy(
        {
            "schema": "structure-project-research-v1",
            "carriers": [
                {"name": "g", "original": "源幺半群载体Type"},
                {"name": "h", "original": "目标幺半群载体Type"},
            ],
            "operations": operations,
            "definitions": definitions,
            "laws": laws,
            "nodes": [
                {"id": "unit", "kind": "lemma", "depends_on": [], "target": unit},
                {"id": "product", "kind": "lemma", "depends_on": ["unit"], "target": product},
                {"id": "problem", "kind": "problem", "depends_on": ["product"], "target": problem},
            ],
        }
    )
