# 类型化证明复用工作台接口 v1

数学请求与证据语义沿用 [typed-proof-composition-v1](typed-proof-composition-v1.md)，本接口不改其清单和执行器。页面为 `/typed-research` 中的“让已证引理，成为下一步”面板。

| 方法／路径 | 内容 |
| --- | --- |
| GET `/v1/typed-reuse/graphs/<graph>/nodes/<node>` | 完整原目标、来源候选、本节点最近20条经审计的记录摘要及总数 |
| GET `/v1/typed-reuse/sources/<item_id>` | 当前可用来源的完整类型、原条件、定义、语义确认、指纹及祖先；无证明数据 |
| POST `/v1/typed-reuse/preview` | 六字段数学请求；预览替换、全部条件及有界证明树，不执行 Lean |
| POST `/v1/typed-reuse/runs` | request_id、request、plan_fingerprint、reviewer、confirmed=true；202排队，唤醒原 worker |
| GET `/v1/typed-reuse/runs/<run>` | 重新审计的完整报告 |
| GET `/v1/typed-reuse/runs/<run>/export` | 同样重新审计的完整 JSON |
| POST `/v1/typed-reuse/runs/<run>/cancel` | 严格空对象；取消本次组合 |
| POST `/v1/typed-reuse/runs/<run>/revoke` | reviewer、reason、confirmed=true；具名持续停用组合 |

所有入口拒绝 query，写入口沿用工作台同源与 CSRF 门禁，拒绝重复 JSON 键、未知字段及超过192KiB的正文。来源与历史摘要不得替代完整报告；读取会审计并可能持久记录失效，不重新执行 Lean。只在本协议当前完整目标通过候选与独立重放时显示 PROVED/E4，原图数学判断独立。失败和取消保持 UNKNOWN/E0。

原类型页与复用面板共用串行传输：至多32个待发请求；GET仅503重读两次，等待250／750ms；POST从入队起固定正文，不自动重试。提交网络失败时当前页面保留原ID与正文，仅人工重发。切换节点／项目、编辑任务或重载会清理确认，跨刷新不保留草稿；应先重读记录，再决定是否登记新任务。

运行中轮询当前组合。操作或来源读取进行时，历史与结果区操作按钮明确禁用，完成后恢复。切换图／节点或历史记录后，迟到响应不能覆盖当前视图；读取失败隐藏旧组合判断。新组合原问题 E4 不改变上方原图判断。撤销组合使依赖它的后继在审计时持续停用；重证来源不复活旧后继。原图节点撤销仍用 `/v1/typed-research/<graph>/withdraw`。

组合最多200条，单次候选与独立重放共享1–30秒；完整性、锁及HTTP等待另计，不承诺硬请求时限。与原图16次／480秒独立记账。共享队列只覆盖当前类型页面的两个面板；其他客户端仍可能竞争，暂忙需人工恢复。完整备份范围仍含原图、子库、组合库、停用标记和所有证明工件。
