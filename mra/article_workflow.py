"""Paper obligations connect to existing research notebooks, never bypassing proof gates."""

from copy import deepcopy
import json
import re
import uuid

from .article_audit import MATH_CLASSES, annotation_identity, closure, evidence_identity
from .domain import digest
from .formalizer import exact_object
from .model_service import text_field
from .research_protocol import AREAS, BACKENDS, source_request
from .research_studio import StudioStore
from .tasks import _now


def snapshot_identity(row, block_id):
    return digest(
        {"source": row["source_fingerprint"], "obligation": annotation_identity(row, block_id)}
    )


def markdown_text(value):
    return re.sub(r"([\\`*_{}\[\]<>#!|])", r"\\\1", str(value)).replace("\r", "")


def review_markdown(report):
    counts = report["counts"]
    lines = [
        "# 论文推理核查报告",
        "",
        markdown_text(report["source"]["title"]),
        "",
        "全文状态：UNKNOWN。尚未完成全文认证；局部结论不能替代作者的完整论证。",
        "",
        "原文版本：" + markdown_text(report["source"]["url"]),
        "原文 SHA256：" + report["source"]["sha256"],
        "论文记录：" + report["article_id"] + "；修订：" + str(report["revision"]),
        "证据观察："
        + (
            "已显式复核，时间 " + report["checked_at"]
            if report["evidence_rechecked"]
            else "历史关联，未复核当前有效性"
        ),
        "",
        f"原文索引 {counts['source_blocks']} 块；人工整理 {counts['classified']} 块；未分类 {counts['unclassified']} 块。",
        f"当前有效义务 {counts['current_verified_obligations']}；已列未验证数学义务 {counts['unverified_mathematical_obligations']}。",
        "这些数量不代表全文证明完成率。依赖图只包含已经人工整理的关系。",
        "",
        "## 优先处理的义务",
        "",
    ]
    for item in report["workflow"]["frontier"][:40]:
        lines.append(
            "- "
            + markdown_text(item["block_id"])
            + "："
            + (
                "所列前提已闭合，可继续研究"
                if item["ready"]
                else "等待 " + markdown_text(", ".join(item["blocked_by"]))
            )
        )
    lines += ["", "## 已整理的数学义务", ""]
    for block in report["blocks"]:
        annotation = block["annotation"]
        if not annotation or annotation["classification"] not in MATH_CLASSES:
            continue
        lines += [
            "### " + markdown_text(block["id"]),
            "",
            "> " + markdown_text(annotation["statement"]).replace("\n", "\n> "),
            "",
            "结论："
            + block["claim_verdict"]
            + "；步骤状态："
            + block["step_status"]
            + "；作者推理核查："
            + block["author_proof_verdict"],
            "依赖："
            + markdown_text(", ".join(annotation["depends_on"]) or "未列出；不保证依赖已经穷尽"),
            "前提："
            + markdown_text("；".join(annotation["assumptions"]) or "未单独列出；需核对完整陈述"),
            "对应及缺口：" + markdown_text(annotation["note"]),
            "",
        ]
        for link in block["links"]:
            lines += [
                "- 关联 "
                + markdown_text(link["graph_id"])
                + " / "
                + markdown_text(link["node_id"])
                + "："
                + link["observation"]["status"],
                "  - 范围：" + markdown_text(link["scope"]),
                "  - 完整目标：" + markdown_text(link["identity"]["formal_statement"]),
                "  - 对应关系："
                + link["relation"]
                + "；复核人："
                + markdown_text(link["reviewer"]),
            ]
    lines += [
        "",
        "## 能力与信任边界",
        "",
        "原文解析、依赖梳理和语义对应仍需人工审查。研究任务及模型文字均不能授予证明状态。",
        "GAP_FOUND 表示本账本的验证缺口，不直接断言作者数学错误。反例特例独立记录，不能自动成为原命题的完整否定证明。",
        "模型未配置也可人工研究和调用已有验证器。本报告不说明论文新颖性、顶刊价值或开放难题求解能力。",
        "",
    ]
    return "\n".join(lines)


