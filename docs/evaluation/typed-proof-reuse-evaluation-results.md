# 类型化固定程序复用对照验收（MRA058）

本轮是作者完全可见的有理数／混合数域机制诊断，没有模型、独立盲测或原创数学能力成绩。完整题集与预算在真实执行前固定，失败版本全部保留。

| 固定题目 | 导入归档 | 展开同一程序 |
|---|---|---|
| rat-polynomial-repeat | COMPLETED | COMPLETED |
| mixed-lift | COMPLETED | COMPLETED |
| rational-cancel | COMPLETED | COMPLETED |
| missing-nonzero | REJECTED | REJECTED |
| source-unavailable | SOURCE_UNAVAILABLE | COMPLETED |
| quantified-parameter | COMPLETED | COMPLETED |

两组完整分母均为 6；来源归档不可用题的差异属于归档可用性消融。错误非零条件两组均真实 REJECTED／UNKNOWN，不能报告 REFUTED。两组在可用归档题上的结果一致。

| 组别 | 已证／完整分母 | 冷检查数 | 预留worker秒 | 实际worker秒 | 检查墙钟秒 | 其中非worker秒 | 目标worker秒 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 导入（含来源构建） | 4/6 | 10 | 300 | 28.284 | 182.925 | 154.641 | 15.017 |
| 展开 | 5/6 | 6 | 180 | 15.293 | 89.994 | 74.702 | 15.293 |

正式轮次 ID：typed-reuse-eval-92a911ca22134fbda9860903a5966424。整轮执行墙钟 275.725 秒；当前独立报告审计墙钟 8.512 秒。检查进程数：导入冷成本 19、展开 11。这些是本次观测，不是端到端硬时限或统计结论。

当前小题不显示总体复用收益；成本必须计入来源构建与反复完整性审计。不得据此宣称一般提速、复用无用，或达到四大期刊级求解能力。更大的数学任务、持出题集和模型规划仍需独立验证。

## 程序隔离与真实边界

来源类型完整闭合；两组在同一冻结策略前以核心 try clear 显式清除所有可能的生成外层变量／条件／定义，再引入来源自己的参数。完整源程序和显式调用树对两组可见。两个真实探针最终验证：正例候选＋独立重放通过；假来源不能借外层矛盾条件通过，拒绝日志包含 grind 失败且没有语法错误。量词实参同名题也保留完整闭合类型。

监督进程在来源真实候选／重放成功后 exit73：父报告 INTERRUPTED，无比较摘要、无自动续跑；子检查有独立 PID 的 E4不自动变成父成功。契约还覆盖撤销、工件损坏还原、实现／题集漂移、删题／重复／重排、确认和幂等。

## 验证与保留失败

- contracts：220 通过／43 跳过，共 263 项，36.376 秒。来源及测试运行前后逐字一致。
- real：3 通过／0 跳过，共 3 项，323.691 秒。来源及测试运行前后逐字一致。
- 真实专项只运行本轮三项；不重跑旧生产 RealTests。非执行夹具不得作为数学证据。
- 实际CLI验收：帮助／准备／报告／不确认与错指纹拒绝／已完成原确认幂等／异名拒绝／错误路径／原命令前缀。没有新增Lean执行。
- Ruff、格式、source gate及原页面JavaScript／Node调度契约另有完整日志。未修改页面，没有新浏览器验收。

保留首轮真实 clear * 语法错误（3项中2项失败，不能把其中负例通过当隔离证据）、@show 应用语法失败探针、第一次CLI格式失败、WSL用户总线启动失败、缺 PYTHONPATH 的helper首调用，以及旧HTTP契约一次 sqlite readonly 失败。该HTTP错误在相同源码再次运行通过，根因未核实；没有改动旧HTTP或数学核心。详见 verification-helper-notes.json 和源版本备份。

## 使用与后续

使用 python3 -m mra typed-reuse-evaluation-prepare 查看完整两组程序及预算，再用 typed-reuse-evaluation-run <ID> --plan-fingerprint <指纹> --reviewer <姓名> --confirmed 执行；typed-reuse-evaluation-report <ID> 重新审计。单次执行，不自动重试／续跑。父报告恒 UNKNOWN/E0，原研究图和主复用库不自动晋升。

下一优先：数论／代数的一般结构与受控归纳证明接口，先做完整条件和真实内核边界，再引入记录曝光的独立任务集及模型规划。模型仍按用户要求稍后配置。
