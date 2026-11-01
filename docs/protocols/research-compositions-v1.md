# 组合证明与研究节点关联 v1

协议：`research-composition-evidence-v1`。存储：`artifacts/research-compositions/flow-*/record.json`，原子带摘要envelope及非阻塞lease，检测损坏后写独立持久停用标记。没有新SQLite主库。

HTTP读取 `GET /v1/research-compositions/{flow}` 返回完整原研究任务、组合账本、节点/原问题判断及当前来源；`GET .../{flow}/nodes/{node}` 返回完整目标与最近200条记录中匹配的历史候选，标签 `RECORDED_E4_REQUIRES_PREVIEW` 不表示已复核有效。

`POST .../{flow}/preview` 输入 `node_id, run_id`，返回UNKNOWN/E0及计划指纹。要求原文、标题、变量名及顺序、全部原前提、目标、预算完全相同，仅映射目标协议schema字段；还要求完整形式化类型一致。来源必须为COMPLETED/PROVED/E4、有效具名对齐、工件摘要一致、候选类型一致和独立进程数据重放。读取并不执行新的Lean检查。

`POST .../{flow}/bind` 输入 `node_id, run_id, plan_fingerprint, request_id, reviewer, confirmed`。确认必须为JSON true，审核者非空。计划绑定父研究语境、完整flow计划、当前具体前置、组合计划/来源/工件/对齐和桥接代码版本。提交重新预览并检查账本revision；同请求ID且相同正文返回当前审计结果，变更正文拒绝。旧请求不重新激活。

`POST .../{flow}/withdraw` 输入 `binding_id, request_id, reviewer, reason, confirmed`。撤销此关联并按拓扑复核后继。只有前置当前PROVED才允许新的后继关联；后继绑定具体选中来源指纹。添加未选备用来源保持原指纹，撤销或替换已选来源则停用依赖后继。来源撤销/损坏、语义对齐或原研究语境变化持久失效；恢复字节/旧请求不复活。真假证据冲突时UNKNOWN，原问题需自己的完整证明。

原研究路线、推演、续研各自保留判断；组合关联只形成新证据视图，不给旧调度器自动解锁，不注入新的Lean假设，不自动归档。旧逐步推演若仍受原路线前置阻塞，会显示其原有拒绝原因。

CLI：`python3 -m mra research-composition-{report|export|catalog|preview|bind|withdraw} FLOW`，catalog另接NODE，其他写操作和preview接 `--input JSON`。严格JSON拒绝重复键、未知字段和超过192KiB输入。HTTP继承Host/同源Origin/CSRF等原安全边界；worker/SQLite锁忙返回503，不写永久损坏。最多50条有组合账本的flow、每flow64条关联、每node8条、每flow256条幂等请求，原子文件2MiB上限仍有效。

网页 `/research` 复用统一串行请求队列。具名预览/确认、撤销、完整审计导出；POST不自动重发，人工重提固定正文/请求ID。GET仅503有限再读、耗尽后隐藏旧证据并手动重读。切换节点/来源、刷新清旧确认，迟到结果不改当前节点。链接 `/compositions#target/FLOW/NODE` 预填完整原目标，需要重新选择来源并构造证明。

`fresh_attestation=false`，`lean_calls=0`和`model_calls=0`指本次关联操作；原组合来源具有自己的真实运行记录。姓名为本地声明，协议fixture与作者功能示例不代表模型能力、新颖性或重大难题求解率。
