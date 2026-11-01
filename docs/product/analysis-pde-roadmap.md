# 分析与偏微分方程优先路线

<!-- MRA087 -->

2026-10-29 · MRA087 当前状态；下文旧阶段原样保留。

新增可编辑经典PDE语言、确切来源应用、独立账本和CLI。28新契约及12质量命令通过，真实账本专项与14条CLI通过。最新相关346项293通过/52跳过/1项旧目录新增竞态失败，原失败保留，旧scanner未修；不声称全项目回归全绿。

A3补齐经典一维PDE的受控新目标/假设、完整类型来源应用与独立账本。网页/研究图仍待接新版本；一般弱解、H₀¹边界迹、高维及存在唯一性继续未完成。新增实数Cauchy–Schwarz例用于核实际内积导入，不计原创成果。见[验收](../evaluation/pde-language-results.md)。

<!-- MRA086 -->

2026-10-29 · MRA086 当前状态；下文旧阶段原样保留。

新增事件监视扫描器与独立 pde-energy-watched-v2。46契约通过；相关317项266通过/51跳过；12质量命令通过；真实全链与成功重放后丢失监视的发布门禁均通过；14条CLI通过。旧MRA085两项竞态失败保留，旧实现没有修复，本次旧测试通过不改变历史结论。

A3仍只有有限经典一维Poisson链；本轮新profile改善扫描期间可观察变化的检测，保留原导数/可积/volume/两端点/端点顺序条件。没有新增一般弱解、Sobolev边界迹、高维或存在唯一性理论。下一步仍是[结构化接入](pde-language-preflight.md)。见[验收](../evaluation/watched-integrity-results.md)。

<!-- MRA085 -->

2026-10-29 · MRA085 当前状态（下文旧阶段保留为历史记录）。

真实Poisson能量链专项通过（939.626秒）、14条CLI与12质量命令通过；相关协议回归270项为218通过/50跳过/2项旧streaming竞态失败，原失败及可复现时间戳诊断保留。

A3已有第一条有限经典一维Poisson积分/能量链，实际导入已核验证明NDJSON、完整类型桥接、结果直接使用审计与独立重放。5历史正目标E4，损坏恢复传播后1当前有效。尚无一般Sobolev弱解/边界迹/高维推广或新网页输入；见[验收](../evaluation/pde-energy-results.md)与[结构化接入预查](pde-language-preflight.md)。


<!-- MRA084 -->

2026-10-29 · MRA084 当前状态（下文旧阶段保留为历史记录）。

257项相关回归208通过/49跳过；12质量命令通过；单独真实图/HTTP专项通过（857.806秒），14条实际CLI通过；实际Edge桌面/390px浏览器14项通过。

A2新增同项目任务图和网页/HTTP真实操作。A3仍未实现；已做[E0能量链源码预查](pde-energy-preflight.md)，现有指定分部积分和Sobolev模块的olean均未取得，不能以源码存在声称已可执行。分部积分仅volume，Sobolev全空间定义不自动对应有界区域零边界空间。见[验收](../evaluation/calculus-research-results.md)。

<!-- MRA083 -->

2026-10-29 · MRA083 当前状态（下文旧阶段保留为历史记录）。

204项相关回归164通过/40跳过，质量10命令与真实工件复核通过（数学目标含未完成）；11个历史E4检查，三项实际恢复控制后8个当前E4；未完成项['quantified-derivative', 'composed-derivative']。A2受控HasDerivAt/IntervalIntegrable、显式测度/有向积分及a.e.语言已进入真实独立重放；下一步研究图与网页，A3 PDE能量/弱形式待开发。见[验收](../evaluation/calculus-language-results.md)。

<!-- MRA082 -->

2026-10-29 · MRA082 当前状态（下文旧阶段保留为历史记录）。

184项相关回归145通过/39跳过，质量10命令与单独真实专项通过。九正目标9个历史E4，恢复控制后7个当前E4；五数学负对照和两政策控制UNKNOWN，两额外安全重放拒绝。A2有限导数/区间积分隔离基础和真实导出重放已完成，原A0/A1保持。尚待通用结构化输入及研究图接入，再进行PDE能量/弱形式。见[验收](../evaluation/calculus-foundation-results.md)。

<!-- MRA081 -->

2026-10-29 · MRA081 当前状态（下文旧阶段内容保留作历史记录）。

本轮两样本未证明稳定提速：第一组新版更快，第二组旧版更快。新profile仅作为显式可选实验入口，未替换原默认执行。164项相关回归，127通过/37跳过；质量10命令及单独真实专项通过。同资源两目标交错对照和真实损坏恢复/取消控制已收集，共7完整检查/12阶段，当前3个E4检查。A1数学语言保持，扫描完整性/worker/整项分别记账。下一步按[A2预查](analysis-a2-preflight.md)准备有限导数/积分隔离环境，后续才做PDE能量/弱形式链。见[验收](../evaluation/analysis-streaming-results.md)。

