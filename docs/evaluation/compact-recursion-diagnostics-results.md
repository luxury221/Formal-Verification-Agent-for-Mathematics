# 精简递归失败诊断验收（MRA078）

2026-10-29。本轮解决MRA077实际暴露的失败grind日志写满1MiB限额问题。新增CLI显式配置及原错误头部摘要；不是新的数学求解算法。权威阶段artifacts/compact-recursion-diagnostics-20261029-01，以最终delivery.json及Windows独立复核为准。

[设计](../adr/0070-opt-in-compact-recursion-diagnostics.md)；[协议](../protocols/compact-recursion-v2.md)。用户后续方向已确认为[分析与偏微分方程](../product/analysis-pde-roadmap.md)，本轮尚无分析新E4。

## 实际修改和隔离

固定Lean4.35.0源码Meta/Tactic/Grind/PP.lean的goalToMessageData及Main.lean的Result.toMessageData表明，config.verbose控制附加facts/eqcs等诊断。原完整日志确实在这些诊断途中达到1048576bytes并报file too large。候选源码不是通过输出字符串判真。

新mra/compact_recursion.py先沿用原严格编译器解析完整请求，只将固定生成的整行all_goals grind替换为all_goals (grind -verbose)，局部引理元数据同样精简。原量词/全部条件/完整目标、结构化证明策略、共享worker秒、公理白名单及候选/独立重放门禁保持；1MiB沙箱限额没有改变。

新schema为structure-recursion-program-v2，必须显式选择；CLI structure-recursion-compact-支持catalog/preview/submit/work/report/list/cancel/revoke/recover。预览仍UNKNOWN/E0，提交具名核完整目标及预算；新账本artifacts/structure-recursion-compact/lab.sqlite3，需显式work。网页和旧v1后台队列保持，不自动迁移、重跑旧任务或导出为独立来源。新manifest绑定旧provider、新编译器及诊断配置，版本漂移/还原后不复活。

报告新增execution_diagnostics运行观察：先用忙感知报告完成原门禁，再对已记录stdout/stderr的最多1MiB真实字节各自核hash；从前4096bytes选最多500字符首条错误，附阶段、字节数和日志hash。失效/损坏/重定向/无法读取不显示旧摘要。诊断字段E0，不改变数学判断；原result.error的末尾6000字符保留。

## 真实预声明对照

最终area real-dde83c9d34cd4e069f14688c7606c4ec，执行前declared-plan.json固定四个已见程序、每组每目标一次、每完整检查20worker秒。只改schema及输出配置，两组全部原类型/目标/证明策略与预算逐项相同；没有候选搜索fuel差异。相同策略在资源有限环境中仍可能受输出成本影响，不做一般语义等价或性能保证。

| 预先固定程序 | 原详细诊断 | 新精简诊断 |
| --- | --- | --- |
| two-lemmas-failure | EXECUTION_ERROR/UNKNOWN，1048576bytes | REJECTED/UNKNOWN，32413bytes |
| two-layer-failure | EXECUTION_ERROR/UNKNOWN，1048576bytes | REJECTED/UNKNOWN，28531bytes |
| positive-two-helper | PROVED/E4，完整类型/公理/独立重放 | PROVED/E4，完整类型/公理/独立重放 |
| unused-false-helper | REJECTED/UNKNOWN，无重放 | REJECTED/UNKNOWN，无重放 |

两个失败目标的新首条摘要均保留grind failed原始错误头。错误helper即使未使用仍拒绝整个程序。另一个新正例实际重放成功后取消，最终CANCELLED/UNKNOWN，没有晋升。本轮测量诊断可靠性，不把原错误状态修复当“多解出两道数学题”。全部任务开发者已看过且与旧来源相关，不是盲测。

最终9完整检查/12实际进程阶段，2当前E4、4REJECTED/UNKNOWN、2EXECUTION_ERROR/UNKNOWN、1CANCELLED/UNKNOWN。初次real-b25ec43d9b8f46329c0b17d229b66644在前4次检查后因断言失败结束：错误地要求旧result.error末尾6000字符含grind，而完整日志开头有此错误。首次产品版本尚无有界首条摘要，修后增加真实hash绑定摘要并重新冻结质量/回归/真实专项；首次源码、完整日志及失败均保留。两轮合计13完整检查/16真实阶段，初次两个新配置记录属历史修订、均UNKNOWN，不冒称当前新E4。没有覆盖MRA077的结果或重复累计为原创数学成功数。

## 验证和保全

最终相关506项：427通过、79旧真实opt-in跳过，145.912秒。新9契约覆盖精确整行转换、helper/原条件保持、schema/预算拒绝、预览不执行、独立账本、版本漂移/恢复不复活、具名幂等/取消、暂忙，以及首条错误hash/迟到损坏隐藏。单独真实专项1项通过，111.526秒，4GiB内存/512MiB swap scope；最终质量10命令通过。测试期间产品/测试源不变，回归→真实专项顺序执行。初轮504项回归和初次真实失败都保存，不用较早通过冒充最终源码证据。

实际CLI十五次调用通过，覆盖新旧schema互拒、预览、具名幂等、取消后work不执行、recover/list/revoke和旧真实报告；Lean0。无新HTTP/网页交互，本轮不重跑浏览器数学；最终79资产逐字节校验和Windows实际HTTP用于核对旧服务仍可用，不冒充新UI验收。

只读审查核全部13条记录的真实文件摘要；最终完整计划重编、类型/公理审计与不同PID重放、原预算/条件、既有独立来源和MRA077当前E4复核。88个旧核心/数据文件不变；旧完整图/组合报告及MRA071两组5/8保持。525旧来源中仅main与5份状态文档改变，431旧数据库全部旧行和800旧状态保持，无任何旧账本追加。5份新账本（两轮各2私有库+新CLI库）、新状态与原索引链独立备份，无实际restore。全部失败和修前代码保留；无新外部依赖、模型调用、提交或发布。

## 下一步

按用户选择进入分析与PDE。已确认本地Mathlib固定commit中的内积/积分源码存在，但指定InnerProductSpace.Basic、Bochner.Basic、IntervalIntegral.Basic目标olean缺失；这只是路径观察，尚未证明依赖闭包、导出器或重放可用。先建立隔离分析环境与真实小目标闭环，再接实数/函数/范数/可积条件及局部能量估计。当前不能宣称已支持一般PDE验证或原创研究。
