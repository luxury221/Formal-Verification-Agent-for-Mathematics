# 证明数据重放协议 v1

实现：`mra/replay.py`、`mra/verifier.py`、`mra/evidence.py` 和封存模板 `lean_workspace/replay-checker.lean.in`。设计依据：ADR-009。无需新命令，`check`、`benchmark`、`reference-benchmark` 和持久参考任务自动执行该验证阶段。

模式为 `data-only-kernel-replay-v1`，策略要求 `require_independent_replay=true`。候选编译成功后导出数据，第二个隔离进程仅解析数据并调用同一版本 Lean 内核重建本地声明。它不导入候选模块、候选 .olean 或候选插件。

| 阶段 | 输入与输出 | 预算和失败行为 |
| --- | --- | --- |
| LEAN_RUNNING | 仓库批准的 Candidate.lean；MRA_AUDIT 与唯一 MRA_EXPORT_BEGIN/END 包围的数据 | 候选失败不进入重放，不等于反驳 |
| REPLAY_PREPARING | 宿主检查格式/版本/大小并写出 proof.ndjson、受信模板和 policy.json；复核运行内容 | 数据或完整性错误保持 UNKNOWN，无证据晋升 |
| REPLAY_RUNNING | 新进程解析导出，忽略基础库同名覆盖，检查全部允许的本地声明、目标桥接、公理；输出 MRA_REPLAY JSON | 使用原墙钟预算剩余额度；超时为 TIMEOUT，取消不发布证据 |
| ASSESSING | EvidenceGate 检查两个成功进程、审计一致性、完整文件摘要和语义对齐 | 无对齐为 UNKNOWN/E3；有确切具名对齐才能 E4 |

证明数据上限 524,288 字节、12,000 行；严格拒绝重复 JSON 键，要求 Lean 4.35.0、NDJSON 3.1.0。LeanExport.Parse 另外验证名称/表达式索引和声明数据。新增常量只允许 MraCandidate 下的 safe、非 partial 定理/定义/opaque；新公理、unsafe、partial 和其他种类被拒绝。目标必须是 MraCandidate.result 定理，其类型由内核与宿主目标核对。反驳的宿主目标为原命题的完整否定。

每次运行保留原 5 个摘要文件：`input/Candidate.lean`、`request.json`、`runtime-snapshot.json`、`stdout.log`、`stderr.log`。准备了重放时再增加 `replay/Candidate.lean`、`replay/proof.ndjson`、`replay/policy.json`、`replay/stdout.log`、`replay/stderr.log`。receipt.json 中 `files` 必须对应实际阶段的精确文件集合；保存目录之外的重定向和任何工件改写均不能晋升证据。

ADR-010 增加 kernel-expression-certificate-v1 输入模式，第一阶段只运行受信模板，额外保存并绑定 `input/certificate.json` 与 certificate_digest。参考模式为 repository-lean-reference-v1。两种模式均须通过本协议的第二阶段；证书目标仍固定，详细字段和七种表达式构造见 [证书协议](kernel-certificates-v1.md)。

回执 `candidate_pid` 记录第一阶段进程，`replay` 记录第二阶段 pid、run_status、returncode、elapsed_seconds 与 audit，`replay_error` 保存拒绝原因。audit 包含 mode、target、declaration_kind、type_matches、candidate_module_imported=false、replayed_declarations、axioms、local_dependency_axioms、local_axioms。整体 elapsed_seconds 是两个 Worker 的总时间，integrity_seconds 单独记录完整性检查，total_elapsed_seconds 记录整个检查耗时。

一次参考/候选尝试至多包含两个 Worker，二者合用原 30 秒默认预算。重放不调用生成服务，不消耗第二次候选/修复机会。参考评测需明确报告候选尝试数和实际 Worker 数；不能把两阶段计为两次模型求解。任务的取消、独占锁、终态提交及历史报告规则仍按任务协议执行。

历史导出不能在另一 Python 进程中持久验签；重新认证必须运行新检查。当前仅允许确切仓库参考。此协议使用独立进程和同一 Lean 内核，不提供不同内核实现的一致性保证、任意源代码能力沙箱或用户身份认证。
