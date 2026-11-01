# ADR-0070：隔离的精简递归失败诊断配置

状态：实施；2026-10-29。

MRA077真实比较出现7次失败grind诊断写满原1MiB文件限额，最终EXECUTION_ERROR/UNKNOWN。固定Lean 4.35.0的Meta/Tactic/Grind/PP.lean中goalToMessageData受config.verbose控制，关闭verbose会保留失败goal而省去内部facts/eqcs诊断；Main.lean同样以verbose控制附加诊断。这解释现有日志膨胀，并非证明未完成的数学反证。

不得直接修改sandbox_worker、verifier或原递归编译器：它们参与已封存证据及独立引理来源身份，修改会造成历史来源失效。本轮新增structure-recursion-program-v2显式schema、CompactRecursionStore及独立账本，原v1及网页保持；CLI structure-recursion-compact-*需重新预览/具名提交并显式work。

先用原编译器严格检查相同结构化程序及完整目标，再仅将固定生成的整行all_goals grind改为all_goals (grind -verbose)，包括局部引理与主证明。原量词/条件/目标、证明策略数据、候选+独立重放、公理政策、总worker预算和沙箱文件限额保持。新编译器及诊断配置加入新manifest，不进入旧manifest；没有原证据自动迁移、免费重跑或旧分数改写。初次真实测试错误地从旧result.error末尾摘要寻找grind头部而中断；新报告另提供逐字节hash重核的有界首条错误摘要，保留原反馈与全部首次失败。

新配置须通过原完整数学门禁；精简输出不是证明，也不保证任何目标的诊断都小于1MiB。新真实实验预先冻结两个已见溢出程序、一份已见双引理正例及一个无用错误helper对照，各组各一次20worker秒，另做真实重放后取消。所有新运行及成本计入本轮，历史结果仍保持。只测有限日志可靠性，不测盲解/原创数学能力。界面集成、独立导出和旧图关联不在本轮实施范围。

协议见[compact recursion v2](../protocols/compact-recursion-v2.md)；最终事实见[验收](../evaluation/compact-recursion-diagnostics-results.md)。
