# 研究推演证据关联 v1

关联是既有 E4 的可撤销来源视图，不产生新的 Lean 执行或重放。其 math_verdict 与 research_task 原路线结论分别返回；节点关联不是整张研究图的证明。顶层结论仅取 problem_id 节点自己的有效关联。

API 使用既有本机 Host/Origin/CSRF 与严格 JSON 门禁：

| 方法与路径 | 输入与作用 |
| --- | --- |
| GET /v1/research-evidence/{flow_id} | 核对来源并返回关联、原路线和节点视图；不执行 Lean。若发现已有来源损坏，可持久记录失效。 |
| POST /v1/research-evidence/{flow_id}/preview | node_id、scope_id、attempt_id；核对完整来源，返回 UNKNOWN/E0 计划指纹，无新关联。 |
| POST /v1/research-evidence/{flow_id}/bind | 上述字段加 plan_fingerprint、request_id、reviewer、confirmed:true；再次核对并保存关联。 |
| POST /v1/research-evidence/{flow_id}/withdraw | binding_id、request_id、reviewer、reason、confirmed:true；只撤销关联。 |

CLI：research-evidence-report FLOW；research-evidence-preview/bind/withdraw FLOW --input 文件。输入最多192KiB，拒绝重复JSON键和额外字段。API通用正文限制沿用HTTP协议，输出账本上限2MiB。不存在的 flow 或不符合上下文的推演不能关联。

预览绑定完整目标、原前置、来源清单、语义确认、候选类型审计、独立 data-only-kernel-replay-v1 及账本修订。新关联必须先按当前修订预览、具名确认；相同请求幂等，变更内容复用请求号会拒绝。旧请求不会复活 WITHDRAWN/INVALIDATED。再次关联一个仍有效的来源需要新的当前计划与请求；原来源被永久停用时，必须重新验证产生新来源。

ACTIVE 关联每次读取重新审计来源。文件恢复不撤销已记录失效。账本内容损坏或结构不合法会隔离，恢复原文件也不能直接重新签发。程序版本漂移停用已有关联；原推演仍按其自身协议管理。存储短暂忙碌不会写入永久失效。

备份必须同时包含关联信封、原flow/scope信封、候选/重放工件及子SQLite；十四主库单独备份不足。摘要用于一致性核对，不能抵抗本机攻击者同时重写代码和全部账本。姓名不是账号系统，不支持公开多租户部署。

前端 /research 打开已保存任务与节点，再打开上方已证推演，使用“将已证推演关联到原节点”。核对全部条件、填写核对者并勾选确认。切换语境、来源或修改姓名清除确认，迟到响应丢弃；导出前再次核对。原路线结论与关联依据分别展示，关联不满足原路线前置门禁、不注入假设、不自动入库。

见[ADR035](../adr/0035-named-research-evidence-links.md)和[专项验收](../evaluation/research-evidence-results.md)。
