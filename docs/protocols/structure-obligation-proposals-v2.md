# Structure recursion proposals v2

通过structure-obligation-propose --input INPUT.json调用。输入严格为schema=structure-recursion-proposals-v2、完整explicit-structures-v1 target（不含proof）、budget（max_candidates/max_expansions/max_rewrite_steps）；默认2/800/4，上限8/2000/6。

候选request仍为structure-recursion-program-v1，可交给原structure-recursion-preview/submit/work/report；该后台没有新网页入口。提案和中间义务恒UNKNOWN/E0，不执行Lean或调用模型。candidate_id只标识请求数据；source_manifest另标识本版生成器与固定编译器，plan_fingerprint绑定建议语境。使用通用CLI执行时编译器来源与具名对齐继续由原协议绑定，评测另绑定生成器/题集/runner。

context_obligations展示最多8层共享项上下文的更强充分等式；不能从外层相等反推内层相等。自动至多两个有序内联引理，原环境/变量/定义/条件和最终形式命题保持；所有义务共同接受真实检查及独立数据重放。synthesis_analysis记录单调累计计数；所有搜索共享全局燃料/归一化节点/计时检查点，固定语法分解与末尾编译另有界但非端到端硬时限。

不进入绑定体，不合成逻辑逆命题，不自动入库/跨图复用。未闭合更强义务仍可尝试完整目标；所有失败留UNKNOWN，不能推REFUTED。来源、成本和最终证据见本轮验收报告，不能外推通用引理发现或原创研究能力。
