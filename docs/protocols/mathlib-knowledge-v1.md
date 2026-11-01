# Mathlib 私有知识归档 v1

协议 `private-mathlib-knowledge-v1`，决策见 [ADR025](../adr/0025-private-mathlib-knowledge.md)。独立账本位于 `artifacts/mathlib-knowledge/knowledge.sqlite3`，证明副本位于同目录 `proofs/{item_id}/`。原审查知识、研究图及 Mathlib 检查协议独立保留。

## 来源与入库

`prepare` 仅接受 `run_id`。来源必须当前可用且为真实 `COMPLETED / PROVED / E4`，完整请求重新编译一致、具名语义确认有效、工件哈希匹配、候选与重放进程不同、确切目标与公理审计通过。来源审计复用冻结的研究桥接器；研究节点的绑定不是入库前提。

预览返回完整原文、请求、全部逻辑前提、证明输入、版本、公理、两阶段审计、文件清单和 `plan_fingerprint`。相同结构完整命题通过 `proposition_fingerprint` 提示重复；不判断 alpha 等价，不合并独立来源。重复提示不影响确认指纹。

入库严格接受 `request_id, run_id, plan_fingerprint, reviewer, confirmed=true, rights_confirmed=true`。服务重新预览，核对完整指纹，复制真实工件，复核来源后提交。每个 run 对应一个稳定 ID：`ml-knowledge-` 加来源 ID 摘要的前32位。姓名与保存权利为本地声明；没有账户身份认证。

保存完整来源记录与真实文件副本，复核源及副本时均逐文件计算摘要。归档不运行新 Lean，响应 `new_lean_calls=0, historical_record=true, fresh_attestation=false`。当前可用条目显示历史 `PROVED / E4`；不能把入库或查询视作新认证。

## 生命周期、资源与并发

条目状态为 `ACTIVE / WITHDRAWN / INVALIDATED / DELETED`。来源撤销、源文件或副本损坏、适配器变化会持久失效；恢复文件不复活。显式撤销保留归档，移除清除本条目内容和副本，只留标识、来源运行 ID 与操作审计。原 Mathlib 实验独立保留；此操作不删除原实验，也不承诺磁盘取证级擦除。研究绑定撤销不影响独立归档。

撤销/移除严格接受 `request_id, reviewer, reason, confirmed=true`。请求 ID 绑定完整操作与内容；同请求重试返回当前状态，不恢复条目。停用后的同一来源不能重新入库，需要新检查产生新 run。移除已提交但文件清理失败时，可用同请求重试清理。

每次读取复核源与副本。HTTP 源撤销后定向复核，空闲 worker 每10秒轮巡一个归档；定向复核暂不可用标记 `DEFERRED_UNTIL_READ_OR_SWEEP`，不掩盖已经成功的源撤销。SQLite BUSY/LOCKED 返回暂不可用，不永久污染证据。

限500条目、2000个幂等请求、每条64事件、每条128源文件；单源文件16MiB、源文件总量64MiB、账本记录2MiB。拒绝符号链接、目录逃逸和未登记文件。文件复制在临时目录完成后原子重命名；中途失败不会出现 ACTIVE 条目。数据库提交前留下的完全一致孤立副本允许重试。

独立非阻塞文件锁串行化归档与移除，SQLite 写锁外进行源核验和复制。读取使用乐观比较后提交持久失效；多个账本之间没有全局原子事务。本机宿主、Python、SQLite和文件系统仍受信，未提供持久签名或不可变存储。

## 检索与重新检查

检索支持原文、完整公式、标题、库引用、条目/运行 ID，默认仅返回 ACTIVE；`include_inactive=true` 包含停用及最小移除记录。关键词最多200字符；前端分批展示30条。全局搜索包含当前可用的 Mathlib 归档。

`request` 仅对当前 ACTIVE 条目返回完整原始请求，标记 `requires_new_preview_and_confirmation=true, proof_imported=false`。它恢复原来的证明输入，不将归档输出证明项导入后续验证环境。界面清空旧预览、审核者及确认，必须重新预览、具名确认和运行真实 Lean / 独立重放，形成独立新 run；旧条目与来源不被覆盖。

## HTTP 与 CLI

写接口沿用精确 Host、同源 Origin、CSRF、192KiB 请求限制及重复字段拒绝。

| 操作 | HTTP |
| --- | --- |
| 当前列表 | `GET /v1/mathlib-knowledge` |
| 检索 | `GET /v1/mathlib-knowledge/search?q=...&include_inactive=false` |
| 预览 | `POST /v1/mathlib-knowledge/prepare` |
| 入库 | `POST /v1/mathlib-knowledge` |
| 详情 / 导出 / 带回 | `GET /v1/mathlib-knowledge/{id}[ /export /request ]` |
| 撤销 / 移除 | `POST /v1/mathlib-knowledge/{id}/withdraw`、`/remove` |

CLI 为 `python3 -m mra mathlib-knowledge-{prepare,admit,search,report,export,request,withdraw,remove}`。prepare/admit/withdraw/remove 使用 `--input 文件`；report/export/request/withdraw/remove 另带条目 ID；search 使用 `--query` 与可选 `--include-inactive`。导出为 JSON 记录与摘要清单；真实证明文件保存在本机归档目录，不打包到导出 JSON。

本轮不增加依赖、不调用模型、不下载权重、不共享、不训练。一般 Mathlib 定义、自动证明搜索、私有证明导入与原创研究能力仍需后续工作。
