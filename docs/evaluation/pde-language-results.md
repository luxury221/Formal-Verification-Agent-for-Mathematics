# 受控经典 PDE 语言与来源应用验收

MRA087，2026-10-29。将固定恒等式扩展为可编辑目标、假设与显式证明来源；不是通用PDE或自主原创研究。

## 实现与数学边界

新增pde-language-v1与pdelang-*身份、独立SQLite账本、watched运行封存和CLI。
实数/函数、显式测度、HasDerivAt、IntervalIntegrable、Set.uIcc、
有向积分和所有原条件均保留；用户通过source_apply填写确切来源、实参及条件证明。
NDJSON实际导入、完整类型内核桥接、最终证明项直接使用及不同PID重放均必需。
当前支持同账本typed来源和原有效watched来源，不自动接纳旧研究图状态或任意Lean代码。

八个已知正例为实数Cauchy–Schwarz、分部积分、零边界Green、经典Poisson测试恒等式、
能量、功非负及反向/相同端点。它们是功能机制诊断，不是独立盲测或模型成绩。
导数、可积、volume、两个边界条件与非负性所需端点顺序不省略。
经典测试函数恒等式不是一般弱解定义，不证明存在性、唯一性、高维或原创性。

## 实际证据

- [28新契约](../../artifacts/pde-language-20261029-01/validation-quick-be8248bb8fee4c2288d31269d9c10330/summary.json)通过。
- [最新相关回归](../../artifacts/pde-language-20261029-01/validation-contracts-1a77762098a14907a5c31b5136f42f60/summary.json)346项：293通过、52跳过、1失败。
  唯一失败为未改动旧streaming目录新增竞态；不重跑取绿，不称旧scanner修复。
  较早相关运行通过与本次失败都保留；跳过的真实专项不算通过。
- [12质量命令](../../artifacts/pde-language-20261029-01/quality-785ce5989ae34e139ed0ab6af17b3089/summary.json)通过，来源不变。
- [正式工件复审](../../artifacts/pde-language-20261029-01/review.json)：真实账本专项通过，
  8历史正目标E4，5数学负对照REJECTED/UNKNOWN；主动损坏恢复及传播控制后6当前有效E4。
  27正式worker阶段，138.163728180 Lean秒；
  unittest耗时6349.093秒，完整性/来源闭包与准备不并入Lean时间。
- 错误完整类型摘要在预览时拒绝；实际错误重放目标/错误导入类型被内核拒绝。
  实际双阶段成功后取消仍CANCELLED/UNKNOWN；监督退出71保留RUNNING/UNKNOWN，
  显式全局锁内recover仅INTERRUPTED/UNKNOWN，不自动重试。
  energy源损坏被观察后恢复原字节仍失效，work-nonnegative后继持续失效。
- [14条实际CLI](../../artifacts/pde-language-20261029-01/cli-1fe55965c1c845ff873cab0366f7cd63/summary.json)通过，额外Lean执行0。
- [源码自审](../../artifacts/pde-language-20261029-01/manual-code-review.json)为Codex自审，
  7来源/13项检查；历史7目标14份渲染除新增一个明确import外一致。
  不是独立人类复审。

## 开发预查与失败保留

[首批E0预查](../../artifacts/pde-language-20261029-01/unsealed-preflight-attempt-1/summary.json)
7任务14真实进程通过，58.107030655 Lean秒；属于未封存来源的开发检查。
后来补齐继承内积规则所需的已有缓存import，
[独立标量预查](../../artifacts/pde-language-20261029-01/unsealed-scalar-catalog-preflight/summary.json)
2真实进程通过，6.660133719秒。均保持UNKNOWN/E0，不计正式E4或重复成果。

首次契约预查对非object采用记录得到ValueError，未满足直接context接口的IntegrityError契约；
封存前修复异常归一，原失败、修复前源码与development-notes保留；未为此重跑数学。
静态导入缺失/未用名在封存前已修正。旧MRA085两失败和本轮旧scanner一失败均保留。

## 后续研究图设计预查（E0）

[图草案预查](../../artifacts/pde-language-20261029-01/next-graph-preflight-attempt-3/summary.json)
6个结构/绑定案例接受、32个无效案例拒绝；来源边与仅调度前置分开，
实参绑定保留完整变量、假设、目标及证明程序。
首轮错误复用单目标总预算导致四节点图被拒绝，原失败和源码保留；
后续为整图独立设置有界总量，每个目标仍受原编译器限制。
该草案从不授权来源、不执行Lean、不发布证明；结果均UNKNOWN/E0。
尚无图存储、执行生命周期、HTTP或网页接入；不计新数学成果或正式worker。

