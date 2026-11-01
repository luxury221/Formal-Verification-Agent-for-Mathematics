# 数论搜索对照 v1

协议：`number-theory-search-evaluation-v1`。数据：`evals/number-theory-search-v1/cases.json`，16 个作者编写且已查看的完整目标，不附参考证明，不授权训练。开发/回归分组按案例族分开，只用于可见功能回归。

## 操作

在项目的 WSL 目录执行：

```sh
python3 -m mra search-evaluation-prepare --split all
```

可选 split 为 all / development / regression。准备命令运行两种有限搜索，保存 `artifacts/number-theory-search-evaluations/ns-eval-<32位hex>/`。返回 PREPARED、完整 plan、plan_fingerprint；没有子检查目录，没有 Lean/模型调用。

打开评测中心的“搜索对照”，刷新并查看该轮记录，逐题审查原文、Nat 域、所有变量/前提、结论、生成证明和预算。随后复制包含实际 ID 和指纹的命令：

```sh
python3 -m mra search-evaluation-run ns-eval-<id> --plan-fingerprint <sha256> --reviewer '核对者姓名' --confirmed
python3 -m mra search-evaluation-report ns-eval-<id>
```

姓名为本地具名声明，不是身份认证或独立数学专家认证。未确认、空姓名、过期指纹和不同核对者均不能启动检查。相同身份和指纹重放命令只返回现有状态，不能重置预算或自动恢复。Ctrl+C 尽力清理检查状态并保留 INTERRUPTED；监督进程硬退出后读取发现 lease 已释放，也呈现 INTERRUPTED，统计为空。终态或中断后若需重新检查，必须准备并审核新轮次。

## 预算与语义

- direct：证明高度 1；composed：高度 6。两者使用同一个冻结搜索器。高度包括 ref/hyp/pair/mp/mpr 构造，带证明参数的单个库引理应用通常也需要高度至少 2。
- 每方案/题目搜索最多 3000 步、32 个输入子项、0.75 秒协作式合成；输入验证、摘要及序列化另计。默认基线刻意简单，不能据此声称超过成熟数学证明器或独立持出题目上的提升。
- 每个实际候选一次 Lean 检查与一次独立内核重放，共享 30 秒 worker；没有自动修复或重试。每轮最多 32 个槽位，最坏 worker 上限 960 秒；实际有候选的槽位数和耗时另行记录，完整性扫描与调度不计入 worker。
- 准备得到候选或没有候选都保持 UNKNOWN/E0。执行后只有完整实际检查和具名对齐有效的子运行才可 PROVED/E4。未找到候选、证明拒绝、超时或中断均不作为反例，REFUTED 不在本协议输出通道。

两种方案的实际搜索展开数、实际 worker 时间、候选数、验证通过数和未知数分别展示。summary 只有整轮 COMPLETED 且完整性通过时才非空；所有题目进入各自分母。composed_only_proved 是当前这批可见题目中“组合方案已证而基线尚未证”的 ID 列表，不证明差异具有统计显著性，也不说明知识复用、模型或更强证明器的效果。

## 数据与报告

计划包含完整定义、数据和实现摘要、原验证 manifest、来源/曝光说明、候选搜索记录、每候选确切验证计划及预算。内容的 plan_fingerprint 在准备完成时确定，不重新运行带墙钟的搜索来重建指纹。

计划和报告均以 `{payload,sha256}` envelope 原子替换。报告保存已处理槽位的顺序前缀、inflight、具名核对者和耗时；读取重新核对原目标、假设、方案顺序、输入/候选摘要、验证计划、子账本与工件。主业务库不保存评测子运行；子检查账本及证明位于本轮 `checks/`。

PREPARED / RUNNING / COMPLETED / INTERRUPTED / EXECUTION_ERROR 是运行状态，独立于子目标的数学结论。UNAVAILABLE 表示完整性复核不通过，结果和统计隐藏，并写入持久 `invalidated.json`；恢复原文件也不能重新启用该轮。最多 50 轮，本轮文件上限 2MiB、数据集上限 128KiB；拒绝路径穿越、符号链接跳转和重复/遗漏/重排结果。SHA 与本地文件仍不是持久签名。

HTTP 仅提供 GET `/v1/number-theory-search-evaluations` 和 GET `/v1/number-theory-search-evaluations/<id>`；浏览器位于 `/evaluations#search-comparison` 或 `/evaluations#<id>`。不开放 Web 执行接口，访问页面不启动搜索、worker 或外部模型。当前模型分数为 null，真实模型调用 0。
