"""Known PDE graph templates; unbound seeds never imply evidence."""

from copy import deepcopy
from .pde_language_examples import example


def examples():
    nodes = []
    for name, key, parent in (
        ("integration-by-parts", "ibp", None),
        ("zero-boundary-green", "green", "ibp"),
        ("poisson-smooth-test", "test", "green"),
        ("poisson-energy", "energy", "test"),
    ):
        target = example(name)
        if parent:
            target["sources"] = [{"name": parent, "node_id": parent}]
        nodes.append(
            {
                "id": key,
                "kind": "problem" if key == "energy" else "lemma",
                "depends_on": [parent] if parent else [],
                "target": target,
            }
        )
    return [
        {
            "id": "scalar",
            "title": "实数 Cauchy–Schwarz · 单目标",
            "nodes": [
                {
                    "id": "problem",
                    "kind": "problem",
                    "depends_on": [],
                    "target": example("scalar-cauchy-schwarz"),
                }
            ],
        },
        {
            "id": "energy-chain",
            "title": "一维 Poisson · 四步能量链",
            "nodes": deepcopy(nodes),
            "seed_required": True,
        },
    ]
