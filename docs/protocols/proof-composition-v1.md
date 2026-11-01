# 分析证明组合 v1

入口：<http://127.0.0.1:8765/proof>。Studio 可选“分析证明组合”；论文任务继续通过原有研究结果回收入口关联。

## 操作

1. 在 Studio 保存完整候选，选择后端 `proof`；或在分析证明组合页面选择研究项目、查看已知示例。
2. target 使用 `schema: proof-language-v1`，其余类型、表达式、变量、完整 assumptions 与目标沿用 calculus。不得删掉原题条件，也不能把待证依赖当作已知假设。
3. `proof.term` 中组合局部引理、定理应用、显式改写和固定算术方法。详见 `mra/proof_examples.py` 的公式25与线性推导示例。
4. 预览完整 Lean 陈述、全部原条件及证明步骤。预览和保存都不执行数学检查。
5. 具名确认后提交一次真实检查。失败诊断只表示这个候选没有通过，不能推出原命题为假。
6. 当前 E4 结果可经研究任务回收到精确原文义务。回收前后的全文状态和未完成义务仍需单独阅读。

## 新证明节点

`have` 包含 `name, formula, proof, then`：`proof` 必须先证明 `formula`，只有 `then` 能引用新增名字。它不是新增全局假设。

`rewrite` 包含 `proof, equalities`。每个改写项为 `{lemma, reverse}` 或 `{proof, reverse}`；必须显式使用布尔方向，最多8项。允许的固定定理在 catalog 列出。自定义等式也必须由当前作用域中的证明生成。

`arithmetic` 包含 `method, proofs`：方法只能是 `linarith`、`nlinarith` 或 `ring`，最多8个显式事实。`ring` 不接收额外事实。缺少必要前提仍可能失败；接口不会自动补条件。

原受控证明规则仍可嵌套，新增规则的确切参数类型和数目以 catalog 为准。不存在任意定理字符串、Lean 源码或自由战术输入。

## API 与 CLI

路径前缀 `/v1/proof-research`，请求和回执结构沿用已有微积分研究图：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/catalog` | 新版本语言、规则、示例 |
| POST | `/preview` | 完整节点图预览 |
| POST | 前缀本身 | 按精确指纹保存，尚未执行 |
| GET | `/projects/PROJECT/BRANCH` | 该分支的新版本任务 |
| GET | `/GRAPH` | 核对现有记录与工件 |
| POST | `/GRAPH/node-preview` | 核对完整节点和当前前置身份 |
| POST | `/GRAPH/submit` | 具名确认后排队 |
| POST | `/GRAPH/cancel` | 取消确切尝试 |
| POST | `/GRAPH/withdraw` | 撤销并传播失效 |

CLI：在项目根执行 `PYTHONPATH=. python3 ops/scripts/proof_research.py --help`。沿用 catalog、preview/create、node-preview/submit、report、list、cancel、withdraw、work、recover 命令及参数。

graph 为 `proofflow-32hex`，attempt 为 `proofattempt-32hex`；子检查仍为 `mathlib-32hex`，位于各图的 `proof-language-private` 账本。图最多8节点、16次检查、480预留worker秒，单次1–40秒候选与独立回放共享。没有自动数学重试；恢复只将遗留执行降为中断，取消/损坏后不会悄悄重跑或恢复已证状态。

本版本与旧 calculus 账本相互隔离。新版本不能读取旧账本为自己的结果，旧版本也不自动接纳新证明构造。原文、完整目标或证据变化后须重新预览与确认。
