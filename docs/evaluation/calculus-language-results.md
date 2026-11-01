# 结构化微积分语言验收

MRA083，2026-10-29。新增calculus-language-v1的可组合输入与独立账本。204项相关回归164通过/40跳过，质量10命令与真实工件复核通过（数学目标含未完成）；11个历史E4检查，三项实际恢复控制后8个当前E4；未完成项['quantified-derivative', 'composed-derivative']。这是已知开发目标的语言/执行验证，尚无PDE、模型搜索、盲测或原创研究结论。

## 可操作范围

在原实数、Type 0实内积空间、函数/λ、量词与逻辑上，新增MeasureReal、显式volume/零/一般测度、deriv及HasDerivAt、带测度的有向实数区间积分及IntervalIntegrable、μ.real(Ioc)与a.e./区间成员条件。微积分目前限ℝ→ℝ。函数、积分变量和量词使用唯一Lean名字；积分端点和测度在外层求值，body在局部作用域求值，不能捕获同名端点。所有原条件包括未用条件均进入完整宿主目标。

可导与可积不由算子默认值推出，不自动补端点顺序、有限测度或正则性；一般测度不套Lebesgue长度公式，a.e.不自动提升逐点。μ.real是toReal语义。原a.e.范数界无显式可积前提，不能额外宣称已证明可积。受控库规则可递归组合；例如保留两HasDerivAt条件，先应用加法再取得deriv值。固定规则集合仍限制证明能力，开放输入不意味着任意数学目标可解。

显式CLI支持catalog/preview/submit/work/report/list/cancel/revoke/recover。preview只UNKNOWN/E0，具名对齐确认绑定完整原文、计划与预算。独立calculus_identity账本拒绝收养旧库或重定向，旧后台worker不自动扫描。当前没有新网页表单或研究图自动接纳。详见[协议](../protocols/calculus-language-v1.md)。

## 实际目标与安全控制

18声明案例中13个正例（12微积分/绑定/组合目标和1既有内积兼容目标），5个数学负对照；另1例真实重放成功后取消。量词导数在共享40秒内候选完成、重放超时；组合导数执行在仅写plan后中断，没有worker启动收据，两项均未完成。负拒绝、超时和中断保持UNKNOWN，不算反证。实际记录如下：

| 案例 | 记录状态 | 当前数学判断 | 永久失效 |
| --- | --- | --- | --- |
| derivative-id | COMPLETED | UNKNOWN | True |
| derivative-constant | COMPLETED | PROVED | False |
| derivative-add | COMPLETED | UNKNOWN | True |
| interval-constant | COMPLETED | PROVED | False |
| interval-add | COMPLETED | UNKNOWN | True |
| interval-reversal | COMPLETED | PROVED | False |
| zero-measure | COMPLETED | PROVED | False |
| general-measure | COMPLETED | PROVED | False |
| ae-norm-bound | COMPLETED | PROVED | False |
| shadowed-endpoint | COMPLETED | PROVED | False |
| quantified-derivative | TIMEOUT | UNKNOWN | False |
| inner-energy-compatibility | COMPLETED | PROVED | False |
| missing-derivative-condition | REJECTED | UNKNOWN | False |
| missing-integrability | REJECTED | UNKNOWN | False |
| wrong-orientation | REJECTED | UNKNOWN | False |
| wrong-measure | REJECTED | UNKNOWN | False |
| ae-is-not-pointwise | REJECTED | UNKNOWN | False |
| cancel-after-real-replay | CANCELLED | UNKNOWN | False |
| composed-derivative | INTERRUPTED | UNKNOWN | False |

错误宿主目标与损坏NDJSON的两个额外真实重放均拒绝。真实proof字节、worker账本记账、原区间可积条件账本分别损坏后失效，恢复原字节/旧行仍失效。取消hook先调用真实原worker并取得成功重放，再发取消，不伪造Lean结果。运行与数学判断分开，三份停用记录不计当前有效证据。相同固定Lean实现的两个PID不是不同内核互证；语义对齐为本地声明。

