# Lean 研究引用类型读取 v1

入口：`/studio` → 选研究步骤 → 搜索 Mathlib → 加入引用 → **读取完整类型**。

`POST /v1/studio/{study_id}/inspect-premise`

```json
{
  "request_id": "a-new-local-request-id",
  "expected_revision": 3,
  "citation_id": "citation-0123456789abcdef0123456789abcdef"
}
```

沿用同源/CSRF 检查。要求当前修订、当前计划且未撤回的引用，无正在执行的模型操作。请求从现有索引重新核对候选；不接收任意 Lean、模块或证明代码。该动作创建独立的元数据工件，不修改研究原题、修订号或数学结论。同一缓存身份/请求号不重复启动；已有成功/歧义/未找到结果直接复用。启动时最多等待 checker 锁两秒，以避开空闲队列轮询；仍被占用则返回既有 busy 诊断，不建立无限队列，不重试 Lean 执行。

笔记本报告 `citation_states[].metadata` 提供状态，网页每两秒只读轮询运行中的任务。READY、AMBIGUOUS、NOT_FOUND 分别表示一个、多个、零个环境名称命中。其他状态包括 NOT_READ、RUNNING、FAILED、TIMEOUT、INTERRUPTED、UNAVAILABLE。中断通过真实锁所有权判断，不依赖 PID 复用或过期时间。保留错误与尝试目录；不自动重试。至多三次显式尝试，超过预算需查看工件；不存在自动归档/删除功能。

每个命中返回：

- `name/module/kind`：环境里的完整限定名、定义模块、声明种类；词法索引的 lemma 对应环境 theorem。
- `universe_parameters`：声明的宇宙参数。
- `type`：易读的完整量化类型；`explicit_type`：同时显示隐含应用参数的类型。二者不等于定义全部展开。
- `binders`：外层 forall 的名字、参数类型及 explicit/implicit/strict_implicit/instance；名字由 Lean pretty printer 输出，可能含 `inst✝`。
- `conclusion`：在全部 binder 局部上下文下打印的结论。

完整 `type` 是自包含的量化陈述；单个 binder/type 与 conclusion 应连同上下文阅读。模块和后缀唯一筛选不等于 Lean 源码行位置映射；歧义结果没有自动选择。源码解析器未覆盖的复杂名称可能 NOT_FOUND，缺少 `.olean` 则 FAILED，均不表示定理为假。

有效当前引用的报告进入 `retrieved_premises.declaration_metadata`，带 `resolution` 和研究用途限制。失效、撤回及旧计划元数据不进入可用上下文。上下文超预算仍由原模型预算门禁拒绝，不静默丢失原题条件。真实模型仍需用户稍后本地配置。

缓存位置 `artifacts/premise-metadata/metadata-{identity}/`；`record.json` 原子封装记录，`attempt-*` 保存模板渲染、请求 JSON、stdout/stderr、supervisor 日志和历史结果。代码/模板变化会形成新缓存身份；旧工件仍保留。状态读取验证缓存工件与封存身份，实际执行前后才完整扫描执行环境；缓存不认证当前环境二进制。

执行复用 Lean 4.35.0、固定 Mathlib commit `56a005855ea17e0b0958f73ec801ae140b0a1679`（Apache-2.0）。无新第三方依赖。40 秒限制针对 Lean worker，不含前后完整环境核验。metadata 的数学状态始终 UNKNOWN/E0，不能替代当前研究目标的类型对齐、证明、公理审核或重放。
