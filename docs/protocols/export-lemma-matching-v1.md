# 独立引理匹配 v1

CLI：structure-export-match --input FILE。HTTP：POST /v1/lemma-research/match；沿用本地Host、同源Origin/CSRF、严格JSON及192KiB上限。GET /v1/lemma-research/matching-example返回从当前核验来源派生的完整双向等式合取示例和原来源标识，明确非盲测；没有可用等式来源则拒绝。

请求严格为schema/target/budget。schema=export-lemma-matching-v1；target为explicit-structures-v1完整目标，不含proof，包含原文、载体、运算、定义、变量、全部条件、完整goal和1至30秒timeout_seconds。客户端不能传入来源描述、证明、历史反馈或PROVED标签。

budget严格字段：max_depth（1–6）、max_rewrite_steps（1–4）、max_terms（1–64）、max_expansions（1–4000）、max_candidates（1–4）；bool和非有限值拒绝。默认4/3/32/1600/4，网页使用最多2候选。最近200条目录仅预筛，最多8个完整当前来源复核；目录/来源达到上限会报告catalog_truncated。相同载体、运算和定义要求沿用原精确记录，含原描述字段；不支持跨环境重命名或添加运算。

响应包含完整target/formal_statement、source_manifest、source_audits、candidates、diagnostics、budget、stats和plan_fingerprint。所有候选与整体均UNKNOWN/E0；lean_calls=0、model_calls=0。目录排序仅使用结构符号重合，标题不参与数学推断。来源不存在或当前失效则排除；暂忙不永久判坏，HTTP503沿用既有读取协调：GET可有界重读；全部POST（包括match）不自动重发。

每个候选包含完整structure-export-application-v1请求、实际使用来源和source_fingerprints、显式applications（全部实参/原条件证明）、生成的proof_source。最多四个来源，未使用来源不导入；来源的每个原变量和原条件必须显式供应。诊断只表示本次有限搜索中的未闭合义务，不证明命题错误或条件必要。

搜索支持类型实参、原假设、合取、等式反向/传递及绑定体外合同；保留旧捕获避免与增长限额。所有来源共用0.75秒计时检查点及显式展开fuel，来源审查和最终完整编译另计，不是HTTP硬超时。达到上限仍保留已生成候选，数学判断保持UNKNOWN。

前端选择候选时重新预览完整应用，严格比对当前source_items的archive_fingerprint和建议source_fingerprints，再要求新的具名确认。后端仍在原submit/work/report门禁复核完整来源，历史建议不是授权或新认证。编辑输入/模式清确认，旧epoch响应不得回填。具体执行/撤销沿用[独立导出协议](structure-lemma-library-v1.md)与[工作台HTTP](lemma-research-web-v1.md)。

[设计](../adr/0069-target-first-export-lemma-matching.md)；[真实验收](../evaluation/export-lemma-matching-results.md)。
