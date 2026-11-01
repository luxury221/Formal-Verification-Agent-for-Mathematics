# 本地 HTTP 工作台协议 v1

设计：ADR-012；统一页面构造见 ADR-015、[工作区协议](workspace-ui-v1.md)。实现：mra/http_api.py、mra/web/。在 WSL 项目根目录执行：

```bash
python3 -m mra serve --port 8765
```

浏览器打开 `http://127.0.0.1:8765`。服务固定监听该回环地址；端口0可由系统分配，启动输出显示实际 URL。已有排队的 CLI/HTTP 任务由单 worker 顺序处理。Ctrl+C 停止接收请求并等待当前有限尝试完成；显式取消请使用页面取消按钮或 CLI。崩溃恢复不自动重试。

| 方法 / 路由 | 行为 |
| --- | --- |
| GET / | 研究总览 |
| GET /verify | 原固定目标验证页面 |
| GET /ideas | 自然数猜想探索，见猜想协议 |
| GET /typed-research | 类型／定义研究图及当前节点的证明复用面板；见[类型研究协议](typed-project-research-v1.md)与[复用HTTP协议](typed-reuse-web-v1.md) |
| GET /papers、/library | 单定理审查与私有知识库；接口分别见[审查协议](paper-review-v1.md)和[知识协议](knowledge-v1.md) |
| GET /evaluations、/activity、/workflow、/settings | 评测中心、真实研究记录、产品全景及设置 |
| GET /v1/evaluations、/v1/evaluations/{id} | 只读计划/历史元数据与核对摘要后的详情，见工作区协议 |
| GET /v1/session | 本次 CSRF token、两个目标的原文/形式化/假设/指纹、作者证书示例及 worker 状态；不运行 Lean |
| GET /v1/tasks | 最近30项执行元数据，包含 task_id/case_id/kind/state/created_at，不包含数学判断 |
| POST /v1/tasks | 幂等入队；返回202及当前完整任务状态，后台执行 |
| GET /v1/tasks/{id} | 现有持久报告、进度事件、尝试与工件状态 |
| POST /v1/tasks/{id}/cancel | 请求体为{}；返回当前任务，取消可能等待阶段边界 |
| POST /v1/tasks/{id}/retry | attempt_id和request_id；按原合同显式重试，最多3次总尝试 |
| GET /v1/tasks/{id}/evidence | 最新已提交尝试的报告与回执，工件完整时available=true；历史记录，不是新签发 |

写请求必须 `Content-Type: application/json`、唯一 Content-Length、Origin 为启动 URL，并提供 `X-MRA-CSRF`，值来自同源 session。服务重启后旧 token 无效。Host 必须为实际 `127.0.0.1:端口`；不接受 localhost 别名、跨源/跨站或代理转发。没有 CORS、任意文件路由或来源路径参数。所有响应 no-store；CSRF token 不是用户身份认证。

提交严格要求以下八个字段：

```json
{
  "request_id": "caller-chosen-stable-id",
  "case_id": "nat_add_zero",
  "input_mode": "repository-lean-reference-v1",
  "certificate": null,
  "timeout_seconds": 30,
  "claim_fingerprint": "session中显示的确切值",
  "formalization_fingerprint": "session中显示的确切值",
  "alignment": null
}
```

证书模式 input_mode 为 `kernel-expression-certificate-v1`，certificate 为原始 JSON **字符串**，不能预先解析成对象；这样重复键等无效输入仍由证书解析器拒绝。请求体最多192 KiB，内部证书仍最多64 KiB。预算为有限的(0,120]秒，候选与重放共用；环境内容核对另计。

语义对齐为null时，检查通过也仅UNKNOWN/E3。明确确认时设置 `alignment={"reviewer":"确认者姓名","confirmed":true}`，两个目标指纹必须仍与显示版本相同。确认后重新提交会产生新检查任务；旧 E3 任务不会被原地修改。此声明与 CLI reviewer 同样为本地姓名，不能冒充身份认证或使用其他版本的审核。

提交 ID 与 CLI 共用命名空间。原始输入经规范化后，内容相同的重复提交返回同一任务的现状，可能已经完成或取消；202不保证任务仍在排队。格式差异保持幂等，模式、证明、预算或审核者变更需要新 ID。完整队列语义见 `tasks-v1.md`。

错误返回JSON的error。输入/合同错误400、Host/Origin/token错误403、未知路由404、长度缺失/重复411、正文过大413、类型错误415、读取时发现工件变化409、暂不可用的存储/worker503。未知任务由现有 TaskStore 报输入错误400。未实现的方法由标准HTTP处理器返回501。处理异常不赋予数学标签。

页面展示任务历史时核对已记录摘要，fresh_attestation恒为false；取消或恢复未提交结果的证据接口返回available=false。需要新认证时重新运行，不从遗留 report.json 恢复E4。该协议当前只开放两个固定目标的新提交，历史查询仍可查看 CLI 创建的其他批准参考。

## 知识实例化扩展

新增 `/v1/knowledge-instances` 与 `/v1/instance-runs` 系列路由、独立第七账本和共用 worker 调度；完整输入、确认、来源撤销与 CLI 见 [knowledge-instances-v1](knowledge-instances-v1.md)。原本机 Origin / Host / CSRF / 正文限制继续适用，冻结验证协议不变。