class ArticleWorkflow:
    def __init__(self, articles, *, studio=None):
        self.articles = articles
        self.studio = studio or StudioStore(articles.root)

    @staticmethod
    def workspace(row, workspace_id):
        value = next(
            (item for item in row.get("workspaces", []) if item["workspace_id"] == workspace_id),
            None,
        )
        if value is None:
            raise ValueError("Select a research task registered for this article")
        return value

    def workspace_state(self, row, workspace):
        if snapshot_identity(row, workspace["block_id"]) != workspace["snapshot_identity"]:
            return {"status": "STALE", "reason": "原文义务或传递前提已修改，需要重新创建研究任务。"}
        try:
            study = self.studio.read(workspace["study_id"])
            if study["source_fingerprint"] != workspace["study_source_fingerprint"]:
                raise ValueError("研究任务的完整原题或条件已变化。")
            return {
                "status": "CURRENT",
                "reason": "与当前义务对应；研究文字仍未验证。",
                "revision": study["revision"],
                "rounds": len(study["rounds"]),
                "transfers": len(study["transfers"]),
            }
        except (OSError, ValueError, KeyError, TypeError) as error:
            return {"status": "UNAVAILABLE", "reason": str(error)[:1000]}

    def enrich(self, report):
        row, _, _ = self.articles.read(report["article_id"])
        if row["revision"] != report["revision"]:
            raise ValueError("Article changed while reading; reload the current revision")
        by_id = {block["id"]: block for block in report["blocks"]}
        annotations = row["annotations"]
        mathematical = {
            key for key, value in annotations.items() if value["classification"] in MATH_CLASSES
        }
        order, seen = [], set()
        for key in mathematical:
            for dependency in closure(annotations, key):
                if dependency not in seen:
                    seen.add(dependency)
                    order.append(dependency)
        # Stable source order within each topological layer, including disconnected obligations.
        depth = {}
        for key in order:
            depth[key] = max(
                (depth[parent] + 1 for parent in annotations[key]["depends_on"]), default=0
            )
        source_order = {key: index for index, key in enumerate(by_id)}
        order.sort(key=lambda key: (depth[key], source_order[key]))
        descendants = {key: set() for key in order}
        for key in reversed(order):
            for parent in annotations[key]["depends_on"]:
                descendants[parent].update({key, *descendants[key]})
        graph, frontier = [], []
        for key in order:
            block, annotation = by_id[key], annotations[key]
            graph.append(
                {
                    "id": key,
                    "depth": depth[key],
                    "classification": annotation["classification"],
                    "status": block["step_status"],
                    "depends_on": annotation["depends_on"],
                    "section": block["section"],
                }
            )
            if block["step_status"] != "VERIFIED":
                frontier.append(
                    {
                        "block_id": key,
                        "ready": not block["blocked_by"],
                        "blocked_by": block["blocked_by"],
                        "downstream_obligations": len(descendants[key]),
                        "statement": annotation["statement"],
                    }
                )
        frontier.sort(
            key=lambda item: (
                not item["ready"],
                -item["downstream_obligations"],
                source_order[item["block_id"]],
            )
        )
        report["workspaces"] = [
            {
                **deepcopy(item),
                "observation": self.workspace_state(row, item),
                "href": "/studio#" + item["study_id"],
            }
            for item in row.get("workspaces", [])
        ]
        report["workflow"] = {
            "schema": "article-workflow-v1",
            "dependency_graph": graph,
            "frontier": frontier,
            "ready_obligations": sum(item["ready"] for item in frontier),
            "unclassified_claim_candidates": sum(
                block["kind"] == "claim_candidate" and not block["annotation"]
                for block in by_id.values()
            ),
            "unclassified_equations": sum(
                block["kind"] == "equation" and not block["annotation"] for block in by_id.values()
            ),
            "research_tasks": len(report["workspaces"]),
            "areas": AREAS,
            "backends": BACKENDS,
            "limits": {"batch_blocks": 32, "research_tasks": 16},
            "scope": "Only manually recorded obligations and dependencies; not exhaustive proof coverage",
        }
        report["review_markdown"] = review_markdown(report)
        return report

    def report(self, key, *, recheck=False):
        return self.enrich(self.articles.report(key, recheck=recheck))

    def create_workspace(self, key, data):
        exact_object(
            data,
            {"request_id", "expected_revision", "block_id", "area", "context"},
            "Research an article obligation",
        )
        text_field(data["block_id"], "Source block ID", 160)
        if data["area"] not in AREAS:
            raise ValueError("Choose a mathematical area")
        if not isinstance(data["context"], str) or len(data["context"]) > 6000:
            raise ValueError(
                "Additional definition context must be text of at most 6000 characters"
            )

        def action(row, inventory):
            annotation = row["annotations"].get(data["block_id"])
            if not annotation or annotation["classification"] not in MATH_CLASSES:
                raise ValueError("Save a complete mathematical obligation before starting research")
            if len(row.get("workspaces", [])) >= 16:
                raise ValueError("Article research-task budget exhausted; reuse an existing task")
            _, source, _ = self.articles.read(key)
            prerequisites = [
                {
                    "block_id": parent,
                    "statement": row["annotations"][parent]["statement"],
                    "assumptions": row["annotations"][parent]["assumptions"],
                }
                for parent in closure(row["annotations"], data["block_id"])
                if parent != data["block_id"]
            ]
            definitions = "\n".join(
                [
                    "原文："
                    + source["title"]
                    + "\n"
                    + source["url"]
                    + "\n原文 SHA256："
                    + source["sha256"]
                    + "\n原文编号："
                    + data["block_id"],
                    "原文解释与缺口：" + annotation["note"],
                    "补充定义：" + data["context"],
                    "下列只是论文声称使用的前置义务，尚非可自由使用的已证假设。必须分别验证或在最终证明中完整消解，禁止直接添加为新假设：",
                    json.dumps(prerequisites, ensure_ascii=False),
                ]
            )
            if len(definitions) > 8000 or len("\n".join(annotation["assumptions"])) > 8000:
                raise ValueError(
                    "Complete context exceeds the research budget; split the obligation explicitly instead of truncating conditions"
                )
            problem = source_request(
                {
                    "title": (data["block_id"] + " · " + source["title"])[:200],
                    "question": annotation["statement"],
                    "areas": [data["area"]],
                    "definitions": definitions,
                    "assumptions": "\n".join(annotation["assumptions"]),
                }
            )
            study = self.studio.create(
                {
                    "request_id": "article-study-" + digest({"article": key, "request": data}),
                    "source": problem,
                }
            )
            if not study["plans"]:
                study = self.studio.scaffold(
                    study["study_id"],
                    {
                        "request_id": "article-scaffold-"
                        + digest({"article": key, "request": data}),
                        "expected_revision": study["revision"],
                    },
                )
            row.setdefault("workspaces", []).append(
                {
                    "workspace_id": "articlework-" + uuid.uuid4().hex,
                    "block_id": data["block_id"],
                    "study_id": study["study_id"],
                    "snapshot_identity": snapshot_identity(row, data["block_id"]),
                    "study_source_fingerprint": study["source_fingerprint"],
                    "created_at": _now(),
                }
            )

        return self.enrich(self.articles.change(key, data, action))

    def evidence_preview(self, key, data):
        exact_object(
            data, {"workspace_id", "graph_id", "node_id"}, "Preview research-task evidence"
        )
        for name in ("workspace_id", "graph_id", "node_id"):
            text_field(data[name], name, 200)
        row, _, _ = self.articles.read(key)
        workspace = self.workspace(row, data["workspace_id"])
        if self.workspace_state(row, workspace)["status"] != "CURRENT":
            raise ValueError("Research task no longer corresponds to this exact obligation")
        study = self.studio.read(workspace["study_id"])
        transfer = next(
            (item for item in study["transfers"] if item["graph_id"] == data["graph_id"]), None
        )
        if transfer is None:
            raise ValueError(
                "Select a verifier task actually transferred from this research notebook"
            )
        self.studio.round(study, transfer["round_id"])
        checked = self.articles.reporter(transfer["backend"], transfer["graph_id"])
        if checked["plan"]["plan_fingerprint"] != transfer["plan_fingerprint"]:
            raise ValueError("Transferred formal task identity changed")
        identity = evidence_identity(checked, data["node_id"])
        if identity["original"] != row["annotations"][workspace["block_id"]]["statement"]:
            raise ValueError("This node does not preserve the complete original article obligation")
        if self.studio.read(workspace["study_id"])["revision"] != study["revision"]:
            raise ValueError(
                "Research notebook changed during evidence review; read the current candidate again"
            )
        value = {
            "article_id": key,
            "article_revision": row["revision"],
            "workspace_id": workspace["workspace_id"],
            "block_id": workspace["block_id"],
            "study_id": workspace["study_id"],
            "study_revision": study["revision"],
            "backend": transfer["backend"],
            "graph_id": transfer["graph_id"],
            "node_id": data["node_id"],
            "identity": identity,
            "assumptions": row["annotations"][workspace["block_id"]]["assumptions"],
            "original": identity["original"],
            "scope": "Exact local obligation only; does not certify the author's full argument",
        }
        value["preview_fingerprint"] = digest(value)
        return value

    def bind_workspace(self, key, data):
        exact_object(
            data,
            {
                "request_id",
                "expected_revision",
                "workspace_id",
                "graph_id",
                "node_id",
                "preview_fingerprint",
                "reviewer",
                "scope",
                "confirmed",
            },
            "Return exact research evidence to the article",
        )
        text_field(data["reviewer"], "Local semantic reviewer", 200)
        text_field(data["scope"], "Source correspondence and limitations", 4000)
        if data["confirmed"] is not True:
            raise ValueError("Confirm the complete source statement, assumptions and formal target")

        def action(row, inventory):
            preview = self.evidence_preview(
                key, {name: data[name] for name in ("workspace_id", "graph_id", "node_id")}
            )
            if preview["preview_fingerprint"] != data["preview_fingerprint"]:
                raise ValueError(
                    "Evidence preview changed; review the current complete target again"
                )
            if len(row["links"]) >= 64:
                raise ValueError("Article evidence-reference budget exhausted")
            block_id = preview["block_id"]
            row["links"].append(
                {
                    "link_id": "articlelink-" + uuid.uuid4().hex,
                    "block_id": block_id,
                    "backend": preview["backend"],
                    "graph_id": data["graph_id"],
                    "node_id": data["node_id"],
                    "relation": "equivalent",
                    "scope": data["scope"],
                    "reviewer": data["reviewer"],
                    "source_fingerprint": row["source_fingerprint"],
                    "annotation_identity": annotation_identity(row, block_id),
                    "identity": preview["identity"],
                    "confirmed_at": _now(),
                    "authentication": "local-assertion-only",
                    "workspace_id": data["workspace_id"],
                }
            )

        return self.enrich(self.articles.change(key, data, action))

    def research_tasks(self, key, workspace_id):
        row, _, _ = self.articles.read(key)
        workspace = self.workspace(row, workspace_id)
        state = self.workspace_state(row, workspace)
        if state["status"] != "CURRENT":
            raise ValueError(state["reason"])
        study = self.studio.read(workspace["study_id"])
        transfers = []
        for item in study["transfers"]:
            try:
                attempt = self.studio.round(study, item["round_id"])
                nodes = [
                    node["id"]
                    for node in attempt["proposal"]["formalization"]["request"]["nodes"]
                    if node["kind"] == "problem"
                    and node["target"]["original"] == study["source"]["question"]
                ]
            except ValueError:
                nodes = []
            transfers.append(
                {**deepcopy(item), "evidence_checked": False, "eligible_node_ids": nodes}
            )
        return {
            "workspace_id": workspace_id,
            "study_id": study["study_id"],
            "transfers": transfers,
            "scope": "Historical task references only; select one for explicit current evidence preview",
        }
