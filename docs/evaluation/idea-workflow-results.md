# 自然数猜想闭环验收

日期：2026-10-24。设计：ADR-013；协议：`docs/protocols/ideas-v1.md`。

## 范围

交付 `/ideas` 新原文输入、有界一阶 Nat 候选、具名语义确认、至多三次证明/反例计划及反馈修复、真实 Lean 候选检查与独立进程内核重放、证据查询、取消、撤销和版本修订。模型服务采用可选兼容 API；用户明确选择稍后配置，因此真实模型调用为 0，未报告模型自动形式化准确率或求解率。

协议模拟仅供应候选/计划，身份为 fixture；数学判断始终来自真实 Lean 工件及人工测试作者的确切映射声明。浏览器及协议模拟不证明真实模型能理解任意命题。

## 专项验证

25 项全部通过，119.057 秒，日志 `artifacts/idea-workflow-targeted-tests.log`。其中 17 项检查公式作用域/类型/预算、不可执行源输入、版本确认、幂等保存、未配置与配置漂移、澄清、取消/恢复及实际本机模型 HTTP 往返/超时/取消/重定向/截断响应；8 项真实 Lean 集成含三个策略子案例。

真实验证覆盖：新全称不等式 PROVED/E4；错误全称命题通过实例化见证证明完整否定，REFUTED/E4；错误证明保持 UNKNOWN；缺少对齐为 E3/UNKNOWN；篡改公式/计划工件拒绝证据；先失败后换方案并经 Lean 反驳；真实 E4 门禁完成后撤销确认仍不发布任务结论；simp、decide、Nat 截断减法的真实证明。

最初 17 项协议测试有一项 tuple/list 序列化比较错误，修复后复验通过，初次日志独立保留。最初 23 项真实/协议组合中 4 项因递归导出基础库触发文件/时间预算而失败；增加只导出本次声明的受信模板后通过，不扩大文件/时间预算。失败记录 `artifacts/idea-workflow-targeted-before-export-fix-tests.log` 不作为通过证据。

## 浏览器与静态检查

实际 Microsoft Edge 的 7 项操作全部通过，JavaScript 异常 0，4 张截图。涵盖未配置保存新命题且零模型调用、390px 布局、候选等待确认、失败后真实反驳、证据显示、撤销清除旧证据、修订令旧候选失效。实际浏览器版本及完整记录见 `artifacts/idea-workflow-browser-qa.json`。已目视核对桌面、移动及证据页面。

Ruff lint/格式检查 46 个 Python 文件，以及原工作台和新工作台 JavaScript 语法检查通过，记录 `artifacts/idea-workflow-quality.json`。未新增运行依赖或模型资产，未运行远端 CI。

## 完整回归

最终覆盖 **253 项独立测试均已取得通过结果**。初次完整回归运行 253 项，252 项通过、1 项失败、0 跳过，用时 926.509 秒；不是一次性全绿。失败项为 `test_crash_after_real_check_requires_fresh_two_process_retry`：之前运行的旧服务 PID629 抢占共享任务队列，使用旧封存范围将任务按 POLICY_BLOCKED 结束，预期的模拟崩溃子进程未获得任务。核对进程与任务记录后停止旧服务，产品和测试源码保持不变，单独重跑该真实 Lean 崩溃/重试检查，1 项通过，用时 49.002 秒。

初次记录 `artifacts/idea-workflow-validation.json` 的 test_exit_code=1、all_passed=false 与失败日志完整保留；最终合并验收记录 `artifacts/idea-workflow-final-validation.json` 的 all_passed=true，并绑定初次日志、独立复验日志、源码摘要、浏览器/质量记录和数据库状态。没有将初次失败改写为通过。干扰诊断见 `artifacts/idea-workflow-initial-interference.json`，复验日志为 `artifacts/idea-workflow-isolated-retest.log`。

同一轮完整测试还执行全部 18 条作者 Nat 参考，18/18 通过、36 个 Lean worker；这些是参考证明验收，不是模型求解率。运行前后源码摘要一致；两份任务/猜想 SQLite integrity_check=ok，RUNNING=0。新版工作台随后在 8765 端口启动，Windows 访问原页面、猜想页面、脚本及 session 均 HTTP200，真实模型仍未配置。

## 环境与证据边界

运行快照 `901dbf78492acf4cb255e744b2affac16ad2789a0a986981da55edb18530d107`。工具链仍 Lean 4.35.0、Mathlib `56a005855ea17e0b0958f73ec801ae140b0a1679`；安装树仍 33,152 项。新增公式编译器、受信模板和策略字段后显式重新封存，核对官方 Lean 17,925 文件、源码 10,267 文件、154 缓存包的 2,310 文件；证据 `artifacts/idea-workflow-seal.json/.log`。

原 HTTP 封存备份在 `artifacts/runtime-seals/pre-ideas-d1df266bf445462b9a2f9e74eaf32629`，中间编译器封存备份在 `pre-ideas-8a2ede4a346144ebb2dde566f922b14c`。不会自动重建基线；依赖变化仍阻止执行和晋升。

只支持有界自然数公式及四种策略。一般非线性算术、自定义函数、实数/集合/分析和论文审稿不在此轮范围。具名确认不是账户认证；历史工件摘要不是持久签名或当前环境新认证；两阶段使用同一 Lean 内核实现。知识检索、自我训练及真实模型效果评测仍待后续。
