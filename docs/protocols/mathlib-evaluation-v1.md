# Mathlib 固定预算功能评测 v1

协议 `mathlib-authored-evaluation-v1`，决策见[ADR027](../adr/0027-authored-mathlib-diagnostics.md)。目标是核对受控检查器的可用范围与失败诊断，不测模型求解或原创性。

## 数据与计划

`evals/mathlib-diagnostics-v1/cases.json` 为项目自编数据：8道题，开发组2题、回归组6题，题目族唯一且不跨组。包含自反性、整除组合、平移后的截断减法、真命题的错误证明项、遗漏前提、错误逆命题、未开放gcd和集合定义。全部题目已由作者查看并在仓库可见，非独立盲测或未经调试的持出集；不用于训练。

计划固定完整原文、Nat变量、全部前提、目标、证明输入、题目/实现/原检查协议/运行快照摘要和预算。未知定义保留原输入与阻塞理由，完整公式为空；不替换为其他命题。作者预期运行状态仅用于功能对照，不授予数学结论。

每题固定30秒候选+重放共享worker预算、最多一次候选进程和一次独立重放、不重试；全部8题worker总上限240秒。环境校验、数据库和进程调度另计，整轮可能比240秒长，不提供整轮硬墙钟截止保证。无模型请求、搜索或自动修复。

## 执行与历史

先运行 `python3 -m mra mathlib-benchmark --plan --split all` 查看完整计划。核对全部输入后使用返回的指纹、实际核对者姓名和确认标记：

```bash
python3 -m mra mathlib-benchmark --split all \
  --plan-fingerprint <计划指纹> --reviewer '核对者' --confirmed
python3 -m mra mathlib-benchmark-report <ml-bench-…>
```

split只能为all/development/regression；过期指纹、空姓名或无确认被拒绝。姓名是本地作者对齐声明，非身份认证或独立专家审查。每轮使用新UUID，不隐式续跑旧轮次。

整轮持有原全局worker锁，其他检查器暂不能执行。每轮目录包含 `plan.json`、原子替换的 `report.json`、本地摘要文件、`execution.lock` 及 `checks/lab.sqlite3` 和真实工件。原生产12库不迁移，评测使用独立目录及校验库。候选与重放沿用冻结Mathlib协议，原Lean源码能力、公理策略和封存不变。

Ctrl+C保留INTERRUPTED及已完成结果，不自动重试；运行中的原检查进程由原执行器清理。进程意外退出留下RUNNING时，读取以租约判定为INTERRUPTED，不晋升、不从旧结果恢复整轮通过率。中断后如需重跑须重新核对并创建新轮次。

## 结果与完整性

每题保留实际运行状态、数学判断、证据等级、完整原文/公式/输入、实际子run、公理与工件审计、候选/重放进程数、worker耗时与拒绝原因。PROVED/E4只来自真实候选与独立重放；证明拒绝或缺少定义保持UNKNOWN/E0，不代表命题为假。此协议没有数学反驳通道。

整轮COMPLETED只表示所有题已处理，其中允许REJECTED和PRECHECK_REJECTED。统计分母包含全部选定题目；仅整轮完成且全部工件一致时报告 `authored_plan_pass_rate`。`model_solve_rate`、`alignment_accuracy`始终null，`model_calls=0`。无独立模型效果或引理复用收益声明。

读取重新核对报告/计划摘要、当前数据/适配器/原检查协议、完整请求和真实子工件。损坏或来源变化使通过率为空；原子报告与摘要更新间短暂不一致可暂不可用，不产生证明标签。原子替换和本地SHA不提供持久签名，宿主、Python、SQLite和文件系统仍受信。子工件审计沿用原持久失效规则，恢复文件不复活。历史读取 `fresh_attestation=false`，不重新执行Lean。

每轮报告与计划限2MiB，数据集128KiB、最多16题，固定30秒预算；最多100本地轮次，界面列最近30条。路径、符号链接及无效ID拒绝。用户资料未被自动加入评测，原归档/实例不参与上传或训练。

## HTTP 与前端

| 方法 / 路由 | 行为 |
| --- | --- |
| GET `/v1/mathlib-evaluations` | 固定计划及最近30轮执行元数据，无存储分数的自动信任 |
| GET `/v1/mathlib-evaluations/plan?split=all` | 指定组的完整冻结计划 |
| GET `/v1/mathlib-evaluations/{id}` | 复核完整工件后的历史逐题报告 |

重复/未知查询字段拒绝，无HTTP执行或配置写入；沿用原Host/Origin/CSP/no-store边界。`/evaluations`支持完整题目查看、分组、命令、历史详情与JSON导出；保留旧Nat模型评测区。导出包含本机完整来源与工件元数据，请按原私有边界保管。
