# structure-proof-composition-v1

在 WSL 项目根运行 python3 -m mra structure-reuse-*。当前为新CLI接口，现有网页／HTTP后台队列尚不消费结构组合；来源必须已经真实通过完整目标检查与独立重放，目录候选不是可用证据。


请求恰有 schema、graph_id、node_id、sources、proof、timeout_seconds 六个字段。目标、原文、全部原条件及共享 carriers／operations／definitions 从保存图加载，调用方不得覆盖。

```json
{
  "schema": "structure-proof-composition-v1",
  "graph_id": "structureflow-<32位小写十六进制>",
  "node_id": "lifted",
  "sources": [{"alias": "power", "item_id": "node.structureflow-<32位小写十六进制>.power"}],
  "proof": {
    "lemma": "power",
    "terms": [{"call":{"name":"g_mul","args":[{"call":{"name":"g_one","args":[]}},{"var":"x"}]}}, {"var": "n"}],
    "proofs": [{"hyp": "g_assoc"}, {"hyp": "g_left"}, {"hyp": "g_right"}, {"hyp": "h_assoc"}, {"hyp": "h_left"}, {"hyp": "h_right"}, {"hyp": "preserves_mul"}, {"hyp": "preserves_one"}]
  },
  "timeout_seconds": 30
}
```

node.<图ID>.<节点ID> 指原协议的当前最佳 E4 检查；run.<mathlib-ID> 指本账本当前有效的新组合结果。每个直接前置恰有一份源，必须在同图且对应同一节点的完整目标。每份源必须显式使用，carriers、operations、definitions 均须逐字相等。源定理的载体与运算全称参数显式应用到当前同一共享环境；不支持跨图、载体／运算重命名映射或定义等价推断。

terms 按来源变量原顺序填写，每个类型都需精确匹配；使用 explicit-structures-v1 的显式 cast，不能隐式转换。proofs 按来源全部原前提的顺序填写，缺一即拒绝。预览列出同时替换结果、避免捕获的量词与Nat折叠步绑定、实例化条件/结论和各前提证明。预览不是数学认证；证明项与条件类型是否匹配最终由 Lean 检查。

证明构造为 hyp、lemma/terms/proofs、pair、fst、snd、mp、mpr、apply、refl、symm、trans、congr；二元构造值为两个证明的列表，一元构造值为一个证明，refl 值为显式载体或数值数据表达式。没有任意 Lean、任意 import 或 tactic 后备。

```bash
python3 -m mra structure-reuse-catalog GRAPH_ID NODE_ID
python3 -m mra structure-reuse-preview --input request.json
python3 -m mra structure-reuse-submit --input confirmation.json
python3 -m mra structure-reuse-work --run-id RUN_ID
python3 -m mra structure-reuse-report RUN_ID
python3 -m mra structure-reuse-list
python3 -m mra structure-reuse-cancel RUN_ID
python3 -m mra structure-reuse-revoke RUN_ID --input revocation.json
python3 -m mra structure-reuse-recover
```

confirmation.json 恰有 request_id、request、plan_fingerprint、reviewer、confirmed；request 为完整请求，指纹来自当前预览，confirmed 必须为 true，姓名不能为空。提交只排队；只有CLI显式 work 执行，依赖失败时不重试。相同ID只接受相同计划及审核人。revocation.json 为 reviewer、reason、confirmed。report 的完整JSON可直接重定向导出，读取会重新审计来源而不会重跑Lean。

候选真实导入每源的有界 NDJSON 数据并核对完整类型；完整结果再由独立进程重放。拒绝、超时、取消、中断一律 UNKNOWN/E0，不是 REFUTED。成功才在本协议为 PROVED/E4；原图判断独立。具名撤销组合用 structure-reuse-revoke；structure-research-withdraw 只操作原协议的节点尝试。原来源撤销会在后继审计时传递停用新组合，但不会把新协议的目标撤销自动写回原图。

持久库为 artifacts/structure-proof-reuse/lab.sqlite3；备份要同时保留本库、runs、invalidated-runs、原图计划/记录、原子库及完整工件。最多200次、4源、8层/64祖先/128访问、每源512KiB；单次候选与重放共用1–30秒，扫描/审计等待另计。预算独立于原图16次/480秒，不自动联合记账。实际执行时图锁入口最多等待30秒，报告读取仍可能暂忙，需要稍后人工重读。量词／类型语言仍受原显式结构协议边界约束，前提相容性未认证。

congr 的值恰有 variable、body、proof：variable 为新鲜 name/type 绑定，body 为受限数据表达式，proof 为等式证明项。它生成固定 congrArg，内核核对所用等式的端点与函数参数。示例（局部原条件 equal 的端点以及 y 均为 h 载体元素）：

```json
{"congr":{"variable":{"name":"z","type":"h"},"body":{"call":{"name":"h_mul","args":[{"var":"z"},{"var":"y"}]}},"proof":{"hyp":"equal"}}}
```

量词全体重命名，Nat折叠的index／zero在外层语境替换，step的index／value仅绑定step.body；替换为同时替换，不递归改写实参。折叠仍是原内核Nat.rec，不能引入任意递归。证明项至多240节点／14层，表达式累计320节点；完整JSON沿用192KiB／4096结构项。上述不是独立模型能力或提速证据。
