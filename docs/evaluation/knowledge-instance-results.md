# 知识实例化首期验收

日期：2026-10-27。MRA030 首期 / ADR022；受限 Nat/Int 功能验收，不是数学原创性、模型或顶级期刊问题的能力评测。

- 最终受影响回归 **69 项通过，0失败/跳过，376.244秒**。新增26项：5项结构代入、3项离线/HTTP/真实SQLite锁边界、18项以真实来源为基础的集成（包括实际Lean/独立重放、API/CLI、并发与生命周期）。最终复验另覆盖原HTTP、UI与知识；不是历史全量回归。较早146项通过后，实际Edge发现全量知识刷新引起写锁竞争并误判永久失效；修改为针对引用的只读审计，随后92项中91项通过、1项发现Python3.10缺少新版SQLite错误码常量。最终修复兼容性并完成本次相关范围重验；原调度24项在上一轮通过，代码保持冻结。所有早期日志与失败浏览器记录保留。
- 常量/符号和部分前缀代入、Nat/Int边界、保留蕴含与存在量词、变量捕获/非法AST/结构膨胀拒绝；预览保存不执行；完整新目标真实PROVED/E4，候选和重放进程不同；无作者步骤仍UNKNOWN。失败策略没有反驳结论。
- 来源/归档改变、删除、撤销后永久失效；失效事务后模拟退出可重试清理；真实E4门禁后撤销或墙钟到期不发布；重放中取消释放锁。真实子进程在完整检查后退出73，保留PROVED工件但恢复INTERRUPTED，不晋升或自动重试。异常注入只控制时机，不伪造数学门禁。
- 实际Edge **10项流程通过**，另有离线桌面/手机预览，**12张截图已目视**；源选择、原文/完整公式/代入、捕获拒绝、编辑清确认、具名门禁、真实检查、私有归档、源撤销到实例及归档的传播、运行取消/重新确认/刷新历史、390px及十页面。预期捕获拒绝的一个HTTP400独立记录，其余JS/console/CSP错误0。
- 演示：http://127.0.0.1:8765/library#instance-cdec78f0fad54aae9d34d0c7ae0748a8。最新运行COMPLETED、完整新命题PROVED/E4，作者证明UNKNOWN；较早取消记录保留。另一个测试实例和它的私有归档因源撤销停用，原来源的独立证明仍保留。
- 最终回归前后源码一致，最终回归后源码修改为零；前端滚动定位、来源复制/研究空间跳转也由最终实际Edge覆盖。Ruff61Python、9JS与源码门禁通过。25个路由资源逐字节匹配；七账本integrity ok、无QUEUED/RUNNING。
- 实例审计只读核对引用及其依赖闭包，仍使用原真实来源与归档规则；写锁保留期间可完成实际验证，临时SQLITE_BUSY不永久停用，真实依赖损坏仍持久传播。没有把临时错误当成可用证明，也没有放宽验证门禁。
- 原6条ACTIVE知识、7项ACTIVE研究绑定、上轮真实三节点示例及冻结验证源码保留。Lean/Mathlib/模板/政策及封存SHA不变，不需重封存。
- 模型调用0；无新增依赖、权重、外部论文、训练、子代理、远端CI或提交/发布。采用既有本机具名声明/SQLite/宿主可信边界；非跨库全局事务。直接使用原Python类须自行调用实例巡检。

来源移除清理知识副本，实例引用快照及原审查仍保留；草稿最多100、列表/运行各最多30，暂无草稿删除或递归实例化。一般Mathlib定义、归档证明项导入、引理战术和持出问题的求解收益评测仍待后续。

证据：`artifacts/knowledge-instance-validation.json`、`knowledge-instance-tests.log`、`knowledge-instance-execution-evidence.json`、`knowledge-instance-quality.json`、`knowledge-instance-browser-qa.json`、`knowledge-instance-browser-execution-evidence.json`、`knowledge-instance-existing-evidence.json`、`knowledge-instance-service-health.json`、`knowledge-instance-final-runtime.json` 与 `knowledge-instance-delivery-check.json`。早期20项专项通过记录保留在 `knowledge-instance-first-tests.log`。
