# 本地参考与证书任务协议 v1

实现：`mra/tasks.py`。决策：ADR-008、ADR-011。命令均在 WSL 项目根目录执行。

```bash
python3 -m mra task-submit nat_add_zero --request-id example-add-zero-v1
python3 -m mra task-submit-certificate nat_add_zero --input evals/certificates/add-zero.json --request-id example-certificate-v1
python3 -m mra task-report <task-id>
python3 -m mra task-work --task-id <task-id>
python3 -m mra task-cancel <task-id>
python3 -m mra task-recover
python3 -m mra task-retry <task-id> --attempt-id <latest-failed-attempt-id> --request-id retry-example-v1
```

`task-submit` 默认 golden suite 和两阶段共用的 30 秒 Worker 预算，支持 `--suite nat-arithmetic-v1`、`--timeout` 与可选 `--reviewer`。先查看 `cases`/数据及确切映射，再明确给出本地姓名；省略姓名时内核检查成功仍是 UNKNOWN/E3。姓名不代表身份已认证，也不自动引用 formalization 审核账本。参考模式只支持完整批准参考，Nat reserved 只能由显式 reference-benchmark 选择。

`task-submit-certificate` 接受 `--input`、`--request-id`、`--timeout` 和可选 `--reviewer`，仅启用 golden 的 `nat_add_zero` 与 `false_all_nat_zero`，不接受 `--suite` 或目标源。证书格式和限制沿用 [证书协议](kernel-certificates-v1.md)。提交保存规范化证书正文及摘要，worker 不再读取原文件。原文件后续修改或删除不改变队列内容；提交重试仍需提供证书 bytes，执行重试只需任务与尝试 ID。

既有请求 `kind=repository-reference-check` 字段及指纹保持原样；证书请求为 `kind=kernel-certificate-check`，另含 `input_mode`、`certificate`（规范化 JSON 文本，末尾换行）和 `certificate_digest`（该文本 UTF-8 bytes 的 SHA-256）。SQLite 表版本仍为 1。两种输入共享提交 ID 空间；空白或 JSON 键顺序变化不构成新内容，证明项或输入模式变化会冲突。worker 在初始化 Lean 前重建并逐字段比较整个请求；未知种类、正文/摘要不一致、目标/策略/运行封存漂移均拒绝执行。

提交要求已经封存运行环境，但不启动 Lean 或模型。request-id 在提交空间内唯一，重试 request-id 在独立重试空间内唯一；标识符为 1–128 个 ASCII 字母、数字、点、下划线和连字符，首字符只能字母/数字。内容相同的重复提交返回同一 task-id 的当前状态；内容变化需要新 request-id。超时必须有限且位于 (0,120] 秒，总尝试上限固定为 3。

`task-work` 未指定 ID 时按入队顺序领取至多一项；指定 ID 时只运行该项。两种形式都先在持有独占锁后恢复中断记录。已有 worker/残留检查器持锁时失败退出，不做强制接管。命令不会常驻；后续调用处理下一项。

输出包含独立的 `state`、`run_status`、`math_verdict`、`evidence_level`、原请求与指纹、按次数排列的 attempts、全局递增序列的 events、当前 attempt-id 和 retryable。RUNNING 或 QUEUED 不表示已有数学结果。已取消为 UNKNOWN/E0；已完成也可能 UNKNOWN/POLICY_BLOCKED。`artifact_status=CHANGED_OR_MISSING` 会抑制顶层历史数学结果，尝试历史保留用于审计。

ADR-009 后，真实阶段在 LEAN_RUNNING 与 ASSESSING 之间增加 REPLAY_PREPARING、REPLAY_RUNNING。每次成功尝试需要候选检查和独立进程的数据重放；两阶段共用该尝试原预算，分别继承 worker 锁并响应取消。历史摘要覆盖新增的重放源、数据、策略和日志。具体工件字段见 [证明数据重放协议](kernel-replay-v1.md)。

排队取消立即结束。运行中取消先设置请求，需等待 worker 到达轮询/阶段边界后结束；环境内容哈希不能立即中断。终态与取消由事务提交顺序决定，已完成结果不会因后来的 cancel 自动撤销。希望撤销语义审核应使用独立审核流程；本队列没有结果授权/撤销系统。

重试必须显式指定最新失败 attempt-id。可重试错误只有 EXECUTION_ERROR、TIMEOUT、INTERRUPTED；目标、证书正文和预算不变，不修改命题或自动扩大资源。初次加后续最多 3 次。取消或完成后无法重试；如需更换预算，应明确创建新请求。恢复只清理状态，不从历史报告签发证据；再次验证需要新尝试。证书任务的重试也必须在新工件目录重新完成候选和重放两阶段，不能从旧阶段继续或复用旧成功报告。

成功操作退出码 0；task-work 返回 FAILED/CANCELLED 时为 2；输入、数据库、锁或环境设置错误为 1。`task-report` 只读历史账本和所记录文件摘要，不执行 Lean，不确认当前依赖环境，也不提供跨进程 HMAC 认证。
