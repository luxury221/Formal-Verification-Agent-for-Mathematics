# 结构固定程序证明复用对照 v1

schema：structure-proof-reuse-evaluation-v1。结果见 ../evaluation/structure-proof-reuse-evaluation-results.md。

## 输入与边界

完整静态题集为 evals/structure-proof-reuse-v1/cases.json：hom-power-substitution、hom-power-congruence-repeat、monoid-power-add-substitution、wrong-identity-premise、archive-unavailable、missing-commutativity。作者编写、全部可见、training_use=false；不是独立持出或模型评测。

完整载体、运算、Nat.rec闭合定义、原条件、目标和显式调用树保留。前三题分别检查同态幂单次代入、两次应用经congrArg/trans组合及幂加法的m+1代入。第四题故意给单位保持律错误证明项；第五题预声明不构建来源归档而公开同一源程序；第六题在仅幺半群三定律下固定grind尝试交换性。失败是UNKNOWN/E0，不提供反例证据就不能标REFUTED。

import 使用真实来源候选及独立重放产出的证明数据，再检查完整目标。inline 把同一完整来源类型及受控归纳／改写程序放到每个调用处，不增加后备策略。两组目标类型逐字相同、每条原条件实参显式提供。两组来源程序都在引入自身完整∀绑定前清除可能的外层mra_c0、8条件、8变量、6定义、8运算及2载体，按依赖逆序逐项try clear。仅替换冻结编译器的确切来源声明token，次数须与应用次数一致，不允许残留导入引用，展开上限192KiB。

## 预算与顺序

候选／重放共享每项30秒，最多一次候选和一次独立重放，不重试。每题每组上限两项／60秒，六题每组上限360秒；inline只用一个目标检查，未用额度不挪作额外搜索。合计最多16完整检查／480预留worker秒：五source、五import、六inline；不能事先扣除预期失败来源。来源构建总在该题两组之前，import/inline目标顺序逐题交替，没有随机化。

报告两组完整六题分母、已证／未知和状态分布。import冷成本含全部已尝试来源，另列目标成本。实际worker秒、预留额度、进程、检查墙钟和非worker成本分别呈现；完整性扫描与锁等待不计worker额度，不声称端到端硬时限。共同预算上限不等于同量搜索或同等知识条件的独立解题试验。

## 生命周期

python3 -m mra structure-reuse-evaluation-prepare 只生成完整计划。structure-reuse-evaluation-run <ID> --plan-fingerprint <指纹> --reviewer <姓名> --confirmed 执行已核对程序；structure-reuse-evaluation-report <ID> 重新审计。姓名为本地声明。模型调用为0。

全局单worker锁及单评测lease，私有direct-checks/import-checks保存实际检查。计划绑定题集、编译器、原执行器和运行快照；报告重核每份子计划、具名确认、完整工件和18个有序步骤，包括不可用状态。缺条目、重复、重排、来源撤销、损坏或漂移均抑制统计，恢复旧字节不复活。监督在子成功、父提交前退出时，父INTERRUPTED且无统计；同确认再次run不续跑。父数学判断恒UNKNOWN/E0；不晋升原研究图，不写主复用库。

无新数学执行器或模板。独立进程采用同一固定Lean内核；检查不证明前提一致性、结构存在性、新颖性或原创研究能力。固定小题不能用于一般性能推断。
