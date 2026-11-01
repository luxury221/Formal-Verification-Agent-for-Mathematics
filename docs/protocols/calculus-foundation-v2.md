# calculus-foundation-v2 基础协议

2026-10-29，MRA082。修正版将作用域命令放在全部imports之后；原v1完整模板导入顺序错误，首批12份失败/中断记录保留，均UNKNOWN，不计数学负对照成绩。仅仓库固定微积分探针，不接收任意Lean或自动改写原条件。

项目根使用 PYTHONPATH=. python3 ops/scripts/calculus_foundation_v2.py。

- catalog：列出三项固定imports、原文/完整形式化目标、候选程序与证据政策。
- seal：仅初次显式封存已准备环境；拒绝覆盖或迁移。需要固定版本/许可adoption、实际缓存acquisition及全部目标olean。
- run --probe ID --reviewer NAME：取得全局worker锁，真实检查候选、导出、独立数据重放。每次新ID，最多各一阶段，共享40 worker秒；没有自动重试或队列取消入口。
- report --run-id ID：重核来源环境、请求/具名对齐、全部成功工件、审计、公理、NDJSON、不同PID和有限共享预算。RUNNING只UNKNOWN/E0，不检尚未写完工件。

物理数学库复用已封存.runtime/calculus-foundation-v1/mathlib，缓存为原独立.cache/calculus-foundation-v1/mathlib，v2的adoption/acquisition/snapshot/lock在.runtime/calculus-foundation-v2/，工件artifacts/calculus-foundation-v2/runs/calculus-<32hex>/。runtime snapshot绑定自身实现、原可信执行器/模板、adoption和acquisition及全运行树。旧树与旧A0/A1 provider不变。仅采用既有基础公理propext/Classical.choice/Quot.sound，sorryAx、局部公理、额外计算信任不准通过。

VERIFIED/PROVED/E4_ALIGNED_AND_VERIFIED需真实候选和独立重放，条件在完整宿主命题内，具名语义对齐仍本地声明。REJECTED、TIMEOUT、系统错误、失效均UNKNOWN；失败不构成反证。永久invalidated.json使原证明/记录恢复后仍失效。监督进程被杀可留下RUNNING/UNKNOWN，当前没有自动重跑或恢复队列。

HasDerivAt表示显式导数证据，不由deriv数值推出；积分算子总定义不等于可积性。积分加法保留两条IntervalIntegrable条件，体积/零/一般测度区分，端点不加顺序，a.e.不自动提升逐点。固定a.e.范数不等式没有显式可积前提时保留原库语义，不能额外宣称已证明可积。

暂无通用微积分输入AST、网页、模型搜索、研究图自动接纳、PDE或原创研究能力。实际成功率、失败、预查/正式进程分别见[验收](../evaluation/calculus-foundation-results.md)。
