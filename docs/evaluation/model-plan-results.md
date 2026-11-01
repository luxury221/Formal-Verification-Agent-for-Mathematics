# 固定预算模型策略评测验收

日期：2026-10-24。设计：[ADR-014](../adr/0014-fixed-budget-model-plan-evaluation.md)，用法：[评测协议](../protocols/model-evaluation-v1.md)。

交付 `model-benchmark --plan`、配置后实际运行与 `model-benchmark-report` 历史完整性检查。用户继续选择稍后配置模型；本轮真实模型调用为0，未报告真实模型求解率。给定目标来自18条既有作者参考的人工 Nat AST 映射，不把该输入模式称为自然语言自动形式化评测。

## 验证结果

最终受影响回归 **62项通过、0失败、0跳过，74.324秒**。包含新增15项（10项协议/CLI、5项真实Lean/历史/取消）及既有数据集合同16项、数据集CLI6项、形式化CLI11项、HTTP协议14项。这是受影响回归，不是重新执行全部268项。

真实Lean执行四次候选验证、7个Lean worker：完整运行中真命题PROVED、假命题完整否定REFUTED、错误方向UNKNOWN；另一运行在真实E4门禁后取消，对外结果仍UNKNOWN且整轮无求解率。fixture只供应计划，全部数学判断来自实际候选检查和独立进程重放。夹具的2/6仅检查覆盖场景，不是模型能力分数。

覆盖固定预算/一次尝试、参考答案与见证不进入请求、目标和生产者绑定、无配置先行拒绝、空训练组、预留组显式选择、拒绝作答/超时/错误均保留在分母、失败请求用量不完整、worker锁、历史模型请求/Lean工件/计划/损坏JSON/题目列表/生产者分类改变时抑制求解率。取消后的部分运行不出分数。

运行前后71个源码/配置摘要一致。Ruff49个Python文件lint/格式通过，源码门禁64个Python/JSON文件通过。CLI额外5条实际命令通过：默认计划、显式全部组计划、未配置运行拒绝、完整fixture历史及取消历史读取。Windows到WSL的四条工作台页面/资源/session均200，模型仍未配置、worker无错误。浏览器界面未修改，未重复原页面视觉验收。

第一次62项回归也通过，109.496秒；随后复核加固了报告JSON损坏和题目列表/生产者核对，保留初次记录 `model-evaluation-before-reader-hardening-*`，最终用同一组62项再次验证。没有在测试运行期间改动源码。

## 工件与边界

最终主记录：`artifacts/model-evaluation-validation.json`，日志：`model-evaluation-tests.log`，质量：`model-evaluation-quality.json`，CLI：`model-evaluation-cli-demo.json`，服务：`model-evaluation-service-health.json`；最终交付检查另见 `model-evaluation-delivery-check.json`。两个测试运行目录如下：

- `artifacts/model-evaluations/nat-plans-69b6502e80af4a05b9b93b8ca7c383ec`：fixture完整运行，5个Lean worker，model_solve_rate=null。
- `artifacts/model-evaluations/nat-plans-fc8174c8657644a382b90b2b67a5601a`：真实门禁后取消，2个Lean worker，model_solve_rate=null。

现有Lean/Mathlib/策略/模板和运行快照901dbf78492acf4cb255e744b2affac16ad2789a0a986981da55edb18530d107不变，无新依赖、下载、模型配置或封存放宽。工件摘要不是持久签名，姓名及作者映射不是身份认证或独立专家审核。历史报告不作为当前环境新认证。

固定数字预算与作者参考一致，但作者证明和四种模型策略的能力限制不同；没有实测即可成立的公平性能比较。自然语言翻译准确率、真实模型效果、盲测、论文审稿和知识积累仍待后续。
