# 本地 HTTP 工作台验收

验收记录日期：2026-10-24。范围：MRA-023 的本机、两个固定目标浏览器流程；设计见 ADR-012，协议见 `docs/protocols/http-workbench-v1.md`。活动源码仍位于 WSL 项目根目录。早期专项和浏览器记录日期为 2026-10-19，最终回归使用更新后的宿主环境。

新增 `python3 -m mra serve --port 8765`，浏览器打开 `http://127.0.0.1:8765`。HTTP 请求线程处理有界输入和状态查询，单独的后台 worker 复用持久任务与原有两阶段真实 Lean 检查。没有新增运行依赖、外部 UI 资产或模型调用；Lean/Mathlib 版本与证明策略未改。

专项测试 **18 项通过、0 失败、0 跳过，105.698 秒**：13 项实际 loopback HTTP/SQLite 协议测试，5 项真实 Lean 异步集成测试。前者使用不能执行 Lean 的 metadata-only 环境，不能算作证明能力。证据为 `artifacts/http-workbench-targeted-tests.log`。

| 路径 | 已检查行为 |
| --- | --- |
| 严格请求边界 | 精确 Host、同源 Origin 和会话 token；跨站、重复 Host/Content-Length、Transfer-Encoding、超大/错误 JSON、重复键及未知路由被拒绝 |
| 确切目标 | 仅两个既有黄金目标；提交绑定显示版本的 Claim/Formalization 指纹；无任意 Lean 源或用户定义目标入口 |
| 持久提交 | 与 CLI 共用 SQLite/幂等空间和原始证书解析器；证书作为原始 JSON 文本传输，保留重复键以供服务端拒绝 |
| E3 与 E4 | 未具名真实检查为 UNKNOWN/E3；显式具名确认后创建新任务重新检查，PROVED/E4，原 E3 不变 |
| 完整否定 | 原式“所有自然数都为零”的完整否定经真实两阶段检查，具名后 REFUTED/E4 |
| 错误证明 | 不能以 Nat 字面量证明全称目标；保持 UNKNOWN/E2，不能将失败重试为反例 |
| 取消与重试 | 真实运行中取消不发布任务证据；超时保持 UNKNOWN，显式重试保留证书与 0.001 秒预算，采用新目录 |
| 历史工件 | 证书篡改使顶层 UNKNOWN/E0，证据接口不可用；取消、恢复未提交结果不会暴露遗留成功报告 |
| 页面状态 | 分开显示运行状态、数学判断和证据等级；任务/尝试/工件状态变化清理旧证据，异步响应核对任务及尝试 |

最终实际 Edge 浏览器 **10 项检查全部通过**，捕获 JavaScript 异常 0，记录 7 个任务状态快照（6 个不同任务，超时任务另有第二次尝试快照），保存 6 张截图。检查覆盖 E3、具名新任务 E4、原 E3 不变、完整否定、错误证明、取消、原预算超时重试、重复键原文传输，以及切换任务后清理旧证据。1440 像素桌面和 390 像素窄屏截图已目视检查，窄屏没有页面横向溢出。记录为 `artifacts/http-workbench-browser-qa.json`，截图为同前缀 PNG。

最后补充了端口占用时的启动清理检查：绑定失败发生在线程启动前，服务现在保留原始 OSError，不再等待尚未启动的 worker。最终 **14 项 HTTP 协议测试全部通过，1.036 秒**，证据为 `artifacts/http-workbench-http-protocol-tests.log`。当前新增测试共 19 项，其中真实 Lean 集成仍为 5 项。

最终完整回归 **228 项通过、0 失败、0 跳过，915.476 秒**。既有209项全部保留，新增HTTP19项；20项模型协议mock只验证合同，真实模型调用仍为0。测试期间包括前端在内的61个源码/配置摘要前后一致；来源门禁56个Python/JSON文件通过，Ruff0.12.0 lint和41个Python文件的格式检查、Node JavaScript语法检查通过。

新封存下，Nat作者参考 **18/18通过，9 PROVED、9 REFUTED，36个Lean Worker，207.4969秒**。协议指纹仍为 `ab64875ee464f316d8276cabc50c32a1bc89783faf487a58e4d5a795f2628040`。报告为 `artifacts/evaluations/nat-arithmetic-v1-5d031149f496495b8106fd9ff12fb155/report.json`；这是作者参考验证，不是模型求解率，预留组仍为作者已查看、仓库可见的开发参考。

