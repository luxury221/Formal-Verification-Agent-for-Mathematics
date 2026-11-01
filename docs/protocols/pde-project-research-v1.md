# 经典 PDE 项目研究图 v1

状态：提供逐节点检查及完整最终目标合并检查。旧 MRA088 顺序四节点专项失败记录保留；新流程的实际验收见 artifacts/pde-delivery-20261030-01。

输入为 project_id、branch_id 和 nodes。每节点包含 id、kind（lemma/subproblem/problem）、depends_on 和完整 pde-language-v1 target。唯一 problem 必须覆盖全部节点；环、孤立节点及未知前置拒绝。

符号来源为 {"name":"green","node_id":"green"}，必须指向直接前置。固定外部来源仍使用确切 run_id 和完整类型摘要。编译与保存不验证来源，节点预览时才从实际账本核验全部祖先。原文、变量、全部假设、证明、预算及最终完整形式类型均不得因绑定发生变化。

CLI：PYTHONPATH=. python3 ops/scripts/pde_research.py，子命令 catalog、preview、create、node-preview、submit、report、list、cancel、withdraw、work、recover。包含 --input 的命令使用严格JSON及192KiB输入上限。work显式执行，recover只保守中断。

HTTP位于 /v1/pde-research：GET catalog、projects/{project_id}/{branch_id}、{graph_id}；POST preview、根路径create、{graph_id}/node-preview、submit、cancel、withdraw。source-preview只接收pdewatched外部来源编号，核验后返回完整类型描述；描述和绑定草稿均UNKNOWN/E0。

网页 /pde 提供已知单实数估计和四步经典一维Poisson模板、完整请求编辑、确切来源绑定、全图预览、逐节点具名确认、真实后台执行、取消、撤销和完整JSON导出。四步模板不含本机运行编号，须显式绑定分部积分来源。所有写请求只发送一次；写响应丢失后由用户读取原项目核对。

复用既有Host/Origin/CSRF、严格JSON和CSP。图层和子检查共享全局worker锁及既有单后台调度器，不提供任意Lean、execute或recover HTTP路由。每次提交均需确认当前完整命题、来源与预算；旧确认不能授权修改后的输入。

检查失败、超时、取消及中断保持UNKNOWN/E0。前置已证不代表原问题已证。来源撤销或损坏后永久失效，恢复原文件不恢复可信等级。执行状态与数学结论分别展示。证据来自真实候选检查、不同PID的独立数据重放与本地具名语义确认；并非独立内核实现或专家认证。

2026-10-30 调度补充：GET /v1/pde-research/{graph_id}/progress 返回执行元数据及 evidence_checked=false，不包含 math_verdict/evidence_level，不取得图锁或进行证明审计。轮询必须使用此接口；取得终态后仍调用完整图报告核验证据。后台发布保留全局 worker 锁，等待图记录锁，不因读者超过30秒而自行放弃发布。参见 ADR-0081。MRA088 原完整专项失败记录保留，本补充不宣告其已验收。

## 合并完整推导

页面点击“合并推导后预览”，保存后在“核对并检查此节点”确认完整原问题，再执行一次真实检查。原四步输入及完整中间命题随计划保留，中间目标显示“未单独验证”，始终为 UNKNOWN/E0。最终原问题具有自己的检查与独立重放结果。

HTTP 新增 POST /v1/pde-research/expanded-preview，输入仍为 project_id、branch_id、nodes；POST /v1/pde-research/expanded，输入为 request_id、原始 request 和本次 plan_fingerprint。CLI 对应 expanded-preview、expanded-create，使用原有 --input 协议。保存和预览均不执行数学检查。

内部证明引用必须提供全部变量实参及条件证明，展开器避免变量捕获，外部源仍绑定真实编号和完整类型摘要。编译后最终原文、变量、条件、结论、预算及完整形式类型保持；不接受只关联调度而没有进入证明的节点。审计重新展开原图并比较全部结果。

项目任务列表现在仅读取元数据，不审核全库的证明、不发布数学判断；打开具体任务仍完整复核。参见 ADR-0082。本轮采用已知经典一维 Poisson 能量恒等式，不是一般弱解、存在唯一性证明或自主难题求解。
