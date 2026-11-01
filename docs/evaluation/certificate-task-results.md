# 固定目标证书持久任务验收

日期：2026-10-19。范围：MRA-021 接入 ADR-010 的固定目标证书；设计见 ADR-011。活动源码位于 WSL 项目根目录。

完整回归 **209 项通过、0 失败、0 跳过，793.234 秒**。在既有 192 项基础上增加 17 项：8 项实际 SQLite/输入状态测试、2 项无 Lean 安装的真实 CLI 测试、7 项真实 Lean 队列集成测试。原有 20 项模型协议 mock 只验证接口合同；真实模型调用仍为 0。

| 检查 | 实际结果 |
| --- | --- |
| 证书冻结与重启 | 规范化正文和 SHA-256 保存到 SQLite；原文件删除后重启 worker 仍完成两阶段，未具名时 UNKNOWN/E3 |
| 提交幂等 | JSON 空白/键顺序变化返回同一任务；证明项、审核者、预算变化及参考/证书模式切换与原 ID 冲突 |
| 提交边界 | 源载荷、重复键、超大输入、目标指纹错误、禁用目标和 suite 覆盖被拒绝，不启动 Lean |
| 执行前重建 | 未知种类、错误模式分派、suite/正文/摘要/预算/假设漂移失败关闭，保持 E0 |
| 精确原式与完整否定 | lambda 加零证书具名后 PROVED/E4；否定全零证书具名后 REFUTED/E4；两者均有两个真实检查进程 |
| 类型与政策拒绝 | Nat 字面量不能充当全称证明；unsafe 常量被拒绝；流程可 COMPLETED，但数学判断 UNKNOWN/E2，不能重试为反驳 |
| 取消 | 第二阶段重放期间取消，以及真实 E4 后终态提交前取消，均不发布任务证据，UNKNOWN/E0 |
| 超时重试 | 两次真实 0.001 秒预算尝试保持同一冻结证书和预算，目录不同；数学判断始终 UNKNOWN |
| 崩溃恢复 | 真实成功报告写完后 SIGKILL 监督进程；恢复为 INTERRUPTED/UNKNOWN/E0；显式重试使用新目录和新进程，重新完成两阶段 |
| 历史证书篡改 | 修改已归档的证书文件后，查询检测 CHANGED_OR_MISSING，顶层结果 UNKNOWN/E0，保留历史尝试 |
| 既有请求兼容 | 对本轮前已有、同运行快照的 9 条参考请求重建合同，正文和请求指纹全部保持一致；数据库仍为 schema v1 |

额外执行 **24 条真实 CLI 命令**，包含三次成功检查：未具名加零为 UNKNOWN/E3、具名 lambda 加零为 PROVED/E4、具名完整否定为 REFUTED/E4；另覆盖重复提交/执行、历史查询、排队取消、超时及显式重试。核对五个执行目录的全部任务记录文件摘要，并核对证书正文/摘要与数据库冻结内容一致。三个成功目录均有两个不同 PID、精确目标审计和 11 个回执文件；两个超时目录不发布数学结论。

原有 Nat 算术参考 **18/18 通过**，9 PROVED、9 REFUTED，36 个 Lean Worker，200.596 秒。参考评测协议指纹仍为 `ab64875ee464f316d8276cabc50c32a1bc89783faf487a58e4d5a795f2628040`。这是作者参考解验证，模型求解率仍为空，预留组仍为仓库可见、作者已查看的开发参考。

来源/JSON 门禁覆盖 54 个文件；包括两个 Lean 模板的 56 个源代码/配置摘要在整个测试和 CLI 验证前后一致。Ruff 0.12.0 lint 与 39 个 Python 文件的格式检查通过。SQLite 只读 `integrity_check=ok`，没有残留 RUNNING 任务。没有新增下载或运行依赖，没有重新封存环境；快照仍为 `23e5407a36de2269955223bfdfcba757316df4a966ddc472c76156fe2f414f18`。

可审计工件：

- `artifacts/certificate-task-tests.log`：完整 209 项回归。
- `artifacts/certificate-task-validation.json`：测试分组、源码摘要、证据摘要、CLI 工件核对及既有请求兼容记录。
- `artifacts/certificate-task-cli-demo.json`：24 条实际命令、退出码和原始结构化输出。
- `artifacts/certificate-task-execution-evidence.json`：本轮真实运行回执、进程、文件摘要及任务/尝试/事件；有意篡改的负例单独记录变更文件。
- `artifacts/certificate-task-quality.json`：Ruff 实际输出。
- `artifacts/evaluations/nat-arithmetic-v1-922c9312e2f64d2ca6e5a6ffd7dd0ea9/report.json`：18 条算术参考。

初次专项日志保留在 `artifacts/certificate-task-targeted-initial-tests.log`：7 项真实 Lean 测试均通过，一项状态测试因测试夹具缺少 annotation 字段报错。修正测试准备代码后，8 项状态测试及最终完整回归全部通过；未将失败日志覆盖为通过记录。

本轮仍仅支持批准的仓库参考及两个固定目标的受限数据证书。没有真实模型生成/修复、任意 Lean 源、用户定义目标、身份认证、持久签名、HTTP 或分布式 worker。两个阶段使用同一 Lean 内核实现；宿主、SQLite 所有者和封存基础库仍受信。历史报告不是新签发，远端 CI 尚未执行。
