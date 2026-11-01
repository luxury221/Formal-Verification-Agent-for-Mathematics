# ADR 0071：隔离分析基础环境

日期：2026-10-29。状态：已采用，固定探针验收通过；不代表一般分析/PDE能力。

用户选择分析与偏微分方程。旧 runtime snapshot 完整覆盖旧 Mathlib 树，原执行器仅导入 Nat.Basic；修改它会令旧证据环境漂移。因此新增 .runtime/analysis-foundation-v1/mathlib，使用普通复制、独立缓存与独立封存；共享只读、原已封存 Lean 4.35.0，不修改旧树/锁/执行器。新入口为运维实验，不自动纳入旧队列、引理库或网页。

复用同一 Mathlib commit 56a005855ea17e0b0958f73ec801ae140b0a1679，新增导入仅 Mathlib.Analysis.InnerProductSpace.Basic。Lean 与 Mathlib 为 Apache-2.0；原已固定的八个 Mathlib 依赖沿用 docs/security/mathlib-dependencies.json 的版本和实际许可文本，LeanExport 沿用原运行树。准备前复核来源 archives、所有已记录许可摘要并保存独立 adoption receipt。没有引入新框架或模型。官方缓存下载属于供应链输入，记录本地获得摘要，不声称为上游签名认证。

先运行仓库固定的实数/实内积空间小目标及错误符号、类型、遗漏实例、局部公理和占位对照。固定目标不接受任意 Lean 输入；候选和数据独立重放均保留完整类型和公理检查。同一 Lean 的不同进程并非第二独立实现。完整内积条件保留，Type 0 是初期明确范围。人工对齐是本地声明，不冒充外部专家认证。

准备、完整性检查、证明、独立重放分别记录成本。旧 Mathlib 只读复制，不使用硬链接；准备失败不写 seal，不覆写已封存环境；对未封存目录的准备重试保存每次日志与 receipt。新 seal 和旧环境同时核验。新分析来源只能属于新身份，不能回填旧 E4/分数。小目标已知且作者已见，不是原创性或 PDE 求解测试。
