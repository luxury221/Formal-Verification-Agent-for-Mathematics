"""Author-visible structure proof reuse programs; not an independent benchmark."""

from copy import deepcopy

from .structure_research_examples import example as original_example


def var(name):
    return {"var": name}


def call(name, *args):
    return {"call": {"name": name, "args": list(args)}}


def eq(a, b):
    return {"eq": [a, b]}


def unit(x):
    return call("g_mul", call("g_one"), x)


def left(x, n):
    return call("map", call("g_power", x, n))


def right(x, n):
    return call("h_power", call("map", x), n)


def example():
    graph = original_example()
    a = deepcopy(graph["nodes"][-1]["target"])
    a["title"] = a["original"] = "引理：完整幺半群同态保持自然数幂"
    b = deepcopy(a)
    b["title"] = b["original"] = "将已证同态幂引理实例化到单位元乘积"
    b["goal"] = eq(left(unit(var("x")), var("n")), right(unit(var("x")), var("n")))
    b["proof"] = {"method": "rfl"}
    c = deepcopy(b)
    c["title"] = c["original"] = "两项同态幂等式在目标幺半群乘法下组合"
    c["variables"] = [
        {"name": "x", "type": "g"},
        {"name": "y", "type": "g"},
        {"name": "n", "type": "Nat"},
        {"name": "m", "type": "Nat"},
    ]
    c["goal"] = eq(
        call("h_mul", left(unit(var("x")), var("n")), left(var("y"), var("m"))),
        call("h_mul", right(unit(var("x")), var("n")), right(var("y"), var("m"))),
    )
    graph["nodes"] = [
        {"id": "power", "kind": "lemma", "depends_on": [], "target": a},
        {"id": "lifted", "kind": "lemma", "depends_on": ["power"], "target": b},
        {"id": "problem", "kind": "problem", "depends_on": ["lifted", "power"], "target": c},
    ]
    return graph


def application(alias, terms):
    return {
        "lemma": alias,
        "terms": terms,
        "proofs": [{"hyp": law["name"]} for law in original_example()["laws"]],
    }


def request(graph_id, node="lifted", source=None):
    sources = [{"alias": "power", "item_id": f"node.{graph_id}.power"}]
    proof = application("power", [unit(var("x")), var("n")])
    if node == "problem":
        sources = [{"alias": "lifted", "item_id": source}, *sources]
        proof = {
            "trans": [
                {
                    "congr": {
                        "variable": {"name": "z", "type": "h"},
                        "body": call("h_mul", var("z"), left(var("y"), var("m"))),
                        "proof": application("lifted", [var("x"), var("n")]),
                    }
                },
                {
                    "congr": {
                        "variable": {"name": "z", "type": "h"},
                        "body": call("h_mul", right(unit(var("x")), var("n")), var("z")),
                        "proof": application("power", [var("y"), var("m")]),
                    }
                },
            ]
        }
    elif source is not None:
        sources[0]["item_id"] = source
    return {
        "schema": "structure-proof-composition-v1",
        "graph_id": graph_id,
        "node_id": node,
        "sources": sources,
        "proof": proof,
        "timeout_seconds": 30,
    }
