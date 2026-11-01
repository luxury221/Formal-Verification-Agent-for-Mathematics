# 微积分隔离基础环境验收

MRA082（2026-10-29）。建立 calculus-foundation-v2 固定导数/区间积分探针的真实验证链。九个已知正目标中9个完成候选和独立数据重放，未完成项为无；两项成功记录因真实恢复控制永久停用，当前7个E4检查。固定探针功能与一般分析能力分开，尚无通用导数/积分输入AST、网页、PDE或原创研究结论。

## 验证的数学语义

| 探针 | 完整目标或条件 |
| --- | --- |
| 恒等/常数导数 | 任意实点的HasDerivAt证据，分别为1和0 |
| 导数加法 | f,g在同一点分别HasDerivAt f df x与HasDerivAt g dg x，两条件都保留 |
| 体积常数积分 | 任意实端点a,b，积分为(b-a)c，无a≤b附加条件 |
| 积分加法 | 两函数分别IntervalIntegrable f volume a b及IntervalIntegrable g volume a b |
| 端点反向 | 任意实函数，按总定义积分满足反向为负 |
| 零测度常数积分 | 明确μ=0时积分为0，不能套体积长度公式 |
| 一般测度常数积分 | 显式μ.real(Ioc a b)-μ.real(Ioc b a)，toReal不自动证明测度有限 |
| a.e.范数界 | 保留体积下无序半开区间的几乎处处范数条件，得到积分范数界 |

不可导deriv默认0、不可积或余域不完备Bochner积分默认0，因此数值表达式不证明可导/可积。a.e.范数界原库命题没有显式可积前提，保留总定义语义，不额外宣称证明了可积，也不把a.e.推成逐点。四份固定Mathlib源码与十三声明位置/摘要在math-source-inspection.json。

五个数学负对照：遗漏两导数条件、遗漏两区间可积条件、错误端点符号、一般测度套体积公式、a.e.提升逐点；实际拒绝保持UNKNOWN，不是已证反证。未用局部公理和sorry两个控制受政策阻止/UNKNOWN。两次额外真实重放对错误宿主目标与损坏数据均拒绝。恒等导数真实proof.ndjson和导数加法真实worker预算记录分别损坏后失效，恢复原字节仍失效。两进程使用同一固定Lean实现，不是不同内核互证；具名语义对齐仍为本地声明。

| 案例 | 记录状态 | 当前状态 | 当前数学判断 |
| --- | --- | --- | --- |
| derivative-id | VERIFIED | INVALIDATED | UNKNOWN |
| derivative-constant | VERIFIED | VERIFIED | PROVED |
| derivative-add | VERIFIED | INVALIDATED | UNKNOWN |
| interval-constant | VERIFIED | VERIFIED | PROVED |
| interval-add | VERIFIED | VERIFIED | PROVED |
| interval-reversal | VERIFIED | VERIFIED | PROVED |
| zero-measure-constant | VERIFIED | VERIFIED | PROVED |
| general-measure-constant | VERIFIED | VERIFIED | PROVED |
| ae-norm-bound | VERIFIED | VERIFIED | PROVED |
| missing-derivative-conditions | REJECTED | REJECTED | UNKNOWN |
| missing-integrability | REJECTED | REJECTED | UNKNOWN |
| wrong-orientation | REJECTED | REJECTED | UNKNOWN |
| wrong-measure | REJECTED | REJECTED | UNKNOWN |
| ae-is-not-pointwise | REJECTED | REJECTED | UNKNOWN |
| unused-axiom | POLICY_BLOCKED | POLICY_BLOCKED | UNKNOWN |
| placeholder | POLICY_BLOCKED | POLICY_BLOCKED | UNKNOWN |

## 过程、失败与资源

固定Lean4.35.0与Mathlib56a005855ea17e0b0958f73ec801ae140b0a1679，三imports仅Deriv.Basic、Deriv.Add、IntervalIntegral.Basic。v1物理树独立普通复制A0并核无硬链接；v2执行版本复用该封存树，不再复制或下载；官方原档八份Cache/manifest/toolchain源码逐字核对。官方生成2692文件计划，1999已有缓存按摘要复用，693新文件下载34784381字节；总缓存归档166253533字节含复用部分，下载231.774秒，解包及旧环境后置核验54.629秒。复制/计划、许可审查和封存另计。本地SHA256不是上游签名，既有供应链限制保持。无新第三方依赖、模型、论文或训练数据。

第一次E0真实候选预查在一个进程核九声明，八项通过、零测度simp未完成，退出1，诊断中的sorryAx未被接受。原候选/日志完整保留preflight-attempt-1。按实际定义改为simp [intervalIntegral]后第二次E0预查退出0、九声明只有允许公理。随后v1正式批次暴露完整模板导入顺序错误，12收据中11候选解析拒绝、1中断、4计划未执行，均UNKNOWN，不能当数学负对照或完成率。确认后精确SIGINT停止原测试；该次没有完整专项elapsed记录，不能把最终专项耗时称本轮总耗时。保留原源码/环境/所有工件后，v2仅在独立执行元数据中修正scope放到全部imports之后，复用同一封存v1数学库和缓存。第三次预查实际执行完整导出候选模板，退出0；增加候选/重放模板顺序回归。三次预查合计41.520worker秒、3进程，均无独立重放或E4晋升；不是正式探针自动重试。

最终源冻结后，184项相关回归145通过/39跳过，质量10命令与单独真实专项通过。相关回归与真实专项分开；正式专项631.162秒，单目标40共享worker秒，每目标最多一次候选和一次重放，不自动重试，完整性耗时另计。正式16完整检查/25候选重放阶段及2安全重放，共27正式进程；连同首批失败阶段与预查，共42已知启动的Lean进程（含首批中断阶段），41份已记录结果，正式worker累计98.047256秒。6GiB/512MiB swap实际cgroup记录保留，资源压力观测仅E0，不证明耗时根因。质量、CLI、后续复审/保全另计。夹具不产生假E4，开发语法错误及修正写入preparation-notes。当前有界error尾部可能显示导出数据而省略开头错误；原stdout完整保留，review摘录实际条件缺口/类型不匹配，后续需结构化诊断。

558旧来源、442旧DB与859旧状态保全另核；A0/A1/streaming有效/失效/超时、旧图/导出/应用及5/8成绩保持，无旧数学重跑或分数改写。当前入口为ops/scripts/calculus_foundation_v2.py，仅运维CLI；v1旧CLI保留失败历史，当前不使用。实际网页静态检查另记；没有整体环境升级/迁移/回滚操作声明。

下一步扩展结构化HasDerivAt、IntervalIntegrable、明确测度/端点及a.e.条件输入，并把真实重放证据接入研究任务，再构建局部PDE能量/弱形式链。当前固定探针不代表解决开放问题的能力。见[协议](../protocols/calculus-foundation-v2.md)、[ADR0074](../adr/0074-isolated-calculus-foundation.md)及[分析领域路线](../product/analysis-pde-roadmap.md)。完整工件在artifacts/calculus-foundation-20261029-01/及artifacts/calculus-foundation-v2/runs/。
