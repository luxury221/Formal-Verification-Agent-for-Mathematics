"""Compile complete PDE graph targets; symbolic sources alone never authorize evidence."""

from copy import deepcopy
import re

from mra.analysis_language import LIMITS as TARGET_LIMITS
from mra.domain import canonical, digest
from mra.formalizer import exact_object
from mra.pde_language import compile_request

SCHEMA = "pde-project-graph-v1"
NODE_LIMIT = 8


def node_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", value):
        raise ValueError("Use a short literal PDE node identifier")
    return value


def bounded_graph(raw):
    """Bound the graph envelope independently; each target still uses its original limit."""
    pending, count = [(raw, 0)], 0
    while pending:
        value, depth = pending.pop()
        count += 1
        if (
            count > NODE_LIMIT * (TARGET_LIMITS["nodes"] + 32) + 16
            or depth > TARGET_LIMITS["depth"] + 4
        ):
            raise ValueError("PDE graph exceeds total node/depth budget")
        if isinstance(value, dict):
            if not all(isinstance(k, str) for k in value):
                raise ValueError("Graph keys must be ordinary JSON strings")
            pending.extend((v, depth + 1) for v in value.values())
        elif isinstance(value, list):
            pending.extend((v, depth + 1) for v in value)
        elif value is not None and type(value) not in (str, int, bool):
            raise ValueError("Graph requires ordinary JSON values without floats")
    if len(canonical(raw)) > NODE_LIMIT * TARGET_LIMITS["bytes"] + 4096:
        raise ValueError("PDE graph exceeds total byte budget")


def compile_graph(raw):
    """Compile full targets while keeping symbolic-source authorization explicitly false."""
    bounded_graph(raw)
    exact_object(raw, {"schema", "nodes"}, "PDE graph draft")
    if raw["schema"] != SCHEMA:
        raise ValueError("Select the draft graph schema explicitly")
    nodes = raw["nodes"]
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= NODE_LIMIT:
        raise ValueError("A graph requires 1-8 complete nodes")
    by_id = {}
    for node in nodes:
        exact_object(node, {"id", "kind", "depends_on", "target"}, "PDE draft node")
        key = node_id(node["id"])
        if key in by_id or node["kind"] not in ("lemma", "subproblem", "problem"):
            raise ValueError("Duplicate node or unsupported role")
        deps = node["depends_on"]
        if (
            not isinstance(deps, list)
            or len(deps) > NODE_LIMIT - 1
            or any(not isinstance(v, str) for v in deps)
            or len(set(deps)) != len(deps)
        ):
            raise ValueError("Use distinct prerequisite identifiers")
        for dep in deps:
            node_id(dep)
        by_id[key] = node
    problems = [key for key, node in by_id.items() if node["kind"] == "problem"]
    if len(problems) != 1:
        raise ValueError("Exactly one original problem is required")
    visiting, seen, order = set(), set(), []

    def visit(key):
        if key not in by_id or key in visiting:
            raise ValueError("Unknown prerequisite or cycle")
        if key in seen:
            return
        visiting.add(key)
        for dep in by_id[key]["depends_on"]:
            visit(dep)
        visiting.remove(key)
        seen.add(key)
        order.append(key)

    visit(problems[0])
    if len(seen) != len(nodes):
        raise ValueError("Every node must lead to the original problem")
    plans, templates = {}, {}
    for key in order:
        node = by_id[key]
        request = deepcopy(node["target"])
        if not isinstance(request, dict) or not isinstance(request.get("sources"), list):
            raise ValueError("Each node needs a complete PDE target and source list")
        symbolic, resolved = [], []
        for source in request["sources"]:
            if not isinstance(source, dict):
                raise ValueError("Source must be a typed object")
            if "node_id" in source:
                exact_object(source, {"name", "node_id"}, "Symbolic graph source")
                origin = node_id(source["node_id"])
                if origin not in node["depends_on"] or origin not in plans:
                    raise ValueError("A source must be a compiled direct prerequisite")
                statement = plans[origin]["formal_statement"]
                # This syntactic marker is never looked up in a ledger or accepted as E4.
                placeholder = "pdelang-" + digest({"draft_node": origin})[:32]
                resolved.append(
                    {
                        "name": source["name"],
                        "run_id": placeholder,
                        "type_fingerprint": digest(statement),
                    }
                )
                symbolic.append(
                    {
                        **deepcopy(source),
                        "formal_statement": statement,
                        "type_fingerprint": digest(statement),
                        "authorization_checked": False,
                    }
                )
            else:
                exact_object(source, {"name", "run_id", "type_fingerprint"}, "Fixed PDE source")
                resolved.append(deepcopy(source))
        request["sources"] = resolved
        plan = compile_request(request)
        assert plan["source_authorization_checked"] is False
        plans[key] = {
            "formal_statement": plan["formal_statement"],
            "type_fingerprint": digest(plan["formal_statement"]),
            "conditions": plan["conditions"],
            "variables": plan["variables"],
            "source_nodes": symbolic,
            "scheduling_prerequisites": deepcopy(node["depends_on"]),
            "source_authorization_checked": False,
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
        }
        templates[key] = request
    value = {
        "schema": SCHEMA,
        "request": deepcopy(raw),
        "order": order,
        "problem_id": problems[0],
        "node_plans": plans,
        "syntactic_templates": templates,
        "execution_supported": False,
        "source_authorization_checked": False,
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "limits": [
            "Compilation alone does not authorize sources or run a proof checker.",
            "Symbolic source edges are separate from scheduling-only edges.",
            "Production must re-audit sources, confirm a materialized full target, and use the sealed checker.",
        ],
    }
    value["draft_fingerprint"] = digest(value)
    return value


def bind_node_draft(graph, key, bindings):
    """Pure substitution preview. The sealed ledger must authorize every source later."""
    if not isinstance(graph, dict) or canonical(graph) != canonical(
        compile_graph(graph.get("request"))
    ):
        raise ValueError("Graph draft changed; recompile its complete request")
    node_id(key)
    if key not in graph["node_plans"]:
        raise ValueError("Unknown graph node")
    sources = graph["node_plans"][key]["source_nodes"]
    expected = {source["node_id"] for source in sources}
    if not isinstance(bindings, dict) or set(bindings) != expected:
        raise ValueError("Supply exactly the required predecessor run bindings")
    request = deepcopy(graph["syntactic_templates"][key])
    aliases = {source["name"]: source for source in sources}
    for source in request["sources"]:
        if source["name"] not in aliases:
            continue
        node = aliases[source["name"]]
        bound = bindings[node["node_id"]]
        exact_object(bound, {"run_id", "type_fingerprint"}, "Untrusted binding preview")
        if (
            not isinstance(bound["run_id"], str)
            or not re.fullmatch(r"pdelang-[a-f0-9]{32}", bound["run_id"])
            or bound["type_fingerprint"] != node["type_fingerprint"]
        ):
            raise ValueError("Binding must preserve the complete predecessor type")
        source.update(deepcopy(bound))
    compiled = compile_request(request)
    if compiled["formal_statement"] != graph["node_plans"][key]["formal_statement"]:
        raise ValueError("Binding changed the complete original target")
    return {
        "request": request,
        "formal_statement": compiled["formal_statement"],
        "source_authorization_checked": False,
        "execution_supported": False,
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
    }
