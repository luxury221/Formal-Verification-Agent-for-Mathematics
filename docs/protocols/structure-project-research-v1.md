# 同项目显式结构研究 v1

schema：structure-project-research-v1。独立于旧typed/Nat图和单例explicit-structures-v1；结果见 ../evaluation/structure-project-research-results.md。

## 完整请求与数学意义

请求恰含 schema、project_id、branch_id、carriers、operations、definitions、laws、nodes。绑定已有本地父项目和研究线；计划保留父问题原文、分支标识和语境指纹。共享carriers／operations／definitions沿用explicit-structures-v1；laws是具名name/formula列表，每条须闭合于共享运算／定义及自己的量词，不能捕获任务节点变量。标签不自动产生定律，不证明结构存在或前提一致。

nodes有1–8项，恰含id、kind、depends_on、target。id独立短名，kind为problem／lemma／subproblem，必须恰有一个problem；所有节点必须位于该原问题的有向无环前置图，每节点至多7个不同前置。target为完整单例字段去掉schema／carriers／operations／definitions，仍显式包含title、original、variables、assumptions、goal、proof、timeout_seconds。禁止节点覆盖共享环境。

每项实际单例请求的assumptions严格为共享laws后接该节点原assumptions；总数仍至多8，名字不得重复。载体／运算／定义、全部共享定律、原变量和条件、原目标与指定程序完整进入子命题。前置不会被加入条件或导入证明项，不能凭前置成功判原问题已证。共享定律的纯编译作用域检查不执行Lean、不提交任何内部目标或产生数学证据。

请求总预算沿用192KiB／4096结构项，子表达式320节点／16层、定义／类型／折叠／一次Nat归纳边界沿用单例协议；不扩展导入或任意Lean权限。父图至多16项检查、480预留worker秒，候选加重放每项共享1–30秒、200000心跳、无自动重试；取消／失败不返还预留额度。完整性扫描另计，不保证端到端硬墙钟。

## CLI

在WSL项目根执行 python3 -m mra 后接：

- structure-research-catalog PROJECT [--branch-id main]：当前项目图目录、完整作者样例及边界。
- structure-research-preview --input graph.json：全部节点完整目标／程序／条件和父项目语境，始终UNKNOWN/E0，不执行Lean。
- structure-research-create --input create.json：恰含request_id、request、plan_fingerprint，保存完整图不授予数学证据。相同ID／正文幂等。
- structure-research-node-preview GRAPH --input node.json：node_id；需要每个前置的当前完整E4。返回全图指纹、具体前置身份、完整child_plan及execution_fingerprint。
- structure-research-submit GRAPH --input confirm.json：恰含request_id、node_id、execution_fingerprint、reviewer、confirmed:true。具名审核完整环境、所有定律、原目标和预算；姓名是本地声明。相同ID和确认幂等，取消后同ID不会重新排队。
- structure-research-work [--graph-id GRAPH]：持全局worker锁，显式同步执行一个确认过的节点。一次只允许一个活跃检查。
- structure-research-report GRAPH：完整计划、节点和尝试状态、子检查、原问题独立数学结论，输出可重定向为完整JSON。fresh_attestation:false不声称重新执行历史证明。
- structure-research-cancel GRAPH --input cancel.json：attempt_id，取消排队或执行中的检查。
- structure-research-withdraw GRAPH --input withdraw.json：node_id、reviewer、reason、confirmed:true，撤销该节点证据并持续停用依赖旧具体身份的后继。
- structure-research-recover：持锁将未提交父尝试降为INTERRUPTED，取消孤儿排队检查，不自动续跑或晋升。

具体structure-research-*分支先于旧structure-*通用分支，旧typed-*接口继续按原协议运行。

## 状态、持久失效与存储

主目录 artifacts/structure-research/structureflow-UUID：带摘要原子plan.json／record.json和独立checks/lab.sqlite3、真实工件；尝试ID为structureattempt-UUID。私有验收存储也只允许项目artifacts内，无路径跳转或链接重定向。完整父语境、实现、定义和预算均受manifest／计划指纹约束。

图级节点成功须有当前未撤销的COMPLETED/PROVED/E4子记录、完整目标／具名对齐／工件匹配，以及候选与独立重放的真实成功审计和不同PID。只有原问题自己的成功尝试才决定图的PROVED/E4。伪造标签而缺真实审计不能开门；失败／超时UNKNOWN，不当数学反证。

前置撤回、工件损坏、父语境或实现变化持续停用原尝试；恢复文件或重证前置不会复活后继，需新预览／确认／尝试。子命题本身独立证明与其在图中的证据身份分开：子检查提交成功而父进程退出，子记录可能仍E4，父尝试恢复为INTERRUPTED，原问题仍UNKNOWN。保留完整双方记录，不自动重跑。

报告与节点提交共用图lease；worker完成后有界等待读者释放，只有进入上下文可重试，不重放一次变更或Lean。空闲恢复只保守降级，不反复全量数学审计；报告及执行门禁继续完整核对。SQLite与文件无跨介质事务，不能从孤儿文件自动推断成功。

本轮CLI协议不由原HTTP后台队列消费，旧网页无法执行新图；不自动迁移旧图／修改旧证据，也未接入模型或原始证明数据复用。作者可见例题、同内核、条件一致性与本地对齐的边界保持。
