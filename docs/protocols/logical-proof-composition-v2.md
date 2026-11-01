# 逻辑证明组合 v2

入口仍是 `/proof`、Studio后端 `proof`、API `/v1/proof-research`，CLI仍为 `ops/scripts/proof_research.py`。选择v2示例或在完整请求中明确使用 `schema: proof-language-v2`。历史v1请求和图继续使用原验证器；保存、预览和模型文字都不等于证明。

## 证明构造

| 构造 | 字段 | 语义 |
| --- | --- | --- |
| `iff`，公式 | 两个公式组成的数组 | 真正的Lean双向蕴含；不是只核查一个方向 |
| `steps`，证明 | `lemmas, then` | 每项`name,formula,proof`先证明后加入后续作用域，最多8项 |
| `exists_intro` | `witness, proof` | 给出有类型的证人，并证明代入后的结论 |
| `exists_elim` | `proof,name,type,hypothesis,formula,then` | 使用已证明存在项；新变量与性质只在then中有效 |
| `or_cases` | `proof,left,right` | 两支分别为`name,proof`，都必须完成相同目标 |
| `by_cases` | `name,formula,yes,no` | 两支分别得到该公式及其否定，不能共享临时证据 |
| `normalize` | `equalities` | 只用明确列出的固定定理或当前已证明事实化简 |

`rewrite`沿用显式定理/证明和布尔方向，v2新增固定数乘/零向量等改写。`have`、原固定规则及代数方法仍可使用。所有定理的参数数目、类型和允许名称见catalog；不接受任意Lean、imports、自由战术或公理。

存在消去所声明的类型和性质必须与实际证明对应，这一点由Lean检查。分情况是逻辑证明操作，不是额外加入原题假设；完整顶层目标和全部原条件仍保留。

## 版本及恢复

catalog返回 `versioned-proof-research-v1`，列出两种supported_languages、默认v2与两版示例。保存后的具体图仍报告自己固定的 `proof-project-research-v1` 或 `proof-project-research-v2`，不能把路由catalog当数学证据。

同一图不能混用版本。v1图前缀`proofflow-*`，v2为`proof2flow-*`；v2尝试为`proof2attempt-*`。后端根据严格标识读取原账本，不迁移或自动重做。创建请求编号同时绑定请求和版本；发布失败后只允许恢复完全相同请求。

单图仍最多8节点、16次检查、480预留worker秒；单次1–40秒由候选和回放共享，完整性核对另计。v2输入131072字节、2400节点、64层，显式事实与局部引理仍有数量限制。没有自动数学重试，旧证据失效/损坏/撤销语义继续适用。

## 论文语义

CS等号示例使用论文公式2的非平凡双系数定义，涵盖任一向量为零；不能无条件用`∃r,v=r•u`替代。命题1示例同时陈述非负性和完整零点充要条件，集合X、ℓ均展开定义。标准欧氏空间由实内积空间实例覆盖。

这些是当前完整局部义务的形式化。源文对应仍需具名本地核对，不能由Lean单独保证翻译忠实，也不能据此称全文作者推理已验证。实际验收见 `artifacts/proof-logic-20261030-01/REPORT.md`。
