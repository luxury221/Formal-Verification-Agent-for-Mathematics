# 显式类型与定义接口 v1

模式 `typed-definitions-v1`。首次交付为 CLI／Python 接口，存储 `artifacts/typed-definitions/lab.sqlite3` 和同目录 `runs/`；不进入旧研究图、知识库或网页队列。

`typed-catalog` 离线显示完整示例和语义边界，不初始化账本或运行 Lean。`typed-preview --input target.json` 编译原任务、完整定义、全称变量、全部前提与结论，返回 `UNKNOWN/E0`、宿主 Lean、固定策略、来源清单、预算和 `plan_fingerprint`；同样不建账本或执行 Lean。

顶层必须恰含 `schema/title/original/definitions/variables/assumptions/goal/strategy/timeout_seconds`。原文和定义说明保持原字节文本，包括首尾空白；修改后须重新确认。输入最大192KiB，表达式共320节点、16层；至多6定义、8全局变量、8前提，每定义至多4参数，每量词至多4绑定。字段名、重复键、类型、数值／预算边界严格检查。

类型绑定为 `{"name":"x","type":"Rat"}`；名称限1–20个小写ASCII字母／数字／下划线、首字符字母。Lean 名字由宿主生成，不将名称或说明当源代码。类型只允许 Nat、Int、Rat；定义结果也可 Prop。定义格式如下：

```json
{
  "name": "square",
  "original": "square(x)=x*x，x为有理数。",
  "parameters": [{"name":"x","type":"Rat"}],
  "result_type": "Rat",
  "body": {"mul":[{"var":"x"},{"var":"x"}]}
}
```

定义只访问自身参数与已声明定义，不捕获全局变量；可用相同的用户参数名，但宿主生成不同名字。禁止递归、前向引用、隐藏参数、额外假设与任意 Lean 源。`call` 为 `{"call":{"name":"square","args":[{"var":"x"}]}}`，每实参必须与签名类型完全相同。

| 表达式 | 格式与限制 |
| --- | --- |
| 变量 | `{"var":"x"}`，必须已绑定 |
| 字面量 | `{"nat":0}`、`{"int":-1}`、`{"rat":[-1,2]}`；整数绝对值≤1000000，Rat分母1–1000000 |
| 数值运算 | `add/mul/sub` 为同类型二元列表；`neg` 仅 Int／Rat；`div` 仅 Rat |
| 显式转换 | `{"cast":{"to":"Rat","value":{"var":"x"}}}`；只允许 Nat→Int、Nat→Rat、Int→Rat |
| 比较 | `eq/le/lt`，同数值类型的二元列表 |
| 逻辑 | `and/or/iff/imp` 为 Prop 二元列表；`not` 为单个 Prop |
| 量词 | `{"forall":{"binders":[{"name":"x","type":"Rat"}],"body":...}}`；`exists` 同形，body须Prop，不能遮蔽已有绑定 |

Nat 减法截断到零。Int／Rat 减法不截断。Rat 一般除法是 Lean 总除法，`a/0=0`；字面量零分母无效，但表达式除零有上述明确定义。系统不自动加入非零假设。前提按 `{"name":"h","formula":...}` 提供，保留全部原前提；未检查其一致性。

`strategy` 只允许 `rfl`（定义相等）、`omega`（Nat／Int线性算术）和 `grind`（核心代数／逻辑）。每次一个固定完整目标检查；没有交互式状态搜索、自动重试或通用求解保证。超时1–30秒，候选与重放共享预算，环境完整性检查另计。

`typed-submit --input confirmation.json` 需要恰含以下字段：

```json
{
  "request_id": "stable-user-chosen-id",
  "request": {"...": "与预览完全一致的完整目标"},
  "plan_fingerprint": "预览返回的指纹",
  "reviewer": "核对原文、定义、全部条件与预算的姓名",
  "confirmed": true
}
```

提交仅排队。同 request_id、完整计划、姓名幂等；任何变化冲突。姓名只是本地声明。`typed-work --run-id mathlib-...` 在共用全局锁下执行这一任务；省略 ID 时仅选本协议最早排队任务，不由旧服务代执行。必须候选内核审计和不同进程证明数据重放都通过，精确目标及公理符合政策，最终事务未取消／撤销，才 `PROVED/E4`。本协议不签发 REFUTED；失败、缺条件、超时均 UNKNOWN。

`typed-report ID` 核对当前实现清单与记录工件摘要，报告为历史证据，`fresh_attestation=false`。`typed-list` 列出记录。`typed-cancel ID` 请求取消；`typed-revoke ID --input revoke.json` 需要 `reviewer/reason/confirmed:true`，撤回后 UNKNOWN/E0。`typed-recover` 只把未完任务标 INTERRUPTED，不执行、不读成功日志晋升；新尝试需要新 request_id 和新确认。工件损坏／实现漂移一经观察持久停用，恢复字节不会复活。

停写后须同时备份 SQLite、运行工件、源码版本与封存依赖；不能仅复制 report.json 当作可恢复凭证。SQLite、宿主与本地文件权限仍受信，SHA不是身份签名。两进程仍采用同一 Lean 内核实现。

九个作者可见完整示例由 `typed-catalog` 提供，含两例缺条件失败对照。它们不是盲测或模型输出。完整实验、初次失败与自审记录见 [验收](../evaluation/typed-definitions-results.md)。
