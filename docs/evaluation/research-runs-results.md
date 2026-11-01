# 受控多节点执行验收

日期：2026-10-26。MRA031 / ADR021；仅 Nat/Int 受限语法与既有策略，不是原创数学能力评测。

- 最终受影响回归 **125 项通过，0失败/跳过，810.676秒**。新增24项=12协议/CLI/HTTP与12真实Lean集成；包含原研究20、HTTP19、UI6、知识18、论文15、整数12、CLI11。不是历史全量回归。源码测试前后完全相同，回归后源码修改为零。
- 真实三节点按序PROVED/E4，逐项独立重放；REFUTED与UNKNOWN前置跳过后继；独立节点仍可运行；总检查/worker额度和墙钟停止；真实门禁后取消、图修改或到期不发布结果；重放中取消释放锁；工件篡改/恢复不复活；真实子进程在检查后退出73，恢复INTERRUPTED，不从残留PROVED工件晋升。异常注入仅控制调度时机，数学检查与门禁未伪造。
- 初次专项24项中23项通过、1项失败：共用项目保留历史失效绑定，测试错误期待空绑定。修正为没有ACTIVE绑定，最终回归通过。初次日志仍为 `artifacts/graph-execution-targeted-tests.log`，不作为最终通过证据。
- 实际Edge **12项流程与2项补充视图检查通过、9截图已目视**；逐项确认门禁、完整公式与语境、1项总预算停止、真实三项执行、明确绑定、已完成来源撤销/依赖失效、运行取消、刷新历史、重新确认与绑定、390px及十页面；JS异常/console/CSP错误0。首轮浏览器等待条件误读已关闭对话框的旧内容，修正脚本，失败记录独立保留 `graph-execution-before-browser-wait-fix-*`。
- 最终示例 http://127.0.0.1:8765/research#research-c7196361c3bb41ccbe8fae376eaf06cf。保留两轮有效来源：预算受限的一项与最终三项，均重新核对工件及独立进程；中间完整执行和取消执行的来源均已撤销。主问题具有自己的完整检查，已通过用户流程明确绑定；不会仅凭引理齐全自动证明。无作者步骤，原证明判断仍UNKNOWN。
- Ruff59Python、8JS语法和源码门禁通过；23路由资源逐字节匹配。六账本integrity ok、无QUEUED/RUNNING。原5条ACTIVE知识与2个ACTIVE研究绑定内容保留。受信编译器、原研究/审查/知识协议、模板及运行封存未变，无需重封存。
- 模型调用/费用0，未新增依赖、下载、权重、训练、远端CI或提交/发布。仍为本机具名声明、SQLite/宿主受信、同一Lean内核实现，不提供跨库分布式事务、身份认证或不可变签名。墙钟扫描不能即时抢占，过期不发布新结果；中断需重新预览确认。

证据：`artifacts/graph-execution-validation.json`、`tests.log`、`execution-evidence.json`、`quality.json`、`browser-qa.json`、`browser-execution-evidence.json`、`existing-evidence.json`、`service-health.json`、`final-runtime.json`、`delivery-check.json`及截图（文件均带 graph-execution- 前缀）。协议见 [research-runs-v1](../protocols/research-runs-v1.md)。下一步优先受控Mathlib定义/引理实例化与领域扩展，不把调度验收称作已能解决重大原创问题。
