# 人工研究结果回收协议 v2

状态：已实现的本地 HTTP / Studio 契约。此协议连接人工研究、已有真实验证器与原文义务账本，不提供自动语义认证或全文证明认证。

实现入口为 `ArticleResearchReturns(ArticleWorkflow)`。旧 `ArticleWorkflow` 与数学受信执行器保持原样；新增适配器只扩展显式的原文回收关系。

## 1. 人工路线与候选

Studio 保存完整原始问题、定义和全部原始条件。研究路线必须保留唯一 `kind="problem"` 的最终步骤，其 `statement` 逐字等于原始问题，且不能给最终步骤新增假设。其他步骤必须沿无环依赖图连接到这个最终步骤。

“人工子问题 / 限定见证”表单创建一个 `kind="subproblem"` 步骤，将其加入最终原题的 `depends_on`，并保存新的路线版本。表单不会替换原题，也不会把子问题的额外条件写成原题的新假设。这里的依赖是研究组织关系，不表示已经完成数学推导。

保存路线后，旧路线、旧推演和旧交接记录继续保留；与当前路线指纹不一致的旧推演不再作为可回收候选。原样重复保存相同路线不会产生新的数学身份，不能依靠保存次数区分相同内容。当前实现有路线版本预算，不能把反复保存路线视为无限制操作。

人工推演保存为 `UNKNOWN / E0`。可附带完整结构化候选：

```json
{
  "backend": "proof",
  "request": {
    "nodes": [
      {
        "id": "root",
        "kind": "problem",
        "depends_on": [],
        "target": {
          "original": "逐字复制当前所选研究步骤的完整陈述"
        }
      }
    ]
  }
}
```

上例只说明外层数据关系，`target` 尚缺具体语言要求的目标、变量、条件和证明步骤，不能直接作为可执行请求。实际候选必须满足所选验证语言的完整结构与预算。支持的后端为 `proof`、`typed`、`calculus` 和 `pde`；不能忠实表达的数学对象应保留为研究缺口。

人工候选中的唯一形式化 `problem` 节点必须满足：

```text
formal_problem.target.original == selected_research_step.statement
```

因此，限定见证可以成为自己的形式化 `problem`，而完整原文问题继续保留在 Studio 路线中。禁止把一个限定目标冠以完整原题的名称来绕过这一关系。

保存推演、预览交接、保存验证任务及执行证明是不同操作。Studio 交接保存真实验证器任务，但不会启动 Lean；执行仍需在验证器中审阅完整目标与预算并显式确认。

当前网页不自动保存人工草稿。有未保存推演、缺口、后续动作或候选 JSON 时，切换问题、切换研究步骤或保存新路线会被拦住。用户应先保存本轮，或显式清空未保存草稿；页面关闭后的草稿恢复不在本协议保证范围内。

## 2. 必须来自实际交接的目标

回收沿以下已有记录追踪，不接受任意已证任务冒充研究任务产出：

```text
原文 workspace
  → 对应当前原文义务的 study
  → 当前路线的 round
  → round 实际保存的 transfer
  → transfer 的 formal problem 节点
  → 该节点的真实 E4 检查身份
```

预览与绑定分别核对：

- workspace 仍对应当前原文、义务及已记录的传递前提；study 的完整原题身份未被替换。
- transfer 确实属于该 study，且 round 属于当前路线；所选研究步骤由 round 的 `node_id` 确定。
- transfer 的后端与 round 候选后端一致；所选形式化节点是实际交接的 `kind="problem"`，其原始文本逐字等于该研究步骤的完整陈述。
- 验证器报告的完整计划指纹等于 transfer 保存的指纹。
- 原有 `evidence_identity` 门槛通过：节点为 `PROVED / E4`，检查完成、未失效，工件哈希匹配，子检查的完整形式化命题等于该图节点命题。
- 被检查目标的原始文本逐字等于实际交接的研究步骤；读取证据期间 study 修订未变化。

候选列表只展示交接关系，不授予证明状态。`eligible_node_ids` 保留旧的完整原题筛选语义；新增 `candidate_nodes` 提供 `node_id`、`research_node_id`、`original` 和允许选择的 `relations`。列表中的关系选项只是可申请的回收类型，不是已经认证的数学关系。

## 3. 两种明确的原文关系

| relation | 结构门槛 | 对原文账本的含义 |
| --- | --- | --- |
| `equivalent` | 被检查的研究步骤陈述必须逐字等于完整原文义务；仍须人工确认语义对应。 | 可按原有义务、依赖与证据门槛参与聚合，不保证作者完整证明或全文完成。 |
| `counterexample_specialization` | 必须来自当前路线中陈述不同于完整原文义务的明确 `lemma` 或 `subproblem`，并保留该步骤的全部条件性限制。 | 仅记录具名本地审核人声明的限定反例对应；原文义务不因此获得 `REFUTED`，也不获得完整证明状态。 |

