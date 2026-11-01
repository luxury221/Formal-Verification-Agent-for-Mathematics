# mathlib-nat-divisibility-v1

2026-10-27。独立的人工 Mathlib 引用实验，入口 `/mathlib`。不是原 Nat/Int 算术协议的隐式扩展。模型保持未配置。

## 陈述与证明

请求必须且只能包含 `schema, title, original, variables, assumptions, goal, proof, timeout_seconds`。schema 为 `mathlib-nat-divisibility-v1`。变量是最多6个不重复的20字符以内小写ASCII名称，全部显式为Nat；宿主编译为`mra_v0`等以防关键字和名称捕获。假设最多6条，每条包含`name, formula`，生成完整外层全称量词与全部前提蕴含。

表达式：`{"var":"a"}`、`{"nat":0}`、`{"add":[expr,expr]}`、`mul`、`sub`。整数常量0到1000000；不接受布尔数、负数、分数、未知变量、除法或任意Lean。自然数减法截断于0。公式：二元`dvd / eq / le`作用于表达式，二元`and / or / iff`作用于公式。整除直接使用封存库中`Dvd Nat`实例，不展开成原算术DSL或替换目标。

证明：`{"hyp":"hab"}`引用前提；`{"ref":"Nat.dvd_trans","terms":[a,b,c],"proofs":[hab,hbc]}`显式应用白名单定理，包括通常隐式的所有Nat实参。`pair`提供两项合取证明；`mp`、`mpr`分别用等价证明和对应方向的前提证明生成结果。没有候选源执行、动态import、任意策略或猜测省略参数。陈述和证明总计最多240节点、深度14。

目录API返回九条精确类型、名称、出处、实参数量及四份自有功能样例：整除传递与加法组合、Mathlib双向整除等价式、截断减法边界、遗漏前提失败对照。库定义与引理自身来自固定Lean/Mathlib，不代表研究新发现。失败对照只检查提交证明被拒绝，不签发该命题的REFUTED。

## 确认、运行和证据

preview规范化输入，展示原文、完整公式、名称映射、证明项、引用与共享worker预算，绑定源码/目录/政策/模板/原运行快照。不执行Lean，数学判断始终UNKNOWN/E0。

submit只接受`request_id, request, plan_fingerprint, reviewer, confirmed`，要求非空本地具名核对者和显式true。当前预览必须与指纹相同；输入、目录、实现或预算变化需新确认。同request_id仅在相同计划和核对者下幂等，冲突拒绝。

队列与原任务共用flock；候选和独立重放共享1–30秒worker预算，环境完整性核对独立计时。候选编译后检查确切目标、定理种类及闭包公理；只导出新增声明和其表达式。重放进程不导入候选模块，引用的库常量从同一封存基线解析，用内核重放新增声明并检查确切目标桥接。**独立性覆盖候选证明，不表示重建或独立验证整个Lean/Mathlib实现。** 唯一允许公理`propext, Classical.choice, Quot.sound`；`sorryAx, Lean.ofReduceBool`及本地公理禁止。

只有两个真实进程及具名对齐有效才形成历史PROVED/E4。类型错误REJECTED、超时、取消、执行故障、策略阻塞或中断均UNKNOWN/E0；运行状态与数学判断单列。工件、事件、候选/重放审计及源码清单保存在`artifacts/mathlib-lab/`的第八个账本及按run ID组织的目录。历史查看核对记录，不产生新的环境认证。

取消队列立即终止；执行中由进程轮询终止并在发布前再检查。撤销对齐立即停用结论。实现/目录/封存指纹变化或已记录工件损坏永久停用，不因文件恢复而自动复活。启动取得worker锁后，把残余RUNNING标INTERRUPTED；从残留工件恢复证明标签被禁止。用户必须带回请求、重新预览具名确认并真实运行。

目录最多200次实验。记录可JSON导出；不自动进入旧知识库或研究图，它们的既有域没有整除语法。此适配器后续另行版本化，不能把整除义务伪装成旧算术式。

## 接口

| 方法 | 路由 | 内容 |
| --- | --- | --- |
| GET | `/v1/mathlib/catalog` | 定义、九条引用、来源/版本、四份示例 |
| POST | `/v1/mathlib/preview` | 原请求；只预览 |
| GET / POST | `/v1/mathlib/runs` | 历史 / 具名提交 |
| GET | `/v1/mathlib/runs/{id}` | 工件摘要与永久失效检查 |
| POST | `/v1/mathlib/runs/{id}/cancel` | 空对象 |
| POST | `/v1/mathlib/runs/{id}/revoke` | reviewer、reason、confirmed |

均沿用本机Host/Origin/CSRF/严格JSON/192KiB限制。CLI对应`mathlib-catalog / preview / submit / list / report / work / cancel / revoke / recover`；输入操作用`--input`，work可用`--run-id`。不配置模型、不下载新依赖或更改旧环境封存。
