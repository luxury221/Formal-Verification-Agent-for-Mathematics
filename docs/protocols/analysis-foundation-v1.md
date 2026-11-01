# 分析基础实验 v1

这是 A0 运维入口，使用独立 Mathlib 树和封存；当前没有网页入口、结构化通用分析语言或模型搜索。固定 Lean 4.35.0 / Mathlib 56a005855ea17e0b0958f73ec801ae140b0a1679。唯一新增数学导入 Mathlib.Analysis.InnerProductSpace.Basic，版本/许可/用途见 [ADR0071](../adr/0071-isolated-analysis-foundation.md)。旧 Nat 环境、运行树、队列和证据身份保持原样。

在 WSL 项目根运行：

~~~sh
PYTHONPATH=. python3 ops/scripts/analysis_foundation.py catalog
PYTHONPATH=. python3 ops/scripts/analysis_foundation.py run --probe space-self --reviewer '本地审核者'
PYTHONPATH=. python3 ops/scripts/analysis_foundation.py report --run-id analysis-实际ID
~~~

run 是主动执行，每次生成新 ID，并非幂等重发；没有自动重试。只能选择 catalog 的八个完整固定程序，不能传任意 Lean、修改条件或策略。CLI 退出码本身不代表数学成功，应检查 state/math_verdict/evidence_level。具名者应检查 original 与 statement 的对应；此为本机对齐声明，不是认证身份或外部专家复核。

三个正例为实数自身内积非负、Type 0 实内积空间自身内积非负、Type 0 实内积空间 Cauchy–Schwarz。明确保留 NormedAddCommGroup 与 InnerProductSpace ℝ 实例。五个负/安全对照为反向符号、遗漏内积实例、实复类型混用、未使用局部公理、sorry 占位。失败仅 UNKNOWN，未执行否定证明，不赋 REFUTED。均为已见已知库结果，不是盲测、原创数学或 PDE 求解。

候选进程输出完整定理的类型、公理、全部本地声明闭包与限量 NDJSON；第二个进程只接受数据并从独立封存的分析导入恢复库常量，逐个重放新增声明，内核检查完整宿主目标。每完整检查共享 40 worker 秒、候选最多一次/重放最多一次，完整性审查另计；保留原沙箱的无网络、只读绑定、1MiB 单文件和 16GiB 虚拟地址限制，验收另受 4GiB cgroup。两进程使用同一 Lean 内核实现。

公理仅 propext/Classical.choice/Quot.sound；local_axioms 必为空，任何局部声明依赖 sorryAx/Lean.ofReduceBool/未知公理均阻止晋升。只有两进程成功、完整原类型/公理一致、来源环境当前、语义对齐记录齐全，才有 PROVED/E4。已有 E4 报告重新核环境、生成器、全部文件、日志、导出数据、预算与对齐；损坏会写永久 invalidated.json，恢复原字节不会复活。RUNNING 的并发只读观察始终 UNKNOWN/E0，不误撤销运行中任务；强制杀死监督进程可能留下 RUNNING/UNKNOWN，当前无恢复队列或免费重跑。

结果存 artifacts/analysis-foundation/runs/，不自动进入旧图、引理来源或评分。报告完整性失败仅停止本证据，不能反推原数学命题错误。seal 子命令只供环境首次明确建立，拒绝覆盖已有 seal/lock；没有隐式迁移或回滚。准备的失败、网络/下载/解包成本及供应链本地摘要均须保留。封存识别实际安装内容，本地摘要不等于上游签名或对整个 Mathlib 的重新内核校验。

本轮真实验收计划另含两次独立重放安全对照：错误宿主目标、损坏证明数据。不得把它们算作额外数学成果。证据损坏恢复控制会主动停用 real-self 正例；其余正例由当前报告决定。验收记录见 [结果文档](../evaluation/analysis-foundation-results.md)。