一个被证明的任意子问题不会自动成为反例。实现能核对记录归属、文本、指纹与工件身份，无法自动证明自然语言原文与形式目标之间的数学语义关系。限定反例必须写清对象、参数、量词、原文假设、零值或边界分支，以及为何这组见证针对原结论。

典型用法是：完整原文声称一般命题，研究步骤单独陈述一组有限维、固定参数的见证。形式化目标证明这组见证的精确性质，回收时选择 `counterexample_specialization`。不能把此目标改选为 `equivalent` 来宣称一般命题已经得到完整验证。

审核人必须主动填写非空 `reviewer` 与 `scope`，并提交字面布尔值 `confirmed: true`。前端不代填姓名或勾选确认。`scope` 应说明确切对应范围、尚存缺口与限制；这些字段是本地审核声明，`authentication` 为 `local-assertion-only`，不是身份认证或独立数学专家背书。

## 4. 请求、预览与绑定

读取实际候选：

```text
GET /v1/article-audits/{article_id}/research-tasks/{workspace_id}
```

显式关系预览：

```text
POST /v1/article-audits/{article_id}/research-preview
```

```json
{
  "workspace_id": "articlework-…",
  "graph_id": "实际交接的图 ID",
  "node_id": "root",
  "relation": "counterexample_specialization"
}
```

新预览标识为 `article-research-return-v2`，包含原文义务全文、原文全部显式前提、所选研究步骤及条件性限制、完整研究定义与上下文、形式化目标与检查身份、所选关系、文章与 study 修订，以及 `preview_fingerprint`。前端展示这些内容后才允许具名确认。

回收请求：

```text
POST /v1/article-audits/{article_id}/research-bind
```

```json
{
  "request_id": "一次明确操作的唯一请求 ID",
  "expected_revision": 42,
  "workspace_id": "articlework-…",
  "graph_id": "实际交接的图 ID",
  "node_id": "root",
  "relation": "counterexample_specialization",
  "preview_fingerprint": "当前预览返回的完整指纹",
  "reviewer": "实际进行本地语义核对的人",
  "scope": "明确写出原文对应、限定参数、全部前提与未覆盖范围",
  "confirmed": true
}
```

示例修订号仅为格式说明，调用时必须使用当前记录的真实修订号。HTTP 写请求沿用本地会话与 CSRF 要求。

绑定在文章变更门槛内重新生成预览，重新读取验证器证据，并要求指纹逐字匹配。文章、study、路线、所选关系、目标或证据身份变化时，应重新读取并审阅预览。确认动作不能把未知、未完成、失效或工件不匹配的检查升级成 E4。

成功后只追加新的证据关联，保留原义务、前提、旧关联及历史修订。限定反例复核可出现 `CHECKED_COUNTEREXAMPLE_SPECIALIZATION` finding，完整义务与全文仍保持各自的未验证边界。

同一 `request_id` 与完全相同请求体重放，沿用原文章账本的幂等语义，不追加第二条关联；同一 ID 对应不同内容会被拒绝。修订冲突、预览失效、缺少具名确认或证据不可用都不能靠更换 ID 绕过。

网页不会自动重发写请求。若网络或磁盘故障发生在提交附近，响应失败不保证此前没有提交：应先刷新账本核对。需要重试时保留原请求 ID 与原始请求体，不能盲目生成新 ID。状态读取与历史后台复核不会替代绑定时的重新验证。

## 5. 兼容与限制

- 不带 `relation` 的旧预览、旧绑定请求，委托原 `ArticleWorkflow`，仍只允许完整义务的 `equivalent` 路径；旧请求体、指纹及已有记录不被迁移或重写。
- 新增关系请求使用自己的完整预览指纹，不能混用旧预览、切换关系后沿用确认，或直接把独立验证任务当作已有 study 的产出。
- 本协议没有证明自然语言与形式化命题的自动等价能力，没有自动反例语义判定，也没有解决一般任意维命题的保证。
- E4 身份仍来自既有真实检查及其工件。本文档与临时 root 的协议 fixture 测试都不构成新的数学证据。
- 预览是读取时的观察。绑定会再验，但任何先前成功响应都不能保证后续工件、环境或原文永远不变；使用证据仍应遵循现有失效检查。
- 后台全文关联复核只记录逐图检查时的历史观察。`MATCHED_AS_OF_CHECK`、后台任务 `COMPLETED` 或历史统计不等于实时 `CURRENT` 授权，也不等于原文 `PROVED`、`REFUTED` 或全文认证。
- 模型接入不是人工流程的前置条件；本协议不表示真实模型或 openJiuwen 已完成接入。
