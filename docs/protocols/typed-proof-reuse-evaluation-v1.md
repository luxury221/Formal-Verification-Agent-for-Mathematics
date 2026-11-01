# 类型化固定程序证明复用对照 v1

状态：实现与真实执行已验收，结果见 ../evaluation/typed-proof-reuse-evaluation-results.md。schema：typed-proof-reuse-evaluation-v1。

## 范围与预声明

六题顺序固定于 evals/typed-proof-reuse-v1/cases.json：rat-polynomial-repeat、mixed-lift、rational-cancel、missing-nonzero、source-unavailable、quantified-parameter。题目由仓库作者编写且全部曝光，禁止训练用途；不是独立研究能力、模型成绩或盲测。未配置或调用模型。

两组获得完整相同的来源类型、共享闭合定义、全部原前提、固定策略程序和显式调用树。import 先以真实候选及独立重放构建来源，再导入其 NDJSON 证明数据检查完整目标；inline 在每个调用处展开同一完整来源类型与同一冻结策略程序，无额外目标 tactic 后备。两组来源程序在冻结策略之前统一用核心 try clear 按依赖逆序逐项清除编译器可能产生的 8 个条件、8 个变量和 6 个定义，隔离调用者外层局部语境，再引入完整来源类型自己的参数与前提；不能借目标中的外部假设证明来源。它衡量归档复用的程序机制和成本，不能推断一般求解收益。私有 evaluation-only 标记不代表关联真实研究图；不得晋升原图。

来源归档不可用题仍公开来源完整程序，只不构建归档。缺失非零条件题故意传入错误类型的条件证明，两组均必须真实交给 Lean。拒绝为 UNKNOWN/E0；不声称 REFUTED。

## 预算、顺序与分母

每个完整 source 或 target 检查：候选与独立重放共享 30 秒，固定单次程序，无自动重试。每题每组至多两项检查／60 秒，六题每组额度 360 秒；inline 只使用一个 target 检查，不挪用未用预算。import 冷成本包含来源构建，另列目标成本。来源不可用题不执行 source/import，但保留分母。

最大实际检查为 16 项／480 秒 worker 预留：五 source、五 import target、六 inline target；失败在预算内计入。两个执行阶段若成功使用独立 PID、同一固定 Lean 内核实现。预算不声称端到端硬墙钟限制；完整性扫描、锁与报告成本另列。首题 source/import/inline，次题 source/inline/import，逐题交替目标组顺序。来源构建始终在该题两组之前；不是随机顺序试验。

所有六题始终计入两组分母，来源失败、拒绝、超时和不可用不删除。不预注册通过率、不做显著性推断或普遍提速结论。完整轮次未结束、父提交前监督退出、取消、工件损坏、来源撤销、数据或实现漂移，均不得发布成功比较摘要；恢复旧字节不复活已停用摘要。

## 生命周期与证据

CLI typed-reuse-evaluation-prepare 只准备完整计划，不启动 Lean。typed-reuse-evaluation-run 需要当前完整计划指纹、具名 reviewer、confirmed；本地姓名是声明，不是认证。typed-reuse-evaluation-report 重新核对数据集、预算、实现、全部子计划、具名确认、状态与工件。

全局单 worker 锁与单评测 lease；开始前保存计划、逐项 inflight 和已提交记录。相同确认再次 run 返回已有状态，不补试、不续跑。父记录始终 UNKNOWN/E0，数学证据只在完整子检查。COMPLETED 必须恰有 18 个有序步骤（含两项不可用状态）；不可删、重复或重排。来源撤销／工件损坏持续停用比较。

没有新增 Lean 执行器或内核政策；复用冻结 MathlibLabStore.execute 与 LibraryCompositionStore.execute，私有检查账本限制于独立评测目录。报告诊断不会写回原研究图、主证明复用库或训练集。

## 指标

每组完整分母、PROVED/UNKNOWN 和运行状态分布；冷启动与目标各自的完整检查数、预留 worker 秒、实际 worker 秒、阶段进程数、检查墙钟和非 worker 耗时；整轮墙钟与报告审计墙钟。进程数基于留存 candidate/replay 结果记录，不推断未提交 inflight 的进程数。两组完整目标逐字相同，含量词、类型、共享定义和所有原条件。
