# 显式结构与自然归纳 v1

schema：explicit-structures-v1。独立 CLI 协议；验收见 ../evaluation/explicit-structures-results.md。

请求恰含 schema、title、original、carriers、operations、definitions、variables、assumptions、goal、proof、timeout_seconds。原文和原条件完整保留，所有字段及完整编译计划绑定指纹。预览不执行 Lean、不创建账本；提交只排队。

carriers 最多两个 {name, original}，编译为全称 Type 参数（宇宙0）。operations 最多八个 {name, original, parameters, result_type}；parameters 是至多四个显式数据类型，result_type 可为数据类型或 Prop。数据类型为 Nat／Int／Rat 或已声明载体名。每条定律必须在 assumptions 中具名列为普通命题前提，不能凭 monoid 等描述添加性质。

definitions 最多六个 {name, original, parameters, result_type, body}，参数恰含 name/type；只访问本定义参数、载体／运算与之前定义，拒绝自递归、前向引用、外层任务变量捕获。目标中编译为闭合透明 let。variables 至多八个 name/type，assumptions 至多八个 name/formula；量词每组至多四个新绑定，不能遮蔽外层变量。

表达式每项仅一个构造：var、nat／int／rat、显式向上 cast、call {name,args}、forall／exists {binders,body}、not、neg、add／mul／sub／div、eq／le／lt、and／or／iff／imp。二元构造值为两个操作数。数值构造沿用 typed 字面量；Nat 减法截断，Rat 总除法 a/0=0，不自动补充非零条件。载体运算只能通过显式 call，实参必须完整且同类型。

nat_fold 的值恰为 {index,zero,step:{index,value,body}}。index 表达式为 Nat，zero 是数据类型；step 的 index/value 是新且不同的名字，body 与 zero 同类型。最多四个折叠，真实编译为 Nat.rec，禁止用户递归 Lean。请求受192KiB／4096结构项、累计320表达式节点／16层预算约束。

proof 可为 {method:rfl}、{method:grind}，或 {method:rewrite,steps:[{rule,reverse}],finish}。每支1–24个重写，reverse 为布尔，finish 为 rfl／grind。rule 仅限原条件名；归纳步另允许 @ih。归纳为 {method:nat_induction,variable,generalize,base,step}；variable 是原任务显式 Nat 变量，generalize 是其他不同原变量名列表；base/step 各用上述非归纳分支。不接受任意 Lean、战术或自动后备。归纳前用 rfl 证明局部 recursor 计算等式，再在分支化简并执行指定程序。

CLI（项目 WSL 根）：

- python3 -m mra structure-catalog：七个作者可见结构化样例及限制，不代表求解成绩。
- structure-preview --input request.json：输出完整目标、定义、映射、所有前提、程序、预算、实现指纹，始终 UNKNOWN/E0。
- structure-submit --input confirmation.json：恰含 request_id、request、plan_fingerprint、reviewer、confirmed:true；具名审核完整计划，姓名非认证。相同请求ID与确认幂等，改变正文必须新ID／重审。
- structure-work [--run-id ID]：显式同步执行一项，与全项目共享 worker 锁；旧HTTP后台worker不会代执行该账本。
- structure-report ID、structure-list：重核当前实现及工件；report 的 fresh_attestation:false 不声称历史执行等于当前重新运行。
- structure-cancel ID：取消排队／执行；structure-revoke ID --input revocation.json：具名 reviewer、reason、confirmed:true 撤销对齐。
- structure-recover：持锁降级未提交的 RUNNING，不自动续跑或晋升。

候选与独立数据重放共享1–30 worker秒、固定200000心跳、无自动重试；完整性扫描时间另计，非端到端硬时限。每项完整目标须通过确切类型、公理及证明数据审计，重放成功后仍须父事务确认。取消／监督中断不晋升；工件或实现变化持续停用，恢复旧字节不复活。失败／超时是 UNKNOWN，不能称 REFUTED。

范围限制：至多 Type 宇宙0、非依赖数据、一次 Nat 归纳；没有 Mathlib 结构实例、集合／拓扑／分析、任意递归、模型规划、原图证据晋升或新网页。未证明结构存在／前提一致／原文自动等价。候选与重放是不同进程、同一固定内核实现。所有示例作者可见、无模型调用。
