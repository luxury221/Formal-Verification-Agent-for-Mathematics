# ADR 0095：框架独立的业务工具边界

日期：2026-11-01。状态：本地 JSON 工具门面已实现；openJiuwen 适配器尚未实现。

## 问题

产品需要支持不同模型与智能体编排方式，同时保留文章原文、完整假设、审核、任务幂等、取消和数学证据的稳定语义。若将这些状态交给编排框架，会出现两个任务账本、两个恢复机制以及“工作流成功”被误当成数学证明的问题。等产品全部完成才明确边界也会增加后续适配成本。

## 决定

现在稳定业务工具契约，继续完成产品核心闭环，再实现可选编排适配器。编排器负责规划、选工具、上下文、会话与进度；项目自己的 store 和验证器仍是命题版本、审核、预算、任务状态、证据及永久失效的权威。

新增 `mra.core_tools.CoreToolAPI` 和单次 JSON CLI `ops/scripts/core_tools.py`。输入协议 `product-tools-v1`，输出协议 `product-tool-result-v1`，提供11个固定工具：

- `catalog`、`mathlib.search`、`study.report`、`formalization.preview`；
- `study.transfer_preview`、`study.transfer`、`study.evidence`；
- `verification.preview`、`verification.status`、`verification.submit`、`verification.cancel`。

门面直接委托既有业务操作，不新建队列、证据数据库、恢复或迁移机制。它不提供模型生成、worker执行、shell、文件系统或任意 Lean 工具。Python 调用默认 `allow_mutations=False`；CLI 通过显式 `--allow-mutations` 启用三种业务写操作。此设置是受信本地进程配置，不构成用户身份认证。

所有预览、关联和核验均使用保存的 study/plan/graph 上下文；调用者不能替换上下文或越过既有语义确认。`reviewer`、`confirmed`、完整预览指纹必须来自操作者的实际审核，不授予模型自动代签权限。

## 状态、失败与幂等

`call_id` 仅用于传输关联，既不缓存结果也不取代业务 `request_id`。业务修改的幂等继续由完整请求正文和原 store 判断。适配器遇到传输失败，应查询回执或重放同一业务编号和完整请求，不能自动改编号创建新任务。

`call_status` 与 `operation_outcome` 只描述调用；数学判断仅存在于原验证器的 domain result 中。参数/访问拒绝为 `NOT_STARTED`，返回为 `RETURNED`，业务异常为 `UNKNOWN`，不能承诺数据库从未写入或已经回滚。进程可能在业务提交后、stdout前退出。

输入128KiB、96层、24000值；外层字段和工具参数严格校验，拒绝重复JSON键、非有限数、未知工具和重定向业务路径。输出超过8MiB时整份省略结果并返回 `RESULT_LIMIT` 与稳定身份回执，不裁掉条件或证明正文。读取接口仍可创建审计锁、检测并永久记录失效；“无请求业务写操作”不等于文件系统完全只读。

## openJiuwen 与部署边界

openJiuwen 的工作流、ReAct、工具调用、状态恢复与观测适合放在本边界外。后续适配应实际调用上述业务能力，并记录编排轨迹；框架恢复不得复活已撤销/损坏证据，自动重试不得越过预算或生成新的数学任务身份。

适配器应为可选进程或服务，通过本地 JSON 工具/受控业务 API 调用核心。其 SDK 与模型依赖在独立环境中固定版本、记录许可和用途，不引入封存 Lean 验证环境，也不为 SDK 兼容而直接升级现有 Python 工具链。当前 SDK 及部署选择仍需在实际采用前完成固定版本兼容核对，不能将本工具门面称为已接入 openJiuwen。

现有 `ModelService` 已提供模型无关请求/响应/预算 DTO；当前研究流程仍保留自身配置与门禁。核心可独立运行，外部编排方式可以逐步替换。本轮未安装 openJiuwen、接入真实模型或改变验证器判据；工具契约测试不代表模型能力或新增数学成绩。

## 回滚与限制

删除可选门面后，原业务记录仍按原协议可读；保留数据库、请求正文和验证工件。不得以“回滚适配器”为由删除或覆盖权威库。当前是受信本地调用边界，无多租户认证、远程自动授权或整篇复核时限保证。

完整参数、返回和CLI约定见 [core-tools-v1](../protocols/core-tools-v1.md)。