实际 `python3 -m mra serve --port 0` 启动后，session和页面均返回200；SIGINT正常退出码0。SQLite只读integrity_check=ok，RUNNING任务0，schema_version仍为1。具体任务、尝试、事件和有意篡改的负例保存在执行清单；最终再次核对6个浏览器任务的结果、尝试和工件状态与QA记录一致。

可审计工件：

- `artifacts/http-workbench-validation.json`：完整测试分组、源码摘要、浏览器截图/日志/CLI/执行清单/质量/封存摘要及数据库检查。
- `artifacts/http-workbench-tests.log`：228项完整回归。
- `artifacts/http-workbench-browser-qa.json`：新封存下10项实际UI检查，实际版本来自Browser.getVersion；原始脚本输出另存 `http-workbench-browser-qa-raw.json`，保留旧静态版本预设以解释元数据修正。
- `artifacts/http-workbench-desktop-e4.png`、`http-workbench-mobile-refuted.png` 等6张截图：最终已目视检查的页面。
- `artifacts/http-workbench-cli-demo.json` 与 `http-workbench-cli.log`：真实CLI启动/读取/退出。
- `artifacts/http-workbench-execution-evidence.json`：真实执行、回执、工件摘要和任务历史；负例变更不被当作有效数学证据。
- `artifacts/http-workbench-quality.json`：实际静态检查输出。
- `artifacts/http-workbench-seal.json`、`.log`、`http-workbench-host-update.log`：宿主更新核查和显式重新封存。


早期验收期间 Windows 的 WSL 服务/子进程异常退出；宿主内存提交量曾达 62.4/68.4 GB，随后 PowerShell 报资源不足。早期完整回归因此中断，不能计为通过；失败日志与浏览器记录独立保留。10月19日浏览器服务采用独立 systemd scope（2 GiB 内存/512 MiB swap）；宿主刷新后的最终浏览器和完整回归均使用 4 GiB/512 MiB。不更改用户全局 WSL 配置，也不修改证明目标、策略或 Worker 预算。Edge 的验收临时文件暂存 D 盘，完成后归档到活动项目的 artifacts；浏览器测试未新增运行依赖。

10月24日恢复验证时，完整性门禁发现宿主 libc 漂移，重封存前回归为 185 项、24 个失败、3 个错误；受阻的类初始化没有被计作执行了其全部测试。错误均不能产生数学证据，失败记录以 `artifacts/http-workbench-before-host-refresh-*` 保留。检查发现 Ubuntu 在10月22日自动更新了 libc/Python/unshare/expat 等包；本机 dpkg 包校验通过，更新日志与变化对应。备份旧封存后，重新对照官方 Lean 17,925 文件、源码10,267文件及154缓存包/2,310解包文件；安装树33,152项和项目封存文件均不变，仅8个宿主文件记录变化。该核查仍以本机包元数据与受信宿主为基础，不是独立签名的宿主证明。

当前快照为 `fb68e9dbd59aeb9374d718ddd7f7304e56faebfcc80e2f31717e9893764064a6`，旧快照 `23e5407a...` 备份在 `artifacts/runtime-seals/pre-http-host-update-ff0fb47ff6104f9a810abc7c7663a055/`。重封存55.073秒，证据为 `artifacts/http-workbench-seal.json`、同名 `.log` 和 `http-workbench-host-update.log`。模板、策略、数据、Lean和Mathlib版本均未改动。

最初浏览器脚本曾错误期望超时为 E0；已将断言修正为形式化目标保留 UNKNOWN/E2，未改变后端门禁。该次失败保存在 `artifacts/http-workbench-browser-initial.json`。外部基础设施失败另保留为 `http-workbench-browser-wsl-failure.json`、`http-workbench-browser-resource-failure.json` 和 `http-workbench-tests-initial-resource-failure.log`。

当前模型调用和费用仍为 0，作者参考和证书不是模型输出。两阶段使用同一 Lean 内核实现；本地姓名不是身份认证，会话 token 不是账户凭证。历史证据始终 `fresh_attestation=false`，不验证当前依赖，也不提供持久签名。真实模型生成/修复、任意目标、公开多租户部署、身份权限及分布式调度仍未交付；远端 CI 未运行。
