# 人工标注单定理审查 v1

日期：2026-10-25。设计：[ADR-016](../adr/0016-annotated-single-theorem-review.md)。页面：<http://127.0.0.1:8765/papers#single-theorem-review>。

## 使用

1. 在论文页下方的“单定理审查”选择完整步骤、真命题但错误证明、缺少条件三个作者示例之一，或在表单中填写自己的原文、变量、前提、结论、行号和步骤。可切换结构化标注 JSON，切换须无损。
2. 点“准备检查义务”，查看带行号的原文和每个确切检查公式。准备不启动 Lean，也不调用模型。
3. 逐项核对变量、全部前提、结论、步骤与引用，填写姓名并确认。
4. 启动真实审查，分别查看命题与作者步骤的结果。点击检查工件读取真实回执；可取消、撤销确认或导出记录。

页面上方原有阅读示例和会话草稿仍独立保留；可明确点击“使用上方文本草稿”带入原文，超过12000字符会拒绝且不截断；本阶段不会从草稿自动抽取标注。结构化标注准备/提交会送到本机服务，原文、确认和记录保存在 `artifacts/paper-reviews/reviews.sqlite3`；不发送给模型。每个已提交审查固定原文版本，修改后新建并重新确认，不复用旧声明。

## 标注

完整格式参考 `evals/paper-review-v1/*.json`。`schema` 为 `nat-paper-review-v1`，整体最多 64 KiB：

| 字段 | 含义 |
| --- | --- |
| document | id、version、title、text；正文最多 12000 字符 |
| variables | 最多四个不同的自然数变量名 |
| assumptions | 最多四项显式公式；所有检查均保留这些全局前提 |
| conclusion | 被审命题的结论公式 |
| claim_span | 命题所在的起止行号，1 开始，包含尾行 |
| claim_plan | 命题的检查方向/受限策略/见证；null 表示不检查 |
| steps | 最多四步，每项含 id、span、formula、depends_on、plan |

公式采用现有 `nat-formula-v1` 构造，见 [猜想协议](ideas-v1.md)。变量由公共上下文绑定；内部量词仍受作用域、256 节点/24 层及数值范围限制，生成的完整义务也须满足限制。`depends_on` 只能引用较早的步骤 ID，不支持未验证外部引理。空引用意味着只使用原文全局前提。`plan` 采用既有 prove/refute 与 omega/simp/decide/counterexample；没有自由 Lean 源或战术。

命题与每步分别最多一次候选检查和独立重放，每项共用 30 秒 Worker 预算；整轮最多五项，环境核对另计。没有模型调用、隐式重试或自动修复。计划未提供或引用尚未验证时跳过对应义务，不能用假设该步骤成立的方式获得后续严格结论。

## 两项结论与诊断

- `claim_verdict` 为独立命题检查的 PROVED / REFUTED / UNKNOWN。
- `author_proof_verdict` 为标注步骤全部成立并达到结论后的 PROVED、具体缺口 GAP_FOUND，或 UNKNOWN。
- `INFERENCE_REFUTED`：指定步骤推理的完整否定已通过 Lean 与语义对齐，附原文行号和确切义务。
- `CONCLUSION_NOT_REACHED`：标注的最后一步未陈述原结论，属于结构性缺口，不是命题反例。
- `UNRESOLVED_OBLIGATION`：缺计划、依赖未验证或检查未成功，原因尚不能定性为数学错误。

确认绑定完整计划指纹，包括原文版本、公式、引用、检查方向与审查实现版本。确认仅是本地具名对齐；结果不认证整篇论文、所有自然语言细节或作者身份。运行状态与数学判断分别提供。取消、撤销、崩溃恢复、工件或协议变化时，两项对外判断均停用为 UNKNOWN；原检查记录继续作为历史保存。

## HTTP 与 CLI

沿用精确 Host、同源 Origin、CSRF、192 KiB 正文上限、CSP 与 no-store。

