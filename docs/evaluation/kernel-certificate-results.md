# 固定目标内核表达式证书验收

日期：2026-10-19。环境：WSL Ubuntu-22.04、Python 3.10.12、Lean 4.35.0 与固定 Mathlib。决策见 ADR-010，输入格式和命令见 `docs/protocols/kernel-certificates-v1.md`。

本轮新增受限证明数据入口：调用者可以为两个固定黄金目标提交自己的结构化证明项。宿主校验 JSON、资源上限、变量作用域和版本指纹；受信模板直接构造 Lean.Expr，以 kernel.addDeclCore 验证精确目标。证书不进入 Lean 源解析器、elaborator 或 tactic 执行器；目标描述来自仓库，证书不能改写命题、前提或方向。成功后仍要经过第二个隔离进程的数据重放、公理审计、工件哈希和 EvidenceGate。

首期目标为 nat_add_zero，以及 false_all_nat_zero 原命题的完整否定。三份自有示例分别采用 Nat.add_zero 常量、lambda 应用同一引理，以及在假设下利用自然数 1 的完整否定证明。这些证明项由作者构造，真实模型调用为 0；不计入 Nat 模型求解率、预留集或训练数据。

最初专项 `MRA_REAL_LEAN=1 python3 -m unittest discover -s tests -p test_certificates.py -v`：27 项通过、0 跳过、114.189 秒，日志 `artifacts/kernel-certificate-targeted-tests.log`。随后增加对内部认证但错误模式/摘要/审计元数据的门禁负例，纳入完整回归。该负例使用测试内部密钥，只验证门禁，不代替真实证明执行。

| 检查 | 结果与证据 |
| --- | --- |
| 多种证明项 | 常量项与 lambda 项均经真实内核检查、导出及第二阶段重放 |
| 缺少/提供语义对齐 | 同一真实证书无对齐为 UNKNOWN/E3，具名版本绑定对齐后为 PROVED/E4 |
| 反驳方向 | 验证完整 ¬ (∀ n : Nat, n = 0)，具名对齐后 REFUTED/E4 |
| 错误目标证明项 | 有效的 0=0 证明不能用于全称加零目标，内核类型拒绝，UNKNOWN/E2 |
| IO 常量 | IO.println 仅是数据；真实内核报告其类型为 α → IO Unit 等，与目标不符，无法执行为证明 |
| unsafe / 未知常量 | 受信解释器拒绝，POLICY_BLOCKED/UNKNOWN |
| sorryAx | 即使构造成目标证明项，也被公理/重放政策拒绝，无法发布可信证据 |
| 源码/tactic/额外节点 | 严格 JSON/节点解析在 Worker 前拒绝；离线 CLI 不创建工件或伪造结果 |
| 字节/深度/节点上限 | 有界文件读取、规范化大小检查、深层 JSON 与平衡大树均有拒绝测试 |
| 版本、假设、方向和目标限制 | 指纹变化被拒绝，未启用案例和 Nat 目标不能经新入口隐式开放 |
| 文件和回执绑定 | 改写保存的证书导致真实回执无法晋升；错误模式/摘要/审计负例同样拒绝 |
| 超时与取消 | 实际 Worker 超时不成为反驳；第二阶段取消保持 UNKNOWN/E0 |

既有 Nat 参考评测保持 18/18 通过、9 PROVED、9 REFUTED，实际 36 个 Worker，总耗时 189.245 秒。报告位于 `artifacts/evaluations/nat-arithmetic-v1-c23897aac7e0498fa1a03d3b5c14214c/report.json`。该评测继续执行仓库参考，不使用新证书替代参考或增加生成次数。

完整回归 `MRA_REAL_LEAN=1 python3 -m unittest discover -s tests -v`：192 项通过、0 失败、0 跳过，651.553 秒。新增证书协议 10 项、无 Lean 的实际 CLI 4 项、真实 Lean/门禁 14 项；既有 164 项全部保留，包括黄金案例、18 条算术参考、审核撤销/修订、独立重放、完整性和任务崩溃/重试/取消/锁继承。20 项原模型服务 mock 只验证协议；证书证明和重放路径使用真实 Lean。

完整日志为 `artifacts/kernel-certificate-tests.log`，机器记录为 `artifacts/kernel-certificate-validation.json`；后者绑定源码/配置/两份 Lean 模板的前后摘要、测试分组、CLI 演示和执行证据。`artifacts/kernel-certificate-execution-evidence.json` 记录本轮真实运行、输入模式、证书摘要、进程、公理审计及故意篡改的负例工件。Ruff 0.12.0 lint 和 38 个 Python 文件格式检查通过，输出保存在 `artifacts/kernel-certificate-quality.json`。

实际六条 CLI 验收保存在 `artifacts/kernel-certificate-cli-demo.json`：无 Lean 的目标描述、常量证明 UNKNOWN/E3、lambda 证明具名 PROVED/E4、完整否定具名 REFUTED/E4、错误目标证明 UNKNOWN/E2，以及既有参考 check 的 UNKNOWN/E3。最终逐一复核五个执行目录的全部回执文件，均匹配；同一个 Nat 目标的三种不同证书（含错误证明）使用完全相同的受信检查源，证书内容未拼接进源代码。

源码门禁通过 53 个 Python/JSON 文件，另将两份 Lean 模板纳入共 55 个源码/配置摘要，验证前后完全一致。最终重新核对源码、测试日志、CLI、执行清单、参考报告、质量输出及封存/专项记录的摘要。任务数据库只读 integrity_check=ok，无残余 RUNNING 任务。以上最终核对结果纳入验收 JSON。

新模板和证书模式/目标策略已显式封存。快照指纹 `23e5407a36de2269955223bfdfcba757316df4a966ddc472c76156fe2f414f18`，重新核验 Lean 17,925 个文件、源码 10,267 个文件、154 个缓存包的 2,310 个解包文件，安装树仍为 33,152 项，耗时 31.741 秒。记录为 `artifacts/kernel-certificate-seal.json/.log`；旧 9b068bfe… 快照及锁保存在其 backup_directory，既有独立重放验收仍作为历史记录保留。

本轮没有新增运行依赖或下载；采用既有 Lean 归档的 Apache-2.0 Expr/Json/kernel API。证书仅支持七种构造和两个固定目标，两个进程仍使用同一内核实现。旧参考输入继续受精确案例/Claim/Formalization allowlist 限制。任意目标、任意 Lean 源、真实模型生成/修复、证书任务队列、用户身份认证、持久签名与不可变存储均未交付；宿主所有者、Python、内核及封存库仍受信。远端 CI 未运行。
