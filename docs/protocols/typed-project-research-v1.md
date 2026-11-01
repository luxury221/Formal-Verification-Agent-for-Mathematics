# 类型定义项目研究协议 v1

Schema：`typed-project-research-v1`。每图位于 `artifacts/typed-research/typedflow-<uuid>/`，包含带摘要的 `plan.json`、`record.json`、可能的持久停用标记及 `checks/lab.sqlite3` 与真实工件。项目关联原 `research.sqlite3`，不改旧图协议。

输入为 `project_id`、`branch_id`、`definitions`、`nodes` 四字段。共享定义遵循 [typed-definitions-v1](typed-definitions-v1.md)。节点有 `id`、`kind`（lemma／subproblem／problem）、`depends_on`、`target`；target包含typed请求除schema与definitions外的全部字段。恰有一个原问题，所有节点均在它的无环前置图中。没有额外注入假设、丢弃原条件或导入前置证明项。

全请求最多192KiB、4096结构项；最多6定义／8节点，每节点继承320表达式节点／16层／8变量与原前提的限制。每图16次已登记检查，每次1–30秒候选与重放共用worker预算，总预留不超过480秒；完整扫描及锁等待不在该时间内；worker提交父记录时每次取锁至多等待30秒，仍失败则保守保留中断语义，不重新检查Lean。最多50图／512事件。取消、拒绝和中断仍占检查预算；不自动重试。

HTTP同源、CSRF、严格JSON与体积门禁沿用工作台：

| 方法／路径 | 操作 |
| --- | --- |
| POST `/v1/typed-research/preview` | 预览全图，不执行Lean |
| POST `/v1/typed-research` | 用request_id、request、plan_fingerprint保存 |
| GET `/v1/typed-research/projects/<project>/<branch>` | 列表元数据与作者示例，不授予数学结论 |
| GET `/v1/typed-research/<graph>` | 重新审计的完整报告 |
| POST `<graph>/node-preview` | node_id；返回完整child_plan与execution_fingerprint |
| POST `<graph>/submit` | request_id、node_id、execution_fingerprint、reviewer、confirmed=true |
| POST `<graph>/cancel` | attempt_id |
| POST `<graph>/withdraw` | node_id、reviewer、reason、confirmed=true |

上述短路径承接 `/v1/typed-research/`。所有入口拒绝query参数。submit唤醒原后台worker；其余操作不产生数学执行。读报告可能持久记录损坏或依赖失效。原问题判断仅由它自身当前精确PROVED/E4证据给出；前置真而原问题检查失败，保持UNKNOWN，不能宣称REFUTED。

CLI：`python3 -m mra typed-research-<action>`。preview／create用`--input`；catalog用project_id与可选`--branch-id`；report用graph_id；node-preview／submit／cancel／withdraw用graph_id与`--input`；work可用`--graph-id`，只执行一个已排队检查；recover只标中断、取消孤儿，不执行或晋升。幂等重发必须保留完全相同的request_id和正文。

网页 [类型与定义](http://127.0.0.1:8765/typed-research) 支持已有项目／研究线、新项目、结构化示例和JSON编辑、全图预览、逐节点具名执行、取消、撤销与完整报告导出。下方 [类型化证明复用面板](typed-reuse-web-v1.md) 可以显式调用前置证明，组合结果独立于本协议原图判断。两面板共享串行传输，GET仅503有限重读，POST不自动重发；切换后迟到响应丢弃，不提前中止服务端handler。尚未实现持久跨刷新重发草稿；关页前应记录请求ID，重读报告确认是否提交，不能将网络错误等同于“未提交”。旧Nat图不自动变为typed图。

备份须停写后同时保存项目研究库、图计划／记录／停用标记、私有子库和所有工件。不能仅凭JSON或恢复旧字节恢复信任。语义与宿主边界见ADR046；作者功能题不计模型或原创研究成绩。
