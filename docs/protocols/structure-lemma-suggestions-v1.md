# 当前结构引理调用建议 v1

协议：structure-lemma-suggestions-v1。建议为UNKNOWN/E0，不启动Lean或模型。

CLI：python3 -m mra structure-lemma-suggestions --input request.json。

请求包含schema、graph_id、node_id、budget、timeout_seconds，拒绝额外字段和客户自报反馈／来源证明。budget含max_depth=4、max_rewrite_steps=3、max_terms=32、max_expansions=1600、max_candidates=4；每项须显式提供整数。后续候选与独立重放的共享预算timeout_seconds为1–30秒，当前建议不消费该Lean预算。

HTTP：POST /v1/structure-reuse/graphs/<graph_id>/nodes/<node_id>/suggestions，正文仅budget与timeout_seconds。沿用原同源、CSRF、严格JSON和请求大小限制，不唤醒执行队列。

响应包括完整目标、原语境指纹、实际来源审计、完整候选request／应用实参／所有条件证明／来源指纹、符号条件推演、原图实际检查反馈、独立成本与限制。候选request仍采用冻结structure-proof-composition-v1；来源仍须恰好覆盖同图每个直接前置，全部显式使用，不能为获得建议静默改变DAG。

原图实际反馈与推演条件分开显示。条件未直接匹配原前提，只代表当前程序搜索尚未闭合；不是必要条件的数学证明，更不是反例。来源目录只给候选ID，必须通过原load_source当前核对才可供搜索。

工作台“匹配已证引理”后可查看全部细节，选择前重新读取来源指纹。带入草稿清旧预览、姓名确认及待提交正文；之后重新预览并具名执行。修改来源／别名／程序／预算或切换语境会丢弃旧建议。提交、撤销、取消、原ID恢复和完整导出继续使用原复用协议，原图判断不自动晋升。

限制：有界数据实参及保守词法匹配、显式引理／假设／自反／合取与等式合同改写；无量词内匹配、自动新引理、跨图搜索或模型规划。最多每前置两个可用来源、八份来源审计及八种组合；共享合成检查点不含来源完整性扫描和后续Lean。不能将本地作者诊断外推为独立研究能力。
