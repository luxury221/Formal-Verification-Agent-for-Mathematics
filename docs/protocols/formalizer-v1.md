# Formalizer v1 接入约定

请求/响应 DTO 见 `mra/model_service.py`，候选与审核类型见 `mra/formalizer.py`。调用 `StructuredFormalizer(service).formalize_claim(source, budget)` 或 `formalize_step`，后者保留原文和上下文而不宣称原作者步骤成立。`suggest_clarifications` 返回当前草稿的问题，不隐式重试或发起第二次调用。

`ModelRequest` 包含唯一 ID、task、input_json、budget 和 `output_schema=formalizer-output-v1`。input_json 是以下内容的规范 JSON，源指纹绑定全部源字段：

```json
{
  "source": {
    "schema_version": "1",
    "id": "example-claim",
    "version": 1,
    "statement": "对任意自然数 n，有 n + 0 = n。",
    "context": []
  },
  "source_fingerprint": "<source.fingerprint>"
}
```

`ModelService.generate` 返回 `ModelResponse`，其中 request_fingerprint 必须等于实际请求指纹。生产者的 provider/model/revision/kind 与 token usage 从适配器取得，不从生成文本读取。revision 应为已解析版本，拒绝 `latest/main/master/HEAD`。kind 必须区分 `model` 与 `fixture`。

生成文本必须是一个 JSON 对象，包含且只包含以下字段：

```json
{
  "schema_version": "1",
  "source_fingerprint": "<source.fingerprint>",
  "status": "CANDIDATES_READY",
  "candidates": [
    {
      "label": "自然数加零",
      "lean_statement": "∀ n : Nat, n + 0 = n",
      "variables": [{"name": "n", "domain": "Nat", "quantifier": "forall"}],
      "assumptions": [],
      "interpretation": "对每个自然数 n，n + 0 等于 n；没有附加假设。"
    }
  ],
  "questions": []
}
```

候选最多三条，variables 最多 32 项，quantifier 为 `forall/exists/parameter`。无自由变量的命题可用空变量列表。assumptions 显式列出提议的前提，不能默默增强假设或弱化结论；人工审核应同时核对原文、Lean 表达式和这些元数据。不会用字符串检查冒充语义等价证明。

| 内容状态 | 约束 | 数学判断 |
| --- | --- | --- |
| CANDIDATES_READY | 1–3 个候选，无未解决问题 | UNKNOWN |
| NEEDS_CLARIFICATION | 1–3 个明确问题，可带备选候选 | NEEDS_CLARIFICATION |
| UNSUPPORTED | 无候选、无问题 | UNKNOWN |

传输异常用 `TimeoutError` 或 `OSError`，无效的供应商回包用 `ValueError`。运行失败由 Formalizer 记录，不允许生成 JSON 自报运行成功、Lean PASS 或 E3/E4。默认预算为 30 秒、序列化输入 16,000 字符、输出 2,048 token 和 65,536 字节；具体网络/进程取消与服务端 token 限制由适配器执行。超限/逾期结果不会产生候选，异常不自动重试。

调用者可以使用 `draft.to_dict()` 获取原文、请求、状态、候选指纹与来源。所有输出的 `evidence_level=E0_CANDIDATE`、`can_execute_lean=false`。`CandidateReview` 包含 draft_fingerprint、candidate_fingerprint、reviewer、decision（accept/reject）、note；接受候选仅记录语义审核声明，不产生 `PROVED`、`REFUTED` 或 `Alignment`。

本地工件位于 `artifacts/formalization/<uuid>/`，由 CLI 生成、查看和追加审核：

```bash
python3 -m mra formalize --input evals/formalization/clear-nat.json
python3 -m mra formalize --input evals/formalization/ambiguous.json
python3 -m mra formalize --input evals/formalization/unsupported.json
python3 -m mra formalization-report 'artifacts/formalization/<id>'
```

查看原文、变量、假设和确切表达式后，用输出中的草稿和候选指纹记录审核：

```bash
python3 -m mra review-formalization 'artifacts/formalization/<id>' \
  --draft-fingerprint '<draft_fingerprint>' \
  --candidate '<candidate_fingerprint>' \
  --reviewer '你的名字' --decision accept \
  --note '已核对自然数定义域、全称量词、假设和结论。' \
  --event-id '<调用前保存的唯一请求ID>'
```

