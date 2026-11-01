# 结构候选程序 v3：有界原参数泛化

schema：structure-strategy-proposals-v3。CLI structure-strategy-v3-plan --input <JSON>及structure-strategy-v3-node <graph_id> --input <JSON>。完整target和预算形状沿用v2，输入不接收参考proof。旧v1／v2保留原协议和来源。

HTTP POST /v1/structure-research/<graph_id>/strategy-proposals-v3，body只有node_id和budget。严格同源／CSRF、无query、严格JSON、192KiB上限；不排队、不调用Lean或模型。网页默认调用v3；所有候选仍UNKNOWN/E0，应用只改本节点proof草稿，不修改保存图。

响应沿用完整target、formal_statement、induction_ranking、候选完整request／proof_source、来源指纹与当前图反馈，新增generalization_analysis。每项含induction_variable、eligible、excluded和options；excluded给出original_premise_dependency或parameter_under_lexical_binder原因。只有归纳假设中实际自由出现的其他原变量可以候选，共同泛化全体、逐个单参数和空列表依序去重。最多7个泛化参数，不改原变量、原前提或目标。

泛化IH按原类型实例化，词法绑定子项只有不含匹配参数时才按alpha等价比较，否则保守拒绝。模型、客户端或题名不提供泛化答案；不生成新引理、任意Lean或新的证明语法。候选origin为symbolic-generalized-induction或heuristic-generalized-induction时，仅说明Python近似路径，真实Lean可能拒绝。

预算仍为显式整数max_rewrite_steps 1–6、max_expansions 1–2000、max_candidates 1–8，网页4／800／8。所有选项共用原0.75秒合成检查点、32768规范化访问和4096替换前估计；准备／完整性审计及后续Lean预算另计。超限保留已产生程序，不保证覆盖全部选项或完整搜索。

当前保存图反馈只从服务端重新读取，原目标及所有条件逐字比较；客户端不能传feedback或generalize来伪造计划。迟到响应、编辑和模式／节点／版本切换清语境。应用后须完整预览、另存新图、具名确认，再取得候选和独立证明数据重放、完整类型与允许公理审计，才能获得该新任务自己的E4。原失败／超时不标REFUTED。

新诊断的来源、曝光、预声明选择及实际结果见docs/evaluation/structure-generalization-results.md。当前功能保守排除依赖前提和绑定内部参数，不证明条件一致性、结构存在、研究新颖性或独立难题能力。

产品HTTP／structure-research-* CLI创建使用完整目录原子发布适配器，防止后台扫描把未完成账本误判损坏；原图协议和数学来源不变。旧失效记录不复活，隐藏准备失败目录仅保留供检查，详见ADR0060。
