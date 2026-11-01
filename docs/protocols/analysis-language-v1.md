# 结构化实分析语言 v1

MRA080。schema：analysis-language-v1；这是 A1 的受控输入与验证入口。复用 A0 的只读 Lean 4.35.0 / Mathlib 56a005855ea17e0b0958f73ec801ae140b0a1679 环境，唯一分析 import 为 Mathlib.Analysis.InnerProductSpace.Basic。无新依赖、下载或模型调用。

## 完整请求

请求必须且仅有 schema、title、original、spaces、variables、assumptions、goal、proof、timeout_seconds。输入为普通 JSON，不接受浮点数、重复 JSON 键或 Lean 文本。

- spaces：最多三个 {name, structure:"real-inner-product"} 声明。每个生成 Type 0、NormedAddCommGroup 和 InnerProductSpace ℝ 的全称语境。
- 类型："Real"、{space:"已声明名字"}、{function:[域,余域]}；函数可嵌套，类型递归深度上限 4。不同空间或函数域不隐式转换。
- variables：最多二十个 {name,type}；assumptions：最多十六个 {name,formula}。包括未用于证明的条件，全部保留在完整宿主目标中。
- goal：有界公式；proof：固定 tactic 或结构化证明项。timeout_seconds 为 1–30 的整数，候选与独立重放共享此 worker 秒数，完整性扫描另计。每阶段最多一次，没有自动重试。

符号为 1–32 位 ASCII 字母开头的字母、数字或下划线。用户符号仅作数据键；Lean 空间、变量、条件及局部绑定均由编译器生成名称。λ/量词和证明引入使用各自词法作用域，可遮蔽同名外层变量，不向外逃逸。顶层重复变量、歧义条件名、未绑定变量均拒绝。

请求上限 128 KiB、1200 个值节点、32 层 JSON；整数绝对值不超过 10^9。实常量为 {real:[整数分子,正整数分母]}，编译前约分，排除布尔数。乘方为实数上的 0–8 次幂。除法采用 Lean 实数的总定义，a/0=0，不自动插入非零条件；若原命题要求非零，必须显式声明。

## 表达式、公式与证明

表达式有 var、real、zero、lambda、apply、add、sub、neg、mul、div、pow、abs、norm、inner、smul。双操作数使用二元素列表；abs 仅对实数，norm 对实数或已声明向量，inner 要求同一实内积空间/实数，smul 为实数乘向量。函数仅支持明确函数类型的应用、λ、相等和逻辑量化，不隐式赋予函数空间范数或正则性。

公式有 eq、le、lt、and、or、imp、not、forall、exists、truth。阶只对实数；相等两侧必须同类型。量词为 {forall:{name,type,body}} 或 exists 对应结构。truth 的值为 JSON 布尔。

固定证明策略为 {tactic:"rfl"}、{tactic:"simp"} 或 {tactic:"grind"}，对应 rfl、simp_all 和精简 grind。结构化证明为 {term:证明项}，支持 hyp、use_hyp、intro、intro_hyp 和 rule。规则名称及参数个数以 catalog 为准；包括内积非负、Cauchy–Schwarz、范数三角不等式、自身内积与范数平方、条件使用与有限逻辑/等式组合。规则是候选，最终仍由 Lean 检查完整目标，不因编译器识别规则而赋予 PROVED。真实验收覆盖的具体子集见[结果](../evaluation/analysis-language-results.md)。

不接受任意 Lean、import、axiom、sorry 或模型自报成功。不自动审查假设是否相容；若显式输入矛盾条件，所得条件定理不能被解释成无条件结论。自然语言与公式的等价性由具名审核者确认，身份仍是本地声明。

## 运维入口

在项目根运行 PYTHONPATH=. python3 ops/scripts/analysis_language.py，支持：

| 命令 | 参数/含义 |
| --- | --- |
| catalog | 类型/表达式/规则及作者已见样例；不启动 Lean |
| preview | --input 请求文件；返回 E0 完整目标和 plan_fingerprint |
| submit | --input 确认信封；创建队列记录 |
| work | 可选 --run-id；在全局工作锁内执行一次 |
| report | --run-id；重核当前证据 |
| list | 当前记录，最多 200 条 |
| cancel | --run-id；请求取消，已取消不重排 |
| revoke | --run-id 与 --input 撤销说明 |
| recover | 锁内将遗留 RUNNING 改为 INTERRUPTED/UNKNOWN，不重试 |

确认信封恰含 request_id、request、plan_fingerprint、reviewer、confirmed:true；每次重新编译比对完整预览，重复相同 request_id 幂等，改内容则拒绝。撤销说明为 reviewer、reason、confirmed:true。CLI 输入上限 192 KiB。

canonical 库为 artifacts/analysis-language/lab.sqlite3，带独立 analysis_identity，拒绝收养无身份的外来库；验收用 artifacts 下的 analysis-language-private。与旧 Nat/结构语言库隔离，不迁移原来源链。当前无 HTTP 或网页表单，旧后台 worker 不扫描新库，必须显式 work。

## 验证与失效

确认的 plan 绑定完整输入、所有类型/条件、预算、策略与 provider 摘要。provider 绑定新编译器/执行器、A0 snapshot/lock 和传递信任源实际摘要。预览只核元数据与信任源，不能证明完整运行树可用；执行和当前 E4 报告再由 A0 load 检查实际字节。旧 A0 源、快照及运行树保持不变。

候选在隔离环境检查完整命题并导出 NDJSON 证明数据；不同 PID 的第二个 Lean 进程不导入候选模块，只重放数据并核完整宿主目标、公理及局部依赖。两进程使用同一固定 Lean 实现，不能称作两个独立内核实现。允许公理仅 propext、Classical.choice、Quot.sound，其他局部公理和占位不接受。

成功必须同时为 COMPLETED / PROVED / E4。报告重编完整 plan，比对具名对齐/提交指纹，核全部十个工件、候选及重放源码、运行快照、审计、NDJSON、策略、不同 PID 与共享 worker 预算。失败、拒绝、取消、中断、失效均为 UNKNOWN/E0，不能推断命题被反驳。运行中报告不核尚未写完的工件，保持 UNKNOWN/E0。

独立 invalidated-runs 标记防止恢复旧数据库或工件字节后重新晋升。SQLite 暂忙读错误直接传播，不永久误判为证据损坏。损坏账本、provider 漂移和工件变更会失效；新预览与新 run 才能建立新证据。此机制不宣称能抵御有完整本机写权限的人删除全部证据和标记。

本轮仅为已知分析基础任务的功能验收，不是盲测、自动证明搜索、原创性评估、导数/积分或 PDE 能力。下一步按[领域路线](../product/analysis-pde-roadmap.md)扩展 A2 的正则性/测度/积分语义，再进入局部 PDE 能量链。