诊断保留export之前的初始错误并提供阶段/类别/至多6000字符文本，完整stdout/stderr仍归档。五个负例的实际原始错误均是类型不匹配或未闭合目标，其中两例Application type mismatch被v1分类器保守归为通用LEAN_DIAGNOSTIC，无模板导入错误；诊断本身不能证明原命题为假。

## 过程、环境与成本

原calculus-foundation-v2身份85ea4b09d3c1112afea78ee3c8d35a2df4446ad5307da054db1df7efc5a123ed及v1物理树保持，Lean4.35.0/Mathlib56a005855ea17e0b0958f73ec801ae140b0a1679不变。新语言只增加已缓存的旧A0 InnerProductSpace.Basic到三微积分imports中；四项明确范围、原版本/许可/用途/限制先记录于.runtime/calculus-language-v1/adoption.json。没有新增树复制、缓存下载、解包或第三方依赖。provider=e4eb5afdadf246ddeea80882ef3ccd73498f7425fce65da2e384b90751a75d7a，绑定编译器、执行器、旧基类/账本辅助源码、环境锁、原模板与adoption摘要；实际执行和成功报告核全运行树。

首次完整模板E0在原三imports检查12个候选，exit0/6.620897587秒；保留旧内积语言并登记第四import、闭合账本辅助依赖后，第二次E0完整模板检查13候选，exit0/7.723370152秒。两次都是候选预查，无独立重放/E4，不是正式目标自动重试。开发格式/unused及测试收集器API自审修正写入preparation-notes；初段启动前已修正；后续正式失败及原记录见下文。

冻结后204项相关回归164通过/40跳过，质量10命令与真实工件复核通过（数学目标含未完成）；11个历史E4检查，三项实际恢复控制后8个当前E4；未完成项['quantified-derivative', 'composed-derivative']。相关回归与真实专项分开。初段真实专项因会话中断而缺少总耗时和退出收据，原11结果与12行账本和全部已有工件已另存；全局锁内恢复组合导数为INTERRUPTED/UNKNOWN，不重跑。显式续跑先核原18案例、结果前缀、provider、工件和账本，只执行未提交的6项，再运行取消/安全/恢复控制。续段unittest 365.837秒，因两项诊断分类断言失败而exit1；两例原始Lean均为Application type mismatch，但v1分类器返回通用LEAN_DIAGNOSTIC。未改写执行器或结果，保留当时收集器来源；随后只修正收集器以接受这两例明确原始错误，不重跑数学，另做工件复核。两段合计18有结果检查记录（含1超时、1取消），另1条仅计划的中断尝试；实际可核的33正式worker阶段（含2安全重放），正式worker累计152.088196秒；另2预查共14.344268worker秒。6GiB/512MiB swap实际cgroup记录保留；memory压力和max事件只是E0观测，不能证明耗时根因。每目标最多一次候选/重放，1–40共享worker秒，完整性、质量、CLI、复审和保全另计，无提速声明。13个canonical CLI命令与2个明确私有账本API报告检查合计15项，无新Lean进程或模型调用。

续跑前相关回归曾出现一次旧test_same_bytes_rewrite_during_open_is_rejected失败（204项中163通过、40跳过、1失败），原日志保留。独立诊断20次未复现，随后原样完整复核通过；未修改旧测试/扫描器。根因尚未证实，不能把这次波动当作已修复，也不能据此声称发现改字节绕过；同字节改写检测的稳定性列为后续问题。

576旧来源、442旧DB和917旧状态以停止服务基线另核，旧数学证据/研究图/成绩不重跑、不改写。两版旧微积分及A0/A1/streaming来源不变，历史失败与失效保持；本轮未实际执行整个项目restore、升级或回滚。完整证据在artifacts/calculus-language-20261029-01/及独立任务账本快照。

下一步将结构化微积分证据接入研究任务与网页，再建立小型PDE能量/弱形式链；研究问题的文献、新颖性、假设相容性和价值判断仍须另外核实。见[ADR0075](../adr/0075-typed-calculus-language.md)与[分析/PDE路线](../product/analysis-pde-roadmap.md)。
