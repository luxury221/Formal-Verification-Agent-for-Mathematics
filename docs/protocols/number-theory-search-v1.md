# 有界数论候选搜索 v1

本协议为 MRA038，配合 [已有数论检查协议](number-theory-v1.md) 使用。入口为 `/number-theory`，HTTP 为同源且需 CSRF 的 `POST /v1/number-theory/search`，CLI 为 `python3 -m mra number-theory-search --input search.json`。GET 不启动搜索；搜索不启动 worker、Lean 或模型，不创建运行。

请求必须且只能包含以下三项。target 与已有完整检查请求相同，但严格不接收 proof。所有变量、原文、假设和结论都保留；标题和原文沿用原编译器的首尾空白规范化，超时规范化为数值。自然语言与形式化是否相符仍需用户审查。

```json
{
  "schema": "mra-number-theory-search-v1",
  "target": {
    "schema": "mathlib-nat-gcd-v1",
    "title": "gcd 自身",
    "original": "对每个自然数 a，gcd(a,a)=a。",
    "variables": ["a"],
    "assumptions": [],
    "goal": {"eq": [{"gcd": [{"var": "a"}, {"var": "a"}]}, {"var": "a"}]},
    "timeout_seconds": 30
  },
  "budget": {"max_depth": 6, "max_expansions": 3000}
}
```

max_depth 为 1–8 整数，max_expansions 为 1–6000 整数；布尔值、非整数、未知字段、重复 JSON 键、任意 Lean 源及不支持的数学定义均拒绝。HTTP/CLI 请求上限 192KiB。原语言约束仍为 6 变量、6 前提、深度 14 和全请求 240 节点；输入预检使用占两节点的占位项，因此最多给目标与前提留 238 节点。占位项从不执行，返回的候选还必须重新通过全请求语法检查。

搜索固定使用 22 条允许引用，按规则特异性排序、迭代加深、活动目标去环。证明高度包括 pair/mp/mpr/ref/hyp 构造，但不计算 Nat 表达式自身高度，后者仍受编译器约束。相同输入通常得到相同候选与计步；墙钟上限、宿主负载或实现变化可能导致不同终点，不作完全可重现承诺。

默认深度 6、3000 步是前端选择，服务端必须显式收到预算。展开目标、匹配规则模式、枚举缺失实参均计一步；多次加深共用该步数预算。子项池按结论、各前提、变量、0/1 的首次出现顺序取前 32 个，截断公开记录；仅用于缺失 Nat 参数的枚举。固定规则可构造对应的中间公式，不作无界项增长。0.75 秒是检查点间的协作式合成预算，输入验证、文件摘要和响应序列化另计，不承诺整个 HTTP 请求硬实时完成。

输出包含 status（CANDIDATE_FOUND / NO_CANDIDATE）、stop_reason、math_verdict=UNKNOWN、evidence_level=E0、target、formal_statement、candidate（完整原检查请求或 null）、references、budget、stats、provenance、unclosed_search_goals、limitations、record_sha256。lean_calls=model_calls=0，requires_preview_and_named_confirmation=true。

stop_reason 为 candidate_found、expansion_limit、wall_time_limit、depth_limit、bounded_space_exhausted 或 proof_language_limit。NO_CANDIDATE 既不能证明命题为假，也不能证明某个诊断目标是必要条件。unclosed_search_goals 最多列 12 项，可能包含某条未成功路线的子目标，不是数学缺口认证。

provenance 包含规则与允许引用表摘要、当前四个实现/运行封存文件摘要、输入指纹及候选指纹。record_sha256 对其余所有输出字段作 canonical JSON SHA256；仅作本地核对。它不替代独立验证，也不构成防篡改签名。生成记录不自动持久化；可从界面导出或保存 CLI JSON。新检查账本只保存实际提交的完整请求和原验证证据，若需追溯搜索过程，应同时保存该导出并核对候选摘要。

界面流程为搜索 → 展开查看 → 导出（可选）→ 带入证明栏 → 核对完整公式 → 具名确认 → 真实 Lean 与独立重放。编辑任何原输入、检查预算或搜索预算，切换示例、历史带回，都停用当前候选；搜索中编辑后恢复也不能采纳旧响应。取消、撤销、损坏及中断继续沿用已有检查生命周期。

能力范围仅为现有 Nat 受控语言的合成，不包括一般 Lean 目标交互、归纳、Or 构造、And 投影、数学归一化、模型自然语言翻译、旧研究图自动执行或顶级期刊问题求解。无新依赖及外部服务调用。
