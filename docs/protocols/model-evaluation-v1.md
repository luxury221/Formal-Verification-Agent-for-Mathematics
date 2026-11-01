# 固定预算模型策略评测 v1

此入口位于 WSL 项目根目录，使用与猜想工作台相同的 `config/model.local.json` 和密钥环境变量。

```bash
# 先查看计划：不调用模型或 Lean，也不要求配置模型
python3 -m mra model-benchmark --plan

# 配置模型后运行回归组的六条题目
python3 -m mra model-benchmark

# 开发组；预留组及全部组须显式选择
python3 -m mra model-benchmark --split development
python3 -m mra model-benchmark --split all --include-reserved

# 读取返回目录中的历史报告并核对工件摘要
python3 -m mra model-benchmark-report artifacts/model-evaluations/nat-plans-<id>
```

未配置模型时，实际运行返回 MODEL_NOT_CONFIGURED，不创建评测运行，也不生成模型分数。计划仅展示选定原文、作者形式化目标、策略、固定预算和协议指纹，排除参考证明、答案标签与反例见证。训练组为空，不能评测。

每题只有一次生成机会与至多一次 Lean 检查，无失败重试。只接受 `nat-proof-output-v1` 的绑定目标 JSON，以及 omega/simp/decide/counterexample 四策略。`plan: null` 是拒绝作答，保持 UNKNOWN。得到有效计划后由程序生成 Lean 源，经真实候选检查、独立进程重放和语义记录门禁；模型自报成功无效。

报告将运行状态与数学判断分开。model_solve_rate 的分母是整组选定题数，包括超时、错误、无方案和检查失败。只有完整结束的真实模型运行才报告该分数。fixture 接口验收仅有 simulation_checked_rate，model_solve_rate 为 null。alignment_accuracy 与 end_to_end_solve_rate 始终为 null，因为这里提供了作者形式化目标。

model_request_attempts 统计实际调用适配器的尝试；model_calls 统计具有匹配请求、正确生产者身份及用量的完整响应。网络失败并不证明供应商未处理请求，usage_complete=false 时 known_input_tokens/known_output_tokens 只是可确认部分，费用未知为 null。响应模型名称来自传输信封，版本仍是操作者配置的部署/版本声明。

Ctrl+C 会清理当前模型/Lean 子进程并结束本轮；部分结果保留，整轮求解率为 null。崩溃后的 RUNNING 文件是中断的历史快照，不能自动恢复证据或当成完整评测。重新运行产生新目录和新请求。配置在题目间漂移会结束本轮；不自动切换模型。网页和 CLI 共用 worker 锁，被占用时应在当前任务结束后重跑。

工件位于 `artifacts/model-evaluations/nat-plans-<id>/`，包括 plan.json、report.json、逐题 request.json/response.json/result.json。真实检查工件仍存于 `artifacts/runs/`。读取历史报告会核对这些文件的摘要、逐题记录与聚合结果；缺失或改变时仅返回 ARTIFACTS_CHANGED，求解率清空。不是持久签名或当前环境新认证。

本数据集只有18条作者可见例题，预留组不能作为独立盲测。数字预算与作者参考相同，但可用证明策略不同。本轮新增的是可运行的评测功能；真实模型分数需配置后实测。
