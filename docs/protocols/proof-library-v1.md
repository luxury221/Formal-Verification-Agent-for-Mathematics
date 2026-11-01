# 组合成果库与传递复用协议 v1

日期：2026-10-28。设计依据 [ADR041](../adr/0041-transitive-composition-proof-library.md)。本协议使用独立新增的 `artifacts/proof-library/library.sqlite3`（items、requests、runs），旧组合与旧 Mathlib 归档协议不变。

归档来源必须显式提供 `{origin: "composition" | "library-composition", run_id: "mathlib-…"}`。预览保留完整标题、原文、Nat 变量顺序、所有原前提、完整结论与预算；原计划、具名语义对齐、两进程审计及全部文件摘要进入来源快照。只有完整、未取消、未撤销、工件匹配的 COMPLETED / PROVED / E4 可以预览入库；候选与数据重放必须为不同进程。预览仍为 UNKNOWN / E0，不复制、不启动 Lean。

入库正文包含原来源、`plan_fingerprint`、`request_id`、`reviewer`、`confirmed:true`、`rights_confirmed:true`。权利确认只覆盖本地私有存储，不授权共享或训练。请求ID绑定完整正文，人工重发相同请求幂等；正文或预览变化须重新确认。条目ID绑定来源协议和具体运行，已退休的同一来源不得重新激活。

归档复制完整运行工件及 source-record.json，每文件最多16MiB、文件合计64MiB，证明NDJSON最多512KiB；复制前后核对摘要，临时目录完成后原子就位。新条目 `proof-knowledge-…` 与旧条目 `ml-knowledge-…` 分开。每次读取重新检查原来源、复制字节、具名对齐及传递祖先；返回为历史审计，`fresh_attestation:false`，不会生成新的内核认证。

新组合 schema 为 `library-lemma-composition-v1`，目标与证明项字段沿用原受控 Nat 组合，包括整除、gcd、互素、逻辑连接。最多4个显式来源，每个引用必须提供全部量化实参与原前提证明。可联合使用旧 Mathlib 引理与新组合归档；不把原前提缩减为整除片段，不把来源的条件自动变成新目标的假设。

来源绑定 item/version/archive_fingerprint，递归祖先去重并检查版本冲突，最多8层归档深度、64份归档（含自身）。一次递归审计最多128次访问，循环拒绝；临时预算耗尽、worker忙、SQLite锁忙返回可恢复错误，不把健康来源写成损坏。完整图超过审计预算时保持不可用，需要简化来源链；没有无限递归或自动放宽预算。

组合预览为 UNKNOWN / E0。新具名确认绑定完整计划后进入现有唯一worker；候选进程导入只读证明数据，内核逐项检查源类型与应用，随后在独立进程重放完整结果。归档的 Lean 源从不作为候选代码执行。两个检查进程共用1–30秒worker预算，完整性检查另计，不自动重试。只有完整两阶段和仍有效具名对齐可以发布 E4；错误证明、超时、取消或中断不代表命题被反驳。

撤回/移除需 request_id、reviewer、reason、confirmed:true。撤回保留副本与历史，移除删除私有副本并保留退休记录。直接或传递上游撤回、删除、字节/对齐/实现变更会持续停用后继；恢复字节或重发旧请求不恢复旧结论。新账本检测到摘要/身份损坏后额外写持久停用标记，避免只恢复旧数据库行使该记录复活。备份须一起保存库、证明目录、停用标记和原始来源；哈希及本地标记不提供对抗恶意宿主的防回滚保证。

HTTP入口使用原Host、Origin、CSRF和严格JSON门禁：

- GET `/v1/proof-library?q=&include_inactive=true|false`、`/items/{id}`；POST `/preview`、`/admit`、`/items/{id}/withdraw|remove`。
- GET `/v1/library-compositions/catalog`、`/sources/{id}`、`/runs`、`/runs/{id}`、`/runs/{id}/export`；POST `/preview`、`/runs`、`/runs/{id}/cancel|revoke`。
- 页面 `/proof-library` 与 `/library-compositions`；原 `/compositions` 的有效结果提供授权归档入口。来源描述仅返回完整签名/出处，不暴露原始NDJSON。

CLI `python3 -m mra` 提供 `proof-library-preview|admit|list|report|withdraw|remove` 与 `library-compose-catalog|list|preview|submit|work|recover|report|export|cancel|revoke`。预览/入库/提交/撤回正文使用 `--input file.json`；report/export/cancel接运行ID，withdraw/remove接条目ID，work可选 `--run-id`。

页面请求串行，GET只对503进行有限重读；POST不自动重发，保留原正文与ID供人工恢复。姓名或输入变化清除确认；切换记录丢弃迟到预览，读取失败收起旧判断，撤回成功立即收起旧目录直到重新复核。

当前仅为受控Nat手工证明复用。姓名是本地声明，两进程用同一Lean内核实现。新组合尚未接入研究图，原版组合关联仍走原协议。自主规划、一般数学定义、真实模型效果与独立同预算收益不由本协议证明。
