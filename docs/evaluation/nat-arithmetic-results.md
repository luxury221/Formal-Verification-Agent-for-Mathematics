# 自然数算术评测基础验收

日期：2026-10-19。环境：WSL Ubuntu-22.04、Python 3.10.12、Lean 4.35.0 和固定 Mathlib；设计见 ADR-007。

首期领域限定为 Nat 上的全称算术命题。18 条作者参考按 9 个手工命题族隔离为 development / regression / reserved 各 6 条，每组 3 条真命题、3 条假命题；train 为空，禁止训练使用。出处、许可、用途、变量/假设及数字见证均有记录，没有引入外部 benchmark 或私有论文。

真实执行 `python3 -m mra reference-benchmark --split all --include-reserved`，18/18 参考通过：9 条原命题被证明，9 条原命题的完整否定被证明，均经过现有目标/公理与证据门禁。整次参考运行用时 137.6952 秒，包含初始化、完整性校验及证据评估。逐题 Lean 源、请求、输出、完整环境快照、回执和作者对齐记录保存在报告指向的 `artifacts/runs/`。

| 拆分 | 命题族 | 真 / 假 | 参考结果 |
| --- | --- | --- | --- |
| development | 加法代数、后继、顺序自反性 | 3 / 3 | 6/6 |
| regression | 截断减法、消去、分配律 | 3 / 3 | 6/6 |
| reserved | 零乘积、加法单调性、乘法恒等式 | 3 / 3 | 6/6 |

例如，`b ≤ a → a - b + b = a` 有真实参考证明；去掉前提的全称式由 a=0、b=1 反驳。无条件乘法消去由 a=0、b=0、c=1 反驳。连接词“或”错误换成“且”、把 ≤ 的结论升级成 < 等案例同样检查完整否定。数字见证注释只是说明，REFUTED 来自 Lean 证明。

本次是作者参考解的检查，真实模型调用为 0，`model_solve_rate=null`、`alignment_accuracy=null`。E4 中的具名对齐明确为 repository-nat-arithmetic-v1-reference-author，不是用户确认或独立专家审核。参考通过率不能用来宣称模型求解率、通用形式化能力或数学研究进步。

预留组已在构造及参考验证时被作者查看，源文件在仓库中可见；不是独立盲测或历史无污染的 holdout。默认命令仅选择 regression；选择 reserved/all 必须显式 --include-reserved，报告记录本次 reserved_selected=true。后续真实模型比较需要固定策略，并建立未用于该策略调试的新盲测流程。

固定协议：每题一次生成、一次 Lean 检查；模型 30 秒、输入 16,000 字符、输出 2,048 token / 65,536 字节；Lean Worker 墙钟 30 秒，环境校验等开销另计。当前参考验证未进行生成，所以记录零模型用量。协议指纹绑定数据、拆分及全部预算；本次 all 拆分指纹为 `d27b92e645c2e39119cebeb44a39a3a8aa68dcc449a43c22eb2d500916f041ba`，不代表已经运行同预算模型比较。

数据集文件摘要为 `bf32357f7c6d59c6556cfb4960b465f34b921978fd91fa0130185e67d06eb898`。封存范围扩展后，显式重新核对 17,925 个 Lean 文件、10,267 个源码文件及 154 个缓存包的 2,310 个解包文件；安装树仍为 33,152 项。新环境摘要 `58f585a822731203f0e1ee781ce7a1af4fb18ca78575f2aaf79c903ed0f1337e`，旧摘要及备份位置见 `artifacts/nat-evaluation-seal.json`，复核日志见 `artifacts/nat-evaluation-seal.log`。

CLI 协议演示保存在 `artifacts/nat-evaluation-cli-demo.json`：默认只返回 6 条回归输入；隐式请求 all 退出码 1；显式请求包含 18 条；train 输出空输入。公共输入不含参考 proof、期望标签、direction 或见证。

完整回归 `MRA_REAL_LEAN=1 python3 -m unittest discover -s tests -v`：116 项通过、0 失败、0 跳过，248.245 秒。

| 分组 | 数量 | 范围 |
| --- | ---: | --- |
| 新数据集合同 | 16 | 许可默认拒绝、摘要、跨族泄漏、拆分覆盖、重复/P0 重叠、定义域/前提/见证、预算及协议指纹 |
| 新数据集真实离线 CLI | 6 | 无 Lean 查看协议、预留集显式选择、空训练集、篡改及缺少封存环境时拒绝 |
| 新参考真实 Lean CLI | 1 | 一个测试方法真实执行上述全部 18 条参考，逐题验证实际 Claim 前提、形式化方向和作者对齐 |
| 既有真实 Lean 门禁 | 16 | 含原 10 个黄金子案例及新参考不能改写 proof/删除前提的拒绝检查 |
| 既有模型协议 mock | 20 | 保留结构、预算和失败合同；不计为模型能力 |
| 既有本地 fixture / 工件 | 7 | 本地样例及完整性 |
| 既有形式化真实 CLI | 11 | 候选、审核、撤销及修订 |
| 既有审核 SQLite / 文件系统 | 23 | 状态、并发、幂等及中断恢复 |
| 环境完整性 / 基础合同 | 12 / 4 | 封存内容、拓扑与不可变合同 |

新数据集和旧黄金用例没有 mock Lean checker，真实模型调用为 0。完整日志 `artifacts/nat-evaluation-tests.log`、分组和源码摘要 `artifacts/nat-evaluation-validation.json`、本次参考报告副本 `artifacts/nat-reference-benchmark.json`。源码门禁通过 44 个 Python/JSON 文件，Ruff 格式与 lint 通过（32 个 Python 文件）。远端 CI 未运行。

MRA-018 的领域、数据和预算基础已交付；真实模型同预算比较仍待 MRA-020。身份认证、任意候选独立验证与持久签名、任务恢复和 HTTP/UI 保持各自待办，不用参考验证代替这些验收。
