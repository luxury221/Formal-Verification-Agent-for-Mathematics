# 项目微积分研究图 v1

MRA084，2026-10-29。本阶段集成已验收；最终封存状态以 evaluation/calculus-research-results.md 和同阶段 delivery.json 为准。

## 范围

schema 为 calculus-project-research-v1，网页入口 /analysis。每张图属于既有研究项目及一条分支；其原始问题、分支语境和具体前置证据均进入身份校验。一个节点的 target 是完整 calculus-language-v1 请求：原文、变量、全部前提、结论、证明程序及共享 worker 预算。各节点各自完整，不共享或自动增加数学假设。

只接受现有受控实分析与微积分语言，不接收任意 Lean 源。可导、可积、a.e.、测度和积分方向必须显式表达。算子的默认值不能代替正则性或存在性证明；本版本未提供 PDE 解的存在性、正则性或弱形式研究能力。

## 输入和预算

完整请求包含 project_id、branch_id、nodes。节点严格包含 id、kind、depends_on、target；kind 为 lemma/subproblem/problem。必须恰有一个原问题，所有节点均属于它的无环前置图。最多 8 节点、7 个直接前置、16 次具名检查、480 预留 worker 秒。每目标 1–40 秒由候选与重放共享，完整性扫描另计；已取消尝试仍占预留预算。

预览返回完整节点计划、顺序、原问题 ID、父语境、来源闭包和 plan_fingerprint。保存绑定该精确指纹，保持 UNKNOWN/E0，不启动 Lean。同一 request_id 只能重现同一内容。图 ID 为 calculusflow-32hex，attempt ID 为 calcattempt-32hex。

## 实际检查与判断

先对可执行节点取得 node-preview，再提交 request_id、node_id、execution_fingerprint、reviewer、confirmed=true。指纹绑定全部原条件、证明计划及每个前置的确切当前证据。具名确认是本地语义声明，不是账户认证或独立专家确认。

前置只控制调度与具体证据绑定，不导入证明项。前置完成时原问题仍 UNKNOWN；原问题须有自己的完整候选检查、数据导出、不同进程重放、精确类型与公理审计及语义对齐。两个进程仍使用同一固定 Lean 实现。运行状态与数学判断分别记录，失败/拒绝/超时/取消/中断均不构成反证。

全局单 worker 串行消费新图和原队列。没有自动数学重试；GET 的暂忙重读不提交新检查。监督进程退出后，显式恢复将 RUNNING 标为 INTERRUPTED/UNKNOWN，即使底层已写出成功重放日志也不会晋升。

## 来源、撤销与保存

新 manifest 绑定原微积分 provider、新图实现、继承的 typed_research/research_flow 生命周期及实际辅助来源。原数学编译器、环境与已封存来源不就地修改。每图使用独立 calculus-language-private/lab.sqlite3，先只读核对既有 identity；缺失或空账本拒绝，不从文件自动重建证据。

完整图在隐藏准备目录写入后，以同文件系统 rename 发布；未完成准备保留但不进入队列扫描。撤销节点使依赖后继停用。父语境、图计划、provider 或工件损坏会留下持久失效标记；恢复旧字节或旧父记录不会复活。导出按钮下载研究记录 JSON，内含来源、完整目标和审计信息，不声称打包全部原始证明文件。

## HTTP

沿用现有回环服务的 Host/Origin/CSRF、请求体和固定资产策略。新路由拒绝 query 参数。

| 方法 | 路由 | 用途 |
| --- | --- | --- |
| GET | /v1/calculus-research/catalog | 语言与两条已知调度示例 |
| POST | /v1/calculus-research/preview | 完整图预览 |
| POST | /v1/calculus-research | 保存 UNKNOWN 图 |
| GET | /v1/calculus-research/projects/PROJECT/BRANCH | 当前项目分支的图 |
| GET | /v1/calculus-research/GRAPH | 重新核对并读取图 |
| POST | /v1/calculus-research/GRAPH/node-preview | 完整节点与前置身份预览 |
| POST | /v1/calculus-research/GRAPH/submit | 具名确认后排队 |
| POST | /v1/calculus-research/GRAPH/cancel | 取消确切 attempt |
| POST | /v1/calculus-research/GRAPH/withdraw | 具名撤销节点及传播 |

网页切换语境、编辑输入、暂忙或读取失败时清除旧确认；证据无法核对时隐藏旧判断。POST 只发送一次，丢响应后使用原请求编号和项目任务列表人工核对。

## CLI

在项目根设置 PYTHONPATH=.，使用 python3 ops/scripts/calculus_research.py。catalog、preview/create --input FILE；node-preview/submit/cancel/withdraw --graph-id GRAPH --input FILE；report --graph-id GRAPH；list --project-id PROJECT --branch-id BRANCH；work [--graph-id GRAPH]；recover。只有显式 work 或运行中的网页后台 worker 执行数学检查。

## 验收边界

协议测试的 fixture 不证明数学成立。真实专项使用已知导数/积分链验证图和 HTTP 集成；错误方向、成功重放后的取消、工件损坏恢复、前置撤销及监督进程退出均须实际执行。另做桌面/窄屏浏览器、CLI、旧数据保全与工件逐项复审。它不重算原 MRA083 题组分数，不是盲测、模型效果或原创研究成绩。
