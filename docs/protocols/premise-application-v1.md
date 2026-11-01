# Lean 定理应用预览 v1

`POST /v1/studio/{study}/application-preview`：

```json
{
  "request_id": "unique-local-request",
  "expected_revision": 7,
  "round_id": "round-...",
  "citation_id": "citation-...",
  "declaration": "norm_inner_le_norm"
}
```

该接口需要真实 Lean 资源，会启动异步隔离诊断；不是普通无执行的 graph preview。只接受当前计划、同一研究步骤、未撤回且源码仍匹配的引用，以及已读取的确切 theorem 名称。歧义由完整限定名显式选择。只支持已保存的 calculus/pde 完整候选。后台从既有真实 preview 编译整个最终节点，用户不能传入 Lean 源码或改写公式。

响应为完整笔记，新增 `applications` 保存绑定身份的引用，`application_states` 提供当前可用诊断。GET原笔记只读状态，不启动或重试 Lean。状态包括 NOT_STARTED、RUNNING、INTERRUPTED、FAILED、TIMEOUT、UNAVAILABLE，以及：

- NO_OPEN_GOALS：`apply` 与局部假设匹配后没有未解目标/表达元变量；不是内核证明收据。
- OPEN_OBLIGATIONS：保留尚未确定的参数、类型类或前提；`report.obligations` 显示实际 Lean 子目标与上下文。
- NO_MATCH：本次 `apply` 无法匹配目标，带实际诊断；不表示原题为假，也不排除其他证明路线。

报告含 `full_type/context/target/bindings/discharged/obligations/application/unresolved_metavariables`，全部仍 UNKNOWN/E0。`bindings` 包含参数名、kind、类型和推断值；`discharged` 为已由当前候选局部假设匹配的证明项。未保存可复用证明，没有独立重放及公理门禁，最终候选仍需原有完整语义核对和显式真实验证。

40秒为单次Lean worker预算，环境完整性审核另计。繁忙时已登记引用保持 NOT_STARTED；可以显式重发完全相同的请求（包括原expected_revision和request_id），不会重复登记。上次实际执行失败/超时/中断后需新request_id，最多3次；旧工件保留，无自动执行重试。每本最多16条登记，全局64种缓存。磁盘故障仍可能中断归档。

来源、计划、候选、元数据或reader改变后旧诊断保留为历史，不能继续作为当前可用诊断。后续模型输入仅带当前身份仍匹配的最近4条应用诊断与失效ID/原因，原题和全部条件继续保留。模型配置没有变化。
