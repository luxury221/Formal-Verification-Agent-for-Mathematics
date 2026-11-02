# 全文自动处理 v1

打开 `/automation` 上传 HTML / LaTeX / PDF，或从论文导入页进入“自动处理全文”。HTML / LaTeX
优先，PDF 文本及英文 OCR 仍沿用导入器的范围与限制。提取完成后，阅读、机器语义核对、
形式化、真实检查、有限修复与报告自动接续，无需逐项创建人工义务或点击送检。

## 配置与启动

模型沿用 [`config/model.example.json`](../../config/model.example.json) 的本地配置约定；见
[模型说明](ideas-v1.md)。密钥来自所配环境变量，不进任务文件或命令行。服务兼容非流式
Chat Completions、JSON 响应和 usage；任务固定操作者配置的模型名称与 revision，另记服务实际
返回的模型名称和 token 用量。远端模型版本没有独立认证。不设默认付费接口，不自动切换提供方。
本轮不替用户配置模型或调用付费服务。

未配置时任务进入 `WAITING_FOR_MODEL`，保留真实原文与未完成覆盖清单；配置后点击“恢复任务”。
传输中断、程序重启同样需要显式恢复；取消为终止操作。更换模型配置或修改原文/实现后应
新建任务，不能混用旧任务的固定输入。真实检查仍需本机已准备的相应 Lean / Mathlib 环境。
重启时 worker 会等待旧子进程释放继承的租约，再接管队列；这只重试取得所有权，不会重复
发送结果不明的模型请求。已提交的完整响应可在显式恢复后复用，未知消耗的预留仍保留。

## 接口

| 方法 / 路径 | 行为 |
| --- | --- |
| `GET /v1/paper-automation/capabilities` | 模型配置状态、预算上限和语言范围 |
| `GET /v1/paper-automation` | 简要任务清单 |
| `POST /v1/paper-automation` | 固定 `request_id, document_id, expected_document_revision, budget` 后排队 |
| `GET /v1/paper-automation/{id}` | 当前报告、原文覆盖、义务、历史候选证据与缺口 |
| `POST /v1/paper-automation/{id}/cancel` | `request_id, expected_revision`；取消自动任务 |
| `POST /v1/paper-automation/{id}/resume` | 同上；仅恢复等待模型或中断的任务 |
| `GET /v1/paper-automation/{id}/export` | 导出报告 JSON |
| `GET /v1/paper-automation/{id}/artifacts/{encoded-name}` | 读取报告已登记、哈希匹配的 JSON 工件；文件名需 URL 编码 |

全部写接口继承 Host、同源 Origin、CSRF、严格 JSON 与版本检查。相同业务请求 ID 不会创建
重复任务或调用；不能为不同输入复用 ID。不接收 Lean 源码、shell、审核员姓名或确认签名。

## 边界

- 只覆盖提取器输出的文字，不认证提取完整性；每个区间都有覆盖条目，精确引文使用完整
  block 的 Unicode 码点下标，非字节或 UTF-16 下标。长 block 的完整上下文不能被局部片段替代。
- 一次阅读最多 8 项完整义务；每项保留对象、定义、量词、全部前提、所有结论、引用与问题。
  超限应显式报告，不能截断条件。依赖是来源建议；循环、缺失或未闭合依赖不能冒充已证前提。
- 自动语义核对分八个维度，必须全部接受。单独调用不代表独立模型或独立专家。
- 支持已有 Nat / Int / Rat 定义、部分实分析与结构证明语言。记录任意数学不等于能表达或
  证明任意数学；外部文献、缺失定义和复杂 PDE 可以作为明确缺口返回。
- 成功候选依次通过真实沙箱、精确目标与公理审计、独立进程 NDJSON 重放和受监视字节检查。
  读取报告会核对历史文件；不执行新的 Lean，也不把历史成功当作当前重新验证。
- 任务可 `COMPLETED` 或 `COMPLETED_WITH_GAPS`；两者都保持原文 `UNKNOWN / E0`、全文未认证。
  没有人工确认，也没有自动接入旧的人工证据账本。
- 预算累计预留不回收；提供方失败也可能计费。活动时间包含处理与扫描，暂停时间除外；
  崩溃后未能确认的活动时间保守计入。预算耗尽后需新建任务，不静默提高预算。

`artifacts/paper-automation/paperauto-*/` 保存固定 source、紧凑 record 及单独的 calls、readings、
obligations、candidates、kernel 工件。大运行时清单保存指纹与封存引用，不复制或重新封存。
