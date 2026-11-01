# 多引理研究空间首期验收

日期：2026-10-26。设计：[ADR-020](../adr/0020-versioned-research-space.md)；协议：[research-space-v1](../protocols/research-space-v1.md)。

本轮建设私有项目、原始问题、定义笔记、类型化问题/子问题/引理、依赖与语境、分支、版本快照和确切历史证据绑定。网页 `/research`、HTTP及CLI共用SQLite账本。主问题须有自身证明；前置节点通过不替代主问题的检查。定义笔记不作为形式化前提，依赖不自动加入Lean假设。

## 实测

- **101项受影响Python回归通过，0失败/跳过，552.342秒**。包含新增研究20项（13协议、7真实Lean来源集成）及HTTP19、工作区6、知识18、论文15、整数12、原CLI11。不是全部历史测试重跑；协议模拟仍只证明协议，真实模型调用0。
- 全程运行源码前后不变；受信Nat/Int编译器、模板、Lean/Mathlib及原单定理/知识实现内容不改，快照文件摘要保持 `d9f606b8c06ac7a53735ddc7062029a5f4065d607156a59e344f584e2d8d9765`，没有重封存或停用旧来源。原4条ACTIVE知识逐项仍可核对。
- Ruff/格式、7份JavaScript语法、source gate和11项公式转换通过。回归后源码变更另列manifest，不把不同源码声称为一次测试。当前回归后变更：无。
- 实际Edge **12项通过，6张桌面/手机截图已目视，JS/console/CSP错误0**。创建/草稿/保存、完整节点带入、真实检查、具名绑定、主问题保持未知、图版本、分支隔离、撤销传播、不自动复活、JSON导出、全局搜索及390px十页面均验收。
- 浏览器从研究节点实际发起1个新Lean义务（候选与独立重放两个进程），命题PROVED/E4；没有作者步骤的原证明保持UNKNOWN。第二节点引用已有作者样例的真实历史E4，绑定不执行新Lean。演示两节点已证、主问题UNKNOWN，不是原创数学成果或模型能力分数。
- 研究账本/图保存/绑定调用预算均0；单定理实际执行继续使用原30秒两阶段共享预算。未引入图自动调度、一般定义、引理实例化或自动证明。MRA029是管理与来源绑定首期，后续执行器须另验总预算、取消和恢复。
- 最终21条静态路由200且逐字节匹配源码；五账本integrity ok，无RUNNING/QUEUED，worker无错误、模型未配置。服务恢复8765，无开机自启、提交/推送/发布或远端CI。

## 证据

工件 `artifacts/research-*`：stage-backup、validation/tests.log、execution-evidence、quality、notation、browser-qa/截图、browser-execution-evidence、existing-knowledge、service-health/start、final-runtime和delivery-check。源码/工件/文档摘要存入manifest。实际项目：<http://127.0.0.1:8765/research#research-85f1afb298174f7499b166eaa5b7aff4>。
