# pde-language-v1

状态：已完成本轮受控CLI与真实账本验收；边界及失败见[验收](../evaluation/pde-language-results.md)。

## 输入

请求包含 schema、title、original、spaces、variables、assumptions、goal、sources、
proof、timeout_seconds。schema=pde-language-v1，spaces=[]，只支持实数及其函数、
显式 MeasureReal。继承 calculus-language-v1 的有向区间、可导、可积和 a.e.语义，
新增 in_uIcc 闭区间成员关系。所有绑定均生成新的内部名字，不捕获外层变量。

sources 最多4项，包含 name、run_id、type_fingerprint。来源是当前有效的
pdewatched-* 或同账本 pdelang-*，完整输入中的类型字符串 digest 必须精确相符。
所有来源数据合计最多512KiB，且每项来源必须在最终结果证明对象中直接使用。
这些限制是资源/信任边界，不是数学定理存在性的断言。

proof 为固定 rfl/simp/grind 或有界结构化 term。source_apply 显式填写来源别名、
实参及条件证明；rewrite 显式填写每个经核验等式及方向；interval_congr 引入
完整闭区间上的点和成员条件。规则实际由 Lean 检查，Python 编译不证明充分性。

## CLI 与执行

在项目根使用 PYTHONPATH=.：

- python3 ops/scripts/pde_language.py catalog
- python3 ops/scripts/pde_language.py source --run-id RUN_ID
- python3 ops/scripts/pde_language.py preview --input request.json
- python3 ops/scripts/pde_language.py submit --input submission.json
- python3 ops/scripts/pde_language.py work --run-id RUN_ID
- python3 ops/scripts/pde_language.py report --run-id RUN_ID
- python3 ops/scripts/pde_language.py list
- python3 ops/scripts/pde_language.py cancel --run-id RUN_ID
- python3 ops/scripts/pde_language.py revoke --run-id RUN_ID --input revocation.json
- python3 ops/scripts/pde_language.py recover

catalog 示例包含故意无效的来源占位，必须用 source 查询后填写真实身份。
source 描述只陈述来源，不证明新目标。提交需 request_id、request、
plan_fingerprint、reviewer、confirmed=true。改输入、预算或来源必须重新预览确认。

work 显式获取既有全局单 worker 锁。候选与独立重放共享1–40秒 Lean预算，
没有自动重试；完整性/来源闭包审计不在 Lean预算中，端到端等待可能更久。
当前后台网页不自动消费本账本。seal 仅首次创建；重复封存拒绝，不提供替换迁移。

## 证据与中断

成功结果是 COMPLETED/PROVED/E4，其余数学判断 UNKNOWN/E0。
REJECTED 仅表示该证明程序未通过，不等于 REFUTED。
候选成功或残留 proof.ndjson 不能越过独立重放、完整工件及语义对齐。

报告重新核对来源、确切类型、编译来源、运行环境、完整原目标、具名确认、
公理/直接使用审计、不同 PID、共享预算、原始日志及 NDJSON 的逐字关联。
source 的撤销或观察到损坏使使用者永久失效；原字节恢复不复活。

取消在发布事务中再次判断；监督退出可保留 RUNNING/UNKNOWN，
显式 recover 在取得全局锁后仅改为 INTERRUPTED/UNKNOWN，不执行残留任务。

## 保证边界

单机可信宿主；watched 校验不是原子快照、持续监控或恶意宿主防护，
也不保证所有 mmap/远程写入事件。两进程使用同一 Lean 实现。
人工原文对齐不由内核自动认证；没有模型、通用PDE、原创性或研究收益声明。
