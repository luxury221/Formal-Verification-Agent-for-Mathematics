"""Known proof-composition examples, not benchmark or novelty claims."""

from copy import deepcopy

from .proof_language import MODE


def rule(name, terms=(), proofs=()):
    return {"rule": {"name": name, "terms": list(terms), "proofs": list(proofs)}}


def equation25():
    g = {"apply": [{"var": "g"}, {"var": "x"}]}
    h = {"apply": [{"var": "h"}, {"var": "x"}]}
    inner = {"inner": [g, h]}
    product = {"mul": [{"pow": [{"norm": g}, 2]}, {"pow": [{"norm": h}, 2]}]}
    square = {"pow": [{"abs": inner}, 2]}
    bound = {"le": [square, product]}
    raw_bound = {"le": [{"mul": [inner, inner]}, {"mul": [{"inner": [g, g]}, {"inner": [h, h]}]}]}
    return {
        "schema": MODE,
        "title": "公式25 · 完整函数、局部引理与显式改写",
        "original": "对任意g,h:ℝ^N→ℝ^M及x:ℝ^N，0≤‖g(x)‖₂²‖h(x)‖₂²−|⟨g(x),h(x)⟩|²=ℓ(x)。",
        "spaces": [{"name": name, "structure": "real-inner-product"} for name in ("D", "E")],
        "variables": [
            {"name": name, "type": {"function": [{"space": "D"}, {"space": "E"}]}}
            for name in ("g", "h")
        ]
        + [{"name": "x", "type": {"space": "D"}}],
        "assumptions": [],
        "goal": {"le": [{"real": [0, 1]}, {"sub": [product, square]}]},
        "proof": {
            "term": {
                "have": {
                    "name": "inner_bound",
                    "formula": raw_bound,
                    "proof": rule("real_inner_mul_inner_self_le", [g, h]),
                    "then": {
                        "have": {
                            "name": "square_bound",
                            "formula": bound,
                            "proof": {
                                "rewrite": {
                                    "proof": {"hyp": "inner_bound"},
                                    "equalities": [
                                        {"lemma": "real_inner_self_eq_norm_sq", "reverse": False},
                                        {"lemma": "pow_two", "reverse": True},
                                        {"lemma": "sq_abs", "reverse": False},
                                    ],
                                }
                            },
                            "then": rule("sub_nonneg_of_le", proofs=[{"hyp": "square_bound"}]),
                        }
                    },
                }
            }
        },
        "timeout_seconds": 40,
    }


def arithmetic():
    x, y = {"var": "x"}, {"var": "y"}
    return {
        "schema": MODE,
        "title": "显式前提与线性推导",
        "original": "任意实数x,y，若x≤y，则x+1≤y+1。",
        "spaces": [],
        "variables": [{"name": n, "type": "Real"} for n in ("x", "y")],
        "assumptions": [{"name": "ordered", "formula": {"le": [x, y]}}],
        "goal": {"le": [{"add": [x, {"real": [1, 1]}]}, {"add": [y, {"real": [1, 1]}]}]},
        "proof": {"term": {"arithmetic": {"method": "linarith", "proofs": [{"hyp": "ordered"}]}}},
        "timeout_seconds": 40,
    }


def examples():
    return [
        {
            "id": key,
            "title": request["title"],
            "nodes": [
                {"id": "root", "kind": "problem", "depends_on": [], "target": deepcopy(request)}
            ],
        }
        for key, request in (("equation25", equation25()), ("arithmetic", arithmetic()))
    ]
