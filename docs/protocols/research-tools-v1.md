# 研究工具循环 v1

沿用 `POST /v1/studio/{study}/generate` 的 `operation: "step"` 与用户选定的 backend。无需新的外部API协议：工具请求是既有 JSON 文本响应的一种分支，不使用供应商原生 `tool_calls`。

模型可以返回既有完整 round JSON，或：

```json
{
  "source_fingerprint": "copy input",
  "plan_fingerprint": "copy input",
  "node_id": "copy input",
  "tool_request": {"kind": "search_mathlib", "query": "norm_inner_le_norm"}
}
```

另一工具为 `{"kind":"preview_formalization","formalization":{"backend":"用户选择的后端","request":{...完整候选图...}}}`。最外层仍必须包含相同三个身份字段。不能传 shell/Python/任意 Lean、下载地址、其他后端或证明提交请求。原题、定义和全部原始条件始终随每次模型输入提供，不因预算而静默删减。

`tool_session` 给出工具说明、剩余模型调用和工具次数，以及本轮历史结果。最多4次模型调用、3次工具请求、5个检索候选/次。归一化重复检索返回诊断并消耗工具次数，避免盲目循环。最后一次模型调用必须返回最终提案；继续请求工具则终止并保留部分记录。

检索结果包含固定源候选及现有类型缓存。类型缓存 READY/AMBIGUOUS/NOT_FOUND 保持原意，其他状态不伪造类型。每次后续模型调用前，重新核对本轮命中的确切源码；来源变化的片段从可用输入移除，并附 unavailable ID/原因。历史归档仍保留当时字节。不会自动把模型发现的候选加入用户引用。

表达预览先检查完整候选的原题与后端身份，再调用已有后端 preview。返回 REPRESENTABLE 仅表示受控表达结构可编译；DIAGNOSTIC 包含结构、来源或表达错误。二者都不表示命题真假、自然语言语义对齐或证明通过。预览不创建图、不启动 Lean。最终候选仍走原完整预览、核对人确认、保存和显式真实检查流程。

整轮预算：90秒模型/工具循环，8192个供应商报告的输出token，128KiB累计模型输出字节，每次完整输入100000字符。每本最多16次模型调用（含失败的调用尝试）；模型/模拟计数分开，输入token可能在多个调用重复计费。上下文准备、归档I/O及源码刷新会产生额外墙钟开销。超限明确失败，原题不被削弱。没有网络/模型自动重试或自动数学检查。

运行记录增加 `loop_version/calls/tools/trace_files`。完整请求、响应、工具事件存于 `artifacts/research-studio/{study}/{operation}/`，通过 SHA256 绑定。开始操作前检查事件余量及主记录空间：距离2MiB上限不足256KiB时拒绝，尚不进入RUNNING，也不调用模型。每次CALL_STARTED在调用供应商前记账，中断不能逃避调用预算。旧记录按原先一次调用兼容读取，不迁移或修改既有证明。

`GET /v1/studio/{study}/trace/{operation}` 返回带哈希核对的历史事件，网页“查看本轮研究过程”呈现检索、表达诊断与完整输入/输出。记录含私有研究原文，沿用本地私有工作区处理，不包含API密钥。工件损坏仅使过程读取失败，原题仍可打开；过程本身不授予数学证据。

取消与恢复沿用既有接口。模型和工具子进程继承原生命周期锁；任务仍在运行时不能恢复为中断。取消丢弃最终提案、保留过程记录；确认原子归档后才发布最终round。整个工具循环仍UNKNOWN/E0。
