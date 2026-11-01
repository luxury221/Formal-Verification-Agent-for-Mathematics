"""Project-authored explicit iteration target; not a model or blind benchmark."""


def example():
    def var(n):
        return {"var": n}

    def call(n, *args):
        return {"call": {"name": n, "args": list(args)}}

    definitions = []
    for name, body in (
        ("iter_f", call("f", var("acc"))),
        ("iter_g", call("g", var("acc"))),
        ("iter_fg", call("f", call("g", var("acc")))),
    ):
        definitions.append(
            {
                "name": name,
                "original": name + "(0,x)=x，以显式 Nat.rec 迭代声明的步进函数。",
                "parameters": [{"name": "n", "type": "Nat"}, {"name": "x", "type": "a"}],
                "result_type": "a",
                "body": {
                    "nat_fold": {
                        "index": var("n"),
                        "zero": var("x"),
                        "step": {"index": "k", "value": "acc", "body": body},
                    }
                },
            }
        )
    n, x = var("n"), var("x")
    return {
        "schema": "explicit-structures-v1",
        "title": "交换函数的迭代与观察",
        "original": "任意类型 a 上，若 f 与 g 交换，则复合函数迭代 n 次，等于先迭代 g 再迭代 f；经任意 h 观察后仍相等。保留交换条件和全部定义。",
        "carriers": [{"name": "a", "original": "任意 Type 载体"}],
        "operations": [
            {
                "name": name,
                "parameters": ["a"],
                "result_type": "a",
                "original": "任意显式函数 " + name,
            }
            for name in ("f", "g", "h")
        ],
        "definitions": definitions,
        "variables": [{"name": "n", "type": "Nat"}, {"name": "x", "type": "a"}],
        "assumptions": [
            {
                "name": "commutes",
                "formula": {
                    "forall": {
                        "binders": [{"name": "u", "type": "a"}],
                        "body": {
                            "eq": [call("f", call("g", var("u"))), call("g", call("f", var("u")))]
                        },
                    }
                },
            }
        ],
        "goal": {
            "eq": [
                call("h", call("iter_fg", n, x)),
                call("h", call("iter_f", n, call("iter_g", n, x))),
            ]
        },
        "timeout_seconds": 20,
    }
