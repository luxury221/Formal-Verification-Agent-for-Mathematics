# 独立进程证明数据重放验收

日期：2026-10-19。环境：WSL Ubuntu-22.04、Python 3.10.12、Lean 4.35.0 与固定 Mathlib。设计见 ADR-009，输入/工件合同见 `docs/protocols/kernel-replay-v1.md`。本轮交付 MRA-022 的独立进程复验部分。

候选成功后，宿主提取有界 NDJSON 证明数据，交给第二个隔离 Lean 进程。该进程只运行封存检查模板，忽略序列化数据对基础库常量的覆盖，内核重放批准种类的本地声明，再以内核桥接定理检查宿主精确目标并审计公理。两个成功进程、审计一致性、完整工件摘要与语义对齐分别由 EvidenceGate 检查。重放沿用原隔离和任务锁，不增加生成/修复机会。

首次端到端运行发现检查模板将 Lean 字符串插值误写为双花括号，真实编译失败并保留 POLICY_BLOCKED/UNKNOWN/E2；该运行位于 `artifacts/runs/nat_add_zero-3a2f82b433d74cc18189efc3e42e6ef7`。当时专项测试被主动中断，失败日志和旧封存保存在新封存记录指向的备份目录，不能作为通过证据。修正模板、重新比较官方归档/源码/缓存并显式封存后，真实端到端检查达到 UNKNOWN/E3。

初次成功工件为 `artifacts/runs/nat_add_zero-d6ad96ac6afe42e9929f21f884ba0693`。候选进程 633、重放进程 640，重放一条本地定理，精确类型匹配、公理为空、candidate_module_imported=false。两阶段 Worker 共 2.0313 秒，完整性检查 6.5853 秒，整个检查 8.6753 秒。这是一次本机样例耗时，不作为通用性能保证。

专项 `test_replay.py` 最初 18 项全部通过、0 跳过，73.468 秒，日志 `artifacts/independent-replay-targeted-tests.log`。随后补充门禁对内部认证但不完整/不一致元数据的拒绝检查，并将其纳入完整回归；该负例使用测试内部密钥，只验证门禁规则，不替代任何真实证明检查。

| 场景 | 真实验证行为 |
| --- | --- |
| 修改证明表达式为其类型 | Lean 内核在重放声明时拒绝 |
| 有效证明搭配另一个目标 | 内核目标桥接失败 |
| 删除目标定理 | 缺少 MraCandidate.result，拒绝重用日志 |
| 增加未使用的新公理 | 在重放前拒绝不允许的声明种类 |
| unsafe 定义 | 在 Lean.Replay 可能跳过它之前明确拒绝 |
| 序列化伪造基础库 Nat.add_zero | 忽略覆盖，使用封存环境的真实引理，目标仍通过 |
| 重复表达式索引 | LeanExport.Parse 拒绝重复绑定 |
| 重放超时或取消 | TIMEOUT 或 CANCELLED，保持 UNKNOWN；取消不发布证据 |
| 两进程之间篡改证明数据 | 真实内核拒绝，不能授予 E3/E4 |
| 检查后改写重放日志 | 回执工件摘要失配，拒绝证据 |
| 审计缺失、进程复用/缺失、空声明数、公理不一致 | 即使是内部认证的负例元数据，EvidenceGate 也拒绝晋升 |

完整回归中的自然数算术 CLI 检查 18/18 参考通过，9 PROVED、9 REFUTED，实际 36 个 Lean Worker，总耗时 209.3467 秒。报告使用一次候选尝试、两阶段共享 30 秒预算；verification 模式纳入计划协议指纹。作者参考审核和通过率仍不代表独立专家确认或模型求解能力，model_calls=0、model_solve_rate=null。

完整回归 `MRA_REAL_LEAN=1 python3 -m unittest discover -s tests -v`：164 项通过、0 失败、0 跳过，561.008 秒。新增 6 项数据协议测试和 13 项真实 Lean 重放/门禁测试；既有 145 项全部保留。原黄金用例、审核修订/撤销、任务取消/崩溃/重试/锁继承和完整性测试均通过。20 项原模型服务 mock 只验证协议，重放与参考证明的实际执行没有 mock 检查器。

机器记录 `artifacts/independent-replay-validation.json` 保存源码（包括 .lean.in 模板）前后摘要、测试分组、日志及 CLI/执行证据摘要。完整日志为 `artifacts/independent-replay-tests.log`；`artifacts/independent-replay-execution-evidence.json` 列出本轮真实运行目录、两个进程的回执/审计及攻击结果，显式标注故意篡改的负例工件。`artifacts/independent-replay-cli-demo.json` 保存实际 CLI 的 UNKNOWN/E3、具名 PROVED/E4 和 REFUTED/E4 三条路径。Ruff 0.12.0 lint 及 36 个 Python 文件格式检查通过，输出保存在 `artifacts/independent-replay-quality.json`。

源码门禁通过 48 个 Python/JSON 文件，另将检查模板纳入共 49 个源码/配置摘要，验证前后完全一致。最终再次核对日志、CLI 演示、执行证据、参考报告、质量输出与源码摘要，并核对三条 CLI 工件的全部回执文件；均匹配。任务数据库只读 integrity_check=ok，无残余 RUNNING 任务。

新模板和策略进入封存，当前快照指纹为 `9b068bfe440ebaff12de640006a2419fa356728b21750d10d6973e4db703444f`。本次封存重新核验 Lean 17,925 个文件、源码 10,267 个文件、154 个缓存包的 2,310 个解包文件，安装树 33,152 项。当前与旧版快照/锁、失败阶段日志的链条见 `artifacts/independent-replay-seal.json` 及其 backup_directory；更早的 58f585a… 基线也通过该历史链保留。

本轮只采用已安装 Lean 归档中的 Apache-2.0 API，没有引入新下载、运行依赖或第三方非商业实现。候选与复验使用同一内核实现，封存基础库仍受信。当前执行器继续要求完整案例、Claim 及 Formalization 精确匹配仓库参考；不接受任意外部 Lean，不保证恶意源完整导出所有声明。持久签名、身份权限、不可变文件系统、真实模型生成与 HTTP/UI 仍未交付。远端 CI 未运行。
