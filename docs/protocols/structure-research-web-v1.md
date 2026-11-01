# 结构研究工作台与 HTTP v1

依据 ADR0052 与 structure-project-research-v1；页面 /typed-research 现名“数学研究”。选择“结构与归纳”后，使用完整显式结构图，原“数域与定义”及其复用面板保留独立协议。

## 操作

选择或新建研究项目及研究线；载入同态保持自然数幂示例，或编辑完整结构化任务。结构编辑器恰有 schema、carriers、operations、definitions、laws、nodes 六字段；项目和研究线由当前语境绑定。点击预览，核对所有共享环境及每节点完整目标，再保存任务。保存仍 UNKNOWN/E0。

选择 READY 节点，准备检查；逐项核对全部结构定律、每条原条件、变量类型、完整目标和受控证明程序，填写本地语义核对人并勾选确认。提交只登记一次，单后台 worker 执行真实候选与独立数据重放。原问题判断与每节点执行状态分别显示。前置已证只解除调度门禁，不代替原问题的完整证明。

执行中可取消；具名撤销某节点后，其旧后继证据持续停用。重新证明前置不会复活旧后继，必须重新预览和确认。导出完整 JSON 包含计划、全部定律／目标、尝试、检查结果和语义对齐记录。

URL 格式为 #PROJECT/BRANCH/GRAPH/structure；旧 typed URL 不变。两模式目录独立；结构模式不提供 typed 证明复用面板。

## HTTP

所有路由禁止 query，使用现有 session／CSRF／同源检查与严格 JSON，正文至多192KiB。GET 不执行 Lean；写操作仍需原协议完整字段。

| 方法与路径 | 作用 |
| --- | --- |
| POST /v1/structure-research/preview | 完整图预览，未执行 |
| POST /v1/structure-research | 带 request_id、request、plan_fingerprint 保存 |
| GET /v1/structure-research/projects/PROJECT/BRANCH | 同项目目录及示例 |
| GET /v1/structure-research/GRAPH | 当前完整报告与审计 |
| GET /v1/structure-research/GRAPH/export | 完整报告，未执行 |
| POST /v1/structure-research/GRAPH/node-preview | 带 node_id 准备具名执行指纹 |
| POST /v1/structure-research/GRAPH/submit | 原 ID／节点／指纹／核对人／confirmed，唤醒原 worker |
| POST /v1/structure-research/GRAPH/cancel | 带 attempt_id 登记取消 |
| POST /v1/structure-research/GRAPH/withdraw | 带 node_id／核对人／原因／confirmed 持续停用 |

同 ID 同正文提交幂等；换姓名、指纹或其他字段不能复用原 ID。沿用原图至多8节点、16检查、480预留 worker 秒，每项候选与重放共用1–30秒；失败和取消不返还额度。

## 恢复与范围

共享 FIFO 串行至完整 JSON 读完；GET 503 最多额外重读两次，POST 从不自动重发。提交响应丢失时，保留页面内的原正文，姓名和确认不可修改，可按原请求 ID 人工恢复。页面刷新或切换会清草稿，应先读取已有记录；失败不意味着服务端未接受。其他客户端可竞争，传输没有硬 HTTP 截止。

编辑、切模式／项目／研究线／节点和重读会清确认；异步预览按 epoch 丢弃迟到结果。报告读取失败隐藏旧 E4，手动刷新成功后才重新展示。模式切换禁止把旧 typed 组合来源交给结构图。

当前为作者可见功能验收，无模型自动规划、一般集合／分析、结构证明项复用或独立难题成功率。结构存在性、前提一致性及中文自动等价未验证；本地姓名非认证，同内核不同进程不等于不同验证器实现。
