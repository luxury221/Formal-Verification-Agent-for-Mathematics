# 受控知识实例化 v1

MRA030 首期 / ADR022。来源是当前 ACTIVE / PROVED / E4 的本机知识条目；新的完整目标仍通过既有受限策略重新独立检查。归档证明项没有导入 Lean，也未测量引用是否提高求解率。

## 操作流程

在 `/library` 选中带有最外层全称量词的可用条目，点击“引用并实例化此引理”。填写新命题原文、标题、新全称参数及代入项，选择连续的最外层全称变量前缀。保存并核对实例展示原始完整公式、来源、原文、代入关系、新公式和预算；此时没有 Lean 调用。具名核对并确认后才进入真实检查队列。可取消、核对当前状态、导出记录，并明确将成功的结果存入私有知识库或从来源审查绑定到研究节点。不会自动入库或绑定。

只支持原受信 Nat/Int 算术数据语法，不跨域；1–8 个连续 leading-forall 变量，最多 4 个新全称参数。参数必须实际出现在代入项中，且与源公式所有量词名不同。代入项不能包含自由的源变量、额外字段或可执行源码。保留未实例化的量词、存在量词、蕴含前提和逻辑结构，结果仍受 256 节点 / 24 层深度 / 8 个嵌套量词等原语法限制。自然数减法仍截断为零，负数与取负只属于 Int。

例如来源 `∀ n : Int, n - 1 < n`，声明参数 `x`，代入 `n ↦ x + 2`，新目标为 `∀ x : Int, (x + 2) - 1 < x + 2`。这是明确实例化后的重新证明；不是一般 Mathlib 引理战术。

## HTTP

沿用本机 Host / Origin / CSRF、192 KiB 正文上限、严格 JSON / 精确字段校验。全部写请求须携带会话的 `X-MRA-CSRF`。

| 路由 | 用途 |
| --- | --- |
| `POST /v1/knowledge-instances/preview` | 纯离线实例化预览 |
| `GET /v1/knowledge-instances` | 最近最多 30 个实例及其最新运行 |
| `POST /v1/knowledge-instances` | 保存不可变草稿，不启动检查 |
| `GET /v1/knowledge-instances/{id}` | 审核来源、返回草稿及最近最多 30 次运行 |
| `POST /v1/knowledge-instances/{id}/run-plan` | 核对本实例完整目标及预算 |
| `POST /v1/knowledge-instances/{id}/runs` | 具名确认并提交队列 |
| `GET /v1/instance-runs/{run_id}` | 审核并读取单次运行 |
| `POST /v1/instance-runs/{run_id}/cancel` | 正文须为 `{}`；撤销本次生成的来源 |

预览正文（精确字段）：

```json
{
  "knowledge_id": "knowledge-...",
  "title": "整数平移后的递减引理",
  "statement": "对任意整数 x，(x + 2) - 1 < x + 2。",
  "parameters": ["x"],
  "substitutions": [{"variable":"n","term":{"op":"add","left":{"op":"var","name":"x"},"right":{"op":"num","value":2}}}]
}
```

保存正文为 `{request_id, input, preview_fingerprint}`，`input` 是上述预览输入；同请求号同内容幂等，异内容拒绝。本机最多 100 个草稿，首期没有草稿删除/自动清理。

检查计划输入为：

```json
{
  "branch_id":"main",
  "items":[{"node_id":"instance","plan":{"direction":"prove","strategy":"omega","witnesses":[]}}],
  "budget":{"max_checks":1,"worker_seconds":30,"wall_seconds":120}
}
```

允许 `omega` / `simp` / `decide`；只检查一个完整目标，固定一次检查 / 30 worker 秒，墙钟 1–600 秒。候选与独立重放共享 30 秒配额；配额预留后不退回。墙钟包含环境扫描，扫描不能即时抢占，但到期不会发布新的结果。

运行提交正文为 `{request_id, input, plan_fingerprint, reviewer, confirmed_nodes:["instance"], confirmed:true}`。`input` 为检查计划输入；来源、实现或任何计划内容变化需要重新准备和确认。每个实例最多同时一项活动运行，工作队列复用公共 worker 锁。运行采用冻结的 `controlled-research-run-v1`，计划另附 `usage_schema: verified-knowledge-instance-v1`、完整引用、新代入、实例与适配器指纹。

## 账本与失效

独立第七账本为 `artifacts/knowledge-instances/instances.sqlite3`，含 `instances` 和 `runs` 两表；不隐式写入真实研究项目表。复用原 `ResearchRunStore` / `ControlledReview`、真实 Lean、独立重放和原发布门禁，冻结文件保持不变。

实例状态 `DRAFT` 只代表不可变记录仍可使用，不等于已经证明；运行状态和数学判断分开。报告的数学判断来自最新运行；旧运行保留在 `runs`，最新取消后不会自动回退到更早的成功记录。策略失败为 UNKNOWN，不能成为 REFUTED。没有作者步骤时作者证明判断仍 UNKNOWN。

引用绑定知识条目版本、完整内容及其中的源/归档摘要、知识实现摘要、实例化实现摘要和原验证协议。来源撤销、移除、篡改或协议变化持久使实例 INVALIDATED，撤销相关运行生成的审查来源；文件恢复不复活。中断恢复只标记 INTERRUPTED、保留工件与配额，不从残留 PROVED 工件发布结果；需要重新确认新运行。

运行中的引用审计只读检查相关条目及其依赖闭包，复用原来源证据与归档检查规则，核对后再次读取账本内容；不会为每次轮询取得知识库全量写锁。实际损坏使用原账本事件/级联规则持久停用。临时 SQLITE_BUSY/LOCKED 返回存储不可用，草稿不会因此永久失效；检查过程遇到存储错误仍拒绝发布，需明确重新执行。

实例读取/准备/提交、运行前中及发布前审查来源；后台每轮巡检一个实例，公共知识 API/CLI 操作也执行核对，使已归档实例的知识副本随来源失效。失效标记和跨账本撤销不是全局事务，中断后下次巡检继续清理；直接调用原 Python 存储类不会额外触发全局巡检。来源移除清理知识账本的内容/证明副本；独立实例记录中的引用快照及原审查仍保留，不是全项目资料删除。首期拒绝以本模块产生的实例再做递归实例化。

## CLI

`python3 -m mra` 后接：

```text
knowledge-instance-preview --input preview.json
knowledge-instance-create --input create.json
knowledge-instance-list
knowledge-instance-report INSTANCE_ID
knowledge-instance-plan INSTANCE_ID --input plan.json
knowledge-instance-submit INSTANCE_ID --input submit.json
knowledge-instance-work [--run-id RUN_ID]
knowledge-instance-run-report RUN_ID
knowledge-instance-cancel RUN_ID
knowledge-instance-recover
```

`--input` 是同 HTTP 的 JSON 正文；CLI 提交不自动执行 worker。模型调用与费用均为零。真实模型、一般定义、递归实例依赖、一般证明项调用及持出数学问题的收益评测独立后续实施。
