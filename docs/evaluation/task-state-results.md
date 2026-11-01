# 持久参考任务验收

日期：2026-10-19。环境：WSL Ubuntu-22.04、Python 3.10.12、Lean 4.35.0 和固定 Mathlib。设计与限制见 ADR-008，命令见 `docs/protocols/tasks-v1.md`。

本轮加入 SQLite 权威任务账本和 task-submit/report/work/cancel/retry/recover。请求绑定确切仓库参考、前提、方向、具名对齐声明、预算、策略与运行快照。相同请求只创建一个任务；真实阶段事件在运行时提交。单个 Linux worker 使用可由检查器继承的排他锁，最多允许初次加后续共 3 次尝试。

真实 CLI 演示保存于 `artifacts/task-state-cli-demo.json`，其中任务 `task-d8e85b4d8f5e41a7acaab10296974fd1` 从 QUEUED 到 COMPLETED/UNKNOWN/E3；相同请求再次提交返回原任务，再运行 worker 为 IDLE，尝试数仍为 1。另一条排队任务取消后保持 CANCELLED/UNKNOWN/E0，不启动 Lean。演示真实执行仓库参考，模型调用为 0。

专项验收检查了以下行为：

| 场景 | 验收依据 |
| --- | --- |
| 并发重复提交/领取 | 同 request-id 仅一个任务；竞争领取仅一个 attempt；事件序号由事务确定 |
| 重试上限/过期重试回放 | 最多 3 次总尝试；只重试最新基础设施失败；旧请求回放不重新排队 |
| 排队及运行中取消 | 排队即时终态；另一进程在 LEAN_RUNNING 后请求取消，真实检查器返回 CANCELLED，无数学结论 |
| 取消与终态提交相交 | 真实门禁已通过但取消先提交时，任务结果仍 UNKNOWN/E0；完成先提交时后来的 cancel 不改写终态 |
| 检查后监督进程 SIGKILL | 磁盘已存在真实 PROVED 报告，账本终态未提交；恢复为 FAILED/INTERRUPTED/UNKNOWN/E0，不导入报告 |
| 恢复后的显式重试 | 新 attempt 和工件目录，重新执行真实 Lean 后才得到已对齐结果 |
| 检查期间 checkpoint 失败 | 真实启动后注入存储异常，检查进程组被回收，继承锁释放，状态 FAILED/UNKNOWN |
| SQLite 事务中断 | 触发器 ABORT 和子进程 SIGKILL 均不留下部分任务状态 |
| 历史工件变更 | 修改已完成任务的 Lean 源后，报告标 CHANGED_OR_MISSING，顶层结果降为 UNKNOWN/E0 |
| 环境/政策与边界 | 政策改变、无可执行环境、无效预算/标识符被拒绝；不能经任务入口执行任意 Lean 或隐式选择 Nat 预留组 |

验证同时保留原 P0 黄金案例、18 条算术作者参考、形式化协议、审核撤销/修订和内容完整性回归。模型协议原有 mock 仅验证 DTO/预算合同；队列真实 Lean 路径没有 mock 检查器。作者对齐的 PROVED/REFUTED 不代表用户或独立专家已确认。

完整回归 `MRA_REAL_LEAN=1 python3 -m unittest discover -s tests -v`：145 项通过、0 失败、0 跳过，331.422 秒；测试期间源码摘要未变。源码门禁通过 46 个 Python/JSON 文件，Ruff lint/格式检查通过 34 个 Python 文件；SQLite integrity_check 为 ok，无残余 RUNNING 任务。远端 CI 未运行。

| 分组 | 测试方法数 | 范围 |
| --- | ---: | --- |
| 新任务状态 | 20 | SQLite、锁及继承、并发、幂等、总尝试上限、取消、事务异常与 SIGKILL |
| 新任务真实离线 CLI | 2 | 无 Lean 的入队/查询/取消/恢复/空闲 worker 和输入拒绝 |
| 新任务真实 Lean | 7 | E3、超时及重试、运行中取消、取消提交竞态、checkpoint 清理、真实检查后崩溃及重验；其中一项含反驳/错误证明/政策拒绝三个子案例 |
| 既有回归 | 116 | 18 条算术参考、原 10 个黄金子案例、证据/完整性、形式化及审核 |

测试计数、日志与源码摘要保存于 `artifacts/task-state-validation.json`，完整日志为 `artifacts/task-state-tests.log`，格式工具输出为 `artifacts/task-state-quality.json`。本次 CLI 和真实测试创建的 11 个任务及其尝试、事件、工件路径、历史与当前结果导出到 `artifacts/task-state-execution-evidence.json`，导出及演示摘要纳入验收 JSON。历史专项日志 `artifacts/task-targeted-tests.log` 保留最初 24 项测试通过的记录。

本轮未更改封存的运行内容；快照指纹仍为 `58f585a822731203f0e1ee781ce7a1af4fb18ca78575f2aaf79c903ed0f1337e`。没有重新封存、模型接入、权重下载或网络服务。当前报告是可核对所记录工件哈希的历史账本，未实现跨进程持久签名或当前环境重新认证。运行期间取消需要等待轮询/阶段边界，不能抢占完整内容哈希；本地数据库、锁和宿主所有者仍受信。任务队列当前只支持批准的仓库参考，真实生成任务、整批评测恢复、分布式调度和 HTTP/UI 仍为后续范围。
