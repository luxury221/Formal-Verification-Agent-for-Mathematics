# 结构候选程序 v2：递推索引排序

schema：structure-strategy-proposals-v2。CLI：structure-strategy-v2-plan --input <JSON>；structure-strategy-v2-node <graph_id> --input <JSON>。完整输入、预算、严格 JSON、反馈与确认边界沿用 v1；旧接口仍使用冻结的 v1 行为。

HTTP：POST /v1/structure-research/<graph_id>/strategy-proposals-v2，仅 node_id 与 budget，严格同源／CSRF、无 query、192KiB 上限。不排队、不唤醒 worker、不调用 Lean 或模型。工作台默认使用此 action，应用仍只带入草稿，不修改已保存图。

响应新增 induction_ranking、ranking_complete、ranking_policy。每项含 variable、active_index_occurrences、zero_exposures、successor_exposures、goal_occurrence_order。排序为后继暴露数降序、零暴露数降序、规范化目标中自由变量首次出现顺序。原样返回完整目标、原形式化陈述、所有候选 request 与 proof_source。计划绑定新版 wrapper、排序器、冻结 v1 Planner、替换依赖和原执行器来源；耗时 stats 不进入计划指纹。

只收集展开后目标中的活跃 fold，不进入悬置 step.body，不因未使用的定义存在 fold 而归纳。只尝试在这些索引中自由出现的顶层 Nat 变量。使用词法绑定排除规则，内部后继变量不来自用户名称；排序近似不构成证明，某些嵌套、量词或非标准递推可能无有用排序。没有活跃索引时先尝试原等式改写。

共享原预算：max_rewrite_steps 1–6、max_expansions 1–2000、max_candidates 1–8；工作台4／800／8。排序也消耗 max_expansions 与规范化燃料，合成检查点0.75秒、规范化32768节点、替换前中间数据估计4096节点；准备／来源审计及后续真实检查另计。超限没有完整搜索保证，ranking_complete=false 时不发布局部排序。候选恒 UNKNOWN/E0、lean_calls=model_calls=0；origin 只说明生成路径。

已有 REJECTED 同程序仅移后，不删除。反馈绑定当前 graph_id、node_id、revision 和完整目标；损坏或撤销后降为 UNKNOWN/E0。网页显示排序依据，编辑或切换上下文丢弃迟到候选。应用只改一个 proof 草稿，需重新预览、另存和具名核对。成功必须取得完整原目标的独立真实 E4，失败／超时仍 UNKNOWN。

诊断方案及实际结果见 docs/evaluation/structure-strategy-ast-results.md。作者可见变体相关，不能用本结果宣称模型、原创研究或四大期刊问题能力。
