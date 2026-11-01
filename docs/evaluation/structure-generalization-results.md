# MRA068：受控原参数泛化及结构图原子发布验收

日期：2026-10-29。权威封存：`artifacts/structure-generalization-20261029-01/delivery.json`；该文件及封存后的Windows独立摘要核对记录为最终依据。

本轮新增结构候选v3，在原目标和原条件下提出单参数／多参数共同泛化；接入现有结构研究工作台、HTTP和CLI。候选生成无Lean或模型调用，全部UNKNOWN/E0。只有另存完整新图、具名确认、真实检查与独立数据重放后，完整目标才可能取得E4。旧v1／v2生成器、历史题集和数学执行／导入／重放核心共53个冻结文件逐字未改。

## 功能与资源边界

GeneralizingPlanner复用v2活跃递推索引排序，选择归纳假设中出现的其他原自由变量。原前提自由依赖的变量、fold／量词绑定子项内部的参数保守排除，不增加变量、假设或引理。选项顺序为所有合格参数共同泛化、逐个单参数、不泛化，去重；最多7个参数。泛化IH按原类型匹配，绑定子项不含待匹配参数时才按词法alpha等价比较，避免捕获。

各选项共享原有0.75秒合成检查点、32768规范化访问、4096替换前表达式估计、展开次数、最多6改写步和8候选上限。检查点不是完整端到端硬时限，也不保证搜索完所有选项。前端展示泛化及排除原因，选择只带入草稿，清除旧确认；v1／v2接口继续独立存在。见[ADR0059](../adr/0059-bounded-original-parameter-generalization.md)及[v3协议](../protocols/structure-strategy-proposals-v3.md)。

## 真实数学配对

新题集`evals/structure-generalization-v1/cases.json`含四个作者可见且相关案例，无参考proof。作用遵守apply(compose(p,q),x)=apply(p,apply(q,x))，power右复合递推；model的后继把初态改为apply(f,x)。普通IH固定初态，泛化IH能实例化到变化初态；双状态例需要同时改变两个初态。原条件均完整保留，不声称数学上所有无泛化证明都不可能。

私有三题配对在执行前保存`declared-plan.json`，v2／v3各取首个非rfl程序，每题每组仅一次完整检查，候选与重放共享20秒。组顺序交替，不补试，不回写旧六题成绩。

| 案例 | v2程序 | v3程序 |
| --- | --- | --- |
| action-model：单初态 | REJECTED／UNKNOWN | PROVED／E4 |
| two-state-observation：双初态观察 | REJECTED／UNKNOWN | PROVED／E4 |
| missing-step：删除模型后继原条件，目标不变 | REJECTED／UNKNOWN | REJECTED／UNKNOWN |

私有6次完整检查、8个真实进程。记录在`real-95a5ef008f6d460fa2dc5b4e797c03cb/`，原真实专项`validation-real-8431b7344eed411a8055bf4c5cfef6c3`为1项通过、101.022秒。以上路径均相对于本轮工件目录。

真实专项先于发布修复执行。之后已有产品源仅`mra/__main__.py`和`mra/http_api.py`改为发布适配器，新增适配器及其测试；数学候选、题集、runner及执行上下文未变。`real-readonly-reaudit-33aa257c5bd04582bf2212cd20807277.json`逐字核对6个原子报告与原存结果，重审完整目标、类型／公理／PID和当前门禁，新增Lean调用0。没有声称真实专项在最终所有源码上重新执行。

网页另执行重命名换序例：原rfl真实拒绝；完整相同泛化程序在新图取得E4。候选PID698579、独立重放PID717104，完整类型一致、审计公理列表为空。全阶段8次完整检查／11个真实进程，当前3个E4、5个UNKNOWN。三次成功均核对完整目标、具名对齐、不同PID及真实类型／公理工件；UNKNOWN不是反证。

## 实际发布竞态与修复

首轮浏览器在保存新图时，后台扫描先看见正式目录、后等到record.json写入，原读器因此记录永久invalidated。受影响图`structureflow-9aa457c43514439bb799bd66648bf09b`从未执行，attempts=[]，保持UNKNOWN；缺文件原因工件及失败截图保留，不删除标记复活。

新增PublishedStructureResearchStore作为HTTP和structure-research-* CLI创建适配器。在原base lease内，用同base隐藏.creating-structureflow目录完成计划、空子账本和记录，再同文件系统rename正式目录并fsync父目录。枚举器只看到已发布图，原数学计划、schema及执行器保持。确定性契约覆盖准备中扫描／catalog、准备失败隐藏、发布后返回前退出按同请求恢复、HTTP实际适配器及计划相同。准备失败目录保留且可能消耗磁盘；直接调用冻结旧低层create不具有新发布保证。见[ADR0060](../adr/0060-atomic-structure-graph-publication.md)。

## 回归、CLI及网页

最终相关回归`validation-contracts-2116e5c3a8764bfdb8a37cfa5696f5a8`：397项，332通过／65跳过，74.355秒。旧生产真实实验按协议跳过，不以跳过为实际验证；新真实专项另列。新增12项泛化契约、4项发布契约已包含在内。早先393项回归和快速契约同样通过，均保留且不累计成新增成功数。

最终`quality-daa96e285c5347449531bf7c7d3fc701`的9条质量命令通过（Ruff检查／格式、源门禁、JS语法及既有JS契约）。`cli-5d961e2690ea4309b00b6866b560f263`的17条CLI通过，包含新旧接口、严格坏输入及原request_id读取已发布图，不新增数学执行。回归、真实专项、CLI串行执行，验证期间产品／测试源冻结。

实际Edge两段验收：`browser-1793219914835`先通过6条检查后因上述保存竞态停住；`browser-1793220586713`续验10条全过，只执行此前未运行的泛化程序。合计16条通过检查、6张直接目视截图（含初次失败画面）。覆盖实际失败反馈、候选UNKNOWN、编辑／切换语境丢弃迟到结果并清确认、另存未知图、具名新检查、完整JSON下载、桌面及390px无横向溢出。原rfl与私有配对未重跑，两段共2次数学submit，无POST自动重发；5个暂忙GET503及损坏图来源GET400留存，后者不计成功。无JS异常、console错误或外部请求。

最终演示：[泛化结构研究图](http://127.0.0.1:8765/typed-research#research-9a151a1e8cfa433dafc27b02c927abb2/main/structureflow-cb3c779259f54f219ffe50f0ef2be82a/structure)。原失败图与新E4图单独保留，损坏未执行图另计。

## 保全与限制

停止空闲服务后，保存旧源、全部393个旧SQLite和573份旧状态；核对旧数据行与历史封存摘要，主研究库仅本轮单项目追加projects／requests／revisions各1行。最终保全／服务进程、内存约束、76份静态资源逐字验证及Windows实际HTTP观察以`preservation-final.json`、`runtime-check-*.json`和`delivery.json`为准。未实际演练灾难恢复，不把备份核对写成已恢复。

没有新外部依赖或真实模型调用，模型仍待用户本地配置。题集是相关、作者可见机制诊断，无独立盲测、统计外推、总体提速或四大期刊级难题能力证据。下一步优先必要引理的条件匹配与失败分支诊断，再扩展明确来源／曝光的新领域任务；依赖前提泛化、嵌套归纳、自动研究规划和完整性扫描的暂忙成本仍待处理。
