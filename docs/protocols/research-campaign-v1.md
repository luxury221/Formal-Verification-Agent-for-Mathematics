# 预审整图证明批次 v1

协议：`reviewed-proof-campaign-v1`。本地私有、作者提供完整任务图与证明程序。支持既有完整组合 E4 和 `library-lemma-composition-v1` 新检查；不自动规划、不调用模型、不自动重试或共享。

输入包含 `flow_id`、覆盖原任务全部节点的 `actions` 和总 `budget`。每个 action 为 `{"kind":"existing","source":{"origin":"composition|library-composition","run_id":"mathlib-…"}}`，或 `{"kind":"compose","request":完整新组合请求}`。现有证据与新请求均须逐字段匹配节点完整目标：标题、原文、变量顺序、全部原前提、结论与单次预算。

新请求的 `sources` 可使用私有成果条目 ID，或 `node.ID`。后者只允许本批次已提交的直接前置节点；不能把“图上有依赖”当作 Lean 假设。原前提的每个证明实参仍需显式提供。预审使用未来前置的完整类型编译程序，UNKNOWN/E0，不执行 Lean、不伪造证明数据。执行仅填入已提交前置的真实证明数据身份，不能更换预审的证明项、目标、预算或根归档。所有源数据通过冻结内核导入模板，新完整目标再独立进程重放。

`preview(input)` 返回完整原图、所有 action、源快照/未来类型、实现摘要和 `plan_fingerprint`。`submit` 输入精确为 `request_id,input,plan_fingerprint,reviewer,confirmed`。具名确认覆盖全部程序与总预算。本地姓名不是身份认证。相同请求 ID/正文保持幂等；不同正文拒绝；取消、失效或中断后的旧请求不重新排队。来源或实现变化需要重新预审和新请求，不修改旧封存来保留标签。

最多 8 节点、50 批次、192 KiB 输入。总预算范围：`max_checks` 1–8、`worker_seconds` 1–240、`wall_seconds` 1–600。每次新检查保守预留完整单次 timeout，候选与重放共享该时限；不退款、不自动缩短预算。现有 E4 只复核，不计新检查。墙钟门禁和运行取消轮询负责停止；完整性扫描与记录锁等待不提供硬实时上界。记录读写采用跨进程排他串行锁，避免状态查询导致监督执行误中断；全局 worker 锁仍拒绝第二个执行器。锁内不启动 Lean 子进程。

运行状态与数学判断分离。节点提交前的孤儿子检查成功不算图证据。批次 COMPLETED/FAILED/BUDGET_EXHAUSTED 可显示已提交且仍有效的部分；取消/中断批次整体保持 UNKNOWN。原问题必须有自己的完整检查，成功前置不能代替它。实现、原研究上下文、来源、前置绑定或工件改变会持续停用后继；恢复字节不能复活旧证据。读取失败不返回可用数学判断。损坏记录采用关闭门禁策略，可能阻止该本地目录的队列处理，需要人工诊断；不自动跳过损坏请求并重新执行。

存储：`artifacts/research-campaigns/campaign-ID/plan.json`、`record.json` 和逐节点 resolution 文件均为原子 SHA envelope；新检查位于该批次独立 `checks/library.sqlite3` 与 runs 工件。不进入主组合队列，不新增全局数学主库。历史报告 `fresh_attestation=false`。SHA 保护本地完整性，不能证明宿主未被恶意修改。两个进程使用同一固定 Lean 内核实现。

HTTP 前缀 `/v1/research-campaigns`：GET `/catalog?flow_id=…`、`/runs[?flow_id=…]`、`/runs/ID`、`/runs/ID/export`；POST `/preview`、`/runs`、`/runs/ID/cancel`（空对象）、`/runs/ID/withdraw`。撤销正文为 `request_id,node_id,reviewer,reason,confirmed`，请求身份绑定批次/节点/操作/正文。沿用 Host、Origin、CSRF、严格 JSON 与输入字节门禁。

CLI：`python3 -m mra campaign-{catalog,preview,submit,list,report,export,cancel,withdraw,work,recover}`。catalog 带 flow_id；preview/submit/withdraw 带 `--input`；report/export/cancel/withdraw 带 campaign_id；work 可带 `--campaign-id`；list 可带 `--flow-id`。UI `/campaigns`，研究任务旁可进入精确任务 ID。

界面请求串行，GET 遇 503 最多有限重读；POST 不自动重试。正文/来源/姓名变更清除相应确认，迟到响应不能恢复旧确认。响应丢失后可手动按相同正文和原 ID 恢复，读取失败收起旧判断。CLI 与网页必须明确区分完整批次证据和旧研究协议的状态；本阶段不会自动改写旧路线、续研或证据桥接的结论。

该阶段证明了受控 Nat 作者程序的数据流闭环，不代表模型求解率、原创性、一般数学定义、独立同预算收益或四大期刊级研究能力。后续需专门评测及扩展。
