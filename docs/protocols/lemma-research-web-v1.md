# 引理研究工作台 HTTP v1

工作台为 /typed-research/lemmas，沿用 /typed-research 的数学研究导航。三类 kind：programs、exports、applications；底层协议分别为 structure-recursion-program-v1、structure-lemma-export-v1、structure-export-application-v1，不可互换。

- GET /v1/lemma-research/example 返回作者显式交换迭代目标，标明非模型/盲测。
- POST /v1/lemma-research/propose 接受严格 structure-recursion-proposals-v2，生成有界 UNKNOWN/E0 候选，不执行 Lean。
- GET /v1/lemma-research/{kind} 返回最多200个经过当前复核的记录摘要。
- POST /v1/lemma-research/{kind}/preview 返回完整预览；POST同前缀 /runs 接受 request_id/request/plan_fingerprint/reviewer/confirmed 具名信封，202入队并唤醒单后台worker。旧请求ID重发沿用原绑定，不能变更内容或预算。
- GET /v1/lemma-research/{kind}/{run_id} 及 /export 返回完整历史记录的当前复核。source_fingerprint仅完整当前PROVED/E4且类型/公理/独立PID审计通过时提供。fresh_attestation=false，不签发新证据。
- GET /v1/lemma-research/exports/{run_id}/source 返回当前核验的完整导出签名与来源摘要，绝不返回原始证明数据。执行器自行读取并内核导入 proof.ndjson。
- POST /v1/lemma-research/{kind}/{run_id}/cancel 要求空对象；/revoke要求 reviewer/reason/confirmed，执行持久撤销。

不接受查询参数、任意路径、原始 Lean、HTTP work/recover 或 proof.ndjson 下载。沿用严格同源、CSRF、Host、内容长度和192KiB预算。暂忙GET503含WORKER_BUSY/retryable_read，前端按旧协调器有界重读；POST单次发送，不自动重试。

启动服务会扫描这三类 canonical 库中已具名确认的 QUEUED，仍共用全局worker lease。旧CLI请求不需迁移；旧RUNNING恢复为INTERRUPTED/UNKNOWN，禁止从旧工件晋升或免费重跑。完整命题、来源和两阶段证据沿用原门禁。

界面编辑或切换会清除旧确认，晚返回的旧语境响应丢弃。已提交请求不因关闭网页而取消，可从历史打开；未知传输结果应保留原请求ID手动重发。来源撤销在访问时级联，回存旧文件不能复活。页面保留完整JSON编辑能力，当前没有任意自然语言自动解析或配置中的真实模型调用。

见[设计](../adr/0068-integrated-lemma-research-workbench.md)与[验收](../evaluation/lemma-research-workbench-results.md)。
