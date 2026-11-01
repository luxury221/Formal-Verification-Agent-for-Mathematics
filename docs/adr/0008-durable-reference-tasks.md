# ADR-008：持久仓库参考任务与显式恢复

日期：2026-10-19。状态：采用，交付 MRA-021 的本地仓库参考任务范围。

此前 Harness 在检查结束后补写 CREATED/RUNNING/终态，不能据此查询实时进度、控制重复执行或恢复中断。新增项目自有 `TaskStore`；SQLite 保存权威请求、尝试和按事务排序的事件，使用外键、BEGIN IMMEDIATE 和 synchronous=FULL。既有每次检查的 `events.jsonl` 明确标为 post-run-summary；任务事件在实际阶段提交时写入数据库。

提交不运行 Lean，但要求已有可加载的本机运行封存。请求固定完整仓库案例摘要、原文、形式化、前提、方向、具名作者对齐声明、超时、策略与运行快照指纹。相同 request-id 和完整请求只创建一个 task-id；复用 ID 改变任一字段被拒绝。检查仍要求案例、Claim 和 Formalization 精确匹配原有 allowlist，不增加任意 Lean 执行能力。Nat 预留组仍须走显式 reference-benchmark 协议，不能经任务入口绕过选择约束。

任务生命周期为 QUEUED → RUNNING → COMPLETED/FAILED/CANCELLED。CREATED 和 QUEUED 在提交事务内记录；STARTED 与 attempt-id、次数、RUNNING 同时提交。checkpoint 包含 PREPARING、CHECKING、LEAN_RUNNING、ASSESSING 和具体工件位置。COMPLETED 仅代表流程结束：无效证明仍可 COMPLETED/UNKNOWN/E2；政策拒绝也可 COMPLETED/POLICY_BLOCKED/UNKNOWN。数学判断只能取自真实后端及 EvidenceGate。

本阶段采用单个 Linux 本地 worker，`task-work` 最多执行一条任务，不启动后台守护进程。文件 flock 排他持有整个尝试，不使用会受时钟或 PID 复用影响的过期租约。检查子进程通过 pass_fds 继承锁；监督进程退出时只关闭自己的描述符，不显式解锁。残余检查进程退出前，新 worker 和恢复操作均被锁阻止。unshare/Lean 保持既有隔离与有限 CPU/墙钟预算，不修改封存的 sandbox_worker。

取消排队任务立即提交 CANCELLED；运行中先记录 CANCEL_REQUESTED，再由 worker 在阶段边界和检查期间轮询。检查期间约每 100ms 查询一次取消；收到后杀死并回收检查进程组，异常或回调失败也清理该进程组。完整环境哈希和证据检查期间不能抢占取消；取消标记会在后续边界及终态事务再次核对。若取消先于终态提交，则任务发布 UNKNOWN/E0；若终态先提交，后到取消不撤销已完成结果。取消与证据评估重叠时，磁盘上可能留下单次检查报告，任务不将它作为有效结果发布。

获得独占锁后，恢复将残留 RUNNING 标为 FAILED/INTERRUPTED；已有取消请求则标为 CANCELLED。保留 attempt 和工件位置，不从 JSON 或旧 HMAC 回执恢复 E3/E4，即使真实检查报告已写完也一样。显式 retry 绑定最新失败 attempt-id 和稳定 request-id，只允许基础设施失败、超时或中断；总尝试次数最多 3 次，初次执行也占一次。政策拒绝、已完成和已取消均不能重试。旧重试请求的幂等回放只返回现状，不能重新排队或覆盖后续尝试。新尝试按原预算与确切目标重新执行 Lean，使用新的工件目录。

最终 task 状态、attempt 结果、工件摘要与终态事件在一个数据库事务中提交。报告对已记录工件逐一核对哈希；文件变更/丢失时顶层结果降为 UNKNOWN/E0，历史尝试保留原记录。报告始终标记 historical_record=true、fresh_attestation=false；重新核对工件摘要不等于在当前运行环境重新认证证据。HMAC 仍为后端进程内认证，不提供持久签发。

SQLite、本地文件所有者、内核和 Python 标准库仍在可信范围内。请求指纹与工件哈希不抵抗恶意本地管理员改写数据库、锁文件替换或恶意并发文件替换。本阶段没有租户权限、身份认证、分布式调度、后台自动重试、候选生成恢复、整批评测事务或 HTTP。真正模型任务和独立复验接入时须沿用本状态机并扩展阶段/预算合同；不能把当前范围称为 MRA-020/022 完成。

备份应在停止写入后同时保存 `artifacts/tasks/tasks.sqlite3` 和引用的 `artifacts/runs/` 工件。数据库丢失时旧 JSON 不自动重建任务结论。回滚时保留数据库和原始工件作为历史证据，停用 task 命令即可；无需改变 Lean/Mathlib 安装或重新封存运行快照。
