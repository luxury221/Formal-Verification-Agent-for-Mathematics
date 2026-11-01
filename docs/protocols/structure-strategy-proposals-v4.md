# Structure strategy proposals v4

输入 schema 为 structure-strategy-proposals-v4，其余字段与旧版一致：target 必须为完整 explicit-structures-v1 目标且不含 proof；budget 显式给出 max_candidates、max_expansions、max_rewrite_steps，默认8/800/4，上限8/2000/6，不接受布尔冒充整数。

使用：python3 -m mra structure-strategy-v4-plan --input request.json。现有结构图可通过 structure-strategy-v4-node GRAPH_ID --input node.json 只读提出候选，node.json 包含 node_id 与 budget。输入不接受客户提供的反馈或参考证明。新 CLI 不会排队执行 Lean。

返回完整原目标、完整形式类型、source_manifest、稳定候选身份及 plan_fingerprint；stats 为运行成本，不纳入计划摘要。selection_analysis 逐程序记录排序评分 symbolically_closed_branches、方向与是否入选：直接闭合记2，归纳每个符号已闭合分支记1；直接程序本身没有两个归纳分支。它是合成观察，不是内核证明。generalization_analysis 继续列出原前提依赖排除。

匹配规则、局部配额与候选次序见 ADR0064。所有候选 UNKNOWN/E0、lean_calls/model_calls=0；选择后仍须原具名预览与独立检查。完整原前提、载体和定义不能被建议改写。旧v1/v2/v3及其来源身份不包含v4文件。

新题集 evals/structure-strategy-v4-v1/cases.json 为非盲作者诊断，真实测试须显式 MRA_REAL_LEAN=1；冻结全部双组候选后才执行，以当前完整E4停止该组，各失败／跳过槽保留。父诊断恒UNKNOWN/E0。旧两道障碍单列，不能回填旧成绩。独立重放为同一可信 Lean 内核的不同进程，不是独立证明助理。
