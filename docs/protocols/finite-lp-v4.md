# 有限维 ℓp 与实幂证明语言 v4

入口沿用 `/proof`、Studio 后端 `proof`、`/v1/proof-research` 和 `ops/scripts/proof_research.py`。每个目标请求必须显式指定 `schema: proof-language-v4`。catalog 默认 v4，并同时列出 v1/v2/v3/v4；一个图只能有一个版本，旧图不迁移或降级。

## 类型与数学表达

`{"finite_vector": 2}` 作为类型表示 `Fin 2 → ℝ`。维数必须为1–8的整数；有限向量可作为函数输入或输出，逐点支持零、加法、取负和实数数乘。坐标表达式的 index 必须为0至n−1。

```json
{
  "lp_norm": {
    "exponent": {"real": [3, 2]},
    "vector": {"finite_vector": [{"real": [1, 1]}, {"real": [4, 1]}]}
  }
}
```

上述只是一项表达式，含义为 `(∑i |uᵢ|^(3/2))^(1/(3/2))`；解析或预览不授予证据。

| 表达式 | JSON参数 | 含义 |
| --- | --- | --- |
| `finite_vector` | 非空实数表达式数组，最多8项 | 显式坐标向量 |
| `coordinate` | `vector`, `index` | 指定坐标 |
| `dot` | 同维向量组成的两项数组 | `∑i uᵢvᵢ` |
| `lp_sum` | `exponent`, `vector` | `∑i |uᵢ|^p` |
| `lp_norm` | `exponent`, `vector` | `(∑i |uᵢ|^p)^(1/p)` |
| `real_rpow` | 两项实数表达式数组 | Mathlib `Real.rpow` 的实幂 |

`lp_norm` 是显式表达式名称，不会验证或暗加 p≥1。Mathlib 实幂及除法是总定义，负指数、零分母等不能借表达式可编译便解释为通常分析定理的适用范围。幂律要求显式 p>0；p≥1 时才是通常的有限维 ℓp 范数。当前规则没有证明一般 Hölder 不等式或全部范数公理。

有限向量不得用旧 `norm`/`inner` 及固定内积范数规则，避免误用 Pi 的默认范数。点积本轮也不隐式转换为内积空间实例；抽象实内积空间仍使用旧类型和规则。

## 固定规则与证明构造

沿用 `{"rule":{"name":...,"terms":[...],"proofs":[...]}}`，各参数数量严格匹配。

| 规则 | terms | proofs | 含义 |
| --- | --- | --- | --- |
| `lp_norm_rpow` | p,u | p>0 的证明 | `lp_norm(p,u)^p = lp_sum(p,u)` |
| `lp_norm_nonneg` | p,u | 空 | 显式 ℓp 表达式非负 |
| `lp_sum_nonneg` | p,u | 空 | 显式幂和非负 |
| `real_rpow_mul` | x,a,b | 0≤x 的证明 | `(x^a)^b = x^(a*b)`，返回上游定理的对称方向 |
| `real_rpow_nat` | x,n | 空 | 实指数自然数幂等于整数幂；n须显式非负整数实数字面量 |

新增证明构造：

- `rational_power`：字段 `base`、`root`、`numerator`、`denominator`、`nonnegative`、`root_power`；分子0–8、分母1–8，需要证明 root≥0 和 root^denominator=base，推出 base^(numerator/denominator)=root^numerator。
- `coordinate_eq`：字段 `left`、`right`、`index`、`proof`；从同维向量等式取坐标等式。
- `finite_compute`：字段 `equalities`；在固定有限求和/实自然数幂/逐点数乘化简集上使用 `norm_num`。等式项仍为 `proof` 与显式 `reverse`。
- `finite_simplify`：字段 `equalities`、`proof`；在同一固定化简集上对明确证明使用 `simpa`。

不接受用户 Lean 源、任意战术、imports、公理或任意定理名字。已有局部引理、逻辑证人和分情况仍受作用域及类型门禁约束。

## 示例与真实结果

catalog 新增三个示例：

| ID | 形式化范围 | 平台真实工件 |
| --- | --- | --- |
| `finite-lp-holder-witness` | 固定 p=3,q=3/2,r=3；存在 g,h:ℝ²→ℝ² 和 x，取常值(1,2)/(1,4)、x=0，适用条件成立、损失零、两向量不线性相关 | `platform-20261101-02/holder-witness-report.json`，PROVED/E4 |
| `finite-lp-norm-power` | 任意三维实向量u、任意正实数p的显式幂律 | `platform-20261101-01/norm-power-report.json`，PROVED/E4 |
| `finite-lp-rational-power` | 非负根证书给出的 `4^(3/2)=8` | `platform-20261101-01/rational-power-report.json`，PROVED/E4 |

路径均相对 `artifacts/holder-equality-20261030-01/`。正式图会有完整目标、具名语义核对、真实候选及不同进程独立数据回放。独立进程使用同一固定 Lean 内核实现，不等于第二种证明器。

初次 `platform-20261101-01/result.json` 为 `all_passed=false`，其 Hölder 候选被拒记录保留。错误声称(1,2)/(1,4)线性相关的负对照被拒，为 UNKNOWN/E0；不能当作 Lean 已证明该命题的否定。后续 `platform-20261101-02` 才取得限定存在见证的正式证据。

该存在见证已按 `counterexample_specialization` 具名绑定至原文 `Thmprop2`，保留完整原题、定义(3)、线性相关定义、p/q/r条件和限定特例关系。本轮链接复核为 `CURRENT`，finding为 `CHECKED_COUNTEREXAMPLE_SPECIALIZATION`，原命题仍为 `UNKNOWN/E0`；不授予原命题或整篇文章 `REFUTED`。文章revision42有12条链接，9项当前有效义务、14项待处理、300文本块未分类，全文未完成。回收与幂等工件见 `article-binding-20261101/result.json`（相对同一证据根目录）；12图复核直接调用原provider并离线聚合，不是HTTP性能通过或新增Lean检查。不能将历史技术试验 `trial-01` 追溯晋升。

## 执行、身份与预算

预览完整目标和条件，保存图，再具名确认执行。v4图前缀 `proof4flow-*`，尝试 `proof4attempt-*`；目录 `artifacts/proof-finite-lp-research`，子工件为 `proof-finite-lp-language-private`，独立账本身份 `proof_finite_lp_identity`。

候选与回放共享1–40秒，完整性核对另计；输入最多131072字节、2400节点、64层，有限维数最多8。导出最多524288字节、12000行。超时、真实拒绝、完整性漂移和取消不得发布成功结论；无自动数学重试。旧v1/v2/v3证据使用其原执行器，v4与外部智能体工具调用不能改写旧身份。

真实模型尚未配置；这些是人工结构化候选的数学验证，不是模型求解率、自动全文形式化或高难研究问题的普遍求解能力。决策见 [ADR 0094](../adr/0094-finite-lp-proof-language-v4.md)。
