# 私有知识库 v1

日期：2026-10-25。设计：[ADR-017](../adr/0017-private-provenance-knowledge.md)。入口：`/library`。

## 使用

在已完成的单定理审查结果中点“存入私有知识库”，或在知识页展开入库区域选择审查。先查看可入库条目，逐项核对完整公式、全部前提、原文版本/行号与步骤引用。填写本地姓名，确认条目范围及有权在本机私有保存，再保存。准备与入库不运行模型或 Lean。

首期只接受该审查中真实通过的 PROVED/E4 义务。命题成立而原证明有缺口时，只接纳真实已证明部分，不能接纳已反驳步骤；全部为反驳或未知时不接纳。各义务完整条件陈述保持不变，不能去掉前提后作为无条件结论使用。

图谱与条目列表均读取真实账本；箭头表示原证明步骤引用，不是全部 Lean 常量依赖。可按公式、原文片段、标题、来源 ID 检索。默认只显示可用条目，勾选“包含已停用”查看撤销与失效；点击条目查看来源、证据清单、环境快照、具名确认和事件。图谱最多显示当前匹配的前30项，列表显示全部匹配，当前私有账本最多500条。

## 状态、隐私与删除

| 状态 | 可出现在默认可信检索中 | 含义 |
| --- | --- | --- |
| ACTIVE | 是 | 来源审查与证明副本当前一致；历史E4，不是新认证 |
| WITHDRAWN | 否 | 用户主动撤销该条目的可用性 |
| INVALIDATED | 否 | 来源确认、协议、源文件、证明副本或步骤依赖失效 |
| DELETED | 否 | 知识内容与归档已移除，保留最小标识和事件 |

检索、详情和入库返回前会复核来源与归档；没有订阅推送，已打开页面需刷新。失效是持久的，恢复文件不会自动恢复信任。重建需新的来源审查及新的入库确认。

撤销作者步骤向后传播，命题的独立证明不依赖作者步骤。移除会同时清理引用该步骤的知识子树及归档；原始论文审查与其工件仍在原位置，接口明确 `source_review_deleted=false`。删除先提交不可用墓碑再清理副本，中断后重复移除完成清理；不承诺磁盘安全擦除、备份清除或原始审查删除。

所有条目 `visibility=private-local`，`sharing_allowed=false`、`training_allowed=false`，本次确认仅声明本地保存权利，不授予训练或分享许可。姓名为本地声明，不是身份认证。没有任何自动上传、训练、Mathlib提交或证明生成器中的自动引理调用。

## 数据与证据

权威账本：`artifacts/knowledge/knowledge.sqlite3`；逐条证明副本：`artifacts/knowledge/proofs/knowledge-…/`。每项保存固定来源ID/key、原文版本、行号、完整公式树/语法指纹、前提、公理、审核记录、工具链/依赖内容快照、回执与工件摘要。

同一来源/步骤只入库一次，请求ID幂等；不同出处的同式证明保留独立版本与出处，不自动合并或替代失效条目。语法指纹不代表数学等价。复制归档先核验源与读取字节，事务失败无可见条目；显式重试只可复用内容相同的孤立归档，不从它恢复授权。备份应停写并整体保存知识账本、证明副本和原始来源账本/工件。导出JSON是报告，不是可独立恢复的认证。

## HTTP 和 CLI

HTTP保留精确Host、同源Origin、CSRF、CSP/no-store和有界正文。客户端不能提交公式、依赖或自报证据等级来入库。

| 路由 | 参数 / 行为 |
| --- | --- |
| GET `/v1/knowledge/search?q=…&include_inactive=false` | 查询≤200字符，返回带来源的匹配项；拒绝未知/重复参数 |
| POST `/v1/knowledge/prepare` | `{review_id}`，显示可入库条目及计划指纹 |
| POST `/v1/knowledge` | request_id、review_id、plan_fingerprint、reviewer、confirmed=true、rights_confirmed=true |
| GET `/v1/knowledge/{id}` | 当前状态、完整陈述/来源/依赖/证据/事件 |
| POST `/v1/knowledge/{id}/withdraw` | reviewer、reason、confirmed=true，撤销并传播 |
| POST `/v1/knowledge/{id}/remove` | 同上，清除知识子树副本；原审查保留 |

```bash
python3 -m mra knowledge-plan '<paper-id>'
python3 -m mra knowledge-admit '<paper-id>' --plan-fingerprint '<显示的指纹>' --reviewer '<姓名>' --request-id '<本次稳定ID>' --rights-confirmed
python3 -m mra knowledge-search --query 'n +'
python3 -m mra knowledge-search --include-inactive
python3 -m mra knowledge-report '<knowledge-id>'
python3 -m mra knowledge-withdraw '<knowledge-id>' --reviewer '<姓名>' --reason '<原因>' --confirmed
python3 -m mra knowledge-remove '<knowledge-id>' --reviewer '<姓名>' --reason '<原因>' --confirmed
```

当前是单定理审查的历史证据索引和可审计归档。猜想/固定目标的直接入库、一般Lean包、跨论文引理依赖、全局文档当前版本注册及自动检索辅助证明仍待后续。

## 类型域扩展（2026-10-26）

支持当前Nat/Int单定理审查中的真实PROVED/E4义务。条目content.domain显式为Nat或Int；proposition_fingerprint绑定 `{domain,formula}`，同一AST不同数学域不合并。源协议、确认、证明摘要和重放要求不变。原实现SHA改变的历史来源保持停用，重新建立需新的审查与具名入库，不自动迁移为新版认证。
