# 私有引理组合网页与 HTTP v1

底层数学协议为冻结的 `private-lemma-composition-v1`，见[组合核心](lemma-composition-v1.md)。本轮新增传输和交互，不改其已封存数学语义。

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/v1/lemma-compositions/catalog` | 有效私有源、22条受控引用及数量限制 |
| GET | `/v1/lemma-compositions/sources/{id}` | 重核归档，返回完整签名、版本、对齐与出处；不返回原证明、Lean源或NDJSON |
| POST | `/v1/lemma-compositions/preview` | 原组合请求；完整目标/条件、每次同时代入及来源快照，UNKNOWN/E0；不执行 |
| POST | `/v1/lemma-compositions/runs` | request_id/request/plan_fingerprint/reviewer/confirmed；仅入队并唤醒单worker |
| GET | `/v1/lemma-compositions/runs` | 最多200条历史的当前证据状态 |
| GET | `/v1/lemma-compositions/runs/{id}` | 重核来源和工件的完整历史 |
| GET | `/v1/lemma-compositions/runs/{id}/export` | 同一重核门禁后的完整JSON；不接受文件路径 |
| POST | `/v1/lemma-compositions/runs/{id}/cancel` | 严格空对象；取消不发布数学结论 |
| POST | `/v1/lemma-compositions/runs/{id}/revoke` | reviewer/reason/confirmed；持久停用原确认 |

POST沿用精确同源Origin与CSRF、严格重复键拒绝、192KiB上限。额外字段、错误完整目标、未知来源与旧计划指纹拒绝。临时worker/SQLite忙返回503，不由传输层改写为证据损坏，不在服务端自动重复提交。幂等语义与核心一致，同ID改变确认正文拒绝。

网页 `/compositions` 提供1–4来源卡片、Nat公式表单和证明树：原前提、私有引理/库引用、合取和等价两方向。全部量化实参与原前提证明按顺序显式填写。表达式为var/nat/add/mul/sub/gcd，命题为dvd/eq/le/coprime/and/or/iff；每次证明输入由服务端重新完整检查。前提可写 `h1: a | b`。自然数减法截断，最多6变量/6前提。

预览显示完整全称目标、原意、变量映射、来源版本/指纹及各次应用的实参和实例化原条件。编辑目标、证明、来源、别名或姓名清旧确认；异步预览/来源读取绑定编辑代次，迟到结果不恢复确认。姓名是本地声明，不是认证身份。

页面请求串行；GET的503最多重试两次，失败显式手动恢复；证据详情暂不可读时收起旧判断。POST一次点击只发送一次。不确定提交保留原正文和ID，允许人工重提；编辑后新ID及重新确认。后台只有一个共享全局锁的worker；候选检查和独立重放两个真实进程，预算沿用1–30秒共享worker时间，无自动证明重试。

运行状态、阶段、数学判断和证据等级独立显示。导出前重新核对；历史`fresh_attestation=false`。取消、超时、错误前提或中断保持UNKNOWN；来源撤销/变更、工件漂移持久停用。页面只轮询当前活动记录，历史列表由用户刷新，不持续全库扫描。

尚未接入研究图关联、组合结果再归档、一般数学类型、归纳、模型规划或自主发现。两个真实进程仍使用同一Lean内核实现。
