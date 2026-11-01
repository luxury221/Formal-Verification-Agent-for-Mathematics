# analysis-streaming-v1 执行协议

2026-10-29，MRA081。输入 schema 仍为 analysis-language-v1。完整类型、空间实例、变量、原条件、证明限制与语义对齐沿用[A1协议](analysis-language-v1.md)。profile 是执行身份，并非新的数学语言。

在项目根目录使用 PYTHONPATH=. python3 ops/scripts/analysis_streaming.py。

- catalog：查看输入、执行身份、扫描政策和已知示例。
- preview --input FILE：生成完整目标、全部条件和来源指纹；仅 E0。
- submit --input FILE：提交 request_id、request、plan_fingerprint、confirmed=true、具名 reviewer；同 ID 严格幂等。
- work [--run-id ID]：显式执行，无自动重试；候选与重放共享输入的1–30 worker秒。完整性和报告耗时另计。
- report --run-id ID / list：核验或列出该版本的记录。
- cancel --run-id ID / revoke --run-id ID --input FILE / recover：取消、具名撤销、锁内中断恢复；恢复不重排执行。

canonical库为 artifacts/analysis-streaming/lab.sqlite3；私有库叶目录必须为 artifacts 下 analysis-streaming-private。不能用新版本收养原 analysis-language 或其他账本。当前没有新网页、HTTP、默认 worker 扫描或旧图来源自动接入。

每次完整核验重新读取全部声明字节，含原封存 trees/files/host_files 和 seal；逐项比较、不保存“已通过”缓存。两线程、pending≤8、阈值65536字节、256KiB buffer；超过阈值才并行。last_scan 和执行 result.integrity_scans 为 E0 性能观察，不能单独证明数学命题。检查范围与环境 fingerprint 沿用 A0，执行器及政策身份单独绑定。

COMPLETED/PROVED/E4 需要真实候选与不同PID的内核数据重放、完整目标及条件确认、合法公理闭包、十项完整工件和实际运行环境。两进程使用同一固定Lean实现。负对照拒绝、超时、取消、中断和失效保持 UNKNOWN/E0，REJECTED 不是已验证反证。具名对齐仍是本地声明，不自动证明自然语言等价或假设相容。

报告重编 plan、核全部工件与原审计、真实字节和 worker预算；永久失效marker使原字节/账本恢复后仍不能复活。数据库暂忙不作永久失效，RUNNING不检尚未写完工件。旧A0/A1来源与已封存结果不改。

当前不支持导数、积分、测度、a.e.、正则性或PDE输入。性能是否改善以[真实对照](../evaluation/analysis-streaming-results.md)为准；该对照不是盲测或原创研究能力评价。
