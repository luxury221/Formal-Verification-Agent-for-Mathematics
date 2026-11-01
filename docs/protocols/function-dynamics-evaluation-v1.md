# 函数迭代领域对照 v1

新增CLI：

- `python3 -m mra function-dynamics-evaluation-prepare`：固定八题完整目标、两组候选和预算；保存计划但不执行Lean。
- `python3 -m mra function-dynamics-evaluation-run <evaluation_id> --plan-fingerprint <指纹> --reviewer <核对人> --confirmed`：核对全部来源、改写与条件后具名执行真实检查。
- `python3 -m mra function-dynamics-evaluation-report <evaluation_id>`：重新审计当前工件，完整JSON可导出；不重新搜索、补试或自动恢复中断。

固定题集在`evals/function-dynamics-v1/cases.json`，每题附公开源commit、完整文件摘要、声明行号／原陈述、改写范围和曝光。来自已采用的Apache-2.0 Mathlib，版本56a005855ea17e0b0958f73ec801ae140b0a1679；本轮从准确上游commit核对原文件／LICENSE字节，完整保存在验收工件。未增加依赖或模型，未将来源证明输入候选生成器。开发者已看原文及相邻证明，非盲测、不用于训练。

legacy为既有v1生成器，current为既有v3；均最多2候选、800合成展开和4步改写。每次候选与独立证明数据重放共享20秒；每题每组最多2检查／40秒，八题双组合计32槽／640秒预留worker。按题交替组顺序，真实完整E4后跳过同组剩余槽；失败、UNKNOWN、NO_CANDIDATE和跳过仍保存。两组实际合成、预留／实际worker、检查墙钟分别报告，完整性审计时间另计；不宣称端到端硬期限或总CPU成本相同。

具名确认前无检查账本，执行使用原全局worker锁与本评测记录锁。完整原条件、目标、程序、对齐和真实工件重新审计，完整类型、公理政策及不同PID重放均满足才计成功。撤销／损坏／版本漂移持久停用统计，恢复旧字节不复活；中断的子E4仍不能发布父评测统计。再次运行已执行ID仅重新报告，不补发或自动重试。

八题分别为固定点、不变量、半共轭、二元运算保持、交换迭代、删除半共轭条件、迭代次数相加、交换复合迭代。删除半共轭条件一题不再等同上游定理；两组搜索失败只能UNKNOWN。存在六个相关任务族，不能将八题当八个独立样本。正题也是Type0／点态／显式Nat.rec改写，没有已验证的上游定义等价桥接。父报告恒UNKNOWN/E0，逐题证据分别展示。当前新增入口为CLI，不改已有网页、旧题及历史分数。
