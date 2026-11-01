# 私有引理证明项组合 v1

模式 `private-lemma-composition-v1`。该独立协议使私有E4归档的实际证明数据用于新Nat目标；与重新执行归档结构化证明输入的 `mathlib-reuse` 分开。现阶段提供Python接口与CLI，网页和研究任务图尚未接入。

请求包括 `schema/title/original/variables/assumptions/goal/sources/proof/timeout_seconds`。完整目标沿用现有Nat整除、gcd、互素语言；最多6变量、6前提、1–4个来源、192KiB输入。每个来源为 `{alias,item_id}`，别名唯一且每个来源都必须显式使用；允许同一来源多次应用。来源必须是有效、未撤销且归档字节完好的私有Mathlib E4条目，原始具名对齐记录及原运行工件也需有效。

新增证明构造：`{lemma: alias, terms: [...], proofs: [...]}`。`terms` 按源定理的变量顺序提供全部Nat实参；`proofs` 按原前提顺序提供全部条件证明。仍可组合既有22条库引用、目标中的具名前提、合取及双向蕴含应用。变量替换同时进行，不做递归代入或捕获；每次使用的完整实例化条件与结论列入计划。类型是否匹配由真实Lean检查；预览始终UNKNOWN/E0，不凭前提数量匹配判断有效。

执行与证据边界：

1. `preview` 重查来源，编译完整目标、证明树、来源签名与字节摘要、显式实参、条件及预算，生成计划指纹。无Lean/模型调用。
2. `submit` 需要 `request_id/request/plan_fingerprint/reviewer/confirmed`，具名确认原意、完整公式、所有应用和预算；同ID/同计划幂等，变更确认拒绝。确认是本地声明，不等于身份认证。
3. `work` 使用全项目同一worker锁，每次一个已确认运行；两个新Lean进程共享1–30秒worker预算，环境完整性核对另计。没有自动重试。
4. 候选进程仅读取已确认的NDJSON归档证明数据，不执行归档Lean源、不导入候选模块。为每个来源重命名全部本地声明和表达式常量引用；基础导入常量始终从固定环境解析。所有新增声明经安全、公理、种类门禁及内核类型检查，完整源类型再经独立桥接检查。保留原Lean编译环境以继续检查新目标。
5. 候选完整目标审计通过后，导出包含来源证明和新证明的本地声明闭包；独立进程使用现有冻结重放器重新检查完整目标、声明种类及公理。不读取候选模块，不执行候选Lean源；两个进程仍共享同一版本内核实现。
6. 仅两阶段均完成且当前语义确认、来源、环境及工件仍有效才为PROVED/E4。错误实参、错误前提、执行失败、取消、超时和中断均UNKNOWN，不能标为REFUTED。前提一致性不在该检查中自动保证。

独立账本为 `artifacts/lemma-compositions/lab.sqlite3`，最多200次运行，工件位于同目录 `runs/`。来源撤销/移除、版本或归档字节变化、完整运行来源失效、新编译器/导入模板或工件漂移，均导致依赖结果在读取/导出时持久停用；恢复字节不会恢复旧确认。需要新计划、新确认和新检查。只读查询遇到临时worker/SQLite锁忙应报错并允许重读，不把暂忙持久化为证据损坏。

CLI命令：

```bash
python3 -m mra lemma-compose-catalog
python3 -m mra lemma-compose-preview --input request.json
python3 -m mra lemma-compose-submit --input confirmation.json
python3 -m mra lemma-compose-work --run-id mathlib-<id>
python3 -m mra lemma-compose-report mathlib-<id>
python3 -m mra lemma-compose-export mathlib-<id>
python3 -m mra lemma-compose-list
python3 -m mra lemma-compose-cancel mathlib-<id>
python3 -m mra lemma-compose-revoke mathlib-<id> --input revocation.json
python3 -m mra lemma-compose-recover
```

`revocation.json` 为具名 `{reviewer,reason,confirmed:true}`；`recover` 只把未完成RUNNING记为INTERRUPTED，不重放旧工件或自动重试。工件导出是历史记录，`fresh_attestation=false`；CLI `work` 对本模式显式运行，当前HTTP worker不会自动执行本模式。

本功能是证明项复用的受控基础，不是独立持出求解收益评测；没有新增模型、任意Lean语法、一般数学类型、自主引理搜索、归档结果再入库或研究图调度。见[ADR038](../adr/0038-private-proof-data-composition.md)。
