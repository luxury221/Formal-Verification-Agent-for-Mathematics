# 结构策略诊断 v1

schema：structure-strategy-evaluation-v1。固定数据：evals/structure-strategies-v1/cases.json。CLI：structure-strategy-evaluation-prepare；structure-strategy-evaluation-run <evaluation_id> --plan-fingerprint <sha256> --reviewer <本地标识> --confirmed；structure-strategy-evaluation-report <evaluation_id>。

## 题目与快照

完整六例为单位律改写、重命名同态、幂加法、幂加法变量顺序变体、三角数fold、缺交换律。来源及曝光明确为本地作者已见示例；两个幂加法实例相关，共五个任务族。每例target含原explicit-structures-v1全部字段但不含proof，不能夹带参考证明。题名不作策略键，生成器只读完整AST和原条件；来源元数据是作者声明。training_use=false，model_solve_rate=null，independent_blind_evaluation/statistical_inference=false。

prepare保存带摘要封装的plan.json／record.json，包括数据及源上下文摘要、完整目标、预编译程序和生成器返回快照。报告验证原完整目标、程序顺序、来源、指纹、当前运行环境和预算，不重新执行候选搜索。只要上下文漂移则持续停用；恢复旧文件不能复活。

## 预算与执行

fixed组为rfl、grind，generated组为MRA065固定4步／800展开／2候选输出的原顺序。两组每题2次、每次候选与独立证明数据重放共享20秒，每题40秒、每组六题240秒、两组合计480秒／24完整检查上限。上限描述请求预算，清理／完整性扫描不作硬实时承诺；实际worker和墙钟单列。生成阶段另有0.75秒检查点和原节点燃料限制。

原全局worker锁串行真实检查。每案例交替两组先后次序；同组首次取得当前完整PROVED/E4后后续槽标记SKIPPED_AFTER_SUCCESS，槽位不删除，余量不转移。输出不足两个候选的槽标记NO_CANDIDATE，保留该题UNKNOWN分母。每个成功须核对完整原命题、具名确认、候选／重放不同PID、完整类型及允许公理。固定程序失败、超时、取消与中断不作REFUTED。

保存24槽完整条目及当前真实子检查，每组六题分母恒定。汇总显示各案例PROVED／UNKNOWN、实际检查次数、预留worker、实际worker及检查墙钟，并显示相关任务族、生成准备和计划构造时间。prepare／run返回完整调用墙钟，report返回审计墙钟；这些时间范围不同，不拼成未经定义的总体性能指标。

## 完整性、撤销与退出

具名确认只允许PREPARED启动一次；重复确认同一已执行计划只读取原结果，其他核对人不能接管。运行中监督者退出后，已提交的子E4可保留，但未提交父槽不计入比较；父显示INTERRUPTED且summary=null，无自动续跑或重试。未提交槽的case／arm／slot及run_id必须绑定下一预声明程序。

报告重新读取每个当前子检查；数据、代码、程序、状态、具名对齐、证明工件或公理审计不符时，写持续invalidated标记并停止发布summary。缺条、重复、错序、伪造跳过或错误成功审计不得发布统计。若需再试，须创建新的完整计划，旧失败与中断仍保留。仅本地私有评测账本，不修改原研究图、旧评测或主证明库。

这是一份作者可见策略功能诊断，不是模型解题率、独立盲测、数学新颖性认证、通用提速或四大期刊能力证据。本地核对人不是认证身份，候选和重放是同一固定Lean内核的两个独立进程。
