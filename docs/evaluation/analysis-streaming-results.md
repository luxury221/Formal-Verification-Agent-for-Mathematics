# 流式完整性扫描：真实新旧对照

MRA081（2026-10-29）。本轮两样本未证明稳定提速：第一组新版更快，第二组旧版更快。新profile仅作为显式可选实验入口，未替换原默认执行。pointwise-hypothesis整项 old 63.327秒 / streaming 33.941秒；lambda-scaled-energy整项 old 27.790秒 / streaming 34.433秒。新增独立 analysis-streaming-v1 执行版本，输入与数学能力仍为 A1。未改旧源码、封存范围、环境或数学命题。每次重新读取全部声明文件的实际字节，scandir 流式逐项比较，较大文件两线程、pending≤8，线程本地256KiB缓冲；没有成功检查缓存。指标仅为E0观察。

## 相同资源的实际结果

两道仓库已知目标每个版本各执行一次，交错顺序为 pointwise old→new、scaled new→old。全部同为6GiB内存、512MiB swap、候选与重放共享30 worker秒，无自动重试。原请求/类型/条件/候选/重放源码逐字核对相同；执行身份、账本和重放policy的mode标签版本独立。完整性时间不占worker预算。

| 目标 | 版本 | 记录状态 | worker秒 | 完整性秒 | 整项任务秒 | 完整扫描次数 |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| pointwise-hypothesis | old | COMPLETED | 4.280 | 58.015 | 63.327 | 5 |
| pointwise-hypothesis | streaming | COMPLETED | 4.122 | 28.893 | 33.941 | 5 |
| lambda-scaled-energy | old | COMPLETED | 4.604 | 22.459 | 27.790 | 5 |
| lambda-scaled-energy | streaming | COMPLETED | 4.667 | 28.883 | 34.433 | 5 |

完整性秒为 work_once 内每次真实 verify 调用的外部计时之和，包括最终成功报告核验；整项任务秒为 work_once 外部计时，含扫描、worker、来源摘要、快照序列化和账本等其他成本。执行result内部elapsed不包含最终报告，因此不能直接与整项任务混用。旧版无逐文件字节指标，新版每次指标与调用时间均原样保存，实际读取范围及封存环境不变。

这只有两个已知目标、每版本每目标一个样本。顺序、文件系统缓存和共享机器状态仍有混杂；不能作普遍速度保证，不能把worker波动全归因scanner，也不能由本轮推断上轮4GiB超时的根因。无论本轮是否完成，上轮TIMEOUT/UNKNOWN工件保持。

## 实际安全控制与范围

本轮共7个完整检查/12个worker阶段；4个历史E4、控制后3个当前E4检查。没有超时或未完成的计划目标。四个历史E4来自两道命题各运行两个版本，不是四项不同数学成果。两项负对照遗漏非零条件/错误能量符号真实REJECTED/UNKNOWN；额外目标在真实重放成功后取消，保持CANCELLED/UNKNOWN。一个真实成功streaming证明的proof.ndjson被篡改后报告永久失效，恢复原字节仍UNKNOWN；没有损坏旧环境或旧工件。失败拒绝不是反证。

164项相关回归，127通过/37跳过；质量10命令及单独真实专项通过。真实专项耗时216.866秒；质量、回归、CLI、后续审计和保全另计。夹具仅测内容门禁/协议，未用mock产生数学E4。边界覆盖同长度同mtime篡改、缺失/多余/权限、symlink escape/redirect/cycle、FIFO及open竞争、目录增长、seal末尾重读、加载scope/provenance、来源漂移、账本身份/恢复、暂忙/取消/中断。CLI显式入口，无新HTTP或网页。

550旧来源作基线，439旧数据库与850旧状态保全另核。A0两当前E4、旧A1十当前记录及两个失效记录、旧超时、旧图/应用/导出/5/8成绩保持，没有旧数学重跑、分数回填或整体恢复。源代码自审和文档事实/链接核对另存于工件。

没有新增依赖、下载、模型调用或A2 import；分析环境 fingerprint 仍为 213e30b5b8deb6a06af950489173b313c9d6543fb4da841217b56d11f02d07db。本轮没有导数、积分、a.e.、弱解或PDE能力结论。下一步转入隔离A2的有限导数/积分环境与真实小目标，先保留可导/可积/测度/端点语义，参见[A2预查](../product/analysis-a2-preflight.md)。协议见[analysis-streaming-v1](../protocols/analysis-streaming-v1.md)，决策见[ADR0073](../adr/0073-streaming-analysis-integrity.md)，完整原始数据在artifacts/analysis-streaming-20261029-01/。
