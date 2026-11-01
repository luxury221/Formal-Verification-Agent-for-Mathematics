# 精简递归诊断 v2

请求沿用[原递归程序](structure-recursion-program-v1.md)全部字段和数学限制，但schema必须显式为structure-recursion-program-v2。旧v1请求不自动升级；用户不能提供verbose配置、任意Lean、tactic字符串或新增字段。预览输出diagnostic_profile=compact-grind-failure-v1，始终UNKNOWN/E0。

CLI前缀structure-recursion-compact-，支持catalog/preview/submit/work/report/list/cancel/revoke/recover。preview/submit/revoke使用--input FILE（192KiB、严格JSON）；report/cancel/revoke使用run_id；work可选--run-id。提交信封仍为request_id/request/plan_fingerprint/reviewer/confirmed，具名重新确认完整原目标、全部条件和预算。work显式执行；Web后台不扫描新库。原v1 CLI和网页不变。

默认独立账本artifacts/structure-recursion-compact/lab.sqlite3；与原structure-recursion库隔离。新manifest含原递归provider、新编译器hash及诊断配置，完整计划重新编译比对。报告不是新认证；临时忙抛出，版本/工件漂移持久停用，还原旧字节不复活。取消、监督恢复和全局单worker锁由原执行器处理；不自动重试、迁移、导出为来源或晋升旧图。

编译器只处理原编译生成的整行all_goals grind，替换为all_goals (grind -verbose)；精简同样记录到局部证明元数据。目标类型、原条件、完整目标检查、公理白名单、真实数据重放、共享worker秒及1MiB文件限额不变。报告增加execution_diagnostics：仅对当前未失效失败记录，重新核对最多1MiB日志的真实字节及hash，再从前4096bytes提取最多500字符首条错误，附阶段/字节数/日志hash；损坏、重定向或暂不可用不显示旧摘要。原result.error末尾6000字符不改，摘要为E0运行观察。关闭verbose省去grind内部诊断，保留失败目标；任何剩余溢出仍是运行错误/UNKNOWN，不因精简或无错误字符串授予PROVED。

本轮不提供“不会溢出”的硬保证，不将既有已见目标重复运行当新研究成绩；所有新对照另立任务、执行前声明且不覆盖旧结果。见[设计](../adr/0070-opt-in-compact-recursion-diagnostics.md)和[真实验收](../evaluation/compact-recursion-diagnostics-results.md)。
