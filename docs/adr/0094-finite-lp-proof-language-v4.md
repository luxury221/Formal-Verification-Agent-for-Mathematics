# ADR 0094：显式有限维 ℓp 表达式与实幂证明语言 v4

日期：2026-11-01。状态：已实现；形式化见证已有平台真实检查工件，并已作为限定反例发现关联回论文原文。

## 问题

一般 Hölder 等号条件不能直接套用实内积空间中 Cauchy–Schwarz 的线性相关条件。论文命题2使用有限维 ℓp/ℓq 范数和实数指数，既有 v3 的抽象内积范数不足以表达该目标。将 `Fin n → ℝ` 直接交给隐式 Pi 范数，还可能把所需 ℓp 范数误写为上确界范数。

## 决定

新增独立 `proof-language-v4`、`proof-project-research-v4`、`proof4flow-*` 图和 `proof4attempt-*` 尝试。`finite_lp_language.py` 继承 v3 的有类型表达与证明组合，新增维数1–8的有限实向量、坐标、显式点积、有限 ℓp 和式、ℓp 表达式与 `Real.rpow`。同一 `/proof`、Studio 后端 `proof`、API 和 CLI 严格按显式版本路由；默认 catalog 为 v4，历史图仍由原版本执行器读取，不混合版本或自动迁移证据。四个队列轮流调度，沿用全局单检查租约。

有限向量类型为 `Fin n → ℝ`，线性运算逐点进行。定义

`lp_sum(p,u) = ∑i |uᵢ|^p`，`lp_norm(p,u) = lp_sum(p,u)^(1/p)`，`dot(u,v) = ∑i uᵢvᵢ`。

这些表达式采用 Mathlib 的总定义，编译器不暗加正性。`lp_norm(p,u)^p = lp_sum(p,u)` 的固定规则要求显式 `0 < p` 证明；这不表示 `0 < p < 1` 时满足范数公理。通常有限维 ℓp 范数要求 `p ≥ 1`。本轮没有新增一般 Hölder 不等式或全部范数公理的证明规则。

有限向量禁止使用隐式 `norm`/`inner` 及继承的内积范数规则，必须使用显式 `lp_norm`/`dot`；原抽象内积空间语义不变。有理实幂证书要求非负根、精确整数幂等式和有界分子分母，不使用浮点近似。固定有限化简、坐标等式及实幂规则只接受结构化 JSON 和已有证明，不开放任意 Lean 源、imports 或定理名。

输入沿用131072字节、2400节点、64层等既有边界，单次候选与回放共享1–40秒；导出最多524288字节、12000行。没有为失败放宽预算、公理或回放门禁。

## 验证与数学范围

正式工件保存在 `artifacts/holder-equality-20261030-01/platform-20261101-01/` 和 `platform-20261101-02/`。第一批 `all_passed=false` 保留：Hölder 见证初次候选被拒；正实数 p 的三维幂律与 `4^(3/2)=8` 均为平台 `PROVED/E4`，错误线性相关证明负对照为 `REJECTED/UNKNOWN/E0`。负对照被拒不等于证明其命题为假。

第二批仅重新提交完整限定见证，图 `proof4flow-0a2448ff63454ec7871a302ea97df111` 为 `PROVED/E4`；候选PID907与独立数据回放PID914不同，精确类型与传递公理审计通过，无局部公理。取 p=3、q=3/2、r=3，存在常值函数 g,h:ℝ²→ℝ² 和 x=0，g=(1,2)、h=(1,4)，满足 p,q≥1、1/p+1/q=1、r>0，损失为零但不存在不全为零的实数 a,b 使 a·g(x)=b·h(x)。

平台证明的是这个完整存在见证，语义核对为具名本地声明，非独立专家认证。它已通过 `counterexample_specialization` 关联至原文 `Thmprop2`；本轮复核该链接为 `CURRENT`，finding为 `CHECKED_COUNTEREXAMPLE_SPECIALIZATION`，原命题仍为 `UNKNOWN/E0`，不授予原命题或整篇文章 `REFUTED`，也不认证作者全部推理。文章revision42共有12条链接，9项当前有效义务、14项待处理、300文本块未分类，全文仍未完成。绑定幂等及12图原provider复核见 `artifacts/holder-equality-20261030-01/article-binding-20261101/result.json`；该聚合是直接调用未修改原provider后生成的离线结果，不是HTTP性能测试或新增Lean执行。技术试验 `trial-01` 仍是 `platform_admitted=false`，不能追溯改为正式E4。

## 身份与回滚

沿用 Lean4.35.0、Mathlib commit `56a005855ea17e0b0958f73ec801ae140b0a1679` 和既有封存基础树，不下载依赖。采用记录 `.runtime/proof-language-v4/adoption.json` 复用先前采用的版本、许可和基础信息；实际 v4 身份由新 mode、编译器/执行器、catalog、policy 和文件内容清单绑定。旧采用记录字段不是跨版本执行授权。

新目录为 `artifacts/proof-finite-lp-research` 与 `artifacts/proof-finite-lp-language`，私有子账本目录严格为 `proof-finite-lp-language-private`，SQLite 身份表为 `proof_finite_lp_identity`。保留独立生命周期实现以免改写旧证据身份，代价是版本间重复维护。升级自审应核对私有路径、版本路由、幂等与旧证据保全；回滚保留新账本与工件，不覆盖旧库，不把 v4 请求降级为 v3。

操作见 [有限维 ℓp 协议](../protocols/finite-lp-v4.md)。真实模型尚未配置，所有本轮数学检查的模型调用为零。

采用记录的说明性遗留字段需明确保留：`use`、`limits` 仍描述 v3 的最小值用途，`execution_profile` 仍为 `proof-language-v3`；本轮用途由新 `purpose` 字段另行说明。这些遗留说明不作为 v4 执行授权或能力依据。当前记录已纳入正式证据内容清单，不可原地修改后声称旧证据仍有效；后续纠正应通过新身份与新图重新验收。
