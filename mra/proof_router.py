"""Explicit version routing; old plans keep their original, immutable verifier."""

from pathlib import Path
from .domain import digest
from .formalizer import exact_object
from .research_flow import atomic, lease
from .search_evaluation import _envelope
from .tasks import _identifier
from .proof_research import ProofResearchStore
from .proof_logic_research import LogicProofResearchStore
from .proof_language import MODE as V1
from .proof_logic import MODE as V2
from .optimization_language import MODE as V3
from .finite_lp_language import MODE as V4, catalog as language_catalog
from .finite_lp_research import FiniteLpResearchStore
from .finite_lp_examples import examples as finite_lp_examples
from .optimization_research import OptimizationResearchStore
from .optimization_examples import examples as optimization_examples
from .proof_examples import examples as old_examples
from .proof_logic_examples import examples as new_examples

SCHEMA = "versioned-proof-research-v1"


def catalog():
    return {
        "schema": SCHEMA,
        "default_language": V4,
        "supported_languages": [V1, V2, V3, V4],
        "language": language_catalog(),
        "examples": [
            *finite_lp_examples(),
            *optimization_examples(),
            *new_examples(),
            *old_examples(),
        ],
    }


class ProofResearchRouter:
    def __init__(self, root):
        self.base = Path(root).resolve() / "artifacts/proof-router"
        if self.base.resolve() != self.base:
            raise ValueError("Proof routing storage cannot be redirected")
        self.base.mkdir(parents=True, exist_ok=True)
        self.stores = {
            V1: ProofResearchStore(root),
            V2: LogicProofResearchStore(root),
            V3: OptimizationResearchStore(root),
            V4: FiniteLpResearchStore(root),
        }
        self.next_version = 0

    def for_request(self, raw):
        if not isinstance(raw, dict) or not isinstance(raw.get("nodes"), list) or not raw["nodes"]:
            raise ValueError("A versioned proof request requires complete nodes")
        versions = set()
        for node in raw["nodes"]:
            if not isinstance(node, dict) or not isinstance(node.get("target"), dict):
                raise ValueError("Every proof node requires a versioned target")
            version = node["target"].get("schema")
            if not isinstance(version, str) or version not in self.stores:
                raise ValueError("Unknown proof language; no fallback or automatic migration")
            versions.add(version)
        if len(versions) != 1:
            raise ValueError("One graph must use one explicit proof language version")
        return self.stores[versions.pop()]

    def for_graph(self, key):
        if isinstance(key, str) and key.startswith("proof4flow-"):
            return self.stores[V4]
        if isinstance(key, str) and key.startswith("proof3flow-"):
            return self.stores[V3]
        if isinstance(key, str) and key.startswith("proof2flow-"):
            return self.stores[V2]
        if isinstance(key, str) and key.startswith("proofflow-"):
            return self.stores[V1]
        raise ValueError("Unknown versioned proof graph identifier")

    def preview(self, raw):
        return self.for_request(raw).preview(raw)

    def create(self, raw):
        exact_object(raw, {"request_id", "request", "plan_fingerprint"}, "Versioned proof creation")
        _identifier(raw["request_id"])
        store = self.for_request(raw["request"])
        plan = store.preview(raw["request"])
        if plan["plan_fingerprint"] != raw["plan_fingerprint"]:
            raise ValueError("Complete versioned proof preview changed")
        version = next(v for v, provider in self.stores.items() if provider is store)
        identity = {
            "request_id": raw["request_id"],
            "version": version,
            "submission_fingerprint": digest(raw),
        }
        with lease(self.base):
            path = self.base / ("request-" + digest(raw["request_id"]) + ".json")
            if path.exists():
                if _envelope(path) != identity:
                    raise ValueError(
                        "Request ID already belongs to another proof or language version"
                    )
            else:
                for old_version, provider in self.stores.items():
                    for directory in provider.paths():
                        record = provider.read(directory.name)
                        if record["request_id"] == raw["request_id"] and (
                            old_version != version
                            or record["submission_fingerprint"] != digest(raw)
                        ):
                            raise ValueError(
                                "Request ID belongs to an existing proof; no cross-version migration"
                            )
                atomic(path, identity)
            return store.create(raw)

    def report(self, key):
        return self.for_graph(key).report(key)

    def read(self, key):
        return self.for_graph(key).read(key)

    def directory(self, key):
        return self.for_graph(key).directory(key)

    def checker(self, key):
        return self.for_graph(key).checker(key)

    def preview_node(self, key, raw):
        return self.for_graph(key).preview_node(key, raw)

    def submit(self, key, raw):
        return self.for_graph(key).submit(key, raw)

    def cancel(self, key, raw):
        return self.for_graph(key).cancel(key, raw)

    def withdraw(self, key, raw):
        return self.for_graph(key).withdraw(key, raw)

    def catalog(self, project_id, branch_id):
        values = [store.catalog(project_id, branch_id) for store in self.stores.values()]
        return {
            **values[-1],
            "schema": SCHEMA,
            "graphs": sorted(
                [
                    {**graph, "language_version": version}
                    for version, value in zip(self.stores, values, strict=True)
                    for graph in value["graphs"]
                ],
                key=lambda graph: (graph["created_at"], graph["graph_id"]),
            ),
            "examples": catalog()["examples"],
            "limits_per_version": {
                version: value["limits"] for version, value in zip(self.stores, values, strict=True)
            },
        }

    def work_once(self, graph_id=None):
        if graph_id is not None:
            return self.for_graph(graph_id).work_once(graph_id)
        versions = list(self.stores)
        for offset in range(len(versions)):
            index = (self.next_version + offset) % len(versions)
            result = self.stores[versions[index]].work_once()
            if result["worker_state"] == "EXECUTED":
                self.next_version = (index + 1) % len(versions)
                return result
        return {"worker_state": "IDLE"}

    def recover(self):
        return {
            "versions": {version: store.recover() for version, store in self.stores.items()},
            "automatic_retry": False,
        }
