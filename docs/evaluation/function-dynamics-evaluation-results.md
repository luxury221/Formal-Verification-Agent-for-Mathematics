# MRA071：公开来源函数迭代领域对照

日期：2026-10-29。新增function-dynamics-evaluation-prepare／run／report CLI，结果工件位于`artifacts/function-dynamics-evaluation-20261029-01/`。决策见[ADR0063](../adr/0063-public-source-function-dynamics-diagnostics.md)，命令及门禁见[协议](../protocols/function-dynamics-evaluation-v1.md)。现有工作台资源未改，本轮不声称新增网页流程或浏览器验收。

## 问题、来源与曝光

MRA066固定六题来自本项目旧例子，不能据此判断最新v3策略在有公开出处的新领域任务中的用途。本轮选择函数迭代、半共轭及运算保持性，固定八题、六个相关任务族，包含两个结构挑战及一个删除条件对照。

来源是既有Mathlib固定提交`56a005855ea17e0b0958f73ec801ae140b0a1679`的[Function/Iterate.lean](https://github.com/leanprover-community/mathlib4/blob/56a005855ea17e0b0958f73ec801ae140b0a1679/Mathlib/Logic/Function/Iterate.lean)。原文件8272字节、SHA256 `b924373d6688ac3cde2e489a248c939650db59da633e0facccda01dd3882e5de`；Apache-2.0 LICENSE为11357字节、SHA256 `b40930bbcf80744c86c46a12bc9da056641d722716c378f5659b9e555ef833e1`。从准确上游commit URL重新读取并与现有归档逐字节一致，见`source-verification/summary.json`及保存的完整文件。首次WSL读取超时，Windows续读同一URL成功；本地Git HEAD不可用，没有冒称已通过本地Git验证。

题目清单`evals/function-dynamics-v1/cases.json`保存原声明行号、原陈述、摘要、改写关系、许可、用途及限制。开发者已经看到原文和相邻证明；所有题均可见，非盲测，不用于训练。生成器只收到完整目标和预算，未输入原定理名、来源文字或参考证明；模型调用0，无新依赖。

七道正向任务是Type0／点态／显式左作用Nat.rec的改写；没有建立与Mathlib原迭代定义之间的已核验等价桥接。真实证据证明的是展示并具名核对的完整改写命题。删除半共轭条件一题不再等同上游定理，不能靠搜索失败宣告反驳。

最初六题草稿在代码审查后、任何真实Lean结果产生前，加入次数相加与交换复合两题，避免只验证基本单层保持性。原六题草稿、初次测试patch入口错误和修正均在`pre-review-backup/`及`initial-findings.json`保留；不是看到真实分数后调题。之后未修改生成器、题目、程序预算或补试追分。

## 固定比较与实际结果

legacy是原v1生成器，current是原v3；每题每组最多2候选、800合成展开、4步改写。一次真实候选检查和独立数据重放共用20秒，每题每组上限40秒，八题双组最多32检查／640秒预留worker。按题交替组顺序，所有槽位、失败、未解和跳过均保留。合成／完整性审计成本另计，不是相同总CPU或端到端墙钟硬预算。

| 完整改写任务 | legacy | current |
| --- | --- | --- |
| 固定点迭代 | PROVED/E4 | PROVED/E4 |
| 不变量观察 | PROVED/E4 | PROVED/E4 |
| 半共轭迭代 | PROVED/E4 | PROVED/E4 |
| 二元运算保持 | PROVED/E4 | PROVED/E4 |
| 交换函数与迭代交换 | PROVED/E4 | PROVED/E4 |
| 删除半共轭条件 | UNKNOWN | UNKNOWN |
| 迭代次数相加 | UNKNOWN | UNKNOWN |
| 交换复合的迭代 | UNKNOWN | UNKNOWN |
| 总计 | 5/8 E4，3/8 UNKNOWN | 5/8 E4，3/8 UNKNOWN |

完整评测ID：`function-dynamics-eval-49daf6b150924b3a986f9ac25c74192e`。32槽全部实际尝试，两组各16检查、320秒预留worker。legacy实际worker 34.135779秒、检查墙钟198.965966秒；current实际worker 34.541091秒、检查墙钟187.354154秒。准备调用成本分别0.657914／0.643240秒，合成循环分别0.014193／0.016493秒；计划构建1.498962秒。各项范围不同，不能相互替代或解释为总体提速。原始报告见`real-52f44000c1724b5fb4479fc71e4a225e/report.json`。

实际入选的前两个程序在八题中均相同：rfl加所选Nat归纳。因此本轮只能说明在此固定候选预算下两组覆盖相同，未显示v3的通过数优势，也没有检验更宽预算或其他排序的效果。

## 真实失败诊断与下一步

`unknown-diagnostics.json`将真实程序、返回码、日志路径／摘要及片段与候选准备记录对应。次数相加两组均选n归纳，step用grind但未闭合；v3把n排在m前，并保守排除出现在词法绑定内部的参数。交换复合任务的grind实际触及`gen := 8`项生成阈值。这些是已尝试路线的事实，不是“最小必要引理”的证明，也不能认定其他路线均不成立。

失败返回码为1，无独立重放；Lean错误后的占位声明即使打印type_matches及含sorryAx的审计文本，也被原门禁拒绝，保持UNKNOWN/E0。不能以日志中的成功形状文本替代真实有效工件。

下一阶段优先研究候选预算分配、词法安全的参数泛化、归纳方向及中间引理计划。替代路线可能有用仍属待验证假设，须另立完整任务／预算后真实核验，不回填本轮成绩。此结果不支持一般独立求解能力、原创数学或四大期刊级研究能力结论。

## 工程验收、退出与保全

| 校验 | 证据 | 结果 |
| --- | --- | --- |
| 最终相关回归 | `validation-contracts-552c63ff1c3e44cb987d642ca7d2db1d/` | 431项，362通过／69跳过，125.702秒 |
| 新真实专项 | `validation-real-661fd133475a4677b6cc5ce16e5a7d10/` | 2项通过，462.305秒；完整八题及子E4后监督退出 |
| 质量 | `quality-5af91fd82d0b4a4cbbb619729f8c1644/` | 9条命令通过 |
| 实际CLI | `cli-91d7823c16c34891bbad21d0a8152302/` | 8条通过；包括准备、报告、已完成ID再运行及旧图／旧组合报告 |
| 自审 | `review.json`、`manual-code-review.json` | 完整目标／proof重新编译，全部实际工件与类型／公理／不同PID核对，旧完整报告保留 |

协议测试中的不可执行模拟元数据仅验证状态机，不计入数学成果。旧六题草稿的相关回归保留但不与最终成绩重复累加；69项跳过未计通过。最终相关回归、真实专项、质量及CLI来源运行前后相同，交付再核对当前字节；没有在真实验证后更改产品或测试。

退出专项ID`function-dynamics-eval-9fc7be7aaf85475fa6f762ef6121431a`：真实子检查取得完整E4后监督进程以73退出，父视图INTERRUPTED、summary为空，子结果未发布为评测成功；再次运行不自动恢复。记录在`real-interruption-b4b78c5314b4447888448256861fb4e1/`。该任务不纳入八题比较分母。

只读自审脚本首次误按sqlite3.Row读取实际元组而失败；修正辅助脚本为row[0]后复核，不修改产品或重做Lean。首次记录保留。新评测父始终UNKNOWN/E0，已证子命题分别报告，现有原图与组合判断不混用。

停服务基线记录479份旧来源、400个旧SQLite账本、587份旧状态。保全行集合、冻结数学源、旧报告及历轮封存映射见`preservation-final.json`；新私有账本和评测状态另行快照，不实施真实恢复演练。最终服务恢复后核对76个静态路由逐字节一致、Windows访问200、worker健康、模型未配置，采用4GiB内存／512MiB交换上限，未设置开机自启。服务与独立Windows封存复核见`runtime-check-*.json`、`windows-check-*.json`、`postseal-windows-recheck.json`及最终`delivery.json`。