另有[真实账本桥接预查](../../artifacts/pde-language-20261029-01/next-graph-bridge-attempt-1/summary.json)：
1个实际E4来源到新节点的预览接受，8个错误输入/来源映射案例拒绝，
包括真实E4的错误完整目标及形式类型相同但原文/请求不同的情况。
桥接调用封存账本的report/preview核验实际来源，源行和全部旧来源保持不变；
新目标仍UNKNOWN/E0，没有提交、worker执行、图存储或网页接入。
预览的来源授权有时效，生产提交和执行还须重新审计；不计新证明。

另有[图生命周期候选复核](../../artifacts/pde-language-20261029-01/graph-lifecycle-finalization/summary.json)：
30项实际存储、具名提交、重复请求、取消、撤销、共享预算与损坏恢复控制符合预期。
原预查末尾重复初始化账本导致SQLite读写锁冲突，整轮exit1和原源码保留；
改为事务外构造一次检查器后，仅续做独立末尾检查，不重新提交这30项控制。
补充检查确认所有候选图和子队列均UNKNOWN/E0且无可运行残留，不称原失败预查全绿。
新建一个独立测试父项目，旧行保全；四节点完整图可保存，未满足前置的原问题不能提交。
所有候选图及子检查均UNKNOWN/E0，没有Lean执行。
[图监督恢复控制](../../artifacts/pde-language-20261029-01/graph-lifecycle-recovery/summary.json)
在真实全局锁内认领图任务后，让监督进程于子worker之前退出73；显式recover仅中断并取消子队列，
不自动重试。单独恢复该合成图record仍不会复活被取消的子检查，不是全项目回滚验证。
以上代码仍为未发布候选；完整PDE图的实际Lean/独立重放与成功后发布竞态仍待验收，正式HTTP/网页尚未接入。

另有[网页候选证据复核](../../artifacts/pde-language-20261029-01/next-web-review/summary.json)：
26项真实HTTP检查通过；首轮实际Edge完成12项控制后，导出预期误用了取消前的读取结果，原失败保留。
原导出与当前取消后账本完整JSON一致；仅补做6项导出/手机/错误与不重复写入检查，原控制重跑0。
5截图已目视；4张实际图及2条已取消子检查全部UNKNOWN/E0、工件和结果为空；新增Lean/模型0。
该网页候选只测试完整预览、保存、具名排队、取消及导出，没有启动数学worker，也没有替换原/analysis。

## 收尾监督与运行成本观察

[收尾进程控制](../../artifacts/pde-language-20261029-01/post-runner-validation-attempt-1/summary.json)
实际覆盖正常退出、退出73、启动失败、忽略SIGTERM的超时、先退出父进程留下的子进程与监督中断共6项。
逐步日志直接落盘，超时/中断清理本次创建的进程组，无自动重试；这些控制不启动Lean。
[现有回执开销观察](../../artifacts/pde-language-20261029-01/cost-observation-attempt-1/summary.json)
覆盖13案例/21阶段：Lean worker合计103.272秒，记录执行耗时2862.731秒，
worker占记录执行耗时3.6%。
余量包括完整性、来源递归、编译与编排；不包含所有外围调用与后续故障控制，不能当作完整专项计时或单函数剖析。
下一版本应先测量单次完整来源闭包中的重复工作，保留逐字节复核和发布门禁；本轮没有削减任何检查。

## 环境与交付

[独立封存](../../artifacts/pde-language-20261029-01/seal.json)指纹
5ee74cb9c3b56f207c4a9712999d05fff4c498fcbf5ec7b5dc39142d88f8c43b。
复用原不可变数学树，两imports均由已有快照字节核对；无新下载/安装。
baseline623旧来源、452旧DB、1036旧状态，旧交付3478摘要均受保全。
最终DB/状态保全、服务恢复与Windows全摘要以本阶段preservation-final、
runtime/windows、delivery和postseal回执为准；未执行全项目升级、回滚或恢复。

模型未配置/调用0；当前新语言仅CLI，原网页未切换。下一步为研究图/网页接入，
再建立明确弱形式、边界空间与来源/曝光清楚的研究价值评估。
