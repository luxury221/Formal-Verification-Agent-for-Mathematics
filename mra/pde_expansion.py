"""Capture-avoiding expansion of bounded PDE proof programs into one exact final target.

Intermediate statements remain E0 descriptions. Only the final complete target is
submitted to the unchanged sealed checker; this is not a cache of node evidence.
"""

from copy import deepcopy
from .domain import canonical, digest
from .pde_language import compile_request
from .pde_research_graph import compile_graph

SCHEMA = "pde-proof-expansion-v1"


def expand_graph(raw):
    graph = compile_graph(raw)
    by_id = {n["id"]: n["target"] for n in graph["request"]["nodes"]}
    reserved, serial, operations = set(), 0, 0
    external, reached = [], {graph["problem_id"]}

    def reserve(value):
        if isinstance(value, str):
            reserved.add(value)
        elif isinstance(value, dict):
            for child in value.values():
                reserve(child)
        elif isinstance(value, list):
            for child in value:
                reserve(child)

    reserve(raw)

    def fresh():
        nonlocal serial
        while True:
            serial += 1
            name = "mraInline" + str(serial)
            if name not in reserved:
                reserved.add(name)
                return name

    def walk(value, variables, hypotheses, owner, depth=0):
        nonlocal operations
        operations += 1
        if operations > 12000 or depth > 64:
            raise ValueError("Expanded proof exceeds its compilation work budget")

        def visit(child, vs=None, hs=None, node=None):
            return walk(
                child,
                variables if vs is None else vs,
                hypotheses if hs is None else hs,
                owner if node is None else node,
                depth + 1,
            )

        if isinstance(value, list):
            return [visit(child) for child in value]
        if not isinstance(value, dict):
            return deepcopy(value)
        if len(value) != 1:
            return {key: visit(child) for key, child in value.items()}
        op, item = next(iter(value.items()))
        if op == "var":
            return deepcopy(variables.get(item, value))
        if op == "hyp":
            return deepcopy(hypotheses.get(item, value))
        if op == "use_hyp":
            proof = hypotheses.get(item["name"], {"hyp": item["name"]})
            if set(proof) != {"hyp"}:
                raise ValueError("Applied condition must remain a named hypothesis when expanded")
            return {
                op: {
                    "name": proof["hyp"],
                    "terms": visit(item["terms"]),
                    "proofs": visit(item["proofs"]),
                }
            }
        if op == "source_apply":
            source = next(s for s in by_id[owner]["sources"] if s["name"] == item["source"])
            terms, proofs = visit(item["terms"]), visit(item["proofs"])
            if "node_id" in source:
                key = source["node_id"]
                target = by_id[key]
                if len(terms) != len(target["variables"]) or len(proofs) != len(
                    target["assumptions"]
                ):
                    raise ValueError(
                        "Internal application must supply every variable and condition"
                    )
                if set(target["proof"]) != {"term"}:
                    raise ValueError("Expansion requires structured proof terms at every used node")
                reached.add(key)
                return visit(
                    target["proof"]["term"],
                    dict(zip((v["name"] for v in target["variables"]), terms, strict=True)),
                    dict(zip((h["name"] for h in target["assumptions"]), proofs, strict=True)),
                    key,
                )
            descriptor = {k: v for k, v in source.items() if k != "name"}
            selected = next(
                (s for s in external if {k: v for k, v in s.items() if k != "name"} == descriptor),
                None,
            )
            if selected is None:
                selected = {"name": "external" + str(len(external)), **descriptor}
                external.append(selected)
            return {op: {"source": selected["name"], "terms": terms, "proofs": proofs}}
        if op in (
            "lambda",
            "forall",
            "exists",
            "ae",
            "interval_integral",
            "intro",
            "interval_congr",
        ):
            name = fresh()
            body = "proof" if op in ("intro", "interval_congr") else "body"
            fields = {k: visit(v) for k, v in item.items() if k not in ("name", body, "membership")}
            nested = {**variables, item["name"]: {"var": name}}
            hs = hypotheses
            if op == "interval_congr":
                member = fresh()
                fields["membership"] = member
                hs = {**hs, item["membership"]: {"hyp": member}}
            return {op: {**fields, "name": name, body: visit(item[body], nested, hs)}}
        if op == "intro_hyp":
            name = fresh()
            return {
                op: {
                    "name": name,
                    "formula": visit(item["formula"]),
                    "proof": visit(item["proof"], hs={**hypotheses, item["name"]: {"hyp": name}}),
                }
            }
        return {op: visit(item)}

    key = graph["problem_id"]
    request = deepcopy(by_id[key])
    if set(request["proof"]) != {"term"}:
        raise ValueError("Expansion requires a structured final proof program")
    request["proof"] = {"term": walk(request["proof"]["term"], {}, {}, key)}
    request["sources"] = external
    if reached != set(by_id):
        raise ValueError("Scheduling-only nodes cannot be claimed as expanded proof steps")
    # Keep the original compiler's complete type, syntax limits and worker budget.
    compiled = compile_request(request)
    if compiled["formal_statement"] != graph["node_plans"][key]["formal_statement"]:
        raise ValueError("Expansion changed the original complete target")
    return {
        "schema": SCHEMA,
        "source_graph": deepcopy(raw),
        "source_graph_fingerprint": graph["draft_fingerprint"],
        "nodes": [{"id": key, "kind": "problem", "depends_on": [], "target": request}],
        "steps": [
            {
                "id": k,
                "formal_statement": graph["node_plans"][k]["formal_statement"],
                "math_verdict": "UNKNOWN",
                "evidence_level": "E0",
            }
            for k in graph["order"]
        ],
        "complete_final_target_unchanged": True,
        "intermediate_types_kernel_checked": False,
        "expanded_request_fingerprint": digest(request),
        "expanded_request_bytes": len(canonical(request)),
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
    }
