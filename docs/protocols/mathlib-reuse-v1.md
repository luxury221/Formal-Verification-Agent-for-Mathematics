# 私有 Mathlib 引理实例化 v1

协议 `private-mathlib-reuse-v1`，决策见[ADR026](../adr/0026-explicit-mathlib-lemma-reuse.md)。独立账本 `artifacts/mathlib-reuse/reuse.sqlite3` 保存实例及关联；原私有归档、检查器和研究图保持原协议。

## 同时替换与完整目标

预览严格接受 `item_id, variables, substitution, title, original, timeout_seconds`。item_id 必须指向当前 ACTIVE/PROVED/E4 的真实 Mathlib 私有归档，源和副本均通过冻结来源适配器核验。variables 为新命题中显式全称量化的 Nat 变量（至多6个），substitution 的键必须恰好覆盖原全称变量。

每个替换项只允许现有 `var / nat / add / mul / sub` 有界表达式，变量须在新 variables 中，Nat 字面量范围0–1000000，拒绝布尔值、负数及任意源代码。替换同时执行一次，不继续改写插入的表达式；所有原前提、目标及结构化证明输入一致替换，保留前提顺序和名称，不自动删去数值上为真的前提。与前提名称冲突时拒绝，不默默改名或丢失条件。

以冻结 Mathlib 编译器生成完整新请求、公式与证明输入，保存新原文和预算。预览展示来源原文/完整公式、映射、新原文/完整公式、证明输入、域与变量、前提、版本及预算。它不运行 Lean，返回 UNKNOWN/E0、new_lean_calls=0、proof_imported=false；重复原文不代表已经对齐新的含义。

## 保存、独立验证与结果关联

创建额外接受 `request_id, plan_fingerprint, reviewer, confirmed=true`。重新预览核对完整指纹后，保存 `AWAITING_CHECK` 实例，仍为 UNKNOWN/E0。新条目 ID 为 `ml-reuse-` 加32位十六进制 UUID。时间预算规范化为浮点数，浏览器JSON整数表示往返不会导致持久误失效。

带回接口只返回完整新请求。前端清空旧预览、审核者和确认，需要再次预览、具名确认并运行真实候选与独立内核重放。原实例不会自动晋升；必须明确选择新实验结果，再核对完整请求并具名关联。

结果预览接受 `{run_id}`，只接纳真实当前可用 COMPLETED/PROVED/E4，包含两进程、完整目标、公理与语义确认审计。新结果的完整请求必须与已保存目标完全相同，包括新原文、变量、前提、证明输入、标题和预算；原归档的来源 run 本身不能代替独立新检查。也可选择先前已经完成且满足上述要求的另一独立运行。

关联额外接受 `request_id, run_id, attachment_fingerprint, reviewer, confirmed=true`。预览固定实例 version 与来源指纹；写入采用乐观比较。成功后为 ACTIVE，显示历史 PROVED/E4；关联本身不运行新 Lean，fresh_attestation=false。导入的是原先受控证明输入的显式改写，不导入归档输出证明项、不扩充可信引理环境，也不生成任意 Lean 源代码。

## 生命周期与私有范围

状态为 AWAITING_CHECK、ACTIVE、WITHDRAWN、INVALIDATED、DELETED。引用链可用需要来源归档和关联的新结果都保持有效。源归档撤销/移除、源/副本/新结果工件变化、语义确认撤销、适配器变化会持久 INVALIDATED，恢复文件不复活；停用项不能带回或重新关联，需新建实例。

每次读取复核，HTTP来源撤销或归档移除后定向复核；暂不能复核时标注 `DEFERRED_UNTIL_READ_OR_SWEEP`，不掩盖原操作成功。空闲 worker 每10秒巡检一条。SQLite BUSY/LOCKED 返回暂不可用，不持久记为数学失效。跨账本没有全局原子事务，本机宿主/文件系统/Python/SQLite仍受信。

撤销/移除接受 `request_id, reviewer, reason, confirmed=true`。撤销停用实例；移除清理本实例的来源快照、替换、确认和关联，只留标识、来源条目ID及操作审计。原归档和独立 Mathlib 实验保留其自身记录；来源归档移除也不代表删除其他已明确创建的实例或实验，不承诺取证级擦除。姓名仅为本地声明，资料保持私有、不共享、不训练。

限400实例、2000幂等写请求，每条64事件/2MiB。替换输入限64KiB、表达式深度8/合计80节点；生成目标仍受原深度14/合计240节点/6前提的编译限制，候选+重放共享1–30秒worker预算，环境完整性另计时。所有写请求ID绑定完整操作；同请求重试只返回当前状态，不复活结果。

## HTTP 与 CLI

所有写接口沿用精确Host、同源Origin、CSRF、192KiB上限和重复JSON字段拒绝。

| 操作 | 接口 |
| --- | --- |
| 预览 | `POST /v1/mathlib-reuse/preview` |
| 创建 / 当前列表 | `POST /v1/mathlib-reuse` / `GET /v1/mathlib-reuse` |
| 检索 | `GET /v1/mathlib-reuse/search?q=...&include_inactive=false` |
| 详情 / 导出 | `GET /v1/mathlib-reuse/{id}` / `GET /v1/mathlib-reuse/{id}/export` |
| 带回完整新请求 | `GET /v1/mathlib-reuse/{id}/request` |
| 结果预览 / 关联 | `POST /v1/mathlib-reuse/{id}/preview-attachment` / `POST /v1/mathlib-reuse/{id}/attach` |
| 撤销 / 移除 | `POST /v1/mathlib-reuse/{id}/withdraw` / `POST /v1/mathlib-reuse/{id}/remove` |

检索最多200字符，默认包含待检查和当前可用实例，可包含停用项；前端每批30条。全局搜索可查询当前实例的原文与完整公式，待检查项不会标为已证明。

CLI 为 `python3 -m mra mathlib-reuse-{preview,create,search,report,export,request,preview-attachment,attach,withdraw,remove}`。写命令和preview使用 `--input JSON文件`；report/export/request/preview-attachment/attach/withdraw/remove另带实例ID，search可用 `--query` / `--include-inactive`。

当前仍限定Nat整除已有定义与9条库引用；没有一般数学定义、假设自动解除、自动证明搜索或持出求解率提升证据。模型仍等待配置。