将示例歧义澄清为自然数时，使用同一源 ID 的版本 2，绑定前一个歧义工件：

```bash
python3 -m mra formalize --input evals/formalization/clarified-nat.json \
  --revise 'artifacts/formalization/<歧义工件id>'
```

CLI 的 fixture 只认识文件中确切原文/上下文。普通命题不支持不是反驳；人工拒绝候选也不是反驳。内容状态正常返回时退出码为 0；运行超时/策略阻止为 2；输入、版本或审核参数错误为 1。旧的 `check` 命令继续使用固定仓库案例和真实 Lean 门禁。

## 当前审核状态与撤销

审核与撤销响应包含 `event_id`、事务 `sequence`、实际 UTC 时间及 `review_state`。`review_status` 描述这条历史事件；当前有效性应读 `review_state.candidates`。`formalization-report` 保留草稿原有顶层字段，并增加以下状态：

| status | 含义 |
| --- | --- |
| UNREGISTERED | 账本未注册，无有效审核；原始工件仍可查看 |
| UNREVIEWED | 当前版本未审核 |
| ACCEPTED / REJECTED | 最新审核有效；仍为 UNKNOWN/E0 |
| REVOKED | 最新审核已撤销；此前审核不恢复 |
| SUPERSEDED | 源已有新版本；该草稿所有审核均无当前效力 |

每个候选同时显示 `last_review_status`、`last_review_id`、`effective_review_id`、`accepted`。`review_state.history` 从 SQLite 账本按事务序号读取，包含已撤销/已过期的历史事件；`source_head` 和 `revision_parent` 展示当前源版本和本次显式修订父目录。不同生成请求和不同候选的审核不会自动移用。

撤销指定的审核，而不是删除审核文件：

```bash
python3 -m mra revoke-formalization 'artifacts/formalization/<id>' \
  --draft-fingerprint '<draft_fingerprint>' \
  --candidate '<candidate_fingerprint>' \
  --review-id '<审核返回的event_id>' \
  --reviewer '原审核者名字' --note '撤回本次审核的原因。' \
  --event-id '<本次撤销的唯一请求ID>'
python3 -m mra formalization-report 'artifacts/formalization/<id>'
```

撤销须绑定原审核者姓名和确切内容。姓名是本地声明，没有身份认证；输出明确标记 `actor_authentication=local-assertion-only`。同一候选取最新审核，撤销最新审核不会恢复更早声明；撤销较早声明也不会影响较新声明。多个本地审核者使用同一事务顺序，没有投票或权限策略。

`--event-id` 可选，省略时自动生成；重试时应传入首次调用前已保存的相同 ID 和参数。相同 ID/内容只产生一次事件，不同内容拒绝。事务提交与 JSON 导出分开，若导出失败或进程中断，应先查看报告再用同一 ID 重试。草稿目录内的 `reviews/*.json` 是历史导出，状态归并不读取它们，不能通过复制或编辑 JSON 获得接受。

初始源版本须为 1；同一源 ID/版本不能有不同内容。`formalize --revise` 会持久化父关系并原子推进当前源版本；所有旧版本草稿转为 `SUPERSEDED`，新版本从未审核开始，旧源不能再收到新审核。旧版本仍可撤销已有声明，幂等重试也只返回历史事件。

旧版工件不会自动把历史接受变成当前授权。查看时标记 `UNREGISTERED`，注册后重新明确审核：

```bash
python3 -m mra register-formalization 'artifacts/formalization/<旧版v1目录>'
python3 -m mra register-formalization 'artifacts/formalization/<旧版v2目录>' \
  --revise 'artifacts/formalization/<已注册v1目录>'
```

文件写完而注册事务失败的孤立草稿同样可以检查后注册；不完整/已篡改的工件不能注册。旧 JSON 审核保留为历史文件，不导入有效状态。停止写入后应将 `review-state.sqlite3` 与草稿目录一起备份，丢失数据库不能只靠导出 JSON 恢复授权。设计和可信边界见 ADR-006。
