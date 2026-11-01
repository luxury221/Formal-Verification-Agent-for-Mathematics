# calculus-language-v1：结构化微积分输入与真实检查

MRA083，2026-10-29。当前为显式CLI语言与任务账本，网页及研究图自动接入待开发；尚无PDE、模型证明搜索、盲测或原创性结论。

## 输入与语义

请求必须包含schema（calculus-language-v1）、title、original、spaces、variables、assumptions、goal、proof、timeout_seconds。保持原analysis-language-v1的128KiB/1200节点/深32、20变量/16假设边界，预算为1–40整数worker秒。函数、λ、量词和逻辑可组合；新微积分目前仅ℝ→ℝ。可继续声明Type 0实内积空间及原分析表达式。

- 类型MeasureReal对应Borel实数上的Measure ℝ。volume和measure_zero的值必须为true；任意测度可作为明确类型的变量。
- interval_integral字段为name/lower/upper/measure/body。name绑定实积分变量；端点和测度在外层求值，body在内层求值。
- deriv字段为function/at，只表示总定义的导数值；has_deriv_at为function/derivative/at，单独表达导数存在及值。
- interval_integrable为function/measure/lower/upper；measure_ioc_real为measure/lower/upper，使用μ.real(Ioc a b)，不自动证明有限测度。
- ae字段为name/measure/body；in_Ioc、in_uIoc为term/lower/upper。a.e.与逐点量词是不同语法，不能自动提升。

可导、区间可积、端点顺序和有限性均不自动补入。不可导时deriv默认0；Bochner不可积或余域不完备时积分默认0；算子等式不等于存在性证据。有向区间积分不默认为a≤b。a.e.范数不等式沿用Mathlib原总定义命题，没有显式可积假设时不额外宣称可积。所有原条件，包括未用条件，保留在完整宿主目标中。

证明可使用原受控规则及has_deriv_id、has_deriv_const、has_deriv_add、deriv_of_has_deriv、interval_const、interval_const_measure、interval_add、interval_reverse、interval_zero_measure、interval_ae_norm_bound。规则可递归组合，允许局部假设与量词引入，固定rfl/simp/grind；不接收任意Lean、imports、公理、sorry或战术字符串。规则参数数目和类型以catalog为准，Lean仍需检查完整目标。假设相容性和自然语言等价性需要额外审查。

## 运维命令

项目根使用PYTHONPATH=. python3 ops/scripts/calculus_language.py：

- catalog：展示语法、限制与已知开发示例。
- preview --input FILE：读取请求，展示完整形式化命题、所有条件、证明程序和计划指纹；仅UNKNOWN/E0。
- submit --input FILE：提交request_id/request/plan_fingerprint/reviewer/confirmed封套，指纹必须与preview一致，具名对齐为local-assertion-only。
- work [--run-id ID]：显式取得全局worker锁执行一次；旧网页后台worker不扫描本账本。
- report --run-id ID、list：读取并核验记录；成功报告重核真实环境与全部工件。
- cancel --run-id ID：请求取消；成功重放后确认取消仍UNKNOWN。
- revoke --run-id ID --input FILE：具名reviewer/reason/confirmed撤销。
- recover：全局锁内将遗留RUNNING标记INTERRUPTED/UNKNOWN，不重试。

独立账本artifacts/calculus-language/lab.sqlite3有calculus_identity；私有测试目录必须位于artifacts下且叶名calculus-language-private，拒绝接收旧库或重定向。沿用原run ID格式mathlib-32hex。请求标识幂等且绑定原文、完整计划和reviewer，最多200条；预算含候选和重放总worker时间，文件完整性扫描另计。

## 环境、工件与失效

复用不可变calculus-foundation-v2环境身份与v1物理Mathlib树。四项imports为Deriv.Basic、Deriv.Add、IntervalIntegral.Basic及原A0的InnerProductSpace.Basic；新使用范围先在.runtime/calculus-language-v1/adoption.json记录版本、许可、用途和限制，没有新下载、解包或第三方依赖。provider manifest绑定新编译器/执行器、旧分析基类、账本辅助源码、原模板/执行闭包、实际运行环境锁及adoption摘要。

候选真实Lean导出NDJSON，另一个PID仅重放证明数据并核完整目标、公理闭包和禁止本地公理/占位/额外计算信任。两进程使用同一固定Lean实现。COMPLETED/PROVED/E4需所有门禁满足；超时、拒绝、取消、错误、中断或失效均UNKNOWN，拒绝不构成反证。永久invalidated-runs标记阻止恢复工件或旧账本行后复活。SQLite暂忙向上传播，不按证据损坏永久失效。

诊断在MRA_EXPORT_BEGIN之前提取至多6000字符并附阶段与错误类别，完整stdout/stderr保留。类型不匹配或未闭合目标均是候选检查诊断，不能单独判断原数学命题真假。实际完成率、预查、失败和安全控制见[验收](../evaluation/calculus-language-results.md)及[ADR0075](../adr/0075-typed-calculus-language.md)。
