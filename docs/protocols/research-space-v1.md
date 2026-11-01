# 私有研究空间 v1

日期：2026-10-26。设计见 [ADR-020](../adr/0020-versioned-research-space.md)。

## 数据与可信边界

`private-research-space-v1` 保存原始研究问题、项目版本、分支、图版本、节点、证据绑定和事件。默认本机私有，`training_allowed` 与 `sharing_allowed` 始终 false。研究管理操作的 `model_calls`、`lean_calls` 都为0。

节点字段严格为 `id, kind, title, statement, domain, formula, depends_on, context, notes`。角色为 problem/subproblem/lemma/definition。数学节点使用 Nat 或 Int，公式是已有受限 AST，须显式包含全部量词与条件。定义节点的 domain/formula 为 null，是文本语境；只能由 context 引用，不能冒充已证前置。depends_on 必须指向数学节点，无循环、重复或悬空引用。跨域前置仅作研究组织，绝不隐式转换或插入逻辑前提。

上限为100项目、每项目8分支、每分支24节点/64图版本；图128KiB、账本单项目2MiB、1024事件。无任意宿主代码/Lean输入、模型调用或图执行器。原问题保留原文，改换研究目标可以建立新项目或分支。

## 修改、历史与恢复

create 输入 `{request_id,title,question}`。edit 输入 `{request_id,expected_version,branch_id,nodes}`。fork 输入 `{request_id,expected_version,branch_id,label}`。每次修改使用项目版本比较；陈旧版本拒绝，不覆盖编辑。请求ID全局唯一，相同内容重复提交幂等，跨动作/项目/正文复用拒绝。重复操作返回当前核对后的项目，不能让撤销绑定复活。

图保存保留不可原地修改的快照。fork 记录父分支与图版本，复制节点结构；新分支的来源需明确绑定。绑定和撤销作为事件保存，不覆盖图快照。重启后从 SQLite 读取结构及来源，每次详情读取重新核对当前绑定。读取不是新一次检查，`fresh_attestation=false`。

## 来源绑定

prepare-binding 输入 `{expected_version,branch_id,node_id,review_id,key}`。key 是现有单定理检查的 claim 或步骤ID。检查该义务真实 COMPLETED、PROVED/REFUTED、E4、确切目标、具名对齐、候选与独立重放、协议及全部工件摘要。完整类型与公式必须相同；没有按数学等价、变量改名或省略前提进行匹配。

预览并列节点原文、定义语境、来源原文、完整公式和历史判决。bind 在上述输入上增加 `{request_id,binding_fingerprint,reviewer,confirmed:true}`。指纹绑定项目版本、节点及语境内容、依赖绑定、来源和研究实现版本。用户对节点原文与来源的对应作新的本地具名声明；历史E4来源不因此成为新的Lean运行。

只有全部 depends_on 节点保持 PROVED 时才允许绑定。没有自身来源的主问题保持 UNKNOWN，即使所有引理均已通过。已反驳前置会阻塞后继；该阻塞不是对后继数学命题的反驳。

节点/定义语境修改、来源撤销/取消、协议/工件变化及依赖绑定变化会持久 INVALIDATED，并沿研究依赖传播；恢复原文件或重新绑定父节点不会自动恢复子节点。withdraw 输入 `{request_id,expected_version,branch_id,node_id,reviewer,reason,confirmed:true}`，保留原始审查与工件。重新绑定需要新的预览与具名确认。历史绑定的来源ID和指纹保留在事件中。

## 入口

网页 `/research` 提供项目、分支、草稿节点编辑、依赖概览、来源绑定、版本查看和JSON导出。未保存修改会提示；非法图保持草稿。导出节点生成现有单定理标注，点击后明确带入 `/papers#research-import`，不会执行验证。用户准备、确认并运行后可返回绑定结果；仍受既有每项30秒候选与独立重放共享预算约束。

| 方法 | 路由 | 操作 |
| --- | --- | --- |
| GET / POST | `/v1/research` | 项目列表 / 创建 |
| GET | `/v1/research/{id}` | 核对后详情 |
| POST | `/v1/research/{id}/edit`、`fork` | 保存图 / 建立分支 |
| POST | `/v1/research/{id}/prepare-binding`、`bind`、`withdraw` | 预览 / 绑定 / 撤销 |
| GET | `/v1/research/{id}/history/{branch}` | 图快照 |
| GET | `/v1/research/{id}/export/{branch}/{node}` | 单定理标注 |

沿用精确Host、同源、CSRF、CSP和no-store规则。受控执行采用独立的 `/run-plan`、`/runs` 与 `/v1/research-runs/{id}` 接口，见 [research-runs-v1.md](research-runs-v1.md)。原研究管理CLI同一账本：`research-list`、`research-report ID`、`research-history ID BRANCH`、`research-export ID BRANCH NODE`；修改及预览命令 `research-create/edit/fork/prepare-binding/bind/withdraw [ID] --input request.json`，请求JSON上限192KiB，失败退出码2。

## 下一阶段

一般数学定义需要类型检查，受控引理引用需要实例化与重新验证。MRA031 的图执行、总预算、取消和恢复独立于本研究管理协议验收；保存和绑定本身仍是零执行操作。原创研究价值、新颖性与数学贡献仍需文献核查和专家复核。
