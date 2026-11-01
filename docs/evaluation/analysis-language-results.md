# 结构化实分析语言验收

MRA080（2026-10-29）。完成受控 Real / Type 0 实内积空间 / 递归函数类型语法、完整命题确认、真实检查与数据独立重放的执行入口。部分已知目标仍因预算超时而未完成，不能称为全部A1数学用例通过。复用A0只读环境，无新依赖、下载或模型调用。当前仅运维CLI，尚无新网页表单。

最终30秒计划的12个已知正例中11个完成真实检查/不同PID重放，未完成：pointwise-hypothesis；未完成者保持UNKNOWN。全部十四个作者可见计划案例仍保留，另有一次真实重放后取消控制。功能协议通过与数学完成率分开，测试现在会完整收集超时，强制其为UNKNOWN/E0；不接受把REJECTED/执行故障伪装为正例成功。实际成功的完整宿主目标、全部条件与公理均由另一个Lean进程核验；相同Lean实现的两进程不是不同实现互证。

## 两次失败及仅续跑未执行项

首轮十四个计划目标每项共享20 worker秒。仅执行两项：Cauchy–Schwarz E4，缩放能量候选已过/重放超时UNKNOWN；测试断言停止，其余十二项未执行。首次测试318.891秒，工件与源备份完整保留。随后明确把功能验收预算改为接口原允许的30秒，同命题、候选程序和条件由复审逐项比对，未把旧超时改判成功。

30秒轮先在4GiB/512MiB限制下执行五项：四项E4，pointwise-hypothesis在重放阶段超时UNKNOWN。测试808.014秒并停止，其余九个案例未执行。第二份原始测试源码和当时五项结果另行备份。没有重跑这五项；修订测试收集器后，只继续原计划尚未执行的九项及取消控制。续跑内存限额改为6GiB、swap仍512MiB，实际cgroup记录在continuation工件，worker预算仍30秒。不同资源批次不能据此作公平性能比较。尝试读取已退出PID的资源观察失败，该次未获得压力数据；另有6GiB续跑的E0资源观测，仅描述当时资源，不证明早前超时根因。

最终续跑专项耗时313.617秒；此前318.891和808.014秒另计，质量/回归、CLI与后续复审亦另计。本轮实际合计17个完整检查、32个worker阶段，worker累计211.692518秒。共享30秒仅是候选+重放worker预算，不是用户等待时间；首个20秒CS执行段含完整性扫描153.328秒，而worker17.432秒。完整性扫描和运行延时应优先优化。

## 具体证据与控制

固定正例包括函数取值处CS、带同名λ遮蔽的缩放能量、范数三角不等式、非零a的a/a=1、全称点态条件、自身内积与范数平方、函数合同、全称平方非负、高阶函数合同、λβ化简、嵌套遮蔽及保留未用条件的范数非负。每例实际结果见下表，不因语法已实现而声称所有数学用例完成。所有Type 0空间的NormedAddCommGroup/InnerProductSpace ℝ实例、变量和原条件保留，没有自动增加完备、连续、可积或非零条件。

两个负对照（遗漏非零条件、错误能量符号）真实拒绝/UNKNOWN；失败不构成已验证反证。取消控制先真实重放成功，再在提交结果前取消，最终CANCELLED/UNKNOWN。λβ正例的真实proof.ndjson及未用条件正例的真实账本行分别损坏后失效，恢复原字节/行仍失效。最终轮历史E4中两个永久停用，当前9个E4；加20秒轮仍有效的重复CS记录，两轮总12个历史E4检查/10个当前E4检查。重复CS不是两项不同数学发现。

| 批次 | 案例 | 记录运行状态 | 当前数学判断 | 永久失效 |
| --- | --- | --- | --- | --- |
| initial-20 | pointwise-cauchy-schwarz | COMPLETED | PROVED | False |
| initial-20 | lambda-scaled-energy | TIMEOUT | UNKNOWN | False |
| final-30 | pointwise-cauchy-schwarz | COMPLETED | PROVED | False |
| final-30 | lambda-scaled-energy | COMPLETED | PROVED | False |
| final-30 | norm-triangle | COMPLETED | PROVED | False |
| final-30 | division-nonzero | COMPLETED | PROVED | False |
| final-30 | pointwise-hypothesis | TIMEOUT | UNKNOWN | False |
| final-30 | inner-energy-square | COMPLETED | PROVED | False |
| final-30 | function-congruence | COMPLETED | PROVED | False |
| final-30 | quantified-square | COMPLETED | PROVED | False |
| final-30 | higher-order-function | COMPLETED | PROVED | False |
| final-30 | lambda-beta | COMPLETED | UNKNOWN | True |
| final-30 | nested-shadowing | COMPLETED | PROVED | False |
| final-30 | unused-original-condition | COMPLETED | UNKNOWN | True |
| final-30 | missing-nonzero | REJECTED | UNKNOWN | False |
| final-30 | wrong-energy-sign | REJECTED | UNKNOWN | False |
| final-30 | cancel-after-replay | CANCELLED | UNKNOWN | False |

132项相关回归96通过/36跳过，单独真实协议专项1项通过、质量10命令。协议测试覆盖绑定/作用域、不同类型拒绝、确认幂等、取消不重排、锁内中断恢复、暂忙不永久失效和外来库/重定向拒绝，夹具不产生假E4。CLI十五调用未启动新Lean。旧核心、运行树、题集、436旧库和830旧状态由保全另核；旧导出/应用、完整图/组合和既有5/8成绩保持，无旧数学重跑或回填。整项目实际升级/回滚未执行。

没有模型、盲测、原创性或PDE能力结论。导数/积分/测度/正则性未实现；只读A2源码预查是E0且三个指定olean缺失。下一步先剖析完整性/执行成本，再做隔离A2的小目标试验。全部成功、超时、测试失败、源备份、批次资源与原始工件在artifacts/analysis-language-20261029-01/。见[协议](../protocols/analysis-language-v1.md)、[ADR](../adr/0072-typed-analysis-language.md)、[领域路线](../product/analysis-pde-roadmap.md)及[A2预查](../product/analysis-a2-preflight.md)。
