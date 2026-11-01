# Mathlib 研究节点协议 v1

在已有 `project_id` / `branch_id` 下保存独立的 Mathlib 节点图。账本为 `artifacts/research-mathlib/research-mathlib.sqlite3`，协议 `research-mathlib-v1`，决策见 ADR024。原 Nat/Int 研究图与检查协议保持原义；本协议未实现混合图调度或私有知识入库。

## 节点与版本

每条研究线最多16个节点，每项包含 `id`（最多32字符）、`kind`（problem/subproblem/lemma）、完整规范化 Mathlib `request`、`depends_on`（最多8个本研究线扩展节点）、`notes`（最多4000字符）。请求格式见 [Mathlib 实验协议](mathlib-lab-v1.md)。图最大160000字节，至多64个图版本、1024个事件、512次幂等写请求，每个研究线记录最多2MiB；整个侧库最多800条研究线。

依赖必须无环、无悬空引用，不会被插入 Lean 假设。各节点需要自身完整证明，所有引理已证也不自动证明主问题。Mathlib 扩展节点不会自动随原图分支复制。

读取响应区分 `revision`（所有提交及持久失效）、`graph_revision`（节点图快照版本）。空图读取返回0且不创建记录。修改携带 `expected_revision` 和 `parent_context_fingerprint`；后者固定原问题、研究线ID、旧图修订及完整旧节点，不依赖原项目全局版本或旧来源绑定。原图修改使扩展绑定停用；另建研究线不会影响原线。

## 证据绑定

预览输入包含 `branch_id, expected_revision, parent_context_fingerprint, node_id, kind, depends_on, notes, run_id`。来源必须是现有真实 `COMPLETED/PROVED/E4`，语义确认有效、记录工件摘要匹配、完整请求重新编译一致、候选与独立重放进程不同、返回码为0、确切目标和公理审计通过。

新节点从来源复制完整请求。已有节点的原文、类型、变量、前提、目标、证明项、预算及节点元数据必须完全一致；绑定入口不覆盖不同节点。前置必须都有有效绑定。预览返回完整节点、来源、项目语境、前置绑定指纹及适配器SHA，生成 `attachment_fingerprint`。

提交额外带 `request_id, reviewer, confirmed=true, attachment_fingerprint`。服务重新预览核对指纹，再以乐观修订写入。幂等键绑定完整操作；相同请求重放只返回当前状态，不恢复旧绑定；不同内容复用键拒绝。姓名是本地声明，尚无身份认证。

绑定只复核历史证据，新增 Lean 调用为0。节点“带回检查”传递完整原请求，必须重新预览、具名确认、真实候选及独立重放，随后再显式绑定。原独立实验及证明工件保留。

## 撤销与并发

图编辑改变的节点及其下游、来源撤销/损坏、适配器变化、项目语境变化、前置被撤销或重新绑定，均使当前绑定持久 `INVALIDATED`。显式撤销记为 `WITHDRAWN`；恢复文件、还原节点、重绑前置不会自动复活后继。历史事件保留旧绑定及来源快照。

原研究账本使用只读连接校验，不在新审计中触发原全项目来源刷新。跨SQLite账本没有全局原子事务；每次读取进行完整复核，HTTP来源撤销后定向复核，空闲worker每10秒轮巡一条研究线。后续定向复核暂不可用时，已经成功的来源撤销仍返回成功并标注 `DEFERRED_UNTIL_READ_OR_SWEEP`。SQLite BUSY/LOCKED 返回暂不可用，不持久污染绑定。通过旧CLI或直接Python类撤销的来源在下次读取/巡检传播。

图/来源在最后一次核对后仍可能被本机进程改变；本地文件系统、Python、SQLite和宿主仍受信。历史响应 `fresh_attestation=false`，不能描述为新一次 Lean 认证、持久签名或不可变存储。

## HTTP 与 CLI

所有写接口沿用精确Host、同源Origin、CSRF及192KiB JSON预算，拒绝未知字段。

| 操作 | 路由 |
| --- | --- |
| 只读研究线选择 | `GET /v1/research/{project}/mathlib-branches` |
| 当前节点与来源复核 | `GET /v1/research/{project}/mathlib/{branch}` |
| 图快照及事件 | `GET /v1/research/{project}/mathlib/{branch}/history` |
| 完整导出 | `GET /v1/research/{project}/mathlib/{branch}/export` |
| 显式编辑完整节点列表 | `POST /v1/research/{project}/mathlib/edit` |
| 预览完整绑定 | `POST /v1/research/{project}/mathlib/preview-attachment` |
| 具名绑定 | `POST /v1/research/{project}/mathlib/attach` |
| 具名撤销扩展绑定 | `POST /v1/research/{project}/mathlib/withdraw` |

写请求中的研究线置于 `branch_id` 字段。编辑额外含 `nodes, request_id`；撤销额外含 `node_id, reviewer, reason, confirmed, request_id`。

```bash
python3 -m mra research-mathlib-report <project-id> main
python3 -m mra research-mathlib-history <project-id> main
python3 -m mra research-mathlib-export <project-id> main
python3 -m mra research-mathlib-edit <project-id> --input graph.json
python3 -m mra research-mathlib-preview-attachment <project-id> --input attachment.json
python3 -m mra research-mathlib-attach <project-id> --input confirmed-attachment.json
python3 -m mra research-mathlib-withdraw <project-id> --input withdrawal.json
```

所有数据保存在本机，不调用模型、不分享、不训练。功能验收不是原创难题求解或研究收益评测。

MRA034补充：Mathlib成果的私有入库通过独立[归档协议](mathlib-knowledge-v1.md)提供；研究绑定仅管理研究线，不是该归档的逻辑依赖，撤销绑定不会撤销独立Mathlib归档。
