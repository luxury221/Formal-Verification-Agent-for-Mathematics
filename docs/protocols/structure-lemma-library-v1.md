# 结构局部引理导出和跨任务应用 v1

导出 schema 为 structure-lemma-export-v1，严格字段：schema、source、lemma、title、original、timeout_seconds。source 仅 run_id 与 fingerprint，来自 structure-lemma-export-catalog 返回的 canonical recursion 当前 E4。lemma 为原局部引理名，title/original 必须重新描述独立定理。预算为 1–30 worker 秒，覆盖该导出的候选及重放；预览保持 UNKNOWN/E0，不运行 Lean。

独立陈述保留父载体/运算/定义、全部原变量（可能存在未使用量词）及全部原条件。父完整目标换为选中的 helper goal；以前的 helpers 仍要在新完整证明中检查。导出类型不一定与原内联 helper 的最小量化类型相同。独立 E4 并不切断来源的撤销关系。

应用 schema 为 structure-export-application-v1，严格字段：schema、target、sources、proof、timeout_seconds。target 为 explicit-structures-v1 全字段但不含 proof；内外预算相同。sources 为一至四个 {alias,item_id}，item_id 只允许 export.<mathlib-id>。载体、运算、定义与来源须完全相同，允许不同变量名和实例项。应用 proof 沿用结构组合数据语法，显式 lemma/terms/proofs 及 congr/symm/trans 等；所有 source 必须实际引用。没有任意 Lean、tactic、候选模块导入或把来源追加成假设的路径。

两组 CLI 前缀分别为 structure-lemma-export- 和 structure-export-application-，均提供 catalog/preview/submit/work/report/list/cancel/revoke/recover。

- preview/submit/revoke 接受 --input FILE；report/cancel/revoke 要求 run_id；work 可指定 --run-id。
- submit 信封严格为 request_id、request、plan_fingerprint、reviewer、confirmed。需对完整新陈述具名确认；姓名仅本地声明。
- canonical 账本分别为 artifacts/structure-lemma-exports/lab.sqlite3 与 artifacts/structure-export-applications/lab.sqlite3。继承每库最多200记录、无自动重试、同全局 worker 锁、取消及恢复协议。
- 运行状态和数学判断分开。未知/错误条件不是 REFUTED。报告为历史证据复核，不是新签名或身份认证。
- 源撤销/损坏在访问时级联持久失效。临时锁竞争应稍后重查；执行中暂忙留下的 RUNNING 必须恢复为 INTERRUPTED/UNKNOWN，不免费复跑。文件修回不恢复已经失效的记录。

完整 CLI 及真实验收见 [验收记录](../evaluation/structure-lemma-library-results.md)，决策见 [ADR0067](../adr/0067-checked-local-lemma-exports.md)。
