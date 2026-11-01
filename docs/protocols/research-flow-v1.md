# 项目研究路线协议 v1

入口：[研究项目](http://127.0.0.1:8765/research)。选定项目和研究线后，页面下方提供“在同一个问题里，保留每条推演路线”。先保存已有研究图，再使用新任务面板。

1. 点击“准备新任务”，编辑完整节点 JSON 或载入三节点数论示例。节点含 `id`、`kind`、`target`、`depends_on`。target 使用现有完整 Nat/gcd/互素目标协议，禁止任意 Lean 源码。恰好一个 problem，其余节点均须位于其前置图中。
2. 预览完整研究任务，再保存。保存不执行 Lean，不赋予数学证据。任务图不可变；更改目标须重新准备新任务。它与所选项目的原问题及分支语境绑定。
3. 选节点和路线，生成计划。可用路线为 `proof-search`、`omega`、`simp-gcd`、`counterexample`、`goal-trace`。有限实验找不到候选时保留 UNKNOWN 和失败反馈。
4. 阅读完整原式、全部前提、Nat 域、变量映射、证明或反驳方向、固定证明来源及预算；填写核对者并勾选确认后，提交真实候选检查和独立重放。具名记录是本地声明，不是身份或自然语言翻译正确性的认证。
5. 引理先分别完成。研究前置不注入逻辑假设，原问题始终需要自己的完整证明。失败、取消、超时、中断、来源改变均保持 UNKNOWN；有限数值穷举没有反例不能证明原式。
6. 任务详情保留每条路线、错误、子检查、实际目标记录和事件，可导出完整 JSON，取消活动尝试或具名撤销历史研究绑定。撤销前置会使依赖绑定失效，原始独立证明文件保留。

`goal-trace` 只开放 `gcd-split-v1`：引入原变量与前提，应用 `Nat.dvd_gcd`，以原假设或 `Nat.dvd_add` 关闭子目标。真实 Lean 记录四个检查点，示例目标数1、1、2、0；不匹配的目标可能失败。没有任意交互步骤、会话恢复或分支搜索。诊断文本不单独构成证明，最终审计与重放仍为门禁。保留全部前提也不证明假设相容。

HTTP 共用 Origin/CSRF 和严格 JSON：`POST /v1/research-flows/preview`、`POST /v1/research-flows`，`GET /v1/research-flows/projects/<project>/<branch>`，`GET /v1/research-flows/<flow>`，以及该 flow 下 POST `propose`、`submit`、`cancel`、`withdraw`。GET 和参数不能启动执行。创建需完整计划指纹；提交需提案指纹、具名确认与幂等 request_id。取消只接受 attempt_id；撤销另需 reviewer/reason/confirmed。

CLI：`research-flow-preview --input 文件`、`research-flow-create --input 文件`、`research-flow-catalog 项目 [--branch-id main]`、`research-flow-report 任务`；`research-flow-propose/submit/cancel/withdraw 任务 --input 文件`；`research-flow-work` 和 `research-flow-recover`。输入上限192KiB，错误退出2。预览、保存与提案均不运行 Lean；只有具名提交的队列可被 worker 执行。

最多50任务、8节点、24提案、16次检查、每信封2MiB；检查共享 worker 上限480秒，完整性扫描另计。无自动重试、自动研究或模型调用。正常只轮询打开任务的详情，每次结束后等待5秒，避免反复扫描全历史。

本地存储或记录锁短暂忙碌时可返回503。界面保留同一份具名确认的请求编号，用户明确重按提交时沿用它，避免重复创建检查；不自动重提证明。已入队任务详情遇到临时503会在5秒后再读取。服务在启动时读取静态文件，前端更新后需先确认队列空闲、正常重启，再核对实际提供的资源字节。

备份须保留十四个主业务库，以及 `artifacts/research-flows/` 内全部信封、锁外的数据、子 SQLite 和运行工件。恢复旧记录不能撤销已检测的失效；恢复中断任务不等于重放，不会由子证明成功推断父尝试完成。见[ADR033](../adr/0033-project-research-routes-and-lean-goals.md)及[验收](../evaluation/research-flow-results.md)。