<!-- MRA080 -->

2026-10-29 · MRA080 当前状态（下文旧阶段内容保留作历史记录）。

A0只读分析环境保持；A1有界Real、Type 0实内积空间、函数/λ、量词及完整条件语法/执行入口已实现。最终30秒计划的12个已知正例中11个完成真实检查/不同PID重放，未完成：pointwise-hypothesis；未完成者保持UNKNOWN。先优化已实测的完整性/执行成本，再进行A2固定导数/积分的隔离环境及真实小目标；A3弱形式/PDE尚未实施。见[验收](../evaluation/analysis-language-results.md)与[A2预查](analysis-a2-preflight.md)。

<!-- MRA079 -->

A0已完成独立InnerProductSpace.Basic环境与三个固定已知正例的真实检查/导出/独立重放；其中一个被损坏恢复控制永久停用、两个一般空间正例当前E4。A1结构化语言、A2导数/积分、A3局部PDE任务均未完成。下列历史源码检查记录保留，最新缓存和结果见[验收](../evaluation/analysis-foundation-results.md)。

2026-10-29：用户明确后续数学方向为“分析与偏微分方程”。这是领域优先级，不代表当前语言已支持实分析或PDE。模型仍待用户本地配置；基础验证开发继续。

当前可操作的是Nat/Int/Rat及显式Type载体、有限结构运算/定律与递归程序。任意载体不是Lean实数，显式运算不是已经建立的范数/积分/弱导数；不得用增加假设把目标改成容易通过的形式，再声称解决了分析问题。

## 分阶段实现和退出证据

| 顺序 | 实施范围 | 必须验证的交付 |
| --- | --- | --- |
| A0 独立分析环境 | 固定Mathlib现有commit的有限分析import清单、缓存和实际导出/独立重放能力 | 查明实际olean/cache可用性；新环境身份和许可/用途记录；完整原类型/公理门禁与旧E4同时保持，不静默扩大旧全局import范围 |
| A1 实数与内积估计 | 显式ℝ、阶/绝对值/范数/内积、函数与向量类型、必需类型类语境 | 从内积非负、Cauchy–Schwarz或简单能量界等已知小目标起步；全条件保留，错误符号/遗漏条件/类型混用真实失败；每项完整目标独立重放 |
| A2 极限、导数与积分 | 显式函数域/余域、连续/可导、可积/几乎处处、积分区间与测度 | 函数表达及绑定不捕获；可积、有限测度和a.e.条件不省略；区分Bochner积分的默认值约定与数学可积性；正负对照和精确来源 |
| A3 PDE局部研究链 | 先选择小型线性能量估计任务，再逐步进入弱形式、边界条件、Sobolev空间 | 未知量/区域/正则性/初边值及解的概念写清；分部积分、测试函数和每个中间引理均有完整类型与证据；数值离散迹象不当连续PDE证明 |
| A4 研究价值评估 | 领域题目来源/曝光记录、固定预算、已知结果与待解决缺口 | 成本含检索/准备/审计/证明；保留未知和失败；未见题目只在确实未曝光时使用此标签；原创性另经文献和专家审查 |

阶段A0/A1优先于扩大现有代数模板数量。现有引理库检索可沿用证据边界，但新分析语境需受控映射及真实类型桥接，不能直接复用旧载体描述。精简失败诊断是运行可靠性改进，不算完成上述任何分析数学阶段。

## 已检查的本地候选来源

目前固定Mathlib commit为56a005855ea17e0b0958f73ec801ae140b0a1679。仅检查本地源码存在以下入口，尚未在本项目证明执行器中运行或获得新分析E4：

- Mathlib/Analysis/InnerProductSpace/Basic.lean：real_inner_self_nonneg、norm_inner_le_norm。
- Mathlib/MeasureTheory/Integral/Bochner/Basic.lean：integral_nonneg_of_ae、integral_nonneg、norm_integral_le_integral_norm。
- Mathlib/MeasureTheory/Integral/IntervalIntegral/Basic.lean：intervalIntegral相关非负性与范数界。

本次检查发现上述InnerProductSpace.Basic、Bochner.Basic、IntervalIntegral.Basic三份候选模块的目标olean文件均缺失；仅检查指定路径，不代表依赖闭包已盘点。源码存在不等于预编译缓存可用、导出器支持全部定义或原问题已形式化；A0须隔离准备缓存并通过真实试验确认。初期不承诺一般PDE存在性/正则性、非线性理论或四大期刊级原创成果。

参见[长期路线](research-capability-roadmap.md)与[蓝图](../../Math_Research_Agent_Development_Blueprint.md)。
