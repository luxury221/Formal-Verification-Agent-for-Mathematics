# 证据依赖续研 v1

在既有研究任务中消费当前有效前置，逐项启动后继命题的独立真实检查。schema 为 dependency-research-continuation-v1；新账本与原 flow 采用相同 flow_id，但路径独立，原路线及具名推演关联不改写。

| 方法与路径 | 作用与正文 |
| --- | --- |
| GET /v1/research-continuations/{flow_id} | 保存前返回 created:false 及完整计划，不创建续研目录或执行检查；保存后审计来源和后继，必要时持久停用失效依赖。 |
| POST /v1/research-continuations/{flow_id}/preview | 空对象；预览完整不可变图及指纹，无 Lean。 |
| POST /v1/research-continuations/{flow_id}/create | request_id、plan_fingerprint；保存副本，无 Lean。一个原任务只有一个续研账本，原语境变化后应创建新原任务。 |
| POST /v1/research-continuations/{flow_id}/propose | node_id、route、request_id；保存完整目标、具体前置来源、候选计划与预算，仍 UNKNOWN/E0，无 Lean。 |
| POST /v1/research-continuations/{flow_id}/submit | proposal_id、proposal_fingerprint、request_id、reviewer、confirmed:true；再次核对当前所选前置及计划，排队一份真实检查。 |
| POST /v1/research-continuations/{flow_id}/cancel | attempt_id；取消活动检查，不重新提交。 |
| POST /v1/research-continuations/{flow_id}/withdraw | attempt_id、reviewer、reason、confirmed:true；停用终态续研来源，并传播到后继。 |

路线首期为 omega、simp-gcd、proof-search，沿用固定策略和有界引用搜索；不接受任意 Lean 源或 tactic。搜索未得到候选时不能提交；搜索失败不是反驳。API沿用本机Host/Origin/CSRF、严格JSON和通用正文限制。CLI为 research-continuation-preview/report FLOW；create/propose/submit/cancel/withdraw FLOW --input 文件；work 和 recover 无参数。CLI输入最多192KiB。重复JSON键、额外字段和客户端覆盖flow_id拒绝。

提案包含完整原式、原前提、固定计划、选定来源与指纹。确认前来源替换/撤销需重新准备。相同提交号与同内容幂等；同号不同内容拒绝。旧请求只返回原尝试，不能复活取消、中断或失效记录。无自动证明重试。

拓扑审计先核对原任务与具名关联，再核对续研结果。前置必须当前PROVED/E4且无冲突。固定优先级为原路线、最早有效推演关联、最早有效续研尝试；每个节点返回 source 与 binding_fingerprint。前置变化会停用历史后继尝试，并向下传播。恢复文件或重新关联同一证明不会复活旧尝试；重新准备、具名检查才可继续。被停用的研究依据不删除其独立子证明。

每个后继仍要真实检查完整原目标，依赖不是逻辑假设，也不导入证明项。节点PROVED并不自动证明根；根自己的完整E4才决定续研顶层结论。原路线结论通过 route_math_verdict 单独展示。

最多50任务、每任务24提案/16尝试/单活动检查，候选与独立重放共享每次1–30秒worker，最大480秒；信封2MiB，完整性扫描单列。来源暂时锁定返回可重试错误，不写永久失效。源码/计划/父语境/工件漂移持久停用；损坏记录或孤儿子库隔离。监督进程在raw replay成功后退出仍记INTERRUPTED，不从残留工件补发PROVED。

备份须包含新续研信封与所有子SQLite、候选/重放工件及原flow/scope/关联来源。只备份十四主库不足；摘要不抵抗本机攻击者同时重写代码和全部记录。具名仅本机审计，不具备账号认证。

网页 /research 的续研区支持保存、节点门禁、三路线准备、具名执行、活动取消、终态撤销、手动来源核对和精确JSON导出。修改来源、路线、姓名或语境后清确认。当前是逐项人工审阅调度，没有自主整图规划或模型调用。见[ADR036](../adr/0036-evidence-dependent-research-continuation.md)和[验收](../evaluation/research-continuation-results.md)。
