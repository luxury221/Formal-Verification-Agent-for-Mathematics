# 受控研究执行 v1

入口：研究空间 `/research` 的“规划本轮检查”；CLI 和 HTTP 复用 `ResearchRunStore`。不调用模型，不接受自由 Lean 源，也不自动绑定研究图。

## 预览与提交

POST `/v1/research/{project_id}/run-plan`，正文示例：

```json
{
  "branch_id": "main",
  "items": [
    {"node_id": "l1", "plan": {"direction": "prove", "strategy": "omega", "witnesses": []}}
  ],
  "budget": {"max_checks": 3, "worker_seconds": 90, "wall_seconds": 300}
}
```

选择 1–8 个数学节点，不能选择定义笔记、重复或悬空节点。返回按拓扑顺序排列的原文、域、完整公式、语境、策略、前置和计划指纹。策略沿用 `omega/simp/decide/counterexample`，最后一项需要 refute 及显式见证；Nat 禁止负见证。未选择的前置必须已有当前有效的 PROVED 绑定，计划固定其绑定摘要。

预览不执行 Lean。POST `/v1/research/{project_id}/runs` 提交：

```json
{
  "request_id": "unique-request-id",
  "input": {"branch_id": "main", "items": [], "budget": {}},
  "plan_fingerprint": "displayed fingerprint",
  "reviewer": "local reviewer name",
  "confirmed_nodes": ["l1"],
  "confirmed": true
}
```

上例 input 需填回预览返回的完整 `input`，不能采用示例空值。`confirmed_nodes` 必须与预览返回的全部节点及顺序完全一致。姓名仅为本机声明。同一请求ID/正文幂等，变更正文冲突；取消后的重试不会复活执行。同一项目分支最多一个 QUEUED/RUNNING 执行，其他分支独立排队。

图版本、原始问题/标题、节点/定义语境、每项原文与完整公式、检查实现、外部前置以及运行快照均纳入指纹。修改图或依据使执行持久 INVALIDATED。仅绑定自身新结果、或修改无关分支，不会改变本分支图快照。

## 调度、预算与终态

GET `/v1/research/{project_id}/runs` 返回最近30条本项目执行；GET `/v1/research-runs/{run_id}` 重新核对图、前置和完成工件。POST `/v1/research-runs/{run_id}/cancel` 的正文必须为 `{}`，撤销该轮所有生成来源，保留原有外部证明。仍沿用精确Host/Origin/CSRF、192KiB请求预算、严格JSON和no-store。

检查上限1–8，总worker30–240秒，总墙钟1–600秒，均为整数。每个开始的节点永久预占一次检查及三十秒worker配额；实际候选和独立重放共享原三十秒。墙钟从首次执行开始，包含完整性扫描；扫描不能即时抢占，可能延迟返回，但到期后不再发布新结果。检查/worker额度不足停止后续；已经按时完成的有效结果可保留。

每项验证的是独立完整命题，依赖仅调度。已选前置必须在本轮 PROVED；REFUTED/UNKNOWN/FAILED 前置使后继 SKIPPED/UNKNOWN，仍可检查独立分支。整个执行 COMPLETED 只表示调度结束，不代表全部命题已证。数学判断只从真实审查工件取得；无作者步骤时原作者证明判断仍 UNKNOWN。

执行过程持有现有单worker锁，子审查从不进入通用 QUEUED 队列；检查完成先保留 RUNNING/GRAPH_COMMIT_PENDING，父执行最后核对后才发布。取消和发布由父事务串行保护。取消、图/来源失效或中断恢复抑制数学结果并撤销子来源；已绑定这些来源的研究节点在下一次核对时沿原协议失效，恢复文件不自动复活。

重启 worker 取得同一锁后将遗留 RUNNING 记为 INTERRUPTED，保留预算与工件，不自动续跑、不从旧工件发布结论。用户需重新预览和确认新执行。QUEUED 执行重启后仍先核对冻结图/依据再运行。

## CLI

```bash
python3 -m mra research-run-plan PROJECT --input plan-request.json
python3 -m mra research-run-submit PROJECT --input confirmed-request.json
python3 -m mra research-run-list PROJECT
python3 -m mra research-run-report RUN
python3 -m mra research-run-work --run-id RUN
python3 -m mra research-run-cancel RUN
python3 -m mra research-run-recover
```

账本 `artifacts/research-runs/runs.sqlite3`；原审查与真实检查仍在 `paper-reviews` 和 `runs`。备份须在停止写入后包括全部六账本与引用工件。存储、SQLite、本机用户和相同Lean内核实现受信；历史记录不是新的认证，不提供分布式事务、身份认证或生产签名。
