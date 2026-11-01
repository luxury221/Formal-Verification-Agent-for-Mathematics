# 受控逐步推演协议 v1

在[研究项目](http://127.0.0.1:8765/research)中打开已保存的研究任务，选择前置满足的节点，使用页面下方“看清目标，再走下一步”。旧研究图有未保存编辑时不允许启动绑定操作。

1. 准备并保存独立推演空间。完整目标、原任务计划和前置绑定进入不可变计划；保存不运行 Lean。
2. 选择“初始目标 · 只读取”，预览完整原式与预算，填写核对者并勾选确认，然后执行。真实 Lean 若仅因未解决目标拒绝完整定理，可保存 OPEN 观察，数学状态 UNKNOWN/E0。
3. 点击“从这个状态继续／分支”，选择一个固定步骤，重新预览和具名确认。每次完整前缀都在新 Lean 进程重建；失败记录保留，可从先前 OPEN 状态再选择其他步骤，不覆盖历史。
4. 完整前缀关闭全部目标仍需类型/公理审计与独立证明数据重放，全部通过才标记推演 PROVED/E4。原研究任务及知识库不会自动晋升。实际目标 pretty 文本不能单独证明定理。
5. 可取消活动检查、查看实际目标轨迹与全部原始审计工件、导出完整 JSON。取消、超时、中断、损坏及漂移均不能成为证明；恢复中断没有自动重试。

固定步骤编号：`intros`、`dvd-gcd`、`dvd-add`、`assumption`、`all-assumptions`、`omega`、`simp-gcd`。编译器只拼接固定宿主源码；不接受任意 tactic 文本、import、自定义定义、归纳或持久会话恢复。研究前置不成为 Lean 假设，全部原前提保留也不证明前提相容。

HTTP 复用 Origin/CSRF、严格 JSON 与请求大小门禁：POST `/v1/proof-steps/preview` 与 `/v1/proof-steps`，GET `/v1/proof-steps/nodes/<flow>/<node>` 及 `/v1/proof-steps/<scope>`；scope 下 POST `preview`、`submit`、`cancel`。创建输入为 flow_id、node_id、plan_fingerprint、request_id；步骤预览为 parent_id/tactic（初始均 null）；提交另需准确指纹、request_id、reviewer、confirmed:true。取消只接受 attempt_id。GET 不启动执行。

CLI：`proof-steps-preview/create --input 文件`、`proof-steps-catalog 任务 节点`、`proof-steps-report 空间`；`proof-steps-step-preview/submit/cancel 空间 --input 文件`；`proof-steps-work`、`proof-steps-recover`。输入上限192KiB，错误退出2。预览和保存无 Lean 执行，只有具名提交队列可以执行。

最多50空间、每空间16次尝试（包括读取、失败和取消）、前缀12步；候选与重放每次共享1–30秒 worker、每空间上限480秒，完整性扫描另计。一次仅一个活动尝试，全局 worker 串行。每5秒读取当前活动空间详情；短暂503可重读，用户手动重提同一确认使用同一请求编号，不自动重试证明。

存储为 `artifacts/proof-steps/scope-*/plan.json`、`record.json` 与每次尝试的 `checks/lab.sqlite3`、候选/重放目录。摘要信封、原子替换/fsync、短 flock 与原全局 worker 锁共同使用。创建与扫描共用基目录短锁，扫描取完快照即释放，不在该锁下执行 Lean。备份只含十四主库不足以恢复推演，须保留完整推演目录及子账本。来源、原语境、祖先或工件漂移持久失效；损坏空间隔离后继续其他工作，不从成功的 raw replay 推断父尝试完成。

前端在服务启动时缓存，更新后须确认空闲、正常重启并比较实际服务字节。模型仍未配置，无自动规划或难题能力分数。见[ADR034](../adr/0034-controlled-proof-prefix-branches.md)及[验收](../evaluation/proof-steps-results.md)。
