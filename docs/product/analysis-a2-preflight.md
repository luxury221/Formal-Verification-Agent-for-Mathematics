# A2 导数与积分：源码预查和实施边界

2026-10-29，MRA080。状态：E0 源码预查与后续提案，尚未实现或通过真实 Lean 验收。固定 Mathlib commit 为 56a005855ea17e0b0958f73ec801ae140b0a1679。三份本地源码及行号、摘要、指定目标 olean 路径观察保存在[预查工件](../../artifacts/analysis-language-20261029-01/a2-source-preflight.json)。未扩大 A0/A1 import、运行树或缓存，没有下载。

## 已核实的语义

| 对象 | 固定本地源码 | 对输入语言的影响 |
| --- | --- | --- |
| deriv / derivWithin | Analysis/Calculus/Deriv/Basic.lean，第141–155行；说明不可导时取0 | 数值等式 deriv f x = 0 本身不能解释为 f 在 x 可导；优先表达 HasDerivAt f f' x 的证据，再提供带明确语义的算子 |
| Bochner integral | MeasureTheory/Integral/Bochner/Basic.lean，第148–162行 | 定义在余域不完备或函数不可积时取0；不能把“积分值存在”自动解释为经典可积性；需要明确余域、测度与可积性 |
| 积分加法 | 同文件第239行，以及 IntervalIntegral/Basic.lean 第774行 | 原库定理明确要求两个函数分别可积/区间可积；不得省略或让默认值掩盖条件缺失 |
| IntervalIntegrable | MeasureTheory/Integral/IntervalIntegral/Basic.lean 第73行 | 使用两个有向半开区间的可积性，语义相当于无序端点间的可积性；原端点顺序需保留 |
| 区间常数积分 | 同文件第831–836行 | 一般测度公式与 Lebesgue 体积公式不同；体积情形为 (b-a) • c，不能隐式把 a≤b 加作条件 |

还核实 deriv_id / hasDerivAt_id / hasDerivAt_const 等基础入口，以及 norm_integral_le_integral_norm 的实际声明。最后一个定理在该版本源码中没有显式 Integrable 前提，这与库中积分总定义有关；不应一概声称“所有积分不等式都要求同样的可积条件”。需要按每个原命题的意图与库语义逐项对齐。

此次仅观察三个指定模块的目标 olean 均缺失。未盘点完整 import 闭包，不能据此估算全部缓存体量、下载时间或判断导出器已兼容。源码存在也不代表当前 A1 可以执行这些表达式。

## 建议的下一步

1. 先处理已实测的重复完整性扫描成本，保留当前数学门禁；将 worker 时间、文件扫描和端到端时间分别记账，不能以省略校验换取速度。批次只读运行视图等方案需单独设计并验证变更/撤销边界。
2. 新建隔离 A2 环境/provider，审查有限 derivative/integral imports 的版本、许可和缓存闭包，保留 A0/A1 当前证据。先做固定小目标的真实导出与重放实验。
3. 先采用 ℝ→ℝ 的 HasDerivAt、显式区间/体积及 IntervalIntegrable；可积、可导和 a.e. 是独立公式或条件，不能由普通函数类型推断。需要时再扩展一般测度与向量值积分。
4. 为遗漏可导/可积条件、混用测度、端点反向、a.e. 与逐点性质混淆准备正负对照；失败始终 UNKNOWN，严格反证须另行真实验证。
5. A2 基础链完成后，才选一条小型线性 PDE 能量或弱形式任务，明确区域、未知量、解的概念、正则性、初边值和测试函数。当前没有 PDE 能力结论。

以上是实施提案，尚未有升级、迁移或整体回滚操作证据。参见[领域路线](analysis-pde-roadmap.md)与[A1协议](../protocols/analysis-language-v1.md)。