| 方法 / 路由 | 行为 |
| --- | --- |
| GET /v1/paper-examples | 三个独立编写的文本/标注，明确标注作者来源 |
| POST /v1/paper-reviews/prepare | `{bundle}` → 可见原文、义务、预算和 plan_fingerprint；不执行检查 |
| POST /v1/paper-reviews | request_id、bundle、plan_fingerprint、reviewer、confirmed=true；幂等入队 |
| GET /v1/paper-reviews | 最近三十条执行元数据，不含数学结论 |
| GET /v1/paper-reviews/{id} | 复核工件后的双通道报告 |
| POST /v1/paper-reviews/{id}/cancel | `{}`；取消排队或正在运行的审查 |
| POST /v1/paper-reviews/{id}/revoke | `{}`；撤销确认并停用对外结论 |
| GET /v1/paper-reviews/{id}/evidence/{key} | 完整且有效时读取 claim 或步骤 key 的历史检查回执/报告 |

```bash
python3 -m mra paper-review-plan --input evals/paper-review-v1/true-claim-bad-proof.json
python3 -m mra paper-review --input evals/paper-review-v1/true-claim-bad-proof.json --plan-fingerprint '<上一步显示的指纹>' --reviewer '<姓名>' --request-id '<本次稳定ID>'
python3 -m mra paper-review-report '<返回的 paper-id>'
```

CLI 运行同样持有共享 worker 锁。已有后台 worker 领取时可读取报告等待，不通过创建新请求 ID 实现隐式重试。备份应在停写后同时保存数据库、原文标注与引用的 `artifacts/runs/`；导出 JSON 不替代权威账本或持久签名。

## 表单公式（2026-10-26）

直接输入 `n + 2 > n`、`n * m = n * k`、`(n = 0) ∨ (0 < n)`；也接受 `<= >= != && || -> !`。加/减先于比较，乘法先于加/减，比较先于合取/析取/蕴含，蕴含右结合。括号可明确分组；量词可写 `forall n, ...` 或 `exists k, ...`。变量须明确声明，不能重复遮蔽。减法为自然数截断减法，不接受负数、除法、幂、LaTeX或隐式乘法。

公式仅转换为既有AST，不执行输入代码。表单解析最多4096字符、512记号、256节点及有界嵌套，服务端继续独立限制完整义务。只接纳现有四种受限策略；未选计划保留UNKNOWN。表单本地校验不构成语义对齐或数学证明。

任何原文或标注改动清除已准备计划与复选确认；非法内容不得退回最近有效JSON提交。移除步骤不会默默删去后续引用；必须由用户明确调整。无法无损表示的JSON保留在原模式；导出下载当前精确标注，不启动检查。详见[ADR-018](../adr/0018-guided-paper-annotations.md)和[验收](../evaluation/annotation-editor-results.md)。

## 整数域扩展（2026-10-26）

`int-paper-review-v1` 使用同一字段结构，变量、常量、减法与量词均解释为Int。数字范围为[-1000000,1000000]，新增 `{op:"neg",body:...}`，反例按量词顺序接受有界负整数。对应 `int-formula-v1` 与 `int-bounded-search-v1`，工件 `input/int-search.json`、回执 `int_bundle_digest` 和受信Int模板；Nat字段/协议保留且拒绝负数。

GET `/v1/paper-examples` 保持三份Nat样例；新增GET `/v1/paper-examples/integers` 返回三份自有整数样例，准备/提交/报告/取消/撤销及CLI命令共用原入口。准备返回明确domain，审核和证据指纹绑定域，不能换标签复用。

表单显式选择域，切换清除准备与确认，不自动改写原文对象范围；用户需重新核对。整数负数和取负使用同一有界公式解析；一般非线性问题不保证解决。协议实现变化导致旧记录结论停用，原始报告和工件保留。详见[ADR-019](../adr/0019-typed-integer-verification.md)与[实测](../evaluation/integer-results.md)。
