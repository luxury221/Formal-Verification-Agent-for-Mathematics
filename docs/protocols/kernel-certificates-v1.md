# 固定目标内核证书协议 v1

设计：ADR-010。宿主校验：mra/certificates.py。受信模板：lean_workspace/certificate-checker.lean.in。验证后续沿用 kernel-replay-v1.md。

在 WSL 项目根目录执行：

```bash
python3 -m mra certificate-target nat_add_zero
python3 -m mra check-certificate nat_add_zero --input evals/certificates/add-zero.json
python3 -m mra check-certificate nat_add_zero --input evals/certificates/add-zero-lambda.json --reviewer '你的名字'
python3 -m mra check-certificate false_all_nat_zero --input evals/certificates/not-all-zero.json --reviewer '你的名字'
```

certificate-target 不启动 Lean，返回原始陈述、精确形式化、方向及 certificate_header，状态为 UNKNOWN/E0。调用者在头部增加 proof。初期仅启用上述两个目标；false_all_nat_zero 的被检查表达式是 ¬ (∀ n : Nat, n = 0)，原文保持“所有自然数都等于零”。具名审核仍是本地对齐声明。

证书顶层严格要求五个字段：schema_version="1"、format="kernel-expression-certificate-v1"、claim_fingerprint、formalization_fingerprint、proof。两个指纹必须与描述中返回的头部完全一致；schema、原文/假设/版本、方向或形式化变化需要新证书。

| 构造 | 严格字段 | 含义 |
| --- | --- | --- |
| const | op, name, levels | 已存在的受信环境常量；levels 为具体宇宙层级数组，不做隐式参数推断 |
| app | op, fn, arg | 函数应用，参数必须显式给出 |
| lam | op, type, body | 显式 lambda 绑定，类型与 body 均为表达式 |
| forall | op, type, body | 显式依赖函数/全称类型绑定 |
| bvar | op, index | 从 0 开始的 de Bruijn 索引，0 指最近的绑定；必须在作用域内 |
| nat | op, value | 非负 Nat 字面量 |
| sort | op, level | Sort 的具体宇宙层级；Prop 对应 0 |

例如，proof 可直接是 `{"op":"const","name":"Nat.add_zero","levels":[]}`。另一份证书使用 lambda 调用同一引理，展示实际接受了调用者提供的不同证明项。反驳证书构造 h : ∀ n, n=0，再将 h 1 交给 Nat.one_ne_zero，证明完整否定。

名称只允许由 ASCII 字母/数字/下划线组成的点分段，每段以字母或下划线开头，最长 192 字符。自然数 ≤2³²−1，宇宙层级及层级数组长度 ≤8。原始文件和规范化 JSON 均 ≤65,536 字节，proof ≤2,048 节点、深度 ≤64。拒绝额外字段、重复键、布尔值充当整数、非标准 JSON 数值、未绑定变量和任意其他节点。

证书内容保存在 input/certificate.json；input/Candidate.lean 是宿主生成的受信模板，只插入固定目标，不插入用户证明源。第一阶段构造 Lean.Expr 并经内核检查，只有通过后才导出到第二阶段。输入模式写入 request/receipt，certificate_digest 是规范化证书（含末尾换行）的 SHA-256，并进入工件摘要。成功的证书运行有 11 个回执文件：原 5 个、证书 1 个、重放 5 个。

审计必须包含 input_mode=kernel-expression-certificate-v1 与 candidate_source_executed=false。EvidenceGate 重新验证保存的证书头和摘要，再执行原有两进程、公理、目标及对齐门禁。不能靠一段自报 PASS 或新造的日志晋升证据。

格式/版本/作用域错误在启动 Worker 前拒绝，CLI 退出 1；不存在/unsafe/partial 常量为 POLICY_BLOCKED/UNKNOWN；内核类型拒绝为 COMPLETED/UNKNOWN/E2，CLI 退出 0 仅表示流程完成。TIMEOUT 等非正常运行退出 2，数学判断仍 UNKNOWN。取消不发布证据。默认 30 秒由候选与重放 Worker 共用，完整性检查另计。

ADR-011 已将此协议接入持久任务：`task-submit-certificate <case-id> --input <json> --request-id <id>` 保存规范化正文，不保存待执行的输入路径；随后使用 `task-work/report/cancel/retry/recover`。详细状态、幂等与恢复合同见 [任务协议](tasks-v1.md)。同步 `check-certificate` 仍可使用。

本轮不支持用户定义目标、任意 Lean 源、tactic 搜索、自动生成、自动修复、用户身份认证或持久签名。两个进程仍使用同一 Lean 内核实现。示例与参考验证均不产生模型求解率。
